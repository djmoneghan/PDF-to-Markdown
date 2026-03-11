# gui/widgets/preview_tab.py
# Preview tab: view converted chunks, navigate, and download.

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QTextEdit, QPushButton,
    QGroupBox, QTableWidget, QTableWidgetItem, QFileDialog, QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QFont

log = logging.getLogger(__name__)


class PreviewTab(QWidget):
    """
    Preview tab for viewing converted chunks.

    Features:
      - Chunk navigator (dropdown)
      - Markdown preview pane
      - YAML metadata display
      - Download single chunk
      - Statistics summary
    """

    def __init__(self):
        """Initialize the preview tab."""
        super().__init__()
        self.chunks: list[Any] = []
        self.output_dir: Path | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Initialize the user interface."""
        layout = QVBoxLayout(self)

        # Title
        title = QLabel("Output Preview")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        # Navigation group
        nav_group = QGroupBox("Chunk Navigator")
        nav_layout = QHBoxLayout(nav_group)
        nav_layout.addWidget(QLabel("Chunk:"))
        self.chunk_combo = QComboBox()
        self.chunk_combo.currentIndexChanged.connect(self._on_chunk_selected)
        nav_layout.addWidget(self.chunk_combo)
        layout.addWidget(nav_group)

        # Preview group
        preview_group = QGroupBox("Markdown Preview")
        preview_layout = QVBoxLayout(preview_group)
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        preview_layout.addWidget(self.preview_text)
        layout.addWidget(preview_group)

        # Metadata group
        meta_group = QGroupBox("Metadata")
        meta_layout = QVBoxLayout(meta_group)
        self.meta_table = QTableWidget()
        self.meta_table.setColumnCount(2)
        self.meta_table.setHorizontalHeaderLabels(["Key", "Value"])
        # Items will be set non-editable when added in _update_metadata_table()
        meta_layout.addWidget(self.meta_table)
        layout.addWidget(meta_group)

        # Control buttons
        button_layout = QHBoxLayout()
        self.download_button = QPushButton("Download This Chunk")
        self.download_button.clicked.connect(self._on_download_chunk)
        button_layout.addWidget(self.download_button)

        self.open_folder_button = QPushButton("Open Output Folder")
        self.open_folder_button.clicked.connect(self._on_open_folder)
        button_layout.addWidget(self.open_folder_button)

        button_layout.addStretch()
        layout.addLayout(button_layout)

    def update_chunks(self, chunks: list[Any], output_dir: Path) -> None:
        """
        Update the chunk list and output directory.

        Args:
            chunks: List of Chunk objects from the completed run.
            output_dir: Path to the output directory.
        """
        self.chunks = chunks
        self.output_dir = output_dir

        self.chunk_combo.clear()
        for chunk in chunks:
            self.chunk_combo.addItem(chunk.chunk_id)

        # Display first chunk
        if chunks:
            self._on_chunk_selected(0)

    @pyqtSlot(int)
    def _on_chunk_selected(self, index: int) -> None:
        """Handle chunk selection."""
        if 0 <= index < len(self.chunks):
            chunk = self.chunks[index]
            self.preview_text.setPlainText(chunk.content)
            self._update_metadata_table(chunk)

    def _update_metadata_table(self, chunk: Any) -> None:
        """Update the metadata table for the selected chunk."""
        from PyQt6.QtCore import Qt
        self.meta_table.setRowCount(0)
        if not chunk.metadata:
            return

        row = 0
        for key, value in chunk.metadata.items():
            self.meta_table.insertRow(row)
            key_item = QTableWidgetItem(str(key))
            value_item = QTableWidgetItem(str(value))
            # Make items non-editable
            key_item.setFlags(key_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            value_item.setFlags(value_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.meta_table.setItem(row, 0, key_item)
            self.meta_table.setItem(row, 1, value_item)
            row += 1

    @pyqtSlot()
    def _on_download_chunk(self) -> None:
        """Download the currently selected chunk to a user-specified location."""
        index = self.chunk_combo.currentIndex()
        if index < 0:
            return

        chunk = self.chunks[index]
        if not self.output_dir:
            return

        # Determine source file (filename from output directory)
        source_md_file = self.output_dir / f"{chunk.chunk_id}.md"

        if not source_md_file.exists():
            QMessageBox.warning(self, "File Not Found", f"Could not find {source_md_file}")
            return

        # Prompt for save location
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Chunk As",
            chunk.chunk_id,
            "Markdown Files (*.md);;All Files (*)",
        )

        if save_path:
            try:
                # Copy source file to save location
                save_path_obj = Path(save_path)
                save_path_obj.write_text(source_md_file.read_text(encoding="utf-8"), encoding="utf-8")
                QMessageBox.information(self, "Success", f"Chunk saved to {save_path}")
                log.info(f"Downloaded {chunk.chunk_id} to {save_path}")
            except Exception as exc:  # noqa: BLE001
                QMessageBox.critical(self, "Error", f"Failed to download: {exc}")
                log.exception("Chunk download error")

    @pyqtSlot()
    def _on_open_folder(self) -> None:
        """Open the output folder in the file explorer."""
        if not self.output_dir:
            return

        import subprocess
        import sys

        try:
            if sys.platform == "win32":
                subprocess.Popen(f'explorer "{self.output_dir}"')
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(self.output_dir)])
            else:
                subprocess.Popen(["xdg-open", str(self.output_dir)])
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Error", f"Failed to open folder: {exc}")
            log.exception("Folder open error")
