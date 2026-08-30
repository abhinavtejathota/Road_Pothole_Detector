"""Fix missing imports after modularization split."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

COMMON_DB = """from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
import json
import math
import os
import time
"""


def patch_db() -> None:
    for p in sorted((ROOT / "db").glob("*.py")):
        if p.name == "__init__.py":
            continue
        text = p.read_text(encoding="utf-8")
        if "from __future__ import annotations" in text:
            continue
        p.write_text(COMMON_DB + "\n" + text, encoding="utf-8")
        print("db patched", p.name)


def patch_detector() -> None:
    track = ROOT / "detector/track.py"
    t = track.read_text(encoding="utf-8")
    if "from detector.types import DetectionRow" not in t:
        lines = t.splitlines(keepends=True)
        idx = 0
        for i, l in enumerate(lines):
            if l.startswith("import ") or l.startswith("from "):
                idx = i + 1
        lines.insert(idx, "from detector.types import DetectionRow\n")
        track.write_text("".join(lines), encoding="utf-8")
        print("detector track patched")

    # parallel/pipeline may need Track
    for rel in ("detector/parallel.py", "detector/pipeline.py", "detector/types.py", "detector/video_io.py"):
        p = ROOT / rel
        t = p.read_text(encoding="utf-8")
        need = []
        if "Track" in t and "from detector.track import" not in t and rel != "detector/track.py":
            if "class Track" not in t:
                need.append("from detector.track import Track\n")
        if need:
            lines = t.splitlines(keepends=True)
            idx = 0
            for i, l in enumerate(lines):
                if l.startswith("import ") or l.startswith("from "):
                    idx = i + 1
            for n in need:
                if n not in t:
                    lines.insert(idx, n)
                    idx += 1
            p.write_text("".join(lines), encoding="utf-8")
            print("patched", rel)


def try_import(label: str, code: str) -> None:
    import runpy
    import sys
    import traceback

    print("===", label)
    try:
        ns = {}
        exec(code, ns, ns)
        print("OK", ns.get("msg", ""))
    except Exception as e:
        print("FAIL", type(e).__name__, e)
        traceback.print_exc()


if __name__ == "__main__":
    patch_db()
    patch_detector()
    try_import("db_utils", "import db_utils; msg=str(hasattr(db_utils,'is_db_configured'))")
    try_import("detector", "import pothole_detector as p; msg=str(hasattr(p,'pothole_detector'))")
    try_import("api", "from routes.api import api_bp; msg=api_bp.name")
