#!/usr/bin/env python3
"""Optional alternate entry — prefer ``python web_app.py`` / run_smartroad.sh.

Kept for SMARTROAD_SERVER=waitress launches that need an explicit serve() wrap.
Defaults match web_app.py (__main__).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Defer to web_app.__main__ so behaviour never diverges from nohup python web_app.py
if __name__ == "__main__":
    os.chdir(ROOT)
    # Ensure web_app is run as __main__
    import runpy
    runpy.run_path(str(ROOT / "web_app.py"), run_name="__main__")
