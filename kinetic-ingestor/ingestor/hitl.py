# ingestor/hitl.py
# HITL review interface using Rich + Prompt Toolkit.

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import yaml

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_review(chunks: list[Any], config: dict[str, Any]) -> list[Any]:
    """Present each chunk for human review; return the reviewed chunk list.

    For each chunk:
      - If confidence_score >= hitl.auto_accept_above → auto-accept (AC-4.3).
      - Otherwise → display side-by-side Rich panel (AC-4.1) and prompt for
        [A]ccept / [E]dit / [F]lag (AC-4.2).

    A Rich Progress bar shows "Chunk N of M" throughout (AC-4.6).
    Ctrl+C halts gracefully and prints a session summary (AC-4.7).

    Args:
        chunks: list of Chunk objects with chunk.metadata already populated.
        config: dict loaded by ingestor.load_config().

    Returns:
        The same list of chunks, each with hitl_status updated.
    """
    from rich.console import Console
    from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn

    auto_accept: float = float(config["hitl"]["auto_accept_above"])
    total: int = len(chunks)
    stats: dict[str, int] = {"accepted": 0, "edited": 0, "flagged": 0, "pending": 0}

    console = Console()

    try:
        with Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            console=console,
            transient=False,
        ) as progress:
            task = progress.add_task(f"Chunk 1 of {total}", total=total)

            for i, chunk in enumerate(chunks):
                progress.update(task, description=f"Chunk {i + 1} of {total}")

                # AC-4.3 — auto-accept above confidence threshold
                if chunk.confidence_score >= auto_accept:
                    progress.console.print(
                        f"✓ Auto-accepted {chunk.chunk_id} "
                        f"(confidence: {chunk.confidence_score:.2f})"
                    )
                    chunk.hitl_status = "accepted"
                    stats["accepted"] += 1
                    progress.advance(task)
                    continue

                # AC-4.1 — side-by-side display
                _display_chunk(progress.console, chunk, i + 1, total)

                # AC-4.2 — three-action loop
                while True:
                    action = _get_action(progress.console)
                    if action == "a":
                        chunk.hitl_status = "accepted"
                        stats["accepted"] += 1
                        break
                    elif action == "e":
                        if _run_edit(progress.console, chunk, config):
                            stats["edited"] += 1
                            break
                        # edit was cancelled — re-display prompt
                    elif action == "f":
                        _run_flag(progress.console, chunk, config)
                        stats["flagged"] += 1
                        break

                progress.advance(task)

    except KeyboardInterrupt:
        # AC-4.7 — graceful interruption: unprocessed chunks stay "pending"
        for c in chunks:
            if c.hitl_status == "pending":
                stats["pending"] += 1
        console.print(
            f"\nSession interrupted. "
            f"{stats['accepted']} accepted, {stats['edited']} edited, "
            f"{stats['flagged']} flagged, {stats['pending']} pending."
        )

    return chunks


# ---------------------------------------------------------------------------
# AC-4.1 — side-by-side display
# ---------------------------------------------------------------------------

def _display_chunk(console: Any, chunk: Any, n: int, total: int) -> None:
    """Render Markdown content (left) and YAML frontmatter (right) side by side."""
    from rich.columns import Columns
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.syntax import Syntax

    meta = chunk.metadata if chunk.metadata else _partial_meta(chunk)
    yaml_text = yaml.safe_dump(meta, default_flow_style=False, allow_unicode=True,
                               sort_keys=False)

    left = Panel(
        Markdown(chunk.content),
        title=f"[bold]{chunk.chunk_id}[/bold] — Content",
        subtitle=f"Pages {chunk.page_range[0]}–{chunk.page_range[1]}",
        expand=True,
    )
    right = Panel(
        Syntax(yaml_text, "yaml", theme="monokai", word_wrap=True),
        title="Proposed YAML",
        subtitle=chunk.breadcrumb,
        expand=True,
    )
    console.print(Columns([left, right]))


# ---------------------------------------------------------------------------
# AC-4.2 — single-keystroke action prompt
# ---------------------------------------------------------------------------

def _get_action(console: Any) -> str:
    """Block until the user presses A, E, or F (case-insensitive). No Enter required."""
    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    console.print(
        "\n[bold]\\[A][/bold] Accept   "
        "[bold]\\[E][/bold] Edit   "
        "[bold]\\[F][/bold] Flag for re-extraction"
    )

    kb = KeyBindings()

    @kb.add("a", eager=True)
    @kb.add("A", eager=True)
    def _accept(event: Any) -> None:
        event.app.exit(result="a")

    @kb.add("e", eager=True)
    @kb.add("E", eager=True)
    def _edit(event: Any) -> None:
        event.app.exit(result="e")

    @kb.add("f", eager=True)
    @kb.add("F", eager=True)
    def _flag(event: Any) -> None:
        event.app.exit(result="f")

    @kb.add("c-c", eager=True)
    def _interrupt(event: Any) -> None:
        raise KeyboardInterrupt

    app = Application(
        layout=Layout(Window(FormattedTextControl(""))),
        key_bindings=kb,
        full_screen=False,
        mouse_support=False,
    )
    return app.run()


# ---------------------------------------------------------------------------
# AC-4.4 — inline YAML editor
# ---------------------------------------------------------------------------

def _run_edit(console: Any, chunk: Any, config: dict[str, Any]) -> bool:
    """Open the YAML block in an inline prompt_toolkit buffer for editing.

    Returns True if the edit was confirmed and valid; False if cancelled.
    """
    from prompt_toolkit.shortcuts import prompt as pt_prompt
    from ingestor.metadata import _validate_schema

    original_meta = dict(chunk.metadata) if chunk.metadata else _partial_meta(chunk)
    current_yaml = yaml.safe_dump(original_meta, default_flow_style=False,
                                  allow_unicode=True, sort_keys=False)

    while True:
        try:
            edited_yaml = pt_prompt(
                "Edit YAML (Ctrl+D to confirm, Ctrl+C to cancel):\n",
                default=current_yaml,
                multiline=True,
            )
        except (KeyboardInterrupt, EOFError):
            console.print("[yellow]Edit cancelled.[/yellow]")
            return False

        try:
            new_meta = yaml.safe_load(edited_yaml)
            if not isinstance(new_meta, dict):
                raise ValueError("Edited content must be a YAML mapping.")
            _validate_schema(new_meta)
        except (yaml.YAMLError, ValueError) as exc:
            console.print(f"[red]Invalid YAML — {exc}. Please correct and try again.[/red]")
            current_yaml = edited_yaml  # preserve user edits across re-open
            continue

        # Commit the edit
        chunk.metadata         = new_meta
        chunk.confidence_score = float(new_meta.get("confidence_score",
                                                     chunk.confidence_score))
        chunk.hitl_status      = "edited"
        chunk.corrections_ref  = _record_correction(chunk.chunk_id, "edited",
                                                     original_meta, new_meta,
                                                     reason=None, config=config)
        return True


# ---------------------------------------------------------------------------
# AC-4.5 — flag mode
# ---------------------------------------------------------------------------

def _run_flag(console: Any, chunk: Any, config: dict[str, Any]) -> None:
    """Save chunk as flagged and write a corrections record."""
    from prompt_toolkit.shortcuts import prompt as pt_prompt

    try:
        reason_raw = pt_prompt("Reason for flagging (optional — press Enter to skip): ")
        reason: str | None = reason_raw.strip() or None
    except (KeyboardInterrupt, EOFError):
        reason = None

    chunk.hitl_status     = "flagged"
    chunk.corrections_ref = _record_correction(
        chunk.chunk_id, "flagged",
        original_yaml=chunk.metadata if chunk.metadata else _partial_meta(chunk),
        corrected_yaml=None,
        reason=reason,
        config=config,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _record_correction(
    chunk_id: str,
    action: str,
    original_yaml: dict[str, Any] | None,
    corrected_yaml: dict[str, Any] | None,
    reason: str | None,
    config: dict[str, Any],
) -> str:
    """Write one correction record; return its record_id (AC-5.4 schema)."""
    from ingestor.corrections import append_correction

    record_id = str(uuid.uuid4())
    append_correction(
        {
            "record_id":     record_id,
            "chunk_id":      chunk_id,
            "timestamp":     datetime.now(timezone.utc).isoformat(),
            "action":        action,
            "original_yaml": original_yaml,
            "corrected_yaml": corrected_yaml,
            "reason":        reason,
        },
        config,
    )
    return record_id


def _partial_meta(chunk: Any) -> dict[str, Any]:
    """Build a partial metadata dict from chunk fields when chunk.metadata is empty."""
    return {
        "source_id":        chunk.source_id,
        "source_file":      chunk.source_file,
        "chunk_id":         chunk.chunk_id,
        "page_range":       chunk.page_range,
        "breadcrumb":       chunk.breadcrumb,
        "parent_header":    chunk.parent_header,
        "topic_category":   "",
        "technical_level":  "",
        "summary":          "",
        "confidence_score": chunk.confidence_score,
        "extraction_engine": chunk.extraction_engine,
        "hitl_status":      chunk.hitl_status,
        "corrections_ref":  chunk.corrections_ref,
    }
