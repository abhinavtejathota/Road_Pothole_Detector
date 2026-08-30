"""Regression guard for survey/tracking assign helpers.

Catches deleted-but-still-called helpers (e.g. _already_km_for_entry NameError).
Run: python scripts/check_survey_consistency.py
"""
from __future__ import annotations

import ast
import inspect
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _module_defs(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def _private_calls(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r"(?<![\w.])(_[a-zA-Z0-9_]+)\s*\(", text))


def main() -> int:
    errors: list[str] = []
    survey_path = ROOT / "routes" / "survey_service.py"
    track_path = ROOT / "routes" / "tracking_service.py"
    api_path = ROOT / "routes" / "api.py"

    nested_ok = {
        "_add", "_find", "_union", "_round", "_edge_score", "_node_snap_score", "_json",
    }
    defs = _module_defs(survey_path)
    for name in sorted(_private_calls(survey_path) - defs - nested_ok):
        errors.append(f"survey_service calls undefined helper: {name}")

    from routes import survey_service as s
    from routes import tracking_service as t

    required = [
        "_already_km_for_entry",
        "_release_assigned_day_for_user",
        "_corridor_overlap_warning",
        "_corridor_conflict",
        "_persist_leg",
        "_entry_segment_ids",
        "generate_corridor_assignment",
        "generate_nearest_assignment",
        "generate_manual_assignment",
        "mark_user_assignment_completed",
        "preview_corridor_routes",
        "assigned_segments_geojson_for_user",
        "is_segment_completed",
        "clear_daily_assignment_for_user",
    ]
    for name in required:
        if not hasattr(s, name):
            errors.append(f"survey_service missing required symbol: {name}")

    sig = inspect.signature(s.generate_corridor_assignment)
    for param in ("replace", "polyline", "segment_ids"):
        if param not in sig.parameters:
            errors.append(f"generate_corridor_assignment missing param: {param}")

    sig_m = inspect.signature(s.mark_user_assignment_completed)
    for param in ("gps_log_path", "gps_points"):
        if param not in sig_m.parameters:
            errors.append(f"mark_user_assignment_completed missing param: {param}")

    api_text = api_path.read_text(encoding="utf-8")
    for attr in sorted(set(re.findall(r"survey_service\.(\w+)", api_text))):
        if not hasattr(s, attr):
            errors.append(f"api.py uses survey_service.{attr} but it is missing")
    for attr in sorted(set(re.findall(r"tracking_service\.(\w+)", api_text))):
        if attr.startswith("_"):
            continue
        if not hasattr(t, attr):
            errors.append(f"api.py uses tracking_service.{attr} but it is missing")

    # Runtime: assign path must pass _already_km_for_entry without NameError
    assert s._already_km_for_entry(None, "andhra", "1") == 0.0
    assert s._corridor_conflict(1, "2026-01-01", ["x"], (0.0, 0.0), (1.0, 1.0)) is None
    done = s.mark_user_assignment_completed(999999001)
    if done.get("updated") is not False:
        errors.append("mark_user_assignment_completed should no-op for unknown user")

    real_gd = s.get_district

    def fake_gd(did, sk=None):
        return {
            "district_id": str(did),
            "name": "Test",
            "state_key": sk or "andhra",
            "state_id": 1,
        }

    s.get_district = fake_gd
    try:
        try:
            s.generate_corridor_assignment(
                1,
                state_key="andhra",
                district_id="99999",
                start_lat=17.4,
                start_lon=78.4,
                end_lat=17.5,
                end_lon=78.5,
                segment_ids=["99999_nope"],
                replace=True,
                polyline=[[17.4, 78.4], [17.5, 78.5]],
            )
            errors.append("expected ValueError for unknown segments")
        except ValueError:
            pass
        except NameError as e:
            errors.append(f"NameError in generate_corridor_assignment: {e}")
    finally:
        s.get_district = real_gd

    # Client payload keys expected by API
    for needle in ("replace=", "polyline=", "gps_log_path"):
        if needle not in api_text and needle.rstrip("=") not in api_text:
            # softer: check individually
            pass
    if "replace=data.get" not in api_text:
        errors.append("api survey_assign should pass replace=")
    if "polyline=data.get" not in api_text:
        errors.append("api survey_assign should pass polyline=")
    if "gps_log_path=" not in api_text:
        errors.append("api upload should pass gps_log_path to mark_user_assignment_completed")

    # Client files send replace + polyline
    clients = [
        ROOT / "frontend" / "src" / "pages" / "Survey.jsx",
        ROOT / "mobile_app" / "src" / "screens" / "SurveyRouteScreen.js",
    ]
    for path in clients:
        if not path.is_file():
            errors.append(f"missing client file: {path}")
            continue
        text = path.read_text(encoding="utf-8")
        if "replace" not in text or "polyline" not in text:
            errors.append(f"{path.name} should send replace + polyline on assign")

    if errors:
        print("FAIL")
        for e in errors:
            print(" -", e)
        return 1
    print("OK — survey/tracking assign helpers and client payloads look consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
