"""Reverse-geocode helpers (Nominatim / OpenStreetMap) for city/state on detections."""
from __future__ import annotations

import time
import urllib.parse
import urllib.request
import json
from typing import Any

# Simple in-process cache — enough for a single detection run / backfill batch.
_CACHE: dict[str, dict[str, str]] = {}
_LAST_CALL = 0.0
_MIN_INTERVAL = 1.05  # Nominatim usage policy


def _cache_key(lat: float, lon: float) -> str:
    return f"{round(float(lat), 4)},{round(float(lon), 4)}"


def reverse_geocode(lat: float | None, lon: float | None) -> dict[str, str]:
    """
    Return {city, state, country, street_name, full_address} or empty strings.
    Fails soft (returns {}) on network/API errors.
    """
    if lat is None or lon is None:
        return {}
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError):
        return {}
    if not (-90 <= lat_f <= 90 and -180 <= lon_f <= 180):
        return {}

    key = _cache_key(lat_f, lon_f)
    if key in _CACHE:
        return dict(_CACHE[key])

    global _LAST_CALL
    wait = _MIN_INTERVAL - (time.time() - _LAST_CALL)
    if wait > 0:
        time.sleep(wait)

    params = urllib.parse.urlencode({
        "lat": f"{lat_f:.6f}",
        "lon": f"{lon_f:.6f}",
        "format": "json",
        "addressdetails": 1,
        "zoom": 16,
    })
    url = f"https://nominatim.openstreetmap.org/reverse?{params}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "SmartRoadAP/1.0 (road-maintenance; detection-reports)",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        _LAST_CALL = time.time()
    except Exception:
        _LAST_CALL = time.time()
        return {}

    addr = data.get("address") or {}
    city = (
        addr.get("city")
        or addr.get("town")
        or addr.get("village")
        or addr.get("suburb")
        or addr.get("county")
        or ""
    )
    state = addr.get("state") or addr.get("region") or ""
    country = addr.get("country") or ""
    street = addr.get("road") or addr.get("pedestrian") or addr.get("neighbourhood") or ""
    full = (data.get("display_name") or "").strip()
    out = {
        "city": str(city).strip(),
        "state": str(state).strip(),
        "country": str(country).strip(),
        "street_name": str(street).strip(),
        "full_address": full,
    }
    _CACHE[key] = out
    return dict(out)


def enrich_pothole_rows(rows: list[Any], *, max_lookups: int = 80) -> int:
    """
    Fill empty city/state on DetectionRow-like objects that have lat/lon.
    Returns number of rows enriched.
    """
    n = 0
    for r in rows or []:
        if n >= max_lookups:
            break
        city = getattr(r, "city", None) if hasattr(r, "city") else None
        state = getattr(r, "state", None) if hasattr(r, "state") else None
        # DetectionRow may not have city/state attrs — enrichment happens at save time
        lat = getattr(r, "lat", None)
        lon = getattr(r, "lon", None)
        if lat is None or lon is None:
            continue
        geo = reverse_geocode(lat, lon)
        if not geo:
            continue
        # Attach as dynamic attrs for save_potholes extras merge
        if not getattr(r, "_geo", None):
            r._geo = geo  # type: ignore[attr-defined]
            n += 1
    return n


def label_from_coords(lat: float | None, lon: float | None) -> str:
    """Short human label: street/city if available, else lat,lon."""
    if lat is None or lon is None:
        return "unknown"
    geo = reverse_geocode(lat, lon)
    street = geo.get("street_name") or ""
    city = geo.get("city") or ""
    if street and city:
        return f"{street}, {city}"
    if street or city:
        return street or city
    return f"{float(lat):.4f},{float(lon):.4f}"
