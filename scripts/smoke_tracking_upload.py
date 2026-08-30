#!/usr/bin/env python3
"""Dry-run smoke: tracking ping + chunked upload lifecycle (no real S3 finalize).

Exercises the same paths the mobile Capture screen uses:
  login (cookie + optional JWT) → ping → session init → chunk (+ lat/lon) →
  finalize GPS gate → discard.

Does NOT call ffmpeg/S3 assemble (discard instead of finalize success).

Usage:
  python scripts/smoke_tracking_upload.py
  python scripts/smoke_tracking_upload.py --jwt
"""
from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from web_app import create_app  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--jwt", action="store_true", help="Use mobile JWT (X-Client: mobile) instead of cookie")
    args = p.parse_args()

    app = create_app()
    c = app.test_client()
    results: list[tuple[bool, str, int, str]] = []

    def check(name: str, resp, ok_codes=(200,)):
        code = resp.status_code
        body = resp.get_json(silent=True) or {}
        ok = code in ok_codes
        err = ""
        if isinstance(body, dict):
            st = body.get("status")
            if isinstance(st, dict):
                err = str(st.get("message") or "")[:120]
            else:
                err = str(body.get("error") or body.get("message") or "")[:120]
        results.append((ok, name, code, err))
        print(("OK  " if ok else "FAIL"), name, "→", code, err or "")
        return body

    # --- auth ---
    login_headers = {"X-Client": "mobile"} if args.jwt else {}
    token = None
    username = None
    me = {}
    for user, pw in (("video", "video"), ("video2", "video2"), ("video4", "video4")):
        r = c.post("/api/auth/login", json={"username": user, "password": pw}, headers=login_headers)
        if r.status_code != 200:
            print("skip login", user, r.status_code)
            continue
        body = check(f"login {user}{' JWT' if args.jwt else ''}", r)
        username = user
        if args.jwt:
            token = body.get("access_token")
            me = body
            if not token:
                print("FAIL: mobile login missing access_token")
                return 1
        else:
            me = c.get("/api/auth/me").get_json() or {}
            check("GET /api/auth/me", c.get("/api/auth/me"))
        break

    if not username:
        print("No videographer credentials — auth rejection only")
        check("ping anon", c.post("/api/tracking/ping", json={"lat": 1, "lon": 1}), (401, 403))
        fails = sum(1 for ok, *_ in results if not ok)
        return 0 if fails == 0 else 1

    if not me.get("is_videographer"):
        print("Logged in but not videographer")
        return 1

    headers = {}
    if token:
        headers = {"Authorization": f"Bearer {token}", "X-Client": "mobile"}

    # --- tracking keepalive ---
    check(
        "POST /api/tracking/ping keepalive",
        c.post(
            "/api/tracking/ping",
            json={"lat": 17.4483, "lon": 78.3915, "accuracy": 10, "recording": False},
            headers=headers,
        ),
        (200,),
    )

    # --- upload status ---
    check("GET /api/upload/status", c.get("/api/upload/status", headers=headers), (200,))

    # --- classic upload GPS gates ---
    check(
        "POST /api/upload no gps → 400",
        c.post(
            "/api/upload",
            data={"media": (io.BytesIO(b"fake"), "t.webm")},
            content_type="multipart/form-data",
            headers=headers,
        ),
        (400,),
    )

    # --- chunk session dry lifecycle ---
    import routes.field_upload_service as fus
    import tempfile

    tmp = tempfile.TemporaryDirectory()
    orig_root = fus.CHUNK_ROOT
    fus.CHUNK_ROOT = Path(tmp.name) / "_chunks"
    fus.CHUNK_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        sid = f"smoke_cap_{int(time.time())}"
        check(
            "POST /api/upload/session/init",
            c.post("/api/upload/session/init", json={"capture_session_id": sid}, headers=headers),
        )

        class _InlineThread:
            def __init__(self, target=None, daemon=None, name=None):
                self._target = target

            def start(self):
                if self._target:
                    self._target()

        fake = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 256
        with mock.patch("threading.Thread", _InlineThread):
            body = check(
                "POST /api/upload/session/chunk 0 (+lat/lon)",
                c.post(
                    "/api/upload/session/chunk",
                    data={
                        "capture_session_id": sid,
                        "chunk_index": "0",
                        "lat": "17.4483",
                        "lon": "78.3915",
                        "accuracy": "8",
                        "media": (io.BytesIO(fake), "m0.mp4"),
                    },
                    content_type="multipart/form-data",
                    headers=headers,
                ),
            )
        if body.get("chunk_index") != 0:
            results.append((False, "chunk_index==0", 0, str(body.get("chunk_index"))))
            print("FAIL chunk_index==0")

        with mock.patch("threading.Thread", _InlineThread):
            body2 = check(
                "POST chunk 2 out-of-order",
                c.post(
                    "/api/upload/session/chunk",
                    data={
                        "capture_session_id": sid,
                        "chunk_index": "2",
                        "media": (io.BytesIO(fake), "m2.mp4"),
                    },
                    content_type="multipart/form-data",
                    headers=headers,
                ),
            )
        missing = body2.get("missing_indices") or []
        if missing != [1]:
            results.append((False, "missing_indices=[1]", 0, str(missing)))
            print("FAIL missing_indices", missing)
        else:
            print("OK   missing_indices=[1]")

        check(
            "POST finalize without GPS → 400",
            c.post(
                "/api/upload/session/finalize",
                data={"capture_session_id": sid},
                content_type="multipart/form-data",
                headers=headers,
            ),
            (400,),
        )

        # Finalize accept path — mock queue so we never touch ffmpeg/S3
        gps_csv = "timestamp,lat,lon\n2026-07-24T12:00:00Z,17.4483,78.3915\n"
        with mock.patch.object(
            fus,
            "queue_chunk_finalize",
            return_value={
                "ok": True,
                "queued": True,
                "capture_session_id": sid,
                "status": {"kind": "ok", "message": "queued (dry-run)"},
            },
        ) as q:
            fin = check(
                "POST finalize with GPS (queued dry-run)",
                c.post(
                    "/api/upload/session/finalize",
                    data={
                        "capture_session_id": sid,
                        "gps_log": (io.BytesIO(gps_csv.encode()), "gps.csv"),
                    },
                    content_type="multipart/form-data",
                    headers=headers,
                ),
                (200, 202),
            )
            if not q.called:
                # Some paths may call differently — still OK if HTTP accepted
                print("WARN queue_chunk_finalize not called — check handler wiring")

        # If finalize set queued status, discard may be blocked — reset meta for discard test
        # Use a fresh session for discard happy path
        sid2 = f"smoke_disc_{int(time.time())}"
        check(
            "init for discard",
            c.post("/api/upload/session/init", json={"capture_session_id": sid2}, headers=headers),
        )
        with mock.patch("threading.Thread", _InlineThread):
            check(
                "chunk before discard",
                c.post(
                    "/api/upload/session/chunk",
                    data={
                        "capture_session_id": sid2,
                        "chunk_index": "0",
                        "media": (io.BytesIO(fake), "d0.mp4"),
                    },
                    content_type="multipart/form-data",
                    headers=headers,
                ),
            )
        check(
            "POST /api/upload/session/discard",
            c.post("/api/upload/session/discard", json={"capture_session_id": sid2}, headers=headers),
        )
        st = fus.chunk_session_status(username=username, session_id=sid2)
        if st.get("exists"):
            results.append((False, "session gone after discard", 0, str(st)))
            print("FAIL session still exists after discard")
        else:
            print("OK   session removed after discard")

        check(
            "POST /api/tracking/discard-session",
            c.post(
                "/api/tracking/discard-session",
                json={"capture_session_id": sid2},
                headers=headers,
            ),
            (200,),
        )
    finally:
        fus.CHUNK_ROOT = orig_root
        tmp.cleanup()

    # Admin list (optional)
    if me.get("is_admin"):
        check("GET /api/tracking/videographers", c.get("/api/tracking/videographers", headers=headers))

    fails = sum(1 for ok, *_ in results if not ok)
    print()
    print(f"{len(results) - fails}/{len(results)} checks passed")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
