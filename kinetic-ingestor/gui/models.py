# gui/models.py
# PyQt6 models for chunk list presentation.

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, QAbstractListModel, QModelIndex, pyqtSignal


class ChunkListModel(QAbstractListModel):
    """
    Model for presenting a list of chunks in a QListView or similar.

    Wraps a list of Chunk objects; provides row count and data access.
    """

    # Signal: emitted when selection changes
    selection_changed = pyqtSignal(int, str)  # (row_index, chunk_id)

    def __init__(self, chunks: list[Any] | None = None, parent=None):
        """
        Initialize the model.

        Args:
            chunks: Initial list of Chunk objects (default: empty).
            parent: Parent QObject.
        """
        super().__init__(parent)
        self.chunks = chunks or []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """Return the number of chunks."""
        if parent.isValid():
            return 0
        return len(self.chunks)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        """
        Return data for a given index and role.

        Args:
            index: Model index of the item.
            role: Qt role (DisplayRole for display text, etc.).

        Returns:
            The requested data, or None if not available.
        """
        if not index.isValid() or index.row() >= len(self.chunks):
            return None

        chunk = self.chunks[index.row()]

        if role == Qt.ItemDataRole.DisplayRole:
            # Display: "chunk_001 — Summary preview..."
            summary = chunk.metadata.get("summary", "")[:50] if chunk.metadata else ""
            return f"{chunk.chunk_id} — {summary}"

        elif role == Qt.ItemDataRole.UserRole:
            # Return the chunk object itself
            return chunk

        elif role == Qt.ItemDataRole.ToolTipRole:
            # Tooltip: full metadata summary
            if chunk.metadata:
                return f"Confidence: {chunk.confidence_score:.2f}\nStatus: {chunk.hitl_status}"
            return None

        return None

    def set_chunks(self, chunks: list[Any]) -> None:
        """
        Replace the entire chunk list.

        Args:
            chunks: New list of Chunk objects.
        """
        self.beginResetModel()
        self.chunks = chunks
        self.endResetModel()

    def get_chunk(self, row: int) -> Any | None:
        """
        Get a chunk by row index.

        Args:
            row: Row index in the model.

        Returns:
            The Chunk object, or None if row is out of bounds.
        """
        if 0 <= row < len(self.chunks):
            return self.chunks[row]
        return None

    def get_chunk_by_id(self, chunk_id: str) -> Any | None:
        """
        Find a chunk by its chunk_id.

        Args:
            chunk_id: The chunk_id to search for (e.g., "chunk_001").

        Returns:
            The Chunk object, or None if not found.
        """
        for chunk in self.chunks:
            if chunk.chunk_id == chunk_id:
                return chunk
        return None
