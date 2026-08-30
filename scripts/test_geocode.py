#!/usr/bin/env python3
"""Smoke-test GET /api/survey/geocode logic (local survey_service, no Flask).

Usage:
  python scripts/test_geocode.py
  python scripts/test_geocode.py --districts 749,523 --queries eluru,chintalapadu
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from routes.survey_service import annotate_geocode_access, geocode_search  # noqa: E402

DEFAULT_DISTRICTS = ["749", "523"]
DEFAULT_QUERIES = [
    "eluru",
    "chintalapadu",
    "denduluru",
    "bhimadole",
    "vijayawada",
    "gannavaram",
    "machilipatnam",
    "nh16",
]


def run(queries: list[str], district_ids: list[str], state_key: str = "andhra") -> int:
    failed = 0
    print(f"Districts: {', '.join(district_ids)}  state={state_key}\n")
    print(f"{'query':<18} {'ok':<6} {'district':<18} top result")
    print("-" * 72)
    for q in queries:
        rows = geocode_search(
            q,
            state_keys=[state_key],
            district_ids=district_ids,
            limit=8,
        )
        annotated = annotate_geocode_access(rows, district_ids, require_allowed=True)
        top = annotated[0] if annotated else {}
        ok = top.get("access_ok", False)
        name = str(top.get("display_name") or "—")[:40]
        dist = str(top.get("district_name") or "—")[:18]
        if not ok or not name or name == "—":
            failed += 1
            flag = "FAIL"
        else:
            flag = "ok"
        print(f"{q:<18} {flag:<6} {dist:<18} {name}")
    print()
    print(f"Done: {len(queries) - failed}/{len(queries)} passed")
    return 1 if failed else 0


def main() -> int:
    p = argparse.ArgumentParser(description="Test survey geocode search")
    p.add_argument(
        "--districts",
        default=",".join(DEFAULT_DISTRICTS),
        help="Comma-separated LGD district ids (default: 749,523 NTR + West Godavari)",
    )
    p.add_argument(
        "--queries",
        default=",".join(DEFAULT_QUERIES),
        help="Comma-separated place/road queries",
    )
    p.add_argument("--state", default="andhra", choices=["andhra", "telangana"])
    args = p.parse_args()
    districts = [x.strip() for x in args.districts.split(",") if x.strip()]
    queries = [x.strip() for x in args.queries.split(",") if x.strip()]
    return run(queries, districts, state_key=args.state)


if __name__ == "__main__":
    raise SystemExit(main())
