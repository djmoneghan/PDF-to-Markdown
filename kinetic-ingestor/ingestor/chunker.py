# ingestor/chunker.py
# DocumentContent -> list[Chunk] semantic splitting.


def chunk(doc, config):
    """Split a DocumentContent into a list of Chunk objects.

    Args:
        doc:    DocumentContent produced by ingestor.extractor.extract().
        config: dict loaded by ingestor.load_config().

    Returns:
        list[Chunk]
    """
    raise NotImplementedError("Phase 5: chunker not yet implemented.")
