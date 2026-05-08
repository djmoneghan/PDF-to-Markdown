# ingestor/hitl.py
# HITL review interface using Rich + Prompt Toolkit.

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from rich.markup import escape as markup_escape

if TYPE_CHECKING:
    from ingestor import Chunk, Config

log = logging.getLogger(__name__)

# Maximum lines of chunk content shown in the display panel.
# Long chunks are truncated here only — chunk.content is never modified.
_DISPLAY_MAX_LINES = 50


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_hitl_review(
    chunks: list[Chunk],
    config: Config,
) -> list[Chunk]:
    """Present each chunk for human review; return the reviewed chunk list.

    For each chunk:
      - If confidence_score >= config.hitl["auto_accept_above"]: auto-accept and log.
      - Otherwise: render the side-by-side panel and wait for a keystroke.

    Keystrokes:
      a — accept      e — edit      f — flag      q — quit & save progress

    Args:
        chunks: Chunk objects with metadata already populated.
        config: typed Config object from ingestor.load_config / Config.from_dict.

    Returns:
        The same list with hitl_status (and corrections_ref) updated on each chunk.
    """
    from rich.console import Console
    from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn
    from prompt_toolkit.shortcuts import confirm

    auto_accept: float = float(config.hitl["auto_accept_above"])
    total: int = len(chunks)
    stats: dict[str, int] = {"accepted": 0, "edited": 0, "flagged": 0}

    console = Console()

    # --- Resume from checkpoint if one exists ---
    start_index = 0
    saved = _load_progress(config)
    if saved is not None:
        try:
            do_resume = confirm(
                f"Resume from saved checkpoint? (saved {saved.get('saved_at', '?')})"
            )
        except (KeyboardInterrupt, EOFError):
            do_resume = False
        if do_resume:
            start_index = _apply_progress(chunks, saved)
            console.print(
                f"[green]Resuming from chunk {start_index + 1} of {total}.[/green]"
            )
            for c in chunks[:start_index]:
                if c.hitl_status in stats:
                    stats[c.hitl_status] += 1

    try:
        with Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            console=console,
            transient=False,
        ) as progress:
            task = progress.add_task(
                f"Chunk {start_index + 1} of {total}",
                total=total,
                completed=start_index,
            )

            for i in range(start_index, total):
                chunk = chunks[i]
                progress.update(task, description=f"Chunk {i + 1} of {total}")

                # Auto-accept above threshold
                if chunk.confidence_score >= auto_accept:
                    log.info(
                        "auto-accepted %s (score: %.2f)",
                        chunk.chunk_id,
                        chunk.confidence_score,
                    )
                    progress.console.print(
                        f"✓ auto-accepted {markup_escape(chunk.chunk_id)} "
                        f"(score: {chunk.confidence_score:.2f})"
                    )
                    chunk.hitl_status = "accepted"
                    stats["accepted"] += 1
                    progress.advance(task)
                    continue

                # Display and action loop
                _display_chunk(progress.console, chunk, i + 1, total)

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
                        # edit cancelled — redisplay so operator can try again
                        _display_chunk(progress.console, chunk, i + 1, total)

                    elif action == "f":
                        _run_flag(progress.console, chunk, config)
                        stats["flagged"] += 1
                        break

                    elif action == "q":
                        _save_progress(chunks, i, config)
                        console.print(
                            f"\n[bold yellow]Progress saved.[/bold yellow] "
                            f"{stats['accepted']} accepted, "
                            f"{stats['edited']} edited, "
                            f"{stats['flagged']} flagged. "
                            f"Stopped at chunk {i + 1} of {total}."
                        )
                        return chunks

                progress.advance(task)

    except KeyboardInterrupt:
        pending = sum(1 for c in chunks if c.hitl_status == "pending")
        console.print(
            f"\nSession interrupted. "
            f"{stats['accepted']} accepted, {stats['edited']} edited, "
            f"{stats['flagged']} flagged, {pending} pending."
        )

    return chunks


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def _display_chunk(
    console: Any,
    chunk: Chunk,
    n: int,
    total: int,
) -> None:
    """Render header row, two-column content/YAML panel, and footer legend.

    Rendering rules:
    - chunk.content is truncated to _DISPLAY_MAX_LINES for the display only.
    - chunk_id and breadcrumb are markup-escaped before use in panel titles.
    - Content is rendered via rich.Syntax (markdown) — no Rich markup parsing.
    - YAML metadata is rendered via rich.Syntax (yaml).
    """
    from rich.columns import Columns
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.text import Text

    # --- Header row ---
    status_colour = {
        "pending":  "yellow",
        "accepted": "green",
        "edited":   "cyan",
        "flagged":  "red",
    }.get(chunk.hitl_status, "white")

    console.rule(
        f"Chunk {n} of {total}  │  "
        f"{markup_escape(chunk.chunk_id)}  │  "
        f"confidence: {chunk.confidence_score:.2f}  │  "
        f"[{status_colour}]{chunk.hitl_status.upper()}[/{status_colour}]"
    )

    # --- Content panel (left) ---
    content_lines = chunk.content.splitlines()
    truncated = False
    if len(content_lines) > _DISPLAY_MAX_LINES:
        display_content = "\n".join(content_lines[:_DISPLAY_MAX_LINES])
        truncated = True
    else:
        display_content = chunk.content

    left_renderable: Any = Syntax(
        display_content,
        "markdown",
        theme="monokai",
        word_wrap=True,
    )
    content_subtitle = f"Pages {chunk.page_range[0]}–{chunk.page_range[1]}"
    if truncated:
        omitted = len(content_lines) - _DISPLAY_MAX_LINES
        content_subtitle += f"  [dim](+{omitted} lines hidden)[/dim]"

    left = Panel(
        left_renderable,
        title="[bold]Content (Markdown)[/bold]",
        subtitle=content_subtitle,
        expand=True,
    )

    # --- Metadata panel (right) ---
    meta = chunk.metadata if chunk.metadata else _partial_meta(chunk)
    yaml_text = yaml.safe_dump(
        meta, default_flow_style=False, allow_unicode=True, sort_keys=False
    )
    right = Panel(
        Syntax(yaml_text, "yaml", theme="monokai", word_wrap=True),
        title="[bold]Metadata (YAML)[/bold]",
        subtitle=markup_escape(chunk.breadcrumb) if chunk.breadcrumb else "(no breadcrumb)",
        expand=True,
    )

    console.print(Columns([left, right]))

    # --- Footer legend ---
    console.print(
        "[bold]\\[A][/bold] Accept   "
        "[bold]\\[E][/bold] Edit   "
        "[bold]\\[F][/bold] Flag   "
        "[bold]\\[Q][/bold] Quit & Save Progress"
    )


# ---------------------------------------------------------------------------
# Keystroke capture
# ---------------------------------------------------------------------------

def _get_action(console: Any) -> str:
    """Block until the operator presses a, e, f, or q (case-insensitive).

    No Enter required. Returns the lowercase key character.
    """
    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    kb = KeyBindings()

    for key, result in [("a", "a"), ("A", "a"), ("e", "e"), ("E", "e"),
                        ("f", "f"), ("F", "f"), ("q", "q"), ("Q", "q")]:
        # closure capture requires default argument trick
        def _make_handler(r: str):
            def _handler(event: Any) -> None:
                event.app.exit(result=r)
            return _handler
        kb.add(key, eager=True)(_make_handler(result))

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
# Edit flow
# ---------------------------------------------------------------------------

def _run_edit(console: Any, chunk: Chunk, config: Config) -> bool:
    """Open chunk content in $EDITOR, then prompt for metadata edits inline.

    Flow:
      1. Write chunk.content to a named temp file (.md).
      2. Open in $EDITOR (fallback: nano). Wait for editor to exit.
      3. Read back edited content; detect if content changed.
      4. Prompt operator field-by-field for metadata; detect changes.
      5. If nothing changed, return False (no-op).
      6. Validate new metadata, record correction, commit to chunk.

    Returns True if edit was committed; False if cancelled or no changes.
    """
    from prompt_toolkit.shortcuts import prompt as pt_prompt
    from ingestor.metadata import _validate_schema

    original_content = chunk.content
    original_meta: dict[str, Any] = (
        dict(chunk.metadata) if chunk.metadata else _partial_meta(chunk)
    )

    # Steps 1 & 2 — content editor
    editor = os.environ.get("EDITOR", "nano")
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(chunk.content)

        result = subprocess.run([editor, str(tmp_path)])
        if result.returncode != 0:
            console.print(
                f"[yellow]Editor exited with code {result.returncode}. "
                f"Edit cancelled.[/yellow]"
            )
            return False

        # Step 3 — read back
        edited_content = tmp_path.read_text(encoding="utf-8")

    except (OSError, KeyboardInterrupt) as exc:
        console.print(f"[yellow]Edit cancelled ({exc}).[/yellow]")
        return False

    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    content_changed = edited_content.strip() != original_content.strip()
    if content_changed:
        chunk.content = edited_content

    # Step 4 — inline metadata prompts
    _SKIP_FIELDS = {"source_id", "source_file", "chunk_id", "extraction_engine",
                    "corrections_ref"}
    new_meta: dict[str, Any] = dict(original_meta)
    meta_changed = False

    console.print(
        "\n[bold]Metadata review[/bold] — Enter to keep, or type a replacement:"
    )
    for field_name, original_value in original_meta.items():
        if field_name in _SKIP_FIELDS:
            continue
        display_val = str(original_value) if original_value is not None else ""
        try:
            user_input = pt_prompt(
                f"  {field_name} [{display_val}]: ",
                default=display_val,
            ).strip()
        except (KeyboardInterrupt, EOFError):
            console.print("[yellow]Metadata edit interrupted.[/yellow]")
            if content_changed:
                # Commit just the content change; keep original metadata
                chunk.hitl_status = "edited"
                chunk.corrections_ref = _record_correction(
                    chunk.chunk_id, chunk.source_id, "edited",
                    original_meta, None, None, config,
                )
                return True
            return False

        if user_input == display_val:
            continue

        meta_changed = True
        if field_name == "confidence_score":
            try:
                new_meta[field_name] = float(user_input)
            except ValueError:
                console.print(
                    f"[red]Invalid float for {field_name} — keeping original.[/red]"
                )
                meta_changed = len(new_meta) != len(original_meta)  # re-check
        elif field_name == "page_range":
            try:
                cleaned = user_input.strip("[] ")
                new_meta[field_name] = [int(x.strip()) for x in cleaned.split(",")]
            except ValueError:
                console.print(
                    f"[red]Invalid page_range — keeping original.[/red]"
                )
        else:
            new_meta[field_name] = user_input

    if not content_changed and not meta_changed:
        console.print("[yellow]No changes made.[/yellow]")
        return False

    # Step 5 — validate
    try:
        _validate_schema(new_meta)
    except (ValueError, KeyError) as exc:
        console.print(f"[red]Schema validation failed: {exc}. Edit not saved.[/red]")
        if content_changed:
            chunk.content = original_content  # roll back content too
        return False

    # Step 6 — commit
    chunk.metadata = new_meta
    chunk.confidence_score = float(new_meta.get("confidence_score", chunk.confidence_score))
    chunk.hitl_status = "edited"
    chunk.corrections_ref = _record_correction(
        chunk.chunk_id, chunk.source_id, "edited",
        original_meta, new_meta, None, config,
    )
    return True


# ---------------------------------------------------------------------------
# Flag flow
# ---------------------------------------------------------------------------

def _run_flag(console: Any, chunk: Chunk, config: Config) -> None:
    """Mark chunk as flagged and write a corrections record."""
    from prompt_toolkit.shortcuts import prompt as pt_prompt

    try:
        reason_raw = pt_prompt("Reason for flagging (Enter to skip): ")
        reason: str | None = reason_raw.strip() or None
    except (KeyboardInterrupt, EOFError):
        reason = None

    chunk.hitl_status = "flagged"
    chunk.corrections_ref = _record_correction(
        chunk.chunk_id, chunk.source_id, "flagged",
        chunk.metadata if chunk.metadata else _partial_meta(chunk),
        None, reason, config,
    )


# ---------------------------------------------------------------------------
# Progress persistence
# ---------------------------------------------------------------------------

def _progress_path(config: Config) -> Path:
    return Path(config.output["processed_root"]) / "review_progress.json"


def _save_progress(chunks: list[Chunk], current_index: int, config: Config) -> None:
    path = _progress_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "resume_index": current_index,
        "statuses": {c.chunk_id: c.hitl_status for c in chunks},
        "corrections_refs": {
            c.chunk_id: c.corrections_ref
            for c in chunks
            if c.corrections_ref is not None
        },
    }
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    tmp.rename(path)
    log.info("Progress saved to %s (resume index %d).", path, current_index)


def _load_progress(config: Config) -> dict[str, Any] | None:
    path = _progress_path(config)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not read review_progress.json (%s) — starting fresh.", exc)
        return None


def _apply_progress(chunks: list[Chunk], saved: dict[str, Any]) -> int:
    """Restore hitl_status and corrections_ref; return the resume index."""
    statuses: dict[str, str] = saved.get("statuses", {})
    refs: dict[str, str] = saved.get("corrections_refs", {})
    for chunk in chunks:
        if chunk.chunk_id in statuses:
            chunk.hitl_status = statuses[chunk.chunk_id]
        if chunk.chunk_id in refs:
            chunk.corrections_ref = refs[chunk.chunk_id]
    return int(saved.get("resume_index", 0))


# ---------------------------------------------------------------------------
# Correction record
# ---------------------------------------------------------------------------

def _record_correction(
    chunk_id: str,
    source_id: str,
    action: str,
    original_yaml: dict[str, Any] | None,
    corrected_yaml: dict[str, Any] | None,
    reason: str | None,
    config: Config,
) -> str:
    """Append one correction record to corrections.json; return its record_id."""
    from ingestor.corrections import append_correction

    project_dir = Path(config.output["processed_root"]) / source_id
    project_dir.mkdir(parents=True, exist_ok=True)

    record_id = str(uuid.uuid4())
    append_correction(
        {
            "record_id":      record_id,
            "chunk_id":       chunk_id,
            "timestamp":      datetime.now(timezone.utc).isoformat(),
            "action":         action,
            "original_yaml":  original_yaml,
            "corrected_yaml": corrected_yaml,
            "reason":         reason,
        },
        {"output": config.output},
        project_dir,
    )
    return record_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _partial_meta(chunk: Chunk) -> dict[str, Any]:
    """Fallback metadata dict built from Chunk fields when chunk.metadata is empty."""
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
