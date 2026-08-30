import os
import cv2
import time
import math
import zipfile
import tempfile
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict

import exifread
from datetime import datetime
import re

# Optional: video metadata GPS (may be absent in most MP4s)
try:
    from pymediainfo import MediaInfo
except Exception:
    MediaInfo = None

# Optional: GPS log parsing (CSV/XLSX)
try:
    import pandas as pd
except Exception:
    pd = None

from model_loader import load_model, resolve_yolo_device
from utils import severity_from_area_and_position

# --- S3 integration ---
from s3_utils import (
    is_s3_configured,
    upload_and_link,
    upload_file,
    download_file,
    move_object,
    copy_object,
    find_sibling_gps_key,
    get_input_bucket,
    get_processed_bucket,
)



# ============================================================
# GPS / EXIF helpers (unchanged)
# ============================================================
def _to_deg(values):
    d = float(values[0].num) / float(values[0].den)
    m = float(values[1].num) / float(values[1].den)
    s = float(values[2].num) / float(values[2].den)
    return d + (m / 60.0) + (s / 3600.0)


def read_gps_from_image(path: str) -> Optional[Tuple[float, float]]:
    try:
        with open(path, "rb") as f:
            tags = exifread.process_file(f, details=False)
        lat = tags.get("GPS GPSLatitude")
        lat_ref = tags.get("GPS GPSLatitudeRef")
        lon = tags.get("GPS GPSLongitude")
        lon_ref = tags.get("GPS GPSLongitudeRef")
        if not (lat and lat_ref and lon and lon_ref):
            return None
        lat_val = _to_deg(lat.values)
        if str(lat_ref.values[0]).upper() == "S":
            lat_val = -lat_val
        lon_val = _to_deg(lon.values)
        if str(lon_ref.values[0]).upper() == "W":
            lon_val = -lon_val
        return (lat_val, lon_val)
    except Exception:
        return None


def read_gps_from_video(path: str) -> Optional[Tuple[float, float]]:
    if MediaInfo is None:
        return None
    try:
        mi = MediaInfo.parse(path)
        for track in mi.tracks:
            loc = getattr(track, "location", None)
            if loc and isinstance(loc, str):
                loc = loc.strip().replace("/", "")
                if "," in loc:
                    a, b = loc.split(",", 1)
                    return (float(a), float(b))
                if loc.startswith(("+", "-")) and ("+" in loc[1:] or "-" in loc[1:]):
                    for i in range(1, len(loc)):
                        if loc[i] in "+-":
                            return (float(loc[:i]), float(loc[i:]))
            iso = getattr(track, "comapplequicktime_location_iso6709", None)
            if iso and isinstance(iso, str):
                iso = iso.strip().replace("/", "")
                for i in range(1, len(iso)):
                    if iso[i] in "+-":
                        return (float(iso[:i]), float(iso[i:]))
        return None
    except Exception:
        return None


def read_timestamp_from_image(path: str) -> str:
    try:
        with open(path, "rb") as f:
            tags = exifread.process_file(f, details=False)
        for k in ["EXIF DateTimeOriginal", "EXIF DateTimeDigitized", "Image DateTime"]:
            v = tags.get(k)
            if v:
                s = str(v).strip()
                try:
                    dt = datetime.strptime(s, "%Y:%m:%d %H:%M:%S")
                    return dt.isoformat()
                except Exception:
                    pass
    except Exception:
        pass
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).isoformat()
    except Exception:
        return ""


def _parse_mediainfo_date(s: str) -> Optional[str]:
    if not s:
        return None
    s = str(s).replace("UTC ", "").strip()
    s = re.sub(r"\.\d+$", "", s)
    for fmt in ("%Y-%m-%d %H:%M:%S",):
        try:
            return datetime.strptime(s, fmt).isoformat()
        except Exception:
            pass
    return None


def read_timestamp_from_video(path: str) -> str:
    if MediaInfo is not None:
        try:
            mi = MediaInfo.parse(path)
            general_tracks = [t for t in mi.tracks if t.track_type == "General"]
            if general_tracks:
                g = general_tracks[0]
                for c in [
                    getattr(g, "encoded_date", None),
                    getattr(g, "tagged_date", None),
                    getattr(g, "recorded_date", None),
                    getattr(g, "file_modified_date", None),
                ]:
                    iso = _parse_mediainfo_date(c) if c else None
                    if iso:
                        return iso
        except Exception:
            pass
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).isoformat()
    except Exception:
        return ""


def maps_link(lat: Optional[float], lon: Optional[float]) -> str:
    if lat is None or lon is None:
        return ""
    return f"https://www.google.com/maps?q={lat},{lon}"


def _is_xlsx_by_magic(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"PK"
    except Exception:
        return False


def load_gps_log(gps_log_path: str) -> Dict[int, Tuple[float, float, str]]:
    """
    Loads a GPS log into {videosecond: (lat, lon, timestamp_str)}.
    Supports CSV, XLSX/XLS, and JSON.

    JSON shapes accepted:
      1) Array of objects:  [{"VideoSecond":1,"Latitude":17.4,"Longitude":78.5,"Timestamp":"..."}, ...]
      2) Object keyed by second: {"1":{"latitude":17.4,"longitude":78.5,"timestamp":"..."}, ...}
      3) Wrapped: {"data": [...]} or {"points": [...]} or {"gps": [...]}
    Field names are case-insensitive: videosecond/second/sec, latitude/lat, longitude/lon/lng, timestamp/time/ts
    """
    if not gps_log_path or not os.path.exists(gps_log_path):
        return {}

    ext = gps_log_path.lower()

    # ----- JSON branch -----
    if ext.endswith(".json"):
        return _load_gps_log_json(gps_log_path)

    # ----- CSV / XLSX branch -----
    if pd is None:
        raise RuntimeError("pandas is required to read CSV/XLSX GPS logs. pip install pandas openpyxl")
    try:
        is_excel = ext.endswith((".xlsx", ".xls")) or _is_xlsx_by_magic(gps_log_path)
        df = pd.read_excel(gps_log_path) if is_excel else pd.read_csv(gps_log_path)
        cols = {str(c).strip().lower(): c for c in df.columns}
        vs_col = cols.get("videosecond") or cols.get("second") or cols.get("sec")
        lat_col = cols.get("latitude") or cols.get("lat")
        lon_col = cols.get("longitude") or cols.get("lon") or cols.get("lng")
        ts_col = cols.get("timestamp") or cols.get("time") or cols.get("ts")
        if not (vs_col and lat_col and lon_col):
            return {}
        gps_map: Dict[int, Tuple[float, float, str]] = {}
        for _, row in df.iterrows():
            try:
                # Prefer round so 0.6s → 1, not truncating everything toward 0
                sec = int(round(float(row[vs_col])))
                lat = float(row[lat_col])
                lon = float(row[lon_col])
                ts = str(row[ts_col]) if ts_col else ""
                if sec < 0:
                    continue
                gps_map[sec] = (lat, lon, ts)
            except Exception:
                continue
        return _densify_gps_map(gps_map)
    except Exception:
        return {}


def _densify_gps_map(
    gps_map: Dict[int, Tuple[float, float, str]],
    max_gap: int = 8,
) -> Dict[int, Tuple[float, float, str]]:
    """Fill missing integer seconds by linear interpolation (mobile GPS is often sparse)."""
    if len(gps_map) < 2:
        return gps_map
    keys = sorted(gps_map.keys())
    out = dict(gps_map)
    for i in range(len(keys) - 1):
        a, b = keys[i], keys[i + 1]
        gap = b - a
        if gap <= 1 or gap > max_gap:
            continue
        lat1, lon1, ts1 = gps_map[a]
        lat2, lon2, ts2 = gps_map[b]
        for s in range(a + 1, b):
            t = (s - a) / gap
            out[s] = (
                lat1 + (lat2 - lat1) * t,
                lon1 + (lon2 - lon1) * t,
                ts1 or ts2 or "",
            )
    return out


def _json_loads_lenient(raw: str):
    """Parse JSON, tolerating malformed array-element separators seen in real
    mobile GPS logger exports: the comma between two objects is sometimes
    duplicated ("...},\n,\n{...") and sometimes missing entirely ("...}  {..."),
    both observed in the same file. Only kicks in when strict parsing already
    failed, and only normalizes '}'..'{' boundaries to a single comma — never
    touches string content.
    """
    import json
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        repaired = re.sub(r"}[\s,]*{", "},{", raw)
        return json.loads(repaired)


def _load_gps_extras_json(path: str) -> Dict[int, dict]:
    """Load full GPS records keyed by videoSecond for DB storage of extra fields."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = _json_loads_lenient(f.read())
    except Exception as e:
        print(f"[gps] Could not parse GPS extras JSON ({path}): {e}")
        return {}
    if isinstance(data, dict):
        for key in ("data", "points", "gps", "log", "records"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break
    result = {}
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            sec = item.get("videoSecond") or item.get("second") or item.get("sec")
            try:
                result[int(sec)] = item
            except Exception:
                continue
    return result


def _load_gps_log_json(path: str) -> Dict[int, Tuple[float, float, str]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = _json_loads_lenient(f.read())
    except Exception as e:
        print(f"[gps] Could not parse GPS log JSON ({path}): {e}")
        return {}

    # Unwrap common containers
    if isinstance(data, dict):
        for key in ("data", "points", "gps", "log", "records"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break

    gps_map: Dict[int, Tuple[float, float, str]] = {}

    def _pick(d: dict, *names):
        # case-insensitive lookup
        lower = {str(k).strip().lower(): v for k, v in d.items()}
        for n in names:
            if n in lower:
                return lower[n]
        return None

    # Case 1: list of records
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            sec = _pick(item, "videosecond", "second", "sec")
            lat = _pick(item, "latitude", "lat")
            lon = _pick(item, "longitude", "lon", "lng")
            ts = _pick(item, "timestamp", "time", "ts") or ""
            try:
                gps_map[int(round(float(sec)))] = (float(lat), float(lon), str(ts))
            except Exception:
                continue
        return _densify_gps_map(gps_map)

    # Case 2: dict keyed by second
    if isinstance(data, dict):
        for k, v in data.items():
            if not isinstance(v, dict):
                continue
            try:
                sec = int(round(float(k)))
            except Exception:
                continue
            lat = _pick(v, "latitude", "lat")
            lon = _pick(v, "longitude", "lon", "lng")
            ts = _pick(v, "timestamp", "time", "ts") or ""
            try:
                gps_map[sec] = (float(lat), float(lon), str(ts))
            except Exception:
                continue
        return _densify_gps_map(gps_map)

    return {}


def _gps_for_frame_second(
    frame_second_0_based: int,
    gps_map: Dict[int, Tuple[float, float, str]],
    gps_second_offset: int = 0,
    max_radius: int = 15,
) -> Tuple[Optional[float], Optional[float], str]:
    """Find the GPS fix nearest to a given video second.

    Real logs commonly have a run of null/missing fixes right at the start
    (the GPS chip hasn't acquired a lock yet) — entries with no coordinates
    are dropped when the log is loaded, so a narrow +-1s window can miss a
    perfectly good nearby fix. Expand outward (nearest first) up to
    max_radius seconds before giving up.
    """
    if not gps_map:
        return None, None, ""
    # Prefer 0-based video seconds (Flutter/Expo elapsed). Also try legacy 1-based logs.
    candidates = [
        frame_second_0_based + gps_second_offset,
        frame_second_0_based + 1 + gps_second_offset,
    ]
    for target in candidates:
        if target in gps_map:
            lat, lon, ts = gps_map[target]
            return lat, lon, ts
    target0 = frame_second_0_based + gps_second_offset
    for radius in range(1, max_radius + 1):
        for sec in (target0 - radius, target0 + radius, target0 + 1 - radius, target0 + 1 + radius):
            if sec in gps_map:
                lat, lon, ts = gps_map[sec]
                return lat, lon, ts
    return None, None, ""


