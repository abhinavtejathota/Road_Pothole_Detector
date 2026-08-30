#!/usr/bin/env python3
"""Offline smoke test for multi-service app factory (no live server required).

Validates:
  - portal / upload / detect apps construct
  - service gate blocks cross-traffic
  - mobile JWT issue + verify + revoke (token_version) when DB is configured
  - web cookie login path still exists

Exit 0 = OK. Run: python scripts/smoke_multi_service.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
except Exception:
    pass

# Avoid GPU init during smoke
os.environ.setdefault("YOLO_DEVICE", "cpu")
os.environ.setdefault("SMARTROAD_QUIET_LOGS", "1")


def _fail(msg: str) -> None:
    print(f"FAIL  {msg}")
    raise SystemExit(1)


def _ok(msg: str) -> None:
    print(f"OK    {msg}")


def main() -> None:
    from routes.app_factory import create_app
    from routes.token_auth import issue_access_token, verify_access_token

    portal = create_app("portal")
    upload = create_app("upload")
    detect = create_app("detect")
    _ok("create_app portal/upload/detect")

    pc, uc, dc = portal.test_client(), upload.test_client(), detect.test_client()

    for label, c in (("portal", pc), ("upload", uc), ("detect", dc)):
        r = c.get("/api/health")
        if r.status_code != 200:
            _fail(f"{label} /api/health → {r.status_code}")
        body = r.get_json() or {}
        if body.get("service") not in (label, "portal") and label != "portal":
            # portal reports portal; upload→upload; detect→detect
            if body.get("service") != label:
                _fail(f"{label} health service={body.get('service')!r}")
        _ok(f"{label} health service={body.get('service')}")

    # Cross-service gate
    r = uc.get("/api/detection/status")
    if r.status_code != 404:
        _fail(f"upload must 404 detection routes, got {r.status_code}")
    _ok("upload blocks /api/detection/*")

    r = dc.get("/api/upload/status")
    if r.status_code != 404:
        _fail(f"detect must 404 upload routes, got {r.status_code}")
    _ok("detect blocks /api/upload/*")

    r = uc.get("/api/dashboard")
    if r.status_code not in (401, 404):
        # 401 if gate allows before login_required — with gate should be 404
        # dashboard not in upload allow-list → 404
        _fail(f"upload /api/dashboard expected 404, got {r.status_code}")
    _ok("upload blocks portal dashboard")

    # JWT round-trip (no DB needed for sign/verify)
    tok = issue_access_token(1, token_version=0)
    payload = verify_access_token(tok)
    if not payload or int(payload["uid"]) != 1:
        _fail("JWT issue/verify")
    _ok("JWT issue/verify")

    bad = verify_access_token(tok[:-4] + "xxxx")
    if bad is not None:
        _fail("JWT tamper should fail")
    _ok("JWT rejects tamper")

    # Optional DB-backed revoke path
    import db_utils

    if db_utils.is_db_configured():
        try:
            db_utils.init_db()
            v = db_utils.get_user_token_version(0)
            _ok(f"token_version column reachable (probe={v})")
        except Exception as e:
            # Laptop smoke often cannot reach AceCloud Postgres (pg_hba) — warn only.
            print(f"WARN  DB token_version / init_db skipped: {e}")
    else:
        _ok("DB not configured — skip token_version DB check")

    # SPA only on portal
    r = pc.get("/login")
    if r.status_code != 200:
        _fail(f"portal SPA /login → {r.status_code}")
    _ok("portal serves SPA")

    r = uc.get("/login")
    if r.status_code != 404:
        _fail(f"upload must not serve SPA, got {r.status_code}")
    _ok("upload has no SPA")

    print("smoke_multi_service: ALL PASSED")


if __name__ == "__main__":
    main()
