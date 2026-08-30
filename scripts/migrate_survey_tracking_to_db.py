#!/usr/bin/env python3
"""Import survey_state.json + tracking_state.json into Postgres (one-shot).

Requires DB_* in .env and tables from schema.sql /
migrations/20260711_survey_tracking_postgres.sql.

Usage:
  python scripts/migrate_survey_tracking_to_db.py
  python scripts/migrate_survey_tracking_to_db.py --init-schema
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate survey/tracking JSON → Postgres")
    parser.add_argument(
        "--init-schema",
        action="store_true",
        help="Run schema.sql / ensure tables exist before import",
    )
    args = parser.parse_args()

    import db_utils

    if not db_utils.is_db_configured():
        print("Database not configured (set DB_NAME / DB_USER in .env)")
        return 1

    if args.init_schema:
        print("Applying schema.sql …")
        db_utils.init_db()
        print("Schema OK")

    survey = db_utils.migrate_survey_json_to_db()
    print("Survey:", survey)
    tracking = db_utils.migrate_tracking_json_to_db()
    print("Tracking:", tracking)

    if not survey.get("ok") and not tracking.get("ok"):
        return 1
    print("Done. App dual-writes Postgres + data/gis/*.json going forward.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
