#!/usr/bin/env python3
# gui_main.py
# Entry point for The Kinetic Ingestor GUI application.

from __future__ import annotations

import sys

if __name__ == "__main__":
    # Add the workspace to sys.path to allow imports
    from pathlib import Path
    workspace_root = Path(__file__).parent / "kinetic-ingestor"
    sys.path.insert(0, str(workspace_root))

    from gui.app import main
    sys.exit(main(sys.argv[1:]))
