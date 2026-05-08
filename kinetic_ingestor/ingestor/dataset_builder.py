# ingestor/dataset_builder.py
# Phase 3 — Fine-Tuning Loop.
#
# Aggregates HITL corrections from all processed documents and converts them
# into training datasets for prompt refinement or LoRA fine-tuning.
#
# Public interface
# ----------------
#   aggregate_corrections(processed_root)  -> list[dict]
#   build_training_pairs(corrections, processed_root) -> list[dict]
#   export_jsonl(pairs, output_path, fmt)  -> int
#   generate_modelfile(pairs, base_model, output_path) -> None
#   compute_stats(corrections)             -> dict

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

log = logging.getLogger("ingestor.dataset_builder")

# Fields that have matching Ollama prompts (and can therefore be fine-tuned).
_TRAINABLE_FIELDS: tuple[str, ...] = (
    "summary",
    "topic_category",
    "technical_level",
    "confidence_score",
)

# Prompt templates that mirror the exact wording used in ingestor/metadata.py.
# The {content} placeholder is replaced with the chunk's Markdown body.
# topic_category also needs {categories}.
_PROMPT_TEMPLATES: dict[str, str] = {
    "summary": (
        "You are a technical documentation assistant specialising in nuclear engineering "
        "and regulatory documentation.\n\n"
        "Summarise the following content in exactly 1–2 sentences. "
        "Use domain-appropriate language. Do NOT use bullet points.\n\n"
        "Content:\n{content}\n\n"
        "Summary (1–2 sentences only):"
    ),
    "topic_category": (
        "Classify the following technical content into exactly one of these categories:\n"
        "{categories}\n\n"
        "Content:\n{content}\n\n"
        "Respond with ONLY the category name, exactly as written above:"
    ),
    "technical_level": (
        "Classify the technical level of the following content as one of:\n"
        "  Executive  — high-level overview, no deep technical knowledge required\n"
        "  Specialist — requires domain expertise and technical background\n"
        "  PhD        — requires advanced research-level understanding\n\n"
        "Content:\n{content}\n\n"
        "Respond with ONLY one word: Executive, Specialist, or PhD:"
    ),
    "confidence_score": (
        "Rate the extraction quality of the following text on a scale from 0.0 to 1.0.\n"
        "Look for: garbled text, incomplete tables, suspicious formula rendering, encoding issues.\n"
        "1.0 = perfect extraction quality. 0.0 = completely unreadable.\n\n"
        "Content:\n{content}\n\n"
        "Respond with ONLY a single decimal number between 0.0 and 1.0 (e.g. 0.85):"
    ),
}

# Frontmatter delimiter pattern (--- ... ---\n).
_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n+", re.DOTALL)


# ---------------------------------------------------------------------------
# 1. Aggregation
# ---------------------------------------------------------------------------

def aggregate_corrections(processed_root: Path) -> list[dict[str, Any]]:
    """Scan *processed_root* for all ``corrections.json`` files and return a
    flat list of correction records.

    Each record is augmented with two private keys:
    - ``_source_id``:   the parent directory name (= document source_id)
    - ``_project_dir``: absolute path to that directory as a string

    Args:
        processed_root: Path to ``workspace/processed/`` (or equivalent).

    Returns:
        Flat list of all correction dicts across all documents, sorted by
        timestamp.  Returns an empty list if no corrections exist yet.
    """
    all_records: list[dict[str, Any]] = []

    for corr_file in sorted(processed_root.glob("*/corrections.json")):
        source_id = corr_file.parent.name
        try:
            raw = corr_file.read_text(encoding="utf-8")
            records = json.loads(raw)
            if not isinstance(records, list):
                log.warning("Skipping %s — not a JSON array.", corr_file)
                continue
            for rec in records:
                rec.setdefault("_source_id", source_id)
                rec.setdefault("_project_dir", str(corr_file.parent))
            all_records.extend(records)
            log.debug("Loaded %d record(s) from %s", len(records), corr_file)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read %s: %s", corr_file, exc)

    # Sort chronologically so the dataset is reproducible
    all_records.sort(key=lambda r: r.get("timestamp", ""))
    log.info(
        "aggregate_corrections: %d total record(s) from %d document(s)",
        len(all_records),
        len({r.get("_source_id") for r in all_records}),
    )
    return all_records


# ---------------------------------------------------------------------------
# 2. Chunk content extraction
# ---------------------------------------------------------------------------

def extract_chunk_content(chunk_id: str, project_dir: Path) -> str | None:
    """Return the Markdown body of *chunk_id*.md, with YAML frontmatter stripped.

    Returns ``None`` if the file does not exist or cannot be parsed.
    """
    md_path = project_dir / f"{chunk_id}.md"
    if not md_path.exists():
        log.debug("Chunk file not found: %s", md_path)
        return None
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("Could not read %s: %s", md_path, exc)
        return None

    m = _FRONTMATTER_RE.match(text)
    if m:
        return text[m.end():].strip()
    # No frontmatter — return as-is
    return text.strip()


# ---------------------------------------------------------------------------
# 3. Training pair builder
# ---------------------------------------------------------------------------

def build_training_pairs(
    corrections: list[dict[str, Any]],
    processed_root: Path,
) -> list[dict[str, Any]]:
    """Build one training pair per changed field per edited correction record.

    Only ``action == "edited"`` records are used.  Flagged-only records
    (which have no ``corrected_yaml``) are skipped.

    Each returned pair has the shape::

        {
            "field":       "summary" | "topic_category" | "technical_level" | "confidence_score",
            "instruction": "<full prompt with content embedded>",
            "input":       "",   # empty — content already in instruction
            "output":      "<corrected field value>",
            "metadata":    {
                "source_id":            str,
                "chunk_id":             str,
                "source_file":          str,
                "record_id":            str,
                "correction_timestamp": str,
                "original_value":       str,
            },
        }

    Args:
        corrections:    Output of :func:`aggregate_corrections`.
        processed_root: Used only if ``_project_dir`` is absent from a record
                        (fallback: ``processed_root / _source_id``).

    Returns:
        List of training pair dicts (may be empty if no edited corrections exist).
    """
    from ingestor.metadata import TOPIC_CATEGORIES

    categories_block = "\n".join(f"- {c}" for c in TOPIC_CATEGORIES)
    pairs: list[dict[str, Any]] = []

    for rec in corrections:
        if rec.get("action") != "edited":
            continue
        orig: dict[str, Any] = rec.get("original_yaml") or {}
        corr: dict[str, Any] = rec.get("corrected_yaml") or {}
        if not corr:
            continue

        chunk_id = rec.get("chunk_id", "")
        project_dir = Path(
            rec.get("_project_dir")
            or (processed_root / rec.get("_source_id", ""))
        )

        content = extract_chunk_content(chunk_id, project_dir)
        if not content:
            log.warning(
                "No content found for chunk %r in %s — skipping.", chunk_id, project_dir
            )
            continue

        source_id = rec.get("_source_id") or orig.get("source_id", "")

        for field in _TRAINABLE_FIELDS:
            orig_val = orig.get(field)
            corr_val = corr.get(field)

            # Skip if field not changed
            if corr_val is None or str(corr_val) == str(orig_val):
                continue

            template = _PROMPT_TEMPLATES[field]
            if field == "topic_category":
                instruction = template.format(
                    content=content, categories=categories_block
                )
            else:
                instruction = template.format(content=content)

            pairs.append({
                "field":       field,
                "instruction": instruction,
                "input":       "",
                "output":      str(corr_val),
                "metadata": {
                    "source_id":            source_id,
                    "chunk_id":             chunk_id,
                    "source_file":          orig.get("source_file", ""),
                    "record_id":            rec.get("record_id", ""),
                    "correction_timestamp": rec.get("timestamp", ""),
                    "original_value":       str(orig_val) if orig_val is not None else "",
                },
            })

    log.info(
        "build_training_pairs: %d pair(s) from %d edited correction(s)",
        len(pairs),
        sum(1 for r in corrections if r.get("action") == "edited"),
    )
    return pairs


# ---------------------------------------------------------------------------
# 4. JSONL export
# ---------------------------------------------------------------------------

def export_jsonl(
    pairs: list[dict[str, Any]],
    output_path: Path,
    fmt: str = "alpaca",
) -> int:
    """Write *pairs* to a JSONL file suitable for fine-tuning.

    Args:
        pairs:       Output of :func:`build_training_pairs`.
        output_path: Destination file (created; parent dirs made if needed).
        fmt:         ``"alpaca"`` (default) or ``"openai"`` chat format.

    Returns:
        Number of records written.

    Raises:
        ValueError: if *fmt* is not ``"alpaca"`` or ``"openai"``.
        RuntimeError: if the file cannot be written.
    """
    if fmt not in ("alpaca", "openai"):
        raise ValueError(f"Unknown format {fmt!r}. Use 'alpaca' or 'openai'.")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    try:
        with output_path.open("w", encoding="utf-8") as fh:
            for pair in pairs:
                if fmt == "openai":
                    record: dict[str, Any] = {
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "You are a technical documentation assistant "
                                    "specialising in nuclear engineering and regulatory "
                                    "documentation."
                                ),
                            },
                            {"role": "user",      "content": pair["instruction"]},
                            {"role": "assistant", "content": pair["output"]},
                        ]
                    }
                else:  # alpaca
                    record = {
                        "instruction": pair["instruction"],
                        "input":       pair["input"],
                        "output":      pair["output"],
                    }
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1
    except OSError as exc:
        raise RuntimeError(f"Failed to write dataset to {output_path}: {exc}") from exc

    log.info("export_jsonl: wrote %d record(s) to %s (fmt=%s)", count, output_path, fmt)
    return count


# ---------------------------------------------------------------------------
# 5. Ollama Modelfile generator
# ---------------------------------------------------------------------------

def generate_modelfile(
    pairs: list[dict[str, Any]],
    base_model: str,
    output_path: Path,
) -> None:
    """Generate an Ollama Modelfile with a system prompt refined from corrections.

    Analyses *pairs* to determine which fields were corrected most often and
    incorporates specific guidance for those fields into the system prompt.
    Even with zero corrections, this generates a baseline Modelfile from the
    current prompt strategy.

    Args:
        pairs:       Output of :func:`build_training_pairs` (may be empty).
        base_model:  Ollama model name to build on (e.g. ``"qwen3:8b"``).
        output_path: Destination ``.Modelfile`` path.

    Raises:
        RuntimeError: if the file cannot be written.
    """
    field_counts: Counter[str] = Counter(p["field"] for p in pairs)

    # Build field-specific refinements ordered by correction frequency
    refinements: list[str] = []

    if field_counts["summary"] > 0:
        refinements.append(
            f"- Summary (corrected {field_counts['summary']}× in corpus): "
            "always use exactly 1–2 sentences; domain-accurate language; no bullets."
        )
    if field_counts["topic_category"] > 0:
        refinements.append(
            f"- Topic category (corrected {field_counts['topic_category']}× in corpus): "
            "choose the most specific applicable category; prefer Fuel Cycle or Reactor Design "
            "over General Reference for nuclear engineering content."
        )
    if field_counts["technical_level"] > 0:
        refinements.append(
            f"- Technical level (corrected {field_counts['technical_level']}× in corpus): "
            "regulatory and safety analysis documents are typically Specialist; "
            "executive summaries are Executive; primary research is PhD."
        )
    if field_counts["confidence_score"] > 0:
        refinements.append(
            f"- Confidence score (corrected {field_counts['confidence_score']}× in corpus): "
            "be conservative; score below 0.80 when any structural issues are visible."
        )

    if not refinements:
        refinements_block = "- Follow all prompt instructions precisely and completely."
    else:
        refinements_block = "\n".join(refinements)

    pair_count = len(pairs)
    edited_docs = len({p["metadata"]["source_id"] for p in pairs}) if pairs else 0

    system_prompt = (
        "You are a technical documentation assistant specialising in nuclear engineering "
        "and regulatory documentation. You produce precise, domain-accurate metadata "
        "for document chunks in a RAG pipeline.\n\n"
        f"This system prompt was generated by the Kinetic Ingestor fine-tuning loop "
        f"from {pair_count} training pair(s) across {edited_docs} document(s).\n\n"
        "Key guidelines learned from human HITL corrections:\n"
        f"{refinements_block}"
    )

    modelfile_content = (
        f"FROM {base_model}\n"
        f"SYSTEM \"\"\"\n{system_prompt}\n\"\"\"\n"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        output_path.write_text(modelfile_content, encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(
            f"Failed to write Modelfile to {output_path}: {exc}"
        ) from exc

    log.info("generate_modelfile: wrote %s (base_model=%s)", output_path, base_model)


# ---------------------------------------------------------------------------
# 6. Statistics
# ---------------------------------------------------------------------------

def compute_stats(corrections: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute summary statistics over the corrections corpus.

    Returns a dict with the following keys:

    - ``total_corrections``           — int: total records across all documents
    - ``by_action``                   — dict: counts per action ("edited", "flagged", …)
    - ``by_field``                    — dict: edited-field change counts per field name
    - ``documents_with_corrections``  — int: unique source_id values
    - ``source_ids``                  — list[str]: sorted list of source IDs
    - ``avg_confidence_before_edit``  — float | None: mean original confidence score
    - ``avg_confidence_after_edit``   — float | None: mean corrected confidence score
    - ``confidence_delta``            — float | None: after − before (positive = improved)
    - ``edit_rate``                   — float | None: edited / total (proportion)
    - ``trainable_pairs_available``   — int: estimate of training pairs (sum of by_field)
    """
    total = len(corrections)
    by_action: Counter[str] = Counter(r.get("action", "unknown") for r in corrections)

    field_change_counts: Counter[str] = Counter()
    confidence_before: list[float] = []
    confidence_after: list[float] = []

    for rec in corrections:
        if rec.get("action") != "edited":
            continue
        orig: dict[str, Any] = rec.get("original_yaml") or {}
        corr: dict[str, Any] = rec.get("corrected_yaml") or {}
        if not corr:
            continue

        for field in _TRAINABLE_FIELDS:
            orig_val = orig.get(field)
            corr_val = corr.get(field)
            if corr_val is not None and str(corr_val) != str(orig_val):
                field_change_counts[field] += 1

        # Confidence delta (only when both are present and field changed)
        if "confidence_score" in orig:
            try:
                confidence_before.append(float(orig["confidence_score"]))
            except (TypeError, ValueError):
                pass
        if "confidence_score" in corr:
            try:
                confidence_after.append(float(corr["confidence_score"]))
            except (TypeError, ValueError):
                pass

    source_ids = sorted(
        s for s in {
            r.get("_source_id") or (r.get("original_yaml") or {}).get("source_id", "")
            for r in corrections
        }
        if s
    )

    avg_before = (
        sum(confidence_before) / len(confidence_before) if confidence_before else None
    )
    avg_after = (
        sum(confidence_after) / len(confidence_after) if confidence_after else None
    )
    delta = (
        round(avg_after - avg_before, 4)
        if avg_before is not None and avg_after is not None
        else None
    )
    edit_count = by_action.get("edited", 0)
    edit_rate = round(edit_count / total, 4) if total > 0 else None

    return {
        "total_corrections":          total,
        "by_action":                  dict(by_action),
        "by_field":                   dict(field_change_counts),
        "documents_with_corrections": len(source_ids),
        "source_ids":                 source_ids,
        "avg_confidence_before_edit": round(avg_before, 4) if avg_before is not None else None,
        "avg_confidence_after_edit":  round(avg_after, 4) if avg_after is not None else None,
        "confidence_delta":           delta,
        "edit_rate":                  edit_rate,
        "trainable_pairs_available":  sum(field_change_counts.values()),
    }
