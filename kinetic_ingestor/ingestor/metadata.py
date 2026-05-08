# ingestor/metadata.py
# Chunk -> YAML metadata via Puck's local Gemma orchestrator
# (OpenAI-compatible chat-completions API at http://localhost:8080).

from __future__ import annotations

import logging
import re
import time
from typing import Any

import httpx

from ingestor import Chunk

log = logging.getLogger("ingestor.metadata")
log.setLevel(logging.DEBUG)

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

# Field-specific token budgets. Generous because Puck's Gemma 4 31B
# checkpoint emits an extended-thinking preamble before the final answer;
# the budget must cover (reasoning tokens) + (answer tokens) or the
# response truncates mid-reasoning and `_strip_reasoning` returns the raw
# preamble. ~80–280 tokens of reasoning observed on classification prompts.
_MAX_TOKENS = {
    "summary": 1024,
    "confidence_score": 512,
    "topic_category": 512,
    "technical_level": 256,
}

# Marker the orchestrator's Gemma checkpoint emits between its reasoning
# preamble and the final answer:
#
#   <|channel>thought\n
#   *   <reasoning bullets>
#   ...
#   <channel|><actual answer>
#
# Note the differently-placed pipe between opener and closer — that is the
# format the model produces, not a typo.
_REASONING_OPENER = "<|channel>thought"
_REASONING_CLOSER = "<channel|>"


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class MetadataGenerationError(Exception):
    """Raised when the orchestrator times out twice for a given chunk / field combination."""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_metadata(chunk: Chunk, config: dict) -> dict:
    """
    Generate metadata for a single chunk via 4 sequential orchestrator calls.

    Populates chunk.metadata and chunk.confidence_score in-place and returns
    the complete metadata dict.

    Raises:
        ConnectionError: if the orchestrator endpoint is unreachable.
        MetadataGenerationError: if a field times out twice (primary + fallback).
    """
    inference = config["inference"]
    endpoint = inference["endpoint"]
    model = inference["model"]
    fallback_model = inference["fallback_model"]
    timeout = inference["timeout_seconds"]

    _ensure_inference_health(endpoint)

    summary = _gen_summary(chunk.content, model, fallback_model, endpoint, timeout, chunk.chunk_id)
    confidence = _gen_confidence(chunk.content, model, fallback_model, endpoint, timeout, chunk.chunk_id)
    topic = _gen_topic_category(chunk.content, model, fallback_model, endpoint, timeout, chunk.chunk_id)
    level = _gen_technical_level(chunk.content, model, fallback_model, endpoint, timeout, chunk.chunk_id)

    meta = {
        "source_id": chunk.source_id,
        "source_file": chunk.source_file,
        "chunk_id": chunk.chunk_id,
        "page_range": chunk.page_range,
        "breadcrumb": chunk.breadcrumb,
        "parent_header": chunk.parent_header,
        "topic_category": topic,
        "technical_level": level,
        "summary": summary,
        "confidence_score": confidence,
        "extraction_engine": chunk.extraction_engine,
        "hitl_status": chunk.hitl_status,
        "corrections_ref": chunk.corrections_ref,
    }
    _validate_schema(meta)
    chunk.metadata = meta
    chunk.confidence_score = confidence
    return meta


# ---------------------------------------------------------------------------
# AC-3.1 — Orchestrator health check
# ---------------------------------------------------------------------------

def _ensure_inference_health(endpoint: str) -> None:
    """Check orchestrator reachability once per endpoint per process session."""
    if endpoint in _checked_endpoints:
        return
    url = f"{endpoint.rstrip('/')}/health"
    try:
        resp = httpx.get(url, timeout=5)
        resp.raise_for_status()
    except Exception as exc:
        raise ConnectionError(
            f"Inference endpoint unreachable at {url}: {exc}"
        ) from exc
    _checked_endpoints.add(endpoint)


# ---------------------------------------------------------------------------
# Per-field generators (AC-3.2 – AC-3.5, each a separate orchestrator call)
# ---------------------------------------------------------------------------

def _gen_summary(
    content: str,
    model: str,
    fallback_model: str,
    endpoint: str,
    timeout: int,
    chunk_id: str,
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
        lambda: _call_inference(prompt, model, endpoint, timeout, "summary"),
        lambda: _call_inference(prompt, fallback_model, endpoint, timeout, "summary"),
        chunk_id,
        "summary",
    )
    text = raw.strip()
    if len(text) > 300:
        truncated = text[:300]
        last_period = truncated.rfind(".")
        text = truncated[: last_period + 1] if last_period > 0 else truncated
        log.warning(
            "Summary for %r truncated to %d chars at sentence boundary.",
            chunk_id,
            len(text),
        )
    return text


def _gen_confidence(
    content: str,
    model: str,
    fallback_model: str,
    endpoint: str,
    timeout: int,
    chunk_id: str,
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
        lambda: _call_inference(prompt, model, endpoint, timeout, "confidence_score"),
        lambda: _call_inference(prompt, fallback_model, endpoint, timeout, "confidence_score"),
        chunk_id,
        "confidence_score",
    )
    return _parse_float(raw.strip(), chunk_id)


def _gen_topic_category(
    content: str,
    model: str,
    fallback_model: str,
    endpoint: str,
    timeout: int,
    chunk_id: str,
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
        lambda: _call_inference(prompt, model, endpoint, timeout, "topic_category"),
        lambda: _call_inference(prompt, fallback_model, endpoint, timeout, "topic_category"),
        chunk_id,
        "topic_category",
    )
    return _match_vocabulary(
        raw.strip(), TOPIC_CATEGORIES, "General Reference", "topic_category", chunk_id
    )


def _gen_technical_level(
    content: str,
    model: str,
    fallback_model: str,
    endpoint: str,
    timeout: int,
    chunk_id: str,
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
        lambda: _call_inference(prompt, model, endpoint, timeout, "technical_level"),
        lambda: _call_inference(prompt, fallback_model, endpoint, timeout, "technical_level"),
        chunk_id,
        "technical_level",
    )
    # DECISION REQUIRED: spec does not specify a default for technical_level on
    # non-conforming response (only AC-3.4 explicitly names "General Reference").
    # Using "Specialist" as the default — revisit if a different default is required.
    return _match_vocabulary(
        raw.strip(), TECHNICAL_LEVELS, "Specialist", "technical_level", chunk_id
    )


# ---------------------------------------------------------------------------
# AC-3.7 — Retry wrapper with fallback model
# ---------------------------------------------------------------------------

def _call_with_retry(
    primary_fn: Any,
    fallback_fn: Any,
    chunk_id: str,
    field_name: str,
) -> str:
    """
    Call primary_fn(); on timeout retry once with fallback_fn().
    Logs model, latency, and fallback status for each attempt.
    Raises MetadataGenerationError after two consecutive timeouts.
    """
    t0 = time.monotonic()
    try:
        result = primary_fn()
        log.info(
            "Inference [%s] chunk=%r model=primary latency=%.0fms",
            field_name, chunk_id, (time.monotonic() - t0) * 1000,
        )
        return result
    except Exception as exc:
        if not _is_timeout(exc):
            raise
        log.warning(
            "Inference timed out (primary) for chunk %r field %r — retrying with fallback model.",
            chunk_id, field_name,
        )

    t0 = time.monotonic()
    try:
        result = fallback_fn()
        log.info(
            "Inference [%s] chunk=%r model=fallback latency=%.0fms",
            field_name, chunk_id, (time.monotonic() - t0) * 1000,
        )
        return result
    except Exception as exc2:
        if _is_timeout(exc2):
            raise MetadataGenerationError(
                f"Inference timed out twice for chunk {chunk_id!r}, field {field_name!r}."
            ) from exc2
        raise


def _is_timeout(exc: Exception) -> bool:
    """Heuristic: treat any exception whose type name or message suggests a timeout."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    return "timeout" in name or "timed out" in msg or "timeout" in msg


# ---------------------------------------------------------------------------
# AC-3.8 — Schema validation
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

def _call_inference(
    prompt: str,
    model: str,
    endpoint: str,
    timeout_sec: int,
    field_name: str,
) -> str:
    """Issue a single chat-completion call to the orchestrator (OpenAI-compatible)."""
    url = f"{endpoint.rstrip('/')}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": _MAX_TOKENS.get(field_name, 1024),
        "temperature": 0.0,
        "stream": False,
    }
    resp = httpx.post(url, json=payload, timeout=timeout_sec)
    resp.raise_for_status()
    body = resp.json()
    try:
        raw = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(
            f"Inference response missing choices[0].message.content: {body!r}"
        ) from exc
    return _strip_reasoning(raw)


def _strip_reasoning(text: str) -> str:
    """Strip Gemma's thinking-channel preamble.

    The orchestrator's Gemma 4 31B checkpoint emits an extended-thinking
    section prefixed with ``<|channel>thought\\n`` and signals the final
    answer with ``<channel|>`` (note the differently-placed pipe — that
    is the format the model produces).

    Returns everything after the first ``<channel|>`` if present;
    otherwise strips a leading ``<|channel>thought`` block conservatively
    by dropping it if it stands alone, otherwise returns the input
    unchanged. Always strips surrounding whitespace.
    """
    if _REASONING_CLOSER in text:
        return text.split(_REASONING_CLOSER, 1)[1].strip()
    # Truncated mid-reasoning (no closer reached): don't try to salvage —
    # let downstream parsers fall back to defaults rather than guessing.
    if text.lstrip().startswith(_REASONING_OPENER):
        return ""
    return text.strip()


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
        raw,
        chunk_id,
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
    if raw in vocabulary:
        return raw
    raw_lower = raw.lower()
    for entry in vocabulary:
        if entry.lower() == raw_lower:
            return entry
    log.warning(
        "LLM returned non-conforming %s %r for chunk %r. Defaulting to %r.",
        field_name,
        raw,
        chunk_id,
        default,
    )
    return default
