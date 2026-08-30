"""Import helpers for the detector package.

``from module import *`` intentionally skips names starting with ``_``.
The split detector modules still share many private helpers (``_open_video_capture``,
``_infer_on_frame``, …), so star-imports silently break video detection.

Use ``reexport(module)`` instead — it pulls public *and* single-underscore names.
"""
from __future__ import annotations

from types import ModuleType


def reexport(mod: ModuleType, dest: dict) -> None:
    """Copy names from ``mod`` into ``dest`` (typically ``globals()``), including ``_foo``."""
    for key, value in vars(mod).items():
        if key.startswith("__"):
            continue
        dest[key] = value
