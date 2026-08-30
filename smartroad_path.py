"""Put repo root and backend/ on sys.path so imports work from any entry point."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
ARTIFACTS = ROOT / "artifacts"
MODELS_DIR = ARTIFACTS / "models"
GIS_DATA = ROOT / "data" / "gis_states"


def setup_paths() -> Path:
    for p in (ROOT, BACKEND):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return ROOT


setup_paths()
