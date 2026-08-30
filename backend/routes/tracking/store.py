"""Live field tracking — GPS pings from Capture, admin Tracking map."""
from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]  # routes/tracking/store.py → repo root
STATE_PATH = ROOT / "data" / "gis" / "tracking_state.json"
IST = ZoneInfo("Asia/Kolkata")

LIVE_TTL_SEC = 45
MAX_TRAIL_POINTS = 2000

# Serialize read-modify-write of the tracking blob within a worker. Cross-worker
# races are reduced via an fcntl file lock on Linux (AceCloud). Never hold this
# lock across Postgres I/O — that starved upload threads under multiuser load.
_STATE_LOCK = threading.Lock()
_LOCK_PATH = STATE_PATH.with_suffix(".lock")

# Bound side-effect threads so a storm of pings cannot spawn unbounded workers.
_PING_SIDE_POOL = None
_PING_SIDE_POOL_GUARD = threading.Lock()


def _ping_side_pool():
    global _PING_SIDE_POOL
    if _PING_SIDE_POOL is None:
        with _PING_SIDE_POOL_GUARD:
            if _PING_SIDE_POOL is None:
                from concurrent.futures import ThreadPoolExecutor
                _PING_SIDE_POOL = ThreadPoolExecutor(
                    max_workers=4, thread_name_prefix="ping-side"
                )
    return _PING_SIDE_POOL


def today_ist() -> str:
    return datetime.now(IST).date().isoformat()


def _now_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in km — lives in store so early helpers can call it."""
    import math
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@contextmanager
def _cross_process_lock():
    """Best-effort file lock so gunicorn workers don't clobber each other's JSON."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = None
    try:
        fh = open(_LOCK_PATH, "a+", encoding="utf-8")
        try:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass
        yield
    finally:
        if fh is not None:
            try:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                fh.close()
            except Exception:
                pass


def _tracking_json_mirror_enabled() -> bool:
    return os.getenv("TRACKING_JSON_MIRROR", "0").lower() in ("1", "true", "yes")


def _read_json() -> dict:
    if not STATE_PATH.is_file():
        return {"videographers": {}}
    try:
        text = STATE_PATH.read_text(encoding="utf-8")
        if not (text or "").strip():
            return {"videographers": {}}
        return json.loads(text)
    except Exception:
        return {"videographers": {}}


def _write_json(state: dict) -> None:
    if not _tracking_json_mirror_enabled():
        return
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Compact JSON — indent=2 made every ping rewrite grow CPU+disk under load.
    tmp = STATE_PATH.with_name(STATE_PATH.name + ".tmp")
    tmp.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def _entry_for_mutation(user_id: int, date: str) -> dict:
    """Load one user/day for ping/commit/discard — Postgres first.

    Mutations used to read the JSON mirror with ``prefer_db=False``. When
    ``TRACKING_JSON_MIRROR=0`` (default) that mirror is empty, so finalize
    ``commit_capture_session`` rewrote the DB trail to [] and wiped cyan GPS.
    """
    import db_utils

    key = str(int(user_id))
    entry: dict = {}
    try:
        if db_utils.is_db_configured():
            day = db_utils.tracking_db_load_for_date(date) or {}
            entry = (day.get("videographers") or {}).get(key) or {}
    except Exception:
        entry = {}
    if not entry and _tracking_json_mirror_enabled():
        entry = (_read_json().get("videographers") or {}).get(key) or {}
    base = dict(entry) if isinstance(entry, dict) else {}
    return _roll_day(base, date)


def _assignment_is_auto_track(user_id: int, date: str) -> bool:
    try:
        return str(_assignment_entry(int(user_id), date).get("mode") or "").lower() == "auto_track"
    except Exception:
        return False


def _live_road_class_or_custom(
    user_id: int,
    date: str,
    lat: float,
    lon: float,
    *,
    include_completed: bool = False,
) -> str | None:
    """Snap to assigned roads; custom (auto_track) routes count as ``other`` anywhere."""
    rc = _nearest_road_class(
        int(user_id), date, lat, lon, include_completed=include_completed,
    )
    if rc:
        return rc
    if _assignment_is_auto_track(int(user_id), date):
        return "other"
    return None


def _load(*, prefer_db: bool | None = None) -> dict:
    """Postgres is source of truth. JSON only if TRACKING_JSON_MIRROR=1 or DB down."""
    if prefer_db is None:
        prefer_db = True
    try:
        import db_utils
        if prefer_db and db_utils.is_db_configured():
            db_state = db_utils.tracking_db_load_state()
            if db_state is not None:
                db_state.setdefault("videographers", {})
                return db_state
    except Exception:
        pass
    json_state = _read_json()
    json_state.setdefault("videographers", {})
    return json_state


def _save(
    state: dict,
    *,
    user_entry: dict | None = None,
    append_points: list | None = None,
    replace_trail: bool = False,
) -> None:
    """Persist to Postgres. JSON mirror only when TRACKING_JSON_MIRROR=1.

    Ping path: ``append_points`` → INSERT-only trail writes.
    Discard/commit: ``replace_trail=True`` → rewrite that user's trail.
    """
    _write_json(state)
    try:
        import db_utils
        if not db_utils.is_db_configured():
            return
        if user_entry is not None:
            if replace_trail:
                db_utils.tracking_db_save_user_entry(user_entry)
            else:
                db_utils.tracking_db_append_ping(user_entry, append_points or [])
        else:
            db_utils.tracking_db_save_state(state)
    except Exception:
        pass


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _roll_day(entry: dict, date: str) -> dict:
    if entry.get("date") == date:
        entry.setdefault("covered_by_class", {"nh": 0.0, "sh": 0.0, "mdr": 0.0, "other": 0.0})
        return entry
    return {
        "user_id": entry.get("user_id"),
        "username": entry.get("username"),
        "full_name": entry.get("full_name"),
        "state_id": entry.get("state_id"),
        "district_id": entry.get("district_id"),
        "date": date,
        "trail": [],
        "recording": False,
        "last": None,
        "covered_km": 0.0,
        "covered_by_class": {"nh": 0.0, "sh": 0.0, "mdr": 0.0, "other": 0.0},
    }


def _empty_by_class() -> dict:
    return {"nh": 0.0, "sh": 0.0, "mdr": 0.0, "other": 0.0}


# Cache: (user_id, date) -> list[(lat, lon, road_class)]
_ASSIGN_CLASS_CACHE: dict[tuple, list[tuple]] = {}
SNAP_TO_ROAD_KM = 0.40
# Dense samples along the driven corridor so GPS mid-block still counts (midpoints alone under-count).
ASSIGN_SAMPLE_STEP_KM = 0.08
COVERED_SAMPLE_STEP_KM = 0.05
# Legacy indigo-projection snap (kept for optional tools); map grey follows GPS trail instead.
COVERED_SNAP_KM = 0.12
COVERED_GAP_FILL_KM = 0.12
COVERED_NEIGHBOR_SAMPLES = 0
TRAIL_JUMP_KM = 1.25
# Display-only: project cyan/grey onto nearby GIS roads (does not affect covered_km).
DISPLAY_SNAP_KM = 0.40
DISPLAY_ROAD_PAD_KM = 0.55
DISPLAY_MAX_PARTS = 600
DISPLAY_MIN_VERTEX_M = 8.0  # dedupe hugged vertices closer than this
# Pre-sum GPS hygiene (stationary drift + impossible vehicle jumps).
TRAIL_MIN_STEP_KM = 0.005  # 5 m — ignore idle wobble / micro-jitter
# Cars are fine here: India NH/expressway limits are ~100–120 km/h; 160 leaves
# GPS overshoot headroom without accepting teleport spikes.
TRAIL_MAX_SPEED_KMH = 160.0
TRAIL_SIMPLIFY_TOL_KM = 0.015  # 15 m Douglas–Peucker before summing
CORRIDOR_COVER_SLACK_KM = 0.35  # home-wander ceiling above assigned route_km
# If cleaned GPS is inflated vs road-snap, prefer snap when within this band.
TRAIL_SNAP_INFLATE_RATIO = 0.75  # use snap if snap >= 75% of driven and snap < driven


def _cap_covered_km_to_corridor(
    covered_km: float,
    corridor_km: float,
    *,
    slack_km: float = CORRIDOR_COVER_SLACK_KM,
) -> float:
    """Shear home-wander: covered may not exceed assigned route + slack."""
    try:
        k = float(covered_km or 0)
        c = float(corridor_km or 0)
        s = float(slack_km)
    except (TypeError, ValueError):
        return float(covered_km or 0)
    if c > 0.5:
        return min(k, c + s)
    return k


def _parse_polyline_latlon(poly) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    if not isinstance(poly, list):
        return out
    for p in poly:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        try:
            lat, lon = float(p[0]), float(p[1])
        except (TypeError, ValueError):
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            out.append((lat, lon))
    return out


def _trail_latlon(trail) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for p in trail or []:
        if not isinstance(p, dict):
            continue
        try:
            lat = float(p.get("lat"))
            lon = float(p.get("lon") if p.get("lon") is not None else p.get("lng"))
        except (TypeError, ValueError):
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            out.append((lat, lon))
    return out


def _assignment_entry(user_id: int, date: str) -> dict:
    from routes import survey_service
    state = survey_service._load_state()
    day = (state.get("daily_assignments", {}).get(date, {}) or {})
    entry = day.get(survey_service._user_day_key(user_id)) or {}
    return entry if isinstance(entry, dict) else {}


def _assignment_class_points(
    user_id: int,
    date: str,
    *,
    include_completed: bool = False,
) -> list[tuple]:
    """Samples for snapping GPS → road_class.

    include_completed=False (quota): skip sealed roads so new distance only
    counts on incomplete assigned / alternate roads.
    include_completed=True (coverage report): keep sealed corridor classes so
    NH/SH/MDR attribution still works after the route is marked complete.
    """
    key = (int(user_id), date, "v4", bool(include_completed))
    if key in _ASSIGN_CLASS_CACHE:
        return _ASSIGN_CLASS_CACHE[key]
    points: list[tuple] = []
    try:
        from routes import survey_service
        status_map = survey_service._load_state().get("segment_status", {})
        seg_mids: list[tuple] = []
        seen_sid: set[str] = set()
        for row in survey_service.assignments_for_user(int(user_id), date):
            sid = str(row.get("id") or row.get("segment_id") or "")
            if not sid:
                continue
            if not include_completed and survey_service.is_segment_completed(sid, status_map):
                continue
            rc = row.get("road_class") or "other"
            if rc not in ("nh", "sh", "mdr", "other"):
                rc = "other"
            mid = None
            sk = row.get("state_key")
            did = str(row.get("district_id") or "")
            if sk and did:
                m = survey_service._segment_meta(sk, did).get(sid) or {}
                mid = m.get("mid")
                if not rc or rc == "other":
                    rc = m.get("road_class") or rc
                    if rc not in ("nh", "sh", "mdr", "other"):
                        rc = "other"
            if mid:
                seg_mids.append((float(mid[0]), float(mid[1]), rc))
                seen_sid.add(sid)

        def _class_near(lat: float, lon: float) -> str:
            best_rc = "other"
            best_d = SNAP_TO_ROAD_KM
            for plat, plon, rc in seg_mids:
                d = _haversine_km(lat, lon, plat, plon)
                if d <= best_d:
                    best_d = d
                    best_rc = rc
            return best_rc

        entry = _assignment_entry(int(user_id), date)
        latlon = _parse_polyline_latlon(entry.get("polyline"))
        if len(latlon) < 2:
            start, end = entry.get("start") or {}, entry.get("end") or {}
            try:
                latlon = [
                    (float(start["lat"]), float(start["lon"])),
                    (float(end["lat"]), float(end["lon"])),
                ]
            except (TypeError, ValueError, KeyError):
                latlon = []

        if len(latlon) >= 2:
            for lat, lon in survey_service._sample_polyline(latlon, step_km=ASSIGN_SAMPLE_STEP_KM):
                points.append((float(lat), float(lon), _class_near(lat, lon)))
        else:
            points = list(seg_mids)

        # Alternate-path roads in assignment districts
        for state_key, district_id in survey_service._assignment_scopes(entry):
            meta_map = survey_service._segment_meta(state_key, str(district_id))
            for sid, m in meta_map.items():
                if sid in seen_sid:
                    continue
                if not include_completed and survey_service.is_segment_completed(sid, status_map):
                    continue
                mid = m.get("mid")
                if not mid:
                    continue
                rc = m.get("road_class") or "other"
                if rc not in ("nh", "sh", "mdr", "other"):
                    rc = "other"
                points.append((float(mid[0]), float(mid[1]), rc))
                seen_sid.add(sid)
                if len(points) >= 4000:
                    break
            if len(points) >= 4000:
                break
    except Exception:
        points = []
    _ASSIGN_CLASS_CACHE[key] = points
    return points


def _nearest_road_class(
    user_id: int,
    date: str,
    lat: float,
    lon: float,
    *,
    include_completed: bool = False,
) -> str | None:
    pts = _assignment_class_points(user_id, date, include_completed=include_completed)
    if not pts:
        return None
    best_d = SNAP_TO_ROAD_KM
    best_rc = None
    for plat, plon, rc in pts:
        d = _haversine_km(lat, lon, plat, plon)
        if d <= best_d:
            best_d = d
            best_rc = rc
    return best_rc


def _quota_delta_allowed(user_id: int, date: str, lat: float, lon: float) -> bool:
    """Only count GPS distance toward quota when near an incomplete assigned road."""
    return _nearest_road_class(user_id, date, lat, lon, include_completed=False) is not None

