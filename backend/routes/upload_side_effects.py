"""Post-finalize side effects (tracking / auto_track / seal / auto-detect).

Used by the external finalize worker so Flask/api.py stays thin.
"""
from __future__ import annotations


def after_chunk_finalize(
    result: dict,
    *,
    user_id: int,
    media_title: str | None,
    gps_path: str | None,
    capture_session_id: str | None,
    capture_mode: str | None = None,
    username: str | None = None,
) -> None:
    """Tracking commit + auto_track verify + seal roads + queue detection."""
    try:
        from routes import tracking_service as _ts

        if capture_session_id:
            _ts.commit_capture_session(user_id, capture_session_id)
        if gps_path:
            trail = _ts.trail_for_user(int(user_id), _ts.today_ist())
            n = len([p for p in (trail or []) if isinstance(p, dict)])
            # Backfill cyan trail from GPS log when live pings were lost/wiped.
            if n < 5 or not capture_session_id:
                _ts.ingest_gps_log_coverage(user_id, gps_path)
        _ts._ASSIGN_CLASS_CACHE.clear()
    except Exception:
        pass
    try:
        from routes import auto_track_service

        keys = result.get("keys") or []
        media_key = keys[0] if keys else (media_title or "")
        verify = auto_track_service.record_upload_and_verify(
            user_id,
            video_title=media_title or media_key,
            gps_log_path=gps_path,
            s3_key=media_key,
        )
        result["auto_track"] = verify
    except Exception as e:
        result["auto_track"] = {"ok": False, "error": str(e)}
    try:
        from routes import survey_service

        result["roads_completed"] = survey_service.mark_user_assignment_completed(
            user_id,
            gps_log_path=gps_path,
        )
    except Exception:
        pass

    # Queue sequential background detection (smartroad_worker consumes).
    try:
        from routes.detect_queue import enqueue_detect_job

        keys = result.get("keys") or []
        media_key = (keys[0] if keys else "") or ""
        storage = (result.get("storage") or "s3").lower()
        mode = (capture_mode or "vehicle").strip().lower()
        if mode not in ("walking", "vehicle"):
            mode = "vehicle"

        job = enqueue_detect_job(
            storage=storage,
            media_key=media_key,
            gps_path=gps_path,
            capture_mode=mode,
            user_id=int(user_id) if user_id else None,
            username=username,
            media_title=media_title,
        )
        if job:
            result["auto_detect"] = {"queued": True, "job_id": job.get("job_id")}
        else:
            result["auto_detect"] = {"queued": False}
    except Exception as e:
        result["auto_detect"] = {"queued": False, "error": str(e)}
