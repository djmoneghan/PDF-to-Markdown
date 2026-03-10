# ingestor/extractor.py
# PDF -> DocumentContent extraction (Docling primary, PyMuPDF fallback).


class ExtractionWarning(Exception):
    """Raised when a structural element (table, formula) cannot be parsed."""


def extract(pdf_path, config):
    """Extract a PDF into a DocumentContent object.

    Args:
        pdf_path: pathlib.Path to the source PDF.
        config:   dict loaded by ingestor.load_config().

    Returns:
        DocumentContent

    Raises:
        ValueError: if pdf_path is not a readable PDF.
        ExtractionWarning: if a table or formula block cannot be parsed.
    """
    raise NotImplementedError("Phase 4: extractor not yet implemented.")
