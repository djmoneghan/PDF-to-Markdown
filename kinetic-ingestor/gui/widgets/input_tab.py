# gui/widgets/input_tab.py
# Input tab: PDF drag-drop zone and folder watcher.

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QFileDialog,
    QGroupBox, QCheckBox,
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QFont

from gui.threads import FileWatcherWorker

log = logging.getLogger(__name__)


class PDFDropZone(QLabel):
    """
    Custom label widget that accepts PDF drag-and-drop.

    Emits pdf_dropped signal when a PDF is dropped.
    """

    pdf_dropped = pyqtSignal(Path)  # (pdf_path)

    def __init__(self):
        """Initialize the drop zone."""
        super().__init__()
        self.setText("Drag PDF here\nor click to browse")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            "QLabel { border: 2px dashed #cccccc; "
            "border-radius: 8px; padding: 40px; "
            "background-color: #f9f9f9; }"
        )
        self.setMinimumHeight(150)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setAcceptDrops(True)

        # Allow clicking to open file browser
        self._setup_click_handler()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """Handle drag enter — highlight if it's a PDF."""
        mime_data = event.mimeData()
        if mime_data.hasUrls():
            urls = mime_data.urls()
            if any(url.path().lower().endswith(".pdf") for url in urls):
                self.setStyleSheet(
                    "QLabel { border: 2px dashed #0078d4; "
                    "border-radius: 8px; padding: 40px; "
                    "background-color: #e3f2fd; }"
                )
                event.acceptProposedAction()
                return

        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        """Handle drag leave — restore style."""
        self.setStyleSheet(
            "QLabel { border: 2px dashed #cccccc; "
            "border-radius: 8px; padding: 40px; "
            "background-color: #f9f9f9; }"
        )

    def dropEvent(self, event: QDropEvent) -> None:
        """Handle drop — check for PDF and emit signal."""
        mime_data = event.mimeData()
        if mime_data.hasUrls():
            for url in mime_data.urls():
                path = Path(url.path())
                if path.suffix.lower() == ".pdf" and path.is_file():
                    log.debug(f"PDF dropped: {path}")
                    self.pdf_dropped.emit(path)
                    event.acceptProposedAction()
                    return

        # Restore style
        self.setStyleSheet(
            "QLabel { border: 2px dashed #cccccc; "
            "border-radius: 8px; padding: 40px; "
            "background-color: #f9f9f9; }"
        )
        event.ignore()

    def _setup_click_handler(self) -> None:
        """Allow clicking to open file browser (implemented via tab interaction)."""
        # This is handled by the parent InputTab


class InputTab(QWidget):
    """
    Input tab for PDF selection and folder monitoring.

    Features:
      - Drag-and-drop zone for PDF files
      - File browser button
      - Folder watcher for auto-detection
      - Project name override
      - Conversion start button
    """

    # Signals
    conversion_requested = pyqtSignal(Path, str, bool)  # (pdf_path, project_name, force)
    folder_watcher_started = pyqtSignal(Path)  # (watch_dir)

    def __init__(self):
        """Initialize the input tab."""
        super().__init__()
        self.selected_pdf: Path | None = None
        self.file_watcher_worker: FileWatcherWorker | None = None

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Initialize the user interface."""
        layout = QVBoxLayout(self)

        # Title
        title = QLabel("Step 1: Upload PDF")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        # Drop zone
        drop_group = QGroupBox("PDF Selection")
        drop_layout = QVBoxLayout(drop_group)
        self.drop_zone = PDFDropZone()
        self.drop_zone.pdf_dropped.connect(self._on_pdf_dropped)
        drop_layout.addWidget(self.drop_zone)

        browse_button = QPushButton("Browse for PDF")
        browse_button.clicked.connect(self._on_browse_clicked)
        drop_layout.addWidget(browse_button)

        layout.addWidget(drop_group)

        # File info group
        info_group = QGroupBox("Selected File")
        info_layout = QVBoxLayout(info_group)
        self.selected_file_label = QLabel("No file selected")
        info_layout.addWidget(self.selected_file_label)
        layout.addWidget(info_group)

        # Project name override
        project_group = QGroupBox("Project Configuration")
        project_layout = QVBoxLayout(project_group)

        project_name_layout = QHBoxLayout()
        project_name_layout.addWidget(QLabel("Project Name (optional):"))
        self.project_name_edit = QLineEdit()
        self.project_name_edit.setPlaceholderText(
            "Leave empty to use PDF filename"
        )
        project_name_layout.addWidget(self.project_name_edit)
        project_layout.addLayout(project_name_layout)

        self.force_overwrite_check = QCheckBox("Overwrite existing output files")
        project_layout.addWidget(self.force_overwrite_check)

        layout.addWidget(project_group)

        # Folder watcher group
        watcher_group = QGroupBox("Folder Monitoring")
        watcher_layout = QVBoxLayout(watcher_group)

        watcher_label = QLabel("Watch a folder for new PDFs:")
        watcher_layout.addWidget(watcher_label)

        watcher_dir_layout = QHBoxLayout()
        self.watch_dir_edit = QLineEdit()
        self.watch_dir_edit.setPlaceholderText("Path to watch")
        watcher_dir_layout.addWidget(self.watch_dir_edit)
        browse_watch_button = QPushButton("Browse")
        browse_watch_button.clicked.connect(self._on_browse_watch_dir)
        watcher_dir_layout.addWidget(browse_watch_button)
        watcher_layout.addLayout(watcher_dir_layout)

        self.start_watcher_button = QPushButton("Start Watching")
        self.start_watcher_button.clicked.connect(self._on_start_watcher)
        watcher_layout.addWidget(self.start_watcher_button)

        layout.addWidget(watcher_group)

        # Spacer
        layout.addStretch()

        # Conversion button
        self.convert_button = QPushButton("Start Conversion")
        self.convert_button.setMinimumHeight(50)
        self.convert_button.setEnabled(False)
        self.convert_button.clicked.connect(self._on_convert_clicked)
        convert_font = QFont()
        convert_font.setPointSize(12)
        convert_font.setBold(True)
        self.convert_button.setFont(convert_font)
        layout.addWidget(self.convert_button)

    def _on_pdf_dropped(self, pdf_path: Path) -> None:
        """Handle PDF dropped onto the drop zone."""
        self._set_selected_pdf(pdf_path)

    def _on_browse_clicked(self) -> None:
        """Handle Browse button clicked."""
        pdf_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select PDF File",
            "",
            "PDF Files (*.pdf);;All Files (*)",
        )
        if pdf_path:
            self._set_selected_pdf(Path(pdf_path))

    def _on_browse_watch_dir(self) -> None:
        """Handle Browse button for watch directory."""
        dir_path = QFileDialog.getExistingDirectory(
            self,
            "Select Folder to Watch",
            "",
        )
        if dir_path:
            self.watch_dir_edit.setText(dir_path)

    def _set_selected_pdf(self, pdf_path: Path) -> None:
        """Update the selected PDF and enable conversion."""
        self.selected_pdf = pdf_path
        self.selected_file_label.setText(f"Selected: {pdf_path.name}")
        self.convert_button.setEnabled(True)

    def _on_convert_clicked(self) -> None:
        """Handle Start Conversion button."""
        if not self.selected_pdf:
            return

        project_name = self.project_name_edit.text().strip() or ""
        force = self.force_overwrite_check.isChecked()

        self.conversion_requested.emit(self.selected_pdf, project_name, force)

    def _on_start_watcher(self) -> None:
        """Handle Start Watching button."""
        watch_dir = self.watch_dir_edit.text().strip()
        if not watch_dir:
            return

        watch_path = Path(watch_dir)
        if not watch_path.is_dir():
            watch_path.mkdir(parents=True, exist_ok=True)

        # Start the file watcher
        if self.file_watcher_worker:
            self.file_watcher_worker.stop()

        self.file_watcher_worker = FileWatcherWorker(watch_path, poll_interval_ms=1000)
        self.file_watcher_worker.pdf_detected.connect(self._on_watched_pdf_detected)
        self.file_watcher_worker.start()

        self.start_watcher_button.setText("Watcher Active")
        self.start_watcher_button.setEnabled(False)
        self.folder_watcher_started.emit(watch_path)

    def _on_watched_pdf_detected(self, pdf_path: Path) -> None:
        """Handle PDF detected by the folder watcher."""
        log.info(f"Watcher detected PDF: {pdf_path}")
        self._set_selected_pdf(pdf_path)
        # Auto-start conversion when PDF is detected
        self._on_convert_clicked()

    def stop_watcher(self) -> None:
        """Stop the file watcher (called on app close)."""
        if self.file_watcher_worker:
            self.file_watcher_worker.stop()
