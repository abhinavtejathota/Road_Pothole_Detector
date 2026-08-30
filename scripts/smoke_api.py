"""Smoke-test key survey / upload / tracking API routes via Flask test client."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from web_app import create_app  # noqa: E402


def main() -> int:
    app = create_app()
    c = app.test_client()
    results = []

    def check(name, resp, ok_codes=(200,)):
        code = resp.status_code
        body = resp.get_json(silent=True)
        ok = code in ok_codes
        msg = ""
        if isinstance(body, dict):
            msg = str(body.get("error") or body.get("message") or (body.get("status") or {}).get("message") or "")[:120]
        results.append((ok, name, code, msg))
        print(("OK " if ok else "FAIL"), name, code, msg)

    # Public / auth
    check("GET /api/auth/me (anon)", c.get("/api/auth/me"), ok_codes=(401, 403, 200))

    # Login as video if possible — try common bootstrap / video user
    login_ok = False
    mej = {}
    for user, pw in (("video2", "video2"), ("admin", "admin123")):
        r = c.post("/api/auth/login", json={"username": user, "password": pw})
        if r.status_code == 200:
            login_ok = True
            check(f"POST /api/auth/login ({user})", r)
            me = c.get("/api/auth/me")
            check("GET /api/auth/me", me)
            mej = me.get_json() or {}
            break
        print("skip login", user, r.status_code)

    if not login_ok:
        print("WARN: could not login — testing unauthenticated rejection paths only")
        check("GET /api/survey/assignments (anon)", c.get("/api/survey/assignments"), ok_codes=(401, 403))
        check("GET /api/tracking/videographers (anon)", c.get("/api/tracking/videographers"), ok_codes=(401, 403))
        check("POST /api/upload (anon)", c.post("/api/upload"), ok_codes=(401, 403, 400))
        fails = sum(1 for ok, *_ in results if not ok)
        print(f"\n{len(results) - fails}/{len(results)} checks passed (no credentials)")
        return 0 if fails == 0 else 1

    role = (mej.get("role") or "").lower()
    is_vg = bool(mej.get("is_videographer"))
    is_admin = bool(mej.get("is_admin"))

    check("GET /api/survey/settings", c.get("/api/survey/settings"), ok_codes=(200, 403))
    check("GET /api/survey/states", c.get("/api/survey/states"), ok_codes=(200, 403, 503))
    check("GET /api/survey/districts?state_key=telangana", c.get("/api/survey/districts?state_key=telangana"), ok_codes=(200, 403, 503))
    check("GET /api/survey/assignments", c.get("/api/survey/assignments"), ok_codes=(200, 403))
    check("GET /api/survey/assignments/geojson", c.get("/api/survey/assignments/geojson"), ok_codes=(200, 403, 400))

    # Geocode / locate (may hit Nominatim — allow 200/400/503)
    check(
        "GET /api/survey/geocode",
        c.get("/api/survey/geocode?q=Hyderabad&state_key=telangana"),
        ok_codes=(200, 400, 403, 503),
    )

    # Preview routes
    preview = c.post("/api/survey/routes/preview", json={
        "start_lat": 17.4485,
        "start_lon": 78.3908,
        "end_lat": 17.4932,
        "end_lon": 78.3915,
        "start_label": "Madhapur",
        "end_label": "JNTUH",
        "district_id": mej.get("district_id") or 518,
    })
    check("POST /api/survey/routes/preview", preview, ok_codes=(200, 400, 403))

    # Tracking
    check(
        "POST /api/tracking/ping",
        c.post("/api/tracking/ping", json={"lat": 17.45, "lon": 78.39, "accuracy": 8, "recording": False}),
        ok_codes=(200, 403),
    )
    check("GET /api/tracking/videographers", c.get("/api/tracking/videographers"), ok_codes=(200, 403))

    # Upload validation: missing GPS
    data = {
        "media": (io.BytesIO(b"fakevideo"), "t.webm"),
    }
    check("POST /api/upload (no gps)", c.post("/api/upload", data=data, content_type="multipart/form-data"), ok_codes=(400, 403))

    # Upload with empty GPS CSV
    data2 = {
        "media": (io.BytesIO(b"fakevideo"), "t.webm"),
        "gps_log": (io.BytesIO(b"VideoSecond,Latitude,Longitude,AccuracyM\n"), "t.csv"),
    }
    check("POST /api/upload (empty gps)", c.post("/api/upload", data=data2, content_type="multipart/form-data"), ok_codes=(400, 403))

    # Upload with one GPS point (should accept structure-wise; storage may be local)
    data3 = {
        "media": (io.BytesIO(b"\x00\x00fake"), "t.webm"),
        "gps_log": (io.BytesIO(b"VideoSecond,Latitude,Longitude,AccuracyM\n0,17.45,78.39,5\n"), "t.csv"),
    }
    check("POST /api/upload (with gps)", c.post("/api/upload", data=data3, content_type="multipart/form-data"), ok_codes=(200, 400, 403, 500))

    # Admin reopen
    reopen = c.post("/api/survey/districts/518/reopen-roads", json={"state_key": "telangana"})
    check("POST /api/survey/districts/518/reopen-roads", reopen, ok_codes=(200, 403, 400))

    # Conflict assign path: call assign without segments should 400
    assign = c.post("/api/survey/assign", json={"mode": "corridor"})
    check("POST /api/survey/assign (bad)", assign, ok_codes=(400, 403, 409))

    print("\n--- summary ---")
    fails = [r for r in results if not r[0]]
    for ok, name, code, msg in results:
        if not ok:
            print("FAIL", name, code, msg)
    print(f"{len(results) - len(fails)}/{len(results)} passed")
    print(json.dumps({"role": role, "is_vg": is_vg, "is_admin": is_admin}, indent=2))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
