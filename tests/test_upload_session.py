"""Smoke tests for upload stream + chunk session disk path (no live DB/S3).

Run:
  python -m pytest tests/test_upload_session.py -q
or:
  python tests/test_upload_session.py
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestUploadStream(unittest.TestCase):
    def test_werkzeug_empty_without_content_length_safe_fallback(self):
        from werkzeug.wsgi import get_input_stream

        payload = b"MINUTE_CLIP_BYTES_12345"
        environ = {
            "wsgi.input": io.BytesIO(payload),
            "CONTENT_LENGTH": None,
        }
        empty = get_input_stream(environ, max_content_length=50 * 1024 * 1024)
        self.assertEqual(empty.read(), b"")

    def test_open_upload_body_stream_reads_without_content_length(self):
        from routes.upload_stream import drain_stream_to_file, open_upload_body_stream

        payload = b"MINUTE_CLIP_BYTES_12345"
        environ = {
            "wsgi.input": io.BytesIO(payload),
            "CONTENT_LENGTH": None,
        }
        stream = open_upload_body_stream(environ, max_content_length=50 * 1024 * 1024)
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            path = tf.name
        try:
            n = drain_stream_to_file(stream, path, buf_size=8)
            self.assertEqual(n, len(payload))
            with open(path, "rb") as f:
                self.assertEqual(f.read(), payload)
        finally:
            os.unlink(path)

    def test_open_upload_body_stream_with_content_length(self):
        from routes.upload_stream import drain_stream_to_file, open_upload_body_stream

        payload = b"ABC123"
        environ = {
            "wsgi.input": io.BytesIO(payload),
            "CONTENT_LENGTH": str(len(payload)),
        }
        stream = open_upload_body_stream(environ, max_content_length=50 * 1024 * 1024)
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            path = tf.name
        try:
            n = drain_stream_to_file(stream, path)
            self.assertEqual(n, len(payload))
        finally:
            os.unlink(path)


class TestChunkSessionDisk(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        # Point chunk root at a temp dir (imported module mutates path at call time via CHUNK_ROOT)
        import routes.field_upload_service as fus

        self.fus = fus
        self._orig_root = fus.CHUNK_ROOT
        fus.CHUNK_ROOT = Path(self._tmpdir.name) / "_chunks"
        fus.CHUNK_ROOT.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: setattr(fus, "CHUNK_ROOT", self._orig_root))

    def test_init_append_status_discard(self):
        fus = self.fus
        sid = "cap_test_session_1"
        out = fus.init_chunk_session(username="vg1", session_id=sid, user_id=1)
        self.assertTrue(out.get("ok"))

        chunk = Path(self._tmpdir.name) / "in.mp4"
        chunk.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)

        a0 = fus.append_chunk(
            username="vg1", session_id=sid, chunk_path=str(chunk), chunk_index=0,
        )
        self.assertTrue(a0.get("ok"), a0)
        self.assertEqual(a0.get("chunk_index"), 0)

        chunk2 = Path(self._tmpdir.name) / "in2.mp4"
        chunk2.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x01" * 64)
        a1 = fus.append_chunk(
            username="vg1", session_id=sid, chunk_path=str(chunk2), chunk_index=1,
        )
        self.assertTrue(a1.get("ok"), a1)

        st = fus.chunk_session_status(username="vg1", session_id=sid)
        self.assertTrue(st.get("ok"))
        self.assertEqual(st.get("chunk_count"), 2)

        disc = fus.discard_chunk_session(username="vg1", session_id=sid)
        self.assertTrue(disc.get("ok"))
        st2 = fus.chunk_session_status(username="vg1", session_id=sid)
        self.assertFalse(st2.get("exists"))

    def test_discard_blocked_while_finalize_queued(self):
        fus = self.fus
        sid = "cap_test_finalize_lock"
        fus.init_chunk_session(username="vg1", session_id=sid, user_id=1)
        chunk = Path(self._tmpdir.name) / "q.mp4"
        chunk.write_bytes(b"x" * 128)
        fus.append_chunk(username="vg1", session_id=sid, chunk_path=str(chunk), chunk_index=0)

        session_dir = fus._chunk_session_dir("vg1", sid)
        with fus._chunk_lock(session_dir):
            meta = fus._read_chunk_meta(session_dir)
            meta["finalize_status"] = "queued"
            fus._write_chunk_meta(session_dir, meta)

        disc = fus.discard_chunk_session(username="vg1", session_id=sid)
        self.assertFalse(disc.get("ok"))
        self.assertIn("assembling", (disc.get("status") or {}).get("message", "").lower())

    def test_finalize_rejects_empty_session(self):
        fus = self.fus
        sid = "cap_empty"
        fus.init_chunk_session(username="vg1", session_id=sid, user_id=1)
        gps = Path(self._tmpdir.name) / "g.csv"
        gps.write_text("VideoSecond,Latitude,Longitude,AccuracyM,FrameIndex\n0,1,2,,0\n")
        out = fus.queue_chunk_finalize(
            username="vg1",
            session_id=sid,
            gps_path=str(gps),
            gps_filename="g.csv",
            user_id=1,
        )
        self.assertFalse(out.get("ok"))
        self.assertIn("no video chunks", (out.get("status") or {}).get("message", "").lower())


class TestChunkBinEndpointSmoke(unittest.TestCase):
    """Flask test client — auth may 401 without DB user; still assert stream plumbing."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")
        # Avoid auto DB init blowing up when DB unset
        os.environ.setdefault("SKIP_DB_INIT", "1")

    def test_chunk_bin_unauthorized_without_login(self):
        from web_app import create_app

        app = create_app()
        app.config["TESTING"] = True
        client = app.test_client()
        r = client.post(
            "/api/upload/session/chunk-bin",
            data=b"hello",
            headers={
                "Content-Type": "video/mp4",
                "Content-Length": "5",
                "X-Capture-Session-Id": "cap_x",
                "X-Chunk-Index": "0",
            },
        )
        self.assertEqual(r.status_code, 401)

    def test_chunk_bin_route_registered(self):
        from web_app import create_app

        app = create_app()
        rules = {str(r) for r in app.url_map.iter_rules()}
        self.assertTrue(any("chunk-bin" in r for r in rules))
        self.assertTrue(any(r.endswith("/upload/session/chunk") for r in rules))
        self.assertTrue(any("/upload/session/init" in r for r in rules))
        self.assertTrue(any("/upload/session/finalize" in r for r in rules))
        self.assertTrue(any("/upload/session/discard" in r for r in rules))


if __name__ == "__main__":
    unittest.main()
