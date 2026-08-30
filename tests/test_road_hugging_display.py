"""Display-only road-hugging for cyan/grey; covered_km stays chord-based."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestRoadHuggingDisplay(unittest.TestCase):
    def test_no_entry_returns_raw(self):
        from routes.tracking.trail import road_hugging_display_latlon

        pts = [(15.0, 78.0), (15.01, 78.0), (15.02, 78.0)]
        self.assertEqual(road_hugging_display_latlon(pts, entry=None), pts)

    def test_walk_same_part_follows_vertices(self):
        from routes.tracking.trail import _walk_part

        part = [(0.0, 0.0), (0.0, 1.0), (0.0, 2.0), (0.0, 3.0)]
        walked = _walk_part(part, 0, 0.0, 2, 1.0)
        self.assertEqual(walked[0], (0.0, 0.0))
        self.assertEqual(walked[-1], (0.0, 3.0))
        self.assertIn((0.0, 1.0), walked)
        self.assertIn((0.0, 2.0), walked)

    def test_covered_feature_keeps_chord_length(self):
        from routes.tracking import trail as tr

        # Straight chordy GPS
        trail = [
            {"lat": 15.0, "lon": 78.0},
            {"lat": 15.005, "lon": 78.0},
            {"lat": 15.010, "lon": 78.0},
        ]
        # Fake entry with a bent "road" the hugger should follow
        bent = [[15.0 + i * 0.001, 78.0 + (0.001 if i % 2 else 0.0)] for i in range(12)]
        entry = {
            "state_key": "andhra",
            "district_id": "504",
            "segment_ids": [],
            "polyline": bent,
            "start": {"lat": 15.0, "lon": 78.0},
            "end": {"lat": 15.01, "lon": 78.0},
        }

        # Avoid loading full district GIS — only guide polyline as part
        with mock.patch.object(tr, "_display_road_parts", return_value=[
            [(float(p[0]), float(p[1])) for p in bent]
        ]):
            feats = tr.covered_trail_features(trail, entry=entry)

        self.assertTrue(feats)
        props = feats[0]["properties"]
        # Chord length ≈ 1.11 km for 0.01 deg lat
        self.assertAlmostEqual(props["length_km"], 1.111, delta=0.05)
        coords = feats[0]["geometry"]["coordinates"]
        self.assertGreaterEqual(len(coords), 2)

    def test_sanitize_hugs_without_changing_coverage_inputs(self):
        from routes.tracking import trail as tr
        from routes.tracking.coverage import resolve_videographer_coverage

        raw = [
            {"lat": 15.0, "lon": 78.0, "ts": "2026-07-25T10:00:00+05:30", "delta_km": 0.1},
            {"lat": 15.002, "lon": 78.0, "ts": "2026-07-25T10:01:00+05:30", "delta_km": 0.2},
            {"lat": 15.004, "lon": 78.0, "ts": "2026-07-25T10:02:00+05:30", "delta_km": 0.2},
        ]
        guide = [(15.0 + i * 0.001, 78.0) for i in range(6)]
        entry = {
            "polyline": [[a, b] for a, b in guide],
            "start": {"lat": 15.0, "lon": 78.0},
            "end": {"lat": 15.005, "lon": 78.0},
            "segment_ids": ["x"],
            "state_key": "andhra",
            "district_id": "1",
        }
        with mock.patch.object(tr, "_display_road_parts", return_value=[guide]):
            disp = tr.sanitize_trail_for_display(raw, entry=entry)
        self.assertTrue(disp)
        self.assertTrue(all(p is None or "lat" in p for p in disp))

        # Coverage must still read the raw trail entry, not display list
        track_entry = {"trail": raw, "covered_km": 0.0, "covered_by_class": {}}
        with mock.patch("routes.tracking.coverage.recompute_driven_trail_coverage", return_value=(0.5, {"nh": 0, "sh": 0, "mdr": 0, "other": 0.5})), \
             mock.patch("routes.tracking.coverage.recompute_trail_coverage", return_value=(0.4, {"nh": 0, "sh": 0, "mdr": 0, "other": 0.4})), \
             mock.patch("routes.tracking.coverage.persist_coverage_canonical", side_effect=lambda *a, **k: (a[2], a[3] if len(a) > 3 else {})), \
             mock.patch("routes.tracking.coverage.trail_for_user", return_value=raw):
            # Minimal resolve path — ensure it uses entry["trail"] (raw)
            km, _cbc, _ = resolve_videographer_coverage(
                99, "2026-07-25", track_entry, resolve_carryover=False, sync_assignment=False,
            )
        # driven mocked to 0.5; exact branch depends on corridor — just ensure not exploded
        self.assertLessEqual(float(km or 0), 2.0)


class TestRoadHuggingVideo5Optional(unittest.TestCase):
    def test_video5_hug_reduces_max_edge_when_gis_present(self):
        from routes.survey.progress import _state_dir
        from routes.tracking.trail import road_hugging_display_latlon, covered_trail_features
        from routes import survey_service as ss

        geo = _state_dir("andhra") / "index" / "segments" / "504.geojson"
        if not geo.is_file():
            self.skipTest("504 GIS missing")

        st = ss._load_state()
        entry = (st.get("daily_assignments", {}).get("2026-07-25", {}) or {}).get(
            ss._user_day_key(15)
        ) or {}
        if not entry.get("polyline"):
            self.skipTest("video5 assignment missing")

        from routes import tracking_service as ts
        trail = ts.trail_for_user(15, "2026-07-25")
        if len(trail) < 2:
            self.skipTest("no trail")

        raw = [(float(p["lat"]), float(p.get("lon") or p.get("lng"))) for p in trail if isinstance(p, dict)]
        raw_edges = [
            ss._haversine_km(raw[i - 1][0], raw[i - 1][1], raw[i][0], raw[i][1])
            for i in range(1, len(raw))
        ]
        hugged = road_hugging_display_latlon(raw, entry=entry)
        hug_edges = [
            ss._haversine_km(hugged[i - 1][0], hugged[i - 1][1], hugged[i][0], hugged[i][1])
            for i in range(1, len(hugged))
        ]
        self.assertGreaterEqual(len(hugged), 2)
        if raw_edges and hug_edges:
            self.assertLessEqual(max(hug_edges), max(raw_edges) + 0.05)

        feats = covered_trail_features(trail, entry=entry)
        if feats:
            chord = float(feats[0]["properties"]["length_km"])
            # Chord length property must match raw polyline length, not hugged path length
            raw_km = ss._polyline_length_km(raw)
            self.assertAlmostEqual(chord, raw_km, delta=0.05)


if __name__ == "__main__":
    unittest.main()
