"""Recompute coverage for every videographer/day and write identical values to
tracking_sessions + survey_daily_assignments (canonical persist).

Uses GPS trail recompute — will NOT keep a bogus tiny sealed_km (e.g. 0.36)
when the trail still has a full drive (~6–7 km).

Run on the server after deploy:
  python scripts/sync_coverage_consistency.py
  python scripts/sync_coverage_consistency.py --user-id 8 --date 2026-07-15

Verify:
  SELECT covered_km, route_km, meta->>'completed'
  FROM survey_daily_assignments WHERE user_id=8 AND assignment_date='2026-07-15';
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", type=int, default=None, help="Limit to one videographer")
    parser.add_argument("--date", type=str, default=None, help="Limit to one day YYYY-MM-DD")
    args = parser.parse_args()

    from routes import tracking_service

    if args.user_id is not None and args.date:
        km, cbc, _ = tracking_service.resolve_videographer_coverage(
            int(args.user_id),
            str(args.date)[:10],
            resolve_carryover=False,
            persist=True,
        )
        result = {
            "user_id": int(args.user_id),
            "date": str(args.date)[:10],
            "covered_km": km,
            "covered_by_class": cbc,
        }
        print(json.dumps(result, indent=2, default=str))
        return 0

    result = tracking_service.repair_all_coverage(user_id=args.user_id)
    print(json.dumps(result, indent=2, default=str))
    return 0 if int(result.get("errors") or 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
