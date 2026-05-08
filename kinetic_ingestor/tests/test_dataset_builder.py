"""
test_dataset_builder.py — Unit tests for ingestor/dataset_builder.py (Phase 3).

All filesystem I/O uses tmp_path; no Ollama or ChromaDB calls are made.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from ingestor import Chunk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_corrections_json(
    project_dir: Path,
    records: list[dict],
) -> Path:
    """Write a corrections.json in *project_dir* and return the path."""
    project_dir.mkdir(parents=True, exist_ok=True)
    p = project_dir / "corrections.json"
    p.write_text(json.dumps(records, indent=2), encoding="utf-8")
    return p


def _make_chunk_md(project_dir: Path, chunk_id: str, content: str) -> Path:
    """Write a minimal chunk .md file with YAML frontmatter."""
    project_dir.mkdir(parents=True, exist_ok=True)
    md = project_dir / f"{chunk_id}.md"
    md.write_text(
        "---\nchunk_id: " + chunk_id + "\n---\n\n" + content,
        encoding="utf-8",
    )
    return md


def _edited_record(
    chunk_id: str = "chunk_001",
    source_id: str = "src-001",
    original_summary: str = "Old summary.",
    corrected_summary: str = "New summary.",
    original_topic: str = "General Reference",
    corrected_topic: str = "Fuel Cycle",
) -> dict:
    return {
        "record_id":       "rec-001",
        "chunk_id":        chunk_id,
        "timestamp":       "2026-03-15T10:00:00+00:00",
        "action":          "edited",
        "original_yaml":   {
            "source_id":       source_id,
            "source_file":     "test.pdf",
            "chunk_id":        chunk_id,
            "summary":         original_summary,
            "topic_category":  original_topic,
            "technical_level": "Specialist",
            "confidence_score": 0.75,
        },
        "corrected_yaml":  {
            "source_id":       source_id,
            "source_file":     "test.pdf",
            "chunk_id":        chunk_id,
            "summary":         corrected_summary,
            "topic_category":  corrected_topic,
            "technical_level": "Specialist",
            "confidence_score": 0.90,
        },
        "reason": None,
    }


def _flagged_record(chunk_id: str = "chunk_002") -> dict:
    return {
        "record_id":      "rec-002",
        "chunk_id":       chunk_id,
        "timestamp":      "2026-03-15T11:00:00+00:00",
        "action":         "flagged",
        "original_yaml":  {"chunk_id": chunk_id, "source_id": "src-001"},
        "corrected_yaml": None,
        "reason":         "Garbled text",
    }


# ---------------------------------------------------------------------------
# Import tests
# ---------------------------------------------------------------------------

class TestDatasetBuilderImports:
    def test_module_importable(self):
        import ingestor.dataset_builder  # noqa: F401

    def test_functions_exist(self):
        from ingestor.dataset_builder import (
            aggregate_corrections,
            extract_chunk_content,
            build_training_pairs,
            export_jsonl,
            generate_modelfile,
            compute_stats,
        )
        for fn in (aggregate_corrections, extract_chunk_content, build_training_pairs,
                   export_jsonl, generate_modelfile, compute_stats):
            assert callable(fn)


# ---------------------------------------------------------------------------
# aggregate_corrections
# ---------------------------------------------------------------------------

class TestAggregateCorrections:
    def test_returns_empty_when_no_corrections(self, tmp_path: Path):
        from ingestor.dataset_builder import aggregate_corrections
        result = aggregate_corrections(tmp_path)
        assert result == []

    def test_reads_single_corrections_file(self, tmp_path: Path):
        from ingestor.dataset_builder import aggregate_corrections
        proj = tmp_path / "src-001"
        _make_corrections_json(proj, [_edited_record()])
        result = aggregate_corrections(tmp_path)
        assert len(result) == 1

    def test_aggregates_multiple_documents(self, tmp_path: Path):
        from ingestor.dataset_builder import aggregate_corrections
        _make_corrections_json(tmp_path / "src-001", [_edited_record("chunk_001")])
        _make_corrections_json(tmp_path / "src-002", [_edited_record("chunk_001")])
        result = aggregate_corrections(tmp_path)
        assert len(result) == 2

    def test_injects_source_id(self, tmp_path: Path):
        from ingestor.dataset_builder import aggregate_corrections
        proj = tmp_path / "my-doc-id"
        _make_corrections_json(proj, [_edited_record()])
        result = aggregate_corrections(tmp_path)
        assert result[0]["_source_id"] == "my-doc-id"

    def test_injects_project_dir(self, tmp_path: Path):
        from ingestor.dataset_builder import aggregate_corrections
        proj = tmp_path / "src-001"
        _make_corrections_json(proj, [_edited_record()])
        result = aggregate_corrections(tmp_path)
        assert str(proj) in result[0]["_project_dir"]

    def test_skips_malformed_file(self, tmp_path: Path):
        from ingestor.dataset_builder import aggregate_corrections
        proj = tmp_path / "bad"
        proj.mkdir()
        (proj / "corrections.json").write_text("{bad json}", encoding="utf-8")
        result = aggregate_corrections(tmp_path)
        assert result == []

    def test_sorted_by_timestamp(self, tmp_path: Path):
        from ingestor.dataset_builder import aggregate_corrections
        r1 = _edited_record("chunk_001")
        r1["timestamp"] = "2026-03-15T09:00:00+00:00"
        r2 = _edited_record("chunk_002")
        r2["timestamp"] = "2026-03-15T11:00:00+00:00"
        _make_corrections_json(tmp_path / "src-001", [r2, r1])
        result = aggregate_corrections(tmp_path)
        assert result[0]["chunk_id"] == "chunk_001"
        assert result[1]["chunk_id"] == "chunk_002"


# ---------------------------------------------------------------------------
# extract_chunk_content
# ---------------------------------------------------------------------------

class TestExtractChunkContent:
    def test_returns_content_after_frontmatter(self, tmp_path: Path):
        from ingestor.dataset_builder import extract_chunk_content
        _make_chunk_md(tmp_path, "chunk_001", "Hello world.")
        result = extract_chunk_content("chunk_001", tmp_path)
        assert result == "Hello world."

    def test_returns_none_when_file_missing(self, tmp_path: Path):
        from ingestor.dataset_builder import extract_chunk_content
        result = extract_chunk_content("chunk_999", tmp_path)
        assert result is None

    def test_no_frontmatter_returns_full_text(self, tmp_path: Path):
        from ingestor.dataset_builder import extract_chunk_content
        p = tmp_path / "chunk_001.md"
        p.write_text("Plain content.", encoding="utf-8")
        result = extract_chunk_content("chunk_001", tmp_path)
        assert result == "Plain content."

    def test_strips_trailing_whitespace(self, tmp_path: Path):
        from ingestor.dataset_builder import extract_chunk_content
        p = tmp_path / "chunk_001.md"
        p.write_text("---\nk: v\n---\n\nContent here.  \n\n", encoding="utf-8")
        result = extract_chunk_content("chunk_001", tmp_path)
        assert result == "Content here."


# ---------------------------------------------------------------------------
# build_training_pairs
# ---------------------------------------------------------------------------

class TestBuildTrainingPairs:
    def _setup(self, tmp_path: Path):
        proj = tmp_path / "src-001"
        _make_corrections_json(proj, [_edited_record()])
        _make_chunk_md(proj, "chunk_001", "Fuel cycle content.")
        from ingestor.dataset_builder import aggregate_corrections
        corrections = aggregate_corrections(tmp_path)
        for r in corrections:
            r["_project_dir"] = str(proj)
        return corrections, tmp_path

    def test_returns_list_of_pairs(self, tmp_path: Path):
        from ingestor.dataset_builder import build_training_pairs
        corrections, root = self._setup(tmp_path)
        pairs = build_training_pairs(corrections, root)
        assert isinstance(pairs, list)
        assert len(pairs) > 0

    def test_only_edited_records_produce_pairs(self, tmp_path: Path):
        from ingestor.dataset_builder import aggregate_corrections, build_training_pairs
        proj = tmp_path / "src-001"
        _make_corrections_json(proj, [_flagged_record("chunk_002")])
        _make_chunk_md(proj, "chunk_002", "Some flagged content.")
        corrections = aggregate_corrections(tmp_path)
        pairs = build_training_pairs(corrections, tmp_path)
        assert pairs == []

    def test_pair_has_required_fields(self, tmp_path: Path):
        from ingestor.dataset_builder import build_training_pairs
        corrections, root = self._setup(tmp_path)
        pairs = build_training_pairs(corrections, root)
        for pair in pairs:
            for key in ("field", "instruction", "input", "output", "metadata"):
                assert key in pair, f"Missing key: {key}"

    def test_field_values_are_trainable_fields(self, tmp_path: Path):
        from ingestor.dataset_builder import build_training_pairs, _TRAINABLE_FIELDS
        corrections, root = self._setup(tmp_path)
        pairs = build_training_pairs(corrections, root)
        for pair in pairs:
            assert pair["field"] in _TRAINABLE_FIELDS

    def test_output_is_corrected_value(self, tmp_path: Path):
        from ingestor.dataset_builder import build_training_pairs
        corrections, root = self._setup(tmp_path)
        pairs = build_training_pairs(corrections, root)
        summary_pair = next((p for p in pairs if p["field"] == "summary"), None)
        assert summary_pair is not None
        assert summary_pair["output"] == "New summary."

    def test_unchanged_fields_not_included(self, tmp_path: Path):
        from ingestor.dataset_builder import build_training_pairs
        # technical_level is same in original and corrected (_edited_record keeps it)
        corrections, root = self._setup(tmp_path)
        pairs = build_training_pairs(corrections, root)
        # technical_level was not changed → should not appear
        tl_pairs = [p for p in pairs if p["field"] == "technical_level"]
        assert tl_pairs == []

    def test_instruction_contains_chunk_content(self, tmp_path: Path):
        from ingestor.dataset_builder import build_training_pairs
        corrections, root = self._setup(tmp_path)
        pairs = build_training_pairs(corrections, root)
        for pair in pairs:
            assert "Fuel cycle content." in pair["instruction"]

    def test_metadata_contains_source_info(self, tmp_path: Path):
        from ingestor.dataset_builder import build_training_pairs
        corrections, root = self._setup(tmp_path)
        pairs = build_training_pairs(corrections, root)
        for pair in pairs:
            assert "source_id" in pair["metadata"]
            assert "chunk_id" in pair["metadata"]
            assert pair["metadata"]["chunk_id"] == "chunk_001"

    def test_missing_chunk_file_skips_record(self, tmp_path: Path):
        from ingestor.dataset_builder import aggregate_corrections, build_training_pairs
        proj = tmp_path / "src-001"
        _make_corrections_json(proj, [_edited_record()])
        # Do NOT create the .md file
        corrections = aggregate_corrections(tmp_path)
        pairs = build_training_pairs(corrections, tmp_path)
        assert pairs == []


# ---------------------------------------------------------------------------
# export_jsonl
# ---------------------------------------------------------------------------

class TestExportJsonl:
    def _make_pairs(self) -> list[dict]:
        return [
            {
                "field":       "summary",
                "instruction": "Summarise this: Hello world.",
                "input":       "",
                "output":      "Corrected summary.",
                "metadata":    {},
            }
        ]

    def test_alpaca_format(self, tmp_path: Path):
        from ingestor.dataset_builder import export_jsonl
        out = tmp_path / "dataset.jsonl"
        n = export_jsonl(self._make_pairs(), out, fmt="alpaca")
        assert n == 1
        record = json.loads(out.read_text(encoding="utf-8"))
        assert "instruction" in record
        assert "input" in record
        assert "output" in record

    def test_openai_format(self, tmp_path: Path):
        from ingestor.dataset_builder import export_jsonl
        out = tmp_path / "dataset.jsonl"
        n = export_jsonl(self._make_pairs(), out, fmt="openai")
        assert n == 1
        record = json.loads(out.read_text(encoding="utf-8"))
        assert "messages" in record
        roles = [m["role"] for m in record["messages"]]
        assert "system" in roles
        assert "user" in roles
        assert "assistant" in roles

    def test_creates_parent_dirs(self, tmp_path: Path):
        from ingestor.dataset_builder import export_jsonl
        out = tmp_path / "deep" / "nested" / "out.jsonl"
        export_jsonl(self._make_pairs(), out)
        assert out.exists()

    def test_returns_count(self, tmp_path: Path):
        from ingestor.dataset_builder import export_jsonl
        pairs = self._make_pairs() * 5
        out = tmp_path / "out.jsonl"
        n = export_jsonl(pairs, out)
        assert n == 5

    def test_invalid_format_raises(self, tmp_path: Path):
        from ingestor.dataset_builder import export_jsonl
        with pytest.raises(ValueError, match="Unknown format"):
            export_jsonl([], tmp_path / "x.jsonl", fmt="bad_format")

    def test_empty_pairs_writes_empty_file(self, tmp_path: Path):
        from ingestor.dataset_builder import export_jsonl
        out = tmp_path / "empty.jsonl"
        n = export_jsonl([], out)
        assert n == 0
        assert out.read_text() == ""


# ---------------------------------------------------------------------------
# generate_modelfile
# ---------------------------------------------------------------------------

class TestGenerateModelfile:
    def _make_pairs(self, fields: list[str]) -> list[dict]:
        return [
            {
                "field": f,
                "instruction": "...",
                "input": "",
                "output": "...",
                "metadata": {"source_id": "src-1", "chunk_id": "c", "source_file": "a.pdf",
                             "record_id": "r", "correction_timestamp": "", "original_value": ""},
            }
            for f in fields
        ]

    def test_creates_file(self, tmp_path: Path):
        from ingestor.dataset_builder import generate_modelfile
        out = tmp_path / "test.Modelfile"
        generate_modelfile([], "qwen3:8b", out)
        assert out.exists()

    def test_from_line_contains_base_model(self, tmp_path: Path):
        from ingestor.dataset_builder import generate_modelfile
        out = tmp_path / "test.Modelfile"
        generate_modelfile([], "qwen3:8b", out)
        content = out.read_text(encoding="utf-8")
        assert "FROM qwen3:8b" in content

    def test_system_block_present(self, tmp_path: Path):
        from ingestor.dataset_builder import generate_modelfile
        out = tmp_path / "test.Modelfile"
        generate_modelfile([], "qwen3:8b", out)
        content = out.read_text(encoding="utf-8")
        assert "SYSTEM" in content

    def test_field_specific_guidance_included(self, tmp_path: Path):
        from ingestor.dataset_builder import generate_modelfile
        out = tmp_path / "test.Modelfile"
        pairs = self._make_pairs(["summary", "topic_category"])
        generate_modelfile(pairs, "qwen3:8b", out)
        content = out.read_text(encoding="utf-8")
        assert "Summary" in content
        assert "topic" in content.lower()

    def test_correction_count_in_modelfile(self, tmp_path: Path):
        from ingestor.dataset_builder import generate_modelfile
        out = tmp_path / "test.Modelfile"
        pairs = self._make_pairs(["summary"] * 7)
        generate_modelfile(pairs, "qwen3:8b", out)
        content = out.read_text(encoding="utf-8")
        assert "7" in content

    def test_creates_parent_dirs(self, tmp_path: Path):
        from ingestor.dataset_builder import generate_modelfile
        out = tmp_path / "deep" / "model.Modelfile"
        generate_modelfile([], "qwen3:8b", out)
        assert out.exists()


# ---------------------------------------------------------------------------
# compute_stats
# ---------------------------------------------------------------------------

class TestComputeStats:
    def test_empty_returns_zeros(self):
        from ingestor.dataset_builder import compute_stats
        stats = compute_stats([])
        assert stats["total_corrections"] == 0
        assert stats["documents_with_corrections"] == 0
        assert stats["trainable_pairs_available"] == 0

    def test_total_corrections_count(self):
        from ingestor.dataset_builder import compute_stats
        records = [_edited_record(), _flagged_record()]
        stats = compute_stats(records)
        assert stats["total_corrections"] == 2

    def test_by_action_counts(self):
        from ingestor.dataset_builder import compute_stats
        records = [_edited_record(), _flagged_record()]
        stats = compute_stats(records)
        assert stats["by_action"]["edited"] == 1
        assert stats["by_action"]["flagged"] == 1

    def test_by_field_counts_changed_fields(self):
        from ingestor.dataset_builder import compute_stats
        # summary and topic_category both changed in _edited_record
        stats = compute_stats([_edited_record()])
        assert stats["by_field"].get("summary", 0) == 1
        assert stats["by_field"].get("topic_category", 0) == 1

    def test_unchanged_field_not_counted(self):
        from ingestor.dataset_builder import compute_stats
        # technical_level is not changed in default _edited_record
        stats = compute_stats([_edited_record()])
        assert stats["by_field"].get("technical_level", 0) == 0

    def test_confidence_delta_positive_when_increased(self):
        from ingestor.dataset_builder import compute_stats
        # original 0.75, corrected 0.90 → delta > 0
        stats = compute_stats([_edited_record()])
        assert stats["confidence_delta"] is not None
        assert stats["confidence_delta"] > 0

    def test_edit_rate_calculation(self):
        from ingestor.dataset_builder import compute_stats
        records = [_edited_record(), _flagged_record(), _flagged_record()]
        stats = compute_stats(records)
        # 1 edited / 3 total = 0.3333
        assert stats["edit_rate"] == pytest.approx(1 / 3, abs=0.001)

    def test_documents_with_corrections(self):
        from ingestor.dataset_builder import compute_stats
        r1 = _edited_record()
        r1["_source_id"] = "doc-1"
        r2 = _edited_record()
        r2["_source_id"] = "doc-2"
        stats = compute_stats([r1, r2])
        assert stats["documents_with_corrections"] == 2

    def test_trainable_pairs_available(self):
        from ingestor.dataset_builder import compute_stats
        # summary + topic_category + confidence_score changed = 3 pairs available
        stats = compute_stats([_edited_record()])
        assert stats["trainable_pairs_available"] == 3

    def test_no_confidence_delta_when_no_corrections(self):
        from ingestor.dataset_builder import compute_stats
        stats = compute_stats([])
        assert stats["confidence_delta"] is None
        assert stats["avg_confidence_before_edit"] is None
        assert stats["avg_confidence_after_edit"] is None
