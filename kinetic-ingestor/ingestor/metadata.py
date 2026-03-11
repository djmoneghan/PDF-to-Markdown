# ingestor/metadata.py
# Chunk -> YAML metadata via local Ollama endpoint.

from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Controlled vocabularies (AC-3.4, AC-3.5)
# ---------------------------------------------------------------------------

TOPIC_CATEGORIES: list[str] = [
    "Fuel Cycle",
    "Reactor Design",
    "Construction",
    "Advanced Manufacturing",
    "Storage, Transportation, and Disposal",
    "Radioisotopes",
    "Materials Science",
    "Thermal Hydraulics",
    "Instrumentation & Control",
    "General Reference",
]

TECHNICAL_LEVELS: list[str] = ["Executive", "Specialist", "PhD"]

# Fields required in every assembled metadata dict (AC-3.8)
_REQUIRED_FIELDS: list[str] = [
    "source_id",
    "source_file",
    "chunk_id",
    "page_range",
    "breadcrumb",
    "parent_header",
    "topic_category",
    "technical_level",
    "summary",
    "confidence_score",
    "extraction_engine",
    "hitl_status",
    "corrections_ref",
]

# Per-session health-check cache (endpoint -> checked)
_checked_endpoints: set[str] = set()


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class MetadataGenerationError(Exception):
    """Raised when Ollama times out twice for a given chunk / field combination."""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_metadata(chunk: Any, config: dict[str, Any]) -> dict[str, Any]:
    """Generate YAML frontmatter metadata for a Chunk via the local Ollama endpoint.

    Issues four separate Ollama calls (AC-3.6):
      1. summary
      2. confidence_score
      3. topic_category
      4. technical_level

    Args:
        chunk:  Chunk object produced by ingestor.chunker.chunk().
        config: dict loaded by ingestor.load_config().

    Returns:
        dict conforming to the YAML frontmatter schema in CLAUDE.md (AC-3.8).

    Raises:
        ConnectionError: if the Ollama health check fails (AC-3.1).
        MetadataGenerationError: if any field times out on both attempts (AC-3.7).
    """
    endpoint: str = config["ollama"]["endpoint"]
    model: str    = config["ollama"]["model"]
    timeout: int  = int(config["ollama"]["timeout_seconds"])

    # AC-3.1 — health check once per endpoint per session
    _ensure_ollama_health(endpoint)

    content = chunk.content

    # AC-3.6 — four separate calls, never combined
    summary          = _gen_summary(content, model, endpoint, timeout, chunk.chunk_id)
    confidence_score = _gen_confidence(content, model, endpoint, timeout, chunk.chunk_id)
    topic_category   = _gen_topic_category(content, model, endpoint, timeout, chunk.chunk_id)
    technical_level  = _gen_technical_level(content, model, endpoint, timeout, chunk.chunk_id)

    metadata: dict[str, Any] = {
        "source_id":        chunk.source_id,
        "source_file":      chunk.source_file,
        "chunk_id":         chunk.chunk_id,
        "page_range":       chunk.page_range,
        "breadcrumb":       chunk.breadcrumb,
        "parent_header":    chunk.parent_header,
        "topic_category":   topic_category,
        "technical_level":  technical_level,
        "summary":          summary,
        "confidence_score": confidence_score,
        "extraction_engine": chunk.extraction_engine,
        "hitl_status":      chunk.hitl_status,
        "corrections_ref":  chunk.corrections_ref,
    }

    # AC-3.8 — validate schema completeness before returning
    _validate_schema(metadata)
    return metadata


# ---------------------------------------------------------------------------
# AC-3.1 — Ollama health check
# ---------------------------------------------------------------------------

def _ensure_ollama_health(endpoint: str) -> None:
    """Check Ollama reachability once per endpoint per process session."""
    if endpoint in _checked_endpoints:
        return
    url = f"{endpoint}/api/tags"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except (urllib.error.URLError, OSError, Exception) as exc:
        raise ConnectionError(
            f"Ollama endpoint unreachable at {url}: {exc}"
        ) from exc
    _checked_endpoints.add(endpoint)


# ---------------------------------------------------------------------------
# Per-field generators (AC-3.2 – AC-3.5, each a separate Ollama call)
# ---------------------------------------------------------------------------

def _gen_summary(
    content: str, model: str, endpoint: str, timeout: int, chunk_id: str
) -> str:
    """AC-3.2 — generate 1-2 sentence summary; truncate at sentence boundary ≤300 chars."""
    prompt = (
        "You are a technical documentation assistant specialising in nuclear engineering "
        "and regulatory documentation.\n\n"
        "Summarise the following content in exactly 1–2 sentences. "
        "Use domain-appropriate language. Do NOT use bullet points.\n\n"
        f"Content:\n{content}\n\n"
        "Summary (1–2 sentences only):"
    )
    raw = _call_with_retry(
        lambda: _call_ollama(prompt, model, endpoint, timeout),
        chunk_id, "summary",
    )
    text = raw.strip()
    if len(text) > 300:
        truncated = text[:300]
        last_period = truncated.rfind(".")
        text = truncated[: last_period + 1] if last_period > 0 else truncated
        log.warning(
            "Summary for %r truncated to %d chars at sentence boundary.",
            chunk_id, len(text),
        )
    return text


def _gen_confidence(
    content: str, model: str, endpoint: str, timeout: int, chunk_id: str
) -> float:
    """AC-3.3 — self-assessed extraction quality score 0.0–1.0."""
    prompt = (
        "Rate the extraction quality of the following text on a scale from 0.0 to 1.0.\n"
        "Look for: garbled text, incomplete tables, suspicious formula rendering, encoding issues.\n"
        "1.0 = perfect extraction quality. 0.0 = completely unreadable.\n\n"
        f"Content:\n{content}\n\n"
        "Respond with ONLY a single decimal number between 0.0 and 1.0 (e.g. 0.85):"
    )
    raw = _call_with_retry(
        lambda: _call_ollama(prompt, model, endpoint, timeout),
        chunk_id, "confidence_score",
    )
    return _parse_float(raw.strip(), chunk_id)


def _gen_topic_category(
    content: str, model: str, endpoint: str, timeout: int, chunk_id: str
) -> str:
    """AC-3.4 — classify into controlled topic_category vocabulary."""
    categories_block = "\n".join(f"- {c}" for c in TOPIC_CATEGORIES)
    prompt = (
        f"Classify the following technical content into exactly one of these categories:\n"
        f"{categories_block}\n\n"
        f"Content:\n{content}\n\n"
        "Respond with ONLY the category name, exactly as written above:"
    )
    raw = _call_with_retry(
        lambda: _call_ollama(prompt, model, endpoint, timeout),
        chunk_id, "topic_category",
    )
    return _match_vocabulary(raw.strip(), TOPIC_CATEGORIES, "General Reference",
                             "topic_category", chunk_id)


def _gen_technical_level(
    content: str, model: str, endpoint: str, timeout: int, chunk_id: str
) -> str:
    """AC-3.5 — classify into controlled technical_level vocabulary."""
    prompt = (
        "Classify the technical level of the following content as one of:\n"
        "  Executive  — high-level overview, no deep technical knowledge required\n"
        "  Specialist — requires domain expertise and technical background\n"
        "  PhD        — requires advanced research-level understanding\n\n"
        f"Content:\n{content}\n\n"
        "Respond with ONLY one word: Executive, Specialist, or PhD:"
    )
    raw = _call_with_retry(
        lambda: _call_ollama(prompt, model, endpoint, timeout),
        chunk_id, "technical_level",
    )
    # DECISION REQUIRED: spec does not specify a default for technical_level on
    # non-conforming response (only AC-3.4 explicitly names "General Reference").
    # Using "Specialist" as the default — revisit if a different default is required.
    return _match_vocabulary(raw.strip(), TECHNICAL_LEVELS, "Specialist",
                             "technical_level", chunk_id)


# ---------------------------------------------------------------------------
# AC-3.7 — retry wrapper
# ---------------------------------------------------------------------------

def _call_with_retry(fn: Any, chunk_id: str, field_name: str) -> str:
    """Call fn(); retry once on timeout. Raises MetadataGenerationError after two failures."""
    try:
        return fn()
    except Exception as exc:
        if not _is_timeout(exc):
            raise
        log.warning(
            "Ollama timed out on first attempt for %r field %r — retrying.",
            chunk_id, field_name,
        )
    try:
        return fn()
    except Exception as exc2:
        if _is_timeout(exc2):
            raise MetadataGenerationError(
                f"Ollama timed out twice for chunk {chunk_id!r}, field {field_name!r}."
            ) from exc2
        raise


def _is_timeout(exc: Exception) -> bool:
    """Heuristic: treat any exception whose type name or message suggests a timeout."""
    name = type(exc).__name__.lower()
    msg  = str(exc).lower()
    return "timeout" in name or "timed out" in msg or "timeout" in msg


# ---------------------------------------------------------------------------
# AC-3.8 — schema validation
# ---------------------------------------------------------------------------

def _validate_schema(metadata: dict[str, Any]) -> None:
    """Raise ValueError if any required field is absent from the metadata dict."""
    missing = [f for f in _REQUIRED_FIELDS if f not in metadata]
    if missing:
        raise ValueError(
            f"Metadata schema violation — missing required fields: {missing}. "
            "This is a hard failure; the chunk will not be exported."
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _call_ollama(prompt: str, model: str, endpoint: str, timeout_sec: int) -> str:
    """Issue a single generate call to the Ollama Python client."""
    import ollama as _ollama  # imported lazily so the module loads without Ollama installed

    client = _ollama.Client(host=endpoint, timeout=timeout_sec)
    resp = client.generate(model=model, prompt=prompt)

    # Handle both object (new API) and dict (old API) response shapes
    if isinstance(resp, dict):
        return resp.get("response", "")
    return getattr(resp, "response", str(resp))


def _parse_float(raw: str, chunk_id: str) -> float:
    """AC-3.3 — parse float from LLM response; default to 0.5 on failure."""
    m = re.search(r"\b(1\.0|0\.\d+|[01])\b", raw)
    if m:
        try:
            value = float(m.group(1))
            return max(0.0, min(1.0, value))  # clamp to [0, 1]
        except ValueError:
            pass
    log.warning(
        "Could not parse confidence_score from %r for chunk %r. Defaulting to 0.5.",
        raw, chunk_id,
    )
    return 0.5


def _match_vocabulary(
    raw: str,
    vocabulary: list[str],
    default: str,
    field_name: str,
    chunk_id: str,
) -> str:
    """Return the vocabulary entry that matches raw (case-insensitive); default otherwise."""
    # Exact match
    if raw in vocabulary:
        return raw
    # Case-insensitive match
    raw_lower = raw.lower()
    for entry in vocabulary:
        if entry.lower() == raw_lower:
            return entry
    log.warning(
        "LLM returned non-conforming %s %r for chunk %r. Defaulting to %r.",
        field_name, raw, chunk_id, default,
    )
    return default
