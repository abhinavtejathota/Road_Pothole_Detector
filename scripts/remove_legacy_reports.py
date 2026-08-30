#!/usr/bin/env python3
"""Remove legacy detection DOCX reports (S3 + local) and clear legacy flags in DB."""
from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=True)
    except Exception:
        pass


def purge_s3_legacy_reports(*, dry_run: bool) -> dict:
    try:
        from s3_utils import get_reports_bucket, is_s3_configured, _client
    except Exception as e:
        return {"deleted": 0, "error": str(e)}

    if not is_s3_configured():
        return {"deleted": 0, "skipped": "S3 not configured"}

    bucket = get_reports_bucket()
    prefix = "Legacy/"
    deleted = 0
    try:
        client = _client()
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents") or []:
                key = obj["Key"]
                if dry_run:
                    print(f"[dry-run] would delete s3://{bucket}/{key}")
                else:
                    client.delete_object(Bucket=bucket, Key=key)
                    print(f"deleted s3://{bucket}/{key}")
                deleted += 1
    except Exception as e:
        return {"deleted": deleted, "bucket": bucket, "prefix": prefix, "error": str(e)}


def purge_local_legacy_reports(*, dry_run: bool) -> dict:
    """Remove local report copies under data/reports/Legacy if present."""
    base = ROOT / "data" / "reports"
    legacy = base / "Legacy"
    if not legacy.exists():
        return {"deleted": 0, "path": str(legacy)}
    count = sum(1 for _ in legacy.rglob("*") if _.is_file())
    if dry_run:
        print(f"[dry-run] would remove {count} file(s) under {legacy}")
    else:
        shutil.rmtree(legacy, ignore_errors=True)
        print(f"removed {count} file(s) under {legacy}")
    return {"deleted": count, "path": str(legacy)}


def purge_db_legacy_reports(*, dry_run: bool) -> dict:
    import db_utils

    if not db_utils.is_db_configured():
        return {"updated": 0, "skipped": "DB not configured"}

    conn = db_utils._get_conn()
    try:
        with conn.cursor() as cur:
            if dry_run:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM video_sessions
                    WHERE is_legacy = TRUE
                       OR LOWER(COALESCE(username, '')) = 'legacy'
                       OR report_s3_key IS NOT NULL
                    """
                )
                n = cur.fetchone()[0]
                print(f"[dry-run] would clear/delete legacy on {n} session row(s)")
                return {"updated": int(n or 0)}
            # Clear report fields on all sessions with reports
            cur.execute(
                """
                UPDATE video_sessions
                SET report_s3_key = NULL,
                    report_generated_at = NULL
                WHERE report_s3_key IS NOT NULL
                """
            )
            cleared = cur.rowcount
            # Hard-delete legacy / unassigned sessions (respect FKs)
            cur.execute(
                """
                SELECT id FROM video_sessions
                WHERE is_legacy = TRUE
                   OR LOWER(COALESCE(username, '')) = 'legacy'
                """
            )
            legacy_ids = [r[0] for r in cur.fetchall()]
            pot = 0
            deleted = 0
            if legacy_ids:
                cur.execute(
                    """
                    DELETE FROM work_order_potholes
                    WHERE work_order_id IN (
                        SELECT id FROM work_orders WHERE session_id = ANY(%s)
                    )
                    OR pothole_id IN (
                        SELECT id FROM potholes WHERE session_id = ANY(%s)
                    )
                    """,
                    (legacy_ids, legacy_ids),
                )
                cur.execute(
                    """
                    DELETE FROM status_history
                    WHERE entity_type = 'work_order'
                      AND entity_id IN (
                        SELECT id FROM work_orders WHERE session_id = ANY(%s)
                      )
                    """,
                    (legacy_ids,),
                )
                cur.execute(
                    "DELETE FROM work_orders WHERE session_id = ANY(%s)",
                    (legacy_ids,),
                )
                cur.execute("DELETE FROM potholes WHERE session_id = ANY(%s)", (legacy_ids,))
                pot = cur.rowcount
                cur.execute("DELETE FROM video_sessions WHERE id = ANY(%s)", (legacy_ids,))
                deleted = cur.rowcount
            # Remaining non-legacy: clear is_legacy flag if any stray
            cur.execute(
                """
                UPDATE video_sessions SET is_legacy = FALSE WHERE is_legacy = TRUE
                """
            )
        conn.commit()
        print(
            f"cleared reports on {cleared} row(s); "
            f"deleted {deleted} legacy session(s) (+ {pot} pothole rows)"
        )
        return {"reports_cleared": cleared, "sessions_deleted": deleted, "potholes_deleted": pot}
    finally:
        conn.close()


def purge_s3_input_legacy(*, dry_run: bool) -> dict:
    """Best-effort delete Legacy/ prefixes in the input videos bucket."""
    try:
        from s3_utils import get_input_bucket, is_s3_configured, _client, MEDIA_EXTS
    except Exception as e:
        return {"deleted": 0, "error": str(e)}
    if not is_s3_configured():
        return {"deleted": 0, "skipped": "S3 not configured"}
    bucket = get_input_bucket()
    deleted = 0
    try:
        client = _client()
        paginator = client.get_paginator("list_objects_v2")
        for prefix in ("Legacy/", "legacy/"):
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents") or []:
                    key = obj["Key"]
                    if dry_run:
                        print(f"[dry-run] would delete s3://{bucket}/{key}")
                    else:
                        client.delete_object(Bucket=bucket, Key=key)
                        print(f"deleted s3://{bucket}/{key}")
                    deleted += 1
    except Exception as e:
        return {"deleted": deleted, "bucket": bucket, "error": str(e)}
    return {"deleted": deleted, "bucket": bucket}


def main() -> int:
    parser = argparse.ArgumentParser(description="Purge legacy detection DOCX reports.")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without deleting")
    args = parser.parse_args()
    _load_env()
    os.chdir(ROOT)
    s3 = purge_s3_legacy_reports(dry_run=args.dry_run)
    s3_input = purge_s3_input_legacy(dry_run=args.dry_run)
    local = purge_local_legacy_reports(dry_run=args.dry_run)
    db = purge_db_legacy_reports(dry_run=args.dry_run)
    print("summary:", {"s3_reports": s3, "s3_input": s3_input, "local": local, "db": db})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
