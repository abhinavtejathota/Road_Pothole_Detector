"""Soft-clear archive + seal-against-cleared-route matching."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestGpsAssignmentMatchScore(unittest.TestCase):
    def test_prefers_matching_corridor(self):
        from routes.survey.assignments import _gps_assignment_match_score

        route_a = {
            "start": {"lat": 17.40, "lon": 78.40},
            "end": {"lat": 17.45, "lon": 78.40},
            "polyline": [[17.40, 78.40], [17.42, 78.40], [17.45, 78.40]],
            "segment_ids": ["518_a"],
        }
        route_b = {
            "start": {"lat": 17.50, "lon": 78.50},
            "end": {"lat": 17.55, "lon": 78.50},
            "polyline": [[17.50, 78.50], [17.52, 78.50], [17.55, 78.50]],
            "segment_ids": ["518_b"],
        }
        gps_a = [(17.40 + i * 0.005, 78.40) for i in range(11)]  # ends on route end
        sa = _gps_assignment_match_score(route_a, gps_a)
        sb = _gps_assignment_match_score(route_b, gps_a)
        self.assertGreater(sa, 0.5, sa)
        self.assertGreater(sa, sb + 0.2, (sa, sb))


class TestResolveSealTarget(unittest.TestCase):
    def test_cleared_wins_over_weak_active(self):
        from routes.survey import assignments as asg

        active = {
            "start": {"lat": 17.50, "lon": 78.50},
            "end": {"lat": 17.55, "lon": 78.50},
            "polyline": [[17.50, 78.50], [17.55, 78.50]],
            "segment_ids": ["b1"],
            "mode": "corridor",
        }
        cleared_entry = {
            "start": {"lat": 17.40, "lon": 78.40},
            "end": {"lat": 17.45, "lon": 78.40},
            "polyline": [[17.40, 78.40], [17.42, 78.40], [17.45, 78.40]],
            "segment_ids": ["a1"],
            "mode": "corridor",
        }
        gps_a = [(17.40 + i * 0.005, 78.40) for i in range(11)]

        with mock.patch.object(
            asg,
            "_list_cleared_assignment_candidates",
            return_value=[{"id": 9, "date": "2026-07-25", "entry": cleared_entry}],
        ), mock.patch.object(asg, "_entry_has_assignment_work", return_value=True):
            target = asg._resolve_seal_target(8, "2026-07-25", gps_a, active)

        self.assertEqual(target["kind"], "cleared", target)
        self.assertEqual(target["archive_id"], 9)
        self.assertGreaterEqual(target["score"], 0.22)


class TestClearArchivesEntry(unittest.TestCase):
    def test_clear_writes_json_archive(self):
        from routes.survey import assignments as asg

        fake_state = {
            "daily_assignments": {
                "2026-07-25": {
                    "u99": {
                        "user_id": 99,
                        "segment_ids": ["518_x"],
                        "start": {"lat": 17.4, "lon": 78.4, "label": "S"},
                        "end": {"lat": 17.45, "lon": 78.4, "label": "E"},
                        "polyline": [[17.4, 78.4], [17.45, 78.4]],
                        "mode": "corridor",
                    }
                }
            },
            "segment_status": {"518_x": "assigned"},
        }

        def _load():
            return fake_state

        def _save(state, **kwargs):
            pass

        mock_db = mock.MagicMock()
        mock_db.survey_db_archive_cleared_assignment.return_value = 42

        with mock.patch.object(asg, "_load_state", _load), \
             mock.patch.object(asg, "_save_state", _save), \
             mock.patch.object(asg, "today_ist", return_value="2026-07-25"), \
             mock.patch.dict("sys.modules", {"db_utils": mock_db}):
            res = asg.clear_daily_assignment_for_user(99, "2026-07-25")

        self.assertTrue(res.get("cleared"), res)
        self.assertEqual(fake_state["segment_status"].get("518_x"), "available")
        archives = fake_state.get("cleared_archives", {}).get("99") or []
        self.assertTrue(archives)
        self.assertEqual(archives[-1].get("entry", {}).get("segment_ids"), ["518_x"])
        self.assertEqual(archives[-1].get("id"), 42)


if __name__ == "__main__":
    unittest.main()
