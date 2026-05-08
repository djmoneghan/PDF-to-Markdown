# ingestor/embedder.py
# Stage 7 — embed chunks into ChromaDB via Ollama /api/embeddings.

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger("ingestor.embedder")

# Per-session health-check cache (mirrors metadata.py pattern)
_checked_endpoints: set[str] = set()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def embed_chunks(chunks: list[Any], config: dict[str, Any]) -> int:
    """Embed all chunks into ChromaDB via Ollama.

    Reads ``config["embeddings"]`` for model, endpoint, and ChromaDB settings.
    Idempotent: chunks whose ``chunk_id`` already exists in the collection are
    skipped when ``skip_if_embedded`` is true (the default).

    Args:
        chunks: Post-HITL list of Chunk objects.
        config: Full config dict loaded by ``ingestor.load_config()``.

    Returns:
        Number of chunks newly embedded (skipped chunks are not counted).

    Raises:
        ConnectionError: if the Ollama embeddings endpoint is unreachable.
        RuntimeError:    if a ChromaDB write fails.
    """
    emb_cfg = config.get("embeddings", {})
    model = emb_cfg.get("model", "nomic-embed-text")
    endpoint = emb_cfg.get("endpoint", config.get("ollama", {}).get("endpoint", "http://localhost:11434"))
    chroma_dir = emb_cfg.get("chroma_persist_dir", "../../workspace/chroma_db")
    collection_name = emb_cfg.get("collection_name", "kinetic_ingestor_chunks")
    skip_if_embedded = emb_cfg.get("skip_if_embedded", True)

    # Resolve chroma_dir relative to this file's package root if not absolute
    from pathlib import Path
    chroma_path = Path(chroma_dir)
    if not chroma_path.is_absolute():
        chroma_path = (Path(__file__).parents[1] / chroma_dir).resolve()

    _ensure_ollama_health(endpoint)
    collection = _get_or_create_collection(str(chroma_path), collection_name)

    newly_embedded = 0
    for ch in chunks:
        try:
            if skip_if_embedded and _already_embedded(collection, ch.chunk_id):
                log.debug("Skipping already-embedded chunk %s", ch.chunk_id)
                continue

            embedding = _get_embedding(ch.content, model, endpoint)
            _store_chunk(collection, ch, embedding)
            newly_embedded += 1
            log.debug("Embedded chunk %s", ch.chunk_id)

        except (ConnectionError, RuntimeError):
            raise
        except Exception as exc:
            log.warning("Embedding failed for chunk %s: %s", ch.chunk_id, exc)

    log.info(
        "embed_chunks complete: %d newly embedded, %d total chunks",
        newly_embedded, len(chunks),
    )
    return newly_embedded


# ---------------------------------------------------------------------------
# Ollama health check (mirrors metadata.py)
# ---------------------------------------------------------------------------

def _ensure_ollama_health(endpoint: str) -> None:
    """Check Ollama reachability once per endpoint per process session."""
    if endpoint in _checked_endpoints:
        return
    url = f"{endpoint}/api/tags"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except (urllib.error.URLError, OSError, Exception) as exc:
        raise ConnectionError(
            f"Ollama endpoint unreachable at {url}: {exc}"
        ) from exc
    _checked_endpoints.add(endpoint)


# ---------------------------------------------------------------------------
# ChromaDB helpers
# ---------------------------------------------------------------------------

def _get_or_create_collection(chroma_path: str, collection_name: str) -> Any:
    """Open (or create) the persistent ChromaDB collection."""
    try:
        import chromadb  # lazy import — not required unless embedding is enabled
    except ImportError as exc:
        raise RuntimeError(
            "chromadb is not installed. Run: pip install chromadb"
        ) from exc

    try:
        client = chromadb.PersistentClient(path=chroma_path)
        return client.get_or_create_collection(name=collection_name)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to open ChromaDB at {chroma_path!r}: {exc}"
        ) from exc


def _already_embedded(collection: Any, chunk_id: str) -> bool:
    """Return True if chunk_id is already in the collection."""
    try:
        result = collection.get(ids=[chunk_id])
        return bool(result and result.get("ids"))
    except Exception:
        return False


def _get_embedding(text: str, model: str, endpoint: str) -> list[float]:
    """Call Ollama /api/embeddings and return the float vector."""
    import ollama as _ollama  # lazy import

    try:
        client = _ollama.Client(host=endpoint)
        resp = client.embeddings(model=model, prompt=text)
        if isinstance(resp, dict):
            return resp["embedding"]
        return list(resp.embedding)
    except Exception as exc:
        # Surface connection errors clearly
        msg = str(exc).lower()
        if any(k in msg for k in ("connect", "connection", "refused", "unreachable")):
            raise ConnectionError(
                f"Ollama embeddings endpoint unreachable at {endpoint}: {exc}"
            ) from exc
        raise


def _store_chunk(collection: Any, chunk: Any, embedding: list[float]) -> None:
    """Write a chunk + its embedding into ChromaDB."""
    meta = _flatten_metadata(chunk.metadata if chunk.metadata else {
        "source_id":    chunk.source_id,
        "source_file":  chunk.source_file,
        "chunk_id":     chunk.chunk_id,
        "hitl_status":  chunk.hitl_status,
    })
    try:
        collection.upsert(
            ids=[chunk.chunk_id],
            embeddings=[embedding],
            documents=[chunk.content],
            metadatas=[meta],
        )
    except Exception as exc:
        raise RuntimeError(
            f"ChromaDB write failed for chunk {chunk.chunk_id!r}: {exc}"
        ) from exc


def _flatten_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Convert a metadata dict to ChromaDB-compatible flat types.

    ChromaDB only accepts str, int, float, and bool as metadata values.
    Lists and nested dicts are JSON-serialised to strings.
    None is converted to empty string.
    """
    flat: dict[str, Any] = {}
    for k, v in meta.items():
        if isinstance(v, (str, int, float, bool)):
            flat[k] = v
        elif v is None:
            flat[k] = ""
        else:
            flat[k] = json.dumps(v)
    return flat
