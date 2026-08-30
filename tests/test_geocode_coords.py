"""Tests for lat/lon geocode, reverse labels, and location access (TG Madhapur).

Covers fixes from the survey modularization + reverse naming work:
- `_geom_midpoint` available for snap/meta (no NameError on coord geocode)
- Coord queries resolve to Ranga Reddy for VG districts 518/700
- Generic GIS road labels (Road Number N) are not preferred over place names
- Far-away / null-island points stay out of scope with the expected message
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _gis_ready() -> bool:
    from routes.survey_service import _state_dir

    rr = _state_dir("telangana") / "index" / "segments" / "518.geojson"
    med = _state_dir("telangana") / "index" / "segments" / "700.geojson"
    return rr.is_file() and med.is_file()


class TestGeomHelpersInState(unittest.TestCase):
    def test_geom_midpoint_exported_on_survey_service(self):
        from routes import survey_service as ss

        self.assertTrue(callable(getattr(ss, "_geom_midpoint", None)))
        mid = ss._geom_midpoint({
            "type": "LineString",
            "coordinates": [[78.0, 17.0], [78.1, 17.1], [78.2, 17.2]],
        })
        self.assertIsNotNone(mid)
        self.assertAlmostEqual(mid[0], 17.1, places=5)
        self.assertAlmostEqual(mid[1], 78.1, places=5)

    def test_states_dir_points_at_repo_gis(self):
        from routes.survey import state as st

        self.assertTrue(st.STATES_DIR.exists(), st.STATES_DIR)
        self.assertEqual(st.STATES_DIR.name, "gis_states")
        self.assertEqual(st.ROOT, ROOT)


class TestGenericRoadName(unittest.TestCase):
    def test_generic_patterns(self):
        from routes.survey.geocode import _is_generic_road_name

        self.assertTrue(_is_generic_road_name("Road Number 10"))
        self.assertTrue(_is_generic_road_name("Road No. 5"))
        self.assertTrue(_is_generic_road_name("road"))
        self.assertTrue(_is_generic_road_name("Unnamed Road"))
        self.assertTrue(_is_generic_road_name("NH 65"))
        self.assertFalse(_is_generic_road_name("60 Feet road"))
        self.assertFalse(_is_generic_road_name("Gachibowli - Miyapur Road"))


class TestParseLatLon(unittest.TestCase):
    def test_india_and_global(self):
        from routes.survey.geocode import _parse_lat_lon_query

        self.assertEqual(_parse_lat_lon_query("17.4483, 78.3915"), (17.4483, 78.3915))
        # lon,lat India order
        self.assertEqual(_parse_lat_lon_query("78.3915, 17.4483"), (17.4483, 78.3915))
        # Colorado (My location bug case) still parses as a coordinate pair
        co = _parse_lat_lon_query("38.93090, -104.62419")
        self.assertIsNotNone(co)
        self.assertAlmostEqual(co[0], 38.93090, places=4)


@unittest.skipUnless(_gis_ready(), "district GIS indexes not present")
class TestCoordGeocodeAndAccess(unittest.TestCase):
    ALLOWED = ["518", "700"]
    MADHAPUR = (17.4483, 78.3915)

    def test_check_location_access_madhapur(self):
        from routes.survey_service import check_location_access

        lat, lon = self.MADHAPUR
        r = check_location_access(
            lat, lon, allowed_district_ids=self.ALLOWED, require_allowed=True,
        )
        self.assertTrue(r.get("in_scope"), r)
        self.assertEqual(str(r.get("located", {}).get("district_id")), "518")

    def test_null_island_message(self):
        from routes.survey_service import check_location_access

        r = check_location_access(
            0.0, 0.0, allowed_district_ids=self.ALLOWED, require_allowed=True,
        )
        self.assertFalse(r.get("in_scope"))
        self.assertIn("Could not match this location", r.get("message") or "")

    def test_colorado_out_of_scope(self):
        from routes.survey_service import check_location_access

        r = check_location_access(
            38.9309, -104.6242, allowed_district_ids=self.ALLOWED, require_allowed=True,
        )
        self.assertFalse(r.get("in_scope"))

    def test_geocode_coords_returns_ranga_reddy(self):
        from routes.survey_service import annotate_geocode_access, geocode_search

        rows = geocode_search(
            "17.4483, 78.3915",
            state_key="telangana",
            district_ids=self.ALLOWED,
            limit=1,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("source"), "coords")
        self.assertEqual(str(rows[0].get("district_id")), "518")
        ann = annotate_geocode_access(rows, self.ALLOWED, require_allowed=True)[0]
        self.assertTrue(ann.get("access_ok"), ann)

    def test_geocode_coords_label_not_road_number(self):
        from routes.survey.geocode import _is_generic_road_name, _reverse_label_for_point

        # Offline-ish: stub Nominatim to the known Madhapur address shape
        fake_nom = {
            "display_name": (
                "60 Feet road, Ayyappa Society Colony, HITEC City, "
                "Ward 107 Madhapur, Hyderabad, Ranga Reddy, Telangana, India"
            ),
            "address": {
                "road": "60 Feet road",
                "neighbourhood": "Ayyappa Society Colony",
                "suburb": "HITEC City",
                "state_district": "Ranga Reddy",
                "state": "Telangana",
                "country": "India",
            },
        }
        with mock.patch("routes.survey.geocode._nominatim_get", return_value=fake_nom):
            lat, lon = self.MADHAPUR
            label = _reverse_label_for_point(lat, lon, district_ids=self.ALLOWED)
        dn = label.get("display_name") or ""
        self.assertTrue(dn, label)
        self.assertNotIn("Road Number", dn)
        self.assertFalse(_is_generic_road_name(dn.split("·")[-1].strip()))
        # Prefer colony / suburb over GIS Road Number N
        self.assertTrue(
            "HITEC" in dn or "Ayyappa" in dn or "Madhapur" in dn,
            f"expected place-like label, got {dn!r}",
        )


@unittest.skipUnless(_gis_ready(), "district GIS indexes not present")
class TestCoordGeocodeApi(unittest.TestCase):
    """Flask test-client: /api/survey/geocode with lat,lon as videographer."""

    def test_geocode_coords_as_video(self):
        from web_app import create_app

        app = create_app()
        c = app.test_client()
        logged = False
        for user, pw in (("video", "video"), ("video4", "video4"), ("video3", "video3")):
            r = c.post("/api/auth/login", json={"username": user, "password": pw})
            if r.status_code == 200:
                logged = True
                break
        if not logged:
            self.skipTest("no videographer credentials available")

        me = c.get("/api/auth/me").get_json() or {}
        if not me.get("is_videographer"):
            self.skipTest("login succeeded but not videographer")

        # Ensure TG districts for video account when possible
        ids = [str(x) for x in (me.get("district_ids") or [])]
        if "518" not in ids and user == "video":
            self.skipTest("video account missing district 518")

        resp = c.get("/api/survey/geocode", query_string={
            "q": "17.4483, 78.3915",
            "state_key": "telangana",
            "district_ids": ",".join(ids or ["518", "700"]),
        })
        self.assertEqual(resp.status_code, 200, resp.get_json())
        body = resp.get_json() or {}
        results = body.get("results") or []
        self.assertTrue(results, body)
        top = results[0]
        self.assertNotIn("Geocode failed", str(body))
        self.assertNotIn("_geom_midpoint", str(body))
        if top.get("access_ok"):
            self.assertEqual(str(top.get("district_id")), "518")
            self.assertNotIn("Road Number", top.get("display_name") or "")


if __name__ == "__main__":
    unittest.main()
