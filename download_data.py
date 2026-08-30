#!/usr/bin/env python3
"""Shim — forwards to tools/ml/download_data.py (legacy root entrypoint)."""
from __future__ import annotations

import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().parent / "tools" / "ml" / "download_data.py"), run_name="__main__")
