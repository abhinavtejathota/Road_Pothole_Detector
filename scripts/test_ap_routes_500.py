#!/usr/bin/env python3
"""Black-box stress: ~500 AP start/end preview_corridor_routes for video4 (59-district account)."""
from __future__ import annotations

import random
import sys
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout

from routes import survey_service as ss
import db_utils

TARGET = 500
STATE = "andhra"
VG = "video4"
CALL_TIMEOUT_S = 25


def _preview(args):
    return ss.preview_corridor_routes(**args)


def main() -> None:
    print("loading users…", flush=True)
    users = db_utils.get_all_users()
    u = next(x for x in users if x.get("username") == VG)
    uid = int(u["id"])
    allowed = [str(x) for x in (u.get("district_ids") or [])]
    print(f"VG {VG} id={uid} districts={len(allowed)} state={u.get('state_id')}", flush=True)

    districts = [d for d in ss.list_districts(STATE) if d.get("center")]
    print(f"AP districts with centers: {len(districts)}", flush=True)
    # Intersect with VG access when possible
    allowed_set = set(allowed)
    in_access = [d for d in districts if str(d["district_id"]) in allowed_set]
    if len(in_access) >= 8:
        districts = in_access
    print(f"using {len(districts)} districts for samples", flush=True)

    pts: list[tuple[float, float, str, str]] = []
    for d in districts:
        lat, lon = float(d["center"][0]), float(d["center"][1])
        name = d.get("name") or str(d["district_id"])
        did = str(d["district_id"])
        pts.append((lat, lon, name, did))
        for dlat, dlon in ((0.06, 0.0), (-0.06, 0.0), (0.0, 0.06), (0.0, -0.06), (0.04, 0.04), (-0.04, 0.04)):
            pts.append((lat + dlat, lon + dlon, name, did))

    random.seed(42)
    random.shuffle(pts)
    pairs = []
    attempts = 0
    while len(pairs) < TARGET and attempts < TARGET * 50:
        attempts += 1
        a, b = random.choice(pts), random.choice(pts)
        dist = ss._haversine_km(a[0], a[1], b[0], b[1])
        if dist < 4.0 or dist > 90.0:
            continue
        pairs.append((a, b, dist))
    print(f"pairs={len(pairs)}", flush=True)

    ok = empty = timed = 0
    errors: list[str] = []
    route_counts: Counter = Counter()
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = []
        for i, (a, b, dist) in enumerate(pairs):
            kwargs = dict(
                state_key=STATE,
                district_id=a[3],
                start_lat=a[0],
                start_lon=a[1],
                end_lat=b[0],
                end_lon=b[1],
                allowed_district_ids=allowed or [a[3]],
                max_options=3,
            )
            futs.append((i, a, b, dist, pool.submit(_preview, kwargs)))

        for n, (i, a, b, dist, fut) in enumerate(futs):
            try:
                res = fut.result(timeout=CALL_TIMEOUT_S)
                routes = res.get("routes") or []
                if not routes:
                    empty += 1
                else:
                    ok += 1
                    route_counts[len(routes)] += 1
            except FutTimeout:
                timed += 1
                errors.append(f"timeout {a[2]}→{b[2]} ({dist:.1f}km)")
            except Exception as e:
                errors.append(f"{a[2]}→{b[2]}: {e}")

            if (n + 1) % 25 == 0 or n == 0 or n + 1 == len(futs):
                print(
                    f"  {n+1}/{len(futs)} ok={ok} empty={empty} timeout={timed} "
                    f"err={len(errors)} elapsed={time.time()-t0:.0f}s",
                    flush=True,
                )

    print("--- summary ---", flush=True)
    print(
        f"ok_with_routes={ok} empty={empty} timeouts={timed} errors={len(errors)} total={len(pairs)}",
        flush=True,
    )
    print(f"route_count_hist={dict(route_counts)}", flush=True)
    for e in errors[:12]:
        print(" ", e, flush=True)

    # Custom assign smoke (pins + tracking payload)
    a, b, _ = pairs[0]
    custom = ss.generate_auto_track_assignment(
        uid,
        state_key=STATE,
        district_id=a[3],
        start_lat=a[0],
        start_lon=a[1],
        end_lat=b[0],
        end_lon=b[1],
        start_label=f"Test {a[2]}",
        end_label=f"Test {b[2]}",
        allowed_district_ids=allowed or [a[3]],
    )
    print("custom_assign mode", (custom.get("summary") or {}).get("mode"), flush=True)
    from routes import tracking_service as ts

    detail = ts.get_videographer_track(uid, ss.today_ist())
    kinds = Counter((f.get("properties") or {}).get("route_kind") for f in (detail.get("features") or []))
    print("track feature kinds", dict(kinds), flush=True)
    assert (custom.get("summary") or {}).get("mode") == "auto_track"
    # success rate gate (OSRM public can flake)
    rate = ok / max(1, len(pairs))
    print(f"success_rate={rate:.1%}", flush=True)
    if rate < 0.5:
        print("WARN: find-routes success below 50%", flush=True)
        sys.exit(2)
    print("PASS", flush=True)


if __name__ == "__main__":
    main()
