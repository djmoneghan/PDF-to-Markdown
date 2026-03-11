# gui/app.py
# PyQt6 application for The Kinetic Ingestor.

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from gui.main_window import KineticApplicationWindow

log = logging.getLogger(__name__)


def setup_logging(debug: bool = False) -> None:
    """Configure logging for the application."""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
    )


class KineticApp(QApplication):
    """
    Main PyQt6 application for The Kinetic Ingestor.

    Initializes the main window and handles application-level signals.
    """

    def __init__(self, argv: list[str] | None = None, debug: bool = False):
        """
        Initialize the application.

        Args:
            argv: Command-line arguments (passed to QApplication).
            debug: If True, enable debug logging.
        """
        super().__init__(argv or [])
        self.setApplicationName("Kinetic Ingestor")
        self.setApplicationVersion("0.1.0")

        setup_logging(debug)

        # Create main window
        config_path = Path(__file__).parent.parent / "config.yaml"
        self.main_window = KineticApplicationWindow(config_path=config_path)
        self.main_window.show()


def main(argv: list[str] | None = None, debug: bool = False) -> int:
    """
    Entry point for the GUI application.

    Args:
        argv: Command-line arguments.
        debug: If True, enable debug logging.

    Returns:
        Application exit code.
    """
    app = KineticApp(argv, debug=debug)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
