# ingestor/hitl_base.py
# Abstract base class for HITL (Human-In-The-Loop) review backends.

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class HitlReviewBackend(ABC):
    """
    Abstract interface for HITL review implementations.

    Allows CLI (terminal-based Rich UI) and GUI (PyQt UI) implementations
    to be swapped without changing the pipeline orchestrator.
    """

    @abstractmethod
    def review(self, chunks: list[Any], config: dict[str, Any]) -> list[Any]:
        """
        Present chunks for human review and return reviewed chunks.

        Args:
            chunks: List of Chunk objects (post-metadata, pre-export).
                    Each chunk has: chunk_id, content, metadata, confidence_score.
            config: Loaded configuration dict.

        Returns:
            The same list of chunks, each with hitl_status updated to one of:
            - "accepted": human approved without changes
            - "edited": human modified YAML metadata
            - "flagged": human marked for manual review
            - "pending": not yet reviewed (should not occur at return)

        Side effects:
            May write to corrections.json (if edits occurred).
            May raise KeyboardInterrupt if user aborts the review session.
        """
        pass
