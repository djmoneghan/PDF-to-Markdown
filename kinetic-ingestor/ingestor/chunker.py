# ingestor/chunker.py
# DocumentContent -> list[Chunk] semantic splitting.

from __future__ import annotations

import logging
import re
from typing import Any

from ingestor import Chunk, DocumentContent

log = logging.getLogger(__name__)

# Matches a markdown header line: one or more # followed by a space and text.
_HEADER_RE = re.compile(r'^(#{1,6})\s+(.+)$')


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def chunk(doc: DocumentContent, config: dict[str, Any]) -> list[Chunk]:
    """Split a DocumentContent into a list of Chunk objects.

    Pipeline:
      1. Build a flat list of (page_no, line) pairs from doc.pages.
      2. Split at configured header boundaries → raw sections.
      3. Enforce min/max token bounds (merge short, split long).
      4. Assign sequential chunk_NNN IDs and build Chunk dataclasses.

    Args:
        doc:    DocumentContent produced by ingestor.extractor.extract().
        config: dict loaded by ingestor.load_config().

    Returns:
        list[Chunk], ordered as they appear in the source document.
    """
    split_levels: list[str] = config["chunking"]["split_levels"]
    min_tokens: int = config["chunking"]["min_chunk_tokens"]
    max_tokens: int = config["chunking"]["max_chunk_tokens"]

    # Convert "#", "##" etc. to integer depths: {"#"} -> {1}, {"##"} -> {2}
    split_depths: set[int] = {len(s.strip()) for s in split_levels}

    # Step 1 — flat tagged lines
    tagged: list[tuple[int, str]] = _build_tagged_lines(doc)

    # Step 2 — split at header boundaries
    sections: list[dict] = _split_at_headers(tagged, split_depths)

    # Step 3 — token bounds
    sections = _apply_token_bounds(sections, min_tokens, max_tokens)

    # Step 4 — assign IDs, build Chunk objects
    return _build_chunks(sections, doc)


# ---------------------------------------------------------------------------
# Step 1 — flat tagged lines
# ---------------------------------------------------------------------------

def _build_tagged_lines(doc: DocumentContent) -> list[tuple[int, str]]:
    tagged: list[tuple[int, str]] = []
    for page_no, page_md in doc.pages:
        for line in page_md.split("\n"):
            tagged.append((page_no, line))
    return tagged


# ---------------------------------------------------------------------------
# Step 2 — split at header boundaries
# ---------------------------------------------------------------------------

def _split_at_headers(
    tagged: list[tuple[int, str]],
    split_depths: set[int],
) -> list[dict]:
    """Walk tagged lines; emit a new section at each split-depth header."""
    sections: list[dict] = []

    # Mutable state bucket (avoids nonlocal gymnastics)
    state: dict[str, Any] = {
        "lines": [],         # str lines accumulated for the current section
        "pages": [],         # page_no for each accumulated line
        "breadcrumb": "",
        "parent_header": "",
        "header_level": 0,
    }
    header_stack: list[tuple[int, str]] = []  # (level, text)

    def flush() -> None:
        content = "\n".join(state["lines"]).strip()
        if not content:
            return
        start_page = state["pages"][0] if state["pages"] else 1
        end_page   = state["pages"][-1] if state["pages"] else 1
        sections.append({
            "breadcrumb":    state["breadcrumb"],
            "parent_header": state["parent_header"],
            "header_level":  state["header_level"],
            "content":       content,
            "page_range":    [start_page, end_page],
        })
        state["lines"] = []
        state["pages"] = []

    for page_no, line in tagged:
        parsed = _parse_header_line(line)

        if parsed and parsed[0] in split_depths:
            # Hit a split-level header → close current section, open new one
            flush()
            level, text = parsed
            _update_header_stack(header_stack, level, text)
            state["breadcrumb"]    = _make_breadcrumb(header_stack)
            state["parent_header"] = header_stack[-1][1] if header_stack else ""
            state["header_level"]  = level
            state["lines"]         = [line]
            state["pages"]         = [page_no]

        else:
            # Non-split header: update the stack for breadcrumb tracking,
            # but do NOT close the current section.
            if parsed:
                _update_header_stack(header_stack, parsed[0], parsed[1])
            state["lines"].append(line)
            state["pages"].append(page_no)

    flush()  # close the final section
    return sections


# ---------------------------------------------------------------------------
# Step 3 — token bounds (AC-2.3, AC-2.4)
# ---------------------------------------------------------------------------

def _apply_token_bounds(
    sections: list[dict],
    min_tokens: int,
    max_tokens: int,
) -> list[dict]:
    """Merge sections that are too short; split sections that are too long."""
    result: list[dict] = []
    i = 0

    while i < len(sections):
        sec = dict(sections[i])
        tok = _count_tokens(sec["content"])

        # AC-2.3 — merge if below minimum
        if tok < min_tokens and i + 1 < len(sections):
            nxt = sections[i + 1]
            log.debug(
                "Merging short section '%s' (%d tokens) with next section '%s'.",
                sec["breadcrumb"], tok, nxt["breadcrumb"],
            )
            # DECISION REQUIRED: spec says "next sibling" (same header level).
            # Implemented as "next section regardless of level" for v1 simplicity.
            # If same-level-only merging is required, revisit before v1 release.
            sec["content"]    = sec["content"] + "\n\n" + nxt["content"]
            sec["page_range"] = [sec["page_range"][0], nxt["page_range"][1]]
            i += 2
            result.append(sec)
            continue

        # AC-2.3 — split if above maximum
        if tok > max_tokens:
            sub_contents = _split_content(sec["content"], max_tokens)
            for sub in sub_contents:
                child = dict(sec)
                child["content"] = sub
                # Page range is inherited from parent section (best-effort for v1
                # since we lack per-paragraph page tracking).
                result.append(child)
            i += 1
            continue

        result.append(sec)
        i += 1

    return result


def _split_content(content: str, max_tokens: int) -> list[str]:
    """
    Split content at paragraph boundaries (blank lines), respecting $$...$$ blocks.

    AC-2.4: a $$ block is never split across chunks.
    AC-2.3: if a single paragraph exceeds max_tokens, split mid-paragraph and warn.
    """
    # Split into paragraphs at one-or-more blank lines
    paragraphs = re.split(r"\n\n+", content)

    groups: list[str] = []
    current: list[str] = []
    current_tokens = 0
    in_formula = False

    for para in paragraphs:
        # Count standalone $$ markers (AC-2.4 formula tracking)
        stripped = para.strip()
        if stripped == "$$":
            in_formula = not in_formula

        para_tokens = _count_tokens(para)

        if in_formula:
            # Inside a formula block — must not split here
            current.append(para)
            current_tokens += para_tokens
            continue

        if current_tokens + para_tokens > max_tokens and current:
            # Flushing would create an oversized chunk; start a new group
            groups.append("\n\n".join(current))
            current = [para]
            current_tokens = para_tokens
        else:
            current.append(para)
            current_tokens += para_tokens

    if current:
        groups.append("\n\n".join(current))

    # AC-2.3 — handle single oversized paragraphs (no blank-line split available)
    result: list[str] = []
    for g in groups:
        if _count_tokens(g) > max_tokens:
            if "$$" in g:
                # AC-2.4 — formula overflow: accept and warn; never split
                log.warning(
                    "Formula block causes chunk to exceed max_tokens. "
                    "Keeping formula intact as required by AC-2.4."
                )
                result.append(g)
            else:
                # AC-2.3 — single paragraph overflow: split mid-paragraph and warn
                log.warning(
                    "Single paragraph exceeds max_tokens; splitting mid-paragraph."
                )
                words = g.split()
                mid = max_tokens
                result.append(" ".join(words[:mid]))
                result.append(" ".join(words[mid:]))
        else:
            result.append(g)

    return [r for r in result if r.strip()]


# ---------------------------------------------------------------------------
# Step 4 — assign IDs, build Chunk objects (AC-2.5)
# ---------------------------------------------------------------------------

def _build_chunks(sections: list[dict], doc: DocumentContent) -> list[Chunk]:
    chunks: list[Chunk] = []
    for i, sec in enumerate(sections):
        chunks.append(Chunk(
            chunk_id        = f"chunk_{i + 1:03d}",   # AC-2.5 zero-padded
            content         = sec["content"],
            page_range      = sec["page_range"],       # AC-2.6
            breadcrumb      = sec["breadcrumb"],       # AC-2.2
            parent_header   = sec["parent_header"],    # AC-2.2
            source_file     = doc.source_file,
            source_id       = doc.source_id,
            extraction_engine = doc.extraction_engine,
            hitl_status     = "pending",
        ))
    return chunks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_tokens(text: str) -> int:
    """Word-count token approximation.

    # DECISION REQUIRED: spec says "tokens" but no tokenizer is specified.
    # Using len(text.split()) (word count) as a fast, dependency-free proxy.
    # Swap in tiktoken or similar if a proper tokenizer is required.
    """
    return len(text.split())


def _parse_header_line(line: str) -> tuple[int, str] | None:
    """Return (level, text) if `line` is a markdown header, else None."""
    m = _HEADER_RE.match(line.strip())
    if m:
        return len(m.group(1)), m.group(2).strip()
    return None


def _update_header_stack(stack: list[tuple[int, str]], level: int, text: str) -> None:
    """Pop headers at the same depth or deeper, then push the new header."""
    while stack and stack[-1][0] >= level:
        stack.pop()
    stack.append((level, text))


def _make_breadcrumb(stack: list[tuple[int, str]]) -> str:
    """Build a ' > '-delimited breadcrumb from the header stack."""
    return " > ".join(text for _, text in stack)
