#!/usr/bin/env python3
"""Debug videographer trail jumps (live DB)."""
import sys
from math import radians, sin, cos, asin, sqrt

import db_utils
from routes.tracking_service import (
    get_videographer_track,
    _trail_dicts_to_latlon_ts,
    _clean_trail_latlon_ts,
    covered_trail_features,
)


def hav(a, b):
    r = 6371
    la1, lo1 = map(radians, a)
    la2, lo2 = map(radians, b)
    x = sin((la2 - la1) / 2) ** 2 + cos(la1) * cos(la2) * sin((lo2 - lo1) / 2) ** 2
    return 2 * r * asin(sqrt(x))


def main():
    username = sys.argv[1] if len(sys.argv) > 1 else "video5"
    date = sys.argv[2] if len(sys.argv) > 2 else "2026-07-22"
    users = db_utils.get_all_users()
    row = next((u for u in users if u.get("username") == username), None)
    if not row:
        print("user not found:", username)
        return 1
    uid = row["id"]
    track = get_videographer_track(uid, date)
    trail = track.get("trail") or []
    summary = track.get("summary") or {}
    start = summary.get("start") or {}
    end = summary.get("end") or {}
    print(f"user={username} id={uid} date={date} trail_pts={len(trail)}")
    print("start", start.get("label"), start.get("lat"), start.get("lon"))
    print("end", end.get("label"), end.get("lat"), end.get("lon"))
    for i in range(1, len(trail)):
        a = (float(trail[i - 1]["lat"]), float(trail[i - 1]["lon"]))
        b = (float(trail[i]["lat"]), float(trail[i]["lon"]))
        d = hav(a, b)
        if d > 0.15:
            ts = trail[i].get("ts")
            print(f"  jump i={i} d={d:.3f}km {a} -> {b} ts={ts}")
    if end.get("lat"):
        ep = (float(end["lat"]), float(end["lon"]))
        best = (999.0, -1)
        for i, p in enumerate(trail):
            pt = (float(p["lat"]), float(p["lon"]))
            d = hav(pt, ep)
            if d < best[0]:
                best = (d, i)
        print("closest to end pin", best)
    pts = _trail_dicts_to_latlon_ts(trail)
    runs = _clean_trail_latlon_ts(pts)
    print("clean runs", len(runs), [len(r) for r in runs])
    feats = covered_trail_features(trail, start=start, end=end)
    print("covered features", len(feats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
