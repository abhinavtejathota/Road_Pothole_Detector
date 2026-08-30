"""Broad API smoke: every major /api group via Flask test client."""
from __future__ import annotations

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
        body = resp.get_json(silent=True) or {}
        ok = code in ok_codes
        err = ""
        if isinstance(body, dict):
            err = str(body.get("error") or body.get("message") or "")[:100]
        results.append((ok, name, code, err))
        print(("OK  " if ok else "FAIL"), name, "->", code, err)

    # Public
    check("GET /api/health", c.get("/api/health"), (200,))
    check("GET /api/auth/me anon", c.get("/api/auth/me"), (401, 403, 200))

    login_user = None
    for user, pw in (("admin", "admin123"), ("video4", "video4"), ("video3", "video3")):
        r = c.post("/api/auth/login", json={"username": user, "password": pw})
        if r.status_code == 200:
            login_user = user
            check(f"POST login {user}", r)
            break
        print("skip login", user, r.status_code)

    if not login_user:
        print("No credentials — auth-only checks done")
        fails = sum(1 for ok, *_ in results if not ok)
        return 0 if fails == 0 else 1

    me = c.get("/api/auth/me")
    check("GET /api/auth/me", me)
    mej = me.get_json() or {}
    is_admin = bool(mej.get("is_admin"))
    is_vg = bool(mej.get("is_videographer"))

    # Dashboard / users (admin)
    check("GET /api/dashboard", c.get("/api/dashboard"), (200, 403))
    check("GET /api/dashboard/map-data", c.get("/api/dashboard/map-data"), (200, 403, 400))
    check("GET /api/users", c.get("/api/users"), (200, 403))

    # Vendors / tasks
    check("GET /api/vendors", c.get("/api/vendors"), (200, 403))
    check("GET /api/tasks", c.get("/api/tasks"), (200, 403))

    # Validation
    check("GET /api/validate/queue", c.get("/api/validate/queue"), (200, 403, 404))

    # Survey
    check("GET /api/survey/settings", c.get("/api/survey/settings"), (200, 403))
    check("GET /api/survey/states", c.get("/api/survey/states"), (200, 403, 503))
    check(
        "GET /api/survey/districts",
        c.get("/api/survey/districts?state_key=andhra"),
        (200, 403, 503),
    )
    check("GET /api/survey/assignments", c.get("/api/survey/assignments"), (200, 403))
    check(
        "GET /api/survey/assignments/geojson",
        c.get("/api/survey/assignments/geojson"),
        (200, 403, 400),
    )
    check(
        "GET /api/survey/geocode",
        c.get("/api/survey/geocode?q=Hyderabad&state_key=telangana"),
        (200, 400, 403, 503),
    )
    check(
        "POST /api/survey/my-districts empty",
        c.patch("/api/survey/my-districts", json={"district_ids": []}),
        (400, 403),
    )
    check(
        "POST /api/survey/assignments/clear",
        c.post("/api/survey/assignments/clear", json={}),
        (200, 400, 403),
    )

    # Detection / model bench / complaints / reports (admin)
    check("GET /api/detection/status", c.get("/api/detection/status"), (200, 403))
    check("GET /api/model-bench/models", c.get("/api/model-bench/models"), (200, 403))
    check("GET /api/complaints", c.get("/api/complaints"), (200, 403))
    check("GET /api/reports", c.get("/api/reports"), (200, 403))

    # Tracking
    check(
        "POST /api/tracking/ping",
        c.post(
            "/api/tracking/ping",
            json={"lat": 17.45, "lon": 78.39, "accuracy": 8, "recording": False},
        ),
        (200, 403),
    )
    check("GET /api/tracking/videographers", c.get("/api/tracking/videographers"), (200, 403))

    # Upload status
    check("GET /api/upload/status", c.get("/api/upload/status"), (200, 403, 404))

    # Bad assign
    check(
        "POST /api/survey/assign bad",
        c.post("/api/survey/assign", json={"mode": "corridor"}),
        (400, 403, 409),
    )

    # Logout
    check("POST /api/auth/logout", c.post("/api/auth/logout"), (200, 302, 401))

    # Module import sanity (modularization shims)
    try:
        import db_utils  # noqa: F401
        import pothole_detector  # noqa: F401
        from routes import survey_service, tracking_service, detection_service  # noqa: F401
        from routes.api import api_bp  # noqa: F401
        assert hasattr(db_utils, "_get_conn")
        assert hasattr(survey_service, "_load_state")
        assert hasattr(tracking_service, "_empty_by_class")
        assert hasattr(detection_service, "run_detection")
        assert api_bp.name == "api"
        print("OK   import shims -> packages load")
        results.append((True, "import shims", 200, ""))
    except Exception as e:
        print("FAIL import shims", e)
        results.append((False, "import shims", 500, str(e)))

    fails = sum(1 for ok, *_ in results if not ok)
    print(f"\n{len(results) - fails}/{len(results)} checks passed (admin={is_admin} vg={is_vg})")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
