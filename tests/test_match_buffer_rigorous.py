"""Rigorous soft-clear / 250 m corridor+endpoint match (≥100 cases per scenario).

Run:
  python -m unittest tests.test_match_buffer_rigorous -v
"""
from __future__ import annotations

import math
import random
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

N = 100
SEED = 42


def _offset_m(lat: float, lon: float, east_m: float, north_m: float) -> tuple[float, float]:
    dlat = north_m / 111_320.0
    dlon = east_m / (111_320.0 * max(0.2, math.cos(math.radians(lat))))
    return lat + dlat, lon + dlon


def _ns_route(base_lat: float, base_lon: float, length_km: float = 5.0) -> dict:
    end_lat = base_lat + length_km / 111.32
    pts = []
    steps = max(6, int(length_km / 0.4))
    for i in range(steps + 1):
        t = i / steps
        pts.append([base_lat + t * (end_lat - base_lat), base_lon])
    return {
        "start": {"lat": base_lat, "lon": base_lon},
        "end": {"lat": end_lat, "lon": base_lon},
        "polyline": pts,
        "segment_ids": [f"seg_{base_lat:.4f}"],
        "mode": "corridor",
    }


def _gps_along(route: dict, *, lateral_m: float = 0.0, seed: int = 0) -> list[tuple[float, float]]:
    poly = route["polyline"]
    out = []
    rng = random.Random(seed)
    for i, p in enumerate(poly):
        lat, lon = float(p[0]), float(p[1])
        # jitter ±20 m along track + fixed lateral offset
        jn = rng.uniform(-20, 20)
        je = lateral_m + rng.uniform(-5, 5)
        out.append(_offset_m(lat, lon, je, jn))
    return out


class TestCorridorBuffer250m(unittest.TestCase):
    """GPS within 250 m of corridor must match; beyond ~400 m must not."""

    def test_within_200m_scores_high_100(self):
        from routes.survey.assignments import (
            MATCH_CLEARED_MIN_SCORE,
            _gps_assignment_match_score,
        )

        fails = []
        for i in range(N):
            route = _ns_route(17.0 + i * 0.01, 78.0 + (i % 10) * 0.02, 4.0 + (i % 5) * 0.5)
            gps = _gps_along(route, lateral_m=200.0, seed=SEED + i)
            score = _gps_assignment_match_score(route, gps)
            if score < MATCH_CLEARED_MIN_SCORE:
                fails.append((i, score))
        self.assertEqual(fails, [], f"{len(fails)}/{N} failed within 200m: {fails[:5]}")

    def test_within_240m_scores_high_100(self):
        from routes.survey.assignments import (
            MATCH_CLEARED_MIN_SCORE,
            _gps_assignment_match_score,
        )

        fails = []
        for i in range(N):
            route = _ns_route(16.5 + i * 0.008, 79.0 + (i % 7) * 0.03, 3.5)
            gps = _gps_along(route, lateral_m=240.0, seed=SEED + 1000 + i)
            score = _gps_assignment_match_score(route, gps)
            if score < MATCH_CLEARED_MIN_SCORE:
                fails.append((i, score))
        self.assertEqual(fails, [], f"{len(fails)}/{N} failed within 240m: {fails[:5]}")

    def test_beyond_450m_scores_low_100(self):
        from routes.survey.assignments import (
            MATCH_CLEARED_MIN_SCORE,
            _gps_assignment_match_score,
        )

        fails = []
        for i in range(N):
            route = _ns_route(15.0 + i * 0.01, 77.5 + (i % 5) * 0.04, 5.0)
            gps = _gps_along(route, lateral_m=450.0, seed=SEED + 2000 + i)
            score = _gps_assignment_match_score(route, gps)
            if score >= MATCH_CLEARED_MIN_SCORE:
                fails.append((i, score))
        self.assertEqual(fails, [], f"{len(fails)}/{N} falsely matched at 450m: {fails[:5]}")


class TestEndpointBuffer250m(unittest.TestCase):
    def test_endpoint_bonus_inside_250m_100(self):
        from routes.survey.assignments import (
            MATCH_ENDPOINT_BUFFER_KM,
            _gps_assignment_match_score,
        )

        self.assertAlmostEqual(MATCH_ENDPOINT_BUFFER_KM, 0.25)
        fails = []
        for i in range(N):
            route = _ns_route(17.2 + i * 0.005, 78.3, 4.0)
            # Perfect corridor except endpoints nudged 200 m east
            gps = _gps_along(route, lateral_m=0.0, seed=i)
            s0 = route["start"]
            e0 = route["end"]
            gps[0] = _offset_m(s0["lat"], s0["lon"], 200, 0)
            gps[-1] = _offset_m(e0["lat"], e0["lon"], 200, 0)
            score = _gps_assignment_match_score(route, gps)
            # Mid-corridor frac alone is high; endpoints still get bonus inside 250 m
            if score < 0.9:
                fails.append((i, score))
        self.assertEqual(fails, [], f"{len(fails)}/{N} endpoint-inside failed: {fails[:5]}")

    def test_endpoint_bonus_outside_400m_no_false_bonus_100(self):
        from routes.survey.assignments import _gps_assignment_match_score

        for i in range(N):
            route = _ns_route(17.3, 78.4 + i * 0.001, 3.0)
            # GPS far from corridor — score must stay near 0 even with endpoint proximity
            gps = [
                _offset_m(17.3, 78.4 + i * 0.001, 2000, 0),
                _offset_m(17.31, 78.4 + i * 0.001, 2000, 0),
                _offset_m(17.32, 78.4 + i * 0.001, 2000, 0),
            ]
            score = _gps_assignment_match_score(route, gps)
            self.assertLess(score, 0.2, (i, score))


class TestSoftClearSealPreference(unittest.TestCase):
    """Upload of route A after clear+assign B must seal against cleared A."""

    def test_cleared_wins_over_unrelated_active_100(self):
        from routes.survey import assignments as asg

        fails = []
        for i in range(N):
            cleared = _ns_route(17.40 + i * 0.002, 78.40, 4.0)
            active = _ns_route(17.60 + i * 0.002, 78.60, 4.0)
            gps = _gps_along(cleared, lateral_m=80.0, seed=SEED + i)
            with mock.patch.object(
                asg,
                "_list_cleared_assignment_candidates",
                return_value=[{"id": 1000 + i, "date": "2026-07-25", "entry": cleared}],
            ), mock.patch.object(asg, "_entry_has_assignment_work", return_value=True):
                target = asg._resolve_seal_target(8, "2026-07-25", gps, active)
            if target.get("kind") != "cleared" or target.get("archive_id") != 1000 + i:
                fails.append((i, target))
        self.assertEqual(fails, [], f"{len(fails)}/{N} soft-clear preference failed: {fails[:3]}")

    def test_active_wins_when_gps_on_active_100(self):
        from routes.survey import assignments as asg

        fails = []
        for i in range(N):
            cleared = _ns_route(17.40, 78.40 + i * 0.001, 4.0)
            active = _ns_route(17.55, 78.55 + i * 0.001, 4.0)
            gps = _gps_along(active, lateral_m=50.0, seed=SEED + 50 + i)
            with mock.patch.object(
                asg,
                "_list_cleared_assignment_candidates",
                return_value=[{"id": 2000 + i, "date": "2026-07-25", "entry": cleared}],
            ), mock.patch.object(asg, "_entry_has_assignment_work", return_value=True):
                target = asg._resolve_seal_target(8, "2026-07-25", gps, active)
            if target.get("kind") != "active":
                fails.append((i, target))
        self.assertEqual(fails, [], f"{len(fails)}/{N} active preference failed: {fails[:3]}")

    def test_prefers_best_of_multiple_cleared_100(self):
        from routes.survey import assignments as asg

        fails = []
        for i in range(N):
            a = _ns_route(17.10 + i * 0.001, 78.10, 3.5)
            b = _ns_route(17.30 + i * 0.001, 78.30, 3.5)
            c = _ns_route(17.50 + i * 0.001, 78.50, 3.5)
            gps = _gps_along(b, lateral_m=100.0, seed=i)
            cleared_rows = [
                {"id": 1, "date": "2026-07-24", "entry": a},
                {"id": 2, "date": "2026-07-25", "entry": b},
                {"id": 3, "date": "2026-07-25", "entry": c},
            ]
            with mock.patch.object(
                asg, "_list_cleared_assignment_candidates", return_value=cleared_rows
            ), mock.patch.object(asg, "_entry_has_assignment_work", return_value=True):
                target = asg._resolve_seal_target(8, "2026-07-25", gps, None)
            if target.get("archive_id") != 2:
                fails.append((i, target))
        self.assertEqual(fails, [], f"{len(fails)}/{N} multi-cleared pick failed: {fails[:3]}")


class TestPinReach250m(unittest.TestCase):
    def test_near_pin_helpers_100(self):
        from routes.survey.progress import (
            END_PIN_REACH_KM,
            START_PIN_REACH_KM,
            _near_any_pin,
        )

        self.assertAlmostEqual(START_PIN_REACH_KM, 0.25)
        self.assertAlmostEqual(END_PIN_REACH_KM, 0.25)
        fails_in = []
        fails_out = []
        for i in range(N):
            pin = (15.0 + i * 0.01, 78.0 + (i % 20) * 0.01)
            inside = _offset_m(pin[0], pin[1], 200, 0)
            outside = _offset_m(pin[0], pin[1], 400, 0)
            if not _near_any_pin(inside[0], inside[1], pin, radius_km=0.25):
                fails_in.append(i)
            if _near_any_pin(outside[0], outside[1], pin, radius_km=0.25):
                fails_out.append(i)
        self.assertEqual(fails_in, [], f"inside misses: {fails_in[:5]}")
        self.assertEqual(fails_out, [], f"outside hits: {fails_out[:5]}")


if __name__ == "__main__":
    unittest.main()
