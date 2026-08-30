#!/usr/bin/env python3
"""
Backfill: regenerate DOCX reports + rename display / S3 filenames to the short
unique scheme:

  <user12>_s<sessionId>_<YYYYMMDD-HHMMSS>.mp4
  SR_<user12>_s<sessionId>_<YYYYMMDD-HHMMSS>.docx

Route labels stay in DB start_label/end_label (and inside the DOCX), not in the filename.

  python scripts/backfill_detection_reports.py
  python scripts/backfill_detection_reports.py --limit 10
  python scripts/backfill_detection_reports.py --no-force   # skip sessions that already have a report
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill short names + detection DOCX reports")
    parser.add_argument(
        "--force",
        action="store_true",
        default=True,
        help="Regenerate/rename even if report exists (default: on)",
    )
    parser.add_argument(
        "--no-force",
        action="store_true",
        help="Skip sessions that already have report_s3_key",
    )
    parser.add_argument("--limit", type=int, default=0, help="Max sessions (0 = all)")
    args = parser.parse_args()
    force = not args.no_force

    import db_utils
    from routes import report_service

    if not db_utils.is_db_configured():
        raise SystemExit("DB not configured — set DATABASE_URL / .env")

    db_utils.init_db()
    sessions = db_utils.get_all_sessions()
    if args.limit > 0:
        sessions = sessions[: args.limit]

    ok = fail = skip = 0
    for s in sessions:
        sid = int(s["id"])
        print(f"\n=== Session {sid}: {s.get('filename')} ===")
        try:
            result = report_service.generate_report_for_session(sid, force=force)
            if result.get("skipped"):
                skip += 1
                print("  skipped (already has report; omit --no-force to rename)")
            elif result.get("ok"):
                ok += 1
                print(f"  ok → {result.get('report_s3_key')}  display={result.get('display_name')}")
            else:
                fail += 1
                print(f"  fail → {result}")
        except Exception as e:
            fail += 1
            print(f"  error → {e}")

    print(f"\nDone. ok={ok} skip={skip} fail={fail} total={len(sessions)}")
    print("Name pattern: <user12>_s<sessionId>_<YYYYMMDD-HHMMSS>.mp4 / SR_… .docx")


if __name__ == "__main__":
    main()
