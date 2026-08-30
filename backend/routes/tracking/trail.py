"""tracking.trail — extends store (includes private _names)."""
from __future__ import annotations

import routes.tracking.store as _store

globals().update({k: v for k, v in vars(_store).items() if not k.startswith('__')})

def covered_trail_features(
    trail,
    *,
    jump_km: float = TRAIL_JUMP_KM,
    start: dict | tuple | None = None,
    end: dict | tuple | None = None,
    entry: dict | None = None,
) -> list[dict]:
    """Grey LineString along GPS from start to nearest approach of the end pin.

    Map overlay freezes at the closest end-pin visit (``lock_end=True``) so grey
    reaches the destination marker; cyan may continue with post-end wander.
    Covered-km accounting still uses an open clip separately.

    Geometry is road-hugged for display when an assignment ``entry`` is present;
    ``length_km`` stays the raw GPS-chord length so KPIs are unchanged.
    """
    from routes import survey_service

    def _pin(p) -> tuple[float, float] | None:
        if not p:
            return None
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            try:
                return float(p[0]), float(p[1])
            except (TypeError, ValueError):
                return None
        if isinstance(p, dict):
            try:
                return float(p.get("lat")), float(p.get("lon") if p.get("lon") is not None else p.get("lng"))
            except (TypeError, ValueError):
                return None
        return None

    if entry is not None:
        start_pins, end_pins = survey_service._effective_progress_pins(entry)
    else:
        start_pins = [p for p in (_pin(start),) if p]
        end_pins = [p for p in (_pin(end),) if p]

    if start_pins and end_pins:
        # Freeze at closest end approach so the grey overlay meets the end pin
        # (open clip left a jump-gap mid-route with grey stopping ~1 km early).
        points = _clip_trail_dicts_with_ts(
            trail, start_pins, end_pins, lock_end=True,
        )
    else:
        # No start+end pair: keep full trail.
        points = _trail_dicts_to_latlon_ts(trail)

    runs = _prepare_trail_runs_for_distance(points)
    features: list[dict] = []
    for i, run in enumerate(runs):
        if len(run) < 2:
            continue
        # Drop micro-teleports inside a run (safety; clean already split jumps)
        coords: list[list[float]] = []
        prev = None
        for lat, lon, _ts in run:
            if prev is not None:
                d = _haversine_km(prev[0], prev[1], lat, lon)
                if d > jump_km:
                    if len(coords) >= 2:
                        features.append(
                            _covered_linestring_feature(i, coords, survey_service, entry=entry)
                        )
                    coords = [[lon, lat]]
                    prev = (lat, lon)
                    continue
            coords.append([lon, lat])
            prev = (lat, lon)
        if len(coords) >= 2:
            features.append(
                _covered_linestring_feature(i, coords, survey_service, entry=entry)
            )
    return features


def _covered_linestring_feature(
    i: int,
    coords: list[list[float]],
    survey_service,
    *,
    entry: dict | None = None,
) -> dict:
    """Build grey feature: chord length for KPIs, road-hugged geometry for the map."""
    latlon = [(c[1], c[0]) for c in coords]
    chord_km = survey_service._polyline_length_km(latlon)
    draw = road_hugging_display_latlon(latlon, entry=entry) if entry else latlon
    if len(draw) < 2:
        draw = latlon
    out_coords = [[lon, lat] for lat, lon in draw]
    return {
        "type": "Feature",
        "properties": {
            "id": f"covered_trail_{i}",
            "segment_id": f"covered_trail_{i}",
            "status": "completed",
            "road_class": "nh",
            "route_kind": "covered",
            "trail_overlay": True,
            "length_km": round(float(chord_km or 0), 3),
            "display_hugged": len(draw) != len(latlon) or draw != latlon,
        },
        "geometry": {
            "type": "LineString",
            "coordinates": out_coords,
        },
    }


def covered_corridor_features(
    polyline,
    trail,
    *,
    snap_km: float = COVERED_SNAP_KM,
    start: dict | None = None,
    end: dict | None = None,
    entry: dict | None = None,
) -> list[dict]:
    """Map grey follows GPS trail start→end (polyline arg unused; kept for call-site compat)."""
    return covered_trail_features(trail, start=start, end=end, entry=entry)


def trail_for_user(user_id: int, date: str) -> list:
    """Trail points for a user/day (DB preferred, JSON fallback)."""
    import db_utils

    entry: dict = {}
    try:
        if db_utils.is_db_configured():
            day_state = db_utils.tracking_db_load_for_date(date)
            if day_state:
                entry = (day_state.get("videographers") or {}).get(str(int(user_id))) or {}
    except Exception:
        entry = {}
    if not entry:
        state = _load()
        entry = _roll_day(
            (state.get("videographers") or {}).get(str(int(user_id))) or {},
            date,
        )
    else:
        entry = _roll_day(entry, date)
    return list(entry.get("trail") or [])


def _by_class_typed_km(by_class: dict | None) -> float:
    c = by_class or {}
    return sum(float(c.get(k) or 0) for k in ("nh", "sh", "mdr"))


def _scale_by_class_to_total(by_class: dict | None, target_km: float) -> dict:
    """Preserve NH/SH/MDR mix while locking total to target_km (e.g. 6.68)."""
    base = dict(by_class or _empty_by_class())
    total = sum(float(base.get(k) or 0) for k in _empty_by_class())
    target = float(target_km or 0)
    if target <= 0.01:
        return _empty_by_class()
    if total < 0.05:
        return {**_empty_by_class(), "other": round(target, 3)}
    scale = target / total
    return {k: round(float(base.get(k) or 0) * scale, 3) for k in _empty_by_class()}


def _is_live(entry: dict) -> bool:
    last = entry.get("last") or {}
    ts = _parse_ts(last.get("ts"))
    if not ts:
        return False
    now = datetime.now(IST)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=IST)
    return (now - ts) <= timedelta(seconds=LIVE_TTL_SEC)


def _trail_ts_of(p: dict | None) -> datetime | None:
    if not isinstance(p, dict):
        return None
    return _parse_ts(p.get("ts") if isinstance(p.get("ts"), str) else None)


def _segment_filter_action(
    prev_lat: float,
    prev_lon: float,
    prev_ts: datetime | None,
    lat: float,
    lon: float,
    ts: datetime | None,
) -> str:
    """Classify a hop for coverage math.

    Returns:
      count — include distance, advance anchor
      skip  — ignore point (drift / impossible speed), keep anchor
      reset — GPS teleport, advance anchor without counting
    """
    try:
        delta = _haversine_km(prev_lat, prev_lon, lat, lon)
    except Exception:
        return "skip"
    if delta <= 0:
        return "skip"
    if delta < TRAIL_MIN_STEP_KM:
        return "skip"
    if delta >= TRAIL_JUMP_KM:
        return "reset"
    if prev_ts is not None and ts is not None:
        try:
            dt_s = (ts - prev_ts).total_seconds()
        except Exception:
            dt_s = None
        if dt_s is not None:
            if dt_s <= 0:
                # Duplicate / clock skew: only count if we moved a real step under jump.
                return "skip" if delta < TRAIL_MIN_STEP_KM * 2 else "count"
            speed = (delta / dt_s) * 3600.0
            if speed > TRAIL_MAX_SPEED_KMH:
                # Spike: drop the bad fix; big gap → resync anchor.
                return "reset" if delta >= 0.25 else "skip"
    return "count"


def _clean_trail_latlon_ts(
    points: list[tuple[float, float, datetime | None]],
) -> list[list[tuple[float, float, datetime | None]]]:
    """Drop stationary drift / speed spikes; split polyline on GPS teleports."""
    if not points:
        return []
    runs: list[list[tuple[float, float, datetime | None]]] = []
    cur: list[tuple[float, float, datetime | None]] = [points[0]]
    for lat, lon, ts in points[1:]:
        plat, plon, pts = cur[-1]
        action = _segment_filter_action(plat, plon, pts, lat, lon, ts)
        if action == "count":
            cur.append((lat, lon, ts))
        elif action == "reset":
            if len(cur) >= 2:
                runs.append(cur)
            cur = [(lat, lon, ts)]
        # skip → keep anchor
    if len(cur) >= 2:
        runs.append(cur)
    elif cur and not runs:
        runs.append(cur)
    return runs


def _perp_dist_km(
    lat: float, lon: float,
    a_lat: float, a_lon: float,
    b_lat: float, b_lon: float,
) -> float:
    """Equirectangular cross-track distance (km) from P to segment AB."""
    import math
    lat0 = (a_lat + b_lat + lat) / 3.0
    lon0 = (a_lon + b_lon + lon) / 3.0
    scale = math.cos(math.radians(lat0)) * 6371.0
    ax = math.radians(a_lon - lon0) * scale
    ay = math.radians(a_lat - lat0) * 6371.0
    bx = math.radians(b_lon - lon0) * scale
    by = math.radians(b_lat - lat0) * 6371.0
    px = math.radians(lon - lon0) * scale
    py = math.radians(lat - lat0) * 6371.0
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _douglas_peucker(
    points: list[tuple[float, float, datetime | None]],
    tol_km: float = TRAIL_SIMPLIFY_TOL_KM,
) -> list[tuple[float, float, datetime | None]]:
    """Simplify zigzag GPS noise before summing path length."""
    if len(points) < 3 or tol_km <= 0:
        return points
    stack = [(0, len(points) - 1)]
    keep = {0, len(points) - 1}
    while stack:
        i0, i1 = stack.pop()
        a = points[i0]
        b = points[i1]
        max_d = -1.0
        max_i = None
        for i in range(i0 + 1, i1):
            d = _perp_dist_km(points[i][0], points[i][1], a[0], a[1], b[0], b[1])
            if d > max_d:
                max_d = d
                max_i = i
        if max_i is not None and max_d > tol_km:
            keep.add(max_i)
            stack.append((i0, max_i))
            stack.append((max_i, i1))
    return [points[i] for i in sorted(keep)]


def _prepare_trail_runs_for_distance(
    points: list[tuple[float, float, datetime | None]],
) -> list[list[tuple[float, float, datetime | None]]]:
    """Filter + simplify into independent polyline runs (no teleport bridges)."""
    runs = _clean_trail_latlon_ts(points)
    return [_douglas_peucker(run) for run in runs if len(run) >= 2]


def _trail_dicts_to_latlon_ts(trail: list | None) -> list[tuple[float, float, datetime | None]]:
    raw: list[tuple[float, float, datetime | None]] = []
    for p in trail or []:
        if not isinstance(p, dict):
            continue
        try:
            lat = float(p.get("lat"))
            lon = float(p.get("lon") if p.get("lon") is not None else p.get("lng"))
        except (TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        raw.append((lat, lon, _trail_ts_of(p)))
    return raw


def _clip_trail_dicts_with_ts(
    trail: list | None,
    start_pins,
    end_pins,
    *,
    lock_end: bool = False,
) -> list[tuple[float, float, datetime | None]]:
    """Same start→end clip as survey truncate, preserving ping timestamps.

    ``lock_end=False`` (default for coverage): keep post-start trail open so
    turnaround fill-in after brushing the end pin still counts.
    ``lock_end=True``: freeze at closest end approach (seal / post-end wander).
    """
    from routes import survey_service

    valid: list[tuple[float, float, datetime | None]] = []
    for p in trail or []:
        if not isinstance(p, dict):
            continue
        try:
            lat = float(p.get("lat"))
            lon = float(p.get("lon") if p.get("lon") is not None else p.get("lng"))
        except (TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        valid.append((lat, lon, _trail_ts_of(p)))
    if not valid:
        return []
    # Truncate on the same valid sequence (no dict/pts index skew).
    as_dicts = [{"lat": a, "lon": b} for a, b, _ in valid]
    clipped = survey_service._truncate_trail_to_progress_pins(
        as_dicts, start_pins, end_pins, lock_end=lock_end,
    )
    if not clipped:
        return []

    def _find(lat, lon, start_at: int) -> int | None:
        for i in range(start_at, len(valid)):
            if abs(valid[i][0] - lat) < 1e-9 and abs(valid[i][1] - lon) < 1e-9:
                return i
        return None

    i0 = _find(clipped[0][0], clipped[0][1], 0)
    i1 = _find(clipped[-1][0], clipped[-1][1], i0 or 0)
    if i0 is None or i1 is None or i1 < i0:
        return [(a, b, None) for a, b in clipped]
    return valid[i0 : i1 + 1]


def _countable_segment_km(
    prev_lat: float,
    prev_lon: float,
    prev_ts: datetime | None,
    lat: float,
    lon: float,
    ts: datetime | None,
) -> float:
    """Distance to add for one hop after hygiene filters (0 if skipped)."""
    if _segment_filter_action(prev_lat, prev_lon, prev_ts, lat, lon, ts) != "count":
        return 0.0
    return _haversine_km(prev_lat, prev_lon, lat, lon)


def _live_ping_delta_km(
    session_tail: list,
    lat: float,
    lon: float,
    ts: datetime | None,
) -> tuple[float, float | None, float | None]:
    """Distance from the last *accepted* session fix (last point with delta_km > 0).

    Falls back to the first point of the capture session so dense <3 m pings
    accumulate once the device has moved far enough — without double-counting.
    Returns (delta_km, anchor_lat, anchor_lon).
    """
    if not session_tail:
        return 0.0, None, None
    anchor = None
    for p in reversed(list(session_tail)):
        if not isinstance(p, dict):
            continue
        try:
            if float(p.get("delta_km") or 0) > 0:
                anchor = p
                break
        except (TypeError, ValueError):
            continue
    if anchor is None:
        anchor = session_tail[0] if isinstance(session_tail[0], dict) else None
    if not isinstance(anchor, dict):
        return 0.0, None, None
    try:
        plat = float(anchor.get("lat"))
        plon = float(anchor.get("lon") if anchor.get("lon") is not None else anchor.get("lng"))
    except (TypeError, ValueError):
        return 0.0, None, None
    action = _segment_filter_action(plat, plon, _trail_ts_of(anchor), lat, lon, ts)
    if action == "count":
        return _haversine_km(plat, plon, lat, lon), plat, plon
    return 0.0, None, None


def _sum_trail_coverage(trail: list) -> tuple[float, dict]:
    """Sum stored per-point delta_km (authoritative after discard/commit)."""
    by_class = _empty_by_class()
    total = 0.0
    for p in trail or []:
        if not isinstance(p, dict):
            continue
        try:
            d = float(p.get("delta_km") or 0)
        except (TypeError, ValueError):
            d = 0.0
        if d <= 0:
            continue
        total += d
        rc = p.get("road_class") or "other"
        if rc not in by_class:
            rc = "other"
        by_class[rc] = float(by_class.get(rc) or 0) + d
    return round(total, 3), {k: round(float(by_class.get(k) or 0), 3) for k in _empty_by_class()}


def recompute_trail_coverage(user_id: int, date: str, trail: list) -> tuple[float, dict]:
    """Re-snap trail distance with dense corridor samples (fixes midpoint under-count)."""
    by_class = _empty_by_class()
    total = 0.0
    runs = _prepare_trail_runs_for_distance(_trail_dicts_to_latlon_ts(trail))
    for run in runs:
        prev = None
        for lat, lon, _ts in run:
            if prev is not None:
                try:
                    delta = _haversine_km(prev[0], prev[1], lat, lon)
                except Exception:
                    delta = 0.0
                rc = None
                if 0 < delta < TRAIL_JUMP_KM:
                    mid_lat = (prev[0] + lat) / 2
                    mid_lon = (prev[1] + lon) / 2
                    # Include sealed roads — otherwise post-complete KPIs collapse to "other"
                    rc = _nearest_road_class(
                        int(user_id), date, mid_lat, mid_lon, include_completed=True,
                    )
                    if not rc:
                        delta = 0.0
                else:
                    delta = 0.0
                if delta > 0:
                    total += delta
                    key = rc if rc in by_class else "other"
                    by_class[key] = float(by_class.get(key) or 0) + delta
            prev = (lat, lon)
    return round(total, 3), {k: round(float(by_class.get(k) or 0), 3) for k in _empty_by_class()}


def sanitize_trail_for_display(
    trail: list | None,
    *,
    entry: dict | None = None,
) -> list[dict]:
    """Filter GPS teleports / speed spikes for map cyan polyline (display only).

    When ``entry`` is provided, each cleaned run is projected onto nearby GIS
    road geometry so cyan hugs the carriageway. Coverage / seal math never
    reads this list — it still uses the raw trail.

    Returns a flat point list with intentional breaks between cleaned runs:
    a ``None`` sentinel is inserted between runs so the client can split
    polylines without drawing city-crossing straight segments.
    """
    pts = _trail_dicts_to_latlon_ts(trail)
    if not pts:
        return []
    runs = _clean_trail_latlon_ts(pts)
    out: list[dict | None] = []
    for ri, run in enumerate(runs):
        if ri > 0 and out:
            out.append(None)  # segment break
        latlon = [(lat, lon) for lat, lon, _ts in run]
        hugged = road_hugging_display_latlon(latlon, entry=entry) if entry else latlon
        if len(hugged) < 2:
            hugged = latlon
        # Preserve timestamps on endpoints when possible
        ts0 = run[0][2] if run else None
        ts1 = run[-1][2] if run else None
        for j, (lat, lon) in enumerate(hugged):
            row: dict = {"lat": lat, "lon": lon}
            if j == 0 and ts0 is not None:
                row["ts"] = ts0.isoformat()
            elif j == len(hugged) - 1 and ts1 is not None:
                row["ts"] = ts1.isoformat()
            out.append(row)
    return out


def _parse_entry_guide_latlon(entry: dict | None) -> list[tuple[float, float]]:
    if not isinstance(entry, dict):
        return []
    out: list[tuple[float, float]] = []
    for p in entry.get("polyline") or []:
        try:
            if isinstance(p, dict):
                out.append((float(p["lat"]), float(p["lon"])))
            elif isinstance(p, (list, tuple)) and len(p) >= 2:
                out.append((float(p[0]), float(p[1])))
        except (TypeError, ValueError, KeyError):
            continue
    if len(out) >= 2:
        return out
    try:
        from routes import survey_service
        start = survey_service._entry_pin_latlon(entry, "start")
        end = survey_service._entry_pin_latlon(entry, "end")
        if start and end:
            return [start, end]
    except Exception:
        pass
    return out


def _display_road_parts(
    entry: dict | None,
    trail_ll: list[tuple[float, float]],
) -> list[list[tuple[float, float]]]:
    """Nearby GIS LineString parts (lat,lon) for display snapping."""
    from routes import survey_service

    guide = _parse_entry_guide_latlon(entry)
    probes = list(trail_ll)
    if guide:
        try:
            probes.extend(survey_service._sample_polyline(guide, step_km=0.35))
        except Exception:
            probes.extend(guide[:: max(1, len(guide) // 40)])
    if not probes:
        return []

    min_lat = min(p[0] for p in probes) - DISPLAY_ROAD_PAD_KM / 111.0
    max_lat = max(p[0] for p in probes) + DISPLAY_ROAD_PAD_KM / 111.0
    # lon pad scales with latitude
    import math
    cos_m = max(0.2, math.cos(math.radians((min_lat + max_lat) * 0.5)))
    pad_lon = (DISPLAY_ROAD_PAD_KM / 111.0) / cos_m
    min_lon = min(p[1] for p in probes) - pad_lon
    max_lon = max(p[1] for p in probes) + pad_lon

    parts: list[list[tuple[float, float]]] = []
    seen: set[str] = set()

    def _add_geom(geom, key: str):
        if key in seen or len(parts) >= DISPLAY_MAX_PARTS:
            return
        seen.add(key)
        for pi, part in enumerate(survey_service._line_coord_parts(geom)):
            coords: list[tuple[float, float]] = []
            for xy in part:
                try:
                    lon, lat = float(xy[0]), float(xy[1])
                except (TypeError, ValueError, IndexError):
                    continue
                coords.append((lat, lon))
            if len(coords) >= 2:
                parts.append(coords)

    # Prefer assigned segment geometries first
    try:
        ids = list(survey_service._entry_segment_ids(entry or {}))
        id_set = set(ids)
        for state_key, district_id in survey_service._assignment_scopes(entry or {}):
            for feat in survey_service._segment_features(state_key, district_id):
                props = feat.get("properties") or {}
                sid = survey_service._segment_id(props, district_id)
                if sid not in id_set:
                    continue
                _add_geom(feat.get("geometry"), f"asg:{sid}")
    except Exception:
        pass

    # Nearby district roads around the trail / corridor (real carriageway shape)
    try:
        scopes = list(survey_service._assignment_scopes(entry or {}))
        if not scopes and isinstance(entry, dict):
            sk = entry.get("state_key")
            did = str(entry.get("district_id") or "")
            if sk and did:
                scopes = [(sk, did)]
        for state_key, district_id in scopes:
            for feat in survey_service._segment_features(state_key, district_id):
                if len(parts) >= DISPLAY_MAX_PARTS:
                    break
                props = feat.get("properties") or {}
                sid = survey_service._segment_id(props, district_id)
                mid = survey_service._geom_midpoint(feat.get("geometry"))
                if not mid:
                    continue
                mlat, mlon = float(mid[0]), float(mid[1])
                if mlat < min_lat or mlat > max_lat or mlon < min_lon or mlon > max_lon:
                    continue
                # Must sit near a probe (trail or corridor)
                near = False
                for plat, plon in probes:
                    if _haversine_km(mlat, mlon, plat, plon) <= DISPLAY_ROAD_PAD_KM:
                        near = True
                        break
                if not near:
                    continue
                _add_geom(feat.get("geometry"), f"near:{sid}")
            if len(parts) >= DISPLAY_MAX_PARTS:
                break
    except Exception:
        pass

    # Corridor polyline only as last-resort guide when GIS near-roads are scarce
    # (otherwise the chordy OSRM/preview poly steals snaps and reintroduces cuts).
    if guide and len(guide) >= 2 and len(parts) < 12:
        parts.append(guide)
    return parts


def _project_onto_parts(
    lat: float,
    lon: float,
    parts: list[list[tuple[float, float]]],
    *,
    max_km: float = DISPLAY_SNAP_KM,
    prefer_part_idx: int | None = None,
) -> tuple[int, int, float, float, float] | None:
    """Nearest projection: (part_idx, seg_idx, t, snap_lat, snap_lon) within max_km.

    When ``prefer_part_idx`` is set (previous GPS snap), stay on that carriageway
    unless another road is meaningfully closer — stops cyan from hopping onto the
    chordy assignment guide mid-run.
    """
    import math

    def _best_on(indices: list[int]):
        best = None  # (dist, part_i, seg_i, t, slat, slon)
        for pi in indices:
            if pi < 0 or pi >= len(parts):
                continue
            part = parts[pi]
            for si in range(len(part) - 1):
                a_lat, a_lon = part[si]
                b_lat, b_lon = part[si + 1]
                mid = math.radians((a_lat + b_lat) * 0.5)
                cos_m = max(0.2, math.cos(mid))
                ax, ay = a_lon * cos_m, a_lat
                bx, by = b_lon * cos_m, b_lat
                px, py = lon * cos_m, lat
                dx, dy = bx - ax, by - ay
                len2 = dx * dx + dy * dy
                if len2 < 1e-18:
                    t = 0.0
                    d = _haversine_km(lat, lon, a_lat, a_lon)
                    slat, slon = a_lat, a_lon
                else:
                    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len2))
                    qx, qy = ax + t * dx, ay + t * dy
                    d = math.hypot(px - qx, py - qy) * 111.0
                    slat = a_lat + t * (b_lat - a_lat)
                    slon = a_lon + t * (b_lon - a_lon)
                if d > max_km:
                    continue
                if best is None or d < best[0]:
                    best = (d, pi, si, t, slat, slon)
        return best

    global_best = _best_on(list(range(len(parts))))
    if prefer_part_idx is not None:
        preferred = _best_on([prefer_part_idx])
        if preferred is not None:
            if global_best is None:
                chosen = preferred
            else:
                # Stick to previous road unless another is clearly closer (~50 m+)
                if preferred[0] <= global_best[0] + 0.05 or preferred[0] <= global_best[0] * 1.4:
                    chosen = preferred
                else:
                    chosen = global_best
            return chosen[1], chosen[2], chosen[3], chosen[4], chosen[5]
    if global_best is None:
        return None
    return global_best[1], global_best[2], global_best[3], global_best[4], global_best[5]


def _walk_part(
    part: list[tuple[float, float]],
    a_seg: int,
    a_t: float,
    b_seg: int,
    b_t: float,
) -> list[tuple[float, float]]:
    """Vertices along ``part`` from projection A to B (inclusive)."""

    def _pt(seg: int, t: float) -> tuple[float, float]:
        a_lat, a_lon = part[seg]
        b_lat, b_lon = part[seg + 1]
        return (a_lat + t * (b_lat - a_lat), a_lon + t * (b_lon - a_lon))

    start = _pt(a_seg, a_t)
    end = _pt(b_seg, b_t)
    if a_seg == b_seg:
        return [start, end]

    forward = (a_seg, a_t) <= (b_seg, b_t)
    out = [start]
    if forward:
        if a_t < 0.999:
            out.append(part[a_seg + 1])
        for i in range(a_seg + 1, b_seg):
            out.append(part[i + 1])
        out.append(end)
    else:
        if a_t > 0.001:
            out.append(part[a_seg])
        for i in range(a_seg - 1, b_seg - 1, -1):
            out.append(part[i])
        out.append(end)
    return out


def _dedupe_display_latlon(latlon: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not latlon:
        return []
    min_km = DISPLAY_MIN_VERTEX_M / 1000.0
    out = [latlon[0]]
    for p in latlon[1:]:
        if _haversine_km(out[-1][0], out[-1][1], p[0], p[1]) >= min_km:
            out.append(p)
    if latlon[-1] != out[-1]:
        out.append(latlon[-1])
    return out


def road_hugging_display_latlon(
    latlon: list[tuple[float, float]],
    *,
    entry: dict | None = None,
) -> list[tuple[float, float]]:
    """Project GPS samples onto nearby roads and walk carriageway vertices between them.

    Display only — callers must not use the result for covered_km / seal.
    Falls back to the input trail when GIS parts are unavailable or snaps fail.
    """
    if len(latlon) < 2 or not isinstance(entry, dict):
        return list(latlon)

    parts = _display_road_parts(entry, latlon)
    if not parts:
        return list(latlon)

    out: list[tuple[float, float]] = []
    prev = None  # (part_i, seg_i, t, lat, lon)
    for i, raw in enumerate(latlon):
        prefer = prev[0] if prev is not None else None
        snap = _project_onto_parts(raw[0], raw[1], parts, prefer_part_idx=prefer)
        if snap is None:
            # Keep raw GPS so we don't invent a false road; break walk continuity
            if not out or _haversine_km(out[-1][0], out[-1][1], raw[0], raw[1]) >= DISPLAY_MIN_VERTEX_M / 1000.0:
                out.append(raw)
            prev = None
            continue
        pi, si, t, slat, slon = snap
        if prev is None:
            out.append((slat, slon))
            prev = (pi, si, t, slat, slon)
            continue
        p_pi, p_si, p_t, p_lat, p_lon = prev
        if pi == p_pi:
            walked = _walk_part(parts[pi], p_si, p_t, si, t)
            for w in walked[1:]:
                if not out or _haversine_km(out[-1][0], out[-1][1], w[0], w[1]) >= DISPLAY_MIN_VERTEX_M / 1000.0:
                    out.append(w)
        else:
            # Different roads: try walking the densest shared nearby part via fresh
            # projections; else join snap-to-snap (short connector only).
            if _haversine_km(out[-1][0], out[-1][1], slat, slon) >= DISPLAY_MIN_VERTEX_M / 1000.0:
                out.append((slat, slon))
        prev = (pi, si, t, slat, slon)

    hugged = _dedupe_display_latlon(out)
    return hugged if len(hugged) >= 2 else list(latlon)


def _trail_dicts_from_latlon(clipped: list[tuple[float, float]]) -> list[dict]:
    return [{"lat": a, "lon": b} for a, b in clipped]


def recompute_driven_trail_coverage(
    user_id: int,
    date: str,
    trail: list,
    *,
    entry: dict | None = None,
) -> tuple[float, dict]:
    """GPS haversine on start→end clip — alternate paths count (may exceed assigned km).

    End is not frozen at first closest approach: turnaround fill-in after the end
    pin still counts (lock_end=False). Corridor+0.35 cap limits post-end wander.
    """
    from routes import survey_service

    if not trail:
        return 0.0, _empty_by_class()
    if entry is None:
        st = survey_service._load_state()
        entry = (
            (st.get("daily_assignments", {}).get(date, {}) or {}).get(
                survey_service._user_day_key(int(user_id))
            )
            or {}
        )
    start_pins, end_pins = survey_service._effective_progress_pins(entry)
    if start_pins and end_pins:
        points = _clip_trail_dicts_with_ts(
            trail, start_pins, end_pins, lock_end=False,
        )
    else:
        points = _trail_dicts_to_latlon_ts(trail)
    runs = _prepare_trail_runs_for_distance(points)
    if not runs:
        return 0.0, _empty_by_class()
    by_class = _empty_by_class()
    total = 0.0
    for run in runs:
        prev = None
        for lat, lon, _ts in run:
            if prev is not None:
                delta = _haversine_km(prev[0], prev[1], lat, lon)
                if 0 < delta < TRAIL_JUMP_KM:
                    mid_lat = (prev[0] + lat) / 2
                    mid_lon = (prev[1] + lon) / 2
                    rc = _nearest_road_class(
                        int(user_id), date, mid_lat, mid_lon, include_completed=True,
                    ) or "other"
                    total += delta
                    key = rc if rc in by_class else "other"
                    by_class[key] = float(by_class.get(key) or 0) + delta
            prev = (lat, lon)
    return round(total, 3), {k: round(float(by_class.get(k) or 0), 3) for k in _empty_by_class()}


def _normalize_covered_pair(covered_km: float, covered_by_class: dict | None) -> tuple[float, dict]:
    """One total + one by_class whose parts sum to that total."""
    km = round(float(covered_km or 0), 2)
    cbc = {
        k: round(float((covered_by_class or {}).get(k) or 0), 3)
        for k in _empty_by_class()
    }
    class_sum = sum(cbc.values())
    if km <= 0.01:
        return 0.0, _empty_by_class()
    if class_sum < 0.01:
        cbc = {**_empty_by_class(), "other": km}
    elif abs(class_sum - km) > 0.08:
        cbc = _scale_by_class_to_total(cbc, km)
    return km, cbc


def persist_coverage_canonical(
    user_id: int,
    date: str,
    covered_km: float,
    covered_by_class: dict | None = None,
) -> tuple[float, dict]:
    """
    Single write path: identical covered_km + covered_by_class into
    tracking_sessions AND survey_daily_assignments (+ assignment JSON mirror).

    All UIs (admin, VG dashboard, Tracking) must read values produced here —
    no separate juggling between the two tables.
    """
    import db_utils
    from routes import survey_service

    uid = int(user_id)
    day = str(date)[:10]
    km, cbc = _normalize_covered_pair(covered_km, covered_by_class)

    if db_utils.is_db_configured():
        try:
            # One connection for both tables — avoids 2× pool pressure under Waitress.
            if not db_utils.coverage_db_persist_both(uid, day, km, cbc):
                db_utils.tracking_db_update_covered(uid, day, km, cbc)
                db_utils.survey_db_update_covered(uid, day, km, cbc)
        except Exception:
            pass

    try:
        survey_service.apply_covered_km_mirror(uid, day, km, cbc)
    except Exception:
        pass

    # Endpoint + enough cover ⇒ keep assignment marked completed (UI / next-route gate).
    try:
        survey_service.heal_assignment_completion_from_coverage(uid, day, covered_km=km)
    except Exception:
        pass

    return km, cbc


def repair_all_coverage(*, user_id: int | None = None) -> dict:
    """Recompute + canonically persist coverage for every tracking/assignment day."""
    import db_utils

    pairs: set[tuple[int, str]] = set()
    try:
        if db_utils.is_db_configured():
            pairs.update(db_utils.tracking_db_list_user_dates() or [])
            pairs.update(db_utils.survey_db_list_user_dates() or [])
    except Exception:
        pass

    if user_id is not None:
        pairs = {(uid, d) for uid, d in pairs if int(uid) == int(user_id)}

    updated = 0
    errors = 0
    samples: list[dict] = []
    for uid, day in sorted(pairs):
        try:
            km, cbc, _ = resolve_videographer_coverage(
                int(uid),
                day,
                resolve_carryover=False,
                persist=True,
            )
            updated += 1
            if len(samples) < 20:
                samples.append({
                    "user_id": int(uid),
                    "date": day,
                    "covered_km": km,
                    "covered_by_class": cbc,
                })
        except Exception:
            errors += 1
    return {
        "pairs": len(pairs),
        "updated": updated,
        "errors": errors,
        "samples": samples,
    }


