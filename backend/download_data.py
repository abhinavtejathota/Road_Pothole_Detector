#!/usr/bin/env python3
"""
Deprecated entrypoint — Roboflow downloads now live under tools/ml/.

Prefer:
  python tools/ml/download_data.py
  python tools/ml/setup_train.py

Edit dataset links in: tools/ml/datasets_config.py
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

print(
    "[note] download_data.py moved into tools/ml/ — "
    "forwarding to tools/ml/download_data.py\n"
)
_ROOT = Path(__file__).resolve().parent.parent
_ML = _ROOT / "tools" / "ml" / "download_data.py"
sys.argv[0] = str(_ML)
runpy.run_path(str(_ML), run_name="__main__")
