# ingestor/__init__.py
# Shared data contracts for The Kinetic Ingestor.

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Re-export config functions for backward compatibility
from ingestor.config import load_config, save_config


# ---------------------------------------------------------------------------
# Data contracts
# ---------------------------------------------------------------------------

@dataclass
class ImageRef:
    """A detected image — location recorded, content not extracted."""
    page: int
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1)


@dataclass
class TableBlock:
    """A table detected by the extractor, rendered as GFM."""
    gfm: str          # GitHub Flavored Markdown table string
    page: int
    table_index: int  # 0-based index within the page


@dataclass
class FormulaBlock:
    """A LaTeX / mathematical formula block, preserved verbatim."""
    latex: str   # raw content, without the $$ delimiters
    page: int


@dataclass
class DocumentContent:
    """
    Structured representation of an extracted PDF document.
    Produced by ingestor/extractor.py; consumed by ingestor/chunker.py.
    """
    source_file: str                          # original PDF filename (stem + suffix)
    source_id: str                            # UUID-v4, assigned at extraction time
    text_blocks: list[str]                    # ordered list of plain-text paragraphs
    headers: list[tuple[int, str, int]]       # (level, text, page_number)
    tables: list[TableBlock] = field(default_factory=list)
    images: list[ImageRef] = field(default_factory=list)
    formula_blocks: list[FormulaBlock] = field(default_factory=list)
    extraction_engine: str = "docling"        # "docling" | "pymupdf"
    # Full markdown representation assembled by the extractor, page-annotated.
    # Each element is a (page_number, markdown_text) tuple.
    pages: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class Chunk:
    """
    A single semantic chunk ready for metadata enrichment and HITL review.
    Produced by ingestor/chunker.py; flows through the rest of the pipeline.
    """
    chunk_id: str                        # "chunk_001", "chunk_002", ...
    content: str                         # Markdown body (no YAML frontmatter)
    page_range: list[int]                # [start_page, end_page]
    breadcrumb: str                      # "Section 2 > Subsection 2.1"
    parent_header: str                   # immediate parent header text
    source_file: str                     # original PDF filename
    source_id: str                       # UUID-v4 shared across all chunks of a doc
    extraction_engine: str = "docling"   # propagated from DocumentContent
    hitl_status: str = "pending"         # accepted | edited | flagged | pending
    metadata: dict[str, Any] = field(default_factory=dict)
    # Set after metadata generation; mirrors the confidence_score field in metadata.
    confidence_score: float = 0.0
    corrections_ref: str | None = None   # corrections.json record_id, if edited

