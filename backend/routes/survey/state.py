"""Survey GIS + daily assignment state — district-based (Andhra / Telangana).

Reads road indexes from data/gis_states/{andhra|telangana}/.
Assignments are per videographer user_id (multiple VGs can share a district).
"""
from __future__ import annotations

import json
import math
import os
import pickle
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import smartroad_path

ROOT = smartroad_path.ROOT
STATES_DIR = ROOT / "data" / "gis_states"
GIS = ROOT / "data" / "gis"  # runtime survey_state.json only
STATE_PATH = GIS / "survey_state.json"

# Assignment/status writes here are read-whole-state -> mutate -> write-whole-state.
# The Flask dev server now runs threaded=True (so one slow request, e.g. a large
# video upload, doesn't block every other user) — this lock keeps concurrent
# assign/status writes from two different videographers from racing and silently
# dropping each other's update ("lost update"), and stops interleaved writers
# from ever corrupting the on-disk JSON mirror.
STATE_LOCK = threading.RLock()

ROAD_CLASS_ORDER = {"nh": 0, "sh": 1, "mdr": 2, "other": 3}
VALID_FOCUS_CLASSES = frozenset({"nh", "sh", "mdr", "other"})
IST = ZoneInfo("Asia/Kolkata")

# In-memory segment GeoJSON cache: (state_key, district_id) -> (mtime, features)
_SEGMENT_FEATURE_CACHE: dict[tuple[str, str], tuple[float, list]] = {}
_SEGMENT_META_CACHE: dict[tuple[str, str], tuple[float, dict[str, dict]]] = {}
_NH_CACHE: dict[str, tuple[float, dict]] = {}
# Slim road-snap index for locate / reverse (mids + optional name/ref), not full meta.
_SNAP_INDEX_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
_SNAP_INDEX_LOCK = threading.Lock()
_SNAP_CELL_DEG = 0.02  # ~2.2 km cells — locate scans a bbox, not every segment
_SNAP_PREWARM_STARTED: set[str] = set()


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in km — defined in state so geocode/snap can use it."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# Geometry helpers used by snap/meta indexes in this module. Defined here (not only
# in geometry.py) because geocode loads state before geometry — otherwise
# NameError: _geom_midpoint during /api/survey/geocode.
_NODE_PREC = 5


def _geom_midpoint(geom: dict) -> tuple[float, float] | None:
    if not geom:
        return None
    t = geom.get("type")
    coords = geom.get("coordinates")
    if not coords:
        return None
    if t == "LineString":
        pts = coords
    elif t == "MultiLineString":
        pts = [p for line in coords for p in line]
    else:
        return None
    if not pts:
        return None
    mid = pts[len(pts) // 2]
    return float(mid[1]), float(mid[0])  # lat, lon


def _node_key(lon: float, lat: float) -> tuple[float, float]:
    return (round(float(lon), _NODE_PREC), round(float(lat), _NODE_PREC))


def _line_coord_parts(geom: dict | None) -> list[list]:
    """Return LineString coordinate lists (MultiLineString → one list per part)."""
    if not geom:
        return []
    t = geom.get("type")
    coords = geom.get("coordinates")
    if not coords:
        return []
    if t == "LineString":
        return [coords] if len(coords) >= 2 else []
    if t == "MultiLineString":
        return [line for line in coords if line and len(line) >= 2]
    return []


def clear_geojson_caches() -> dict:
    """Drop stale in-process GIS caches (safe anytime; next request rebuilds)."""
    n_seg = len(_SEGMENT_FEATURE_CACHE)
    n_meta = len(_SEGMENT_META_CACHE)
    n_nh = len(_NH_CACHE)
    n_snap = len(_SNAP_INDEX_CACHE)
    _SEGMENT_FEATURE_CACHE.clear()
    _SEGMENT_META_CACHE.clear()
    _NH_CACHE.clear()
    _SNAP_INDEX_CACHE.clear()
    return {"segments": n_seg, "meta": n_meta, "nh": n_nh, "snap": n_snap}


def parse_focus_classes(focus) -> set[str] | None:
    """Return allowed road classes, or None for all.
    Accepts 'all', 'nh', 'nh,sh', or ['nh','sh'].
    """
    if focus is None:
        return None
    if isinstance(focus, (list, tuple, set)):
        parts = [str(x).strip().lower() for x in focus]
    else:
        raw = str(focus).strip().lower()
        if not raw or raw == "all":
            return None
        parts = [p.strip() for p in raw.replace("|", ",").split(",") if p.strip()]
    classes = {p for p in parts if p in VALID_FOCUS_CLASSES}
    if not classes or classes == VALID_FOCUS_CLASSES:
        return None
    return classes


def focus_to_storage(focus) -> str:
    classes = parse_focus_classes(focus)
    if classes is None:
        return "all"
    return ",".join(sorted(classes, key=lambda c: ROAD_CLASS_ORDER.get(c, 9)))

# state_id in users table (andhra=1, telangana=2)
STATE_BY_ID = {
    1: {"key": "andhra", "label": "Andhra Pradesh"},
    2: {"key": "telangana", "label": "Telangana"},
}
STATE_ID_BY_KEY = {v["key"]: k for k, v in STATE_BY_ID.items()}

STATE_BOUNDS = {
    "andhra": [[12.62, 76.75], [19.92, 84.86]],
    "telangana": [[15.75, 77.20], [19.95, 81.85]],
}
STATE_CENTER = {
    "andhra": [15.9, 80.75],
    "telangana": [17.9, 79.5],
}
INDIA_BOUNDS = [[8.0, 68.5], [35.5, 96.5]]
INDIA_CENTER = [22.5, 79.0]

NOMINATIM_UA = "SmartRoadAP/1.0 (road-survey; contact=local-dev)"
# Photon (komoot) is the fast OSM forward-geocoder; Nominatim remains fallback / reverse.
PHOTON_URL = (os.getenv("PHOTON_URL") or "https://photon.komoot.io/api/").rstrip("/") + "/"
GEOCODE_PROVIDER = (os.getenv("GEOCODE_PROVIDER") or "photon").strip().lower()  # photon|nominatim


def today_ist() -> str:
    return datetime.now(IST).date().isoformat()


def _empty_survey_state() -> dict:
    return {
        "settings": {"daily_km": 100, "focus_road_class": "all"},
        "segment_status": {},
        "daily_assignments": {},
    }


def _json_mirror_enabled() -> bool:
    """JSON survey_state.json is off by default — Postgres is source of truth."""
    return os.getenv("SURVEY_JSON_MIRROR", "0").lower() in ("1", "true", "yes")


_STATE_MEM: dict = {"ts": 0.0, "state": None}
_STATE_MEM_TTL = float(os.getenv("SURVEY_STATE_CACHE_S", "15"))


def _cache_survey_state(state: dict) -> dict:
    _STATE_MEM["ts"] = time.time()
    _STATE_MEM["state"] = state
    return state


def _invalidate_survey_state_cache() -> None:
    _STATE_MEM["ts"] = 0.0
    _STATE_MEM["state"] = None


def _quarantine_corrupt_state(exc: Exception) -> None:
    """Move a torn/partial survey_state.json aside so the next read can use DB."""
    try:
        if STATE_PATH.is_file():
            stamp = datetime.now(IST).strftime("%Y%m%d-%H%M%S")
            dest = GIS / f"survey_state.corrupt.{stamp}.json"
            STATE_PATH.replace(dest)
            print(f"[survey] quarantined corrupt {STATE_PATH.name} → {dest.name}: {exc}", flush=True)
    except Exception as move_err:
        print(f"[survey] failed to quarantine corrupt state: {move_err}", flush=True)


def _read_json_state() -> dict:
    """Optional JSON mirror only (SURVEY_JSON_MIRROR=1). Never raise on corrupt."""
    with STATE_LOCK:
        if not STATE_PATH.is_file():
            return _empty_survey_state()
        try:
            text = STATE_PATH.read_text(encoding="utf-8")
            if not (text or "").strip():
                return _empty_survey_state()
            raw = json.loads(text)
        except (json.JSONDecodeError, OSError, UnicodeError) as e:
            _quarantine_corrupt_state(e)
            return _empty_survey_state()
        if not isinstance(raw, dict):
            _quarantine_corrupt_state(ValueError("survey_state root is not an object"))
            return _empty_survey_state()
        raw.setdefault("settings", {"daily_km": 100, "focus_road_class": "all"})
        raw.setdefault("segment_status", {})
        raw.setdefault("daily_assignments", {})
        return raw


def _load_state() -> dict:
    """Postgres is source of truth. JSON mirror is opt-in only.

    After JSON wipe/corruption, empty mirrors were preferred over DB and showed
    0.4 km / incomplete while assignments in Postgres still had 6.68 + completed.
    """
    now = time.time()
    cached = _STATE_MEM.get("state")
    if cached is not None and (now - float(_STATE_MEM.get("ts") or 0)) < _STATE_MEM_TTL:
        return cached

    try:
        import db_utils
        if db_utils.is_db_configured():
            db_state = db_utils.survey_db_load_state()
            if db_state is not None:
                db_state.setdefault("settings", {"daily_km": 100, "focus_road_class": "all"})
                db_state.setdefault("segment_status", {})
                db_state.setdefault("daily_assignments", {})
                return _cache_survey_state(db_state)
    except Exception as e:
        print(f"[survey] DB load failed, falling back: {e}", flush=True)

    if _json_mirror_enabled():
        return _cache_survey_state(_read_json_state())
    return _cache_survey_state(_empty_survey_state())


def _save_state(state: dict, *, persist_db: bool = True) -> None:
    """Persist to Postgres (default). JSON only if SURVEY_JSON_MIRROR=1.

    High-frequency GPS paths must call with ``persist_db=False`` and use
    ``survey_db_update_covered`` / segment upserts — full ``survey_db_save_state``
    deletes+rewrites tables and deadlocks under load.
    """
    _cache_survey_state(state)
    with STATE_LOCK:
        if _json_mirror_enabled():
            GIS.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(state, separators=(",", ":"), ensure_ascii=False)
            tmp = STATE_PATH.with_name(STATE_PATH.name + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, STATE_PATH)
        if not persist_db:
            return
        try:
            import db_utils
            if db_utils.is_db_configured():
                db_utils.survey_db_save_state(state)
        except Exception:
            pass


_DB_SYNC_TIMER = None
_DB_SYNC_LOCK = threading.Lock()
_PENDING_DB_STATE = None


def _schedule_survey_db_sync(state: dict, delay_s: float = 2.0) -> None:
    """Coalesce full DB rewrites so seal/assign doesn't block the next dashboard load."""
    global _DB_SYNC_TIMER, _PENDING_DB_STATE
    import copy
    with _DB_SYNC_LOCK:
        _PENDING_DB_STATE = copy.deepcopy(state)

        def _flush():
            global _DB_SYNC_TIMER, _PENDING_DB_STATE
            with _DB_SYNC_LOCK:
                payload = _PENDING_DB_STATE
                _PENDING_DB_STATE = None
                _DB_SYNC_TIMER = None
            if payload is None:
                return
            try:
                import db_utils
                if db_utils.is_db_configured():
                    db_utils.survey_db_save_state(payload)
                    _cache_survey_state(payload)
            except Exception:
                pass

        if _DB_SYNC_TIMER is not None:
            try:
                _DB_SYNC_TIMER.cancel()
            except Exception:
                pass
        t = threading.Timer(delay_s, _flush)
        t.daemon = True
        _DB_SYNC_TIMER = t
        t.start()


def get_settings() -> dict:
    raw = dict(_load_state().get("settings", {"daily_km": 100}))
    raw.setdefault("daily_km", 100)
    raw.setdefault("focus_road_class", "all")
    # Normalize legacy single-class + expose list for UI
    stored = focus_to_storage(raw.get("focus_road_class"))
    raw["focus_road_class"] = stored
    parsed = parse_focus_classes(stored)
    raw["focus_road_classes"] = sorted(parsed, key=lambda c: ROAD_CLASS_ORDER.get(c, 9)) if parsed else ["nh", "sh", "mdr", "other"]
    raw["focus_is_all"] = parsed is None
    return raw


def update_settings(**kwargs) -> dict:
    state = _load_state()
    settings = state.setdefault("settings", {"daily_km": 100})
    if "focus_road_class" in kwargs:
        kwargs["focus_road_class"] = focus_to_storage(kwargs["focus_road_class"])
    if "focus_road_classes" in kwargs and "focus_road_class" not in kwargs:
        kwargs["focus_road_class"] = focus_to_storage(kwargs.pop("focus_road_classes"))
    elif "focus_road_classes" in kwargs:
        kwargs.pop("focus_road_classes", None)
    settings.update(kwargs)
    _save_state(state)
    return get_settings()


def _state_dir(state_key: str) -> Path:
    key = resolve_state_key(state_key=state_key)
    if not key:
        raise ValueError(f"Unknown state_key: {state_key!r}")
    base = STATES_DIR.resolve()
    path = (STATES_DIR / key).resolve()
    if path != base and base not in path.parents:
        raise ValueError(f"Invalid state path: {state_key!r}")
    return path


def _safe_district_id(district_id: str | int) -> str:
    """LGD district ids are numeric — reject path traversal / odd filenames."""
    did = str(district_id).strip()
    if not did.isdigit():
        raise ValueError(f"Invalid district_id: {district_id!r}")
    return did


def resolve_state_key(state_id: int | None = None, state_key: str | None = None) -> str | None:
    if state_key:
        key = str(state_key).strip().lower()
        if key in STATE_ID_BY_KEY:
            return key
    if state_id is not None:
        info = STATE_BY_ID.get(int(state_id))
        if info:
            return info["key"]
    return None


def list_states() -> list[dict]:
    return [
        {
            "id": sid,
            "state_id": sid,
            "key": info["key"],
            "name": info["label"],
            "bounds": STATE_BOUNDS[info["key"]],
            "center": STATE_CENTER[info["key"]],
            "has_roads": (_state_dir(info["key"]) / "roads_meta.json").is_file(),
        }
        for sid, info in STATE_BY_ID.items()
    ]


def list_districts(state_key: str | None = None) -> list[dict]:
    keys = [state_key] if state_key else list(STATE_ID_BY_KEY.keys())
    out = []
    for key in keys:
        path = _state_dir(key) / "index" / "districts.json"
        if not path.is_file():
            continue
        sid = STATE_ID_BY_KEY[key]
        for d in json.loads(path.read_text(encoding="utf-8")):
            out.append({
                "id": str(d["id"]),
                "district_id": str(d.get("district_id") or d["id"]),
                "name": d.get("name") or str(d["id"]),
                "state_key": key,
                "state_id": sid,
                "state_name": STATE_BY_ID[sid]["label"],
                "center": d.get("center"),
            })
    out.sort(key=lambda x: (x["state_name"], x["name"]))
    return out


def get_district(district_id: str | int, state_key: str | None = None) -> dict | None:
    did = str(district_id)
    for d in list_districts(state_key):
        if d["district_id"] == did or d["id"] == did:
            return d
    return None


def _state_key_for_district_id(district_id: str | int | None) -> str | None:
    """Map LGD district id → state_key (andhra|telangana). Defined here so early callers work after split."""
    if district_id is None or str(district_id).strip() == "":
        return None
    dist = get_district(district_id)
    return (dist or {}).get("state_key")


def road_length_summary(state_key: str | None = None, district_id: str | int | None = None) -> dict:
    """NH/SH/MDR/local km totals from roads_meta.json (state) or district segment index."""
    if state_key and district_id:
        did = str(district_id)
        dist = get_district(did, state_key)
        by = {"nh": 0.0, "sh": 0.0, "mdr": 0.0, "other": 0.0}
        for feat in _segment_features(state_key, did):
            props = feat.get("properties") or {}
            rc = props.get("road_class") or "other"
            if rc not in by:
                rc = "other"
            by[rc] += float(props.get("length_km") or 0)
        label = (dist or {}).get("name") or did
        state_label = STATE_BY_ID[STATE_ID_BY_KEY[state_key]]["label"]
        return {
            "scope": "district",
            "state_key": state_key,
            "district_id": did,
            "label": f"{label} district",
            "parent_label": state_label,
            "km_by_class": {k: round(v, 1) for k, v in by.items()},
            "total_km": round(sum(by.values()), 1),
        }

    if not state_key:
        # India / no state — aggregate both states we have
        totals = {"nh": 0.0, "sh": 0.0, "mdr": 0.0, "other": 0.0, "total": 0.0}
        states = []
        for key in STATE_ID_BY_KEY:
            meta_path = _state_dir(key) / "roads_meta.json"
            if not meta_path.is_file():
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            by = meta.get("km_by_class") or {}
            entry = {
                "state_key": key,
                "state_name": STATE_BY_ID[STATE_ID_BY_KEY[key]]["label"],
                "km_by_class": by,
                "total_km": meta.get("total_km", 0),
            }
            states.append(entry)
            for k in ("nh", "sh", "mdr", "other"):
                totals[k] += float(by.get(k) or 0)
            totals["total"] += float(meta.get("total_km") or 0)
        return {
            "scope": "india_available",
            "label": "Andhra Pradesh + Telangana (available data)",
            "km_by_class": {k: round(v, 1) for k, v in totals.items() if k != "total"},
            "total_km": round(totals["total"], 1),
            "states": states,
            "note": "Full India road inventory is not loaded — showing AP + TG only.",
        }

    meta_path = _state_dir(state_key) / "roads_meta.json"
    if not meta_path.is_file():
        raise FileNotFoundError(
            f"Missing {meta_path}. Run: python tools/gis/download_state_roads.py --state {state_key} --all-roads"
        )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    by = meta.get("km_by_class") or {}
    return {
        "scope": "state",
        "state_key": state_key,
        "label": STATE_BY_ID[STATE_ID_BY_KEY[state_key]]["label"],
        "km_by_class": {
            "nh": round(float(by.get("nh") or 0), 1),
            "sh": round(float(by.get("sh") or 0), 1),
            "mdr": round(float(by.get("mdr") or 0), 1),
            "other": round(float(by.get("other") or 0), 1),
        },
        "total_km": round(float(meta.get("total_km") or 0), 1),
        "feature_count": meta.get("feature_count"),
    }


def _segment_id(props: dict, district_id: str | None = None) -> str:
    if props.get("segment_id"):
        return str(props["segment_id"])
    did = district_id or props.get("district_id") or "x"
    osm = props.get("osm_id") or props.get("osmid") or props.get("id") or "0"
    return f"{did}_{osm}"


def _segment_features(state_key: str, district_id: str) -> list[dict]:
    sk = resolve_state_key(state_key=state_key)
    if not sk:
        return []
    try:
        did = _safe_district_id(district_id)
    except ValueError:
        return []
    path = _state_dir(sk) / "index" / "segments" / f"{did}.geojson"
    # Defense in depth: stay under the segments directory.
    segs_dir = (_state_dir(sk) / "index" / "segments").resolve()
    try:
        resolved = path.resolve()
    except OSError:
        return []
    if segs_dir not in resolved.parents and resolved.parent != segs_dir:
        return []
    if not path.is_file():
        return []
    key = (sk, did)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    hit = _SEGMENT_FEATURE_CACHE.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    data = json.loads(path.read_text(encoding="utf-8"))
    features = data.get("features", [])
    _SEGMENT_FEATURE_CACHE[key] = (mtime, features)
    return features


# Lightweight per-segment index for routing / search (avoids re-parsing geometry)


def _snap_index_path(state_key: str, district_id: str) -> Path:
    return _state_dir(state_key) / "index" / "segments" / f"{district_id}.snap.pkl"


def _build_snap_grid(
    rows: list[tuple[float, float, str, str, str]],
    *,
    cell: float = _SNAP_CELL_DEG,
) -> dict[tuple[int, int], list[tuple[float, float, str, str, str]]]:
    grid: dict[tuple[int, int], list[tuple[float, float, str, str, str]]] = {}
    for row in rows:
        lat, lon = row[0], row[1]
        key = (int(math.floor(lat / cell)), int(math.floor(lon / cell)))
        grid.setdefault(key, []).append(row)
    return grid


def _mids_from_geojson(path: Path) -> list[tuple[float, float, str, str, str]]:
    """Parse segment GeoJSON into (lat, lon, name, ref, district_name) only — no ends."""
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[tuple[float, float, str, str, str]] = []
    for feat in data.get("features") or []:
        props = feat.get("properties") or {}
        try:
            length = float(props.get("length_km") or 0)
        except (TypeError, ValueError):
            length = 0.0
        if length <= 0:
            continue
        mid = _geom_midpoint(feat.get("geometry"))
        if not mid:
            continue
        out.append((
            float(mid[0]),
            float(mid[1]),
            str(props.get("name") or ""),
            str(props.get("ref") or ""),
            str(props.get("district_name") or ""),
        ))
    return out


def _get_snap_index(state_key: str, district_id: str) -> dict:
    """Grid of road midpoints for fast locate. Disk-cached next to the GeoJSON."""
    did = str(district_id)
    path = _state_dir(state_key) / "index" / "segments" / f"{did}.geojson"
    key = (state_key, did)
    try:
        mtime = path.stat().st_mtime if path.is_file() else 0.0
    except OSError:
        mtime = 0.0
    hit = _SNAP_INDEX_CACHE.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    if not path.is_file():
        empty = {"grid": {}, "cell": _SNAP_CELL_DEG, "n": 0}
        _SNAP_INDEX_CACHE[key] = (mtime, empty)
        return empty

    with _SNAP_INDEX_LOCK:
        hit = _SNAP_INDEX_CACHE.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
        pkl = _snap_index_path(state_key, did)
        if pkl.is_file():
            try:
                blob = pickle.loads(pkl.read_bytes())
                if (
                    isinstance(blob, dict)
                    and blob.get("mtime") == mtime
                    and abs(float(blob.get("cell") or 0) - _SNAP_CELL_DEG) < 1e-9
                    and isinstance(blob.get("grid"), dict)
                ):
                    # Keys may have been stored as tuples; normalize after unpickle.
                    grid_in = blob["grid"]
                    grid: dict[tuple[int, int], list] = {}
                    for gk, rows in grid_in.items():
                        if isinstance(gk, tuple) and len(gk) == 2:
                            grid[(int(gk[0]), int(gk[1]))] = rows
                        elif isinstance(gk, str) and "," in gk:
                            a, b = gk.split(",", 1)
                            grid[(int(a), int(b))] = rows
                    idx = {
                        "grid": grid,
                        "cell": _SNAP_CELL_DEG,
                        "n": int(blob.get("n") or 0),
                    }
                    _SNAP_INDEX_CACHE[key] = (mtime, idx)
                    return idx
            except Exception:
                pass

        rows = _mids_from_geojson(path)
        grid = _build_snap_grid(rows)
        idx = {"grid": grid, "cell": _SNAP_CELL_DEG, "n": len(rows)}
        try:
            # String keys so the pickle stays stable across Python versions.
            disk_grid = {f"{i},{j}": v for (i, j), v in grid.items()}
            pkl.write_bytes(pickle.dumps({
                "mtime": mtime,
                "cell": _SNAP_CELL_DEG,
                "n": len(rows),
                "grid": disk_grid,
            }, protocol=pickle.HIGHEST_PROTOCOL))
        except OSError:
            pass
        _SNAP_INDEX_CACHE[key] = (mtime, idx)
        return idx


def _nearest_snap_in_district(
    lat: float,
    lon: float,
    state_key: str,
    district_id: str,
    *,
    max_km: float = 15.0,
) -> tuple[float, float, str, str, str, float] | None:
    """Return (lat, lon, name, ref, district_name, distance_km) or None."""
    idx = _get_snap_index(state_key, district_id)
    grid = idx.get("grid") or {}
    if not grid:
        return None
    cell = float(idx.get("cell") or _SNAP_CELL_DEG)
    # Bbox in degrees; lon shrinks near poles but TG/AP are fine with cos(lat).
    dlat = max_km / 111.0
    cos_lat = max(0.2, math.cos(math.radians(lat)))
    dlon = max_km / (111.0 * cos_lat)
    i0 = int(math.floor((lat - dlat) / cell))
    i1 = int(math.floor((lat + dlat) / cell))
    j0 = int(math.floor((lon - dlon) / cell))
    j1 = int(math.floor((lon + dlon) / cell))
    best_d = max_km
    best: tuple[float, float, str, str, str] | None = None
    for i in range(i0, i1 + 1):
        for j in range(j0, j1 + 1):
            for row in grid.get((i, j), ()):
                ml, mo = float(row[0]), float(row[1])
                if abs(ml - lat) > dlat or abs(mo - lon) > dlon:
                    continue
                d = _haversine_km(lat, lon, ml, mo)
                if d < best_d:
                    best_d = d
                    best = (ml, mo, str(row[2] or ""), str(row[3] or ""), str(row[4] or ""))
    if best is None:
        return None
    return (*best, best_d)


def prewarm_snap_indexes(district_ids: list | set | None) -> dict:
    """Build/load snap indexes for the given LGD districts (background-safe)."""
    loaded: list[str] = []
    for x in (district_ids or []):
        if x is None or str(x).strip() == "":
            continue
        did = str(x)
        sk = _state_key_for_district_id(did)
        if not sk:
            continue
        try:
            idx = _get_snap_index(sk, did)
            loaded.append(did)
            _ = idx.get("n")
        except Exception:
            continue
    return {"districts": loaded, "count": len(loaded)}


_DISTRICT_SELF_LOCK = threading.Lock()
_DISTRICT_SELF_BUSY: set[int] = set()


def update_videographer_districts_self(
    user_id: int,
    district_ids: list,
    *,
    state_id: int | None = None,
) -> dict:
    """Videographer self-service district pick — only districts in their state."""
    uid = int(user_id)
    ids = [str(x) for x in (district_ids or []) if x is not None and str(x).strip()]
    if not ids:
        raise ValueError("Select at least one district")
    sid = int(state_id) if state_id is not None else None
    if sid is None:
        keys = state_keys_for_district_ids(ids)
        if keys:
            sid = STATE_ID_BY_KEY[keys[0]]
    if sid is None:
        raise ValueError("state_id required to pick districts")
    # Restrict to districts that belong to the VG's state (no cross-state self-grant).
    sk = None
    for k, v in STATE_ID_BY_KEY.items():
        if int(v) == int(sid):
            sk = k
            break
    if not sk:
        raise ValueError("Unknown state")
    allowed = {str(d.get("district_id")) for d in (list_districts(sk) or []) if d.get("district_id") is not None}
    bad = [i for i in ids if i not in allowed]
    if bad:
        raise ValueError(
            f"District(s) not in your state: {', '.join(bad[:8])}"
            + ("…" if len(bad) > 8 else "")
        )
    with _DISTRICT_SELF_LOCK:
        if uid in _DISTRICT_SELF_BUSY:
            raise ValueError("Districts are loading — please wait.")
        _DISTRICT_SELF_BUSY.add(uid)
    try:
        prewarm_snap_indexes(ids)
        import db_utils
        row = db_utils.update_user_districts(uid, sid, [int(x) for x in ids])
        if not row:
            raise ValueError("Could not update districts")
        return {"ok": True, "user": row, "district_ids": ids}
    finally:
        with _DISTRICT_SELF_LOCK:
            _DISTRICT_SELF_BUSY.discard(uid)


def schedule_prewarm_snap_indexes(district_ids: list | set | None) -> None:
    """Fire-and-forget prewarm so the first locate after login is warm."""
    ids = []
    for x in (district_ids or []):
        if x is None or str(x).strip() == "":
            continue
        s = str(x)
        if s not in ids:
            ids.append(s)
    if not ids:
        return
    key = ",".join(sorted(ids))
    if key in _SNAP_PREWARM_STARTED:
        return
    _SNAP_PREWARM_STARTED.add(key)

    def _run():
        try:
            prewarm_snap_indexes(ids)
        except Exception:
            _SNAP_PREWARM_STARTED.discard(key)

    threading.Thread(target=_run, name="snap-prewarm", daemon=True).start()


def _segment_meta(state_key: str, district_id: str) -> dict[str, dict]:
    """sid -> {length, road_class, mid, ends, name, ref, district_name}."""
    path = _state_dir(state_key) / "index" / "segments" / f"{district_id}.geojson"
    key = (state_key, str(district_id))
    try:
        mtime = path.stat().st_mtime if path.is_file() else 0.0
    except OSError:
        mtime = 0.0
    hit = _SEGMENT_META_CACHE.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    out: dict[str, dict] = {}
    for feat in _segment_features(state_key, str(district_id)):
        props = feat.get("properties") or {}
        sid = _segment_id(props, str(district_id))
        length = float(props.get("length_km") or 0)
        if length <= 0:
            continue
        geom = feat.get("geometry")
        mid = _geom_midpoint(geom)
        if not mid:
            continue
        ends = []
        for part in _line_coord_parts(geom):
            u = _node_key(part[0][0], part[0][1])
            v = _node_key(part[-1][0], part[-1][1])
            if u != v:
                ends.append((u, v))
        if not ends:
            continue
        out[sid] = {
            "length": length,
            "road_class": props.get("road_class", "other") or "other",
            "mid": mid,
            "ends": ends,
            "name": str(props.get("name") or ""),
            "ref": str(props.get("ref") or ""),
            "district_name": str(props.get("district_name") or ""),
            "district_id": str(district_id),
            "state_key": state_key,
        }
    _SEGMENT_META_CACHE[key] = (mtime, out)
    return out


def _km_for_segment_ids(state_key: str, district_id: str, ids: list[str]) -> float:
    if not ids:
        return 0.0
    meta = _segment_meta(state_key, str(district_id))
    id_set = set(ids)
    return sum(float(meta[sid]["length"]) for sid in id_set if sid in meta)


def _assignment_scopes(entry: dict) -> list[tuple[str, str]]:
    """Unique (state_key, district_id) scopes covered by an assignment (multi-leg aware)."""
    scopes: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for leg in entry.get("legs") or []:
        sk = leg.get("state_key") or entry.get("state_key") or ""
        did = str(leg.get("district_id") or entry.get("district_id") or "")
        if sk and did and (sk, did) not in seen:
            seen.add((sk, did))
            scopes.append((sk, did))
    if not scopes:
        sk = entry.get("state_key") or ""
        did = str(entry.get("district_id") or "")
        if sk and did:
            scopes.append((sk, did))
    return scopes


def _round_geom(geom: dict | None, ndigits: int = 5) -> dict | None:
    """Reduce coordinate precision to shrink JSON payloads."""
    if not geom or "coordinates" not in geom:
        return geom

    def _round(c):
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            return [round(float(c[0]), ndigits), round(float(c[1]), ndigits)]
        return [_round(x) for x in c]

    return {"type": geom.get("type"), "coordinates": _round(geom["coordinates"])}


MAP_PROP_KEYS = (
    "id", "segment_id", "district_id", "state_key", "road_class", "status",
    "length_km", "name", "ref", "highway", "district_name",
)


def segments_geojson(
    state_key: str,
    district_id: str,
    *,
    road_classes: set[str] | None = None,
    light: bool = True,
) -> dict:
    state = _load_state()
    status_map = state.get("segment_status", {})
    features = []
    for feat in _segment_features(state_key, str(district_id)):
        props_in = feat.get("properties") or {}
        rc = props_in.get("road_class", "other")
        if road_classes and rc not in road_classes:
            continue
        props = dict(props_in)
        sid = _segment_id(props, str(district_id))
        props["segment_id"] = sid
        props["id"] = sid
        props["district_id"] = str(district_id)
        props["state_key"] = state_key
        props["status"] = status_map.get(sid, "available")
        props["length_km"] = round(float(props.get("length_km") or 0), 3)
        if light:
            props = {k: props[k] for k in MAP_PROP_KEYS if k in props}
        geom = feat.get("geometry")
        if light:
            geom = _round_geom(geom)
        features.append({
            "type": "Feature",
            "properties": props,
            "geometry": geom,
        })
    return {"type": "FeatureCollection", "features": features}


def state_nh_geojson(state_key: str) -> dict:
    """NH-only overview for a state (prebuilt index/nh_overview.geojson)."""
    sk = resolve_state_key(state_key=state_key)
    if not sk:
        raise ValueError(f"Unknown state_key: {state_key!r}")
    path = _state_dir(sk) / "index" / "nh_overview.geojson"
    if not path.is_file():
        raise FileNotFoundError(
            f"Missing NH overview for {sk}. "
            "Re-run download_state_roads + build_nh_overview, or see tools/gis/README.md"
        )
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    hit = _NH_CACHE.get(sk)
    if hit and hit[0] == mtime:
        return hit[1]

    data = json.loads(path.read_text(encoding="utf-8"))
    features = []
    for feat in data.get("features", []):
        props = dict(feat.get("properties") or {})
        props.setdefault("road_class", "nh")
        props["state_key"] = sk
        props["length_km"] = round(float(props.get("length_km") or 0), 3)
        features.append({
            "type": "Feature",
            "properties": props,
            "geometry": feat.get("geometry"),
        })
    result = {"type": "FeatureCollection", "features": features}
    _NH_CACHE[sk] = (mtime, result)
    return result


