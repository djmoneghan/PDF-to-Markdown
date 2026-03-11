# ingestor/pipeline.py
# Pipeline orchestrator: wraps 6-stage conversion pipeline with callback support.

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from ingestor.config import load_config
from ingestor.extractor import extract
from ingestor.chunker import chunk as chunk_doc
from ingestor.metadata import generate_metadata
from ingestor.hitl_base import HitlReviewBackend
from ingestor.exporter import export as export_chunks

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Type aliases for callbacks
# ---------------------------------------------------------------------------

# Callback: (stage_name: str, current: int, total: int) -> None
ProgressCallback = Callable[[str, int, int], None]

# Callback: (chunk_id: str, markdown_preview: str) -> None
ChunkReadyCallback = Callable[[str, str], None]

# Callback: (output_dir: Path) -> None
CompleteCallback = Callable[[Path], None]

# Callback: (exception: Exception) -> None
ErrorCallback = Callable[[Exception], None]


# ---------------------------------------------------------------------------
# Pipeline Orchestrator
# ---------------------------------------------------------------------------

class PipelineOrchestrator:
    """
    Wraps the 6-stage document conversion pipeline with callback support.

    Stages:
      1. Load and validate config
      2. Extract PDF → DocumentContent
      3. Chunk → list[Chunk]
      4. Generate metadata via Ollama
      5. HITL review (CLI or GUI)
      6. Export → .md files + manifest.json

    Callbacks allow GUI to inject custom UI updates without modifying the pipeline.
    """

    def __init__(
        self,
        on_progress: ProgressCallback | None = None,
        on_chunk_ready: ChunkReadyCallback | None = None,
        on_complete: CompleteCallback | None = None,
        on_error: ErrorCallback | None = None,
    ):
        """
        Initialize the orchestrator with optional callbacks.

        Args:
            on_progress: Called as (stage_name, current, total) during long operations.
            on_chunk_ready: Called as (chunk_id, markdown_preview) when chunk is ready.
            on_complete: Called as (output_dir) when pipeline completes successfully.
            on_error: Called as (exception) if any stage raises.
        """
        self.on_progress = on_progress or (lambda *args: None)
        self.on_chunk_ready = on_chunk_ready or (lambda *args: None)
        self.on_complete = on_complete or (lambda *args: None)
        self.on_error = on_error or (lambda *args: None)

    def run(
        self,
        pdf_path: Path | str,
        config_path: Path | str = "config.yaml",
        hitl_backend: HitlReviewBackend | None = None,
        project_name: str | None = None,
        force: bool = False,
    ) -> Path:
        """
        Execute the full pipeline: extract → chunk → metadata → HITL → export.

        Args:
            pdf_path: Path to the source PDF document.
            config_path: Path to config.yaml (default: ./config.yaml).
            hitl_backend: HITL implementation (CLI or GUI). If None, uses CLI backend.
            project_name: Override the project name derived from PDF filename.
            force: If True, overwrite existing output files without prompting.

        Returns:
            Path to the output project directory.

        Raises:
            Various exceptions from each stage (caught and passed to on_error callback).
        """
        pdf_path = Path(pdf_path)

        try:
            # Stage 1 — Load config
            log.debug(f"Loading config from {config_path}")
            self.on_progress("Loading config", 1, 6)
            config = load_config(config_path)

            # Apply project_name override if provided
            if project_name:
                config.setdefault("_runtime", {})["project_name"] = project_name

            # Stage 2 — Extract PDF
            log.debug(f"Extracting {pdf_path.name}")
            self.on_progress("Extracting PDF", 2, 6)
            doc = extract(pdf_path, config)
            log.info(
                f"Extracted {len(doc.pages)} page(s), "
                f"{len(doc.tables)} table(s), "
                f"{len(doc.formula_blocks)} formula(s)"
            )

            # Stage 3 — Chunk
            log.debug("Chunking document")
            self.on_progress("Chunking document", 3, 6)
            chunks = chunk_doc(doc, config)
            log.info(f"Produced {len(chunks)} chunk(s)")

            # Stage 4 — Metadata generation
            log.debug("Generating metadata")
            self.on_progress("Generating metadata", 4, 6)
            for i, ch in enumerate(chunks, 1):
                try:
                    meta = generate_metadata(ch, config)
                except Exception as exc:  # noqa: BLE001
                    log.warning(f"Metadata generation failed for {ch.chunk_id}: {exc}")
                    meta = {}
                ch.metadata = meta
                ch.confidence_score = float(meta.get("confidence_score", 0.0))
                self.on_progress(f"Metadata generation", i, len(chunks))
                self.on_chunk_ready(ch.chunk_id, ch.content[:500])  # brief preview

            # Stage 5 — HITL review
            log.debug("Starting HITL review")
            self.on_progress("HITL review", 5, 6)
            if hitl_backend is None:
                # Default to CLI HITL backend
                from ingestor.hitl import CliHitlReview
                hitl_backend = CliHitlReview()
            reviewed = hitl_backend.review(chunks, config)

            # Stage 6 — Export
            log.debug("Exporting chunks")
            self.on_progress("Exporting chunks", 6, 6)

            # Synthesise effective_pdf_path if project_name was overridden
            effective_pdf_path = pdf_path
            if project_name:
                effective_pdf_path = pdf_path.with_name(
                    project_name.replace(" ", "_").lower() + pdf_path.suffix
                )

            output_dir = export_chunks(reviewed, effective_pdf_path, config, force=force)
            log.info(f"Export complete: {output_dir}")
            self.on_complete(output_dir)

            return output_dir

        except Exception as exc:  # noqa: BLE001
            log.exception(f"Pipeline failed: {exc}")
            self.on_error(exc)
            raise
