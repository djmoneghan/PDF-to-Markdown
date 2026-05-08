# ingestor/extractor.py
# Document -> DocumentContent extraction.
#   PDF:  Docling primary, PyMuPDF fallback.
#   DOCX: Docling only — PyMuPDF cannot parse DOCX, so there is no fallback.

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from ingestor import DocumentContent, FormulaBlock, ImageRef, TableBlock

log = logging.getLogger(__name__)


class ExtractionWarning(Exception):
    """Raised when a structural element (table, formula) cannot be parsed to GFM."""


class ExtractionError(Exception):
    """Raised when both extraction engines (Docling and PyMuPDF) fail,
    or when DOCX extraction is requested but Docling is unavailable."""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

_SUPPORTED_SUFFIXES: set[str] = {".pdf", ".docx"}


def extract(doc_path: Path | str, config: dict[str, Any]) -> DocumentContent:
    """Extract a PDF or DOCX into a DocumentContent object.

    PDF: Docling primary, PyMuPDF fallback (engine choice driven by
    ``config['extraction']['engine']`` and the confidence threshold).
    DOCX: Docling only — PyMuPDF cannot parse DOCX. If Docling is not
    importable, ``ExtractionError`` is raised with a clear message.

    Args:
        doc_path: Path to the source document (.pdf or .docx).
        config:   Dict loaded by ingestor.load_config().

    Returns:
        DocumentContent

    Raises:
        ValueError: if ``doc_path`` is missing, not a file, or not a
            supported document type (AC-1.6).
        ExtractionError: if DOCX is requested but Docling is unavailable,
            or both engines fail on a PDF.

    Notes:
        Tables that Docling cannot convert to GFM (AC-1.2) are soft-skipped:
        a visible "[Extraction note]" marker is inserted into the page
        content for downstream HITL review, a WARNING is logged, and the
        rest of the document continues to extract. Hard-failing on a single
        bad table is too brittle in practice — most real documents have at
        least one quirky table, and aborting throws away all the good
        content. ``ExtractionWarning`` is retained as a public class for
        callers that want to filter logs or handle markers specifically,
        but ``extract()`` no longer raises it.
    """
    doc_path = Path(doc_path)

    # AC-1.6 — validate before any extraction attempt
    if not doc_path.exists():
        raise ValueError(
            f"Document path does not exist: {doc_path.resolve()}"
        )
    if not doc_path.is_file():
        raise ValueError(
            f"Document path is not a file: {doc_path.resolve()}"
        )
    suffix = doc_path.suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        raise ValueError(
            f"Unsupported document type {suffix!r}: {doc_path.resolve()}. "
            f"Supported: {sorted(_SUPPORTED_SUFFIXES)}"
        )

    # DOCX has no PyMuPDF fallback — Docling is required, regardless of the
    # configured engine. Fail fast with a clear message if Docling is missing.
    if suffix == ".docx":
        try:
            from docling.document_converter import DocumentConverter  # noqa: F401
        except ImportError as exc:
            raise ExtractionError(
                f"DOCX extraction requires Docling, which is unavailable: {exc}. "
                "Install Docling (pip install docling) or supply the document as PDF."
            ) from exc
        doc, _confidence = _extract_docling(doc_path, config)
        return doc

    # PDF path — existing engine selection logic.
    engine = config["extraction"]["engine"]

    if engine == "pymupdf":
        try:
            return _extract_pymupdf(doc_path, config)
        except Exception as exc:
            raise ExtractionError(f"PyMuPDF engine failed: {exc}") from exc

    # AC-1.1 — try Docling with confidence check; fall back on import, runtime error,
    # or confidence below threshold. ExtractionWarning must propagate (CLAUDE.md rule 1).
    docling_reason: str | None = None
    try:
        doc, confidence = _extract_docling(doc_path, config)
        threshold = config["extraction"]["confidence_threshold"]
        if confidence >= threshold:
            return doc
        docling_reason = f"confidence {confidence:.2f} below threshold {threshold}"
        log.warning(
            "Docling confidence %.2f below threshold %.2f. Falling back to PyMuPDF.",
            confidence,
            threshold,
        )
    except ExtractionWarning:
        raise
    except ImportError:
        docling_reason = "Docling not installed"
        log.warning(
            "Docling is not installed. Falling back to PyMuPDF. "
            "Structural features (tables, formulas) will not be available."
        )
    except Exception as exc:
        docling_reason = f"{type(exc).__name__}: {exc}"
        log.warning(
            "Docling raised a runtime error (%s: %s). Falling back to PyMuPDF.",
            type(exc).__name__,
            exc,
        )

    try:
        return _extract_pymupdf(doc_path, config)
    except Exception as pymupdf_exc:
        raise ExtractionError(
            f"Both extraction engines failed. Docling: {docling_reason}. "
            f"PyMuPDF: {pymupdf_exc}"
        ) from pymupdf_exc


# ---------------------------------------------------------------------------
# Docling extraction path
# ---------------------------------------------------------------------------

def _extract_docling(doc_path: Path, config: dict[str, Any]) -> tuple[DocumentContent, float]:
    from docling.document_converter import DocumentConverter  # AC-1.1 guard

    # Resolve DocItemLabel — import path varies across Docling versions
    try:
        from docling_core.types.doc import DocItemLabel
    except ImportError:
        from docling.datamodel.document import DocItemLabel  # type: ignore[no-redef]

    converter = DocumentConverter()
    result = converter.convert(str(doc_path))
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
            except Exception as exc:
                # Soft-skip: insert a visible HITL marker and continue. The
                # raw `item.text` is intentionally NOT emitted as a fallback —
                # silently leaking unstructured table cells into prose would
                # be worse than a missing-table marker.
                marker = (
                    f"\n> **[Extraction note]** Docling could not produce GFM for the "
                    f"table at page {page_no}, index {idx} "
                    f"({type(exc).__name__}: {exc}). Manual review required.\n"
                )
                log.warning(
                    "Table at page %d, index %d skipped (Docling export failed): %s",
                    page_no, idx, exc,
                )
                pages_content[page_no].append(marker)
                continue
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

    total_pages = len(pages)
    confidence = (
        sum(1 for _, text in pages if text.strip()) / total_pages
        if total_pages else 0.0
    )

    return DocumentContent(
        source_file=doc_path.name,
        source_id=source_id,
        text_blocks=text_blocks,
        headers=headers,
        tables=tables,
        images=images,
        formula_blocks=formula_blocks,
        extraction_engine="docling",
        pages=pages,
    ), confidence


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
