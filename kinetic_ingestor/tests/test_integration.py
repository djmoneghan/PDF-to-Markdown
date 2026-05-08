# tests/test_integration.py
# End-to-end integration tests for the Kinetic Ingestor pipeline.
#
# These tests exercise the full extract → chunk → metadata → HITL → export
# chain using real filesystem I/O, a synthetic test PDF, and a mocked Ollama
# client.  They are intentionally excluded from fast unit-test runs:
#
#   pytest -m "not integration"   # unit tests only
#   pytest -m integration         # integration tests only

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Path bootstrap — ensure the package root is importable when pytest is
# invoked from any working directory.
# ---------------------------------------------------------------------------

_KI_ROOT = Path(__file__).parents[1]
if str(_KI_ROOT) not in sys.path:
    sys.path.insert(0, str(_KI_ROOT))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def minimal_pdf(tmp_path: Path) -> Path:
    """Create a minimal two-section PDF via PyMuPDF and return its path.

    The text contains markdown-style headers so the chunker can produce at
    least two chunks, exercising the full splitting logic.
    """
    fitz = pytest.importorskip("fitz", reason="PyMuPDF (fitz) not installed")

    pdf_path = tmp_path / "test_document.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (50, 72),
        (
            "# Introduction\n\n"
            "This document is used to exercise the Kinetic Ingestor integration pipeline.\n"
            "It contains enough text to satisfy the minimum token threshold and produce\n"
            "at least one valid chunk after splitting.\n\n"
            "## Background\n\n"
            "Nuclear engineering documentation requires precise extraction and chunking.\n"
            "Formulae and structural elements must be preserved with high fidelity.\n"
            "This section provides sufficient content for a second chunk."
        ),
    )
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.fixture()
def pipeline_config(tmp_path: Path) -> Path:
    """Write a minimal config.yaml to tmp_path; return its path.

    - Uses pymupdf engine so Docling is not required.
    - Points processed_root at a subdirectory of tmp_path.
    - Low token thresholds to guarantee chunking on a tiny test document.
    """
    config = {
        "ollama": {
            "endpoint": "http://localhost:11434",
            "model": "test-model",
            "fallback_model": "test-fallback",
            "timeout_seconds": 10,
        },
        "extraction": {
            "engine": "pymupdf",
            "confidence_threshold": 0.0,
        },
        "chunking": {
            "split_levels": ["#", "##"],
            "min_chunk_tokens": 5,
            "max_chunk_tokens": 1500,
        },
        "output": {
            "processed_root": str(tmp_path / "processed"),
            "manifest_filename": "manifest.json",
            "corrections_filename": "corrections.json",
        },
        "hitl": {
            "auto_accept_above": 0.92,
            "show_raw_markdown": True,
        },
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config), encoding="utf-8")
    return config_path


# ---------------------------------------------------------------------------
# Ollama stub
# ---------------------------------------------------------------------------

def _fake_generate_metadata(chunk, config):
    """Return deterministic, schema-valid metadata without hitting Ollama."""
    meta = {
        "source_id":        chunk.source_id,
        "source_file":      chunk.source_file,
        "chunk_id":         chunk.chunk_id,
        "page_range":       chunk.page_range,
        "breadcrumb":       chunk.breadcrumb,
        "parent_header":    chunk.parent_header,
        "topic_category":   "General Reference",
        "technical_level":  "Specialist",
        "summary":          f"Integration test summary for {chunk.chunk_id}.",
        "confidence_score": 0.95,
        "extraction_engine": chunk.extraction_engine,
        "hitl_status":      chunk.hitl_status,
        "corrections_ref":  chunk.corrections_ref,
    }
    chunk.metadata = meta
    chunk.confidence_score = 0.95
    return meta


def _run(argv: list[str]) -> int:
    """Import main fresh and invoke main() with the given argv."""
    from main import main
    return main(argv=argv)


def _base_argv(pdf: Path, cfg: Path) -> list[str]:
    return [str(pdf), "--no-hitl", "--force", "--config", str(cfg)]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNoHitlFlag:
    """--no-hitl CLI flag: parsing and pipeline bypass."""

    def test_flag_is_recognised_by_parser(self):
        from main import _build_parser
        args = _build_parser().parse_args(["dummy.pdf", "--no-hitl"])
        assert args.no_hitl is True

    def test_flag_defaults_to_false(self):
        from main import _build_parser
        args = _build_parser().parse_args(["dummy.pdf"])
        assert args.no_hitl is False


class TestFullPipeline:
    """End-to-end pipeline with --no-hitl and mocked Ollama."""

    def test_pipeline_exits_zero(self, minimal_pdf, pipeline_config):
        with patch("ingestor.metadata.generate_metadata", side_effect=_fake_generate_metadata):
            ec = _run(_base_argv(minimal_pdf, pipeline_config))
        assert ec == 0

    def test_processed_root_directory_created(self, minimal_pdf, pipeline_config, tmp_path):
        with patch("ingestor.metadata.generate_metadata", side_effect=_fake_generate_metadata):
            _run(_base_argv(minimal_pdf, pipeline_config))
        proc_root = tmp_path / "processed"
        assert proc_root.exists(), "processed/ root not created"

    def test_source_id_subdirectory_created(self, minimal_pdf, pipeline_config, tmp_path):
        with patch("ingestor.metadata.generate_metadata", side_effect=_fake_generate_metadata):
            _run(_base_argv(minimal_pdf, pipeline_config))
        proc_root = tmp_path / "processed"
        subdirs = [p for p in proc_root.iterdir() if p.is_dir()]
        assert len(subdirs) == 1, f"Expected exactly one source_id directory; got {subdirs}"

    def test_at_least_one_chunk_md_written(self, minimal_pdf, pipeline_config, tmp_path):
        with patch("ingestor.metadata.generate_metadata", side_effect=_fake_generate_metadata):
            _run(_base_argv(minimal_pdf, pipeline_config))
        md_files = list((tmp_path / "processed").rglob("chunk_*.md"))
        assert md_files, "No chunk_*.md files were written"

    def test_manifest_json_written(self, minimal_pdf, pipeline_config, tmp_path):
        with patch("ingestor.metadata.generate_metadata", side_effect=_fake_generate_metadata):
            _run(_base_argv(minimal_pdf, pipeline_config))
        manifests = list((tmp_path / "processed").rglob("manifest.json"))
        assert manifests, "manifest.json not found in output"

    def test_manifest_contains_required_fields(self, minimal_pdf, pipeline_config, tmp_path):
        with patch("ingestor.metadata.generate_metadata", side_effect=_fake_generate_metadata):
            _run(_base_argv(minimal_pdf, pipeline_config))
        manifest_path = next((tmp_path / "processed").rglob("manifest.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for key in ("source_id", "source_file", "chunk_count", "chunks", "extraction_engine"):
            assert key in manifest, f"manifest.json missing required field: {key!r}"

    def test_manifest_chunk_count_matches_files(self, minimal_pdf, pipeline_config, tmp_path):
        with patch("ingestor.metadata.generate_metadata", side_effect=_fake_generate_metadata):
            _run(_base_argv(minimal_pdf, pipeline_config))
        proc_root = tmp_path / "processed"
        manifest = json.loads(next(proc_root.rglob("manifest.json")).read_text())
        md_files = list(proc_root.rglob("chunk_*.md"))
        assert manifest["chunk_count"] == len(md_files)

    def test_chunk_md_files_start_with_yaml_frontmatter(self, minimal_pdf, pipeline_config, tmp_path):
        with patch("ingestor.metadata.generate_metadata", side_effect=_fake_generate_metadata):
            _run(_base_argv(minimal_pdf, pipeline_config))
        for md_file in (tmp_path / "processed").rglob("chunk_*.md"):
            content = md_file.read_text(encoding="utf-8")
            assert content.startswith("---\n"), \
                f"{md_file.name} does not begin with YAML frontmatter"
            assert "\n---\n" in content, \
                f"{md_file.name} frontmatter block is not closed"

    def test_no_hitl_sets_status_accepted_in_frontmatter(self, minimal_pdf, pipeline_config, tmp_path):
        with patch("ingestor.metadata.generate_metadata", side_effect=_fake_generate_metadata):
            _run(_base_argv(minimal_pdf, pipeline_config))
        for md_file in (tmp_path / "processed").rglob("chunk_*.md"):
            content = md_file.read_text(encoding="utf-8")
            assert "hitl_status: accepted" in content, \
                f"{md_file.name}: hitl_status should be 'accepted' with --no-hitl"

    def test_chunk_ids_are_zero_padded_sequential(self, minimal_pdf, pipeline_config, tmp_path):
        with patch("ingestor.metadata.generate_metadata", side_effect=_fake_generate_metadata):
            _run(_base_argv(minimal_pdf, pipeline_config))
        md_files = sorted((tmp_path / "processed").rglob("chunk_*.md"))
        for i, md_file in enumerate(md_files, 1):
            assert md_file.stem == f"chunk_{i:03d}", \
                f"Expected chunk_{i:03d}.md, got {md_file.name}"

    def test_force_flag_allows_second_run_without_error(self, minimal_pdf, pipeline_config):
        with patch("ingestor.metadata.generate_metadata", side_effect=_fake_generate_metadata):
            ec1 = _run(_base_argv(minimal_pdf, pipeline_config))
            ec2 = _run(_base_argv(minimal_pdf, pipeline_config))
        assert ec1 == 0
        assert ec2 == 0


class TestPipelineErrorHandling:
    """Pipeline surface-level error propagation."""

    def test_nonexistent_pdf_returns_exit_code_1(self, pipeline_config):
        ec = _run(["/no/such/file.pdf", "--no-hitl", "--config", str(pipeline_config)])
        assert ec == 1

    def test_missing_config_returns_exit_code_1(self, minimal_pdf, tmp_path):
        ec = _run([str(minimal_pdf), "--no-hitl", "--config", str(tmp_path / "missing.yaml")])
        assert ec == 1
