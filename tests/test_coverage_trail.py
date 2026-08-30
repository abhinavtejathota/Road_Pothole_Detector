"""Coverage trail: lock_end turnaround, late-ping gate, corridor ceiling.

Run:
  python -m pytest tests/test_coverage_trail.py -q
or:
  python tests/test_coverage_trail.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _trail(*latlon: tuple[float, float]) -> list[dict]:
    return [{"lat": a, "lon": b} for a, b in latlon]


class TestLockEndTurnaround(unittest.TestCase):
    """VG brushes end, turns around, fills missed stretch — open vs frozen clip."""

    def setUp(self):
        from routes import survey_service as sv

        self.sv = sv
        # ~north–south corridor near AP latitudes
        self.start = (15.0000, 78.0000)
        self.end = (15.0500, 78.0000)  # ~5.55 km north
        # Mid corridor, slightly east of axis (the "missed" fill-in)
        self.mid = (15.0250, 78.0000)
        self.missed = (15.0250, 78.0015)
        self.past_end = (15.0520, 78.0000)  # still inside END_PIN_REACH (~0.25 km)
        self.home = (15.0800, 78.0000)

        self.trail = _trail(
            (14.9900, 78.0000),  # before start (dropped)
            self.start,
            self.mid,
            self.end,
            self.past_end,
            self.home,  # post-end wander
            self.missed,  # turnaround fill-in
        )

    def test_lock_end_false_keeps_turnaround_fill_in(self):
        sv = self.sv
        out = sv._truncate_trail_to_progress_pins(
            self.trail, [self.start], [self.end], lock_end=False,
        )
        self.assertGreaterEqual(len(out), 2)
        self.assertEqual(out[0], self.start)
        # Full open slice from start — includes home wander AND missed fill-in
        self.assertIn(self.missed, out)
        self.assertIn(self.home, out)
        self.assertNotIn((14.9900, 78.0000), out)

    def test_lock_end_true_freezes_at_closest_end_approach(self):
        sv = self.sv
        out = sv._truncate_trail_to_progress_pins(
            self.trail, [self.start], [self.end], lock_end=True,
        )
        self.assertGreaterEqual(len(out), 2)
        self.assertEqual(out[0], self.start)
        # Closest approach to end among points after start is exactly at end pin
        # (or past_end if nearer — past_end is ~0.44 km past, end is 0).
        self.assertEqual(out[-1], self.end)
        self.assertNotIn(self.home, out)
        self.assertNotIn(self.missed, out)

    def test_lock_end_false_skips_closest_end_scan(self):
        """Micro-opt: open path must not depend on best_i / best_d."""
        sv = self.sv
        # Never enter end radius — still returns open trail from start
        short = _trail(self.start, self.mid)
        out = sv._truncate_trail_to_progress_pins(
            short, [self.start], [self.end], lock_end=False,
        )
        self.assertEqual(out, [self.start, self.mid])


class TestCorridorCeiling(unittest.TestCase):
    def test_shears_home_wander_above_route_plus_slack(self):
        from routes.tracking_service import (
            CORRIDOR_COVER_SLACK_KM,
            _cap_covered_km_to_corridor,
        )

        corridor = 8.47
        driven = 12.0
        capped = _cap_covered_km_to_corridor(driven, corridor)
        self.assertAlmostEqual(capped, corridor + CORRIDOR_COVER_SLACK_KM, places=5)

    def test_no_cap_when_corridor_unknown(self):
        from routes.tracking_service import _cap_covered_km_to_corridor

        self.assertEqual(_cap_covered_km_to_corridor(3.2, 0.0), 3.2)
        self.assertEqual(_cap_covered_km_to_corridor(3.2, 0.4), 3.2)

    def test_under_cap_unchanged(self):
        from routes.tracking_service import _cap_covered_km_to_corridor

        self.assertEqual(_cap_covered_km_to_corridor(6.6, 8.47), 6.6)


class TestLatePingFinalizeGate(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        import routes.field_upload_service as fus

        self.fus = fus
        self._orig_root = fus.CHUNK_ROOT
        fus.CHUNK_ROOT = Path(self._tmpdir.name) / "_chunks"
        fus.CHUNK_ROOT.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: setattr(fus, "CHUNK_ROOT", self._orig_root))

    def _set_status(self, username: str, sid: str, status: str | None):
        fus = self.fus
        fus.init_chunk_session(username=username, session_id=sid, user_id=1)
        if status is None:
            return
        session_dir = fus._chunk_session_dir(username, sid)
        with fus._chunk_lock(session_dir):
            meta = fus._read_chunk_meta(session_dir)
            meta["finalize_status"] = status
            fus._write_chunk_meta(session_dir, meta)

    def test_is_finalizing_for_queued_running_done(self):
        fus = self.fus
        for st in ("queued", "running", "done"):
            sid = f"cap_{st}"
            self._set_status("vg1", sid, st)
            self.assertTrue(
                fus.is_chunk_session_finalizing(username="vg1", session_id=sid),
                st,
            )

    def test_not_finalizing_while_recording_or_error(self):
        fus = self.fus
        self._set_status("vg1", "cap_open", None)
        self.assertFalse(fus.is_chunk_session_finalizing(username="vg1", session_id="cap_open"))
        self._set_status("vg1", "cap_err", "error")
        self.assertFalse(fus.is_chunk_session_finalizing(username="vg1", session_id="cap_err"))
        self.assertFalse(
            fus.is_chunk_session_finalizing(username="vg1", session_id="missing_sid"),
        )

    def test_record_ping_ignores_after_finalize(self):
        from routes import tracking_service as ts

        sid = "cap_late_ping"
        self._set_status("vg_late", sid, "done")

        with mock.patch.object(ts, "_STATE_LOCK"):
            # Gate runs before lock / trail mutate — still verify return shape
            out = ts.record_ping(
                user_id=99,
                username="vg_late",
                full_name="VG Late",
                lat=15.01,
                lon=78.01,
                recording=True,
                capture_session_id=sid,
            )
        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("ignored"))
        self.assertEqual(out.get("reason"), "session_finalized")


class TestEquirectangularDp(unittest.TestCase):
    def test_perp_dist_uses_km_not_raw_degrees(self):
        from routes.tracking_service import _perp_dist_km

        # Horizontal offset ~111 m at equator in lon vs same in lat — both ~0.1 km
        d_lat = _perp_dist_km(0.001, 0.0, 0.0, 0.0, 0.0, 0.01)
        d_lon = _perp_dist_km(0.0, 0.001, 0.0, 0.0, 0.01, 0.0)
        self.assertAlmostEqual(d_lat, d_lon, delta=0.02)

    def test_douglas_peucker_preserves_endpoints(self):
        from datetime import datetime, timezone

        from routes.tracking_service import _douglas_peucker

        ts = datetime.now(timezone.utc)
        pts = [
            (15.0, 78.0, ts),
            (15.00005, 78.00005, ts),  # noise within 15 m
            (15.01, 78.0, ts),
        ]
        out = _douglas_peucker(pts, tol_km=0.015)
        self.assertEqual(out[0][:2], pts[0][:2])
        self.assertEqual(out[-1][:2], pts[-1][:2])


if __name__ == "__main__":
    unittest.main()
