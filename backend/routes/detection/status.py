"""Pothole detection API — business logic migrated from app.py (Gradio)."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from s3_utils import (
    delete_object,
    find_sibling_gps_key,
    find_sibling_json_key,
    get_input_bucket,
    get_processed_bucket,
    head_exists,
    is_s3_configured,
    list_media_keys,
    presign_input_url,
    read_text_object,
)
# pothole_detector / YOLO imported lazily inside run_detection — importing at
# module load pulls ultralytics→torch, and with gunicorn --preload that
# initializes CUDA in the master before fork (broken GPU in workers).

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # routes/detection/status.py → repo root
OUTPUTS_ROOT = PROJECT_ROOT / "outputs"
ALLOWED_SERVE_ROOTS = (OUTPUTS_ROOT.resolve(),)
_DETECT_LOCK = PROJECT_ROOT / "data" / ".detection.lock"


def _is_video(path: str) -> bool:
    return str(path).lower().endswith((".mp4", ".avi", ".mov", ".mkv"))


def _acquire_detection_lock(timeout_s: float = 5.0) -> Optional[Any]:
    """One heavy YOLO job at a time so the GPU/host stays responsive for API/uploads."""
    _DETECT_LOCK.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:
        return None  # Windows — no flock; rely on gunicorn concurrency
    fh = open(_DETECT_LOCK, "a+", encoding="utf-8")
    deadline = time.time() + timeout_s
    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fh.seek(0)
            fh.truncate()
            fh.write(f"pid={os.getpid()} ts={time.time():.0f}\n")
            fh.flush()
            return fh
        except BlockingIOError:
            if time.time() >= deadline:
                fh.close()
                return False  # type: ignore[return-value]
            time.sleep(0.25)


def _release_detection_lock(fh: Any) -> None:
    if not fh:
        return
    try:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        fh.close()
    except Exception:
        pass


def _status(kind: str, message: str) -> dict:
    return {"kind": kind, "message": message}


def _s3_status() -> dict:
    if is_s3_configured():
        return _status(
            "ok",
            f"S3 connected. Source: {get_input_bucket()} → Processed: {get_processed_bucket()}",
        )
    return _status(
        "warn",
        "S3 not configured — set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in .env. Local upload still works.",
    )


def _db_status() -> dict:
    try:
        from db_utils import is_db_configured

        if is_db_configured():
            return _status("ok", "PostgreSQL connected — detections are being saved to DB.")
    except Exception:
        pass
    return _status(
        "warn",
        "DB not configured — set DB_NAME / DB_USER / DB_PASSWORD in .env to enable dashboard.",
    )


def get_banners() -> dict:
    return {
        "s3": _s3_status(),
        "db": _db_status(),
        "input_bucket": get_input_bucket() if is_s3_configured() else None,
    }


def list_s3_media() -> dict:
    catalog = list_s3_catalog()
    keys = catalog.get("keys") or []
    if not keys:
        return {"keys": [], "catalog": catalog, "status": catalog.get("status") or _status("error", "No S3 media.")}
    return {
        "keys": keys,
        "catalog": catalog,
        "status": _status("ok", f"Found {len(keys)} file(s) in s3://{get_input_bucket()}/"),
    }


_SKIP_TOP = frozenset({"legacy", "_unscoped", "_anonymous", "field_uploads"})
_CITIZEN_STAMP_RE = re.compile(r"^(\d{8})_(\d{4,6})$")
_CITIZEN_LEGACY_FOLDER_RE = re.compile(r"^(\d{10,12})_(\d{8})_(\d{4,6})$")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")
_VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".avi")


def _folder_display_label(folder: str) -> str:
    """Human label for route/citizen folder."""
    raw = (folder or "").strip()
    if not raw or raw in ("_unsorted", "route"):
        return raw or "Route"
    m = _CITIZEN_STAMP_RE.match(raw)
    if m:
        t = m.group(2)
        return f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:8]} {t[:2]}:{t[2:4]}"
    m2 = _CITIZEN_LEGACY_FOLDER_RE.match(raw)
    if m2:
        t = m2.group(3)
        return f"{m2.group(1)} · {m2.group(2)} {t[:2]}:{t[2:4]}"
    body, _, stamp = raw.rpartition("-")
    if stamp and len(stamp) == 11 and stamp[8] == "-":
        parts = raw.split("-")
        if len(parts) >= 3:
            stamp = f"{parts[-2]}-{parts[-1]}"
            body = "-".join(parts[:-2])
    label = body.replace("_", " → ").replace("-", " · ")
    return f"{label} ({stamp})" if stamp else label


