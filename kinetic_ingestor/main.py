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
        nargs="?",
        default=None,
        help="Path to the source PDF document. Omit when using --batch or --batch-manifest.",
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
    parser.add_argument(
        "--no-hitl",
        action="store_true",
        default=False,
        dest="no_hitl",
        help=(
            "Skip interactive HITL review and auto-accept all chunks regardless "
            "of confidence score. Intended for automated pipelines and CI testing."
        ),
    )
    parser.add_argument(
        "--export-dataset",
        metavar="PATH",
        default=None,
        dest="export_dataset",
        help=(
            "Phase 3: export the corrections corpus as a training JSONL to PATH. "
            "Skips PDF ingestion entirely when specified. "
            "Also generates a corrections.Modelfile alongside the JSONL."
        ),
    )
    parser.add_argument(
        "--dataset-fmt",
        choices=["alpaca", "openai"],
        default="alpaca",
        dest="dataset_fmt",
        help="JSONL format for --export-dataset: 'alpaca' (default) or 'openai'.",
    )
    parser.add_argument(
        "--base-model",
        default="llama3.1:8b",
        dest="base_model",
        help=(
            "Base Ollama model name for the generated Modelfile "
            "(default: llama3.1:8b). Used only with --export-dataset; the "
            "Modelfile is consumed by an Ollama fine-tuning workflow that is "
            "separate from KI's own metadata-generation backend."
        ),
    )
    parser.add_argument(
        "--batch",
        metavar="DIR",
        default=None,
        dest="batch",
        help=(
            "Phase 6: batch mode — process all PDFs in DIR without HITL review. "
            "Chunks are written with hitl_status=pending for later web UI review. "
            "Mutually exclusive with pdf_path."
        ),
    )
    parser.add_argument(
        "--batch-manifest",
        metavar="FILE",
        default=None,
        dest="batch_manifest",
        help=(
            "Phase 6: batch mode — process PDFs listed in FILE. "
            "FILE may be a JSON array or one absolute path per line. "
            "Mutually exclusive with pdf_path."
        ),
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

    # --export-dataset bypasses the PDF pipeline entirely
    if getattr(args, "export_dataset", None):
        return _run_export_dataset(args)

    # --batch / --batch-manifest bypasses the single-doc pipeline
    if getattr(args, "batch", None) or getattr(args, "batch_manifest", None):
        return _run_batch(args)

    # Single-document mode requires pdf_path
    if args.pdf_path is None:
        parser.error(
            "pdf_path is required unless --batch, --batch-manifest, or "
            "--export-dataset is specified."
        )

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
    # Stage 4 — metadata generation (Puck orchestrator: Gemma 4 31B at :8080)
    # ------------------------------------------------------------------
    from ingestor.metadata import generate_metadata

    console.print("Generating metadata via local orchestrator …")
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
    # Stage 5 — HITL review loop (or auto-accept bypass with --no-hitl)
    # ------------------------------------------------------------------
    from ingestor import Config

    if args.no_hitl:
        console.print(
            "[dim]HITL review skipped (--no-hitl) — "
            "auto-accepting all chunks.[/dim]"
        )
        for ch in chunks:
            ch.hitl_status = "accepted"
            if ch.metadata:
                ch.metadata["hitl_status"] = "accepted"
        reviewed = chunks
    else:
        from ingestor.hitl import run_hitl_review
        console.print("Starting HITL review …")
        reviewed = run_hitl_review(chunks, Config.from_dict(config))

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


def _run_batch(args: argparse.Namespace) -> int:
    """Phase 6 — batch ingest a directory or manifest list of PDFs."""
    from ingestor import load_config
    from ingestor.extractor import extract
    from ingestor.chunker import chunk as chunk_doc
    from ingestor.metadata import generate_metadata
    from ingestor.exporter import export
    from ingestor import Config
    import json as _json

    # Resolve file list
    pdf_files: list[Path] = []

    if args.batch:
        batch_dir = Path(args.batch).resolve()
        if not batch_dir.is_dir():
            console.print(f"[bold red]Error:[/bold red] --batch path is not a directory: {batch_dir}")
            return 1
        pdf_files = sorted(batch_dir.glob("*.pdf"))
        if not pdf_files:
            console.print(f"[yellow]No PDF files found in {batch_dir}.[/yellow]")
            return 0

    elif args.batch_manifest:
        manifest_path = Path(args.batch_manifest).resolve()
        if not manifest_path.exists():
            console.print(f"[bold red]Error:[/bold red] Manifest not found: {manifest_path}")
            return 1
        raw = manifest_path.read_text(encoding="utf-8").strip()
        try:
            paths = _json.loads(raw)
            if not isinstance(paths, list):
                raise ValueError("JSON manifest must be an array")
        except (_json.JSONDecodeError, ValueError):
            # Fall back to one-path-per-line format
            paths = [line.strip() for line in raw.splitlines() if line.strip()]
        pdf_files = [Path(p) for p in paths]

    console.print(
        f"[bold]Kinetic Ingestor — Batch Mode[/bold] "
        f"({len(pdf_files)} file(s), HITL deferred)"
    )

    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[bold red]Config error:[/bold red] {exc}")
        return 1

    succeeded = 0
    failed = 0

    for i, pdf_path in enumerate(pdf_files, 1):
        console.print(f"\n[bold][{i}/{len(pdf_files)}][/bold] {pdf_path.name}")
        if not pdf_path.exists():
            console.print(f"  [red]File not found — skipping.[/red]")
            failed += 1
            continue

        try:
            doc = extract(pdf_path.resolve(), config)
            chunks = chunk_doc(doc, config)
            for ch in chunks:
                try:
                    meta = generate_metadata(ch, config)
                except Exception as meta_exc:
                    console.print(f"  [yellow]Metadata warning ({ch.chunk_id}): {meta_exc}[/yellow]")
                    meta = {}
                ch.metadata = meta
                ch.confidence_score = float(meta.get("confidence_score", 0.0))

            # Batch mode: mark all chunks pending (no HITL)
            for ch in chunks:
                ch.hitl_status = "pending"
                if ch.metadata:
                    ch.metadata["hitl_status"] = "pending"

            output_dir = export(chunks, pdf_path.resolve(), config, force=args.force)
            console.print(
                f"  [green]Done.[/green] {len(chunks)} chunk(s) → [cyan]{output_dir}[/cyan]"
            )
            succeeded += 1

        except Exception as exc:
            failed += 1
            if args.debug:
                import traceback as _tb
                _tb.print_exc()
            else:
                console.print(f"  [red]Failed:[/red] {exc}")

    console.print(
        f"\n[bold]Batch complete.[/bold] "
        f"[green]{succeeded} succeeded[/green], [red]{failed} failed[/red]. "
        f"Review pending chunks with the Phase 7 web UI."
    )
    return 0 if failed == 0 else 1


def _run_export_dataset(args: argparse.Namespace) -> int:
    """Phase 3 — export corrections corpus as a training dataset."""
    from ingestor import load_config
    from ingestor.dataset_builder import (
        aggregate_corrections,
        build_training_pairs,
        compute_stats,
        export_jsonl,
        generate_modelfile,
    )
    from pathlib import Path as _Path

    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[bold red]Config error:[/bold red] {exc}")
        return 1

    processed_root_raw = config["output"]["processed_root"]
    processed_root = _Path(processed_root_raw)
    if not processed_root.is_absolute():
        processed_root = (args.config.parent / processed_root_raw).resolve()

    output_path = _Path(args.export_dataset)

    console.print(f"[bold]Kinetic Ingestor[/bold] — exporting training dataset")
    console.print(f"  Source: [cyan]{processed_root}[/cyan]")
    console.print(f"  Output: [cyan]{output_path}[/cyan]")

    corrections = aggregate_corrections(processed_root)
    stats = compute_stats(corrections)

    console.print(
        f"  Found [bold]{stats['total_corrections']}[/bold] correction(s) across "
        f"{stats['documents_with_corrections']} document(s)."
    )

    if not corrections:
        console.print(
            "[yellow]No corrections to export.[/yellow] "
            "Ingest and review documents with [bold][E]dit[/bold] in HITL first."
        )
        return 0

    pairs = build_training_pairs(corrections, processed_root)
    n = export_jsonl(pairs, output_path, fmt=args.dataset_fmt)
    console.print(f"  Wrote [bold green]{n}[/bold green] training pair(s) to {output_path}.")

    mf_path = output_path.parent / "corrections.Modelfile"
    generate_modelfile(pairs, args.base_model, mf_path)
    console.print(f"  Modelfile written to [cyan]{mf_path}[/cyan].")
    console.print(
        f"\n  To load: [bold]ollama create ki-metadata-v2 -f {mf_path}[/bold]"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
