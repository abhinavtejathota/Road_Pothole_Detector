"""Requeue a processed video session for re-detection.

Restores the source video (+ GPS siblings) to the input bucket, removes DB
rows for the session, and purges the matching prefixes from the processed bucket.
"""
from __future__ import annotations

from typing import Any

import db_utils
from routes.field_upload_service import (
    processed_run_prefix,
    processed_source_key,
    split_field_key,
)
from s3_utils import (
    GPS_EXTS,
    VIDEO_EXTS,
    _client,
    copy_object,
    delete_prefix,
    get_input_bucket,
    get_processed_bucket,
    is_s3_configured,
    object_exists,
)


def _is_video_key(key: str) -> bool:
    low = (key or "").lower()
    return any(low.endswith(ext) for ext in VIDEO_EXTS)


def _is_gps_key(key: str) -> bool:
    low = (key or "").lower()
    return any(low.endswith(ext) for ext in GPS_EXTS)


def _find_processed_video_key(
    pb: str,
    s3_key: str,
    display_name: str | None,
    filename: str | None,
) -> str | None:
    """Locate the archived source video under the processed bucket."""
    meta = split_field_key(s3_key)
    folder = f"sources/{meta['username']}/{meta['route_folder']}/"
    cands: list[str] = [processed_source_key(s3_key)]
    for name in (display_name, filename):
        if name and _is_video_key(str(name)):
            cands.append(folder + str(name))
    try:
        resp = _client().list_objects_v2(Bucket=pb, Prefix=folder, MaxKeys=80)
        for o in resp.get("Contents") or []:
            k = o["Key"]
            if _is_video_key(k) and "/frames/" not in k:
                cands.append(k)
    except Exception:
        pass

    uniq: list[str] = []
    for k in cands:
        if k and k not in uniq:
            uniq.append(k)
    return next((k for k in uniq if object_exists(pb, k)), None)


def _restore_sources_to_input(
    *,
    ib: str,
    pb: str,
    s3_key: str,
    processed_video_key: str,
) -> dict[str, Any]:
    """Copy video + GPS siblings from processed sources/ back to the input key folder."""
    meta = split_field_key(s3_key)
    sources_folder = f"sources/{meta['username']}/{meta['route_folder']}/"
    input_folder = s3_key.rsplit("/", 1)[0] + "/" if "/" in s3_key else ""

    restored: list[str] = []
    skipped: list[str] = []

    # Primary video → original input key
    if not object_exists(ib, s3_key):
        copy_object(pb, processed_video_key, ib, s3_key)
        restored.append(s3_key)
    else:
        skipped.append(s3_key)

    # GPS / log siblings sitting beside the archived source (not frames/).
    # Do not copy other videos — processed archives are often renamed
    # (e.g. video5_s71_….mp4) while GPS keeps the original hash basename.
    try:
        resp = _client().list_objects_v2(Bucket=pb, Prefix=sources_folder, MaxKeys=200)
        objs = resp.get("Contents") or []
    except Exception:
        objs = []

    for o in objs:
        src = o.get("Key") or ""
        if not src or "/frames/" in src:
            continue
        if src == processed_video_key:
            continue
        if not _is_gps_key(src):
            continue
        name = src.rsplit("/", 1)[-1]
        if not name:
            continue
        dst = f"{input_folder}{name}" if input_folder else name
        if object_exists(ib, dst):
            skipped.append(dst)
            continue
        copy_object(pb, src, ib, dst)
        restored.append(dst)

    return {
        "restored_keys": restored,
        "skipped_existing": skipped,
        "sources_folder": sources_folder,
    }


def _delete_session_db(session_id: int) -> dict[str, int]:
    """Remove work-order tree + potholes + video_sessions row for one session."""
    with db_utils.db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM work_orders WHERE session_id = %s",
            (session_id,),
        )
        wo_ids = [int(r[0]) for r in cur.fetchall()]

        status_deleted = 0
        if wo_ids:
            cur.execute(
                """
                DELETE FROM status_history
                WHERE entity_type = 'work_order' AND entity_id = ANY(%s)
                """,
                (wo_ids,),
            )
            status_deleted = cur.rowcount or 0
            cur.execute(
                "DELETE FROM work_orders WHERE id = ANY(%s)",
                (wo_ids,),
            )
        wo_deleted = len(wo_ids)

        cur.execute("DELETE FROM potholes WHERE session_id = %s", (session_id,))
        potholes_deleted = cur.rowcount or 0
        cur.execute("DELETE FROM video_sessions WHERE id = %s", (session_id,))
        sessions_deleted = cur.rowcount or 0
        conn.commit()

    return {
        "work_orders_deleted": wo_deleted,
        "status_history_deleted": status_deleted,
        "potholes_deleted": potholes_deleted,
        "sessions_deleted": sessions_deleted,
    }


def requeue_video_session(session_id: int) -> dict[str, Any]:
    """
    Move a processed session back onto the detect queue path:
      1) copy video (+ GPS) from processed → input bucket
      2) delete DB records for the session
      3) purge sources/ + runs/ prefixes from the processed bucket
    """
    if not is_s3_configured():
        raise RuntimeError("S3 is not configured")

    session = db_utils.get_session_by_id(int(session_id))
    if not session:
        raise LookupError("session not found")

    s3_key = (session.get("s3_key") or "").strip()
    if not s3_key:
        raise RuntimeError("session has no s3_key; cannot restore input video")

    ib = get_input_bucket()
    pb = get_processed_bucket()

    src_video = _find_processed_video_key(
        pb,
        s3_key,
        session.get("display_name"),
        session.get("filename"),
    )
    if not src_video:
        raise RuntimeError(
            "Processed source video not found in S3 — cannot requeue this session"
        )

    restore = _restore_sources_to_input(
        ib=ib,
        pb=pb,
        s3_key=s3_key,
        processed_video_key=src_video,
    )

    # Fail closed before mutating DB if the restored input video is missing.
    if not object_exists(ib, s3_key):
        raise RuntimeError(
            f"Restore failed — input object missing after copy: s3://{ib}/{s3_key}"
        )

    db_stats = _delete_session_db(int(session_id))

    meta = split_field_key(s3_key)
    sources_prefix = f"sources/{meta['username']}/{meta['route_folder']}/"
    purge_errors: list[str] = []
    deleted_sources = 0
    deleted_runs = 0
    runs_prefix = None
    try:
        deleted_sources = delete_prefix(pb, sources_prefix)
    except Exception as e:
        purge_errors.append(f"sources: {e}")

    run_id = (session.get("run_id") or "").strip()
    if run_id:
        runs_prefix = processed_run_prefix(s3_key, run_id)
        if not runs_prefix.endswith("/"):
            runs_prefix = f"{runs_prefix}/"
        try:
            deleted_runs = delete_prefix(pb, runs_prefix)
        except Exception as e:
            purge_errors.append(f"runs: {e}")

    return {
        "ok": True,
        "session_id": int(session_id),
        "input_key": s3_key,
        "processed_video_key": src_video,
        "restore": restore,
        "db": db_stats,
        "purged": {
            "sources_prefix": sources_prefix,
            "sources_objects": deleted_sources,
            "runs_prefix": runs_prefix,
            "runs_objects": deleted_runs,
            "errors": purge_errors,
        },
    }
