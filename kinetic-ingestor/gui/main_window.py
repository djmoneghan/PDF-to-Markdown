# gui/main_window.py
# Main application window for The Kinetic Ingestor GUI.

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QLabel, QPushButton,
    QFileDialog, QMessageBox, QStatusBar, QTextEdit, QSplitter,
)
from PyQt6.QtCore import Qt, pyqtSlot, pyqtSignal, QObject
from PyQt6.QtGui import QIcon, QFont

from ingestor.config import load_config, save_config
from gui.models import ChunkListModel
from gui.widgets.input_tab import InputTab
from gui.widgets.progress_tab import ProgressTab
from gui.widgets.preview_tab import PreviewTab
from gui.widgets.settings_tab import SettingsTab

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom logging handler for GUI
# ---------------------------------------------------------------------------

class GUILogHandler(QObject, logging.Handler):
    """
    Custom logging handler that emits messages to a PyQt signal.
    
    Allows logging to be captured and displayed in the GUI log pane.
    """

    # Signal: (message: str)
    log_emit = pyqtSignal(str)

    def __init__(self):
        """Initialize both parent classes."""
        QObject.__init__(self)
        logging.Handler.__init__(self)

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log record to the GUI."""
        try:
            msg = self.format(record)
            self.log_emit.emit(msg)
        except Exception:  # noqa: BLE001
            self.handleError(record)


class KineticApplicationWindow(QMainWindow):
    """
    Main application window for The Kinetic Ingestor.

    Features: tabbed interface with Input, Progress, Preview, and Settings tabs.
    Shared state: config, current project, chunks list, output directory.
    Log panel at the bottom showing all activity.
    """

    def __init__(self, config_path: Path | str = "config.yaml"):
        """
        Initialize the main window.

        Args:
            config_path: Path to config.yaml (default: ./config.yaml).
        """
        super().__init__()
        self.setWindowTitle("Kinetic Ingestor — PDF to Markdown Converter")
        self.setGeometry(100, 100, 1400, 900)

        # Shared state
        self.config_path = Path(config_path)
        self.config: dict[str, Any] = {}
        self.current_pdf_path: Path | None = None
        self.chunks: list[Any] = []
        self.output_dir: Path | None = None
        self.chunk_model = ChunkListModel()

        # Load initial config
        try:
            self.config = load_config(self.config_path)
        except (FileNotFoundError, ValueError) as exc:
            QMessageBox.critical(
                self,
                "Config Error",
                f"Failed to load config: {exc}\n\nPlease check {self.config_path}.",
            )
            self.config = self._default_config()

        # Build UI and set up logging
        self._setup_ui()
        self._setup_logging()

    def _setup_ui(self) -> None:
        """Initialize the user interface."""
        # Create main splitter: tabs on top, log pane on bottom
        main_splitter = QSplitter(Qt.Orientation.Vertical)

        # Tab widget
        self.tabs = QTabWidget()
        main_splitter.addWidget(self.tabs)

        # Input tab
        self.input_tab = InputTab()
        self.input_tab.conversion_requested.connect(self._on_conversion_requested)
        self.tabs.addTab(self.input_tab, "Input")

        # Progress tab
        self.progress_tab = ProgressTab()
        self.tabs.addTab(self.progress_tab, "Progress")

        # Preview tab
        self.preview_tab = PreviewTab()
        self.tabs.addTab(self.preview_tab, "Preview")

        # Settings tab
        self.settings_tab = SettingsTab(self.config)
        self.settings_tab.settings_changed.connect(self._on_settings_changed)
        self.tabs.addTab(self.settings_tab, "Settings")

        # Log pane (bottom)
        log_panel = QWidget()
        log_layout = QVBoxLayout(log_panel)

        log_label = QLabel("Activity Log")
        log_label_font = QFont()
        log_label_font.setBold(True)
        log_label.setFont(log_label_font)
        log_layout.addWidget(log_label)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(120)
        self.log_text.setMinimumHeight(80)
        self.log_text.setStyleSheet(
            "QTextEdit { background-color: #2b2b2b; color: #d4d4d4; "
            "font-family: monospace; font-size: 9pt; }"
        )
        log_layout.addWidget(self.log_text)
        log_layout.setContentsMargins(0, 5, 0, 0)

        main_splitter.addWidget(log_panel)
        main_splitter.setStretchFactor(0, 3)
        main_splitter.setStretchFactor(1, 1)

        # Set splitter as central widget
        self.setCentralWidget(main_splitter)

        # Status bar
        self.statusBar().showMessage("Ready")

    def _setup_logging(self) -> None:
        """Set up custom logging handler to display in GUI log pane."""
        # Create custom handler
        self.gui_log_handler = GUILogHandler()
        self.gui_log_handler.setLevel(logging.DEBUG)
        self.gui_log_handler.log_emit.connect(self._on_log_message)

        # Format: levelname [logger] message
        formatter = logging.Formatter(
            "%(levelname)s [%(name)s] %(message)s",
            datefmt="%H:%M:%S"
        )
        self.gui_log_handler.setFormatter(formatter)

        # Add to root logger
        root_logger = logging.getLogger()
        root_logger.addHandler(self.gui_log_handler)
        root_logger.setLevel(logging.DEBUG)

        # Log startup
        log.info("Kinetic Ingestor GUI started")

    @pyqtSlot(str)
    def _on_log_message(self, message: str) -> None:
        """Handle log message from handler."""
        self.log_text.append(message)
        # Auto-scroll to bottom
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    @pyqtSlot(dict)
    def _on_settings_changed(self, new_config: dict) -> None:
        """Handle settings change from settings tab."""
        self.config = new_config
        try:
            save_config(self.config, self.config_path)
            self.statusBar().showMessage("Settings saved")
            log.info("Settings saved to config.yaml")
        except Exception as exc:  # noqa: BLE001
            log.error(f"Failed to save config: {exc}")
            self.statusBar().showMessage(f"Error saving settings: {exc}")

    @pyqtSlot(Path, str, bool)
    def _on_conversion_requested(self, pdf_path: Path, project_name: str, force: bool) -> None:
        """Handle conversion request from input tab."""
        self.current_pdf_path = pdf_path
        self.progress_tab.reset()
        self.tabs.setCurrentWidget(self.progress_tab)
        self.statusBar().showMessage(f"Converting {pdf_path.name}…")
        log.info(f"Conversion requested: {pdf_path.name}")

    def _default_config(self) -> dict[str, Any]:
        """Return a minimal default configuration."""
        return {
            "ollama": {
                "endpoint": "http://localhost:11434",
                "model": "gpt-oss:120b",
                "fallback_model": "gpt-oss:120b",
                "api_key": "",
                "timeout_seconds": 120,
            },
            "extraction": {
                "engine": "docling",
                "confidence_threshold": 0.75,
            },
            "chunking": {
                "split_levels": ["#", "##"],
                "min_chunk_tokens": 100,
                "max_chunk_tokens": 1500,
            },
            "output": {
                "processed_root": "processed",
                "manifest_filename": "manifest.json",
                "corrections_filename": "corrections.json",
            },
            "hitl": {
                "auto_accept_above": 0.92,
                "show_raw_markdown": True,
            },
        }

    def closeEvent(self, event) -> None:
        """Handle window close event."""
        # TODO: Save any unsaved configs, shut down workers, etc.
        event.accept()
