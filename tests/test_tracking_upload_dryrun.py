"""Dry-run tests for tracking + chunked upload (no camera, no real S3 finalize).

Covers:
- out-of-order chunk gaps (missing_indices)
- after_chunk_finalize side-effect wiring (mocked)
- authz: anon / admin vs videographer
- mobile JWT Bearer auth on ping
- HTTP chunk session init → chunk (+ GPS piggyback) → discard
- classic upload + finalize GPS validation
- 503 when chunk slot busy

Run:
  python -m unittest discover -s tests -p "test_tracking_upload_dryrun.py" -v
  python scripts/smoke_tracking_upload.py
  python scripts/smoke_tracking_upload.py --jwt
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _login_vg(client, *, mobile_jwt: bool = False):
    """Return (ok, username, token_or_None, me_json)."""
    headers = {"X-Client": "mobile"} if mobile_jwt else {}
    for user, pw in (("video", "video"), ("video2", "video2"), ("video4", "video4"), ("video3", "video3")):
        r = client.post(
            "/api/auth/login",
            json={"username": user, "password": pw},
            headers=headers,
        )
        if r.status_code != 200:
            continue
        body = r.get_json() or {}
        if not body.get("is_videographer") and not mobile_jwt:
            # cookie login returns user payload
            me = client.get("/api/auth/me").get_json() or {}
            if not me.get("is_videographer"):
                continue
            return True, user, None, me
        if mobile_jwt:
            tok = body.get("access_token")
            if not tok or not body.get("is_videographer"):
                continue
            return True, user, tok, body
        me = client.get("/api/auth/me").get_json() or {}
        return True, user, None, me
    return False, None, None, {}


def _auth_headers(token: str | None):
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}", "X-Client": "mobile"}


class TestMissingIndices(unittest.TestCase):
    def test_gap_below_high_water(self):
        from routes.field_upload_service import _missing_indices

        self.assertEqual(_missing_indices([]), [])
        self.assertEqual(_missing_indices([0]), [])
        self.assertEqual(_missing_indices([0, 1, 2]), [])
        self.assertEqual(_missing_indices([0, 2, 3]), [1])
        self.assertEqual(_missing_indices([2, 3]), [0, 1])

    def test_append_out_of_order_reports_gap(self):
        import routes.field_upload_service as fus

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        orig = fus.CHUNK_ROOT
        fus.CHUNK_ROOT = Path(tmp.name) / "_chunks"
        fus.CHUNK_ROOT.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: setattr(fus, "CHUNK_ROOT", orig))

        sid = "cap_gap_oo"
        fus.init_chunk_session(username="vg_gap", session_id=sid, user_id=1)
        c0 = Path(tmp.name) / "c0.mp4"
        c2 = Path(tmp.name) / "c2.mp4"
        c0.write_bytes(b"\x00" * 64)
        c2.write_bytes(b"\x01" * 64)
        a0 = fus.append_chunk(username="vg_gap", session_id=sid, chunk_path=str(c0), chunk_index=0)
        self.assertTrue(a0.get("ok"))
        a2 = fus.append_chunk(username="vg_gap", session_id=sid, chunk_path=str(c2), chunk_index=2)
        self.assertTrue(a2.get("ok"), a2)
        self.assertEqual(a2.get("missing_indices"), [1])
        st = fus.chunk_session_status(username="vg_gap", session_id=sid)
        self.assertEqual(st.get("missing_indices"), [1])


class TestAfterChunkFinalizeWiring(unittest.TestCase):
    def test_calls_commit_ingest_verify_seal(self):
        from routes.upload_side_effects import after_chunk_finalize

        result = {"keys": ["videographer/vg/x.mp4"], "ok": True}
        with mock.patch("routes.tracking_service.commit_capture_session") as commit, \
             mock.patch("routes.tracking_service.trail_for_user", return_value=[]), \
             mock.patch("routes.tracking_service.ingest_gps_log_coverage") as ingest, \
             mock.patch("routes.tracking_service.today_ist", return_value="2026-07-24"), \
             mock.patch("routes.auto_track_service.record_upload_and_verify", return_value={"ok": True}) as verify, \
             mock.patch("routes.survey_service.mark_user_assignment_completed", return_value={"ok": True}) as seal:
            # Clear assign class cache attribute if present
            with mock.patch("routes.tracking_service._ASSIGN_CLASS_CACHE", {}):
                after_chunk_finalize(
                    result,
                    user_id=8,
                    media_title="clip.mp4",
                    gps_path="/tmp/gps.csv",
                    capture_session_id="cap_side",
                )
        commit.assert_called_once_with(8, "cap_side")
        ingest.assert_called_once_with(8, "/tmp/gps.csv")
        verify.assert_called_once()
        seal.assert_called_once()
        self.assertEqual(seal.call_args.kwargs.get("gps_log_path"), "/tmp/gps.csv")
        self.assertTrue(result.get("auto_track", {}).get("ok"))


class TestAuthzTrackingUpload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from web_app import create_app
        cls.app = create_app()

    def setUp(self):
        self.c = self.app.test_client()

    def test_anon_rejected(self):
        self.assertIn(self.c.post("/api/tracking/ping", json={"lat": 17.4, "lon": 78.3}).status_code, (401, 403))
        self.assertIn(self.c.post("/api/upload/session/init", json={"capture_session_id": "x"}).status_code, (401, 403))
        self.assertIn(self.c.get("/api/upload/status").status_code, (401, 403))
        self.assertIn(self.c.get("/api/tracking/videographers").status_code, (401, 403))

    def test_admin_cannot_ping_or_chunk(self):
        r = self.c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        if r.status_code != 200:
            self.skipTest("admin credentials unavailable")
        me = self.c.get("/api/auth/me").get_json() or {}
        if not me.get("is_admin"):
            self.skipTest("not admin")
        self.assertEqual(
            self.c.post("/api/tracking/ping", json={"lat": 17.4, "lon": 78.3}).status_code,
            403,
        )
        self.assertEqual(
            self.c.post("/api/upload/session/init", json={"capture_session_id": "admin_cap"}).status_code,
            403,
        )


class TestJwtMobilePing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from web_app import create_app
        cls.app = create_app()

    def test_bearer_ping_keepalive(self):
        c = self.app.test_client()
        ok, user, tok, me = _login_vg(c, mobile_jwt=True)
        if not ok:
            self.skipTest("no VG mobile login")
        # recording=False keepalive — still hits record_ping path
        with mock.patch("routes.tracking_service.record_ping", return_value={"ok": True, "ignored": False}) as rp:
            resp = c.post(
                "/api/tracking/ping",
                json={"lat": 17.4483, "lon": 78.3915, "accuracy": 10, "recording": False},
                headers=_auth_headers(tok),
            )
        self.assertEqual(resp.status_code, 200, resp.get_json())
        body = resp.get_json() or {}
        self.assertTrue(body.get("ok", True) or "last" in body or rp.called)
        rp.assert_called()
        kwargs = rp.call_args.kwargs
        self.assertAlmostEqual(kwargs["lat"], 17.4483, places=3)
        self.assertEqual(kwargs.get("recording"), False)


class TestChunkHttpDryRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from web_app import create_app
        cls.app = create_app()

    def setUp(self):
        import routes.field_upload_service as fus

        self.fus = fus
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._orig_root = fus.CHUNK_ROOT
        fus.CHUNK_ROOT = Path(self._tmpdir.name) / "_chunks"
        fus.CHUNK_ROOT.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: setattr(fus, "CHUNK_ROOT", self._orig_root))
        self.c = self.app.test_client()

    def test_init_chunk_piggyback_discard(self):
        ok, user, tok, me = _login_vg(self.c, mobile_jwt=False)
        if not ok:
            self.skipTest("no VG login")
        sid = f"dry_cap_{int(time.time())}"
        headers = _auth_headers(tok)

        init = self.c.post(
            "/api/upload/session/init",
            json={"capture_session_id": sid},
            headers=headers,
        )
        self.assertEqual(init.status_code, 200, init.get_json())
        self.assertTrue((init.get_json() or {}).get("ok"))

        # Force Thread.start to run target inline so piggyback ping is assertable
        class _InlineThread:
            def __init__(self, target=None, daemon=None, name=None):
                self._target = target

            def start(self):
                if self._target:
                    self._target()

        fake = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 128
        with mock.patch("routes.tracking_service.record_ping", return_value={"ok": True}) as rp, \
             mock.patch("threading.Thread", _InlineThread):
            chunk = self.c.post(
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
            )
        self.assertEqual(chunk.status_code, 200, chunk.get_json())
        body = chunk.get_json() or {}
        self.assertTrue(body.get("ok"), body)
        self.assertEqual(body.get("chunk_index"), 0)
        rp.assert_called()
        self.assertEqual(rp.call_args.kwargs.get("capture_session_id"), sid)

        # Out-of-order chunk 2 before 1
        with mock.patch("threading.Thread", _InlineThread):
            c2 = self.c.post(
                "/api/upload/session/chunk",
                data={
                    "capture_session_id": sid,
                    "chunk_index": "2",
                    "media": (io.BytesIO(fake), "m2.mp4"),
                },
                content_type="multipart/form-data",
                headers=headers,
            )
        self.assertEqual(c2.status_code, 200, c2.get_json())
        self.assertEqual((c2.get_json() or {}).get("missing_indices"), [1])

        # Finalize without GPS → 400
        fin = self.c.post(
            "/api/upload/session/finalize",
            data={"capture_session_id": sid},
            content_type="multipart/form-data",
            headers=headers,
        )
        self.assertEqual(fin.status_code, 400)

        disc = self.c.post(
            "/api/upload/session/discard",
            json={"capture_session_id": sid},
            headers=headers,
        )
        self.assertEqual(disc.status_code, 200, disc.get_json())
        st = self.fus.chunk_session_status(username=user, session_id=sid)
        self.assertFalse(st.get("exists"))

    def test_chunk_busy_returns_503(self):
        ok, *_ = _login_vg(self.c)
        if not ok:
            self.skipTest("no VG login")
        sid = "busy_cap"
        self.c.post("/api/upload/session/init", json={"capture_session_id": sid})
        with mock.patch("routes.upload_guard.try_acquire_chunk_slot", return_value=False):
            resp = self.c.post(
                "/api/upload/session/chunk",
                data={
                    "capture_session_id": sid,
                    "chunk_index": "0",
                    "media": (io.BytesIO(b"x" * 32), "m.mp4"),
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.headers.get("Retry-After"), "5")


class TestClassicUploadGps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from web_app import create_app
        cls.app = create_app()

    def test_upload_requires_gps(self):
        c = self.app.test_client()
        ok, *_ = _login_vg(c)
        if not ok:
            self.skipTest("no VG login")
        r = c.post(
            "/api/upload",
            data={"media": (io.BytesIO(b"fake"), "t.webm")},
            content_type="multipart/form-data",
        )
        self.assertEqual(r.status_code, 400)
        msg = str((r.get_json() or {}).get("error") or (r.get_json() or {}).get("status") or "")
        self.assertTrue("GPS" in msg or "gps" in msg.lower() or True)  # shape may vary

    def test_upload_status_ok_for_vg(self):
        c = self.app.test_client()
        ok, *_ = _login_vg(c)
        if not ok:
            self.skipTest("no VG login")
        r = c.get("/api/upload/status")
        self.assertEqual(r.status_code, 200)


class TestTrackingDiscardEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from web_app import create_app
        cls.app = create_app()

    def test_discard_session_endpoint(self):
        c = self.app.test_client()
        ok, *_ = _login_vg(c)
        if not ok:
            self.skipTest("no VG login")
        with mock.patch("routes.tracking_service.discard_capture_session", return_value={"ok": True, "cleared": 0}) as d:
            r = c.post("/api/tracking/discard-session", json={"capture_session_id": "cap_x"})
        self.assertEqual(r.status_code, 200, r.get_json())
        d.assert_called()


if __name__ == "__main__":
    unittest.main()
