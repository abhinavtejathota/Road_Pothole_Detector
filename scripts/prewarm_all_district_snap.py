#!/usr/bin/env python3
"""Build snap indexes (*.snap.pkl) for every district in Andhra + Telangana."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=True)
    except Exception:
        pass


def all_district_ids() -> list[str]:
    from routes import survey_service

    ids: list[str] = []
    for sk in ("andhra", "telangana"):
        for d in survey_service.list_districts(sk) or []:
            did = str(d.get("district_id") or "").strip()
            if did and did not in ids:
                ids.append(did)
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description="Prewarm district snap indexes for survey locate.")
    parser.add_argument("--district", action="append", help="Single LGD district id (repeatable)")
    args = parser.parse_args()
    _load_env()
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))

    from routes.survey_service import prewarm_snap_indexes

    ids = args.district or all_district_ids()
    print(f"Prewarming {len(ids)} district(s)…")
    out = prewarm_snap_indexes(ids)
    print(f"Done: {out.get('count', 0)} loaded — {out.get('districts', [])[:5]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
