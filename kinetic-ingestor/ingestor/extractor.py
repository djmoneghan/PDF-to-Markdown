# ingestor/extractor.py
# PDF -> DocumentContent extraction (Docling primary, PyMuPDF fallback).

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from ingestor import DocumentContent, FormulaBlock, ImageRef, TableBlock

log = logging.getLogger(__name__)


class ExtractionWarning(Exception):
    """Raised when a structural element (table, formula) cannot be parsed to GFM."""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract(pdf_path: Path | str, config: dict[str, Any]) -> DocumentContent:
    """Extract a PDF into a DocumentContent object.

    Uses Docling by default. Falls back to PyMuPDF when:
      - config extraction.engine is 'pymupdf', OR
      - Docling is not importable, OR
      - Docling raises a runtime error during conversion.

    Args:
        pdf_path: Path to the source PDF.
        config:   Dict loaded by ingestor.load_config().

    Returns:
        DocumentContent

    Raises:
        ValueError: if pdf_path is not a readable .pdf file (AC-1.6).
        ExtractionWarning: if a table cannot be converted to GFM (AC-1.2).
    """
    pdf_path = Path(pdf_path)

    # AC-1.6 — validate before any extraction attempt
    if not pdf_path.exists():
        raise ValueError(
            f"PDF path does not exist: {pdf_path.resolve()}"
        )
    if not pdf_path.is_file():
        raise ValueError(
            f"PDF path is not a file: {pdf_path.resolve()}"
        )
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(
            f"Expected a .pdf file, got '{pdf_path.suffix}': {pdf_path.resolve()}"
        )

    engine = config["extraction"]["engine"]

    if engine == "pymupdf":
        return _extract_pymupdf(pdf_path, config)

    # AC-1.1 — try Docling; fall back on import or runtime error.
    # ExtractionWarning must propagate — never swallow it silently (CLAUDE.md rule 1).
    try:
        return _extract_docling(pdf_path, config)
    except ExtractionWarning:
        raise
    except ImportError:
        log.warning(
            "Docling is not installed. Falling back to PyMuPDF. "
            "Structural features (tables, formulas) will not be available."
        )
        return _extract_pymupdf(pdf_path, config)
    except Exception as exc:
        log.warning(
            "Docling raised a runtime error (%s: %s). Falling back to PyMuPDF.",
            type(exc).__name__,
            exc,
        )
        return _extract_pymupdf(pdf_path, config)


# ---------------------------------------------------------------------------
# Docling extraction path
# ---------------------------------------------------------------------------

def _extract_docling(pdf_path: Path, config: dict[str, Any]) -> DocumentContent:
    from docling.document_converter import DocumentConverter  # AC-1.1 guard

    # Resolve DocItemLabel — import path varies across Docling versions
    try:
        from docling_core.types.doc import DocItemLabel
    except ImportError:
        from docling.datamodel.document import DocItemLabel  # type: ignore[no-redef]

    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    doc = result.document

    source_id = str(uuid.uuid4())
    text_blocks: list[str] = []
    headers: list[tuple[int, str, int]] = []
    tables: list[TableBlock] = []
    images: list[ImageRef] = []
    formula_blocks: list[FormulaBlock] = []
    pages_content: dict[int, list[str]] = {}
    table_count_by_page: dict[int, int] = {}

    for item, _level in doc.iterate_items():
        page_no: int = item.prov[0].page_no if item.prov else 1
        pages_content.setdefault(page_no, [])

        label = getattr(item, "label", None)

        # ------------------------------------------------------------------ #
        # Tables — AC-1.2                                                      #
        # ------------------------------------------------------------------ #
        if label == DocItemLabel.TABLE:
            idx = table_count_by_page.get(page_no, 0)
            table_count_by_page[page_no] = idx + 1
            try:
                gfm = item.export_to_markdown()
                if not gfm or not gfm.strip():
                    raise ValueError("Docling returned empty GFM for this table.")
            except ExtractionWarning:
                raise
            except Exception as exc:
                raise ExtractionWarning(
                    f"Table at page {page_no}, index {idx} could not be parsed to GFM: "
                    f"{exc}. Manual review required."
                ) from exc
            tables.append(TableBlock(gfm=gfm, page=page_no, table_index=idx))
            pages_content[page_no].append(gfm)

        # ------------------------------------------------------------------ #
        # Images — AC-1.4                                                      #
        # ------------------------------------------------------------------ #
        elif label == DocItemLabel.PICTURE:
            raw_bbox = item.prov[0].bbox if item.prov else None
            if raw_bbox is not None:
                bbox: tuple[float, float, float, float] = (
                    float(getattr(raw_bbox, "l", 0)),
                    float(getattr(raw_bbox, "t", 0)),
                    float(getattr(raw_bbox, "r", 0)),
                    float(getattr(raw_bbox, "b", 0)),
                )
            else:
                bbox = (0.0, 0.0, 0.0, 0.0)
            images.append(ImageRef(page=page_no, bbox=bbox))
            pages_content[page_no].append(
                f"<!-- IMAGE: page {page_no}, position {bbox} -->"
            )

        # ------------------------------------------------------------------ #
        # Formulas — AC-1.3                                                    #
        # ------------------------------------------------------------------ #
        elif label == DocItemLabel.FORMULA:
            latex = (getattr(item, "text", "") or "").strip()
            formula_blocks.append(FormulaBlock(latex=latex, page=page_no))
            # Wrap verbatim — never reformat
            pages_content[page_no].append(f"$$\n{latex}\n$$")

        # ------------------------------------------------------------------ #
        # Headers                                                              #
        # ------------------------------------------------------------------ #
        elif label == DocItemLabel.TITLE:
            text = (getattr(item, "text", "") or "").strip()
            headers.append((1, text, page_no))
            pages_content[page_no].append(f"# {text}")

        elif label == DocItemLabel.SECTION_HEADER:
            text = (getattr(item, "text", "") or "").strip()
            # item.level is 1-based depth under the document title → add 1
            raw_level = int(getattr(item, "level", 1))
            md_level = min(raw_level + 1, 6)
            headers.append((md_level, text, page_no))
            pages_content[page_no].append(f"{'#' * md_level} {text}")

        # ------------------------------------------------------------------ #
        # Plain text                                                           #
        # ------------------------------------------------------------------ #
        elif hasattr(item, "text") and item.text:
            text_blocks.append(item.text)
            pages_content[page_no].append(item.text)

    pages = [
        (page_no, "\n\n".join(lines))
        for page_no, lines in sorted(pages_content.items())
    ]

    return DocumentContent(
        source_file=pdf_path.name,
        source_id=source_id,
        text_blocks=text_blocks,
        headers=headers,
        tables=tables,
        images=images,
        formula_blocks=formula_blocks,
        extraction_engine="docling",
        pages=pages,
    )


# ---------------------------------------------------------------------------
# PyMuPDF fallback path
# ---------------------------------------------------------------------------

def _extract_pymupdf(pdf_path: Path, config: dict[str, Any]) -> DocumentContent:
    """AC-1.5 — text-only extraction via PyMuPDF."""
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise RuntimeError(
            "PyMuPDF (fitz) is not installed. Install it with: pip install pymupdf"
        ) from exc

    # AC-1.5 — log visible warning about missing structural features
    log.warning(
        "PyMuPDF engine active. Structural features (tables, formulas, images) "
        "are NOT available for this run. All chunk metadata will carry "
        "extraction_engine='pymupdf'."
    )

    source_id = str(uuid.uuid4())
    text_blocks: list[str] = []
    pages: list[tuple[int, str]] = []

    try:
        pdf = fitz.open(str(pdf_path))
    except Exception as exc:
        raise ValueError(
            f"PyMuPDF could not open '{pdf_path}': {exc}"
        ) from exc

    try:
        for page_no, page in enumerate(pdf, start=1):
            text: str = page.get_text("text")  # type: ignore[attr-defined]
            if text.strip():
                text_blocks.append(text)
            pages.append((page_no, text))
    finally:
        pdf.close()

    return DocumentContent(
        source_file=pdf_path.name,
        source_id=source_id,
        text_blocks=text_blocks,
        headers=[],
        tables=[],
        images=[],
        formula_blocks=[],
        extraction_engine="pymupdf",
        pages=pages,
    )
