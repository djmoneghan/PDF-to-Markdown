# ingestor/config.py
# Configuration loading and validation for The Kinetic Ingestor.

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Config schema validation
# ---------------------------------------------------------------------------

_REQUIRED_KEYS: list[tuple[str, ...]] = [
    ("ollama", "endpoint"),
    ("ollama", "model"),
    ("ollama", "fallback_model"),
    ("ollama", "api_key"),
    ("ollama", "timeout_seconds"),
    ("extraction", "engine"),
    ("extraction", "confidence_threshold"),
    ("chunking", "split_levels"),
    ("chunking", "min_chunk_tokens"),
    ("chunking", "max_chunk_tokens"),
    ("output", "processed_root"),
    ("output", "manifest_filename"),
    ("output", "corrections_filename"),
    ("hitl", "auto_accept_above"),
    ("hitl", "show_raw_markdown"),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_config(config_path: Path | str = "config.yaml") -> dict[str, Any]:
    """
    Read and validate config.yaml.

    Args:
        config_path: Path to config.yaml file (default: ./config.yaml).

    Returns:
        Validated config dict.

    Raises:
        FileNotFoundError: if the config file does not exist.
        ValueError: if any required key is missing or the file is not valid YAML.
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path.resolve()}")

    with config_path.open("r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)

    if not isinstance(config, dict):
        raise ValueError(f"Config file {config_path} is empty or not a YAML mapping.")

    for key_path in _REQUIRED_KEYS:
        node = config
        for part in key_path:
            if not isinstance(node, dict) or part not in node:
                dotted = ".".join(key_path)
                raise ValueError(
                    f"Missing required config key '{dotted}' in {config_path}."
                )
            node = node[part]

    return config


def save_config(config: dict[str, Any], config_path: Path | str = "config.yaml") -> None:
    """
    Write config dict back to config.yaml with validation.

    Args:
        config: Configuration dict to write.
        config_path: Path to config.yaml file (default: ./config.yaml).

    Raises:
        ValueError: if required keys are missing from the config dict.
        IOError: if the file cannot be written.
    """
    config_path = Path(config_path)

    # Validate before writing
    for key_path in _REQUIRED_KEYS:
        node = config
        for part in key_path:
            if not isinstance(node, dict) or part not in node:
                dotted = ".".join(key_path)
                raise ValueError(
                    f"Cannot save config: missing required key '{dotted}'."
                )
            node = node[part]

    # Write with YAML formatting
    with config_path.open("w", encoding="utf-8") as fh:
        yaml.dump(config, fh, default_flow_style=False, sort_keys=False)
