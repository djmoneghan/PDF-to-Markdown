"""
test_embedder.py — Unit tests for ingestor/embedder.py (Phase 2 Stage 7).

All Ollama calls and ChromaDB operations are mocked.  Tests verify:
  - embed_chunks() returns a correct new-embedding count
  - Idempotency: chunks already in ChromaDB are skipped
  - ConnectionError propagates when Ollama is unreachable
  - RuntimeError propagates on ChromaDB write failure
  - Per-chunk embedding failures are logged and skipped (not raised)
  - _flatten_metadata() handles all ChromaDB-incompatible value types
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from ingestor import Chunk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_chunk(chunk_id: str = "chunk_001", content: str = "Hello world.") -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        content=content,
        page_range=[1, 1],
        breadcrumb="Section 1",
        parent_header="Section 1",
        source_file="test.pdf",
        source_id="src-001",
        hitl_status="accepted",
        metadata={"summary": "A test chunk.", "confidence_score": 0.9},
        confidence_score=0.9,
    )


def _minimal_config(chroma_dir: str = "/tmp/chroma_test") -> dict:
    return {
        "ollama": {"endpoint": "http://localhost:11434", "model": "qwen3:8b",
                   "fallback_model": "qwen3:8b", "timeout_seconds": 10},
        "embeddings": {
            "enabled": True,
            "model": "nomic-embed-text",
            "endpoint": "http://localhost:11434",
            "chroma_persist_dir": chroma_dir,
            "collection_name": "test_collection",
            "batch_size": 10,
            "skip_if_embedded": True,
        },
    }


def _mock_collection(existing_ids: list[str] | None = None) -> MagicMock:
    """Return a mock ChromaDB collection."""
    col = MagicMock()
    existing = existing_ids or []
    col.get.side_effect = lambda ids, **_: {"ids": [i for i in ids if i in existing]}
    col.upsert = MagicMock()
    return col


# ---------------------------------------------------------------------------
# Import tests
# ---------------------------------------------------------------------------

class TestEmbedderImports:
    def test_module_importable(self):
        import ingestor.embedder  # noqa: F401

    def test_embed_chunks_function_exists(self):
        from ingestor.embedder import embed_chunks
        assert callable(embed_chunks)

    def test_flatten_metadata_importable(self):
        from ingestor.embedder import _flatten_metadata
        assert callable(_flatten_metadata)


# ---------------------------------------------------------------------------
# _flatten_metadata
# ---------------------------------------------------------------------------

class TestFlattenMetadata:
    def test_primitives_pass_through(self):
        from ingestor.embedder import _flatten_metadata
        meta = {"a": "string", "b": 1, "c": 0.5, "d": True}
        assert _flatten_metadata(meta) == meta

    def test_none_becomes_empty_string(self):
        from ingestor.embedder import _flatten_metadata
        assert _flatten_metadata({"x": None})["x"] == ""

    def test_list_becomes_json_string(self):
        from ingestor.embedder import _flatten_metadata
        import json
        result = _flatten_metadata({"page_range": [1, 3]})
        assert result["page_range"] == json.dumps([1, 3])

    def test_nested_dict_becomes_json_string(self):
        from ingestor.embedder import _flatten_metadata
        import json
        result = _flatten_metadata({"nested": {"a": 1}})
        assert result["nested"] == json.dumps({"a": 1})


# ---------------------------------------------------------------------------
# embed_chunks — success paths
# ---------------------------------------------------------------------------

class TestEmbedChunksSuccess:
    def _run(self, chunks, config, collection, embedding=None):
        """Helper: patch Ollama health check, embeddings call, and ChromaDB."""
        fake_embedding = embedding or [0.1, 0.2, 0.3]

        mock_ollama_client = MagicMock()
        mock_ollama_client.embeddings.return_value = {"embedding": fake_embedding}

        mock_chroma_client = MagicMock()
        mock_chroma_client.get_or_create_collection.return_value = collection

        with patch("ingestor.embedder._ensure_ollama_health"), \
             patch("ingestor.embedder._get_or_create_collection", return_value=collection), \
             patch("ingestor.embedder._get_embedding", return_value=fake_embedding):
            from ingestor.embedder import embed_chunks
            return embed_chunks(chunks, config)

    def test_returns_count_of_newly_embedded(self):
        chunks = [_make_chunk("chunk_001"), _make_chunk("chunk_002")]
        col = _mock_collection(existing_ids=[])
        result = self._run(chunks, _minimal_config(), col)
        assert result == 2

    def test_skips_already_embedded_chunks(self):
        chunks = [_make_chunk("chunk_001"), _make_chunk("chunk_002")]
        col = _mock_collection(existing_ids=["chunk_001"])
        result = self._run(chunks, _minimal_config(), col)
        assert result == 1

    def test_returns_zero_when_all_already_embedded(self):
        chunks = [_make_chunk("chunk_001")]
        col = _mock_collection(existing_ids=["chunk_001"])
        result = self._run(chunks, _minimal_config(), col)
        assert result == 0

    def test_skipping_disabled_when_skip_if_embedded_false(self):
        cfg = _minimal_config()
        cfg["embeddings"]["skip_if_embedded"] = False
        chunks = [_make_chunk("chunk_001")]
        col = _mock_collection(existing_ids=["chunk_001"])
        result = self._run(chunks, cfg, col)
        # skip_if_embedded=False → should embed even if already present
        assert result == 1

    def test_upsert_called_for_each_new_chunk(self):
        chunks = [_make_chunk("chunk_001"), _make_chunk("chunk_002")]
        col = _mock_collection(existing_ids=[])
        self._run(chunks, _minimal_config(), col)
        assert col.upsert.call_count == 2

    def test_upsert_not_called_for_skipped_chunks(self):
        chunks = [_make_chunk("chunk_001")]
        col = _mock_collection(existing_ids=["chunk_001"])
        self._run(chunks, _minimal_config(), col)
        col.upsert.assert_not_called()

    def test_empty_chunks_list_returns_zero(self):
        col = _mock_collection()
        result = self._run([], _minimal_config(), col)
        assert result == 0


# ---------------------------------------------------------------------------
# embed_chunks — error paths
# ---------------------------------------------------------------------------

class TestEmbedChunksErrors:
    def test_connection_error_propagates(self):
        from ingestor.embedder import embed_chunks
        chunks = [_make_chunk()]
        with patch("ingestor.embedder._ensure_ollama_health",
                   side_effect=ConnectionError("Ollama unreachable")), \
             patch("ingestor.embedder._get_or_create_collection"):
            with pytest.raises(ConnectionError, match="Ollama unreachable"):
                embed_chunks(chunks, _minimal_config())

    def test_runtime_error_on_chromadb_open_failure(self):
        from ingestor.embedder import embed_chunks
        chunks = [_make_chunk()]
        with patch("ingestor.embedder._ensure_ollama_health"), \
             patch("ingestor.embedder._get_or_create_collection",
                   side_effect=RuntimeError("ChromaDB open failed")):
            with pytest.raises(RuntimeError, match="ChromaDB open failed"):
                embed_chunks(chunks, _minimal_config())

    def test_per_chunk_embedding_failure_continues(self):
        """A non-connection error on one chunk should not abort the whole batch."""
        from ingestor.embedder import embed_chunks
        chunks = [_make_chunk("chunk_001"), _make_chunk("chunk_002")]
        col = _mock_collection(existing_ids=[])

        call_count = 0
        def _fail_first(text, model, endpoint):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("transient error")
            return [0.1, 0.2, 0.3]

        with patch("ingestor.embedder._ensure_ollama_health"), \
             patch("ingestor.embedder._get_or_create_collection", return_value=col), \
             patch("ingestor.embedder._get_embedding", side_effect=_fail_first):
            result = embed_chunks(chunks, _minimal_config())

        # chunk_001 failed but chunk_002 succeeded
        assert result == 1

    def test_chromadb_write_failure_raises(self):
        from ingestor.embedder import embed_chunks
        chunks = [_make_chunk()]
        col = _mock_collection(existing_ids=[])
        col.upsert.side_effect = Exception("disk full")

        with patch("ingestor.embedder._ensure_ollama_health"), \
             patch("ingestor.embedder._get_or_create_collection", return_value=col), \
             patch("ingestor.embedder._get_embedding", return_value=[0.1]):
            with pytest.raises(RuntimeError, match="ChromaDB write failed"):
                embed_chunks(chunks, _minimal_config())


# ---------------------------------------------------------------------------
# Integration: embed_chunks with real ChromaDB in a temp dir
# ---------------------------------------------------------------------------

class TestEmbedChunksIntegration:
    """Uses a real ChromaDB PersistentClient in tmp_path; mocks only Ollama."""

    def test_real_chromadb_stores_and_skips(self, tmp_path: Path):
        pytest.importorskip("chromadb")
        from ingestor.embedder import embed_chunks

        cfg = _minimal_config(chroma_dir=str(tmp_path / "chroma"))
        chunks = [_make_chunk("chunk_001"), _make_chunk("chunk_002")]

        fake_embedding = [float(i) for i in range(384)]

        with patch("ingestor.embedder._ensure_ollama_health"), \
             patch("ingestor.embedder._get_embedding", return_value=fake_embedding):
            first_run = embed_chunks(chunks, cfg)

        assert first_run == 2

        # Second run — both chunks already present, should skip
        with patch("ingestor.embedder._ensure_ollama_health"), \
             patch("ingestor.embedder._get_embedding", return_value=fake_embedding):
            second_run = embed_chunks(chunks, cfg)

        assert second_run == 0
