"""Disk queue for post-upload pothole detection (sequential).

Flask / finalize worker enqueues a job after a successful field upload.
``smartroad_worker`` (or any consumer of this queue) runs YOLO one job at a
time so uploads stay responsive and detections never pile up on the GPU.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
QUEUE_ROOT = Path(os.getenv("DETECT_QUEUE_DIR", str(_ROOT / "data" / "detect_queue")))
PENDING = QUEUE_ROOT / "pending"
RUNNING = QUEUE_ROOT / "running"
DONE = QUEUE_ROOT / "done"
FAILED = QUEUE_ROOT / "failed"

_ENQUEUE_LOCK = threading.Lock()


def _is_production_env() -> bool:
    return (os.getenv("SMARTROAD_ENV") or "").strip().lower() in ("production", "prod")


def _env_bool(name: str, *, default_when_unset: bool) -> bool:
    """Parse env flag; when unset, use *default_when_unset* (not always True)."""
    raw = os.getenv(name)
    if raw is not None and str(raw).strip() != "":
        return raw.lower() in ("1", "true", "yes")
    return default_when_unset


def auto_detect_enabled() -> bool:
    """Queue detection after upload finalize.

    Default off for local dev; on when ``SMARTROAD_ENV=production`` unless
    ``AUTO_DETECT_ON_UPLOAD`` is set explicitly.
    """
    return _env_bool(
        "AUTO_DETECT_ON_UPLOAD",
        default_when_unset=_is_production_env(),
    )


def ensure_queue_dirs() -> None:
    for d in (PENDING, RUNNING, DONE, FAILED):
        d.mkdir(parents=True, exist_ok=True)


def enqueue_detect_job(
    *,
    storage: str,
    media_key: str,
    gps_path: str | None = None,
    capture_mode: str = "vehicle",
    user_id: int | None = None,
    username: str | None = None,
    media_title: str | None = None,
) -> dict[str, Any] | None:
    """Queue one detection. ``media_key`` is an S3 key or local relative path."""
    if not auto_detect_enabled():
        return None
    media_key = (media_key or "").strip()
    if not media_key:
        return None
    ensure_queue_dirs()
    job_id = f"{int(time.time())}_{uuid.uuid4().hex[:10]}"
    job = {
        "job_id": job_id,
        "created_at": time.time(),
        "storage": (storage or "s3").lower(),
        "media_key": media_key,
        "gps_path": gps_path,
        "capture_mode": capture_mode if capture_mode in ("walking", "vehicle") else "vehicle",
        "user_id": user_id,
        "username": username,
        "media_title": media_title,
    }
    dest = PENDING / f"{job_id}.json"
    tmp = PENDING / f".{job_id}.tmp"
    with _ENQUEUE_LOCK:
        tmp.write_text(json.dumps(job, indent=2), encoding="utf-8")
        tmp.replace(dest)
    return {"job_id": job_id, "path": str(dest)}


def list_pending() -> list[Path]:
    ensure_queue_dirs()
    return sorted(PENDING.glob("*.json"), key=lambda p: p.stat().st_mtime)


def claim_next_job() -> dict[str, Any] | None:
    ensure_queue_dirs()
    with _ENQUEUE_LOCK:
        pending = list_pending()
        if not pending:
            return None
        src = pending[0]
        try:
            job = json.loads(src.read_text(encoding="utf-8"))
        except Exception:
            try:
                src.replace(FAILED / src.name)
            except Exception:
                pass
            return None
        dest = RUNNING / src.name
        try:
            src.replace(dest)
        except FileNotFoundError:
            return None
        job["_running_path"] = str(dest)
        return job


def finish_job(job: dict[str, Any], *, ok: bool, error: str | None = None) -> None:
    ensure_queue_dirs()
    running = Path(job.get("_running_path") or "")
    if not running.is_file():
        jid = job.get("job_id") or "unknown"
        running = RUNNING / f"{jid}.json"
    if not running.is_file():
        return
    dest_dir = DONE if ok else FAILED
    dest = dest_dir / running.name
    try:
        payload = json.loads(running.read_text(encoding="utf-8"))
    except Exception:
        payload = dict(job)
    payload["finished_at"] = time.time()
    payload["ok"] = ok
    if error:
        payload["error"] = error
    try:
        dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        running.unlink(missing_ok=True)
    except Exception:
        try:
            running.replace(dest)
        except Exception:
            pass


def queue_counts() -> dict[str, int]:
    ensure_queue_dirs()
    return {
        "pending": len(list(PENDING.glob("*.json"))),
        "running": len(list(RUNNING.glob("*.json"))),
        "done": len(list(DONE.glob("*.json"))),
        "failed": len(list(FAILED.glob("*.json"))),
    }


def failed_job_summaries(limit: int = 20) -> list[dict[str, Any]]:
    """Recent failed detect jobs (media_key + error) for worker/ops logs."""
    ensure_queue_dirs()
    out: list[dict[str, Any]] = []
    paths = sorted(FAILED.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in paths[: max(1, limit)]:
        try:
            job = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({
            "job_id": job.get("job_id"),
            "media_key": job.get("media_key"),
            "error": (job.get("error") or "unknown")[:500],
        })
    return out


_VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")


def _job_media_keys(*dirs: Path) -> set[str]:
    out: set[str] = set()
    for d in dirs:
        if not d.is_dir():
            continue
        for p in d.glob("*.json"):
            try:
                job = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            key = (job.get("media_key") or "").strip()
            if key:
                out.add(key)
    return out


def s3_scan_enabled() -> bool:
    """Periodically enqueue media still sitting in the S3 input bucket."""
    return _env_bool(
        "AUTO_DETECT_SCAN_S3",
        default_when_unset=_is_production_env(),
    )


def scan_and_enqueue_s3_pending(
    *,
    sources: tuple[str, ...] = ("videographer",),
    capture_mode: str = "vehicle",
    limit: int | None = None,
) -> dict[str, Any]:
    """List S3 input media and queue detection for keys not already queued.

    Successful detection moves the source out of the input bucket, so anything
    still listed here is awaiting processing (including pre-deploy uploads).
    Videos without a sibling GPS log are skipped (detection requires GPS).
    """
    if not auto_detect_enabled() or not s3_scan_enabled():
        return {"queued": 0, "skipped": 0, "reason": "disabled"}

    try:
        from s3_utils import (
            find_sibling_gps_key,
            get_input_bucket,
            is_s3_configured,
            list_media_keys,
        )
    except Exception as e:
        return {"queued": 0, "skipped": 0, "error": str(e)}

    if not is_s3_configured():
        return {"queued": 0, "skipped": 0, "reason": "s3_not_configured"}

    try:
        from routes.detection.catalog import list_s3_catalog
    except Exception:
        list_s3_catalog = None

    bucket = get_input_bucket()
    keys: list[str] = []
    if list_s3_catalog is not None:
        for src in sources:
            cat = list_s3_catalog(source=src) or {}
            keys.extend(cat.get("keys") or [])
    else:
        prefix = os.getenv("S3_INPUT_PREFIX", "").strip()
        if prefix and not prefix.endswith("/"):
            prefix = f"{prefix}/"
        keys = list_media_keys(prefix=prefix)

    keys = sorted(set(k for k in keys if k))
    already = _job_media_keys(PENDING, RUNNING)
    # Avoid hammering the same failures every scan interval
    failed_keys = _job_media_keys(FAILED)
    requeue_failed = os.getenv("DETECT_S3_REQUEUE_FAILED", "0").lower() in ("1", "true", "yes")

    queued = 0
    skipped = 0
    skip_no_gps = 0
    skip_queued = 0
    skip_failed = 0
    max_n = limit
    if max_n is None:
        try:
            max_n = int(os.getenv("DETECT_S3_SCAN_LIMIT", "50"))
        except (TypeError, ValueError):
            max_n = 50

    for key in keys:
        if queued >= max_n:
            break
        low = key.lower()
        is_video = low.endswith(_VIDEO_EXTS)
        is_image = low.endswith(_IMAGE_EXTS)
        if not is_video and not is_image:
            skipped += 1
            continue
        if key in already:
            skip_queued += 1
            skipped += 1
            continue
        if key in failed_keys and not requeue_failed:
            skip_failed += 1
            skipped += 1
            continue
        if is_video:
            try:
                gps = find_sibling_gps_key(bucket, key)
            except Exception:
                gps = None
            if not gps:
                skip_no_gps += 1
                skipped += 1
                continue
        job = enqueue_detect_job(
            storage="s3",
            media_key=key,
            gps_path=None,
            capture_mode=capture_mode,
            media_title=Path(key).name,
        )
        if job:
            queued += 1
            already.add(key)
        else:
            skipped += 1

    return {
        "queued": queued,
        "skipped": skipped,
        "skip_queued": skip_queued,
        "skip_no_gps": skip_no_gps,
        "skip_failed": skip_failed,
        "scanned": len(keys),
        "bucket": bucket,
    }
