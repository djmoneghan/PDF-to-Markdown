# gui/widgets/settings_tab.py
# Settings tab: configure Ollama, extraction, chunking, and HITL parameters.

from __future__ import annotations

import logging
from typing import Any

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QSpinBox, QDoubleSpinBox,
    QComboBox, QCheckBox, QPushButton, QGroupBox, QScrollArea, QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont

log = logging.getLogger(__name__)


class SettingsTab(QWidget):
    """
    Settings tab for configuring the pipeline.

    Features:
      - Ollama endpoint, model, timeout config
      - Extraction method and confidence threshold
      - Chunking parameters (split levels, min/max tokens)
      - HITL thresholds
    """

    # Signal: emitted when settings are saved
    settings_changed = pyqtSignal(dict)  # (new_config)

    def __init__(self, initial_config: dict[str, Any]):
        """
        Initialize the settings tab.

        Args:
            initial_config: Initial configuration dict.
        """
        super().__init__()
        self.config = initial_config.copy()
        self._setup_ui()
        self._load_config_values()

    def _setup_ui(self) -> None:
        """Initialize the user interface."""
        layout = QVBoxLayout(self)

        # Title
        title = QLabel("Settings")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        # Scrollable content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)

        # Ollama section
        scroll_layout.addWidget(self._create_ollama_group())

        # Extraction section
        scroll_layout.addWidget(self._create_extraction_group())

        # Chunking section
        scroll_layout.addWidget(self._create_chunking_group())

        # HITL section
        scroll_layout.addWidget(self._create_hitl_group())

        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)

        # Button layout
        button_layout = QHBoxLayout()
        self.reset_button = QPushButton("Reset to Defaults")
        self.reset_button.clicked.connect(self._on_reset)
        button_layout.addWidget(self.reset_button)

        self.save_button = QPushButton("Save Settings")
        self.save_button.clicked.connect(self._on_save)
        button_layout.addWidget(self.save_button)

        layout.addLayout(button_layout)

    def _create_ollama_group(self) -> QGroupBox:
        """Create the Ollama configuration group."""
        group = QGroupBox("Ollama Configuration")
        layout = QVBoxLayout(group)

        # Endpoint
        endpoint_layout = QHBoxLayout()
        endpoint_layout.addWidget(QLabel("Endpoint:"))
        self.endpoint_edit = QLineEdit()
        self.endpoint_edit.setPlaceholderText("http://localhost:11434")
        endpoint_layout.addWidget(self.endpoint_edit)
        layout.addLayout(endpoint_layout)

        # Model
        model_layout = QHBoxLayout()
        model_layout.addWidget(QLabel("Model:"))
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.addItems([
            "gpt-oss:120b",
            "gpt-oss:20b",
            "qwen3-30b-a3b",
            "qwen3-14b",
        ])
        model_layout.addWidget(self.model_combo)
        layout.addLayout(model_layout)

        # Fallback model
        fallback_layout = QHBoxLayout()
        fallback_layout.addWidget(QLabel("Fallback Model:"))
        self.fallback_model_edit = QLineEdit()
        self.fallback_model_edit.setPlaceholderText("gpt-oss:120b")
        fallback_layout.addWidget(self.fallback_model_edit)
        layout.addLayout(fallback_layout)

        # API Key
        api_key_layout = QHBoxLayout()
        api_key_layout.addWidget(QLabel("API Key (optional):"))
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setPlaceholderText("Leave blank if not required")
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        api_key_layout.addWidget(self.api_key_edit)
        layout.addLayout(api_key_layout)

        # Timeout
        timeout_layout = QHBoxLayout()
        timeout_layout.addWidget(QLabel("Timeout (seconds):"))
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setMinimum(10)
        self.timeout_spin.setMaximum(600)
        timeout_layout.addWidget(self.timeout_spin)
        layout.addLayout(timeout_layout)

        return group

    def _create_extraction_group(self) -> QGroupBox:
        """Create the extraction configuration group."""
        group = QGroupBox("Extraction")
        layout = QVBoxLayout(group)

        # Engine
        engine_layout = QHBoxLayout()
        engine_layout.addWidget(QLabel("Engine:"))
        self.engine_combo = QComboBox()
        self.engine_combo.addItems(["docling", "pymupdf"])
        engine_layout.addWidget(self.engine_combo)
        layout.addLayout(engine_layout)

        # Confidence threshold
        conf_layout = QHBoxLayout()
        conf_layout.addWidget(QLabel("Confidence Threshold (0.0–1.0):"))
        self.confidence_spin = QDoubleSpinBox()
        self.confidence_spin.setMinimum(0.0)
        self.confidence_spin.setMaximum(1.0)
        self.confidence_spin.setSingleStep(0.05)
        conf_layout.addWidget(self.confidence_spin)
        layout.addLayout(conf_layout)

        return group

    def _create_chunking_group(self) -> QGroupBox:
        """Create the chunking configuration group."""
        group = QGroupBox("Chunking")
        layout = QVBoxLayout(group)

        # Min tokens
        min_layout = QHBoxLayout()
        min_layout.addWidget(QLabel("Min Chunk Tokens:"))
        self.min_tokens_spin = QSpinBox()
        self.min_tokens_spin.setMinimum(10)
        self.min_tokens_spin.setMaximum(5000)
        min_layout.addWidget(self.min_tokens_spin)
        layout.addLayout(min_layout)

        # Max tokens
        max_layout = QHBoxLayout()
        max_layout.addWidget(QLabel("Max Chunk Tokens:"))
        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setMinimum(10)
        self.max_tokens_spin.setMaximum(10000)
        max_layout.addWidget(self.max_tokens_spin)
        layout.addLayout(max_layout)

        return group

    def _create_hitl_group(self) -> QGroupBox:
        """Create the HITL configuration group."""
        group = QGroupBox("Human-In-The-Loop (HITL)")
        layout = QVBoxLayout(group)

        # Auto-accept threshold
        auto_accept_layout = QHBoxLayout()
        auto_accept_layout.addWidget(QLabel("Auto-Accept Confidence:"))
        self.auto_accept_spin = QDoubleSpinBox()
        self.auto_accept_spin.setMinimum(0.0)
        self.auto_accept_spin.setMaximum(1.0)
        self.auto_accept_spin.setSingleStep(0.05)
        auto_accept_layout.addWidget(self.auto_accept_spin)
        layout.addLayout(auto_accept_layout)

        # Show raw markdown
        self.show_raw_check = QCheckBox("Show raw Markdown during review")
        layout.addWidget(self.show_raw_check)

        return group

    def _load_config_values(self) -> None:
        """Load current config values into UI controls."""
        # Ollama
        self.endpoint_edit.setText(self.config.get("ollama", {}).get("endpoint", ""))
        self.model_combo.setCurrentText(self.config.get("ollama", {}).get("model", ""))
        self.fallback_model_edit.setText(
            self.config.get("ollama", {}).get("fallback_model", "")
        )
        self.api_key_edit.setText(self.config.get("ollama", {}).get("api_key", ""))
        self.timeout_spin.setValue(
            self.config.get("ollama", {}).get("timeout_seconds", 120)
        )

        # Extraction
        self.engine_combo.setCurrentText(
            self.config.get("extraction", {}).get("engine", "docling")
        )
        self.confidence_spin.setValue(
            self.config.get("extraction", {}).get("confidence_threshold", 0.75)
        )

        # Chunking
        self.min_tokens_spin.setValue(
            self.config.get("chunking", {}).get("min_chunk_tokens", 100)
        )
        self.max_tokens_spin.setValue(
            self.config.get("chunking", {}).get("max_chunk_tokens", 1500)
        )

        # HITL
        self.auto_accept_spin.setValue(
            self.config.get("hitl", {}).get("auto_accept_above", 0.92)
        )
        self.show_raw_check.setChecked(
            self.config.get("hitl", {}).get("show_raw_markdown", True)
        )

    def _get_config_from_ui(self) -> dict[str, Any]:
        """Extract current config values from UI controls."""
        config = self.config.copy()

        # Ollama
        config["ollama"]["endpoint"] = self.endpoint_edit.text()
        config["ollama"]["model"] = self.model_combo.currentText()
        config["ollama"]["fallback_model"] = self.fallback_model_edit.text()
        config["ollama"]["api_key"] = self.api_key_edit.text()
        config["ollama"]["timeout_seconds"] = self.timeout_spin.value()

        # Extraction
        config["extraction"]["engine"] = self.engine_combo.currentText()
        config["extraction"]["confidence_threshold"] = self.confidence_spin.value()

        # Chunking
        config["chunking"]["min_chunk_tokens"] = self.min_tokens_spin.value()
        config["chunking"]["max_chunk_tokens"] = self.max_tokens_spin.value()

        # HITL
        config["hitl"]["auto_accept_above"] = self.auto_accept_spin.value()
        config["hitl"]["show_raw_markdown"] = self.show_raw_check.isChecked()

        return config

    def _on_save(self) -> None:
        """Handle Save Settings button."""
        try:
            new_config = self._get_config_from_ui()
            self.config = new_config
            self.settings_changed.emit(new_config)
            QMessageBox.information(self, "Success", "Settings saved successfully.")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Error", f"Failed to save settings: {exc}")
            log.exception("Settings save error")

    def _on_reset(self) -> None:
        """Handle Reset to Defaults button."""
        reply = QMessageBox.question(
            self,
            "Confirm Reset",
            "Reset all settings to defaults?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.config = self._default_config()
            self._load_config_values()

    @staticmethod
    def _default_config() -> dict[str, Any]:
        """Return default configuration."""
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
