# ingestor/exporter.py
# Chunk -> .md file writer and manifest generator.

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def export(
    chunks: list[Any],
    source_pdf_path: Path | str,
    config: dict[str, Any],
    force: bool = False,
) -> Path:
    """Write all reviewed chunks to .md files; generate manifest.json.

    AC-5.1 — atomic writes, path derived from PDF stem.
    AC-5.2 — YAML frontmatter + blank line + Markdown content.
    AC-5.3 — manifest.json written after all chunks.
    AC-5.5 — halt if output files exist and force=False.

    Args:
        chunks:          List of Chunk objects (post-HITL).
        source_pdf_path: Path to the original PDF (used to derive project_name).
        config:          Dict loaded by ingestor.load_config().
        force:           If True, overwrite existing .md files silently.

    Returns:
        Path to the project output directory.

    Raises:
        FileExistsError: if output files already exist and force=False.
        RuntimeError:    if an atomic write fails.
    """
    source_pdf_path = Path(source_pdf_path)
    project_name = _derive_project_name(source_pdf_path)

    processed_root = Path(config["output"]["processed_root"])
    project_dir = processed_root / project_name
    project_dir.mkdir(parents=True, exist_ok=True)

    # AC-5.5 — pre-flight conflict check
    if not force:
        conflicts = [
            project_dir / f"{chunk.chunk_id}.md"
            for chunk in chunks
            if (project_dir / f"{chunk.chunk_id}.md").exists()
        ]
        if conflicts:
            conflict_list = "\n  ".join(str(p) for p in conflicts)
            raise FileExistsError(
                f"Output files already exist. Pass --force to overwrite:\n"
                f"  {conflict_list}"
            )

    # Write each chunk atomically
    for chunk in chunks:
        _write_chunk(chunk, project_dir)

    # AC-5.3 — manifest.json
    _write_manifest(chunks, source_pdf_path, project_name, project_dir, config)

    log.info(
        "Exported %d chunks to %s", len(chunks), project_dir
    )
    return project_dir


# ---------------------------------------------------------------------------
# AC-5.2 — individual chunk file
# ---------------------------------------------------------------------------

def _write_chunk(chunk: Any, project_dir: Path) -> None:
    """Write a single chunk as an atomic .md file (AC-5.1, AC-5.2)."""
    out_path = project_dir / f"{chunk.chunk_id}.md"

    # Build YAML frontmatter from chunk.metadata if available, else assemble
    if chunk.metadata:
        meta = dict(chunk.metadata)
    else:
        meta = _build_meta(chunk)

    yaml_text = yaml.safe_dump(
        meta, default_flow_style=False, allow_unicode=True, sort_keys=False
    )

    # AC-5.2 — `---\n{yaml}\n---\n\n{content}`
    file_content = f"---\n{yaml_text}---\n\n{chunk.content}\n"

    _atomic_write(out_path, file_content)
    log.debug("Wrote %s", out_path)


def _build_meta(chunk: Any) -> dict[str, Any]:
    """Assemble metadata dict directly from Chunk fields when chunk.metadata is empty."""
    return {
        "source_id":         chunk.source_id,
        "source_file":       chunk.source_file,
        "chunk_id":          chunk.chunk_id,
        "page_range":        chunk.page_range,
        "breadcrumb":        chunk.breadcrumb,
        "parent_header":     chunk.parent_header,
        "topic_category":    "",
        "technical_level":   "",
        "summary":           "",
        "confidence_score":  chunk.confidence_score,
        "extraction_engine": chunk.extraction_engine,
        "hitl_status":       chunk.hitl_status,
        "corrections_ref":   chunk.corrections_ref,
    }


# ---------------------------------------------------------------------------
# AC-5.3 — manifest.json
# ---------------------------------------------------------------------------

def _write_manifest(
    chunks: list[Any],
    source_pdf_path: Path,
    project_name: str,
    project_dir: Path,
    config: dict[str, Any],
) -> None:
    """Write manifest.json to the project output directory (AC-5.3)."""
    manifest = {
        "source_file":   source_pdf_path.name,
        "project_name":  project_name,
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "total_chunks":  len(chunks),
        "chunks": [
            {
                "chunk_id":        chunk.chunk_id,
                "file":            str(project_dir / f"{chunk.chunk_id}.md"),
                "page_range":      chunk.page_range,
                "hitl_status":     chunk.hitl_status,
                "confidence_score": chunk.confidence_score,
            }
            for chunk in chunks
        ],
    }

    manifest_path = project_dir / Path(config["output"]["manifest_filename"]).name
    manifest_text = json.dumps(manifest, indent=2, ensure_ascii=False)
    _atomic_write(manifest_path, manifest_text)
    log.info("Manifest written to %s", manifest_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _derive_project_name(pdf_path: Path) -> str:
    """Derive a filesystem-safe project name from the PDF stem (AC-5.1)."""
    return pdf_path.stem.lower().replace(" ", "_")


def _atomic_write(path: Path, content: str) -> None:
    """Write *content* to *path* atomically via a .tmp intermediate (AC-5.1)."""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as fh:
            fh.write(content)
        tmp_path.rename(path)  # POSIX atomic
    except OSError as exc:
        raise RuntimeError(
            f"Failed to write {path}: {exc}"
        ) from exc
