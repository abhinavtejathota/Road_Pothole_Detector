"""District locate must follow nearest road, not nearest district centroid.

Madhapur / Shilparamam / HITEC are Ranga Reddy even when Medchal's center is closer.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Skip if GIS indexes are not present (CI without data/gis_states).
def _gis_ready() -> bool:
    from routes.survey_service import _state_dir
    rr = _state_dir("telangana") / "index" / "segments" / "518.geojson"
    med = _state_dir("telangana") / "index" / "segments" / "700.geojson"
    return rr.is_file() and med.is_file()


@unittest.skipUnless(_gis_ready(), "district GIS indexes not present")
class TestDistrictLocateAccuracy(unittest.TestCase):
    ALLOWED = ["518", "700"]  # Ranga Reddy + Medchal

    def test_madhapur_corridor_is_ranga_reddy(self):
        from routes.survey_service import (
            annotate_geocode_access,
            locate_point_in_allowed_districts_fast,
        )

        points = [
            ("Shilparamam", 17.4529378, 78.3794936),
            ("Cyber Towers", 17.4504, 78.3811),
            ("Gachibowli", 17.4401, 78.3489),
        ]
        for name, lat, lon in points:
            loc = locate_point_in_allowed_districts_fast(lat, lon, self.ALLOWED)
            self.assertIsNotNone(loc, name)
            self.assertEqual(loc["district_id"], "518", f"{name} should be Ranga Reddy")
            self.assertFalse(loc.get("by_center"), f"{name} must be road-snap, not center")
            row = annotate_geocode_access(
                [{"lat": lat, "lon": lon, "display_name": name}],
                self.ALLOWED,
                require_allowed=True,
            )[0]
            self.assertTrue(row["access_ok"], name)
            self.assertEqual(row["district_id"], "518", name)

    def test_dundigal_is_medchal(self):
        from routes.survey_service import locate_point_in_allowed_districts_fast

        loc = locate_point_in_allowed_districts_fast(17.5997, 78.4175, self.ALLOWED)
        self.assertIsNotNone(loc)
        self.assertEqual(loc["district_id"], "700")


if __name__ == "__main__":
    unittest.main()
