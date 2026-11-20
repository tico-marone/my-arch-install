#!/usr/bin/env python3
"""Convenience launcher so `python run.py` works directly from a git clone."""

from __future__ import annotations

from pathlib import Path
import sys

SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from my_arch_install.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
