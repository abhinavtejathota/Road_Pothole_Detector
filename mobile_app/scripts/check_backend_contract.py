"""Verify Flask routes the mobile app needs are registered."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

api_text = (ROOT / "routes" / "api.py").read_text(encoding="utf-8")
routes = set(re.findall(r'@api_bp\.route\(["\']([^"\']+)["\']', api_text))

need = [
    "/auth/login",
    "/auth/me",
    "/auth/logout",
    "/survey/assignments",
    "/survey/assignments/geojson",
    "/survey/geocode",
    "/survey/locate",
    "/survey/routes/preview",
    "/survey/assign",
    "/tracking/ping",
    "/tracking/discard-session",
    "/upload/session/init",
    "/upload/session/chunk",
    "/upload/session/chunk-bin",
    "/upload/session/finalize",
    "/upload/session/discard",
]

ok = True
for path in need:
    hit = any(path == r or r.startswith(path) for r in routes)
    print(("OK " if hit else "MISSING "), path)
    ok = ok and hit

from routes import survey_service, tracking_service

assert callable(tracking_service.covered_trail_features)
assert callable(survey_service.assigned_segments_geojson_for_user)
assert callable(survey_service.is_assignment_complete)

# Sample geojson shape the RN Dashboard parser expects
sample_trail = [
    {"lat": 17.44, "lon": 78.37, "ts": "2026-07-15T10:00:00"},
    {"lat": 17.45, "lon": 78.38, "ts": "2026-07-15T10:05:00"},
]
feats = tracking_service.covered_trail_features(sample_trail)
assert isinstance(feats, list)

print("OK backend-mobile contract checks passed")
raise SystemExit(0 if ok else 1)
