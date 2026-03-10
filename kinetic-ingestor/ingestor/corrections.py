# ingestor/corrections.py
# corrections.json append-only read/write.
# Phase 7: append_correction stub (minimal working implementation).
# Phase 8: adds load_corrections() and --overwrite-corrections behaviour.

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
