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
    # ------------------------------------------------------------------
    # Stage 1 — load and validate config.yaml
    # ------------------------------------------------------------------
    from ingestor import load_config

    console.print(f"[bold]Kinetic Ingestor[/bold] — loading config from {args.config}")
    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[bold red]Config error:[/bold red] {exc}")
        return 1

    # Apply --project override to config so downstream modules see it
    if args.project:
        config.setdefault("_runtime", {})["project_name"] = args.project

    # ------------------------------------------------------------------
    # Stage 2 — extract PDF → DocumentContent
    # ------------------------------------------------------------------
    from ingestor.extractor import extract

    pdf_path = args.pdf_path.resolve()
    console.print(f"Extracting [cyan]{pdf_path.name}[/cyan] …")
    doc = extract(pdf_path, config)
    console.print(
        f"  Extracted {len(doc.pages)} page(s), "
        f"{len(doc.tables)} table(s), "
        f"{len(doc.formula_blocks)} formula(s) "
        f"[dim](engine: {doc.extraction_engine})[/dim]"
    )

    # ------------------------------------------------------------------
    # Stage 3 — chunk → list[Chunk]
    # ------------------------------------------------------------------
    from ingestor.chunker import chunk as chunk_doc

    console.print("Chunking document …")
    chunks = chunk_doc(doc, config)
    console.print(f"  Produced {len(chunks)} chunk(s).")

    # ------------------------------------------------------------------
    # Stage 4 — metadata generation (Ollama)
    # ------------------------------------------------------------------
    from ingestor.metadata import generate_metadata

    console.print("Generating metadata via Ollama …")
    for i, ch in enumerate(chunks, 1):
        try:
            meta = generate_metadata(ch, config)
        except Exception as exc:  # noqa: BLE001
            console.print(
                f"  [yellow]Warning:[/yellow] metadata generation failed for "
                f"{ch.chunk_id}: {exc}"
            )
            meta = {}
        ch.metadata = meta
        ch.confidence_score = float(meta.get("confidence_score", 0.0))
        console.print(
            f"  [{i}/{len(chunks)}] {ch.chunk_id} "
            f"— confidence {ch.confidence_score:.2f}",
            end="\r",
        )
    console.print()  # newline after \r progress

    # ------------------------------------------------------------------
    # Stage 5 — HITL review loop
    # ------------------------------------------------------------------
    from ingestor.hitl import run_review

    console.print("Starting HITL review …")
    reviewed = run_review(chunks, config)

    # ------------------------------------------------------------------
    # Stage 6 — export .md files + manifest.json
    # ------------------------------------------------------------------
    from ingestor.exporter import export

    # Honour --project override: patch source_pdf_path stem if needed
    effective_pdf_path = pdf_path
    if args.project:
        # Synthesise a path whose stem matches the desired project_name so
        # _derive_project_name() produces the right directory.
        effective_pdf_path = pdf_path.with_name(
            args.project.replace(" ", "_").lower() + pdf_path.suffix
        )

    console.print("Exporting chunks …")
    try:
        output_dir = export(reviewed, effective_pdf_path, config, force=args.force)
    except FileExistsError as exc:
        console.print(
            f"[bold red]Output conflict:[/bold red] {exc}\n"
            "Re-run with [bold]--force[/bold] to overwrite."
        )
        return 1

    console.print(
        f"[bold green]Done.[/bold green] "
        f"{len(reviewed)} chunk(s) written to [cyan]{output_dir}[/cyan]."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
