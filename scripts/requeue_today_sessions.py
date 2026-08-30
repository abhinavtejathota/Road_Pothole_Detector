"""One-off: delete today's video_sessions and restore input videos for re-detect."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

import psycopg2

from routes.field_upload_service import processed_source_key, split_field_key
from s3_utils import (
    _client,
    copy_object,
    get_input_bucket,
    get_processed_bucket,
    object_exists,
)


def main() -> int:
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )
    cur = conn.cursor()
    cur.execute(
        """
        SELECT vs.id, vs.s3_key, vs.display_name, vs.filename, vs.username
        FROM video_sessions vs
        WHERE (vs.processed_at AT TIME ZONE 'Asia/Kolkata')::date
              = (NOW() AT TIME ZONE 'Asia/Kolkata')::date
        ORDER BY vs.id
        """
    )
    sessions = cur.fetchall()
    ids = [r[0] for r in sessions]
    print("deleting sessions", ids)
    if not ids:
        print("nothing to delete")
        return 0

    cur.execute("DELETE FROM potholes WHERE session_id = ANY(%s)", (ids,))
    print("potholes deleted", cur.rowcount)
    cur.execute("DELETE FROM video_sessions WHERE id = ANY(%s)", (ids,))
    print("sessions deleted", cur.rowcount)
    conn.commit()

    ib = get_input_bucket()
    pb = get_processed_bucket()
    c = _client()

    seen: set[str] = set()
    for sid, s3_key, display_name, filename, _username in sessions:
        if not s3_key or s3_key in seen:
            continue
        seen.add(s3_key)
        meta = split_field_key(s3_key)
        folder = f"sources/{meta['username']}/{meta['route_folder']}/"
        cands: list[str] = [processed_source_key(s3_key)]
        for name in (display_name, filename):
            if name and str(name).lower().endswith((".mp4", ".webm", ".mov")):
                cands.append(folder + str(name))
        try:
            r = c.list_objects_v2(Bucket=pb, Prefix=folder, MaxKeys=80)
            for o in r.get("Contents") or []:
                k = o["Key"]
                if k.lower().endswith((".mp4", ".webm", ".mov")) and "/frames/" not in k:
                    cands.append(k)
        except Exception as e:
            print("list fail", folder, e)

        uniq: list[str] = []
        for k in cands:
            if k and k not in uniq:
                uniq.append(k)

        src = next((k for k in uniq if object_exists(pb, k)), None)
        print(f"\n=== restore for session {sid} ===")
        print(" target", s3_key)
        print(" source", src)
        if not src:
            print(" SKIP — no processed video")
            continue
        if object_exists(ib, s3_key):
            print(" input already present")
            continue
        print(" copying…")
        try:
            copy_object(pb, src, ib, s3_key)
            print(" OK", object_exists(ib, s3_key))
        except Exception as e:
            print(" COPY FAIL", e)

    cur.execute(
        """
        SELECT COUNT(*) FROM video_sessions
        WHERE (processed_at AT TIME ZONE 'Asia/Kolkata')::date
              = (NOW() AT TIME ZONE 'Asia/Kolkata')::date
        """
    )
    print("\nToday sessions remaining:", cur.fetchone()[0])
    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
