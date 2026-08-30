"""
Detection DOCX reports → S3 smartroad-reports/<username>/…

Short unique names (route labels live in DB start_label/end_label, not the filename):
  video:  <user12>_s<sessionId>_<YYYYMMDD-HHMMSS>.mp4
  report: SR_<user12>_s<sessionId>_<YYYYMMDD-HHMMSS>.docx
"""
from __future__ import annotations

import io
import math
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parents[2]
LOGO_CANDIDATES = [
    ROOT / "frontend" / "public" / "logo.png",
    ROOT / "frontend" / "src" / "styles" / "logo.jpeg",
    ROOT / "mobile_app" / "assets" / "icon.png",
]
POTHOLE_BUFFER_KM = 0.05  # ~50 m stretch credited as "has pothole" per detection
# Unique GPS-log coverage grid (~40 m). Circles / out-and-back do not re-count.
GPS_LOG_CELL_M = float(os.getenv("REPORT_GPS_CELL_M", "40"))


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def unique_track_coverage_km(
    points: list[tuple[float, float]],
    *,
    cell_m: float = GPS_LOG_CELL_M,
) -> float:
    """Length of ground covered by a GPS track without re-counting revisits.

    Walks the cleaned path, marks ~cell_m grid cells, returns
    ``unique_cells × cell_km``. Circling the same block or out-and-back on the
    same road counts once. Teleports / stationary jitter are filtered first.
    """
    if len(points) < 2 or cell_m <= 0:
        return 0.0
    try:
        from routes.tracking_service import _prepare_trail_runs_for_distance
        runs = _prepare_trail_runs_for_distance(
            [(float(lat), float(lon), None) for lat, lon in points]
        )
    except Exception:
        runs = [[(float(lat), float(lon), None) for lat, lon in points]]

    cell_km = float(cell_m) / 1000.0
    dlat = cell_km / 111.32
    visited: set[tuple[int, int]] = set()
    path_km = 0.0

    def _mark(lat: float, lon: float):
        # lon cell width scales with latitude
        dlon = cell_km / max(0.2, 111.32 * math.cos(math.radians(lat)))
        visited.add((int(round(lat / dlat)), int(round(lon / dlon))))

    for run in runs:
        coords = [(float(p[0]), float(p[1])) for p in run]
        if len(coords) < 2:
            if coords:
                _mark(coords[0][0], coords[0][1])
            continue
        _mark(coords[0][0], coords[0][1])
        for i in range(1, len(coords)):
            a_lat, a_lon = coords[i - 1]
            b_lat, b_lon = coords[i]
            seg = _haversine_km(a_lat, a_lon, b_lat, b_lon)
            if seg <= 0.001:
                _mark(b_lat, b_lon)
                continue
            # After Douglas-Peucker, long straight segments are normal — sample
            # them into the grid (do not treat as teleports here).
            path_km += seg
            steps = max(1, int(math.ceil(seg / cell_km)))
            for s in range(1, steps + 1):
                t = s / steps
                _mark(a_lat + (b_lat - a_lat) * t, a_lon + (b_lon - a_lon) * t)

    unique_km = len(visited) * cell_km
    # Never claim more than the cleaned path actually travelled
    if path_km > 0:
        return round(min(unique_km, path_km), 3)
    return round(unique_km, 3)


def _gps_points_from_log_path(path: str) -> list[tuple[float, float]]:
    from pothole_detector import load_gps_log

    gps_map = load_gps_log(path) or {}
    out: list[tuple[float, float]] = []
    for _sec, triple in sorted(gps_map.items(), key=lambda kv: kv[0]):
        try:
            lat, lon = float(triple[0]), float(triple[1])
        except (TypeError, ValueError, IndexError):
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            out.append((lat, lon))
    return out


def lookup_s3_gps_track_km(s3_key: str | None) -> tuple[float, dict]:
    """Download the video's sibling GPS log from S3 and compute unique coverage km.

    Full track is NOT stored in Postgres — only per-detection lat/lon copies.
    The authoritative path is the sibling CSV/XLSX/JSON next to the video.
    """
    meta: dict = {"source": None, "points": 0, "s3_key": None}
    key = (s3_key or "").strip()
    if not key:
        return 0.0, meta
    try:
        import s3_utils
        from routes.field_upload_service import processed_source_key
    except Exception as e:
        meta["error"] = str(e)
        return 0.0, meta

    input_bucket = s3_utils.get_input_bucket()
    processed_bucket = s3_utils.get_processed_bucket()
    candidates: list[tuple[str, str]] = []
    # Prefer original upload location, then archived processed copy
    for bucket, media in (
        (input_bucket, key),
        (processed_bucket, processed_source_key(key)),
        (processed_bucket, key),
    ):
        if not bucket or not media:
            continue
        try:
            sibling = s3_utils.find_sibling_gps_key(bucket, media)
        except Exception:
            sibling = None
        if sibling:
            candidates.append((bucket, sibling))

    seen: set[tuple[str, str]] = set()
    for bucket, sibling in candidates:
        pair = (bucket, sibling)
        if pair in seen:
            continue
        seen.add(pair)
        suffix = Path(sibling).suffix or ".json"
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                local = tmp.name
            try:
                s3_utils.download_file(bucket, sibling, local)
                pts = _gps_points_from_log_path(local)
            finally:
                try:
                    os.unlink(local)
                except OSError:
                    pass
        except Exception as e:
            meta["error"] = str(e)
            continue
        if len(pts) < 2:
            continue
        km = unique_track_coverage_km(pts)
        meta.update({
            "source": "s3_gps_log",
            "points": len(pts),
            "s3_key": f"s3://{bucket}/{sibling}",
            "raw_unique_km": km,
        })
        return float(km or 0), meta
    return 0.0, meta


def _span_km_from_potholes(potholes: list[dict]) -> float:
    """DEPRECATED fallback — bbox extent of detection pins, NOT hop-sum.

    Hop-summing consecutive pothole GPS points inflated short surveys (GPS
    jitter × N detections). Prefer ``lookup_s3_gps_track_km`` instead.
    """
    rows = [
        p for p in potholes
        if p.get("lat") is not None and p.get("lon") is not None
    ]
    if len(rows) < 2:
        return 0.0
    lats = [float(p["lat"]) for p in rows]
    lons = [float(p["lon"]) for p in rows]
    # Axis-aligned extent (not hop sum) — honest upper bound when no GPS log
    corner_km = _haversine_km(min(lats), min(lons), max(lats), max(lons))
    return round(float(corner_km), 3)


def _safe_slug(text: str, max_len: int = 48) -> str:
    s = re.sub(r"[^\w.\-]+", "-", str(text or "").strip()).strip("-.")
    return (s or "unknown")[:max_len]


def short_place_label(label: str, max_len: int = 22) -> str:
    """Compact place for UI captions (not used in S3 filenames)."""
    s = (label or "").strip()
    if not s:
        return ""
    low = s.lower()
    if low.startswith("my location") or "my-location" in low or "my_location" in low:
        m = re.search(r"(-?\d+\.\d+)\s*[, ]\s*(-?\d+\.\d+)", s)
        if m:
            return f"loc{m.group(1)[:8]}_{m.group(2)[:8]}"[:max_len]
        return "MyLoc"[:max_len]
    s = s.split(",")[0].strip()
    return _safe_slug(s, max_len)


def short_route_caption(start_label: str, end_label: str) -> str:
    a = short_place_label(start_label, 18)
    b = short_place_label(end_label, 18)
    if a and b:
        return f"{a} → {b}"
    return a or b or ""


def _logo_path() -> Path | None:
    for p in LOGO_CANDIDATES:
        if p.is_file():
            return p
    return None


def _bbox_area(row: dict) -> float:
    try:
        return max(0.0, float(row["x2"] - row["x1"]) * float(row["y2"] - row["y1"]))
    except Exception:
        return 0.0


def resolve_uploader(s3_key: str | None) -> tuple[str, int | None, bool]:
    """Return (username, user_id|None, is_legacy)."""
    from routes.field_upload_service import split_field_key
    import db_utils

    meta = split_field_key(s3_key or "")
    username = (meta.get("username") or "").strip()
    if not username or username in ("_unscoped", "_unsorted"):
        return "Legacy", None, True
    user_id = None
    try:
        if db_utils.is_db_configured():
            u = db_utils.get_user_by_username(username)
            if u:
                user_id = int(u["id"])
    except Exception:
        pass
    return username, user_id, False


def lookup_assignment_for_report(
    user_id: int,
    processed_at: str | None,
    *,
    potholes: list | None = None,
) -> tuple[dict | None, str | None]:
    """Resolve (assignment_entry, work_date) for a detection report.

    Strict: only the video/session calendar day. Do **not** carry over an
    incomplete prior-day assignment and do **not** grab a ±5-day neighbour —
    that made Jul-18 videos show Jul-15 survey dates / corridor km.
    If nothing exists for that day → ``(None, None)``.
    """
    import db_utils
    from routes import survey_service

    day = str(processed_at)[:10] if processed_at else ""
    if not (len(day) == 10 and day[4] == "-"):
        # Prefer capture timestamps only when they share one common day
        days: list[str] = []
        for p in potholes or []:
            for key in ("captured_at", "ts", "timestamp"):
                raw = p.get(key)
                if not raw:
                    continue
                d = str(raw)[:10]
                if len(d) == 10 and d[4] == "-" and d not in days:
                    days.append(d)
        day = days[0] if len(days) == 1 else (survey_service.today_ist() if not days else "")
        if len(days) > 1:
            # Ambiguous capture days — do not invent an assignment link
            return None, None
    if not day:
        return None, None

    entry = None
    if db_utils.is_db_configured():
        try:
            entry = db_utils.survey_db_get_assignment(int(user_id), day)
        except Exception:
            entry = None
    if not entry:
        try:
            st = survey_service._load_state()
            entry = (
                (st.get("daily_assignments", {}).get(day, {}) or {}).get(
                    survey_service._user_day_key(int(user_id))
                )
                or None
            )
        except Exception:
            entry = None
    if isinstance(entry, dict) and (
        entry.get("segment_ids")
        or entry.get("start")
        or entry.get("end")
        or entry.get("route_km")
        or entry.get("corridor_km")
    ):
        return entry, day
    return None, None


def resolve_start_end_labels(
    *,
    username: str,
    user_id: int | None,
    potholes: list[dict],
    processed_at: str | None = None,
) -> tuple[str, str, dict | None]:
    """
    Prefer assignment corridor pin labels; ad-hoc falls back to compact lat,lon
    (no Nominatim street invention for report route titles).
    Returns (start_label, end_label, assignment_entry|None).
    """
    asg = None
    try:
        if user_id is not None:
            asg, work_date = lookup_assignment_for_report(
                int(user_id), processed_at, potholes=potholes,
            )
            if asg:
                if work_date:
                    asg = dict(asg)
                    asg["_work_date"] = work_date
                pins = asg.get("pins") or []
                start_pin = next((p for p in pins if str(p.get("endpoint") or "").lower() == "start"), None)
                end_pin = next((p for p in pins if str(p.get("endpoint") or "").lower() == "end"), None)
                start_lbl = (start_pin or {}).get("label") or (start_pin or {}).get("name")
                end_lbl = (end_pin or {}).get("label") or (end_pin or {}).get("name")
                if not start_lbl:
                    start_lbl = (asg.get("start") or {}).get("label") or asg.get("start_label")
                if not end_lbl:
                    end_lbl = (asg.get("end") or {}).get("label") or asg.get("end_label")
                if not start_lbl or not end_lbl:
                    route = str(asg.get("route_name") or asg.get("label") or "")
                    if "→" in route or "->" in route:
                        parts = re.split(r"\s*(?:→|->)\s*", route, maxsplit=1)
                        if len(parts) == 2:
                            start_lbl = start_lbl or parts[0].strip()
                            end_lbl = end_lbl or parts[1].strip()
                if start_lbl and end_lbl:
                    return str(start_lbl), str(end_lbl), asg
                if start_lbl or end_lbl:
                    return (
                        str(start_lbl or "start"),
                        str(end_lbl or "end"),
                        asg,
                    )
                return "start", "end", asg
    except Exception:
        asg = None

    pts = [
        (float(p["lat"]), float(p["lon"]))
        for p in potholes
        if p.get("lat") is not None and p.get("lon") is not None
    ]
    if len(pts) >= 1:
        # Ad-hoc: compact coords only — do not invent Nominatim street names.
        def _coord_lbl(lat: float, lon: float) -> str:
            return f"{lat:.5f}, {lon:.5f}"

        start_lbl = _coord_lbl(*pts[0])
        end_lbl = _coord_lbl(*pts[-1]) if len(pts) > 1 else start_lbl
        return start_lbl, end_lbl, asg
    return "start", "end", asg


def _corridor_km_from_assignment(assignment: dict | None) -> float:
    if not assignment:
        return 0.0
    try:
        from routes.survey_service import _route_km_for_entry
        return float(_route_km_for_entry(assignment) or 0)
    except Exception:
        pass
    for key in ("route_km", "corridor_km", "preferred_km"):
        try:
            v = float(assignment.get(key) or 0)
            if v > 0.2:
                return v
        except (TypeError, ValueError):
            continue
    return 0.0


def lookup_tracking_covered_km(
    user_id: int | None,
    processed_at: str | None,
    *,
    assignment: dict | None = None,
    work_date: str | None = None,
    potholes: list | None = None,
) -> tuple[float, dict]:
    """GPS covered km (+ by_class) — Postgres tracking/assignment first, then resolve."""
    if user_id is None:
        if assignment:
            try:
                return float(assignment.get("covered_km") or 0), dict(
                    assignment.get("covered_by_class") or {}
                )
            except (TypeError, ValueError):
                pass
        return 0.0, {}
    try:
        import db_utils
        from routes import survey_service

        # Detection report: use the video day only — never carry over a prior
        # incomplete assignment date (that pulled Jul-15 GPS onto a Jul-18 video).
        date = str(processed_at)[:10] if processed_at else ""
        if not (len(date) == 10 and date[4] == "-"):
            date = work_date or (str(assignment.get("_work_date"))[:10] if assignment else "")
        if not (len(str(date or "")) == 10 and str(date)[4] == "-"):
            date = survey_service.today_ist()
        date = str(date)[:10]

        km = 0.0
        by_class: dict = {}

        if db_utils.is_db_configured():
            try:
                t_km, t_by = db_utils.tracking_db_get_covered(int(user_id), date)
                if t_km > km:
                    km, by_class = float(t_km), dict(t_by or {})
            except Exception:
                pass
            try:
                asg = assignment if (
                    assignment and str(assignment.get("_work_date") or date)[:10] == date
                ) else None
                if asg is None:
                    asg = db_utils.survey_db_get_assignment(int(user_id), date)
                if asg:
                    a_km = float(asg.get("covered_km") or 0)
                    if a_km > km:
                        km = a_km
                        by_class = dict(asg.get("covered_by_class") or by_class)
            except Exception:
                pass

        if km < 0.05:
            from routes import tracking_service
            r_km, r_by, _ = tracking_service.resolve_videographer_coverage(
                int(user_id),
                date,
                resolve_carryover=False,
                sync_assignment=False,
            )
            if float(r_km or 0) > km:
                km, by_class = float(r_km or 0), dict(r_by or {})

        return float(km or 0), dict(by_class or {})
    except Exception:
        if assignment:
            try:
                return float(assignment.get("covered_km") or 0), dict(
                    assignment.get("covered_by_class") or {}
                )
            except (TypeError, ValueError):
                pass
        return 0.0, {}


def _assignment_start_end(assignment: dict | None) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    if not assignment:
        return None, None
    start = assignment.get("start") or {}
    end = assignment.get("end") or {}
    try:
        s = (float(start["lat"]), float(start["lon"])) if start.get("lat") is not None else None
    except (TypeError, ValueError, KeyError):
        s = None
    try:
        e = (float(end["lat"]), float(end["lon"])) if end.get("lat") is not None else None
    except (TypeError, ValueError, KeyError):
        e = None
    if not s or not e:
        for pin in assignment.get("pins") or []:
            try:
                pt = (float(pin["lat"]), float(pin["lon"]))
            except (TypeError, ValueError, KeyError):
                continue
            ep = str(pin.get("endpoint") or "").lower()
            if ep == "start" and not s:
                s = pt
            elif ep == "end" and not e:
                e = pt
    return s, e


def _project_km_along(lat: float, lon: float, start: tuple[float, float], end: tuple[float, float], route_km: float) -> float:
    """Approximate distance along start→end (clamped)."""
    if route_km <= 0:
        return 0.0
    import math
    lat0 = math.radians((start[0] + end[0]) / 2)
    sx = (start[1]) * math.cos(lat0)
    sy = start[0]
    ex = (end[1]) * math.cos(lat0)
    ey = end[0]
    px = lon * math.cos(lat0)
    py = lat
    dx, dy = ex - sx, ey - sy
    denom = dx * dx + dy * dy
    if denom <= 1e-18:
        return 0.0
    t = ((px - sx) * dx + (py - sy) * dy) / denom
    t = max(0.0, min(1.0, t))
    return t * float(route_km)


def _affected_km_merged(
    potholes: list[dict],
    *,
    analysed_km: float,
    assignment: dict | None = None,
    buffer_km: float = POTHOLE_BUFFER_KM,
) -> float:
    """Union of ~buffer_km intervals along the corridor (not n×buffer)."""
    if analysed_km <= 0 or not potholes:
        return 0.0
    start, end = _assignment_start_end(assignment)
    route_km = analysed_km
    try:
        if assignment:
            route_km = max(
                analysed_km,
                float(assignment.get("route_km") or assignment.get("corridor_km") or 0) or analysed_km,
            )
    except (TypeError, ValueError):
        pass

    positions: list[float] = []
    if start and end and route_km > 0.2:
        for poth in potholes:
            if poth.get("lat") is None or poth.get("lon") is None:
                continue
            try:
                positions.append(
                    _project_km_along(float(poth["lat"]), float(poth["lon"]), start, end, route_km)
                )
            except (TypeError, ValueError):
                continue
    if not positions:
        centers: list[tuple[float, float]] = []
        for poth in potholes:
            if poth.get("lat") is None or poth.get("lon") is None:
                continue
            try:
                lat, lon = float(poth["lat"]), float(poth["lon"])
            except (TypeError, ValueError):
                continue
            if any(_haversine_km(lat, lon, c[0], c[1]) < 2 * buffer_km for c in centers):
                continue
            centers.append((lat, lon))
        return round(min(analysed_km, len(centers) * 2 * buffer_km), 3)

    positions.sort()
    intervals = [(max(0.0, t - buffer_km), min(route_km, t + buffer_km)) for t in positions]
    merged: list[list[float]] = []
    for a, b in intervals:
        if not merged or a > merged[-1][1]:
            merged.append([a, b])
        else:
            merged[-1][1] = max(merged[-1][1], b)
    affected = sum(b - a for a, b in merged)
    return round(min(analysed_km, max(0.0, affected)), 3)


def _road_label(meta: dict) -> str:
    name = (meta.get("name") or "").strip()
    ref = (meta.get("ref") or "").strip()
    if name and ref:
        return f"{name} ({ref})"
    return name or ref or "Unnamed corridor segment"


def _roads_from_assignment(assignment: dict | None, potholes: list[dict]) -> list[dict]:
    """Roads on the assigned/sealed corridor — not Nominatim street noise."""
    if not assignment:
        return []
    try:
        from routes import survey_service
    except Exception:
        return []

    ids = survey_service._entry_segment_ids(assignment)
    if not ids:
        return []
    status_map = (survey_service._load_state() or {}).get("segment_status") or {}
    scopes = survey_service._assignment_scopes(assignment)

    sealed_ids = {sid for sid in ids if status_map.get(sid) in ("completed", "verified")}
    use_ids = sealed_ids or set(ids)

    buckets: dict[str, dict] = {}
    meta_by_sid: dict[str, dict] = {}
    for sk, did in scopes:
        meta = survey_service._segment_meta(sk, str(did))
        for sid in use_ids:
            if sid not in meta:
                continue
            m = meta[sid]
            meta_by_sid[sid] = m
            label = _road_label(m)
            b = buckets.setdefault(label, {"name": label, "road_km": 0.0, "potholes": 0, "sids": []})
            b["road_km"] += float(m.get("length") or 0)
            b["sids"].append(sid)

    if not buckets:
        return []

    for poth in potholes:
        if poth.get("lat") is None or poth.get("lon") is None:
            continue
        try:
            lat, lon = float(poth["lat"]), float(poth["lon"])
        except (TypeError, ValueError):
            continue
        best_label, best_d = None, 0.25
        for sid, m in meta_by_sid.items():
            mid = m.get("mid")
            if not mid:
                continue
            d = _haversine_km(lat, lon, float(mid[0]), float(mid[1]))
            if d < best_d:
                best_d = d
                best_label = _road_label(m)
        if best_label and best_label in buckets:
            buckets[best_label]["potholes"] += 1

    rows = []
    for b in sorted(buckets.values(), key=lambda x: -x["road_km"]):
        rows.append({
            "name": b["name"],
            "potholes": int(b["potholes"]),
            "road_km": round(float(b["road_km"]), 3),
            "proper_km": 0.0,
            "affected_km": 0.0,
        })
    return rows


def compute_road_stats(
    potholes: list[dict],
    assignment: dict | None,
    *,
    covered_km: float | None = None,
    gps_log_km: float | None = None,
) -> dict:
    """
    Corridor / covered / proper-vs-affected km for the detection report.

    Length priority:
      1) survey GPS covered (tracking / assignment)
      2) assigned corridor
      3) unique coverage from the S3 GPS log (no circle re-count)
      4) detection bbox extent (last resort — never hop-sum pothole pins)
    """
    corridor_km = _corridor_km_from_assignment(assignment)
    if covered_km is not None:
        gps_covered = float(covered_km or 0)
    elif assignment:
        try:
            gps_covered = float(assignment.get("covered_km") or 0)
        except (TypeError, ValueError):
            gps_covered = 0.0
    else:
        gps_covered = 0.0

    log_km = float(gps_log_km or 0)
    span_km = 0.0
    if corridor_km < 0.2 and gps_covered < 0.2 and log_km < 0.05:
        span_km = _span_km_from_potholes(potholes)

    if gps_covered > 0.2:
        analysed_km = gps_covered
        length_source = "gps_covered"
    elif corridor_km > 0.2:
        analysed_km = corridor_km
        length_source = "corridor"
    elif log_km > 0.05:
        analysed_km = log_km
        length_source = "s3_gps_log"
    elif span_km > 0:
        analysed_km = span_km
        length_source = "bbox_extent"
    else:
        analysed_km = 0.0
        length_source = "none"

    affected = _affected_km_merged(potholes, analysed_km=analysed_km, assignment=assignment)
    proper_km = max(0.0, analysed_km - affected)
    clear_pct = round((proper_km / analysed_km) * 100, 1) if analysed_km > 0 else 0.0
    pothole_pct = round((affected / analysed_km) * 100, 1) if analysed_km > 0 else 0.0

    per_road = _roads_from_assignment(assignment, potholes)
    if per_road:
        total_n = max(1, sum(r["potholes"] for r in per_road) or len(potholes) or 1)
        for r in per_road:
            frac = (r["potholes"] / total_n) if total_n else 0
            road_aff = min(float(r["road_km"]), affected * frac) if r["road_km"] > 0 else 0.0
            if r["potholes"] > 0 and road_aff <= 0 and analysed_km > 0:
                road_aff = min(analysed_km * frac, affected * frac)
            r["affected_km"] = round(road_aff, 3)
            r["proper_km"] = round(max(0.0, float(r["road_km"]) - road_aff), 3)
    else:
        # No GIS assignment segments — do NOT invent Nominatim street shares.
        if analysed_km > 0.05 or potholes:
            per_road = [{
                "name": "Survey corridor",
                "potholes": len(potholes),
                "proper_km": round(proper_km, 3),
                "affected_km": round(affected, 3),
                "road_km": round(analysed_km, 3),
            }]

    return {
        "total_km": round(analysed_km, 3),
        "corridor_km": round(corridor_km, 3),
        "covered_km": round(gps_covered, 3),
        "gps_log_km": round(log_km, 3),
        "proper_km": round(proper_km, 3),
        "affected_km": round(affected, 3),
        "clear_pct": clear_pct,
        "pothole_pct": pothole_pct,
        "assigned_km": round(corridor_km, 3),
        "span_km": round(span_km, 3),
        "length_source": length_source,
        "per_road": per_road,
    }


def top5_large_potholes(potholes: list[dict]) -> list[dict]:
    ranked = sorted(potholes, key=_bbox_area, reverse=True)
    out = []
    for poth in ranked:
        if len(out) >= 5:
            break
        lat, lon = poth.get("lat"), poth.get("lon")
        try:
            lat_f = float(lat) if lat is not None else None
            lon_f = float(lon) if lon is not None else None
        except (TypeError, ValueError):
            lat_f = lon_f = None
        link = (poth.get("map_link") or "").strip()
        if not link and lat_f is not None and lon_f is not None:
            link = f"https://www.google.com/maps?q={lat_f:.6f},{lon_f:.6f}"
        if _bbox_area(poth) <= 0 and not link:
            continue
        out.append({
            **poth,
            "lat": lat_f if lat_f is not None else poth.get("lat"),
            "lon": lon_f if lon_f is not None else poth.get("lon"),
            "bbox_area": round(_bbox_area(poth), 1),
            "map_link": link,
        })
    return out


def severity_distribution(potholes: list[dict]) -> dict[str, int]:
    """Count Low/Medium/High from stored detection rows (same labels as utils.severity_*)."""
    counts = {"Low": 0, "Medium": 0, "High": 0, "Unknown": 0}
    for p in potholes or []:
        raw = str(p.get("severity") or "").strip().lower()
        if raw in ("low", "l"):
            counts["Low"] += 1
        elif raw in ("medium", "med", "m"):
            counts["Medium"] += 1
        elif raw in ("high", "critical", "h"):
            counts["High"] += 1
        else:
            counts["Unknown"] += 1
    return counts


def priority_action_rows(per_road: list[dict], *, limit: int = 10) -> list[dict]:
    """Priority list from Section 04 data — density then affected share (no costing)."""
    rows = []
    for r in per_road or []:
        km = float(r.get("road_km") or 0)
        n = int(r.get("potholes") or 0)
        aff = float(r.get("affected_km") or 0)
        dens = (n / km) if km > 0.05 else float(n)
        aff_pct = (aff / km * 100.0) if km > 0.05 else 0.0
        rows.append({
            "name": str(r.get("name") or "Segment"),
            "potholes": n,
            "road_km": round(km, 3),
            "density": round(dens, 2),
            "affected_km": round(aff, 3),
            "affected_pct": round(aff_pct, 1),
        })
    rows.sort(key=lambda x: (x["density"], x["affected_pct"], x["potholes"]), reverse=True)
    return rows[: max(1, int(limit))]


def display_corridor_km(corridor_km: float, *, has_assignment: bool) -> str:
    if float(corridor_km or 0) > 0.05:
        return f"{float(corridor_km):.2f} km"
    if has_assignment:
        return "Assigned (length not stored)"
    return "Not assigned - ad-hoc survey"


def display_gps_covered(covered_km: float, corridor_km: float = 0.0) -> str:
    """Client-facing GPS covered — show 0 km honestly when survey tracking is empty."""
    cov = float(covered_km or 0)
    cor = float(corridor_km or 0)
    if cov > 0.05:
        if cor > 0.2:
            pct = round((cov / cor) * 100, 1)
            return f"{cov:.2f} km ({pct}% of corridor)"
        return f"{cov:.2f} km"
    return "0 km"


def model_version_label() -> str:
    raw = (os.getenv("MODEL_PATH") or "").strip()
    if raw:
        return Path(raw.replace("\\", "/")).name
    default = ROOT / "artifacts/models" / "smartroad_ap.pt"
    return default.name if default.is_file() else "smartroad_ap.pt"


def log_report_data_gaps(stats: dict, *, session_id: int | None = None) -> list[str]:
    """Server-side warnings when headline fields would have been blank placeholders."""
    gaps: list[str] = []
    if float(stats.get("corridor_km") or 0) <= 0.05:
        gaps.append("assigned_corridor_missing")
    if float(stats.get("covered_km") or 0) <= 0.05:
        gaps.append("gps_covered_missing")
    src = str(stats.get("length_source") or "")
    if src == "bbox_extent":
        gaps.append("analysed_length_bbox_fallback")
    if src == "none":
        gaps.append("analysed_length_missing")
    if gaps:
        sid = f" session={session_id}" if session_id is not None else ""
        print(f"[report] data gaps{sid}: {', '.join(gaps)}", flush=True)
    return gaps


def enrich_stats_for_report(
    stats: dict,
    potholes: list[dict],
    *,
    has_assignment: bool,
    session_id: int | None = None,
) -> dict:
    """Attach display strings + severity/priority derived from the same DB-backed stats."""
    out = dict(stats or {})
    corridor = float(out.get("corridor_km") or 0)
    covered = float(out.get("covered_km") or 0)
    out["has_assignment"] = bool(has_assignment)
    out["corridor_display"] = display_corridor_km(corridor, has_assignment=has_assignment)
    out["covered_display"] = display_gps_covered(covered, corridor)
    out["severity_counts"] = severity_distribution(potholes)
    out["priority_actions"] = priority_action_rows(out.get("per_road") or [], limit=10)
    out["model_version"] = model_version_label()
    out["data_gaps"] = log_report_data_gaps(out, session_id=session_id)
    return out


def lookup_prior_survey_summary(
    *,
    username: str,
    start_label: str,
    end_label: str,
    before_session_id: int,
    processed_at: str | None,
) -> dict | None:
    """Previous detection report on a similar corridor (same VG + start/end labels)."""
    import db_utils

    if not username or username in ("Legacy", "_unscoped"):
        return None
    try:
        rows = db_utils.get_all_sessions_for_reports()
    except Exception:
        return None
    sl = (start_label or "").strip().lower()
    el = (end_label or "").strip().lower()
    if len(sl) < 3 or len(el) < 2:
        return None
    candidates = []
    for r in rows or []:
        try:
            sid = int(r.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if sid >= int(before_session_id):
            continue
        if (r.get("username") or "").strip() != username:
            continue
        if not r.get("report_s3_key"):
            continue
        rsl = (r.get("start_label") or "").strip().lower()
        rel = (r.get("end_label") or "").strip().lower()
        if not rsl or not rel:
            continue
        if rsl == sl and rel == el:
            candidates.append(r)
    if not candidates:
        return None
    candidates.sort(key=lambda x: str(x.get("processed_at") or ""), reverse=True)
    prev = candidates[0]
    try:
        n = int(prev.get("total_potholes") or 0)
    except (TypeError, ValueError):
        n = 0
    return {
        "session_id": int(prev["id"]),
        "processed_at": str(prev.get("processed_at") or "")[:19],
        "total_potholes": n,
        "start_label": prev.get("start_label"),
        "end_label": prev.get("end_label"),
    }


def build_report_json_payload(
    *,
    report_id: str,
    session_id: int,
    username: str,
    start_label: str,
    end_label: str,
    stats: dict,
    top5: list[dict],
    total_potholes: int,
    processed_at: str,
    prior: dict | None = None,
) -> dict:
    """Machine-readable sibling of the DOCX — same numbers, no Word layout."""
    return {
        "report_id": report_id,
        "session_id": int(session_id),
        "username": username,
        "route": {"start": start_label, "end": end_label},
        "survey_processed_at": processed_at,
        "model_version": stats.get("model_version") or model_version_label(),
        "total_potholes": int(total_potholes),
        "stats": {
            "total_km": stats.get("total_km"),
            "corridor_km": stats.get("corridor_km"),
            "covered_km": stats.get("covered_km"),
            "gps_log_km": stats.get("gps_log_km"),
            "proper_km": stats.get("proper_km"),
            "affected_km": stats.get("affected_km"),
            "clear_pct": stats.get("clear_pct"),
            "pothole_pct": stats.get("pothole_pct"),
            "length_source": stats.get("length_source"),
            "span_km": stats.get("span_km"),
            "corridor_display": stats.get("corridor_display"),
            "covered_display": stats.get("covered_display"),
            "severity_counts": stats.get("severity_counts"),
            "data_gaps": stats.get("data_gaps") or [],
        },
        "roads": stats.get("per_road") or [],
        "priority_actions": stats.get("priority_actions") or [],
        "top5": [
            {
                "severity": t.get("severity"),
                "bbox_area_px2": t.get("bbox_area"),
                "lat": t.get("lat"),
                "lon": t.get("lon"),
                "map_link": t.get("map_link"),
            }
            for t in (top5 or [])
        ],
        "vs_last_survey": prior,
    }


def severity_legend_rows() -> list[tuple[str, str]]:
    """Document the same thresholds as utils.severity_from_area_and_position."""
    return [
        ("Low", "Score < 0.20 — smaller / farther-up-frame boxes"),
        ("Medium", "Score 0.20–0.40 — mid-sized or mid-frame boxes"),
        ("High", "Score ≥ 0.40 — larger and/or nearer (lower in frame)"),
        (
            "Score",
            "0.6 × (box_area / frame_height²) + 0.4 × (y_center / frame_height)",
        ),
        (
            "Size column",
            "Bounding-box area (px²) is a temporary proxy — not comparable across camera mounts",
        ),
    ]



def build_display_names(
    *,
    username: str,
    session_id: int | None = None,
    start_label: str = "",
    end_label: str = "",
    when: datetime | None = None,
) -> tuple[str, str]:
    """(display_video_name, report_docx_name) — short + unique via session id + stamp.

    Route text stays in DB ``start_label`` / ``end_label`` (and report body), not
    in the S3 object name — that was causing 100+ char filenames that clip the UI.
    """
    import hashlib

    when = when or datetime.now(IST)
    stamp = when.strftime("%Y%m%d-%H%M%S")
    user_s = _safe_slug(username, 12) or "user"
    if session_id is not None:
        tag = f"s{int(session_id)}"
    else:
        dig = hashlib.sha1(
            f"{username}|{start_label}|{end_label}|{stamp}".encode("utf-8")
        ).hexdigest()[:6]
        tag = f"t{dig}"
    video = f"{user_s}_{tag}_{stamp}.mp4"
    report = f"SR_{user_s}_{tag}_{stamp}.docx"
    return video, report


def _pie_chart_png(proper_km: float, affected_km: float) -> bytes | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    proper = max(0.0, float(proper_km))
    affected = max(0.0, float(affected_km))
    if proper + affected <= 0:
        proper, affected = 1.0, 0.0
    fig, ax = plt.subplots(figsize=(5.2, 3.6), dpi=140)
    fig.patch.set_facecolor("white")
    wedges, texts, autotexts = ax.pie(
        [proper, affected],
        labels=["Proper / clear", "Has potholes"],
        colors=["#1e7a4c", "#b3261e"],
        autopct="%1.1f%%",
        startangle=90,
        textprops={"fontsize": 10, "color": "#16202a", "fontfamily": "sans-serif"},
        wedgeprops={"width": 0.55, "edgecolor": "white", "linewidth": 2},
        pctdistance=0.72,
    )
    for t in autotexts:
        t.set_color("white")
        t.set_fontweight("600")
        t.set_fontsize(9)
    ax.set_title(
        "Corridor condition (km share)",
        fontsize=12,
        color="#0f3a5f",
        fontweight="600",
        pad=12,
    )
    ax.text(0, 0, f"{proper + affected:.1f}\nkm", ha="center", va="center",
            fontsize=11, color="#0f3a5f", fontweight="600")
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", facecolor="white", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


# Brand tokens — match portal (frontend/src/styles/global.css)
_INK = (0x16, 0x20, 0x2A)          # --text
_INK_SOFT = (0x5B, 0x6B, 0x78)     # --muted
_ACCENT = (0x0F, 0x3A, 0x5F)       # --accent (navy blue)
_ACCENT_DARK = (0x0A, 0x25, 0x40)  # --accent-dark
_SUCCESS = (0x1E, 0x7A, 0x4C)      # --success
_RULE = (0xD4, 0xD8, 0xE0)
_TINT = (0xEE, 0xF1, 0xF4)         # --bg
_HEADER_HEX = "0F3A5F"
_ACCENT_HEX = "0F3A5F"


def _rgb(*rgb: int):
    from docx.shared import RGBColor
    return RGBColor(*rgb)


def _set_run_font(run, *, size=11, bold=False, color=_INK, name="Calibri"):
    from docx.shared import Pt
    from docx.oxml.ns import qn

    run.bold = bold
    run.font.size = Pt(float(size))
    run.font.color.rgb = _rgb(*color)
    run.font.name = name
    try:
        rPr = run._element.get_or_add_rPr()
        rFonts = rPr.get_or_add_rFonts()
        rFonts.set(qn("w:ascii"), name)
        rFonts.set(qn("w:hAnsi"), name)
        rFonts.set(qn("w:eastAsia"), name)
    except Exception:
        pass


def _para(
    doc_or_cell,
    text: str = "",
    *,
    size=11,
    bold=False,
    color=_INK,
    align="left",
    space_before=0,
    space_after=6,
    name="Calibri",
    line_spacing=1.2,
):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    if hasattr(doc_or_cell, "add_paragraph"):
        p = doc_or_cell.add_paragraph()
    else:
        p = doc_or_cell.paragraphs[0] if doc_or_cell.paragraphs else doc_or_cell.add_paragraph()
        p.clear()
    p.paragraph_format.space_before = Pt(space_before)
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.line_spacing = line_spacing
    if align == "center":
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif align == "right":
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    elif align == "justify":
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    else:
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    if text:
        run = p.add_run(text)
        _set_run_font(run, size=size, bold=bold, color=color, name=name)
    return p


def _shade_cell(cell, hex_color: str):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), hex_color)
    shd.set(qn("w:val"), "clear")
    tcPr.append(shd)


def _set_cell_borders(cell, color="D4D8E0", sz="4"):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    for edge in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), sz)
        el.set(qn("w:color"), color)
        borders.append(el)
    tcPr.append(borders)


def _set_cell_text(cell, text: str, *, bold=False, size=10, color=_INK, align="left", name="Calibri"):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    cell.text = ""
    p = cell.paragraphs[0]
    if align == "right":
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    elif align == "center":
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(2)
    run = p.add_run(str(text))
    _set_run_font(run, size=size, bold=bold, color=color, name=name)


def _set_table_fixed_width(table, width_cm: float):
    """Lock table to content width so cells clip cleanly instead of spilling."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm

    table.autofit = False
    try:
        table.allow_autofit = False
    except Exception:
        pass
    tbl = table._tbl
    tblPr = tbl.tblPr
    if tblPr is None:
        tblPr = OxmlElement("w:tblPr")
        tbl.insert(0, tblPr)
    # Prefer fixed layout
    layout = tblPr.find(qn("w:tblLayout"))
    if layout is None:
        layout = OxmlElement("w:tblLayout")
        tblPr.append(layout)
    layout.set(qn("w:type"), "fixed")
    tblW = tblPr.find(qn("w:tblW"))
    if tblW is None:
        tblW = OxmlElement("w:tblW")
        tblPr.append(tblW)
    tw = int(Cm(width_cm).twips)
    tblW.set(qn("w:w"), str(tw))
    tblW.set(qn("w:type"), "dxa")
    # Equal column widths unless already set
    n = len(table.columns)
    if n:
        col_w = tw // n
        for cell in table.rows[0].cells:
            tc = cell._tc
            tcPr = tc.get_or_add_tcPr()
            tcW = tcPr.find(qn("w:tcW"))
            if tcW is None:
                tcW = OxmlElement("w:tcW")
                tcPr.append(tcW)
            tcW.set(qn("w:w"), str(col_w))
            tcW.set(qn("w:type"), "dxa")
    # Compact cell margins
    for row in table.rows:
        for cell in row.cells:
            tc = cell._tc
            tcPr = tc.get_or_add_tcPr()
            mar = tcPr.find(qn("w:tcMar"))
            if mar is not None:
                tcPr.remove(mar)
            mar = OxmlElement("w:tcMar")
            for edge, val in (("top", 40), ("left", 60), ("bottom", 40), ("right", 60)):
                el = OxmlElement(f"w:{edge}")
                el.set(qn("w:w"), str(val))
                el.set(qn("w:type"), "dxa")
                mar.append(el)
            tcPr.append(mar)


def _set_col_widths(table, widths_cm: list[float]):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm

    for row in table.rows:
        for cell, w in zip(row.cells, widths_cm):
            tc = cell._tc
            tcPr = tc.get_or_add_tcPr()
            tcW = tcPr.find(qn("w:tcW"))
            if tcW is None:
                tcW = OxmlElement("w:tcW")
                tcPr.append(tcW)
            tcW.set(qn("w:w"), str(int(Cm(w).twips)))
            tcW.set(qn("w:type"), "dxa")


def _style_table(table, header=True, content_width_cm: float = 17.0):
    from docx.shared import Pt

    _set_table_fixed_width(table, content_width_cm)
    for i, row in enumerate(table.rows):
        for cell in row.cells:
            _set_cell_borders(cell)
            if header and i == 0:
                _shade_cell(cell, _HEADER_HEX)
                for p in cell.paragraphs:
                    for run in p.runs:
                        run.font.color.rgb = _rgb(0xFF, 0xFF, 0xFF)
                        run.bold = True
                        run.font.size = Pt(9)
                        run.font.name = "Calibri"
            elif i % 2 == 1:
                _shade_cell(cell, "EEF1F4")


def _section_heading(doc, number: str, title: str, *, content_width_cm: float = 17.0):
    """Full-width navy bar heading (portal chrome), matching sample HTML section headers."""
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    # Spacer before heading
    sp = doc.add_paragraph()
    sp.paragraph_format.space_before = Pt(14)
    sp.paragraph_format.space_after = Pt(0)

    tbl = doc.add_table(rows=1, cols=1)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_fixed_width(tbl, content_width_cm)
    cell = tbl.rows[0].cells[0]
    _shade_cell(cell, _HEADER_HEX)
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    for edge in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:color"), _HEADER_HEX)
        borders.append(el)
    tcPr.append(borders)
    mar = OxmlElement("w:tcMar")
    for edge, val in (("top", "60"), ("left", "100"), ("bottom", "60"), ("right", "100")):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:w"), val)
        el.set(qn("w:type"), "dxa")
        mar.append(el)
    tcPr.append(mar)
    _set_cell_valign(cell, "center")

    cell.text = ""
    p = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(4)
    r1 = p.add_run(f"{number}  ")
    _set_run_font(r1, size=10, bold=True, color=(0xFF, 0xFF, 0xFF))
    r2 = p.add_run(title.upper())
    _set_run_font(r2, size=10, bold=True, color=(0xFF, 0xFF, 0xFF))

    after = doc.add_paragraph()
    after.paragraph_format.space_before = Pt(0)
    after.paragraph_format.space_after = Pt(8)
    return tbl


def _add_map_hyperlink(paragraph, url: str, text: str = "Open map") -> bool:
    """Insert a working external hyperlink into an existing paragraph."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    try:
        part = paragraph.part
        r_id = part.relate_to(
            url,
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
            is_external=True,
        )
        hyperlink = OxmlElement("w:hyperlink")
        hyperlink.set(qn("r:id"), r_id)
        new_run = OxmlElement("w:r")
        rPr = OxmlElement("w:rPr")
        color_el = OxmlElement("w:color")
        color_el.set(qn("w:val"), _ACCENT_HEX)
        u = OxmlElement("w:u")
        u.set(qn("w:val"), "single")
        sz = OxmlElement("w:sz")
        sz.set(qn("w:val"), "18")
        rFonts = OxmlElement("w:rFonts")
        rFonts.set(qn("w:ascii"), "Calibri")
        rFonts.set(qn("w:hAnsi"), "Calibri")
        rPr.append(rFonts)
        rPr.append(color_el)
        rPr.append(u)
        rPr.append(sz)
        text_el = OxmlElement("w:t")
        text_el.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        text_el.text = text
        new_run.append(rPr)
        new_run.append(text_el)
        hyperlink.append(new_run)
        for child in list(paragraph._p):
            if child.tag.endswith("}r") or child.tag.endswith("}hyperlink"):
                paragraph._p.remove(child)
        paragraph._p.append(hyperlink)
        return True
    except Exception:
        return False



def _hrule(doc, *, color=_HEADER_HEX, sz="18"):
    """Thin full-width accent rule via 1-cell shaded table."""
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.shared import Pt, Cm
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    rule = doc.add_table(rows=1, cols=1)
    rule.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_fixed_width(rule, 17.0)
    cell = rule.rows[0].cells[0]
    _shade_cell(cell, color)
    # Collapse cell padding / paragraph spacing so the bar stays thin
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    mar = OxmlElement("w:tcMar")
    for edge in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:w"), "0")
        el.set(qn("w:type"), "dxa")
        mar.append(el)
    tcPr.append(mar)
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(0)
    for run in list(p.runs):
        run.text = ""
    run = p.add_run(" ")
    run.font.size = Pt(1)
    # Exact row height ~2.5pt
    tr = rule.rows[0]._tr
    trPr = tr.get_or_add_trPr()
    trHeight = OxmlElement("w:trHeight")
    trHeight.set(qn("w:val"), "40")
    trHeight.set(qn("w:hRule"), "exact")
    trPr.append(trHeight)
    return rule


def _faded_logo_bytes(logo_path: Path, *, opacity: float = 0.08, max_px: int = 1100) -> bytes | None:
    """Create a low-opacity PNG of the logo for page watermarks."""
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        im = Image.open(logo_path).convert("RGBA")
        w, h = im.size
        scale = min(1.0, max_px / max(w, h))
        if scale < 1.0:
            im = im.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
        alpha = im.split()[3]
        alpha = alpha.point(lambda p: int(p * opacity))
        im.putalpha(alpha)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        buf.seek(0)
        return buf.read()
    except Exception:
        return None


def _inline_to_behind_anchor(run, *, width_emu: int, height_emu: int, page_w_emu: int, page_h_emu: int):
    """
    Convert an inline picture to a floating anchor centered on the page,
    drawn behind document text (true watermark).
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    drawing = run._r.find(qn("w:drawing"))
    if drawing is None:
        return
    inline = drawing.find(qn("wp:inline"))
    if inline is None:
        return

    # Preserve graphic / docPr / cNvGraphicFramePr from inline
    docPr = inline.find(qn("wp:docPr"))
    cNv = inline.find(qn("wp:cNvGraphicFramePr"))
    graphic = inline.find(qn("a:graphic"))

    anchor = OxmlElement("wp:anchor")
    anchor.set("distT", "0")
    anchor.set("distB", "0")
    anchor.set("distL", "0")
    anchor.set("distR", "0")
    anchor.set("simplePos", "0")
    anchor.set("relativeHeight", "0")
    anchor.set("behindDoc", "1")
    anchor.set("locked", "0")
    anchor.set("layoutInCell", "1")
    anchor.set("allowOverlap", "1")

    simplePos = OxmlElement("wp:simplePos")
    simplePos.set("x", "0")
    simplePos.set("y", "0")
    anchor.append(simplePos)

    # Center horizontally & vertically on the page
    pos_h = OxmlElement("wp:positionH")
    pos_h.set("relativeFrom", "page")
    align_h = OxmlElement("wp:align")
    align_h.text = "center"
    pos_h.append(align_h)
    anchor.append(pos_h)

    pos_v = OxmlElement("wp:positionV")
    pos_v.set("relativeFrom", "page")
    align_v = OxmlElement("wp:align")
    align_v.text = "center"
    pos_v.append(align_v)
    anchor.append(pos_v)

    ext = OxmlElement("wp:extent")
    ext.set("cx", str(width_emu))
    ext.set("cy", str(height_emu))
    anchor.append(ext)

    effect = OxmlElement("wp:effectExtent")
    for edge in ("l", "t", "r", "b"):
        effect.set(edge, "0")
    anchor.append(effect)

    wrap = OxmlElement("wp:wrapNone")
    anchor.append(wrap)

    if docPr is not None:
        anchor.append(docPr)
    if cNv is not None:
        anchor.append(cNv)
    if graphic is not None:
        anchor.append(graphic)

    drawing.remove(inline)
    drawing.append(anchor)


def _add_logo_watermark(section, logo_path: Path | None):
    """Faded logo floating behind text, centered on every page of this section."""
    if not logo_path:
        return
    faded = _faded_logo_bytes(logo_path, opacity=0.09)
    if not faded:
        return
    from docx.shared import Inches, Emu, Cm, Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    header = section.header
    header.is_linked_to_previous = False
    # Keep header height minimal — watermark is floating, not flow content
    try:
        section.header_distance = Cm(0.5)
    except Exception:
        pass

    # Clear existing header paragraphs
    for p in header.paragraphs:
        p.clear()
    p = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(0)

    width_in = 5.2
    run = p.add_run()
    inline_shape = run.add_picture(io.BytesIO(faded), width=Inches(width_in))
    try:
        width_emu = int(inline_shape.width)
        height_emu = int(inline_shape.height)
    except Exception:
        width_emu = int(Inches(width_in))
        height_emu = int(Inches(width_in))
    page_w = int(section.page_width)
    page_h = int(section.page_height)
    _inline_to_behind_anchor(
        run,
        width_emu=width_emu,
        height_emu=height_emu,
        page_w_emu=page_w,
        page_h_emu=page_h,
    )


def _add_footer(section, report_id: str, *, model_name: str = "", session_id: int | None = None):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, Cm
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    footer = section.footer
    footer.is_linked_to_previous = False
    try:
        section.footer_distance = Cm(1.0)
    except Exception:
        pass
    # Clear existing paragraphs
    for p in footer.paragraphs:
        p.clear()
    p = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pPr = p._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    top = OxmlElement("w:top")
    top.set(qn("w:val"), "single")
    top.set(qn("w:sz"), "6")
    top.set(qn("w:space"), "8")
    top.set(qn("w:color"), "D4D8E0")
    pBdr.append(top)
    pPr.append(pBdr)

    meta_bits = [f"SmartRoad AP  ·  {report_id}"]
    if session_id is not None:
        meta_bits.append(f"session {session_id}")
    if model_name:
        meta_bits.append(f"model {model_name}")
    meta_bits.append("Confidential")
    r = p.add_run("  ·  ".join(meta_bits) + "  ·  Page ")
    _set_run_font(r, size=8, color=_INK_SOFT, name="Calibri")

    def _add_field(paragraph, instr: str):
        run1 = paragraph.add_run()
        fld_begin = OxmlElement("w:fldChar")
        fld_begin.set(qn("w:fldCharType"), "begin")
        run1._r.append(fld_begin)
        run2 = paragraph.add_run()
        instr_el = OxmlElement("w:instrText")
        instr_el.set(qn("xml:space"), "preserve")
        instr_el.text = instr
        run2._r.append(instr_el)
        run3 = paragraph.add_run()
        fld_sep = OxmlElement("w:fldChar")
        fld_sep.set(qn("w:fldCharType"), "separate")
        run3._r.append(fld_sep)
        run4 = paragraph.add_run(" ")
        _set_run_font(run4, size=8, color=_INK_SOFT, name="Calibri")
        run5 = paragraph.add_run()
        fld_end = OxmlElement("w:fldChar")
        fld_end.set(qn("w:fldCharType"), "end")
        run5._r.append(fld_end)

    _add_field(p, " PAGE ")
    r2 = p.add_run(" of ")
    _set_run_font(r2, size=8, color=_INK_SOFT, name="Calibri")
    _add_field(p, " NUMPAGES ")


def _severity_color(sev: str) -> tuple[int, int, int]:
    s = (sev or "").strip().lower()
    if s in ("critical", "high"):
        return (0xB3, 0x26, 0x1E) if s == "critical" else (0xD9, 0x77, 0x06)
    if s == "medium":
        return (0xB8, 0xA1, 0x1F)
    return (0x7A, 0x86, 0x99)


def _set_row_exact_height(row, twips: int):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tr = row._tr
    trPr = tr.get_or_add_trPr()
    trHeight = OxmlElement("w:trHeight")
    trHeight.set(qn("w:val"), str(int(twips)))
    trHeight.set(qn("w:hRule"), "exact")
    trPr.append(trHeight)


def _set_cell_valign(cell, align: str = "center"):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    vAlign = OxmlElement("w:vAlign")
    vAlign.set(qn("w:val"), align)
    tcPr.append(vAlign)


def _kpi_card_row(doc, cards: list[tuple[str, str]], *, content_width_cm: float = 17.0):
    """Two-row KPI cards: (big value, small label) × N — matches portal summary cards."""
    from docx.enum.table import WD_TABLE_ALIGNMENT

    n = max(1, len(cards))
    tbl = doc.add_table(rows=2, cols=n)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    col_w = content_width_cm / n
    for i, (val, label) in enumerate(cards):
        top = tbl.rows[0].cells[i]
        bot = tbl.rows[1].cells[i]
        _shade_cell(top, "EEF1F4")
        _shade_cell(bot, "EEF1F4")
        _set_cell_borders(top, color="C5CDD6")
        _set_cell_borders(bot, color="C5CDD6")
        _set_cell_text(top, val, size=18, bold=True, color=_ACCENT, align="center")
        _set_cell_text(bot, label.upper(), size=7, color=_INK_SOFT, align="center")
        _set_cell_valign(top, "center")
        _set_cell_valign(bot, "center")
    _set_table_fixed_width(tbl, content_width_cm)
    _set_col_widths(tbl, [col_w] * n)
    _set_row_exact_height(tbl.rows[0], 450)
    _set_row_exact_height(tbl.rows[1], 280)
    return tbl


def build_docx(
    *,
    username: str,
    start_label: str,
    end_label: str,
    stats: dict,
    top5: list[dict],
    total_potholes: int,
    processed_at: str,
    display_name: str,
    is_legacy: bool,
    session_id: int | None = None,
    prior_survey: dict | None = None,
    report_id: str | None = None,
) -> bytes:
    from docx import Document
    from docx.shared import Inches, Pt, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    when = datetime.now(IST)
    report_id = (report_id or "").strip() or f"SR-AP-{when.strftime('%Y-%m%d-%H%M')}"
    logo = _logo_path()
    total_km = float(stats.get("total_km") or 0)
    corridor_km = float(stats.get("corridor_km") or stats.get("assigned_km") or 0)
    covered_km = float(stats.get("covered_km") or 0)
    proper_km = float(stats.get("proper_km") or 0)
    affected_km = float(stats.get("affected_km") or 0)
    clear_pct = float(stats.get("clear_pct") or (
        round((proper_km / total_km) * 100, 1) if total_km > 0 else 0.0
    ))
    pothole_pct = float(stats.get("pothole_pct") or (
        round((affected_km / total_km) * 100, 1) if total_km > 0 else 0.0
    ))
    density = (total_potholes / total_km) if total_km > 0.05 else float(total_potholes)
    content_w = 17.0  # A4 21cm − 2×2cm margins
    corridor_display = str(stats.get("corridor_display") or display_corridor_km(
        corridor_km, has_assignment=bool(stats.get("has_assignment")),
    ))
    covered_display = str(stats.get("covered_display") or display_gps_covered(covered_km, corridor_km))
    sev_counts = stats.get("severity_counts") or {"Low": 0, "Medium": 0, "High": 0, "Unknown": 0}
    priority = list(stats.get("priority_actions") or [])
    model_name = str(stats.get("model_version") or model_version_label())

    doc = Document()
    # Normal style defaults (academic-style body)
    try:
        style = doc.styles["Normal"]
        style.font.name = "Calibri"
        style.font.size = Pt(11)
        style.paragraph_format.line_spacing = 1.2
        style.paragraph_format.space_after = Pt(6)
    except Exception:
        pass

    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.left_margin = Cm(2.0)
    section.right_margin = Cm(2.0)
    section.top_margin = Cm(1.8)
    section.bottom_margin = Cm(1.8)
    _add_logo_watermark(section, logo)
    _add_footer(section, report_id, model_name=model_name, session_id=session_id)

    # ── COVER — vertically + horizontally centered in a full-page cell ─────
    cover = doc.add_table(rows=1, cols=1)
    cover.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_fixed_width(cover, content_w)
    # ~ usable height between margins (29.7 − 1.8 − 1.8 − footer slack)
    _set_row_exact_height(cover.rows[0], int(Cm(24.0).twips))
    cc = cover.rows[0].cells[0]
    _set_cell_valign(cc, "center")
    # No borders on cover frame
    tc = cc._tc
    tcPr = tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    for edge in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "nil")
        borders.append(el)
    tcPr.append(borders)

    # Clear default para; build cover content centered inside cell
    cc.paragraphs[0].clear()
    cc.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

    if logo:
        try:
            p = cc.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_after = Pt(20)
            run = p.add_run()
            run.add_picture(str(logo), width=Inches(1.9))
        except Exception:
            pass

    def _cover_line(text, *, size=11, bold=False, color=_INK, space_after=6, name="Calibri"):
        p = cc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(space_after)
        p.paragraph_format.line_spacing = 1.15
        if text:
            r = p.add_run(text)
            _set_run_font(r, size=size, bold=bold, color=color, name=name)
        return p

    _cover_line(
        "ROADS & BUILDINGS DEPARTMENT  ·  GOVERNMENT OF ANDHRA PRADESH",
        size=9,
        bold=True,
        color=_INK_SOFT,
        space_after=10,
    )
    _cover_line("SmartRoad AP", size=34, bold=True, color=_ACCENT, space_after=4)
    _cover_line(
        "Automated Road Condition Assessment System",
        size=12,
        color=_INK_SOFT,
        space_after=18,
    )

    # Accent bar — thin centered rule
    bar_wrap = cc.add_paragraph()
    bar_wrap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    bar_wrap.paragraph_format.space_before = Pt(4)
    bar_wrap.paragraph_format.space_after = Pt(18)
    br = bar_wrap.add_run("—" * 28)
    _set_run_font(br, size=9, bold=True, color=_ACCENT)

    _cover_line(
        "ROAD CONDITION ASSESSMENT REPORT",
        size=13,
        bold=True,
        color=_INK,
        space_after=10,
    )
    _cover_line(
        f"{start_label}  →  {end_label}",
        size=12,
        bold=True,
        color=_ACCENT,
        space_after=16,
    )
    # Survey date = when the video was processed — never a borrowed assignment day
    survey_date = str(processed_at)[:10] if processed_at else when.strftime("%Y-%m-%d")
    if not (len(survey_date) == 10 and survey_date[4] == "-"):
        survey_date = when.strftime("%Y-%m-%d")
    _cover_line(
        f"Survey date  {survey_date}\nReport generated  {when.strftime('%d %B %Y')}",
        size=10,
        color=_INK_SOFT,
        space_after=22,
    )
    _cover_line(report_id, size=11, bold=True, color=_ACCENT, space_after=4)
    _cover_line("Confidential — Client Deliverable", size=9, color=_INK_SOFT, space_after=0)

    doc.add_page_break()

    # ── BODY — masthead ────────────────────────────────────────────────────
    mast = doc.add_table(rows=1, cols=2)
    mast.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_fixed_width(mast, content_w)
    _set_col_widths(mast, [11.0, 6.0])
    left, right = mast.rows[0].cells
    for cell in (left, right):
        tc = cell._tc
        tcPr = tc.get_or_add_tcPr()
        borders = OxmlElement("w:tcBorders")
        for edge in ("top", "left", "bottom", "right"):
            el = OxmlElement(f"w:{edge}")
            el.set(qn("w:val"), "nil")
            borders.append(el)
        tcPr.append(borders)

    left.text = ""
    p = left.paragraphs[0]
    p.paragraph_format.space_after = Pt(2)
    r = p.add_run("ROADS & BUILDINGS  ·  GOVT. OF ANDHRA PRADESH")
    _set_run_font(r, size=8, color=_INK_SOFT, bold=True)
    p2 = left.add_paragraph()
    p2.paragraph_format.space_before = Pt(0)
    p2.paragraph_format.space_after = Pt(2)
    r2 = p2.add_run("SmartRoad AP")
    _set_run_font(r2, size=18, bold=True, color=_ACCENT)
    p3 = left.add_paragraph()
    p3.paragraph_format.space_before = Pt(0)
    p3.paragraph_format.space_after = Pt(0)
    r3 = p3.add_run("Automated Road Condition Assessment")
    _set_run_font(r3, size=9, color=_INK_SOFT)

    right.text = ""
    rp = right.paragraphs[0]
    rp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    rp.paragraph_format.space_after = Pt(2)
    rr = rp.add_run(report_id)
    _set_run_font(rr, size=11, bold=True, color=_ACCENT)
    rp2 = right.add_paragraph()
    rp2.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    rp2.paragraph_format.space_before = Pt(0)
    rp2.paragraph_format.space_after = Pt(0)
    rr2 = rp2.add_run(f"Generated {when.strftime('%d-%b-%Y · %H:%M IST')}\nConfidential")
    _set_run_font(rr2, size=8, color=_INK_SOFT)

    _para(doc, "", space_after=4)
    _hrule(doc, color=_HEADER_HEX)

    _para(
        doc,
        "ROAD CONDITION ASSESSMENT REPORT",
        size=12,
        bold=True,
        color=_INK,
        space_before=14,
        space_after=2,
    )
    _para(
        doc,
        f"{start_label}  →  {end_label}",
        size=11,
        color=_INK_SOFT,
        space_after=12,
    )

    # 1 Metadata — client-facing only (no field-crew identifiers)
    _section_heading(doc, "01", "Survey metadata", content_width_cm=content_w)
    meta = doc.add_table(rows=4, cols=4)
    meta.alignment = WD_TABLE_ALIGNMENT.CENTER
    meta_rows = [
        ("Route start", start_label, "Route end", end_label),
        ("Survey date", survey_date, "Assigned corridor", corridor_display),
        ("GPS covered", covered_display, "Analysed length", f"{total_km:.2f} km"),
        ("Potholes found", str(total_potholes), "Density", f"{density:.1f} / km"),
    ]
    for i, (a, b, c, d) in enumerate(meta_rows):
        cells = meta.rows[i].cells
        _set_cell_text(cells[0], a, size=9, color=_INK_SOFT)
        _set_cell_text(cells[1], b, size=9, bold=True, color=_INK)
        _set_cell_text(cells[2], c, size=9, color=_INK_SOFT)
        _set_cell_text(cells[3], d, size=9, bold=True, color=_INK)
        for cell in cells:
            _set_cell_borders(cell, color="D0D7DE", sz="4")
            if i % 2 == 0:
                _shade_cell(cell, "EEF1F4")
    _set_table_fixed_width(meta, content_w)
    _set_col_widths(meta, [3.6, 4.9, 3.6, 4.9])

    # 2 Coverage details — card layout (clear vs potholes)
    _section_heading(doc, "02", "Coverage details", content_width_cm=content_w)
    _kpi_card_row(
        doc,
        [
            (f"{proper_km:.1f} km", f"Clear / proper · {clear_pct:.1f}%"),
            (f"{affected_km:.1f} km", f"Has potholes · {pothole_pct:.1f}%"),
            (f"{total_km:.1f} km", "Analysed length"),
            (corridor_display if len(corridor_display) < 18 else corridor_display[:16] + "…", "Assigned corridor"),
        ],
        content_width_cm=content_w,
    )

    chart = _pie_chart_png(proper_km, affected_km)
    if chart:
        try:
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = Pt(8)
            p.paragraph_format.space_after = Pt(2)
            run = p.add_run()
            run.add_picture(io.BytesIO(chart), width=Inches(4.4))
            _para(
                doc,
                "Legend — Clear / proper (teal) vs affected stretch with detections (amber).",
                size=8, color=_INK_SOFT, align="center", space_before=2, space_after=6,
            )
        except Exception:
            pass

    # 3 Executive summary + severity
    _section_heading(doc, "03", "Executive summary", content_width_cm=content_w)
    _kpi_card_row(
        doc,
        [
            (f"{total_potholes}", "Potholes detected"),
            (f"{density:.1f}", "Potholes / km"),
            (f"{proper_km:.1f} km", "Proper / clear"),
            (f"{affected_km:.1f} km", "Affected stretch"),
        ],
        content_width_cm=content_w,
    )

    # Severity distribution cards (same DB severity labels as Top-5)
    _para(doc, "Severity distribution", size=9, bold=True, color=_INK, space_before=12, space_after=4)
    _kpi_card_row(
        doc,
        [
            (str(int(sev_counts.get("High") or 0)), "High"),
            (str(int(sev_counts.get("Medium") or 0)), "Medium"),
            (str(int(sev_counts.get("Low") or 0)), "Low"),
            (str(int(sev_counts.get("Unknown") or 0)), "Unclassified"),
        ],
        content_width_cm=content_w,
    )

    length_note = f"the analysed {total_km:.2f} km"
    length_source = str(stats.get("length_source") or "")
    if corridor_km > 0.2 and covered_km > 0.2:
        length_note = (
            f"the {covered_km:.2f} km GPS-covered of the {corridor_km:.2f} km assigned corridor"
        )
    elif corridor_km > 0.2 and covered_km <= 0.05:
        length_note = f"the analysed {total_km:.2f} km (corridor {corridor_km:.2f} km; GPS covered 0 km)"
    elif length_source in ("s3_gps_log", "bbox_extent", "none"):
        length_note = f"the analysed {total_km:.2f} km"

    sev_bits = []
    for label in ("High", "Medium", "Low"):
        n = int(sev_counts.get(label) or 0)
        if n:
            sev_bits.append(f"{n} {label}")
    sev_phrase = (", ".join(sev_bits) + ".") if sev_bits else "no severity labels on detections."

    _para(
        doc,
        f"This assessment recorded {total_potholes} pothole detection(s) along "
        f"{start_label} → {end_label}. Of {length_note}, "
        f"approximately {proper_km:.2f} km ({clear_pct:.1f}%) is estimated clear and "
        f"{affected_km:.2f} km ({pothole_pct:.1f}%) is associated with detections "
        f"(merged ~50 m buffers along the corridor). By severity: {sev_phrase}",
        size=10,
        color=_INK,
        align="justify",
        space_before=14,
        space_after=6,
        line_spacing=1.35,
    )

    # vs last survey (same corridor labels)
    if prior_survey:
        prev_n = int(prior_survey.get("total_potholes") or 0)
        delta = int(total_potholes) - prev_n
        delta_txt = f"+{delta}" if delta > 0 else str(delta)
        prev_when = str(prior_survey.get("processed_at") or "")[:10] or "prior survey"
        _para(
            doc,
            f"Vs last survey ({prev_when}): {prev_n} potholes previously → "
            f"{total_potholes} now ({delta_txt} detections).",
            size=9,
            color=_INK_SOFT,
            space_before=4,
            space_after=10,
        )
    else:
        _para(
            doc,
            "Vs last survey: no earlier report found for this videographer with the same start → end labels.",
            size=8,
            color=_INK_SOFT,
            space_before=2,
            space_after=10,
        )

    # 4 Roads
    _section_heading(doc, "04", "Roads covered", content_width_cm=content_w)
    roads = list(stats.get("per_road") or [])
    t = doc.add_table(rows=1, cols=5)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = t.rows[0].cells
    for cell, label, align in zip(
        hdr,
        ["Road / segment", "Length km", "Potholes", "Clear km", "Affected km"],
        ["left", "right", "right", "right", "right"],
    ):
        _set_cell_text(cell, label.upper(), bold=True, size=8, color=(0xFF, 0xFF, 0xFF), align=align)
    if not roads:
        roads = [{
            "name": f"{start_label} → {end_label}",
            "potholes": total_potholes,
            "road_km": total_km,
            "proper_km": proper_km,
            "affected_km": affected_km,
        }]
    for road in roads:
        row = t.add_row().cells
        _set_cell_text(row[0], str(road.get("name") or "Unnamed segment"), size=10)
        _set_cell_text(row[1], f"{float(road.get('road_km') or 0):.2f}", size=10, align="right")
        _set_cell_text(row[2], str(road.get("potholes") or 0), size=10, align="right")
        _set_cell_text(row[3], f"{float(road.get('proper_km') or 0):.2f}", size=10, align="right")
        _set_cell_text(row[4], f"{float(road.get('affected_km') or 0):.2f}", size=10, align="right")
    tot = t.add_row().cells
    _set_cell_text(tot[0], "TOTAL", size=10, bold=True)
    _set_cell_text(tot[1], f"{sum(float(r.get('road_km') or 0) for r in roads):.2f}", size=10, bold=True, align="right")
    _set_cell_text(tot[2], str(sum(int(r.get("potholes") or 0) for r in roads)), size=10, bold=True, align="right")
    _set_cell_text(tot[3], f"{proper_km:.2f}", size=10, bold=True, align="right")
    _set_cell_text(tot[4], f"{affected_km:.2f}", size=10, bold=True, align="right")
    _style_table(t, header=True, content_width_cm=content_w)
    _set_col_widths(t, [7.0, 2.5, 2.5, 2.5, 2.5])

    # 5 Priority actions (from same per_road numbers — no costing)
    _section_heading(doc, "05", "Priority segments", content_width_cm=content_w)
    _para(
        doc,
        "Segments ranked by potholes per km, then share of length affected. "
        "Use this list for field follow-up — not a cost estimate.",
        size=9, color=_INK_SOFT, space_before=2, space_after=8,
    )
    if not priority:
        priority = priority_action_rows(roads, limit=10)
    t_pri = doc.add_table(rows=1, cols=5)
    t_pri.alignment = WD_TABLE_ALIGNMENT.CENTER
    for cell, label, align in zip(
        t_pri.rows[0].cells,
        ["#", "Segment", "Potholes/km", "Affected %", "Potholes"],
        ["center", "left", "right", "right", "right"],
    ):
        _set_cell_text(cell, label.upper(), bold=True, size=8, color=(0xFF, 0xFF, 0xFF), align=align)
    for i, row in enumerate(priority, 1):
        cells = t_pri.add_row().cells
        _set_cell_text(cells[0], str(i), size=9, align="center")
        _set_cell_text(cells[1], str(row.get("name") or ""), size=9)
        _set_cell_text(cells[2], f"{float(row.get('density') or 0):.1f}", size=9, align="right")
        _set_cell_text(cells[3], f"{float(row.get('affected_pct') or 0):.0f}%", size=9, align="right")
        _set_cell_text(cells[4], str(int(row.get("potholes") or 0)), size=9, align="right")
    _style_table(t_pri, header=True, content_width_cm=content_w)
    _set_col_widths(t_pri, [1.2, 8.0, 2.8, 2.5, 2.5])

    # 6 Top 5
    _section_heading(doc, "06", "Top 5 largest detections", content_width_cm=content_w)
    _para(
        doc,
        "Ranked by bounding-box area (px²) as a temporary size proxy — "
        "not a ground-truth diameter. Severity uses the production score "
        "(area + vertical position on the frame).",
        size=8, color=_INK_SOFT, space_before=2, space_after=8,
    )
    if not top5:
        _para(doc, "No potholes with measurable size in this session.", size=10, color=_INK_SOFT)
    else:
        t2 = doc.add_table(rows=1, cols=5)
        t2.alignment = WD_TABLE_ALIGNMENT.CENTER
        for cell, label, align in zip(
            t2.rows[0].cells,
            ["#", "Severity", "Size proxy", "Coordinates", "Map"],
            ["center", "left", "right", "left", "center"],
        ):
            _set_cell_text(cell, label.upper(), bold=True, size=8, color=(0xFF, 0xFF, 0xFF), align=align)
        for i, poth in enumerate(top5, 1):
            cells = t2.add_row().cells
            lat, lon = poth.get("lat"), poth.get("lon")
            try:
                lat_f = float(lat) if lat is not None else None
                lon_f = float(lon) if lon is not None else None
            except (TypeError, ValueError):
                lat_f = lon_f = None
            coord = f"{lat_f:.5f}, {lon_f:.5f}" if lat_f is not None and lon_f is not None else "Not geolocated"
            link = str(poth.get("map_link") or "").strip()
            if not link and lat_f is not None and lon_f is not None:
                link = f"https://www.google.com/maps?q={lat_f:.6f},{lon_f:.6f}"
            sev = str(poth.get("severity") or "Unknown")
            _set_cell_text(cells[0], str(i), size=10, align="center")
            _set_cell_text(cells[1], sev.upper(), size=9, bold=True, color=_severity_color(sev))
            _set_cell_text(cells[2], f"{float(poth.get('bbox_area') or 0):.0f} px²", size=10, align="right")
            _set_cell_text(cells[3], coord, size=9)
            cells[4].text = ""
            para = cells[4].paragraphs[0]
            para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            if link:
                ok = _add_map_hyperlink(para, link, "Open map")
                if not ok:
                    _set_cell_text(cells[4], link[:48] + ("…" if len(link) > 48 else ""), size=8, color=_ACCENT, align="center")
            else:
                _set_cell_text(cells[4], "No map link", size=9, color=_INK_SOFT, align="center")
        _style_table(t2, header=True, content_width_cm=content_w)
        _set_col_widths(t2, [1.2, 2.6, 3.2, 6.5, 3.5])

    # 7 Severity legend
    _section_heading(doc, "07", "Severity legend", content_width_cm=content_w)
    leg = doc.add_table(rows=1, cols=2)
    leg.alignment = WD_TABLE_ALIGNMENT.CENTER
    for cell, label in zip(leg.rows[0].cells, ["Label", "Definition"]):
        _set_cell_text(cell, label.upper(), bold=True, size=8, color=(0xFF, 0xFF, 0xFF))
    for label, definition in severity_legend_rows():
        cells = leg.add_row().cells
        _set_cell_text(cells[0], label, size=9, bold=True, color=_severity_color(label))
        _set_cell_text(cells[1], definition, size=9, color=_INK)
    _style_table(leg, header=True, content_width_cm=content_w)
    _set_col_widths(leg, [3.5, 13.5])

    # 8 Methodology
    _section_heading(doc, "08", "Methodology & limitations", content_width_cm=content_w)
    method_rows = [
        ("Clear vs affected", f"Merged ~{int(POTHOLE_BUFFER_KM * 1000)} m buffers; overlaps not double-counted."),
        ("Analysed length", f"{total_km:.2f} km"),
        ("Assigned corridor", corridor_display),
        ("GPS covered", covered_display),
        ("Roads table", "Assignment segments when linked; otherwise one corridor row."),
        ("Out of scope", "Repair costing, vendor recommendations, and work-order status."),
        ("Model", f"{model_name} · session {session_id or '—'}"),
    ]
    mt = doc.add_table(rows=1, cols=2)
    mt.alignment = WD_TABLE_ALIGNMENT.CENTER
    for cell, label in zip(mt.rows[0].cells, ["Topic", "Detail"]):
        _set_cell_text(cell, label.upper(), bold=True, size=8, color=(0xFF, 0xFF, 0xFF))
    for topic, detail in method_rows:
        cells = mt.add_row().cells
        _set_cell_text(cells[0], topic, size=9, bold=True, color=_INK)
        _set_cell_text(cells[1], detail, size=9, color=_INK)
    _style_table(mt, header=True, content_width_cm=content_w)
    _set_col_widths(mt, [4.0, 13.0])

    _para(doc, "", space_before=18, space_after=0)
    _hrule(doc, color="C5CDD6", sz="6")
    _para(
        doc,
        "— End of report —",
        size=9,
        color=_INK_SOFT,
        align="center",
        space_before=10,
        space_after=0,
    )

    _ = (username, display_name, is_legacy)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def enrich_session_potholes_city_state(session_id: int, *, limit: int = 60) -> int:
    """Fill empty city/state via Nominatim for stored potholes. Returns updates."""
    import db_utils
    from routes import geocode_utils

    rows = db_utils.get_potholes_for_session(int(session_id))
    updated = 0
    conn = db_utils._get_conn()
    try:
        with conn.cursor() as cur:
            for r in rows:
                if updated >= limit:
                    break
                if (r.get("city") or "").strip() and (r.get("state") or "").strip():
                    continue
                lat, lon = r.get("lat"), r.get("lon")
                if lat is None or lon is None:
                    continue
                geo = geocode_utils.reverse_geocode(lat, lon)
                if not geo:
                    continue
                cur.execute(
                    """
                    UPDATE potholes
                    SET city = COALESCE(NULLIF(city, ''), %s),
                        state = COALESCE(NULLIF(state, ''), %s),
                        street_name = COALESCE(NULLIF(street_name, ''), %s),
                        country = COALESCE(NULLIF(country, ''), %s),
                        full_address = COALESCE(NULLIF(full_address, ''), %s)
                    WHERE id = %s
                    """,
                    (
                        geo.get("city") or "",
                        geo.get("state") or "",
                        geo.get("street_name") or "",
                        geo.get("country") or "",
                        geo.get("full_address") or "",
                        int(r["id"]),
                    ),
                )
                updated += 1
        conn.commit()
    finally:
        conn.close()
    return updated


def rename_processed_media(
    *,
    old_s3_key: str,
    old_filename: str,
    new_filename: str,
    username: str,
) -> dict:
    """
    Safely rename processed-bucket source object if present.
    Copy→verify→delete old. Returns {old_key, new_key, renamed: bool}.
    """
    import s3_utils
    from routes.field_upload_service import split_field_key, processed_source_key

    if not old_s3_key or old_filename == new_filename:
        return {"renamed": False, "old_key": old_s3_key, "new_key": old_s3_key}

    meta = split_field_key(old_s3_key)
    route = meta.get("route_folder") or "_unsorted"
    user = username if username and username != "Legacy" else (meta.get("username") or "Legacy")
    # Prefer processed sources/ layout
    old_proc = processed_source_key(old_s3_key)
    new_proc = f"sources/{user}/{route}/{new_filename}"
    bucket = s3_utils.get_processed_bucket()
    out = {"renamed": False, "old_key": old_proc, "new_key": new_proc, "bucket": bucket}

    if not s3_utils.is_s3_configured():
        return out
    try:
        if s3_utils.object_exists(bucket, old_proc):
            s3_utils.rename_object_safe(bucket, old_proc, new_proc, delete_source=True)
            out["renamed"] = True
            return out
        # Fallback: object still under input-style key in processed bucket
        if s3_utils.object_exists(bucket, old_s3_key):
            # Keep same folder, only change filename leaf
            parts = old_s3_key.replace("\\", "/").split("/")
            parts[-1] = new_filename
            new_key = "/".join(parts)
            s3_utils.rename_object_safe(bucket, old_s3_key, new_key, delete_source=True)
            out.update({"renamed": True, "old_key": old_s3_key, "new_key": new_key})
    except Exception as e:
        out["error"] = str(e)
    return out


def generate_report_for_session(session_id: int, *, force: bool = False) -> dict:
    """
    Build DOCX, upload to smartroad-reports/<username>/, update video_sessions.
    Also sets display_name and renames processed source object when needed.
    """
    import db_utils
    import s3_utils

    if not db_utils.is_db_configured():
        return {"ok": False, "error": "DB not configured"}

    session = db_utils.get_session_by_id(int(session_id))
    if not session:
        return {"ok": False, "error": f"Session {session_id} not found"}

    if session.get("report_s3_key") and not force:
        return {
            "ok": True,
            "skipped": True,
            "report_s3_key": session["report_s3_key"],
            "display_name": session.get("display_name") or session.get("filename"),
        }

    # Enrich city/state lightly (capped) — full backfill can do more offline
    try:
        enrich_session_potholes_city_state(int(session_id), limit=8)
    except Exception as e:
        print(f"[report] geocode enrich: {e}")

    potholes = db_utils.get_potholes_for_session(int(session_id))
    username, user_id, is_legacy = resolve_uploader(session.get("s3_key"))
    if session.get("username"):
        username = session["username"]
        is_legacy = bool(session.get("is_legacy"))
    if session.get("user_id"):
        user_id = int(session["user_id"])

    # Prefer labels already stored on the session row when present
    start_label, end_label, asg = resolve_start_end_labels(
        username=username,
        user_id=user_id,
        potholes=potholes,
        processed_at=session.get("processed_at"),
    )
    if (session.get("start_label") or "").strip() and (session.get("end_label") or "").strip():
        # Keep DB labels when assignment pins were empty / geocode-only
        if not asg or start_label in ("start",) or end_label in ("end",):
            start_label = str(session["start_label"]).strip()
            end_label = str(session["end_label"]).strip()
        elif asg and (
            start_label.lower().startswith("17.") or "→" not in f"{start_label}{end_label}"
        ):
            # Session labels from a prior good generate beat weak geocode crumbs
            sl = str(session["start_label"]).strip()
            el = str(session["end_label"]).strip()
            if len(sl) > 3 and len(el) > 2 and "unnamed" not in sl.lower():
                start_label, end_label = sl, el

    if not asg:
        # No corridor that day → mark legacy for Reports filter, but keep username
        # so the file still appears under that videographer (not a global Legacy dump).
        is_legacy = True
        if username in ("", "_unscoped"):
            username = "Legacy"

    work_date = (asg or {}).get("_work_date") if isinstance(asg, dict) else None
    covered_km, _cbc = lookup_tracking_covered_km(
        user_id,
        session.get("processed_at"),
        assignment=asg,
        work_date=work_date,
        potholes=potholes,
    )
    gps_log_km = 0.0
    corridor_km = _corridor_km_from_assignment(asg)
    # Same gate as compute_road_stats: only pull S3 GPS when survey covered + corridor are empty.
    if float(covered_km or 0) < 0.2 and float(corridor_km or 0) < 0.2:
        try:
            gps_log_km, gps_meta = lookup_s3_gps_track_km(session.get("s3_key"))
            if gps_log_km > 0:
                print(
                    f"[report] session={session_id} analysed from S3 GPS log "
                    f"{gps_meta.get('s3_key')} → {gps_log_km:.3f} km "
                    f"({gps_meta.get('points')} points)",
                    flush=True,
                )
        except Exception as e:
            print(f"[report] S3 GPS log length failed session={session_id}: {e}", flush=True)
            gps_log_km = 0.0
    stats = compute_road_stats(
        potholes, asg, covered_km=covered_km, gps_log_km=gps_log_km,
    )
    # Video day for the report header — not assignment carryover date
    video_day = str(session.get("processed_at") or "")[:10]
    if len(video_day) == 10 and video_day[4] == "-":
        stats["work_date"] = video_day
    elif work_date:
        stats["work_date"] = work_date
    stats = enrich_stats_for_report(
        stats, potholes, has_assignment=bool(asg), session_id=int(session_id),
    )
    top5 = top5_large_potholes(potholes)

    prior = lookup_prior_survey_summary(
        username=username,
        start_label=start_label,
        end_label=end_label,
        before_session_id=int(session_id),
        processed_at=str(session.get("processed_at") or ""),
    )

    when = datetime.now(IST)
    display_name, report_name = build_display_names(
        username=username,
        session_id=int(session_id),
        start_label=start_label,
        end_label=end_label,
        when=when,
    )
    # Keep original extension if image
    old_fn = session.get("filename") or "capture.mp4"
    ext = os.path.splitext(old_fn)[1] or ".mp4"
    display_name = os.path.splitext(display_name)[0] + ext

    # Always use actual stored detections as the count of truth (session column can lag)
    n_detected = len(potholes)
    try:
        n_session = int(session.get("total_potholes") or 0)
    except (TypeError, ValueError):
        n_session = 0
    total_potholes = max(n_detected, n_session)

    report_id = f"SR-AP-{when.strftime('%Y-%m%d-%H%M')}"
    docx_bytes = build_docx(
        username=username,
        start_label=start_label,
        end_label=end_label,
        stats=stats,
        top5=top5,
        total_potholes=total_potholes,
        processed_at=str(session.get("processed_at") or when.isoformat()),
        display_name=display_name,
        is_legacy=is_legacy,
        session_id=int(session_id),
        prior_survey=prior,
        report_id=report_id,
    )

    json_payload = build_report_json_payload(
        report_id=report_id,
        session_id=int(session_id),
        username=username,
        start_label=start_label,
        end_label=end_label,
        stats=stats,
        top5=top5,
        total_potholes=total_potholes,
        processed_at=str(session.get("processed_at") or when.isoformat()),
        prior=prior,
    )
    import json as _json
    json_bytes = _json.dumps(json_payload, indent=2, ensure_ascii=False).encode("utf-8")
    json_name = os.path.splitext(report_name)[0] + ".json"

    report_key = f"{_safe_slug(username)}/{report_name}"
    uploaded = False
    if s3_utils.is_s3_configured():
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            tmp.write(docx_bytes)
            tmp_path = tmp.name
        try:
            s3_utils.upload_report(tmp_path, report_key)
            uploaded = True
            # Sibling JSON next to the DOCX (best-effort)
            try:
                with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as jtmp:
                    jtmp.write(json_bytes)
                    jpath = jtmp.name
                try:
                    s3_utils.upload_report(jpath, f"{_safe_slug(username)}/{json_name}")
                finally:
                    try:
                        os.unlink(jpath)
                    except OSError:
                        pass
            except Exception as je:
                print(f"[report] JSON sibling upload skipped: {je}", flush=True)
        except Exception as up_err:
            print(f"[report] S3 upload failed, falling back to local: {up_err}")
            local_dir = ROOT / "data" / "reports" / _safe_slug(username)
            local_dir.mkdir(parents=True, exist_ok=True)
            local_path = local_dir / report_name
            local_path.write_bytes(docx_bytes)
            (local_dir / json_name).write_bytes(json_bytes)
            report_key = f"local:{local_path}"
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    else:
        # Local fallback under data/reports/
        local_dir = ROOT / "data" / "reports" / _safe_slug(username)
        local_dir.mkdir(parents=True, exist_ok=True)
        (local_dir / report_name).write_bytes(docx_bytes)
        (local_dir / json_name).write_bytes(json_bytes)
        report_key = f"local:{local_dir / report_name}"

    rename_info = rename_processed_media(
        old_s3_key=session.get("s3_key") or "",
        old_filename=old_fn,
        new_filename=display_name,
        username=username,
    )

    db_utils.update_session_report_meta(
        int(session_id),
        filename=display_name,
        display_name=display_name,
        username=username,
        user_id=user_id,
        start_label=start_label,
        end_label=end_label,
        report_s3_key=report_key if uploaded or str(report_key).startswith("local:") else None,
        is_legacy=is_legacy,
        # Keep original s3_key (uploader path) — processed rename is independent
        s3_key=None,
    )

    return {
        "ok": True,
        "session_id": int(session_id),
        "username": username,
        "is_legacy": is_legacy,
        "display_name": display_name,
        "report_s3_key": report_key,
        "report_json_name": json_name,
        "start_label": start_label,
        "end_label": end_label,
        "stats": stats,
        "top5_count": len(top5),
        "s3_renamed": rename_info.get("renamed", False),
        "uploaded": uploaded,
    }


def list_reports_for_ui() -> dict:
    """Group sessions that already have a DOCX report (skip bare detections)."""
    import db_utils

    rows = db_utils.get_all_sessions_for_reports()
    groups: dict[str, list] = {}
    listed = 0
    for r in rows:
        # Reports page is for generated DOCX only — detections without a report
        # belong on Detection, not here as "#id · (no report)".
        if not r.get("report_s3_key"):
            continue
        key = r.get("username") or ("Legacy" if r.get("is_legacy") else "Legacy")
        if r.get("is_legacy") and not r.get("username"):
            key = "Legacy"
        groups.setdefault(key, []).append({
            "id": r["id"],
            "filename": r.get("display_name") or r.get("filename"),
            "processed_at": r.get("processed_at"),
            "total_potholes": r.get("total_potholes"),
            "report_s3_key": r.get("report_s3_key"),
            "start_label": r.get("start_label"),
            "end_label": r.get("end_label"),
            "route_short": short_route_caption(
                r.get("start_label") or "", r.get("end_label") or "",
            ),
            "has_report": True,
            "is_legacy": bool(r.get("is_legacy")),
        })
        listed += 1
    # Stable order: Legacy last
    ordered = []
    for name in sorted(k for k in groups if k != "Legacy"):
        ordered.append({"username": name, "sessions": groups[name]})
    if "Legacy" in groups:
        ordered.append({"username": "Legacy", "sessions": groups["Legacy"]})
    return {"videographers": ordered, "total_sessions": listed}


def presign_report(report_s3_key: str) -> str | None:
    import s3_utils

    if not report_s3_key:
        return None
    if str(report_s3_key).startswith("local:"):
        return None
    if not s3_utils.is_s3_configured():
        return None
    try:
        return s3_utils.presign_url(report_s3_key, bucket=s3_utils.get_reports_bucket())
    except Exception:
        return None
