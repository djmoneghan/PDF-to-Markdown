# tests/test_extractor.py
# Tests for ingestor/extractor.py
# AC references map to REQUIREMENTS.md Feature 1.

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ingestor.extractor import ExtractionWarning, extract


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_pdf():
    """Create an empty temp file with .pdf suffix; return its Path."""
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    return Path(path)


def _docling_config():
    return {"extraction": {"engine": "docling"}}


def _pymupdf_config():
    return {"extraction": {"engine": "pymupdf"}}


def _make_docling_sys_mocks():
    """Return (sys_modules_dict, dc_mod, label_mock) for Docling patching."""
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
    return sys_mocks, dc_mod, label


def _wire_converter(dc_mod, items):
    """Wire DocumentConverter mock to yield *items* from doc.iterate_items()."""
    mock_doc = MagicMock()
    mock_doc.iterate_items.return_value = [(item, 0) for item in items]
    mock_result = MagicMock()
    mock_result.document = mock_doc
    dc_mod.DocumentConverter.return_value.convert.return_value = mock_result


def _make_item(text="", page_no=1, label_val=None):
    """Build a minimal Docling document item mock."""
    item = MagicMock()
    item.label = label_val
    item.text = text
    prov = MagicMock()
    prov.page_no = page_no
    item.prov = [prov]
    return item


class _FakePDF:
    """Simple iterable replacement for a fitz PDF object."""
    def __init__(self, pages):
        self._pages = pages

    def __iter__(self):
        return iter(self._pages)

    def close(self):
        pass


def _make_fitz_sys_mock(pages_text=None):
    """Return (sys_modules_dict, fitz_mock) with a mocked fitz.open()."""
    if pages_text is None:
        pages_text = ["Sample page text."]

    fitz_mock = MagicMock(name="fitz")
    page_mocks = []
    for text in pages_text:
        p = MagicMock()
        p.get_text.return_value = text
        page_mocks.append(p)

    fitz_mock.open.return_value = _FakePDF(page_mocks)
    return {"fitz": fitz_mock}, fitz_mock


# ---------------------------------------------------------------------------
# Import tests
# ---------------------------------------------------------------------------

class TestExtractorImports(unittest.TestCase):
    """Verify the module and its public surface are importable."""

    def test_module_importable(self):
        import ingestor.extractor  # noqa: F401

    def test_extract_function_exists(self):
        self.assertTrue(callable(extract))

    def test_extraction_warning_exists(self):
        self.assertTrue(issubclass(ExtractionWarning, Exception))


# ---------------------------------------------------------------------------
# AC-1.1 — Docling primary path
# ---------------------------------------------------------------------------

class TestAC11_DoclingPrimaryPath(unittest.TestCase):
    """AC-1.1 — extract() uses Docling when engine=docling and Docling is available."""

    def setUp(self):
        self.pdf = _tmp_pdf()

    def tearDown(self):
        self.pdf.unlink(missing_ok=True)

    def test_docling_called_when_engine_is_docling(self):
        sys_mocks, dc_mod, label = _make_docling_sys_mocks()
        _wire_converter(dc_mod, [_make_item("Hello")])

        with patch.dict(sys.modules, sys_mocks):
            doc = extract(self.pdf, _docling_config())

        dc_mod.DocumentConverter.return_value.convert.assert_called_once()
        self.assertEqual(doc.extraction_engine, "docling")

    def test_pymupdf_not_called_when_docling_succeeds(self):
        sys_mocks, dc_mod, label = _make_docling_sys_mocks()
        _wire_converter(dc_mod, [_make_item("Text")])
        fitz_mocks, fitz_mock = _make_fitz_sys_mock()
        sys_mocks.update(fitz_mocks)

        with patch.dict(sys.modules, sys_mocks):
            extract(self.pdf, _docling_config())

        fitz_mock.open.assert_not_called()

    def test_falls_back_to_pymupdf_on_docling_import_error(self):
        # Docling not installed → import raises ModuleNotFoundError → fitz used.
        fitz_mocks, fitz_mock = _make_fitz_sys_mock()

        with patch.dict(sys.modules, fitz_mocks):
            # docling is not in sys.modules (not installed) → ImportError path
            doc = extract(self.pdf, _docling_config())

        self.assertEqual(doc.extraction_engine, "pymupdf")
        fitz_mock.open.assert_called_once()

    def test_uses_pymupdf_when_config_engine_is_pymupdf(self):
        fitz_mocks, fitz_mock = _make_fitz_sys_mock()

        with patch.dict(sys.modules, fitz_mocks):
            doc = extract(self.pdf, _pymupdf_config())

        fitz_mock.open.assert_called_once()
        self.assertEqual(doc.extraction_engine, "pymupdf")


# ---------------------------------------------------------------------------
# AC-1.2 — Table → GFM
# ---------------------------------------------------------------------------

class TestAC12_TableToGFM(unittest.TestCase):
    """AC-1.2 — Detected tables are converted to GFM; failed tables raise ExtractionWarning."""

    def setUp(self):
        self.pdf = _tmp_pdf()

    def tearDown(self):
        self.pdf.unlink(missing_ok=True)

    def test_table_rendered_as_gfm(self):
        sys_mocks, dc_mod, label = _make_docling_sys_mocks()
        tbl = _make_item("", page_no=2, label_val=label.TABLE)
        tbl.export_to_markdown.return_value = "| A | B |\n|---|---|\n| 1 | 2 |"
        _wire_converter(dc_mod, [tbl])

        with patch.dict(sys.modules, sys_mocks):
            doc = extract(self.pdf, _docling_config())

        self.assertEqual(len(doc.tables), 1)
        self.assertIn("|", doc.tables[0].gfm)

    def test_failed_table_raises_extraction_warning(self):
        sys_mocks, dc_mod, label = _make_docling_sys_mocks()
        tbl = _make_item("", page_no=1, label_val=label.TABLE)
        tbl.export_to_markdown.side_effect = RuntimeError("parse failure")
        _wire_converter(dc_mod, [tbl])

        with patch.dict(sys.modules, sys_mocks):
            with self.assertRaises(ExtractionWarning):
                extract(self.pdf, _docling_config())

    def test_extraction_warning_contains_page_and_index(self):
        sys_mocks, dc_mod, label = _make_docling_sys_mocks()
        tbl = _make_item("", page_no=3, label_val=label.TABLE)
        tbl.export_to_markdown.side_effect = ValueError("bad table")
        _wire_converter(dc_mod, [tbl])

        with patch.dict(sys.modules, sys_mocks):
            try:
                extract(self.pdf, _docling_config())
                self.fail("Expected ExtractionWarning")
            except ExtractionWarning as exc:
                msg = str(exc)
                self.assertIn("page 3", msg)
                self.assertIn("index 0", msg)

    def test_no_raw_text_emitted_for_failed_table(self):
        """ExtractionWarning is raised rather than silently emitting plain text."""
        sys_mocks, dc_mod, label = _make_docling_sys_mocks()
        tbl = _make_item("fallback plain text", page_no=1, label_val=label.TABLE)
        tbl.export_to_markdown.side_effect = Exception("fail")
        _wire_converter(dc_mod, [tbl])

        with patch.dict(sys.modules, sys_mocks):
            # Must raise — no fallback plain-text emission
            with self.assertRaises(ExtractionWarning):
                extract(self.pdf, _docling_config())


# ---------------------------------------------------------------------------
# AC-1.3 — Formula preservation
# ---------------------------------------------------------------------------

class TestAC13_FormulaPreservation(unittest.TestCase):
    """AC-1.3 — LaTeX / math content is wrapped verbatim in $$ blocks."""

    def setUp(self):
        self.pdf = _tmp_pdf()

    def tearDown(self):
        self.pdf.unlink(missing_ok=True)

    def test_formula_wrapped_in_double_dollar(self):
        sys_mocks, dc_mod, label = _make_docling_sys_mocks()
        formula = _make_item(r"\frac{E}{mc^2}", page_no=1, label_val=label.FORMULA)
        _wire_converter(dc_mod, [formula])

        with patch.dict(sys.modules, sys_mocks):
            doc = extract(self.pdf, _docling_config())

        page_text = doc.pages[0][1]
        self.assertIn("$$", page_text)

    def test_formula_content_not_modified(self):
        raw_latex = r"\sum_{i=0}^{n} x_i"
        sys_mocks, dc_mod, label = _make_docling_sys_mocks()
        formula = _make_item(raw_latex, page_no=1, label_val=label.FORMULA)
        _wire_converter(dc_mod, [formula])

        with patch.dict(sys.modules, sys_mocks):
            doc = extract(self.pdf, _docling_config())

        # Latex preserved byte-for-byte
        self.assertEqual(doc.formula_blocks[0].latex, raw_latex)

    def test_formula_not_summarized_or_cleaned(self):
        raw_latex = r"\alpha + \beta \neq \gamma"
        sys_mocks, dc_mod, label = _make_docling_sys_mocks()
        formula = _make_item(raw_latex, page_no=1, label_val=label.FORMULA)
        _wire_converter(dc_mod, [formula])

        with patch.dict(sys.modules, sys_mocks):
            doc = extract(self.pdf, _docling_config())

        # All special LaTeX tokens must survive verbatim
        stored = doc.formula_blocks[0].latex
        self.assertIn(r"\alpha", stored)
        self.assertIn(r"\neq", stored)
        self.assertIn(r"\gamma", stored)


# ---------------------------------------------------------------------------
# AC-1.4 — Image detection
# ---------------------------------------------------------------------------

class TestAC14_ImageDetection(unittest.TestCase):
    """AC-1.4 — Images are detected and recorded; placeholder injected into content."""

    def setUp(self):
        self.pdf = _tmp_pdf()

    def tearDown(self):
        self.pdf.unlink(missing_ok=True)

    def test_image_page_and_bbox_recorded(self):
        sys_mocks, dc_mod, label = _make_docling_sys_mocks()
        img = _make_item("", page_no=2, label_val=label.PICTURE)
        bbox_mock = MagicMock()
        bbox_mock.l, bbox_mock.t, bbox_mock.r, bbox_mock.b = 10.0, 20.0, 110.0, 120.0
        img.prov[0].bbox = bbox_mock
        _wire_converter(dc_mod, [img])

        with patch.dict(sys.modules, sys_mocks):
            doc = extract(self.pdf, _docling_config())

        self.assertEqual(len(doc.images), 1)
        self.assertEqual(doc.images[0].page, 2)
        self.assertEqual(doc.images[0].bbox, (10.0, 20.0, 110.0, 120.0))

    def test_image_placeholder_in_markdown(self):
        sys_mocks, dc_mod, label = _make_docling_sys_mocks()
        img = _make_item("", page_no=1, label_val=label.PICTURE)
        bbox_mock = MagicMock()
        bbox_mock.l = bbox_mock.t = bbox_mock.r = bbox_mock.b = 0.0
        img.prov[0].bbox = bbox_mock
        _wire_converter(dc_mod, [img])

        with patch.dict(sys.modules, sys_mocks):
            doc = extract(self.pdf, _docling_config())

        page_text = doc.pages[0][1]
        self.assertIn("<!-- IMAGE:", page_text)
        self.assertIn("page 1", page_text)

    def test_image_not_embedded_in_output(self):
        sys_mocks, dc_mod, label = _make_docling_sys_mocks()
        img = _make_item("", page_no=1, label_val=label.PICTURE)
        bbox_mock = MagicMock()
        bbox_mock.l = bbox_mock.t = bbox_mock.r = bbox_mock.b = 0.0
        img.prov[0].bbox = bbox_mock
        _wire_converter(dc_mod, [img])

        with patch.dict(sys.modules, sys_mocks):
            doc = extract(self.pdf, _docling_config())

        for _, page_text in doc.pages:
            self.assertNotIn("data:image", page_text)
            self.assertNotIn("base64", page_text)


# ---------------------------------------------------------------------------
# AC-1.5 — PyMuPDF fallback
# ---------------------------------------------------------------------------

class TestAC15_PyMuPDFFallback(unittest.TestCase):
    """AC-1.5 — PyMuPDF path extracts text-only and sets correct engine field."""

    def setUp(self):
        self.pdf = _tmp_pdf()

    def tearDown(self):
        self.pdf.unlink(missing_ok=True)

    def test_pymupdf_sets_extraction_engine_field(self):
        fitz_mocks, _ = _make_fitz_sys_mock(["Text content."])

        with patch.dict(sys.modules, fitz_mocks):
            doc = extract(self.pdf, _pymupdf_config())

        self.assertEqual(doc.extraction_engine, "pymupdf")

    def test_pymupdf_logs_structural_features_warning(self):
        fitz_mocks, _ = _make_fitz_sys_mock()

        with patch.dict(sys.modules, fitz_mocks):
            with self.assertLogs("ingestor.extractor", level="WARNING") as cm:
                extract(self.pdf, _pymupdf_config())

        self.assertTrue(
            any("structural" in msg.lower() or "pymupdf" in msg.lower()
                for msg in cm.output)
        )

    def test_pymupdf_does_not_detect_tables(self):
        fitz_mocks, _ = _make_fitz_sys_mock()

        with patch.dict(sys.modules, fitz_mocks):
            doc = extract(self.pdf, _pymupdf_config())

        self.assertEqual(doc.tables, [])

    def test_pymupdf_does_not_detect_formulas(self):
        fitz_mocks, _ = _make_fitz_sys_mock()

        with patch.dict(sys.modules, fitz_mocks):
            doc = extract(self.pdf, _pymupdf_config())

        self.assertEqual(doc.formula_blocks, [])


# ---------------------------------------------------------------------------
# AC-1.6 — Unsupported file types
# ---------------------------------------------------------------------------

class TestAC16_UnsupportedFileTypes(unittest.TestCase):
    """AC-1.6 — Non-PDF or unreadable paths raise ValueError immediately."""

    def test_nonexistent_path_raises_value_error(self):
        with self.assertRaises(ValueError):
            extract(Path("/no/such/file.pdf"), _docling_config())

    def test_non_pdf_extension_raises_value_error(self):
        fd, path = tempfile.mkstemp(suffix=".txt")
        os.close(fd)
        try:
            with self.assertRaises(ValueError):
                extract(Path(path), _docling_config())
        finally:
            Path(path).unlink(missing_ok=True)

    def test_value_error_message_contains_path(self):
        bad_path = Path("/tmp/definitely_not_here_xyz.pdf")
        try:
            extract(bad_path, _docling_config())
            self.fail("Expected ValueError")
        except ValueError as exc:
            self.assertIn("definitely_not_here_xyz.pdf", str(exc))

    def test_no_extraction_attempted_before_validation(self):
        sys_mocks, dc_mod, _ = _make_docling_sys_mocks()

        with patch.dict(sys.modules, sys_mocks):
            try:
                extract(Path("/no/such/file.pdf"), _docling_config())
            except ValueError:
                pass

        dc_mod.DocumentConverter.assert_not_called()


if __name__ == "__main__":
    unittest.main()
