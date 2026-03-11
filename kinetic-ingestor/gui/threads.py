# gui/threads.py
# QThread workers for pipeline execution and file watching.

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal

from ingestor.pipeline import PipelineOrchestrator

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline Worker
# ---------------------------------------------------------------------------

class PipelineWorker(QThread):
    """
    QThread worker that executes the full conversion pipeline.

    Emits signals for progress, chunk readiness, completion, and errors.
    """

    # Signals
    progress = pyqtSignal(str, int, int)  # (stage_name, current, total)
    chunk_ready = pyqtSignal(str, str)  # (chunk_id, markdown_preview)
    complete = pyqtSignal(Path)  # (output_dir)
    error = pyqtSignal(Exception)  # (exception)

    def __init__(self, pdf_path: Path | str, config_path: Path | str = "config.yaml",
                 project_name: str | None = None, force: bool = False):
        """
        Initialize worker with pipeline parameters.

        Args:
            pdf_path: Path to the source PDF.
            config_path: Path to config.yaml.
            project_name: Optional override for project directory name.
            force: If True, overwrite existing output files.
        """
        super().__init__()
        self.pdf_path = Path(pdf_path)
        self.config_path = Path(config_path)
        self.project_name = project_name
        self.force = force
        self._is_running = True

    def run(self) -> None:
        """Execute the pipeline in the background thread."""
        try:
            orchestrator = PipelineOrchestrator(
                on_progress=self._on_progress,
                on_chunk_ready=self._on_chunk_ready,
                on_complete=self._on_complete,
                on_error=self._on_error,
            )

            output_dir = orchestrator.run(
                pdf_path=self.pdf_path,
                config_path=self.config_path,
                project_name=self.project_name,
                force=self.force,
            )

        except Exception as exc:  # noqa: BLE001
            log.exception(f"Pipeline worker error: {exc}")
            if self._is_running:
                self.error.emit(exc)

    def _on_progress(self, stage: str, current: int, total: int) -> None:
        """Callback for pipeline progress."""
        if self._is_running:
            self.progress.emit(stage, current, total)

    def _on_chunk_ready(self, chunk_id: str, preview: str) -> None:
        """Callback for chunk readiness."""
        if self._is_running:
            self.chunk_ready.emit(chunk_id, preview)

    def _on_complete(self, output_dir: Path) -> None:
        """Callback for pipeline completion."""
        if self._is_running:
            self.complete.emit(output_dir)

    def _on_error(self, exc: Exception) -> None:
        """Callback for pipeline errors."""
        if self._is_running:
            self.error.emit(exc)

    def stop(self) -> None:
        """Signal the worker to stop processing."""
        self._is_running = False
        self.wait()


# ---------------------------------------------------------------------------
# File Watcher Worker
# ---------------------------------------------------------------------------

class FileWatcherWorker(QThread):
    """
    QThread worker that monitors a directory for new PDF files.

    Uses polling (simple, no external dependencies) to detect new files.
    """

    # Signal: (pdf_path: Path)
    pdf_detected = pyqtSignal(Path)

    def __init__(self, watch_dir: Path | str, poll_interval_ms: int = 1000):
        """
        Initialize the file watcher.

        Args:
            watch_dir: Directory to monitor for PDFs.
            poll_interval_ms: Poll interval in milliseconds (default 1 second).
        """
        super().__init__()
        self.watch_dir = Path(watch_dir)
        self.poll_interval_ms = poll_interval_ms
        self._is_running = False
        self._last_mtimes: dict[Path, float] = {}

    def run(self) -> None:
        """Monitor the directory for new PDF files."""
        self._is_running = True
        self.watch_dir.mkdir(parents=True, exist_ok=True)

        # Initial scan to establish baseline mtimes
        for pdf_file in self.watch_dir.glob("*.pdf"):
            self._last_mtimes[pdf_file] = pdf_file.stat().st_mtime

        while self._is_running:
            try:
                # Check for new or modified PDFs
                for pdf_file in self.watch_dir.glob("*.pdf"):
                    if not pdf_file.is_file():
                        continue

                    current_mtime = pdf_file.stat().st_mtime
                    last_mtime = self._last_mtimes.get(pdf_file)

                    if last_mtime is None or current_mtime > last_mtime:
                        log.debug(f"New/modified PDF detected: {pdf_file}")
                        self._last_mtimes[pdf_file] = current_mtime
                        if self._is_running:
                            self.pdf_detected.emit(pdf_file)

                # Check for deleted PDFs
                for pdf_file in list(self._last_mtimes.keys()):
                    if not pdf_file.exists():
                        del self._last_mtimes[pdf_file]

            except Exception as exc:  # noqa: BLE001
                log.warning(f"File watcher error: {exc}")

            # Sleep before next poll
            if self._is_running:
                self.msleep(self.poll_interval_ms)

    def stop(self) -> None:
        """Stop the file watcher."""
        self._is_running = False
        self.wait()
