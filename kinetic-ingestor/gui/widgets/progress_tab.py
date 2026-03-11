# gui/widgets/progress_tab.py
# Progress tab: real-time conversion status and logging.

from __future__ import annotations

import logging
from typing import Any

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QProgressBar, QTextEdit, QPushButton,
    QHBoxLayout, QGroupBox,
)
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QFont

log = logging.getLogger(__name__)


class ProgressTab(QWidget):
    """
    Progress tab for displaying real-time conversion status.

    Features:
      - Overall progress bar (6 stages)
      - Current stage label
      - Detailed log of all events
      - Stop/cancel button
    """

    def __init__(self):
        """Initialize the progress tab."""
        super().__init__()
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Initialize the user interface."""
        layout = QVBoxLayout(self)

        # Title
        title = QLabel("Conversion Progress")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        # Status group
        status_group = QGroupBox("Status")
        status_layout = QVBoxLayout(status_group)

        self.status_label = QLabel("Waiting to start conversion…")
        status_layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(6)  # 6 stages
        self.progress_bar.setValue(0)
        status_layout.addWidget(self.progress_bar)

        self.detail_label = QLabel("Ready")
        detail_font = QFont()
        detail_font.setPointSize(10)
        self.detail_label.setFont(detail_font)
        status_layout.addWidget(self.detail_label)

        layout.addWidget(status_group)

        # Log group
        log_group = QGroupBox("Log Output")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(300)
        log_layout.addWidget(self.log_text)
        layout.addWidget(log_group)

        # Control buttons
        button_layout = QHBoxLayout()
        self.stop_button = QPushButton("Stop Conversion")
        self.stop_button.setEnabled(False)
        button_layout.addWidget(self.stop_button)
        button_layout.addStretch()
        layout.addLayout(button_layout)

    @pyqtSlot(str, int, int)
    def on_progress(self, stage: str, current: int, total: int) -> None:
        """Update progress display."""
        # Map stage name to progress value
        stage_map = {
            "Loading config": 1,
            "Extracting PDF": 2,
            "Chunking document": 3,
            "Generating metadata": 4,
            "HITL review": 5,
            "Exporting chunks": 6,
        }
        stage_num = stage_map.get(stage, 1)
        self.progress_bar.setValue(stage_num)

        if current == 1:  # Starting a new stage
            self.status_label.setText(f"Running: {stage}")
            self.log(f"→ {stage}")

        self.detail_label.setText(f"{stage} ({current}/{total})")

    @pyqtSlot(str)
    def on_conversion_complete(self, output_dir: str) -> None:
        """Handle conversion completion."""
        self.progress_bar.setValue(6)
        self.status_label.setText("✓ Conversion Complete")
        self.detail_label.setText(f"Output: {output_dir}")
        self.log(f"✓ Conversion complete: {output_dir}")
        self.stop_button.setEnabled(False)

    @pyqtSlot(str)
    def on_conversion_error(self, error_msg: str) -> None:
        """Handle conversion error."""
        self.status_label.setText("✗ Conversion Failed")
        self.detail_label.setText(f"Error: {error_msg}")
        self.log(f"✗ Error: {error_msg}")
        self.stop_button.setEnabled(False)

    def log(self, message: str) -> None:
        """Append a message to the log."""
        self.log_text.append(message)
        # Scroll to bottom
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def reset(self) -> None:
        """Reset the progress display for a new conversion."""
        self.progress_bar.setValue(0)
        self.status_label.setText("Waiting to start conversion…")
        self.detail_label.setText("Ready")
        self.log_text.clear()
        self.stop_button.setEnabled(True)
