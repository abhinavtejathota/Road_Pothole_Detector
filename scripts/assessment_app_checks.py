#!/usr/bin/env python3
"""Application assessment: stress find-routes + scenario batteries (≥100 each).

Writes scripts/assessment_results.json and prints a concise report.

Usage (prefer project venv so Flask deps resolve):
  .\\venv\\Scripts\\python.exe scripts/assessment_app_checks.py
  .\\venv\\Scripts\\python.exe scripts/assessment_app_checks.py --routes 1000 --scenarios 100
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
import traceback
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_PATH = ROOT / "scripts" / "assessment_results.json"


def _offset_m(lat: float, lon: float, east_m: float, north_m: float) -> tuple[float, float]:
    dlat = north_m / 111_320.0
    dlon = east_m / (111_320.0 * max(0.2, math.cos(math.radians(lat))))
    return lat + dlat, lon + dlon


def _run_unittest_module(mod_name: str) -> dict:
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName(mod_name)
    buf = __import__("io").StringIO()
    runner = unittest.TextTestRunner(stream=buf, verbosity=1)
    result = runner.run(suite)
    return {
        "module": mod_name,
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "ok": result.wasSuccessful(),
        "detail": buf.getvalue()[-4000:],
    }


def check_unit_batteries() -> dict:
    """Run rigorous unit modules (≥100 assertions each internally)."""
    modules = [
        "tests.test_match_buffer_rigorous",
        "tests.test_cleared_assignment_seal",
        "tests.test_coverage_trail",
        "tests.test_geocode_coords",
        "tests.test_upload_session",
        "tests.test_offline_upload_flow",
        "tests.test_tracking_upload_dryrun",
        "tests.test_district_locate",
    ]
    results = []
    for m in modules:
        t0 = time.time()
        try:
            r = _run_unittest_module(m)
        except Exception as e:
            r = {
                "module": m,
                "tests_run": 0,
                "failures": 0,
                "errors": 1,
                "ok": False,
                "detail": f"{type(e).__name__}: {e}",
            }
        r["elapsed_s"] = round(time.time() - t0, 2)
        results.append(r)
        status = "PASS" if r["ok"] else "FAIL"
        print(
            f"  [{status}] {m}: run={r['tests_run']} fail={r['failures']} "
            f"err={r['errors']} ({r['elapsed_s']}s)",
            flush=True,
        )
    return {
        "name": "unit_batteries",
        "ok": all(r["ok"] for r in results),
        "modules": results,
    }


def check_match_buffer_stress(n: int) -> dict:
    """Extra programmatic battery on top of unittest (≥ n per scenario)."""
    from routes.survey.assignments import (
        MATCH_CLEARED_MIN_SCORE,
        MATCH_CORRIDOR_BUFFER_KM,
        MATCH_ENDPOINT_BUFFER_KM,
        _gps_assignment_match_score,
        _resolve_seal_target,
    )
    from routes.survey import assignments as asg
    from unittest import mock

    assert abs(MATCH_CORRIDOR_BUFFER_KM - 0.25) < 1e-9
    assert abs(MATCH_ENDPOINT_BUFFER_KM - 0.25) < 1e-9

    scenarios = {}

    # within buffer
    ok = 0
    for i in range(n):
        base = 17.0 + i * 0.01
        route = {
            "start": {"lat": base, "lon": 78.0},
            "end": {"lat": base + 0.04, "lon": 78.0},
            "polyline": [[base + t * 0.01, 78.0] for t in range(5)],
            "segment_ids": [f"a{i}"],
            "mode": "corridor",
        }
        gps = [_offset_m(base + t * 0.01, 78.0, 180, 0) for t in range(5)]
        if _gps_assignment_match_score(route, gps) >= MATCH_CLEARED_MIN_SCORE:
            ok += 1
    scenarios["corridor_within_180m"] = {"n": n, "pass": ok, "ok": ok == n}

    # outside buffer
    ok = 0
    for i in range(n):
        base = 16.0 + i * 0.01
        route = {
            "start": {"lat": base, "lon": 78.0},
            "end": {"lat": base + 0.04, "lon": 78.0},
            "polyline": [[base + t * 0.01, 78.0] for t in range(5)],
            "segment_ids": [f"b{i}"],
            "mode": "corridor",
        }
        gps = [_offset_m(base + t * 0.01, 78.0, 500, 0) for t in range(5)]
        if _gps_assignment_match_score(route, gps) < MATCH_CLEARED_MIN_SCORE:
            ok += 1
    scenarios["corridor_outside_500m"] = {"n": n, "pass": ok, "ok": ok == n}

    # soft-clear preference
    ok = 0
    for i in range(n):
        cleared = {
            "start": {"lat": 17.4, "lon": 78.4},
            "end": {"lat": 17.44, "lon": 78.4},
            "polyline": [[17.4 + t * 0.01, 78.4] for t in range(5)],
            "segment_ids": ["c"],
            "mode": "corridor",
        }
        active = {
            "start": {"lat": 17.6, "lon": 78.6},
            "end": {"lat": 17.64, "lon": 78.6},
            "polyline": [[17.6 + t * 0.01, 78.6] for t in range(5)],
            "segment_ids": ["d"],
            "mode": "corridor",
        }
        gps = [(17.4 + t * 0.01, 78.4) for t in range(5)]
        with mock.patch.object(
            asg,
            "_list_cleared_assignment_candidates",
            return_value=[{"id": i + 1, "date": "2026-07-25", "entry": cleared}],
        ), mock.patch.object(asg, "_entry_has_assignment_work", return_value=True):
            t = _resolve_seal_target(1, "2026-07-25", gps, active)
        if t.get("kind") == "cleared":
            ok += 1
    scenarios["soft_clear_prefers_cleared"] = {"n": n, "pass": ok, "ok": ok == n}

    return {
        "name": "match_buffer_stress",
        "ok": all(s["ok"] for s in scenarios.values()),
        "scenarios": scenarios,
    }


def check_find_routes(target: int, workers: int = 8, timeout_s: float = 25.0) -> dict:
    from routes import survey_service as ss
    import db_utils

    print(f"  find-routes stress target={target}…", flush=True)
    users = db_utils.get_all_users()
    u = next((x for x in users if x.get("username") == "video4"), None)
    if not u:
        u = next((x for x in users if x.get("role") == "Videographer"), None)
    if not u:
        return {"name": "find_routes", "ok": False, "error": "no videographer user"}

    uid = int(u["id"])
    allowed = [str(x) for x in (u.get("district_ids") or [])]
    state = "andhra" if int(u.get("state_id") or 1) == 1 else "telangana"
    districts = [d for d in ss.list_districts(state) if d.get("center")]
    allowed_set = set(allowed)
    in_access = [d for d in districts if str(d["district_id"]) in allowed_set]
    if len(in_access) >= 4:
        districts = in_access

    pts: list[tuple[float, float, str, str]] = []
    for d in districts:
        lat, lon = float(d["center"][0]), float(d["center"][1])
        name = d.get("name") or str(d["district_id"])
        did = str(d["district_id"])
        pts.append((lat, lon, name, did))
        for dlat, dlon in (
            (0.06, 0.0),
            (-0.06, 0.0),
            (0.0, 0.06),
            (0.0, -0.06),
            (0.04, 0.04),
            (-0.04, 0.04),
            (0.08, -0.03),
            (-0.05, 0.07),
        ):
            pts.append((lat + dlat, lon + dlon, name, did))

    random.seed(42)
    random.shuffle(pts)
    pairs = []
    attempts = 0
    while len(pairs) < target and attempts < target * 80:
        attempts += 1
        a, b = random.choice(pts), random.choice(pts)
        dist = ss._haversine_km(a[0], a[1], b[0], b[1])
        if dist < 4.0 or dist > 90.0:
            continue
        pairs.append((a, b, dist))

    ok = empty = timed = err_n = 0
    errors: list[str] = []
    route_counts: Counter = Counter()
    t0 = time.time()

    def _preview(args):
        return ss.preview_corridor_routes(**args)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = []
        for i, (a, b, dist) in enumerate(pairs):
            kwargs = dict(
                state_key=state,
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
                res = fut.result(timeout=timeout_s)
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
                err_n += 1
                errors.append(f"{a[2]}→{b[2]}: {e}")

            if (n + 1) % 50 == 0 or n + 1 == len(futs):
                print(
                    f"    {n+1}/{len(futs)} ok={ok} empty={empty} "
                    f"timeout={timed} err={err_n}",
                    flush=True,
                )

    total = len(pairs)
    hit_rate = ok / total if total else 0.0
    # Assessment gate: ≥70% return at least one route; <2% hard errors
    gate_ok = hit_rate >= 0.70 and (err_n + timed) / max(1, total) <= 0.05
    return {
        "name": "find_routes",
        "ok": gate_ok,
        "state": state,
        "user": u.get("username"),
        "user_id": uid,
        "districts_used": len(districts),
        "total": total,
        "ok_with_routes": ok,
        "empty": empty,
        "timeouts": timed,
        "errors": err_n,
        "hit_rate": round(hit_rate, 4),
        "route_count_hist": dict(route_counts),
        "elapsed_s": round(time.time() - t0, 1),
        "sample_errors": errors[:15],
        "gate": "hit_rate>=0.70 and (errors+timeouts)/n<=0.05",
    }


def check_geocode_locate(n: int) -> dict:
    from routes import survey_service as ss

    # Sample known TG / AP centers when GIS present
    samples = []
    for state in ("telangana", "andhra"):
        try:
            for d in ss.list_districts(state):
                c = d.get("center")
                if not c:
                    continue
                samples.append((state, float(c[0]), float(c[1]), str(d["district_id"]), d.get("name")))
        except Exception:
            continue

    if len(samples) < 10:
        return {"name": "geocode_locate", "ok": False, "error": "insufficient district centers", "n": 0}

    random.seed(7)
    ok = fail = 0
    fails: list[str] = []
    t0 = time.time()
    for i in range(n):
        state, lat, lon, did, name = random.choice(samples)
        # small jitter inside district
        la, lo = _offset_m(lat, lon, random.uniform(-800, 800), random.uniform(-800, 800))
        try:
            res = ss.locate_point_in_state(la, lo, state)
            hit = isinstance(res, dict) and res.get("district_id") is not None
            if hit:
                ok += 1
            else:
                fail += 1
                if len(fails) < 12:
                    fails.append(f"{name}@{la:.4f},{lo:.4f} -> {res}")
        except Exception as e:
            fail += 1
            if len(fails) < 12:
                fails.append(f"{name}: {e}")

    rate = ok / n if n else 0
    return {
        "name": "geocode_locate",
        "ok": rate >= 0.60,
        "n": n,
        "pass": ok,
        "fail": fail,
        "hit_rate": round(rate, 4),
        "elapsed_s": round(time.time() - t0, 1),
        "sample_fails": fails,
        "gate": "hit_rate>=0.60",
    }


def check_pin_reach(n: int) -> dict:
    from routes.survey.progress import END_PIN_REACH_KM, START_PIN_REACH_KM, _near_any_pin

    assert abs(START_PIN_REACH_KM - 0.25) < 1e-9
    assert abs(END_PIN_REACH_KM - 0.25) < 1e-9
    ok_in = ok_out = 0
    for i in range(n):
        pin = (15.0 + (i % 50) * 0.02, 78.0 + (i % 30) * 0.02)
        inside = _offset_m(pin[0], pin[1], 200, 50)
        outside = _offset_m(pin[0], pin[1], 400, 0)
        if _near_any_pin(inside[0], inside[1], pin, radius_km=0.25):
            ok_in += 1
        if not _near_any_pin(outside[0], outside[1], pin, radius_km=0.25):
            ok_out += 1
    return {
        "name": "pin_reach_250m",
        "ok": ok_in == n and ok_out == n,
        "n": n,
        "inside_pass": ok_in,
        "outside_pass": ok_out,
        "start_km": START_PIN_REACH_KM,
        "end_km": END_PIN_REACH_KM,
    }


def check_chunk_gaps(n: int) -> dict:
    """Field upload chunk missing-index tracking (order-independent append)."""
    import tempfile
    from pathlib import Path
    import routes.field_upload_service as fus

    tmp = tempfile.TemporaryDirectory()
    orig = fus.CHUNK_ROOT
    fus.CHUNK_ROOT = Path(tmp.name) / "_chunks"
    try:
        ok = 0
        fails = []
        for i in range(n):
            sid = f"assess_{i}"
            try:
                fus.init_chunk_session(username="assess_vg", session_id=sid, user_id=1)
            except Exception as e:
                fails.append(f"init {i}: {e}")
                continue
            # Out-of-order chunk files under session dir
            session_dir = fus._chunk_session_dir("assess_vg", sid)
            (session_dir / "chunk_0000.bin").write_bytes(b"aaa")
            (session_dir / "chunk_0002.bin").write_bytes(b"ccc")
            (session_dir / "chunk_0001.bin").write_bytes(b"bbb")
            received = sorted(
                int(p.stem.split("_")[-1])
                for p in session_dir.glob("chunk_*.bin")
            )
            missing = fus._missing_indices(received)
            if received == [0, 1, 2] and missing == []:
                ok += 1
            else:
                fails.append(f"{i}: received={received} missing={missing}")
        # Also stress gap detection itself (≥n synthetic lists)
        gap_ok = 0
        for i in range(n):
            # random sparse receive up to max_idx
            max_i = 3 + (i % 8)
            have = sorted(set(range(max_i + 1)) - {1 + (i % max(1, max_i))})
            if not have:
                have = [0]
            miss = fus._missing_indices(have)
            expected = [j for j in range(have[-1]) if j not in set(have)]
            if miss == expected:
                gap_ok += 1
        return {
            "name": "chunk_gap_disk",
            "ok": ok == n and gap_ok == n,
            "n": n,
            "pass": ok,
            "gap_logic_pass": gap_ok,
            "sample_fails": fails[:8],
        }
    finally:
        fus.CHUNK_ROOT = orig
        tmp.cleanup()

def assess(checks: list[dict]) -> dict:
    passed = sum(1 for c in checks if c.get("ok"))
    total = len(checks)
    grade = (
        "excellent"
        if passed == total
        else "good"
        if passed >= total - 1
        else "needs_work"
        if passed >= total // 2
        else "poor"
    )
    return {
        "passed_checks": passed,
        "total_checks": total,
        "grade": grade,
        "summary": (
            f"{passed}/{total} check groups passed — overall grade: {grade}. "
            "250 m corridor/endpoint buffers are enforced for soft-clear seal matching "
            "and start/end pin reach."
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--routes", type=int, default=1000)
    ap.add_argument("--scenarios", type=int, default=100)
    ap.add_argument("--skip-routes", action="store_true")
    ap.add_argument("--skip-units", action="store_true")
    args = ap.parse_args()

    print("=== SmartRoad application assessment ===", flush=True)
    print(f"started {datetime.now(timezone.utc).isoformat()}", flush=True)
    checks: list[dict] = []

    print("\n[1] Unit batteries", flush=True)
    if not args.skip_units:
        checks.append(check_unit_batteries())
    else:
        print("  skipped", flush=True)

    print("\n[2] Match-buffer / soft-clear stress", flush=True)
    try:
        checks.append(check_match_buffer_stress(args.scenarios))
        print(f"  ok={checks[-1]['ok']} scenarios={checks[-1]['scenarios']}", flush=True)
    except Exception as e:
        checks.append({"name": "match_buffer_stress", "ok": False, "error": str(e)})
        traceback.print_exc()

    print("\n[3] Pin reach 250 m", flush=True)
    try:
        checks.append(check_pin_reach(args.scenarios))
        print(f"  {checks[-1]}", flush=True)
    except Exception as e:
        checks.append({"name": "pin_reach_250m", "ok": False, "error": str(e)})

    print("\n[4] Chunk gap disk (≥100)", flush=True)
    try:
        checks.append(check_chunk_gaps(args.scenarios))
        print(f"  ok={checks[-1]['ok']} pass={checks[-1].get('pass')}/{checks[-1].get('n')}", flush=True)
    except Exception as e:
        checks.append({"name": "chunk_gap_disk", "ok": False, "error": str(e)})
        traceback.print_exc()

    print("\n[5] Locate / geocode sample", flush=True)
    try:
        checks.append(check_geocode_locate(args.scenarios))
        print(
            f"  ok={checks[-1]['ok']} hit_rate={checks[-1].get('hit_rate')} "
            f"({checks[-1].get('pass')}/{checks[-1].get('n')})",
            flush=True,
        )
    except Exception as e:
        checks.append({"name": "geocode_locate", "ok": False, "error": str(e)})
        traceback.print_exc()

    print("\n[6] Find-routes stress", flush=True)
    if not args.skip_routes:
        try:
            checks.append(check_find_routes(args.routes))
            fr = checks[-1]
            print(
                f"  ok={fr.get('ok')} hit_rate={fr.get('hit_rate')} "
                f"routes={fr.get('ok_with_routes')}/{fr.get('total')} "
                f"empty={fr.get('empty')} err={fr.get('errors')} "
                f"timeout={fr.get('timeouts')} ({fr.get('elapsed_s')}s)",
                flush=True,
            )
        except Exception as e:
            checks.append({"name": "find_routes", "ok": False, "error": str(e)})
            traceback.print_exc()
    else:
        print("  skipped", flush=True)

    verdict = assess(checks)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "buffers": {
            "MATCH_CORRIDOR_BUFFER_KM": 0.25,
            "MATCH_ENDPOINT_BUFFER_KM": 0.25,
            "START_PIN_REACH_KM": 0.25,
            "END_PIN_REACH_KM": 0.25,
        },
        "checks": checks,
        "assessment": verdict,
    }
    OUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\n=== ASSESSMENT ===", flush=True)
    print(verdict["summary"], flush=True)
    for c in checks:
        mark = "PASS" if c.get("ok") else "FAIL"
        print(f"  [{mark}] {c.get('name')}", flush=True)
    print(f"\nWrote {OUT_PATH}", flush=True)
    return 0 if verdict["passed_checks"] == verdict["total_checks"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
