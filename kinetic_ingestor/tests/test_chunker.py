# tests/test_chunker.py
# Tests for ingestor/chunker.py
# AC references map to REQUIREMENTS.md Feature 2.

import unittest

from ingestor import Chunk, DocumentContent
from ingestor.chunker import _count_tokens, chunk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(split_levels=None, min_tokens=2, max_tokens=500):
    return {
        "chunking": {
            "split_levels": split_levels if split_levels is not None else ["#", "##"],
            "min_chunk_tokens": min_tokens,
            "max_chunk_tokens": max_tokens,
        }
    }


def _make_doc(pages, source_file="test.pdf", source_id="test-uuid"):
    """Build a minimal DocumentContent from a list of (page_no, markdown_text) tuples."""
    return DocumentContent(
        source_file=source_file,
        source_id=source_id,
        text_blocks=[],
        headers=[],
        pages=pages,
    )


# ---------------------------------------------------------------------------
# Import tests
# ---------------------------------------------------------------------------

class TestChunkerImports(unittest.TestCase):
    """Verify the module and its public surface are importable."""

    def test_module_importable(self):
        import ingestor.chunker  # noqa: F401

    def test_chunk_function_exists(self):
        from ingestor.chunker import chunk
        self.assertTrue(callable(chunk))


# ---------------------------------------------------------------------------
# AC-2.1 — Header-based splitting
# ---------------------------------------------------------------------------

class TestAC21_HeaderBasedSplitting(unittest.TestCase):
    """AC-2.1 — Chunks are created at configured header boundaries."""

    def test_splits_on_h1_boundary(self):
        doc = _make_doc([(1, "# Alpha\n\nFirst section content words.\n\n# Beta\n\nSecond section content words.")])
        chunks = chunk(doc, _make_config())
        self.assertEqual(len(chunks), 2)

    def test_splits_on_h2_boundary(self):
        doc = _make_doc([(1, "# Intro\n\nPreamble content text.\n\n## Sub A\n\nSubsection content here now.")])
        chunks = chunk(doc, _make_config())
        self.assertEqual(len(chunks), 2)

    def test_does_not_split_on_h3_by_default(self):
        doc = _make_doc([(1, "# Section\n\nContent here.\n\n### Deep\n\nDeep content words.")])
        chunks = chunk(doc, _make_config(split_levels=["#", "##"]))
        # H3 does not trigger a split; all content stays in one chunk
        self.assertEqual(len(chunks), 1)

    def test_split_levels_read_from_config(self):
        doc = _make_doc([(1, "# Top\n\nTop content here.\n\n### Deep\n\nDeep content here now.")])
        chunks = chunk(doc, _make_config(split_levels=["#", "##", "###"]))
        self.assertEqual(len(chunks), 2)


# ---------------------------------------------------------------------------
# AC-2.2 — Breadcrumb injection
# ---------------------------------------------------------------------------

class TestAC22_BreadcrumbInjection(unittest.TestCase):
    """AC-2.2 — Breadcrumb and parent_header are correctly constructed per chunk."""

    def test_breadcrumb_reflects_header_hierarchy(self):
        doc = _make_doc([(1, "# Chapter\n\nChapter intro text.\n\n## Section\n\nSection body text.")])
        chunks = chunk(doc, _make_config())
        # Second chunk lives under ## Section; breadcrumb must include both levels
        self.assertIn("Chapter", chunks[1].breadcrumb)
        self.assertIn("Section", chunks[1].breadcrumb)

    def test_parent_header_is_immediate_parent(self):
        doc = _make_doc([(1, "# Chapter\n\nIntro text.\n\n## Section\n\nSection body.")])
        chunks = chunk(doc, _make_config())
        self.assertEqual(chunks[1].parent_header, "Section")

    def test_breadcrumb_single_level(self):
        doc = _make_doc([(1, "# Only Header\n\nSome content for this single section.")])
        chunks = chunk(doc, _make_config())
        self.assertEqual(chunks[0].breadcrumb, "Only Header")

    def test_breadcrumb_three_levels(self):
        doc = _make_doc([(1,
            "# H1\n\nH1 content here.\n\n"
            "## H2\n\nH2 content here.\n\n"
            "### H3\n\nH3 content here."
        )])
        chunks = chunk(doc, _make_config(split_levels=["#", "##", "###"]))
        # Third chunk (under H3) breadcrumb must have three parts
        third = chunks[2]
        self.assertIn("H1", third.breadcrumb)
        self.assertIn("H2", third.breadcrumb)
        self.assertIn("H3", third.breadcrumb)


# ---------------------------------------------------------------------------
# AC-2.3 — Token bounds
# ---------------------------------------------------------------------------

class TestAC23_TokenBounds(unittest.TestCase):
    """AC-2.3 — Chunks respect min/max token bounds from config."""

    def test_short_section_merged_with_next_sibling(self):
        # "# A\n\nhi" → ~3 tokens < min=10 → merges with section B
        doc = _make_doc([(1,
            "# A\n\nhi\n\n"
            "# B\n\nThis section has plenty of words in it to exceed minimum."
        )])
        with self.assertLogs("ingestor.chunker", level="DEBUG") as cm:
            chunks = chunk(doc, _make_config(min_tokens=10, max_tokens=500))

        self.assertEqual(len(chunks), 1)
        self.assertTrue(any("erging" in msg for msg in cm.output))

    def test_long_section_split_at_paragraph_boundary(self):
        # Two paragraphs each > max_tokens=5 → split into multiple chunks
        doc = _make_doc([(1,
            "# Section\n\n"
            "one two three four five six\n\n"
            "seven eight nine ten eleven twelve"
        )])
        chunks = chunk(doc, _make_config(min_tokens=1, max_tokens=5))
        self.assertGreater(len(chunks), 1)

    def test_single_paragraph_over_max_warns(self):
        # One unsplittable paragraph exceeding max_tokens
        big_para = " ".join(f"word{i}" for i in range(20))
        doc = _make_doc([(1, f"# Section\n\n{big_para}")])
        with self.assertLogs("ingestor.chunker", level="WARNING") as cm:
            chunk(doc, _make_config(min_tokens=1, max_tokens=5))
        self.assertTrue(
            any("mid-paragraph" in msg or "exceed" in msg.lower() for msg in cm.output)
        )

    def test_no_chunk_below_min_tokens(self):
        doc = _make_doc([(1,
            "# Alpha\n\nAlpha beta gamma delta epsilon.\n\n"
            "# Beta\n\nZeta eta theta iota kappa lambda."
        )])
        chunks = chunk(doc, _make_config(min_tokens=3, max_tokens=500))
        for ch in chunks:
            self.assertGreaterEqual(
                _count_tokens(ch.content), 3,
                f"{ch.chunk_id} has fewer tokens than min_chunk_tokens"
            )

    def test_no_chunk_above_max_tokens_unless_formula_overflow(self):
        doc = _make_doc([(1,
            "# Section\n\n"
            "alpha beta gamma\n\n"
            "delta epsilon zeta\n\n"
            "eta theta iota kappa"
        )])
        chunks = chunk(doc, _make_config(min_tokens=1, max_tokens=4))
        for ch in chunks:
            if "$$" not in ch.content:
                self.assertLessEqual(
                    _count_tokens(ch.content), 4,
                    f"{ch.chunk_id} exceeds max_tokens without formula overflow"
                )


# ---------------------------------------------------------------------------
# AC-2.4 — Formula blocks never split
# ---------------------------------------------------------------------------

class TestAC24_FormulaBlocksNeverSplit(unittest.TestCase):
    """AC-2.4 — $$...$$ blocks are never split across chunk boundaries."""

    def test_formula_block_kept_intact(self):
        content = (
            "# Section\n\n"
            "Intro text.\n\n"
            "$$\n\\frac{a}{b}\n$$\n\n"
            "Trailing text here."
        )
        doc = _make_doc([(1, content)])
        chunks = chunk(doc, _make_config(min_tokens=1, max_tokens=5))

        all_content = "\n\n".join(ch.content for ch in chunks)
        self.assertIn("$$", all_content)
        self.assertIn(r"\frac{a}{b}", all_content)

    def test_overflow_accepted_with_warning_when_formula_causes_it(self):
        # Formula paragraph with many tokens forces overflow
        big_latex = " ".join(f"term{i}" for i in range(10))  # 10 tokens
        content = f"# Section\n\n$$\n{big_latex}\n$$"
        doc = _make_doc([(1, content)])

        with self.assertLogs("ingestor.chunker", level="WARNING") as cm:
            chunks = chunk(doc, _make_config(min_tokens=1, max_tokens=5))

        self.assertTrue(any("formula" in msg.lower() for msg in cm.output))
        all_content = "\n\n".join(ch.content for ch in chunks)
        self.assertIn("$$", all_content)


# ---------------------------------------------------------------------------
# AC-2.5 — Sequential chunk IDs
# ---------------------------------------------------------------------------

class TestAC25_SequentialChunkIds(unittest.TestCase):
    """AC-2.5 — Chunks receive zero-padded sequential IDs starting at chunk_001."""

    def test_first_chunk_id_is_chunk_001(self):
        doc = _make_doc([(1, "# Start\n\nSome content for the first chunk here.")])
        chunks = chunk(doc, _make_config())
        self.assertEqual(chunks[0].chunk_id, "chunk_001")

    def test_ids_are_zero_padded_to_three_digits(self):
        pages = [(1, "\n\n".join(
            f"# Section {i}\n\nContent for section {i} with enough words here."
            for i in range(1, 12)
        ))]
        doc = _make_doc(pages)
        chunks = chunk(doc, _make_config(min_tokens=1))
        ids = [ch.chunk_id for ch in chunks]
        self.assertIn("chunk_010", ids)
        self.assertIn("chunk_011", ids)
        # Must not have non-padded variant
        self.assertNotIn("chunk_10", ids)

    def test_ids_restart_per_document_run(self):
        doc1 = _make_doc([(1, "# Section A\n\nContent for document one.")])
        doc2 = _make_doc([(1, "# Section B\n\nContent for document two.")])
        config = _make_config()
        chunks1 = chunk(doc1, config)
        chunks2 = chunk(doc2, config)
        self.assertEqual(chunks1[0].chunk_id, "chunk_001")
        self.assertEqual(chunks2[0].chunk_id, "chunk_001")


# ---------------------------------------------------------------------------
# AC-2.6 — Page range tracking
# ---------------------------------------------------------------------------

class TestAC26_PageRangeTracking(unittest.TestCase):
    """AC-2.6 — Each chunk records the page range it spans."""

    def test_page_range_is_list_of_two_ints(self):
        doc = _make_doc([(1, "# Title\n\nSome text content here.")])
        chunks = chunk(doc, _make_config())
        pr = chunks[0].page_range
        self.assertIsInstance(pr, list)
        self.assertEqual(len(pr), 2)
        self.assertIsInstance(pr[0], int)
        self.assertIsInstance(pr[1], int)

    def test_page_range_start_lte_end(self):
        doc = _make_doc([
            (1, "# Chapter One\n\nFirst page content."),
            (2, "Continuing content on page two."),
            (3, "# Chapter Two\n\nNew chapter content."),
        ])
        chunks = chunk(doc, _make_config())
        for ch in chunks:
            self.assertLessEqual(ch.page_range[0], ch.page_range[1])

    def test_multi_page_chunk_records_correct_range(self):
        doc = _make_doc([
            (3, "# Section\n\nPage three content."),
            (4, "Page four content."),
            (5, "Page five content."),
        ])
        chunks = chunk(doc, _make_config())
        self.assertEqual(chunks[0].page_range[0], 3)
        self.assertEqual(chunks[0].page_range[1], 5)


if __name__ == "__main__":
    unittest.main()
