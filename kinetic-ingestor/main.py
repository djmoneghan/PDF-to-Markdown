#!/usr/bin/env python3
# main.py
# CLI entry point for The Kinetic Ingestor.

from __future__ import annotations

import argparse
import logging
import sys
import traceback
from pathlib import Path

# Rich console used for all user-facing output
from rich.console import Console

console = Console(stderr=False)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kinetic-ingestor",
        description=(
            "The Kinetic Ingestor — convert PDF documents into "
            "semantically chunked, metadata-enriched Markdown files."
        ),
    )
    parser.add_argument(
        "pdf_path",
        type=Path,
        help="Path to the source PDF document.",
    )
    parser.add_argument(
        "--project",
        metavar="NAME",
        default=None,
        help=(
            "Override the project name derived from the PDF filename. "
            "Used as the output subdirectory name."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite existing .md output files without prompting.",
    )
    parser.add_argument(
        "--overwrite-corrections",
        action="store_true",
        default=False,
        dest="overwrite_corrections",
        help=(
            "Allow overwriting existing corrections.json records that share "
            "a record_id. Default is append-only."
        ),
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Print full tracebacks on error instead of clean messages.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        metavar="PATH",
        help="Path to config.yaml (default: ./config.yaml).",
    )
    return parser


def _configure_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s [%(name)s] %(message)s",
    )


def main(argv: list[str] | None = None) -> int:  # returns exit code
    parser = _build_parser()
    args = parser.parse_args(argv)

    _configure_logging(args.debug)

    try:
        return _run_pipeline(args)
    except KeyboardInterrupt:
        # Pipeline stages handle KeyboardInterrupt internally (hitl.py).
        # This guard catches interrupts that happen outside the HITL loop.
        console.print("\n[yellow]Interrupted.[/yellow]")
        return 130
    except Exception as exc:  # noqa: BLE001
        if args.debug:
            traceback.print_exc()
        else:
            console.print(f"[bold red]Error:[/bold red] {exc}")
        return 1


def _run_pipeline(args: argparse.Namespace) -> int:
    """Execute the pipeline using PipelineOrchestrator with CLI-based callbacks."""
    from ingestor.pipeline import PipelineOrchestrator

    def log_progress(stage: str, current: int, total: int) -> None:
        """Log progress for each stage."""
        # Only log when starting a new stage (current=1) to avoid spam
        if current == 1:
            console.print(f"[bold]{stage}[/bold] …")

    def log_chunk_ready(chunk_id: str, preview: str) -> None:
        """Log chunk readiness (used during metadata generation)."""
        # Brief feedback during metadata generation
        pass  # Handled by verbose progress output in loop

    def log_complete(output_dir) -> None:
        """Log successful completion."""
        console.print(f"[bold green]Done.[/bold green] Output: [cyan]{output_dir}[/cyan]")

    def log_error(exc: Exception) -> None:
        """Log errors (re-raised after callback)."""
        pass  # Exception will be re-raised and handled by outer try/except

    orchestrator = PipelineOrchestrator(
        on_progress=log_progress,
        on_chunk_ready=log_chunk_ready,
        on_complete=log_complete,
        on_error=log_error,
    )

    try:
        orchestrator.run(
            pdf_path=args.pdf_path,
            config_path=args.config,
            project_name=args.project,
            force=args.force,
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        if args.debug:
            raise
        else:
            console.print(f"[bold red]Error:[/bold red] {exc}")
            return 1


if __name__ == "__main__":
    sys.exit(main())
