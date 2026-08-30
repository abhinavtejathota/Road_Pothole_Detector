#!/usr/bin/env python3
"""Smoke lat/lon geocode + locate for Telangana VG districts (Ranga Reddy / Medchal).

Usage:
  python scripts/test_geocode_coords.py
  python scripts/test_geocode_coords.py --lat 17.4483 --lon 78.3915
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from routes.survey_service import (  # noqa: E402
    annotate_geocode_access,
    check_location_access,
    geocode_search,
)
from routes.survey.geocode import _is_generic_road_name, _reverse_label_for_point  # noqa: E402

DEFAULT_DISTRICTS = ["700", "518"]


def run(lat: float, lon: float, district_ids: list[str]) -> int:
    failed = 0
    q = f"{lat}, {lon}"
    print(f"Query: {q}")
    print(f"Districts: {', '.join(district_ids)}\n")

    t0 = time.time()
    rows = geocode_search(
        q,
        state_key="telangana",
        district_ids=district_ids,
        limit=1,
    )
    elapsed = time.time() - t0
    ann = annotate_geocode_access(rows, district_ids, require_allowed=True)
    top = ann[0] if ann else {}
    print(f"geocode_search  {elapsed:.2f}s  access_ok={top.get('access_ok')}  "
          f"district={top.get('district_name')}  name={top.get('display_name')!r}")
    if not top.get("access_ok"):
        failed += 1
        print("  FAIL: expected access_ok")
    if str(top.get("district_id")) != "518" and lat > 17.4:
        # Madhapur-ish should be RR; other points may differ
        print(f"  WARN: district_id={top.get('district_id')} (expected 518 for Madhapur corridor)")
    dn = str(top.get("display_name") or "")
    if "Road Number" in dn or _is_generic_road_name(dn):
        failed += 1
        print(f"  FAIL: generic road label still used: {dn!r}")

    chk = check_location_access(
        lat, lon, allowed_district_ids=district_ids, require_allowed=True,
    )
    print(f"check_location  in_scope={chk.get('in_scope')}  "
          f"located={chk.get('located', {}).get('district_name')}  msg={chk.get('message')!r}")
    if not chk.get("in_scope"):
        failed += 1
        print("  FAIL: expected in_scope")

    label = _reverse_label_for_point(lat, lon, district_ids=district_ids)
    print(f"reverse_label   {label.get('display_name')!r}  source={label.get('source')}")

    # Negative: Colorado must not be in-scope
    far = check_location_access(
        38.93, -104.62, allowed_district_ids=district_ids, require_allowed=True,
    )
    print(f"colorado_check  in_scope={far.get('in_scope')}  msg={(far.get('message') or '')[:70]!r}")
    if far.get("in_scope"):
        failed += 1
        print("  FAIL: Colorado should be out of scope")

    print()
    print("PASS" if failed == 0 else f"FAIL ({failed})")
    return 1 if failed else 0


def main() -> int:
    p = argparse.ArgumentParser(description="Test lat/lon survey geocode + locate")
    p.add_argument("--lat", type=float, default=17.4483)
    p.add_argument("--lon", type=float, default=78.3915)
    p.add_argument(
        "--districts",
        default=",".join(DEFAULT_DISTRICTS),
        help="Comma-separated LGD district ids",
    )
    args = p.parse_args()
    districts = [x.strip() for x in args.districts.split(",") if x.strip()]
    return run(args.lat, args.lon, districts)


if __name__ == "__main__":
    raise SystemExit(main())
