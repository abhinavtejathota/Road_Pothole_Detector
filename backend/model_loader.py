# YOLOv12 model loader for road-defect severity detection

import os
from pathlib import Path
from typing import Any

# Repo root (this file lives in backend/).
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL_PATH = ROOT / "artifacts" / "models" / "smartroad_ap.pt"
_LEGACY_MODEL_DIR = ROOT / "artifacts/models"

# Do NOT import ultralytics/torch at module import time.
# Gunicorn --preload + fork leaves CUDA half-initialized in workers:
#   "Cannot re-initialize CUDA in forked subprocess"


def _normalize_model_path(raw: str | os.PathLike | None) -> Path:
    """Resolve MODEL_PATH on Windows and Linux (env often uses backslashes)."""
    if raw is None or str(raw).strip() == "":
        return DEFAULT_MODEL_PATH

    text = str(raw).strip().strip('"').strip("'")
    # Windows-style separators in .env break on Linux (literal backslash in filename)
    text = text.replace("\\", "/")

    resolved = Path(text)
    if not resolved.is_absolute():
        resolved = (ROOT / resolved).resolve()
    else:
        resolved = resolved.resolve()

    if resolved.is_file():
        return resolved

    # Fallbacks when env points at a missing / wrongly-separated path
    for candidate in (
        DEFAULT_MODEL_PATH,
        _LEGACY_MODEL_DIR / "smartroad_ap.pt",
        ROOT / "artifacts" / "models" / Path(text).name,
        _LEGACY_MODEL_DIR / Path(text).name,
        ROOT / Path(text).name,
    ):
        if candidate.is_file():
            return candidate.resolve()

    return resolved


def resolve_yolo_device() -> str:
    """Device string for Ultralytics predict/to().

    ``YOLO_DEVICE`` / ``POTHOLE_DEVICE``:
      auto | cpu | 0 | cuda:0 | …
    ``auto`` → first CUDA GPU if available, else cpu.
    """
    raw = (os.getenv("YOLO_DEVICE") or os.getenv("POTHOLE_DEVICE") or "auto").strip().lower()
    if not raw or raw == "auto":
        try:
            import torch
            if torch.cuda.is_available():
                return "0"
        except Exception:
            pass
        return "cpu"
    if raw in ("cuda", "gpu"):
        return "0"
    return raw


def load_model(model_path: str | os.PathLike | None = None) -> Any:
    """Load the fine-tuned YOLOv12 model.

    Resolution order:
      1. Explicit `model_path` argument
      2. MODEL_PATH environment variable
      3. <repo>/artifacts/models/smartroad_ap.pt

    Device is taken from YOLO_DEVICE / POTHOLE_DEVICE (see resolve_yolo_device).
    """
    from ultralytics import YOLO  # lazy — avoid CUDA init before gunicorn fork

    resolved = _normalize_model_path(model_path if model_path is not None else os.getenv("MODEL_PATH"))

    if not resolved.is_file():
        raise FileNotFoundError(
            f"Model file not found at: {resolved} "
            f"(also tried {DEFAULT_MODEL_PATH}). "
            f"Set MODEL_PATH with forward slashes, e.g. artifacts/models/smartroad_ap.pt"
        )

    model = YOLO(str(resolved))
    try:
        from model_testing.yolo12_aattn_compat import patch_yolo_aattn

        patch_yolo_aattn(model)
    except Exception:
        pass
    device = resolve_yolo_device()
    try:
        model.to("cpu" if device == "cpu" else device)
    except Exception:
        # Ultralytics still accepts device= on predict even if .to() fails.
        pass
    # Stash for _infer_on_frame (plain attribute — YOLO wrappers tolerate this).
    try:
        model._smartroad_device = device  # type: ignore[attr-defined]
    except Exception:
        pass
    # Throughput knobs for AceCloud A6000 (safe no-ops on CPU / missing torch).
    if device != "cpu":
        try:
            import torch

            torch.backends.cudnn.benchmark = True
            if hasattr(torch, "set_float32_matmul_precision"):
                torch.set_float32_matmul_precision("high")
        except Exception:
            pass
    return model
