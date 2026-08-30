"""Report stats + display consistency (no blank em-dash placeholders)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestReportRoadStats(unittest.TestCase):
    def test_no_assignment_single_honest_row(self):
        from routes.report_service import compute_road_stats

        potholes = [
            {"lat": 17.44, "lon": 78.37, "x1": 0, "y1": 0, "x2": 10, "y2": 10, "street_name": "Fake A"},
            {"lat": 17.45, "lon": 78.38, "x1": 0, "y1": 0, "x2": 20, "y2": 20, "street_name": "Fake B"},
        ]
        stats = compute_road_stats(potholes, None, covered_km=0.0)
        self.assertEqual(len(stats["per_road"]), 1)
        self.assertIn("survey corridor", stats["per_road"][0]["name"].lower())
        self.assertEqual(stats["per_road"][0]["potholes"], 2)

    def test_uses_gps_covered_over_span(self):
        from routes.report_service import compute_road_stats

        potholes = [
            {"lat": 17.40, "lon": 78.30, "x1": 0, "y1": 0, "x2": 5, "y2": 5},
            {"lat": 17.50, "lon": 78.40, "x1": 0, "y1": 0, "x2": 5, "y2": 5},
        ]
        asg = {
            "route_km": 8.5,
            "corridor_km": 8.5,
            "start": {"lat": 17.40, "lon": 78.30, "label": "A"},
            "end": {"lat": 17.48, "lon": 78.35, "label": "B"},
            "covered_km": 6.6,
            "segment_ids": [],
        }
        stats = compute_road_stats(potholes, asg, covered_km=6.6)
        self.assertAlmostEqual(stats["covered_km"], 6.6, places=2)
        self.assertAlmostEqual(stats["corridor_km"], 8.5, places=2)
        self.assertAlmostEqual(stats["total_km"], 6.6, places=2)
        self.assertEqual(stats["length_source"], "gps_covered")


class TestReportDisplayEnrichment(unittest.TestCase):
    def test_fallbacks_never_emdash(self):
        from routes.report_service import (
            display_corridor_km,
            display_gps_covered,
            enrich_stats_for_report,
            compute_road_stats,
            priority_action_rows,
            severity_distribution,
        )

        self.assertNotIn("—", display_corridor_km(0, has_assignment=False))
        self.assertIn("ad-hoc", display_corridor_km(0, has_assignment=False).lower())
        self.assertNotEqual(display_corridor_km(0, has_assignment=False).strip(), "—")
        self.assertNotIn("—", display_gps_covered(0))
        self.assertNotEqual(display_gps_covered(0).strip(), "—")
        self.assertEqual(display_gps_covered(0).strip().lower(), "0 km")

        potholes = [
            {"severity": "High", "lat": 17.4, "lon": 78.3, "x1": 0, "y1": 0, "x2": 40, "y2": 40},
            {"severity": "Low", "lat": 17.41, "lon": 78.31, "x1": 0, "y1": 0, "x2": 10, "y2": 10},
            {"severity": "Medium", "lat": 17.42, "lon": 78.32, "x1": 0, "y1": 0, "x2": 20, "y2": 20},
        ]
        stats = compute_road_stats(potholes, None, covered_km=0.0)
        enriched = enrich_stats_for_report(stats, potholes, has_assignment=False)
        self.assertEqual(enriched["severity_counts"]["High"], 1)
        self.assertEqual(enriched["severity_counts"]["Low"], 1)
        self.assertEqual(enriched["severity_counts"]["Medium"], 1)
        self.assertIn("ad-hoc", enriched["corridor_display"].lower())
        self.assertTrue(enriched["priority_actions"])
        top = priority_action_rows(enriched["per_road"], limit=5)
        self.assertGreaterEqual(len(top), 1)
        dist = severity_distribution(potholes)
        self.assertEqual(dist["High"] + dist["Medium"] + dist["Low"], 3)


class TestReportGpsLogLength(unittest.TestCase):
    def test_unique_coverage_no_circle_recount(self):
        from routes.report_service import unique_track_coverage_km

        # ~1.11 km north, then reverse the same path (out-and-back)
        one_way = []
        for i in range(0, 12):
            one_way.append((17.40 + i * 0.001, 78.30))  # ~0.111 km steps
        round_trip = one_way + list(reversed(one_way))
        km = unique_track_coverage_km(round_trip, cell_m=40)
        # Must be closer to one-way (~1.1) than to full round-trip (~2.2)
        self.assertGreater(km, 0.6)
        self.assertLess(km, 1.6)

    def test_uses_gps_log_over_bbox(self):
        from routes.report_service import compute_road_stats

        potholes = [
            {"lat": 17.40, "lon": 78.30, "x1": 0, "y1": 0, "x2": 5, "y2": 5},
            {"lat": 17.50, "lon": 78.40, "x1": 0, "y1": 0, "x2": 5, "y2": 5},
        ]
        stats = compute_road_stats(potholes, None, covered_km=0.0, gps_log_km=0.95)
        self.assertAlmostEqual(stats["total_km"], 0.95, places=2)
        self.assertEqual(stats["length_source"], "s3_gps_log")
        self.assertAlmostEqual(stats["gps_log_km"], 0.95, places=2)

    def test_bbox_fallback_not_hop_sum(self):
        from routes.report_service import compute_road_stats, _span_km_from_potholes

        # Many pins in a tiny cluster — hop-sum would explode; bbox stays small
        potholes = []
        for i in range(50):
            potholes.append({
                "id": i,
                "lat": 17.44680 + (i % 5) * 0.00005,
                "lon": 78.37897 + (i % 7) * 0.00005,
                "x1": 0, "y1": 0, "x2": 5, "y2": 5,
            })
        span = _span_km_from_potholes(potholes)
        self.assertLess(span, 0.15)
        stats = compute_road_stats(potholes, None, covered_km=0.0, gps_log_km=0.0)
        self.assertEqual(stats["length_source"], "bbox_extent")
        self.assertLess(stats["total_km"], 0.15)


class TestReportAssignmentLookup(unittest.TestCase):
    def test_no_near_date_invention(self):
        """Missing same-day assignment must not pull a neighbour day's corridor."""
        from unittest import mock
        from routes import report_service as rs

        with mock.patch("db_utils.is_db_configured", return_value=True), \
             mock.patch("db_utils.survey_db_get_assignment", return_value=None), \
             mock.patch("db_utils.survey_db_find_assignment_near_date") as near, \
             mock.patch("routes.survey_service._load_state", return_value={"daily_assignments": {}}):
            entry, day = rs.lookup_assignment_for_report(
                1, "2026-07-18T17:36:00+05:30", potholes=[],
            )
            self.assertIsNone(entry)
            self.assertIsNone(day)
            near.assert_not_called()

    def test_survey_date_prefers_processed_at(self):
        from routes.report_service import display_corridor_km
        # Sanity: empty corridor is honest, not a fake neighbour length
        self.assertIn("ad-hoc", display_corridor_km(0, has_assignment=False).lower())


if __name__ == "__main__":
    unittest.main()
