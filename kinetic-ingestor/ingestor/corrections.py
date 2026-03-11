# ingestor/corrections.py
# corrections.json append-only read/write.

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def append_correction(record: dict[str, Any], config: dict[str, Any]) -> None:
    """Append a HITL correction record to corrections.json (append-only).

    The file is a JSON array. On first write the array is initialised.
    Existing records are never overwritten unless --overwrite-corrections is
    passed at the CLI level (enforced in Phase 8).

    Args:
        record: correction dict conforming to the schema in REQUIREMENTS.md §AC-5.4.
        config: dict loaded by ingestor.load_config().
    """
    corrections_path = Path(config["output"]["corrections_filename"])

    records: list[dict] = []
    if corrections_path.exists():
        try:
            with corrections_path.open("r", encoding="utf-8") as fh:
                records = json.load(fh)
            if not isinstance(records, list):
                log.warning(
                    "corrections.json is not a JSON array — reinitialising. "
                    "Previous contents moved aside."
                )
                records = []
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read corrections.json (%s) — starting fresh.", exc)
            records = []

    records.append(record)

    tmp_path = corrections_path.with_suffix(".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(records, fh, indent=2, ensure_ascii=False)
        tmp_path.rename(corrections_path)  # atomic on POSIX
    except OSError as exc:
        raise RuntimeError(
            f"Failed to write corrections.json at {corrections_path}: {exc}"
        ) from exc


def load_corrections(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Load all records from corrections.json.

    Returns an empty list if the file does not exist or is uninitialised.

    Args:
        config: dict loaded by ingestor.load_config().

    Returns:
        List of correction record dicts (may be empty).

    Raises:
        ValueError: if the file exists but contains invalid JSON.
    """
    corrections_path = Path(config["output"]["corrections_filename"])

    if not corrections_path.exists():
        return []

    try:
        with corrections_path.open("r", encoding="utf-8") as fh:
            records = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"corrections.json is not valid JSON: {exc}"
        ) from exc

    if not isinstance(records, list):
        raise ValueError(
            "corrections.json must contain a JSON array at the top level."
        )

    return records


def overwrite_correction(
    record: dict[str, Any], config: dict[str, Any]
) -> None:
    """Replace an existing record with the same record_id (--overwrite-corrections).

    If no record with matching record_id exists, the new record is appended.

    Args:
        record: correction dict; must contain a ``record_id`` key.
        config: dict loaded by ingestor.load_config().

    Raises:
        KeyError:    if *record* has no ``record_id`` field.
        RuntimeError: if the atomic write fails.
    """
    if "record_id" not in record:
        raise KeyError("correction record must contain a 'record_id' field.")

    records = load_corrections(config)

    replaced = False
    for i, existing in enumerate(records):
        if existing.get("record_id") == record["record_id"]:
            records[i] = record
            replaced = True
            break

    if not replaced:
        log.debug(
            "overwrite_correction: no existing record with id %s — appending.",
            record["record_id"],
        )
        records.append(record)

    corrections_path = Path(config["output"]["corrections_filename"])
    tmp_path = corrections_path.with_suffix(".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(records, fh, indent=2, ensure_ascii=False)
        tmp_path.rename(corrections_path)
    except OSError as exc:
        raise RuntimeError(
            f"Failed to write corrections.json at {corrections_path}: {exc}"
        ) from exc
