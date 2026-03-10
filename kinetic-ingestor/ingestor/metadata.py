# ingestor/metadata.py
# Chunk -> YAML metadata via local Ollama endpoint.


class MetadataGenerationError(Exception):
    """Raised when Ollama fails twice for a given chunk field."""


def generate_metadata(chunk, config):
    """Generate and attach YAML metadata to a Chunk via the local Ollama endpoint.

    Args:
        chunk:  Chunk object produced by ingestor.chunker.chunk().
        config: dict loaded by ingestor.load_config().

    Returns:
        dict conforming to the YAML frontmatter schema in CLAUDE.md.

    Raises:
        ConnectionError: if the Ollama health check fails.
        MetadataGenerationError: if a field times out twice.
    """
    raise NotImplementedError("Phase 6: metadata generator not yet implemented.")
