"""Disk queue for chunk finalize — Flask never runs ffmpeg/S3.

Flask (portal + phone chunk receive) only writes a job JSON under
``data/finalize_queue/pending/``. A separate ``smartroad-worker`` systemd
unit (or ``scripts/smartroad_worker.py``) consumes jobs so assemble/S3 CPU
cannot stall /detection or login.
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
QUEUE_ROOT = Path(os.getenv("FINALIZE_QUEUE_DIR", str(_ROOT / "data" / "finalize_queue")))
PENDING = QUEUE_ROOT / "pending"
RUNNING = QUEUE_ROOT / "running"
DONE = QUEUE_ROOT / "done"
FAILED = QUEUE_ROOT / "failed"

_ENQUEUE_LOCK = threading.Lock()


def ensure_queue_dirs() -> None:
    for d in (PENDING, RUNNING, DONE, FAILED):
        d.mkdir(parents=True, exist_ok=True)


def external_worker_enabled() -> bool:
    # Default ON — keep finalize out of the Flask process entirely.
    return os.getenv("FINALIZE_EXTERNAL_WORKER", "1").lower() in ("1", "true", "yes")


def enqueue_finalize_job(
    *,
    finalize_payload: dict[str, Any],
    side_effects: dict[str, Any],
    session_id: str,
    username: str,
) -> dict[str, Any]:
    """Atomically drop a job file for smartroad-worker. Returns job metadata."""
    ensure_queue_dirs()
    job_id = f"{int(time.time())}_{uuid.uuid4().hex[:10]}"
    job = {
        "job_id": job_id,
        "created_at": time.time(),
        "username": username,
        "session_id": session_id,
        "finalize": finalize_payload,
        "side_effects": side_effects,
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
    """Move oldest pending job → running/. Returns job dict or None."""
    ensure_queue_dirs()
    with _ENQUEUE_LOCK:
        pending = list_pending()
        if not pending:
            return None
        src = pending[0]
        try:
            job = json.loads(src.read_text(encoding="utf-8"))
        except Exception:
            failed = FAILED / src.name
            try:
                src.replace(failed)
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
        # Recover by job_id
        jid = job.get("job_id") or "unknown"
        running = RUNNING / f"{jid}.json"
    job["finished_at"] = time.time()
    job["ok"] = ok
    if error:
        job["error"] = error[:800]
    target_dir = DONE if ok else FAILED
    target = target_dir / running.name
    try:
        target.write_text(json.dumps(job, indent=2, default=str), encoding="utf-8")
        if running.is_file():
            try:
                running.unlink()
            except OSError:
                pass
    except Exception:
        try:
            if running.is_file():
                running.replace(target)
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
