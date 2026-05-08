# tests/test_extractor_docx.py
# Group A of Project Librarian Prompt 03 — DOCX support in ingestor/extractor.py.

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ingestor.extractor import ExtractionError, extract


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tmp_with_suffix(suffix: str) -> Path:
    """Create an empty temp file with *suffix*; return its Path."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return Path(path)


def _docling_config(confidence_threshold: float = 0.0):
    return {"extraction": {"engine": "docling", "confidence_threshold": confidence_threshold}}


def _pymupdf_config():
    return {"extraction": {"engine": "pymupdf"}}


def _make_docling_sys_mocks():
    """Return (sys_modules_dict, dc_mod) for Docling patching."""
    label = MagicMock(name="DocItemLabel")
    label.TABLE = "TABLE"
    label.PICTURE = "PICTURE"
    label.FORMULA = "FORMULA"
    label.TITLE = "TITLE"
    label.SECTION_HEADER = "SECTION_HEADER"

    dc_mod = MagicMock(name="docling.document_converter")
    dcore_doc_mod = MagicMock(name="docling_core.types.doc")
    dcore_doc_mod.DocItemLabel = label

    sys_mocks = {
        "docling": MagicMock(),
        "docling.document_converter": dc_mod,
        "docling_core": MagicMock(),
        "docling_core.types": MagicMock(),
        "docling_core.types.doc": dcore_doc_mod,
    }
    return sys_mocks, dc_mod


def _wire_converter_with_text(dc_mod, text: str = "Hello world"):
    """Wire DocumentConverter mock to yield a single text item."""
    item = MagicMock()
    item.label = None
    item.text = text
    prov = MagicMock()
    prov.page_no = 1
    item.prov = [prov]

    mock_doc = MagicMock()
    mock_doc.iterate_items.return_value = [(item, 0)]
    mock_result = MagicMock()
    mock_result.document = mock_doc
    dc_mod.DocumentConverter.return_value.convert.return_value = mock_result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExtractorDocxSupport(unittest.TestCase):
    """Group A — DOCX is accepted, routes to Docling unconditionally,
    and fails fast with ExtractionError if Docling is unavailable."""

    def test_extract_rejects_unsupported_suffix(self):
        """A .txt file is neither PDF nor DOCX → ValueError naming the supported set."""
        path = _tmp_with_suffix(".txt")
        try:
            with self.assertRaises(ValueError) as cm:
                extract(path, _docling_config())
            msg = str(cm.exception)
            self.assertIn("'.txt'", msg)
            self.assertIn(".pdf", msg)
            self.assertIn(".docx", msg)
        finally:
            path.unlink(missing_ok=True)

    def test_extract_docx_requires_docling(self):
        """When Docling is unimportable, .docx extraction raises ExtractionError
        (NOT a silent fallback to PyMuPDF — DOCX has no PyMuPDF path)."""
        docx = _tmp_with_suffix(".docx")

        # Force Docling import to fail by removing any cached docling modules
        # AND by inserting a finder that raises ImportError for docling.*.
        class _BlockDocling:
            @staticmethod
            def find_spec(name, path=None, target=None):
                if name == "docling" or name.startswith("docling."):
                    raise ImportError(f"Docling forcibly disabled for test: {name}")
                return None

        cleared = {k: sys.modules[k] for k in list(sys.modules)
                   if k == "docling" or k.startswith("docling")}
        for k in cleared:
            del sys.modules[k]
        sys.meta_path.insert(0, _BlockDocling)
        try:
            with self.assertRaises(ExtractionError) as cm:
                extract(docx, _docling_config())
            msg = str(cm.exception)
            self.assertIn("DOCX", msg)
            self.assertIn("Docling", msg)
        finally:
            sys.meta_path.remove(_BlockDocling)
            sys.modules.update(cleared)
            docx.unlink(missing_ok=True)

    def test_extract_docx_routes_to_docling_only(self):
        """Even when config.extraction.engine='pymupdf', .docx still goes
        through Docling and never touches PyMuPDF (which can't read DOCX)."""
        docx = _tmp_with_suffix(".docx")
        sys_mocks, dc_mod = _make_docling_sys_mocks()
        _wire_converter_with_text(dc_mod, "DOCX body")

        with patch.dict(sys.modules, sys_mocks), \
             patch("ingestor.extractor._extract_pymupdf") as mock_pymupdf:
            try:
                doc = extract(docx, _pymupdf_config())
            finally:
                docx.unlink(missing_ok=True)

        self.assertEqual(doc.extraction_engine, "docling")
        self.assertEqual(doc.source_file, docx.name)
        mock_pymupdf.assert_not_called()
        dc_mod.DocumentConverter.assert_called_once()

    def test_extract_pdf_unchanged(self):
        """Regression guard — PDF still routes per config.extraction.engine."""
        pdf = _tmp_with_suffix(".pdf")
        sys_mocks, dc_mod = _make_docling_sys_mocks()
        _wire_converter_with_text(dc_mod, "PDF body")

        # Engine=docling → Docling called.
        with patch.dict(sys.modules, sys_mocks), \
             patch("ingestor.extractor._extract_pymupdf") as mock_pymupdf:
            doc = extract(pdf, _docling_config())
        self.assertEqual(doc.extraction_engine, "docling")
        mock_pymupdf.assert_not_called()

        # Engine=pymupdf → PyMuPDF called, Docling NOT called.
        with patch("ingestor.extractor._extract_pymupdf") as mock_pymupdf, \
             patch("ingestor.extractor._extract_docling") as mock_docling:
            mock_pymupdf.return_value = MagicMock(extraction_engine="pymupdf")
            doc2 = extract(pdf, _pymupdf_config())

        self.assertEqual(doc2.extraction_engine, "pymupdf")
        mock_docling.assert_not_called()
        mock_pymupdf.assert_called_once()

        pdf.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
