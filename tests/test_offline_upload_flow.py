"""Offline capture → deferred upload protocol tests.

Mirrors Expo offlineCapture.js / Flutter OfflineCapture + server chunk session:
  local save chunks → (later online) init → upload unmarked chunks → finalize.

Run:
  venv\\Scripts\\python.exe tests/test_offline_upload_flow.py -v
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── Local offline store (same meta schema as mobile apps) ─────────────────────

class LocalOfflineStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index: list[str] = []

    def session_dir(self, sid: str) -> Path:
        d = self.root / sid
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _meta_path(self, sid: str) -> Path:
        return self.session_dir(sid) / "meta.json"

    def read_meta(self, sid: str) -> dict | None:
        p = self._meta_path(sid)
        if not p.is_file():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def write_meta(self, sid: str, meta: dict) -> None:
        self._meta_path(sid).write_text(json.dumps(meta), encoding="utf-8")

    def init_local(self, sid: str, *, offline: bool = False) -> dict:
        meta = self.read_meta(sid)
        if not meta:
            meta = {
                "sessionId": sid,
                "offlineStart": offline,
                "chunks": [],
                "uploaded": [],
                "status": "recording",
            }
        else:
            if meta.get("status") != "uploaded":
                meta["status"] = "recording"
            meta["offlineStart"] = bool(meta.get("offlineStart")) or offline
        self.write_meta(sid, meta)
        if sid not in self.index:
            self.index.insert(0, sid)
        return meta

    def save_chunk(self, sid: str, index: int, data: bytes) -> Path:
        dest = self.session_dir(sid) / f"chunk_{index:04d}.mp4"
        if not dest.exists():
            dest.write_bytes(data)
        meta = self.read_meta(sid) or {
            "sessionId": sid,
            "chunks": [],
            "uploaded": [],
            "status": "pending_upload",
        }
        chunks = sorted(set(int(x) for x in meta.get("chunks") or []) | {index})
        meta["chunks"] = chunks
        meta["status"] = "pending_upload"
        self.write_meta(sid, meta)
        return dest

    def save_gps(self, sid: str, csv_text: str) -> Path:
        p = self.session_dir(sid) / "gps.csv"
        p.write_text(csv_text, encoding="utf-8")
        return p

    def mark_uploaded(self, sid: str, index: int) -> None:
        meta = self.read_meta(sid)
        if not meta:
            return
        uploaded = sorted(set(int(x) for x in meta.get("uploaded") or []) | {index})
        meta["uploaded"] = uploaded
        self.write_meta(sid, meta)

    def clear_uploaded(self, sid: str) -> None:
        meta = self.read_meta(sid)
        if not meta:
            return
        meta["uploaded"] = []
        self.write_meta(sid, meta)

    def list_chunks(self, sid: str) -> list[dict]:
        meta = self.read_meta(sid) or {}
        uploaded = set(int(x) for x in meta.get("uploaded") or [])
        out = []
        for i in sorted(int(x) for x in meta.get("chunks") or []):
            path = self.session_dir(sid) / f"chunk_{i:04d}.mp4"
            if path.is_file():
                out.append({"index": i, "path": path, "uploaded": i in uploaded})
        return out

    def list_pending(self) -> list[dict]:
        ids = list(self.index)
        for child in self.root.iterdir():
            if child.is_dir() and child.name not in ids:
                ids.append(child.name)
        pending = []
        for sid in ids:
            meta = self.read_meta(sid)
            if not meta or meta.get("status") == "uploaded":
                continue
            chunks = self.list_chunks(sid)
            if not chunks:
                continue
            pending.append({**meta, "chunkCount": len(chunks)})
        return pending

    def mark_session_uploaded(self, sid: str) -> None:
        meta = self.read_meta(sid)
        if meta:
            meta["status"] = "uploaded"
            self.write_meta(sid, meta)
        shutil.rmtree(self.session_dir(sid), ignore_errors=True)
        self.index = [x for x in self.index if x != sid]


def upload_plan(chunks: list[dict], *, need_reinit: bool) -> list[int]:
    """Indices that must be POSTed. After server re-init, marks are cleared."""
    if need_reinit:
        return [c["index"] for c in chunks]
    return [c["index"] for c in chunks if not c["uploaded"]]


class TestOfflineStoreLogic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = LocalOfflineStore(Path(self.tmp.name) / "captures")

    def test_offline_record_then_list_pending(self):
        sid = "cap_off_1"
        self.store.init_local(sid, offline=True)
        self.store.save_chunk(sid, 0, b"chunk0" * 20)
        self.store.save_chunk(sid, 1, b"chunk1" * 20)
        self.store.save_gps(
            sid,
            "VideoSecond,Latitude,Longitude,AccuracyM,FrameIndex\n"
            "0,17.1,78.4,,0\n1,17.1,78.41,,30\n",
        )
        pending = self.store.list_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["chunkCount"], 2)
        self.assertTrue(pending[0]["offlineStart"])
        self.assertEqual(pending[0]["status"], "pending_upload")

    def test_empty_pending_filtered(self):
        sid = "cap_empty"
        self.store.init_local(sid, offline=True)
        self.assertEqual(self.store.list_pending(), [])

    def test_upload_plan_skips_marked_when_not_reinit(self):
        chunks = [
            {"index": 0, "uploaded": True},
            {"index": 1, "uploaded": False},
            {"index": 2, "uploaded": True},
        ]
        self.assertEqual(upload_plan(chunks, need_reinit=False), [1])
        self.assertEqual(upload_plan(chunks, need_reinit=True), [0, 1, 2])

    def test_stale_marks_after_server_wipe_need_clear(self):
        """Regression: re-init wipes server; skipping local 'uploaded' → empty finalize."""
        sid = "cap_stale"
        self.store.init_local(sid, offline=False)
        self.store.save_chunk(sid, 0, b"a" * 64)
        self.store.save_chunk(sid, 1, b"b" * 64)
        self.store.mark_uploaded(sid, 0)
        self.store.mark_uploaded(sid, 1)

        # Phone still thinks ready=False after kill → re-init required
        chunks = self.store.list_chunks(sid)
        self.assertEqual(upload_plan(chunks, need_reinit=False), [])  # BUG path without clear
        self.store.clear_uploaded(sid)
        chunks2 = self.store.list_chunks(sid)
        self.assertEqual(upload_plan(chunks2, need_reinit=True), [0, 1])

    def test_mark_uploaded_wipes_files(self):
        sid = "cap_done"
        self.store.init_local(sid)
        self.store.save_chunk(sid, 0, b"x" * 32)
        self.store.mark_session_uploaded(sid)
        self.assertFalse((self.store.root / sid).exists())
        self.assertEqual(self.store.list_pending(), [])


class TestOfflineToServerIntegration(unittest.TestCase):
    """Local offline store → field_upload_service (same as phone after coming online)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.store = LocalOfflineStore(Path(self.tmp.name) / "captures")

        import routes.field_upload_service as fus

        self.fus = fus
        self._orig = fus.CHUNK_ROOT
        fus.CHUNK_ROOT = Path(self.tmp.name) / "_chunks"
        fus.CHUNK_ROOT.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: setattr(fus, "CHUNK_ROOT", self._orig))

    def _deferred_upload(self, sid: str, *, need_reinit: bool) -> dict:
        fus = self.fus
        if need_reinit:
            fus.init_chunk_session(username="vg_off", session_id=sid, user_id=9)
            self.store.clear_uploaded(sid)

        chunks = self.store.list_chunks(sid)
        for c in chunks:
            if c["uploaded"] and not need_reinit:
                continue
            out = fus.append_chunk(
                username="vg_off",
                session_id=sid,
                chunk_path=str(c["path"]),
                chunk_index=c["index"],
            )
            self.assertTrue(out.get("ok"), out)
            self.store.mark_uploaded(sid, c["index"])

        gps = self.store.session_dir(sid) / "gps.csv"
        self.assertTrue(gps.is_file(), "gps.csv required for finalize")
        # Phone-facing path: queue accepts immediately (ffmpeg/S3 later).
        # Assert acceptance + chunk presence — not concat (needs ffmpeg on PATH).
        result = fus.queue_chunk_finalize(
            username="vg_off",
            session_id=sid,
            gps_path=str(gps),
            gps_filename="gps.csv",
            user_id=9,
        )
        return result

    def _assert_accepted(self, result: dict, sid: str, min_chunks: int):
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(result.get("accepted"), result)
        st = self.fus.chunk_session_status(username="vg_off", session_id=sid)
        self.assertTrue(st.get("exists"))
        self.assertGreaterEqual(int(st.get("chunk_count") or 0), min_chunks)
        # Let background finalize attempt start briefly without blocking cleanup.
        import time
        time.sleep(0.2)

    def test_full_offline_then_upload(self):
        sid = "cap_offline_full"
        self.store.init_local(sid, offline=True)
        self.store.save_chunk(sid, 0, b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 200)
        self.store.save_chunk(sid, 1, b"\x00\x00\x00\x18ftypmp42" + b"\x01" * 200)
        self.store.save_gps(
            sid,
            "VideoSecond,Latitude,Longitude,AccuracyM,FrameIndex\n"
            "0,17.385,78.486,,0\n"
            "60,17.386,78.487,,1800\n",
        )

        # No server session yet → need_reinit
        result = self._deferred_upload(sid, need_reinit=True)
        self._assert_accepted(result, sid, min_chunks=2)

    def test_kill_resume_with_stale_marks_reuploads(self):
        sid = "cap_kill_resume"
        self.store.init_local(sid, offline=False)
        self.store.save_chunk(sid, 0, b"A" * 128)
        self.store.save_chunk(sid, 1, b"B" * 128)
        self.store.save_gps(
            sid,
            "VideoSecond,Latitude,Longitude,AccuracyM,FrameIndex\n0,1,2,,0\n",
        )
        # Simulate earlier online uploads marked locally
        self.store.mark_uploaded(sid, 0)
        self.store.mark_uploaded(sid, 1)

        # Without clear: plan empty → would finalize with 0 server chunks
        plan_bad = upload_plan(self.store.list_chunks(sid), need_reinit=False)
        self.assertEqual(plan_bad, [])

        # Correct client path after kill: re-init + clear marks
        result = self._deferred_upload(sid, need_reinit=True)
        self._assert_accepted(result, sid, min_chunks=2)

    def test_partial_upload_continues_remaining(self):
        sid = "cap_partial"
        self.store.init_local(sid)
        self.store.save_chunk(sid, 0, b"A" * 128)
        self.store.save_chunk(sid, 1, b"B" * 128)
        self.store.save_chunk(sid, 2, b"C" * 128)
        self.store.save_gps(
            sid,
            "VideoSecond,Latitude,Longitude,AccuracyM,FrameIndex\n0,1,2,,0\n",
        )

        fus = self.fus
        fus.init_chunk_session(username="vg_off", session_id=sid, user_id=9)
        # Chunk 0 already on server + marked
        fus.append_chunk(
            username="vg_off",
            session_id=sid,
            chunk_path=str(self.store.list_chunks(sid)[0]["path"]),
            chunk_index=0,
        )
        self.store.mark_uploaded(sid, 0)

        # Same server session still ready → only upload 1,2
        plan = upload_plan(self.store.list_chunks(sid), need_reinit=False)
        self.assertEqual(plan, [1, 2])
        result = self._deferred_upload(sid, need_reinit=False)
        self._assert_accepted(result, sid, min_chunks=3)

    def test_gap_index_recorded_as_missing(self):
        sid = "cap_gap"
        self.store.init_local(sid, offline=True)
        self.store.save_chunk(sid, 0, b"A" * 64)
        self.store.save_chunk(sid, 2, b"C" * 64)  # missing 1
        self.store.save_gps(
            sid,
            "VideoSecond,Latitude,Longitude,AccuracyM,FrameIndex\n0,1,2,,0\n",
        )
        fus = self.fus
        fus.init_chunk_session(username="vg_off", session_id=sid, user_id=9)
        for c in self.store.list_chunks(sid):
            fus.append_chunk(
                username="vg_off",
                session_id=sid,
                chunk_path=str(c["path"]),
                chunk_index=c["index"],
            )
        st = fus.chunk_session_status(username="vg_off", session_id=sid)
        self.assertIn(1, st.get("missing_indices") or [])


if __name__ == "__main__":
    unittest.main()
