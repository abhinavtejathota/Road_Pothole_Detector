#!/usr/bin/env python3
"""SmartRoad worker — finalize uploads + sequential auto-detect.

Consumes:
  data/finalize_queue/pending/  — ffmpeg concat + S3 + side effects (queues detect)
  data/detect_queue/pending/    — YOLO detection one-at-a-time

Usage:
  python scripts/smartroad_worker.py
  # or systemd: smartroad-worker.service
"""
from __future__ import annotations

import os
import shutil
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import smartroad_path  # noqa: F401 — backend/ on sys.path

for _k in (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
):
    os.environ.setdefault(_k, "2")
os.environ.setdefault("S3_UPLOAD_CONCURRENCY", os.getenv("FINALIZE_S3_CONCURRENCY", "8"))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
except Exception:
    pass

# Lower scheduling priority vs Flask (Linux).
try:
    if hasattr(os, "nice"):
        os.nice(int(os.getenv("FINALIZE_WORKER_NICE", "10")))
except Exception:
    pass


def _log(msg: str) -> None:
    print(f"[smartroad-worker] {msg}", flush=True)


def _process_finalize_job(job: dict) -> None:
    from routes.field_upload_service import (
        _chunk_lock,
        _chunk_session_dir,
        _read_chunk_meta,
        _write_chunk_meta,
        finalize_chunk_session,
    )
    from routes.finalize_queue import finish_job
    from routes.upload_side_effects import after_chunk_finalize

    payload = dict(job.get("finalize") or {})
    side = dict(job.get("side_effects") or {})
    session_id = payload.get("session_id") or job.get("session_id") or ""
    username = payload.get("username") or job.get("username") or ""
    session_dir = _chunk_session_dir(username, session_id)

    if session_dir.is_dir():
        with _chunk_lock(session_dir):
            m = _read_chunk_meta(session_dir)
            m["finalize_status"] = "running"
            m["finalize_job_id"] = job.get("job_id")
            _write_chunk_meta(session_dir, m)

    _log(f"finalize start job={job.get('job_id')} session={session_id}")
    try:
        result = finalize_chunk_session(**payload)
        kind = (result.get("status") or {}).get("kind")
        gps_path = side.get("gps_path")
        if not gps_path:
            gps_path = payload.get("gps_path")
        if kind == "ok":
            after_chunk_finalize(
                result,
                user_id=int(side.get("user_id") or 0),
                media_title=side.get("media_title"),
                gps_path=gps_path,
                capture_session_id=side.get("capture_session_id"),
                capture_mode=side.get("capture_mode") or payload.get("capture_mode"),
                username=username,
            )
            if session_dir.is_dir():
                shutil.rmtree(session_dir, ignore_errors=True)
            finish_job(job, ok=True)
            _log(f"finalize done job={job.get('job_id')} auto_detect={result.get('auto_detect')}")
        else:
            err = (result.get("status") or {}).get("message") or "finalize failed"
            if session_dir.is_dir():
                with _chunk_lock(session_dir):
                    m = _read_chunk_meta(session_dir)
                    m["finalize_status"] = "error"
                    m["finalize_error"] = err
                    _write_chunk_meta(session_dir, m)
            finish_job(job, ok=False, error=str(err))
            _log(f"finalize fail job={job.get('job_id')}: {err}")
    except Exception as e:
        if session_dir.is_dir():
            try:
                with _chunk_lock(session_dir):
                    m = _read_chunk_meta(session_dir)
                    m["finalize_status"] = "error"
                    m["finalize_error"] = str(e)
                    _write_chunk_meta(session_dir, m)
            except Exception:
                pass
        finish_job(job, ok=False, error=str(e))
        _log(f"finalize error job={job.get('job_id')}: {e}\n{traceback.format_exc()}")


def _process_detect_job(job: dict) -> None:
    """Run pothole detection for one uploaded media file (blocking, sequential)."""
    from routes.detect_queue import finish_job

    # Wait for any in-flight Detection UI run to finish before claiming GPU.
    os.environ.setdefault(
        "DETECTION_LOCK_WAIT_S",
        os.getenv("DETECT_WORKER_LOCK_WAIT_S", "7200"),
    )

    storage = (job.get("storage") or "s3").lower()
    media_key = (job.get("media_key") or "").strip()
    gps_path = job.get("gps_path")
    capture_mode = job.get("capture_mode") or "vehicle"
    _log(f"detect start job={job.get('job_id')} key={media_key} storage={storage}")

    try:
        from routes import detection_service

        kwargs = {
            "capture_mode": capture_mode if capture_mode in ("walking", "vehicle") else "vehicle",
            "rotation": 0,
        }
        if storage == "s3":
            kwargs["s3_key"] = media_key
            # Let run_detection find sibling GPS in S3 when local gps_path is gone
            if gps_path and Path(str(gps_path)).is_file():
                kwargs["gps_path"] = gps_path
        else:
            local = Path(media_key)
            if not local.is_file():
                local = ROOT / media_key
            if not local.is_file():
                raise FileNotFoundError(f"Local media not found: {media_key}")
            kwargs["local_path"] = str(local)
            if gps_path and Path(str(gps_path)).is_file():
                kwargs["gps_path"] = gps_path
            else:
                # Sibling GPS next to media
                stem = local.with_suffix("")
                for ext in (".csv", ".xlsx", ".json", ".gpx"):
                    sib = Path(str(stem) + ext)
                    if sib.is_file():
                        kwargs["gps_path"] = str(sib)
                        break

        result = detection_service.run_detection(**kwargs)
        kind = (result.get("status") or {}).get("kind")
        if kind == "ok":
            finish_job(job, ok=True)
            _log(f"detect done job={job.get('job_id')}")
        else:
            err = (result.get("status") or {}).get("message") or "detection failed"
            finish_job(job, ok=False, error=str(err))
            _log(f"detect fail job={job.get('job_id')}: {err}")
    except Exception as e:
        finish_job(job, ok=False, error=str(e))
        _log(f"detect error job={job.get('job_id')}: {e}\n{traceback.format_exc()}")


def main() -> int:
    from routes.detect_queue import (
        claim_next_job as claim_detect,
        ensure_queue_dirs as ensure_detect_dirs,
        failed_job_summaries,
        queue_counts as detect_counts,
        scan_and_enqueue_s3_pending,
        s3_scan_enabled,
    )
    from routes.finalize_queue import (
        claim_next_job as claim_finalize,
        ensure_queue_dirs as ensure_finalize_dirs,
        queue_counts as finalize_counts,
    )

    ensure_finalize_dirs()
    ensure_detect_dirs()
    poll = float(os.getenv("FINALIZE_WORKER_POLL_S", "2"))
    scan_every = float(os.getenv("DETECT_S3_SCAN_S", "60"))
    last_scan = 0.0
    _log(
        f"listening finalize={os.getenv('FINALIZE_QUEUE_DIR', 'data/finalize_queue')} "
        f"detect={os.getenv('DETECT_QUEUE_DIR', 'data/detect_queue')} poll={poll}s "
        f"s3_scan={'on' if s3_scan_enabled() else 'off'} every={scan_every}s"
    )
    _log(f"finalize_counts={finalize_counts()} detect_counts={detect_counts()}")

    def _log_failed_detect_hint(summary: dict | None) -> None:
        if not summary or not int(summary.get("skip_failed") or 0):
            return
        for row in failed_job_summaries(8):
            _log(
                f"detect_failed key={row.get('media_key')} "
                f"error={row.get('error')}"
            )
        _log(
            "detect requeue: DETECT_S3_REQUEUE_FAILED=1 python scripts/enqueue_s3_detect.py --requeue-failed"
        )

    # Immediate backfill of whatever is already in the input bucket
    try:
        summary = scan_and_enqueue_s3_pending()
        _log(f"s3_scan initial={summary}")
        _log_failed_detect_hint(summary)
        last_scan = time.time()
    except Exception as e:
        _log(f"s3_scan initial error: {e}")

    while True:
        now = time.time()
        if s3_scan_enabled() and (now - last_scan) >= scan_every:
            try:
                summary = scan_and_enqueue_s3_pending()
                if summary.get("queued"):
                    _log(f"s3_scan={summary}")
            except Exception as e:
                _log(f"s3_scan error: {e}")
            last_scan = now

        # Prefer finalize (unblocks phones) then drain detect FIFO.
        job = claim_finalize()
        if job is not None:
            _process_finalize_job(job)
            continue
        job = claim_detect()
        if job is not None:
            _process_detect_job(job)
            continue
        time.sleep(poll)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
