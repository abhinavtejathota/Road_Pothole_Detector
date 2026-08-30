"""survey.progress — extends geometry (includes private _names)."""
from __future__ import annotations

import routes.survey.geometry as _geometry

globals().update({k: v for k, v in vars(_geometry).items() if not k.startswith('__')})

# ── Assignments (per user_id) ─────────────────────────────────────────────────

def _user_day_key(user_id: int) -> str:
    return f"u{int(user_id)}"


def resolve_work_date_for_user(user_id: int, date: str | None = None) -> str:
    """When asking for today, surface the oldest incomplete carryover day if today is empty."""
    date = date or today_ist()
    today = today_ist()
    if date != today:
        return date
    open_asg = find_open_incomplete_assignment(user_id)
    if not open_asg or not open_asg.get("date") or open_asg["date"] >= today:
        return date
    state = _load_state()
    today_entry = (state.get("daily_assignments", {}).get(today, {}) or {}).get(_user_day_key(user_id))
    if isinstance(today_entry, dict) and (
        (today_entry.get("segment_ids") or [])
        or (
            str(today_entry.get("mode") or "").lower() == "auto_track"
            and today_entry.get("start")
            and today_entry.get("end")
        )
    ):
        return date
    return open_asg["date"]


def assignments_for_user(
    user_id: int,
    date: str | None = None,
    *,
    resolve_carryover: bool = True,
    light: bool = False,
) -> list[dict]:
    date = resolve_work_date_for_user(user_id, date) if resolve_carryover else (date or today_ist())
    state = _load_state()
    entry = (state.get("daily_assignments", {}).get(date, {}) or {}).get(_user_day_key(user_id))
    if not entry:
        return []
    if isinstance(entry, list):
        # legacy AC-keyed list — ignore
        return []

    ids = list(entry.get("segment_ids") or [])
    status_map = state.get("segment_status", {})
    if not ids:
        return []

    # Dashboard / mobile: skip district GeoJSON scan (can be 10s+ cold).
    if light:
        did = str(entry.get("district_id") or "")
        dname = entry.get("district_name") or ""
        sk = entry.get("state_key")
        return [
            {
                "id": sid,
                "segment_id": sid,
                "district_id": did,
                "district_name": dname,
                "state_key": sk,
                "name": "",
                "ref": "",
                "road_class": "other",
                "length_km": 0.0,
                "status": status_map.get(sid, "assigned"),
                "assignment_date": date,
            }
            for sid in ids
        ]

    remaining = set(ids)
    by_id = {}
    for state_key, district_id in _assignment_scopes(entry):
        for feat in _segment_features(state_key, district_id):
            props = dict(feat.get("properties") or {})
            sid = _segment_id(props, district_id)
            if sid not in remaining:
                continue
            by_id[sid] = {
                "props": props,
                "geometry": feat.get("geometry"),
                "district_id": district_id,
                "state_key": state_key,
            }
            remaining.discard(sid)
            if not remaining:
                break
        if not remaining:
            break

    rows = []
    for sid in ids:
        hit = by_id.get(sid)
        if not hit:
            continue
        props = hit["props"]
        did = hit["district_id"]
        rows.append({
            "id": sid,
            "segment_id": sid,
            "district_id": did,
            "district_name": props.get("district_name") or entry.get("district_name") or "",
            "state_key": hit["state_key"],
            "name": props.get("name") or props.get("ref") or "",
            "ref": props.get("ref") or "",
            "road_class": props.get("road_class", "other"),
            "length_km": round(float(props.get("length_km") or 0), 3),
            "status": status_map.get(sid, "assigned"),
            "assignment_date": date,
        })
    return rows


def assigned_segments_geojson_for_user(
    user_id: int,
    date: str | None = None,
    *,
    resolve_carryover: bool = True,
) -> dict:
    date = resolve_work_date_for_user(user_id, date) if resolve_carryover else (date or today_ist())
    state = _load_state()
    entry = (state.get("daily_assignments", {}).get(date, {}) or {}).get(_user_day_key(user_id))
    if not entry or isinstance(entry, list):
        return {"type": "FeatureCollection", "features": []}
    ids = list(_entry_segment_ids(entry))
    id_set = set(ids)
    features = []

    # Custom (auto_track) routes: pins + grey GPS overlay (no indigo corridor).
    if str(entry.get("mode") or "").lower() == "auto_track":
        try:
            from routes import tracking_service as _ts
            trail = _ts.trail_for_user(int(user_id), date)
            for cf in _ts.covered_trail_features(trail, entry=entry):
                props = dict(cf.get("properties") or {})
                props["trail_overlay"] = True
                features.append({**cf, "properties": props})
        except Exception:
            pass
        _append_endpoint_markers(features, entry)
        return {"type": "FeatureCollection", "features": features}

    # Continuous OSRM / preview polyline first (fills visual gaps between snapped segments)
    poly = entry.get("polyline") or []
    continuous_ok = False
    if isinstance(poly, list) and len(poly) >= 2:
        coords = []
        for p in poly:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                try:
                    lat, lon = float(p[0]), float(p[1])
                    coords.append([lon, lat])
                except (TypeError, ValueError):
                    continue
        if len(coords) >= 2:
            continuous_ok = True
            poly_km = _polyline_length_km([(c[1], c[0]) for c in coords])
            route_kind = "auto_track" if str(entry.get("mode") or "").lower() == "auto_track" else "continuous"
            features.append({
                "type": "Feature",
                "properties": {
                    "id": "assignment_polyline",
                    "segment_id": "assignment_polyline",
                    "status": "assigned",
                    "road_class": "nh",
                    "route_kind": route_kind,
                    "length_km": round(poly_km or float(entry.get("route_km") or 0) or 0, 3),
                },
                "geometry": {"type": "LineString", "coordinates": coords},
            })

    # Older DB rows dropped polyline — do NOT call OSRM on the read path
    # (that blocked every mobile dashboard load for many seconds). Approximate
    # with start→end so the map still draws immediately.
    if not continuous_ok:
        start = entry.get("start") or {}
        end = entry.get("end") or {}
        try:
            slat, slon = float(start.get("lat")), float(start.get("lon"))
            elat, elon = float(end.get("lat")), float(end.get("lon"))
        except (TypeError, ValueError):
            slat = slon = elat = elon = None
        if slat is not None and elat is not None:
            latlon = [(slat, slon), (elat, elon)]
            coords = [[lon, lat] for lat, lon in latlon]
            continuous_ok = True
            pk = _polyline_length_km(latlon)
            features.append({
                "type": "Feature",
                "properties": {
                    "id": "assignment_polyline",
                    "segment_id": "assignment_polyline",
                    "status": "assigned",
                    "road_class": "nh",
                    "route_kind": "continuous",
                    "length_km": round(pk, 3),
                    "approx": True,
                },
                "geometry": {"type": "LineString", "coordinates": coords},
            })

    # Continuous indigo corridor + grey = GPS trail start→end (what they drove).
    # Do not paint sealed OSM as grey — that blobs roads they never drove when
    # they took an alternate path (and looks like broken dots on the corridor).
    if continuous_ok:
        try:
            from routes import tracking_service as _ts
            trail = _ts.trail_for_user(int(user_id), date)
            for cf in _ts.covered_trail_features(trail, entry=entry):
                props = dict(cf.get("properties") or {})
                props["trail_overlay"] = True
                features.append({**cf, "properties": props})
        except Exception:
            pass
        _append_endpoint_markers(features, entry)
        return {"type": "FeatureCollection", "features": features}

    for state_key, district_id in _assignment_scopes(entry):
        for feat in _segment_features(state_key, district_id):
            props = dict(feat.get("properties") or {})
            sid = _segment_id(props, district_id)
            if sid not in id_set:
                continue
            st = (state.get("segment_status") or {}).get(sid) or (state.get("segment_status") or {}).get(str(sid)) or "assigned"
            props["id"] = sid
            props["segment_id"] = sid
            props["district_id"] = district_id
            props["state_key"] = state_key
            props["status"] = st
            props["route_kind"] = "segment"
            props["length_km"] = round(float(props.get("length_km") or 0), 3)
            features.append({
                "type": "Feature",
                "properties": props,
                "geometry": feat.get("geometry"),
            })
    # Unsealed corridor: thin GPS trail only (clipped start→end)
    try:
        from routes import tracking_service as _ts
        trail = _ts.trail_for_user(int(user_id), date)
        for cf in _ts.covered_trail_features(trail, entry=entry):
            props = dict(cf.get("properties") or {})
            props["trail_overlay"] = True
            features.append({**cf, "properties": props})
    except Exception:
        pass
    _append_endpoint_markers(features, entry)
    return {"type": "FeatureCollection", "features": features}


def _append_endpoint_markers(features: list, entry: dict) -> None:
    """Add Start/End Point features so mobile maps show the two custom-route pins."""
    start = entry.get("start") or {}
    end = entry.get("end") or {}
    for key, label, color in (
        ("start", start.get("label") or "Start", "#16a34a"),
        ("end", end.get("label") or "End", "#dc2626"),
    ):
        pt = start if key == "start" else end
        try:
            lat, lon = float(pt.get("lat")), float(pt.get("lon"))
        except (TypeError, ValueError):
            continue
        features.append({
            "type": "Feature",
            "properties": {
                "id": f"assignment_{key}",
                "segment_id": f"assignment_{key}",
                "status": "assigned",
                "route_kind": "endpoint",
                "endpoint": key,
                "label": label,
                "marker_color": color,
            },
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
        })


def assignment_summary_for_user(
    user_id: int,
    date: str | None = None,
    *,
    resolve_carryover: bool = True,
    sync_coverage: bool = False,
    light: bool = False,
) -> dict:
    requested = date or today_ist()
    date = resolve_work_date_for_user(user_id, requested) if resolve_carryover else requested
    state = _load_state()
    entry = (state.get("daily_assignments", {}).get(date, {}) or {}).get(_user_day_key(user_id)) or {}
    # Prefer driven polyline length when present (avoids dual-carriageway double count in UI)
    poly_km = _polyline_length_km(entry.get("polyline") if isinstance(entry, dict) else None)
    route_km = float((entry or {}).get("route_km") or 0) if isinstance(entry, dict) else 0.0
    # light / continuous corridor: never scan district GeoJSON for a km total
    if light or route_km > 0.2 or poly_km > 0.2:
        assigned = []
        total_km = route_km if route_km > 0.2 else (poly_km if poly_km > 0.2 else 0.0)
        if total_km <= 0.2 and isinstance(entry, dict):
            total_km = float(entry.get("corridor_km") or 0)
    else:
        assigned = assignments_for_user(user_id, date, resolve_carryover=False)
        total_km = sum(a["length_km"] for a in assigned)
    settings = get_settings()
    target_km = float(settings.get("daily_km", 100))
    covered_km = float(entry.get("covered_km") or 0) if isinstance(entry, dict) else 0.0
    by_class_resolved = None
    # Keep covered km in sync with Tracking — but do NOT persist on every list/history
    # read (that corrupted JSON + exhausted the DB pool under Waitress).
    # Also skip trail recompute on light reads — it pegged a CPU core on every
    # Survey / mobile dashboard load.
    if isinstance(entry, dict) and entry and not light:
        try:
            from routes import tracking_service as _ts
            resolved_km, resolved_by, _ = _ts.resolve_videographer_coverage(
                int(user_id),
                date,
                resolve_carryover=resolve_carryover,
                persist=bool(sync_coverage),
            )
            if resolved_km >= 0 and (
                abs(float(resolved_km) - float(covered_km)) > 0.01
                or resolved_by != (entry.get("covered_by_class") or {})
            ):
                covered_km = resolved_km
                entry = dict(entry)
                entry["covered_km"] = resolved_km
                entry["covered_by_class"] = resolved_by
            if resolved_by and any(float(resolved_by.get(k) or 0) > 0 for k in ("nh", "sh", "mdr", "other")):
                by_class_resolved = resolved_by
            if sync_coverage:
                try:
                    tr = _ts.trail_for_user(int(user_id), date)
                    if tr:
                        backfill_progress_from_trail(int(user_id), date, tr)
                        entry = (state.get("daily_assignments", {}).get(date, {}) or {}).get(
                            _user_day_key(user_id)
                        ) or entry
                        if isinstance(entry, dict) and covered_km <= 0:
                            covered_km = float(entry.get("covered_km") or 0)
                except Exception:
                    pass
        except Exception:
            by_class_resolved = None
    mode = (entry.get("mode") if isinstance(entry, dict) else None) or "corridor"
    seg_ids = _entry_segment_ids(entry) if isinstance(entry, dict) else []
    has_work = bool(assigned or seg_ids or mode == "auto_track")
    incomplete = covered_km < target_km if has_work else True
    by_class = by_class_resolved or (entry.get("covered_by_class") if isinstance(entry, dict) else None) or {
        "nh": 0.0, "sh": 0.0, "mdr": 0.0, "other": 0.0,
    }
    assigned_by_class = {"nh": 0.0, "sh": 0.0, "mdr": 0.0, "other": 0.0}
    for a in assigned:
        rc = a.get("road_class") or "other"
        if rc not in assigned_by_class:
            rc = "other"
        assigned_by_class[rc] += float(a.get("length_km") or 0)
    scopes = _assignment_scopes(entry) if entry else []
    district_ids = [did for _, did in scopes]
    district_names = []
    for sk, did in scopes:
        d = get_district(did, sk)
        if d and d.get("name"):
            district_names.append(d["name"])
        elif entry.get("district_name") and did == str(entry.get("district_id")):
            district_names.append(entry["district_name"])

    if light:
        probe = {
            **entry,
            "user_id": int(user_id),
            "assignment_date": date,
            "date": date,
        } if isinstance(entry, dict) and entry else None
        is_complete = bool(is_assignment_complete(probe)) if probe else False
        change_gate = can_change_or_replace_assignment(user_id, date=date)
        open_block = find_open_incomplete_assignment(user_id)
        today = today_ist()
        carryover = None
        is_carryover = date < today
        if open_block and open_block.get("date") and open_block["date"] < today:
            carryover = open_block
            is_carryover = True
        return {
            "user_id": int(user_id),
            "date": date,
            "requested_date": requested,
            "assignment_date": date,
            "is_carryover": is_carryover,
            "mode": mode,
            "total_km": round(total_km, 1),
            "route_km": round(route_km or total_km, 1),
            "covered_km": round(covered_km, 1),
            "covered_by_class": by_class,
            "assigned_by_class": assigned_by_class,
            "segment_count": len(seg_ids),
            "target_km": target_km,
            "quota_incomplete": incomplete,
            "quota_complete": (not incomplete) and has_work,
            "completed": is_complete,
            "assignment_complete": is_complete,
            "progress_level": change_gate.get("progress_level") or "none",
            "can_assign": open_block is None,
            "can_change_route": bool(change_gate.get("can_change_route")),
            "change_route_message": change_gate.get("message") or "",
            "open_assignment": open_block,
            "carryover": carryover,
            "start": entry.get("start") if isinstance(entry, dict) else None,
            "end": entry.get("end") if isinstance(entry, dict) else None,
            "district_id": entry.get("district_id") if isinstance(entry, dict) else None,
            "district_name": entry.get("district_name") if isinstance(entry, dict) else None,
            "district_ids": district_ids,
            "district_names": district_names,
            "state_key": entry.get("state_key") if isinstance(entry, dict) else None,
        }

    open_block = find_open_incomplete_assignment(user_id)
    probe = (
        {**entry, "user_id": int(user_id), "assignment_date": date, "date": date}
        if isinstance(entry, dict) and entry else None
    )
    is_complete = bool(is_assignment_complete(probe)) if probe else False
    change_gate = can_change_or_replace_assignment(user_id, date=date)
    progress = change_gate.get("progress_level") or (
        assignment_progress_level({**entry, "user_id": int(user_id), "assignment_date": date, "date": date})
        if isinstance(entry, dict) and entry else "none"
    )
    today = today_ist()
    carryover = None
    is_carryover = date < today
    if open_block and open_block.get("date") and open_block["date"] < today:
        carryover = open_block
        is_carryover = True
    can_assign = open_block is None
    return {
        "date": date,
        "requested_date": requested,
        "assignment_date": date,
        "is_carryover": is_carryover,
        "user_id": int(user_id),
        "district_id": entry.get("district_id") if isinstance(entry, dict) else None,
        "district_name": entry.get("district_name") if isinstance(entry, dict) else None,
        "district_ids": district_ids,
        "district_names": district_names,
        "legs": entry.get("legs") if isinstance(entry, dict) else [],
        "state_key": entry.get("state_key") if isinstance(entry, dict) else None,
        "start": entry.get("start") if isinstance(entry, dict) else None,
        "end": entry.get("end") if isinstance(entry, dict) else None,
        "mode": mode if isinstance(entry, dict) and entry else None,
        "segment_count": len(seg_ids) if seg_ids else len(assigned),
        "total_km": round(total_km, 1),
        "target_km": target_km,
        "covered_km": round(covered_km, 1),
        "covered_by_class": {k: round(float(by_class.get(k) or 0), 2) for k in assigned_by_class},
        "assigned_by_class": {k: round(v, 2) for k, v in assigned_by_class.items()},
        "quota_complete": (not incomplete) and bool(assigned or mode == "auto_track"),
        "quota_incomplete": incomplete and bool(assigned or mode == "auto_track"),
        "assignment_complete": is_complete,
        "completed": is_complete,
        "progress_level": progress,
        "can_assign": can_assign,
        "can_change_route": bool(change_gate.get("can_change_route")),
        "change_route_message": change_gate.get("message") or "",
        "open_assignment": open_block,
        "carryover": carryover,
        "over_target": total_km > target_km,
        "over_by_km": round(total_km - target_km, 1) if total_km > target_km else 0,
    }


ASSIGN_COMPLETE_REMAINING_KM = 1.0  # assigned-corridor backup / seal gate (≤1 km remaining)
# Endpoint complete = start→end on any path (cover km is optional).
# Enough-cover below only gates sealing *assigned* OSM / corridor backup unlock.
ASSIGN_COMPLETE_MIN_FRAC = 0.55  # ≥55% of assigned route (alternate shorter path)
ASSIGN_COMPLETE_MIN_KM = 2.0     # and at least this much on longer routes
# Change-route allowed while progress is still light (not started, or only a little GPS).
CHANGE_ROUTE_LIGHT_KM = 3.0
CHANGE_ROUTE_LIGHT_FRAC = 0.05
# Start pin: must visit corridor start before quota / complete can unlock.
START_PIN_REACH_KM = 0.25
# End pin (incl. alternate/cyan paths on a parallel road): 250 m buffer.
END_PIN_REACH_KM = 0.25
# Back-compat alias (prefer START_/END_ explicitly at call sites).
PIN_REACH_KM = END_PIN_REACH_KM


def _entry_pin_latlon(entry: dict | None, key: str) -> tuple[float, float] | None:
    if not isinstance(entry, dict):
        return None
    pin = entry.get(key) or {}
    try:
        lat, lon = float(pin.get("lat")), float(pin.get("lon"))
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def _corridor_endpoint_pins(entry: dict | None) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    """First/last vertex of the assigned OSRM polyline (fallback when UI pins are wrong)."""
    if not isinstance(entry, dict):
        return None, None
    poly = entry.get("polyline") or []
    if not isinstance(poly, list) or len(poly) < 2:
        return None, None
    try:
        a, b = poly[0], poly[-1]
        s = (float(a[0]), float(a[1]))
        e = (float(b[0]), float(b[1]))
    except (TypeError, ValueError, IndexError):
        return None, None
    if not (-90 <= s[0] <= 90 and -180 <= s[1] <= 180):
        s = None
    if not (-90 <= e[0] <= 90 and -180 <= e[1] <= 180):
        e = None
    return s, e


def _enough_cover_for_complete(entry: dict | None, covered: float | None = None) -> bool:
    """True when covered km is enough to treat the assignment as finished.

    Prevents false completes from merely brushing start + end pins while
    covered_km is still ~0.8 of a 5 km corridor.
    """
    if not isinstance(entry, dict):
        return False
    try:
        route = float(entry.get("route_km") or entry.get("corridor_km") or 0)
    except (TypeError, ValueError):
        route = 0.0
    try:
        cov = float(covered if covered is not None else entry.get("covered_km") or 0)
    except (TypeError, ValueError):
        cov = 0.0
    if route < 0.5:
        return cov >= 0.05 or bool(entry.get("start") and entry.get("end"))
    if cov >= max(0.0, route - ASSIGN_COMPLETE_REMAINING_KM):
        return True
    return cov >= max(ASSIGN_COMPLETE_MIN_KM, ASSIGN_COMPLETE_MIN_FRAC * route)


def _effective_progress_pins(
    entry: dict | None,
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Pins that count for start→end complete / grey / seal.

    Rule: any driven path is fine. Complete when GPS hits a start pin, then later
    comes within END_PIN_REACH_KM (~0.25 km) of an end pin. End accepts the labeled
    destination OR the corridor polyline end (alternate/parallel roads count).
    Start prefers the corridor start; a labeled "My location" start near the
    destination is ignored so it cannot block or false-complete the route.
    """
    if not isinstance(entry, dict):
        return [], []
    labeled_start = _entry_pin_latlon(entry, "start")
    labeled_end = _entry_pin_latlon(entry, "end")
    c_start, c_end = _corridor_endpoint_pins(entry)
    start_pins: list[tuple[float, float]] = []
    end_pins: list[tuple[float, float]] = []

    if c_start:
        start_pins.append(c_start)
    if labeled_start:
        if c_start and c_end:
            d_s = _haversine_km(labeled_start[0], labeled_start[1], c_start[0], c_start[1])
            d_e = _haversine_km(labeled_start[0], labeled_start[1], c_end[0], c_end[1])
            # Keep labeled start only when it sits near the corridor beginning
            if d_s <= d_e + 0.05:
                start_pins.append(labeled_start)
        else:
            start_pins.append(labeled_start)

    if labeled_end:
        end_pins.append(labeled_end)
    if c_end:
        end_pins.append(c_end)

    # Dedupe near-identical pins
    def _uniq(pins: list[tuple[float, float]]) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for p in pins:
            if any(_haversine_km(p[0], p[1], q[0], q[1]) < 0.04 for q in out):
                continue
            out.append(p)
        return out

    return _uniq(start_pins), _uniq(end_pins)


def _point_near(lat: float, lon: float, pin: tuple[float, float] | None, radius_km: float = PIN_REACH_KM) -> bool:
    if not pin:
        return False
    return _haversine_km(lat, lon, pin[0], pin[1]) <= radius_km


def _near_any_pin(lat: float, lon: float, *pins: tuple[float, float] | None, radius_km: float = PIN_REACH_KM) -> bool:
    return any(_point_near(lat, lon, p, radius_km) for p in pins if p)


def _trail_latlon_points(trail: list | None) -> list[tuple[float, float]]:
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


def _truncate_trail_start_to_end(
    trail: list | None,
    start: tuple[float, float] | None,
    end: tuple[float, float] | None,
    *,
    radius_km: float | None = None,
    start_radius_km: float | None = None,
    end_radius_km: float | None = None,
) -> list[tuple[float, float]]:
    """GPS from first start visit through first near-end point (no post-end wander)."""
    starts = [start] if start else []
    ends = [end] if end else []
    return _truncate_trail_to_progress_pins(
        trail,
        starts,
        ends,
        radius_km=radius_km,
        start_radius_km=start_radius_km,
        end_radius_km=end_radius_km,
    )


def _truncate_trail_to_progress_pins(
    trail: list | None,
    start_pins: list[tuple[float, float]] | None,
    end_pins: list[tuple[float, float]] | None,
    *,
    radius_km: float | None = None,
    start_radius_km: float | None = None,
    end_radius_km: float | None = None,
    lock_end: bool = True,
) -> list[tuple[float, float]]:
    """Clip trail start→end by geometry (not meta.endpoint_reached).

    After the first start-pin visit, when ``lock_end`` is True (seal / final grey),
    stop at the sample whose distance to any end pin is the **minimum** (closest
    approach within ``end_radius_km``). That drops post-end parking wander.

    When ``lock_end`` is False (live tracking / incomplete day), return the full
    trail from start onward immediately (no closest-end scan) so a VG who reaches
    the end, turns around, and fills a missed corridor section still has those
    points counted.
    """
    pts = _trail_latlon_points(trail)
    starts = [p for p in (start_pins or []) if p]
    ends = [p for p in (end_pins or []) if p]
    if not pts or not starts or not ends:
        return []
    start_r = (
        float(start_radius_km)
        if start_radius_km is not None
        else float(radius_km)
        if radius_km is not None
        else START_PIN_REACH_KM
    )
    end_r = (
        float(end_radius_km)
        if end_radius_km is not None
        else float(radius_km)
        if radius_km is not None
        else END_PIN_REACH_KM
    )
    si = next(
        (i for i, (la, lo) in enumerate(pts) if _near_any_pin(la, lo, *starts, radius_km=start_r)),
        None,
    )
    if si is None:
        return []

    # Live / re-expandable coverage: keep full trail from start. Skip the O(n)
    # closest-end scan — both "near end" and "not yet" return the same slice.
    if not lock_end:
        return pts[si:]

    # Closest approach to any end pin after start (argmin distance)
    best_i = None
    best_d = None
    for i in range(si, len(pts)):
        la, lo = pts[i]
        d = min(_haversine_km(la, lo, e[0], e[1]) for e in ends)
        if best_d is None or d < best_d:
            best_d = d
            best_i = i

    if best_i is None or best_d is None or best_d > end_r:
        # Never got near enough — keep open from start (in progress)
        return pts[si:]
    return pts[si : best_i + 1]


def _already_covered_stats(
    segment_ids: list[str],
    sid_district: dict,
    status_map: dict,
) -> dict:
    """How much of a candidate route is already sealed by anyone."""
    total = 0.0
    done = 0.0
    for sid in segment_ids or []:
        sid = str(sid)
        did = str((sid_district or {}).get(sid) or "")
        sk = None
        length = 0.0
        for state_key in ("andhra", "telangana"):
            if not did:
                break
            m = (_segment_meta(state_key, did).get(sid) or {})
            if m:
                sk = state_key
                length = float(m.get("length") or 0)
                break
        if length <= 0:
            length = 0.05
        total += length
        if status_map.get(sid) in ("completed", "verified"):
            done += length
    pct = round((done / total) * 100.0, 1) if total > 0 else 0.0
    return {
        "already_covered_km": round(done, 2),
        "already_covered_pct": pct,
        "route_network_km": round(total, 2),
    }


def assignment_coverage_flags(user_id: int, date: str | None = None) -> dict:
    """Live flags: must leave start before counting; stop after end."""
    date = date or today_ist()
    state = _load_state()
    entry = (state.get("daily_assignments", {}).get(date, {}) or {}).get(_user_day_key(int(user_id)))
    if not isinstance(entry, dict):
        return {
            "has_assignment": False,
            "start_reached": False,
            "end_reached": False,
            "completed": False,
            "allow_count": False,
        }
    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
    completed = bool(meta.get("completed"))
    start_reached = bool(meta.get("start_reached"))
    end_reached = bool(meta.get("end_reached")) or completed
    return {
        "has_assignment": True,
        "start_reached": start_reached,
        "end_reached": end_reached,
        "completed": completed,
        "allow_count": bool(start_reached and not end_reached and not completed),
    }


def note_start_pin_if_near(user_id: int, date: str, lat: float, lon: float) -> bool:
    """Fast path: mark start_reached so quota can begin (no seal)."""
    date = date or today_ist()
    user_id = int(user_id)
    state = _load_state()
    ukey = _user_day_key(user_id)
    day = state.setdefault("daily_assignments", {}).setdefault(date, {})
    entry = day.get(ukey)
    if not isinstance(entry, dict):
        return False
    meta = dict(entry.get("meta") if isinstance(entry.get("meta"), dict) else {})
    if meta.get("start_reached"):
        return True
    labeled = _entry_pin_latlon(entry, "start")
    start_pins, _end_pins = _effective_progress_pins(entry)
    if not start_pins and labeled:
        start_pins = [labeled]
    if not start_pins or not _near_any_pin(lat, lon, *start_pins, radius_km=START_PIN_REACH_KM):
        return False
    meta["start_reached"] = True
    meta["start_reached_at"] = datetime.now(IST).isoformat()
    entry["meta"] = meta
    day[ukey] = entry
    _save_state(state, persist_db=False)
    return True


def backfill_progress_from_trail(user_id: int, date: str, trail: list | None) -> dict:
    """Replay trail pins so historical drives get start/end/complete without new pings."""
    date = date or today_ist()
    user_id = int(user_id)
    pts = _trail_latlon_points(trail)
    if not pts:
        return {"updated": False}
    out = {"updated": False, "start_reached": False, "end_reached": False, "completed": False}
    for lat, lon in pts:
        res = note_assignment_pin_visit(user_id, date, lat, lon, trail=trail)
        out["updated"] = out["updated"] or bool(res.get("updated"))
        out["start_reached"] = bool(res.get("start_reached"))
        out["end_reached"] = bool(res.get("end_reached"))
        out["completed"] = bool(res.get("completed"))
        if out["completed"]:
            break
    return out


def note_assignment_pin_visit(
    user_id: int,
    date: str,
    lat: float,
    lon: float,
    *,
    trail: list | None = None,
) -> dict:
    """Update start/end reached from a GPS fix; complete + seal driven path at endpoint."""
    date = date or today_ist()
    user_id = int(user_id)
    state = _load_state()
    ukey = _user_day_key(user_id)
    day = state.setdefault("daily_assignments", {}).setdefault(date, {})
    entry = day.get(ukey)
    if not isinstance(entry, dict):
        return {"updated": False}

    meta = dict(entry.get("meta") if isinstance(entry.get("meta"), dict) else {})
    start_pins, end_pins = _effective_progress_pins(entry)
    changed = False

    # Primary rule: any path. Hit start, then come within END_PIN_REACH_KM of an
    # end pin (labeled destination or corridor end) → complete + seal + grey.
    if not meta.get("start_reached") and _near_any_pin(
        lat, lon, *start_pins, radius_km=START_PIN_REACH_KM,
    ):
        meta["start_reached"] = True
        meta["start_reached_at"] = datetime.now(IST).isoformat()
        changed = True

    newly_complete = False
    if (
        meta.get("start_reached")
        and not meta.get("end_reached")
        and _near_any_pin(lat, lon, *end_pins, radius_km=END_PIN_REACH_KM)
    ):
        meta["end_reached"] = True
        meta["end_reached_at"] = datetime.now(IST).isoformat()
        changed = True
        # Complete = start→end on any path (own route or assigned). Cover amount
        # is their choice. Enough-cover only gates sealing the *assigned* corridor.
        meta["completed"] = True
        meta["completed_at"] = datetime.now(IST).isoformat()
        meta["complete_reason"] = "endpoint_reached"
        newly_complete = True

    if changed:
        entry["meta"] = meta
        day[ukey] = entry
        _save_state(state, persist_db=False)
        _schedule_survey_db_sync(state, delay_s=2.5)

    sealed = {"count": 0}
    if newly_complete:
        # Seal assigned OSM only when they covered enough of *our* corridor;
        # alternate own-path still completes the assignment (unlock next route)
        # and greys the GPS trail, without painting the whole assigned network.
        try:
            probe = dict(entry)
            probe["meta"] = meta
            if _enough_cover_for_complete(probe, float(entry.get("covered_km") or 0)):
                sealed = seal_driven_path_until_endpoint(user_id, date, trail=trail)
            else:
                sealed = {
                    "count": 0,
                    "skipped": "alternate_path_complete_without_corridor_seal",
                }
        except Exception:
            sealed = {"count": 0, "error": "seal_failed"}

    return {
        "updated": changed,
        "start_reached": bool(meta.get("start_reached")),
        "end_reached": bool(meta.get("end_reached")),
        "completed": bool(meta.get("completed")),
        "sealed": sealed,
    }


# Seal only roads GPS actually traversed (aligned with tracking snap distance).
# Defined here (not assignments) so seal_driven_path_until_endpoint can call them —
# assignments extends progress, so these stay available further down the chain.
SEAL_SNAP_KM = 0.35
SEAL_MIN_COVER_FRAC = 0.35
SEAL_SAMPLE_STEP_KM = 0.08
SEAL_SHORT_SEG_KM = 0.15


def _sample_geom_latlon(geom: dict | None, step_km: float = SEAL_SAMPLE_STEP_KM) -> list[tuple[float, float]]:
    """Sample (lat, lon) points along a LineString / MultiLineString."""
    latlon: list[tuple[float, float]] = []
    for part in _line_coord_parts(geom):
        for lon, lat in part:
            try:
                latlon.append((float(lat), float(lon)))
            except (TypeError, ValueError):
                continue
    if len(latlon) < 2:
        mid = _geom_midpoint(geom)
        return [mid] if mid else latlon
    return _sample_polyline(latlon, step_km=step_km)


def _segment_covered_by_gps(
    geom: dict | None,
    length_km: float,
    gps_pts: list[tuple[float, float]],
    *,
    snap_km: float = SEAL_SNAP_KM,
    min_frac: float = SEAL_MIN_COVER_FRAC,
) -> bool:
    """True when GPS samples cover enough of the segment geometry."""
    if not geom or not gps_pts:
        return False
    samples = _sample_geom_latlon(geom)
    if not samples:
        return False

    # Bbox pad (~snap) to skip distant GPS quickly
    lats = [s[0] for s in samples]
    lons = [s[1] for s in samples]
    pad = snap_km / 100.0 + 0.002  # ~deg fudge
    min_lat, max_lat = min(lats) - pad, max(lats) + pad
    min_lon, max_lon = min(lons) - pad, max(lons) + pad
    nearby = [
        (la, lo) for la, lo in gps_pts
        if min_lat <= la <= max_lat and min_lon <= lo <= max_lon
    ]
    if not nearby:
        return False

    hits = 0
    for slat, slon in samples:
        for glat, glon in nearby:
            if _haversine_km(slat, slon, glat, glon) <= snap_km:
                hits += 1
                break

    if float(length_km or 0) < SEAL_SHORT_SEG_KM:
        return hits >= 1
    return (hits / len(samples)) >= min_frac


def seal_driven_path_until_endpoint(
    user_id: int,
    date: str | None = None,
    *,
    trail: list | None = None,
) -> dict:
    """Seal OSM roads the VG actually drove from start→end (any path).

    Undriven indigo spurs stay unsealed. Travel after the end pin is ignored.
    """
    date = date or today_ist()
    user_id = int(user_id)
    state = _load_state()
    entry = (state.get("daily_assignments", {}).get(date, {}) or {}).get(_user_day_key(user_id))
    if not isinstance(entry, dict):
        return {"updated": False, "count": 0, "message": "No assignment."}

    start_pins, end_pins = _effective_progress_pins(entry)
    if trail is None:
        try:
            from routes import tracking_service as _ts
            trail = _ts.trail_for_user(user_id, date)
        except Exception:
            trail = []
    gps_pts = _truncate_trail_to_progress_pins(
        trail, start_pins, end_pins, lock_end=False,
    )
    if len(gps_pts) < 2:
        return {
            "updated": False,
            "count": 0,
            "gps_points": len(gps_pts),
            "message": "Need GPS from start through end to seal the driven path.",
        }

    status_map = state.setdefault("segment_status", {})
    sealed: list[str] = []
    checked = 0
    for state_key, district_id in _assignment_scopes(entry):
        for feat in _segment_features(state_key, district_id):
            props = feat.get("properties") or {}
            sid = _segment_id(props, district_id)
            if not sid:
                continue
            checked += 1
            if status_map.get(sid) in ("completed", "verified"):
                continue
            length_km = float(props.get("length_km") or 0)
            if not _segment_covered_by_gps(feat.get("geometry"), length_km, gps_pts):
                continue
            status_map[sid] = "completed"
            sealed.append(sid)

    if sealed:
        _save_state(state, persist_db=False)
        _schedule_survey_db_sync(state, delay_s=2.5)
        try:
            from routes import tracking_service as _ts
            _ts._ASSIGN_CLASS_CACHE.clear()
        except Exception:
            pass

    return {
        "updated": bool(sealed),
        "count": len(sealed),
        "checked": checked,
        "gps_points": len(gps_pts),
        "segment_ids": sealed,
        "message": (
            f"Sealed {len(sealed)} road(s) on the path from start to end."
            if sealed
            else "Reached end — no new roads to seal on the driven path."
        ),
    }


def _sealed_segment_count(entry: dict | None) -> int:
    ids = list(_entry_segment_ids(entry or {}))
    if not ids:
        return 0
    status_map = _load_state().get("segment_status", {})
    return sum(1 for sid in ids if status_map.get(sid) in ("completed", "verified"))


def _route_km_for_entry(entry: dict | None) -> float:
    if not isinstance(entry, dict):
        return 0.0
    route_km = float(entry.get("route_km") or 0)
    if route_km < 0.2:
        poly_km = _polyline_length_km(entry.get("polyline") if isinstance(entry.get("polyline"), list) else None)
        if poly_km > 0.2:
            route_km = poly_km
    if route_km < 0.2:
        route_km = float(entry.get("corridor_km") or 0)
    return max(0.0, route_km)


def assignment_progress_level(entry: dict | None) -> str:
    """``none`` | ``light`` | ``mid`` | ``complete`` — drives Change-route gating."""
    if not entry or not isinstance(entry, dict):
        return "none"
    probe = dict(entry)
    if is_assignment_complete(probe):
        return "complete"
    covered = float(entry.get("covered_km") or 0)
    sealed = _sealed_segment_count(entry)
    route_km = _route_km_for_entry(entry)
    light_limit = CHANGE_ROUTE_LIGHT_KM
    if route_km >= 1.0:
        light_limit = max(CHANGE_ROUTE_LIGHT_KM, route_km * CHANGE_ROUTE_LIGHT_FRAC)
    if sealed <= 0 and covered <= light_limit:
        return "none" if covered <= 0.05 and sealed <= 0 else "light"
    return "mid"


def can_change_or_replace_assignment(user_id: int, *, date: str | None = None) -> dict:
    """Whether the VG may replace the active route (not started / only light GPS).

    Applies to today’s assignment or an incomplete prior-day carryover.
    Mid-progress → blocked (“complete the current assignment”).
    """
    user_id = int(user_id)
    today = today_ist()
    date = date or today
    open_asg = find_open_incomplete_assignment(user_id)
    # Prefer the blocking open assignment’s date (carryover) over the requested date
    work_date = date
    if open_asg and open_asg.get("date"):
        work_date = open_asg["date"]

    state = _load_state()
    ukey = _user_day_key(user_id)
    entry = (state.get("daily_assignments", {}).get(work_date, {}) or {}).get(ukey)
    if not isinstance(entry, dict):
        return {
            "ok": True,
            "can_change_route": True,
            "progress_level": "none",
            "open_assignment": None,
            "message": "",
        }

    level = assignment_progress_level({
        **entry, "user_id": user_id, "assignment_date": work_date, "date": work_date,
    })
    if level in ("none", "light"):
        return {
            "ok": True,
            "can_change_route": True,
            "progress_level": level,
            "open_assignment": open_asg if open_asg and open_asg.get("date") == work_date else None,
            "message": "",
            "work_date": work_date,
        }
    if level == "complete":
        return {
            "ok": True,
            "can_change_route": True,
            "progress_level": level,
            "open_assignment": None,
            "message": "",
            "work_date": work_date,
        }
    start = entry.get("start") or {}
    end = entry.get("end") or {}
    day_note = f" from {work_date}" if work_date < today else ""
    return {
        "ok": False,
        "can_change_route": False,
        "progress_level": "mid",
        "open_assignment": open_asg,
        "work_date": work_date,
        "message": (
            f"Complete the current assignment{day_note} before changing route "
            f"({start.get('label') or 'start'} → {end.get('label') or 'end'}). "
            "You are already mid-route — finish Capture + upload, or ask an admin to clear it."
        ),
    }


def _assigned_sealed_and_total_km(entry: dict | None) -> tuple[float, float]:
    """(sealed_km, total_assigned_km) for the entry's segment list."""
    ids = list(_entry_segment_ids(entry or {}))
    if not ids:
        return 0.0, 0.0
    status_map = _load_state().get("segment_status", {})
    total = 0.0
    sealed = 0.0
    scopes = _assignment_scopes(entry or {})
    meta_by_sid: dict[str, float] = {}
    for state_key, district_id in scopes:
        for sid, m in (_segment_meta(state_key, str(district_id)) or {}).items():
            if sid in ids:
                meta_by_sid[sid] = float(m.get("length") or 0)
    for sid in ids:
        length = meta_by_sid.get(str(sid)) or 0.05
        total += length
        if status_map.get(sid) in ("completed", "verified"):
            sealed += length
    return sealed, total


def is_assignment_complete(entry: dict | None) -> bool:
    """True when the VG finished the assignment (unlocks next route).

    Primary: start visited, then within ~PIN_REACH of an end pin on *any* path.
    Cover km is optional (own route may be shorter/longer than assigned).
    Backup: GPS covered or sealed within 1 km of full *assigned* route length.

    Uses stored ``covered_km`` / meta flags only — never re-resolves the GPS
    trail here. Trail recompute belongs on write paths (ping / upload / seal);
    doing it on every Survey/dashboard read pegged CPU and timed out clients.
    """
    if not entry or not isinstance(entry, dict):
        return True
    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
    covered = float(entry.get("covered_km") or 0)
    uid = entry.get("user_id")
    day = entry.get("assignment_date") or entry.get("date")

    # Endpoint rule — any driven path
    if meta.get("completed") is True:
        return True
    if meta.get("end_reached") and meta.get("start_reached"):
        return True

    mode = str(entry.get("mode") or "corridor").lower()
    if mode == "auto_track":
        if uid and day:
            try:
                from routes import auto_track_service
                for up in auto_track_service.list_uploads_for_user(int(uid), str(day)[:10]):
                    if up.get("match_status") == "matched":
                        return True
            except Exception:
                pass
        return False

    route_km = float(entry.get("route_km") or 0)
    if route_km < 0.2:
        poly_km = _polyline_length_km(entry.get("polyline") if isinstance(entry.get("polyline"), list) else None)
        if poly_km > 0.2:
            route_km = poly_km
    # Assigned-corridor backup: nearly full cover/seal of *our* route
    if route_km >= 0.5 and covered >= max(0.0, route_km - ASSIGN_COMPLETE_REMAINING_KM):
        return True

    ids = list(_entry_segment_ids(entry))
    if not ids:
        if entry.get("start") and entry.get("end"):
            return False
        return True
    # Prefer route_km over district GIS seal scan when we already have a length
    if route_km >= 0.5:
        return False
    sealed_km, total_km = _assigned_sealed_and_total_km(entry)
    if total_km >= 0.5 and sealed_km >= max(0.0, total_km - ASSIGN_COMPLETE_REMAINING_KM):
        return True
    return False


def _maybe_mark_assignment_complete(user_id: int, date: str, entry: dict | None = None) -> bool:
    """Persist meta.completed when the within-1km (or endpoint) threshold is reached."""
    date = date or today_ist()
    state = _load_state()
    ukey = _user_day_key(int(user_id))
    day = state.get("daily_assignments", {}).get(date, {}) or {}
    cur = entry if isinstance(entry, dict) else day.get(ukey)
    if not isinstance(cur, dict):
        return False
    probe = dict(cur)
    probe["assignment_date"] = date
    probe["date"] = date
    probe["user_id"] = int(user_id)
    if not is_assignment_complete(probe):
        return False
    meta = cur.get("meta") if isinstance(cur.get("meta"), dict) else {}
    if meta.get("completed") is True:
        return True
    meta = dict(meta)
    meta["completed"] = True
    meta["completed_at"] = datetime.now(IST).isoformat()
    meta["complete_reason"] = "cover_or_seal_threshold"
    # Endpoint was reached in the field — keep flags consistent for UI.
    meta.setdefault("start_reached", True)
    meta.setdefault("end_reached", True)
    cur["meta"] = meta
    day[ukey] = cur
    state.setdefault("daily_assignments", {})[date] = day
    _save_state(state, persist_db=False)
    _schedule_survey_db_sync(state, delay_s=2.5)
    return True


def heal_assignment_completion_from_coverage(
    user_id: int,
    date: str,
    *,
    covered_km: float | None = None,
    trail: list | None = None,
) -> bool:
    """Re-assert completed after coverage sync when the end pin was (or is) reached.

    Coverage recompute can rewrite covered_km without touching meta; light UIs that
    only looked at meta.completed then looked incomplete. Also recovers wiped meta
    when the GPS trail still reaches start→end with enough cover.
    """
    date = str(date or today_ist())[:10]
    uid = int(user_id)
    state = _load_state()
    ukey = _user_day_key(uid)
    day = state.setdefault("daily_assignments", {}).setdefault(date, {})
    entry = day.get(ukey)
    if not isinstance(entry, dict):
        return False

    if covered_km is not None:
        try:
            entry["covered_km"] = float(covered_km)
        except (TypeError, ValueError):
            pass

    meta = dict(entry.get("meta") if isinstance(entry.get("meta"), dict) else {})
    start_pins, end_pins = _effective_progress_pins(entry)
    cov = float(entry.get("covered_km") or 0)

    # Geometry: trail clipped start→end closed near an end pin ⇒ endpoint reached.
    trail_end_ok = False
    trail_start_ok = bool(meta.get("start_reached"))
    if start_pins and end_pins:
        try:
            from routes import tracking_service as _ts
            use_trail = trail if trail is not None else _ts.trail_for_user(uid, date)
            clipped = _truncate_trail_to_progress_pins(
                use_trail, start_pins, end_pins, lock_end=False,
            )
            if clipped:
                la, lo = clipped[0]
                trail_start_ok = trail_start_ok or _near_any_pin(
                    la, lo, *start_pins, radius_km=START_PIN_REACH_KM,
                )
                ea, eo = clipped[-1]
                trail_end_ok = _near_any_pin(ea, eo, *end_pins, radius_km=END_PIN_REACH_KM)
        except Exception:
            trail_end_ok = False

    end_ok = bool(meta.get("end_reached")) or trail_end_ok or bool(meta.get("completed"))
    start_ok = trail_start_ok or bool(meta.get("start_reached")) or bool(meta.get("completed"))

    # Assigned-corridor backup (nearly full cover of our route) when pins missing
    cover_ok = _enough_cover_for_complete(entry, cov) and (
        float(entry.get("route_km") or 0) >= 0.5
        and cov >= max(0.0, float(entry.get("route_km") or 0) - ASSIGN_COMPLETE_REMAINING_KM)
    )

    if not ((end_ok and start_ok) or cover_ok):
        return _maybe_mark_assignment_complete(uid, date, entry)

    changed = False
    if end_ok and start_ok:
        if not meta.get("start_reached"):
            meta["start_reached"] = True
            meta.setdefault("start_reached_at", datetime.now(IST).isoformat())
            changed = True
        if not meta.get("end_reached"):
            meta["end_reached"] = True
            meta.setdefault("end_reached_at", datetime.now(IST).isoformat())
            changed = True
        if not meta.get("completed"):
            meta["completed"] = True
            meta["completed_at"] = datetime.now(IST).isoformat()
            meta["complete_reason"] = "endpoint_reached"
            changed = True
        elif meta.get("complete_reason") == "end_pin_only_insufficient_cover":
            meta["complete_reason"] = "endpoint_reached"
            meta["completed"] = True
            changed = True
    elif cover_ok and not meta.get("completed"):
        return _maybe_mark_assignment_complete(uid, date, entry)

    if changed:
        entry["meta"] = meta
        day[ukey] = entry
        _save_state(state, persist_db=True)
    return bool(meta.get("completed"))


def find_open_incomplete_assignment(user_id: int) -> dict | None:
    """Oldest incomplete assignment that still blocks new assigns (carry across days)."""
    user_id = int(user_id)
    state = _load_state()
    daily = state.get("daily_assignments", {}) or {}
    ukey = _user_day_key(user_id)
    today = today_ist()
    for date_key in sorted(daily.keys()):
        if date_key > today:
            continue
        entry = daily.get(date_key, {}).get(ukey)
        if not isinstance(entry, dict):
            continue
        has_work = bool(_entry_segment_ids(entry)) or (
            str(entry.get("mode") or "").lower() == "auto_track"
            and entry.get("start")
            and entry.get("end")
        )
        if not has_work:
            continue
        # Attach date for completion checks / UI
        probe = dict(entry)
        probe["assignment_date"] = date_key
        probe["date"] = date_key
        if is_assignment_complete(probe):
            continue
        start = entry.get("start") or {}
        end = entry.get("end") or {}
        return {
            "date": date_key,
            "mode": entry.get("mode") or "corridor",
            "segment_count": len(_entry_segment_ids(entry)),
            "route_km": float(entry.get("route_km") or 0),
            "covered_km": float(entry.get("covered_km") or 0),
            "start": start,
            "end": end,
            "message": (
                f"You still have an incomplete assignment from {date_key} "
                f"({start.get('label') or 'start'} → {end.get('label') or 'end'}). "
                f"Finish that day's quota (Capture + upload until roads are sealed) "
                f"before you can assign a new route. Ask an admin to clear it if it was a test."
            ),
        }
    return None


def assert_can_create_assignment(user_id: int, *, allow_same_day_replace: bool = False) -> None:
    """Block new assigns while incomplete work is mid-route; allow replace when progress is light."""
    open_asg = find_open_incomplete_assignment(user_id)
    if not open_asg:
        return
    today = today_ist()
    open_date = open_asg.get("date") or today
    gate = can_change_or_replace_assignment(user_id, date=open_date)
    if allow_same_day_replace and gate.get("ok"):
        # Change-route / replace: clear light/not-started work (including prior-day carryover)
        if open_date != today:
            try:
                clear_daily_assignment_for_user(user_id, open_date)
            except Exception:
                pass
        return
    if open_date < today:
        raise ValueError(
            gate.get("message")
            or open_asg.get("message")
            or f"Finish your incomplete assignment from {open_date} before assigning a new route."
        )
    raise ValueError(gate.get("message") or open_asg["message"])


CONFLICT_CORRIDOR_KM = 1.5  # soft proximity (warning only; hard conflict = segment overlap)


def _other_assignments_today(user_id: int, date: str) -> list[dict]:
    """All other videographers' assignment entries for the date."""
    state = _load_state()
    day = state.get("daily_assignments", {}).get(date, {}) or {}
    out = []
    for ukey, entry in day.items():
        if not isinstance(entry, dict):
            continue
        if int(entry.get("user_id") or 0) == int(user_id):
            continue
        if not entry.get("segment_ids") and not (entry.get("legs") or []):
            continue
        out.append(entry)
    return out


def _entry_segment_ids(entry: dict) -> set[str]:
    """All segment ids on an assignment, including every leg."""
    ids: set[str] = set()
    for sid in entry.get("segment_ids") or []:
        if sid:
            ids.add(str(sid))
    for leg in entry.get("legs") or []:
        if not isinstance(leg, dict):
            continue
        for sid in leg.get("segment_ids") or []:
            if sid:
                ids.add(str(sid))
    return ids


def _already_km_for_entry(existing: dict | None, fallback_state_key: str, fallback_district_id: str) -> float:
    if not existing:
        return 0.0
    legs_prev = existing.get("legs") or []
    if legs_prev:
        km = 0.0
        for leg in legs_prev:
            km += _km_for_segment_ids(
                leg.get("state_key") or existing.get("state_key") or fallback_state_key,
                str(leg.get("district_id") or existing.get("district_id") or fallback_district_id),
                list(leg.get("segment_ids") or []),
            )
        return km
    return _km_for_segment_ids(
        existing.get("state_key") or fallback_state_key,
        str(existing.get("district_id") or fallback_district_id),
        list(existing.get("segment_ids") or []),
    )


def _release_assigned_day_for_user(state: dict, user_id: int, date: str) -> dict | None:
    """Clear today's assignment for a user and free only 'assigned' segments (keep completed)."""
    day = state.setdefault("daily_assignments", {}).setdefault(date, {})
    ukey = _user_day_key(user_id)
    existing = day.get(ukey) if isinstance(day.get(ukey), dict) else None
    if not existing:
        return None
    status_map = state.setdefault("segment_status", {})
    for sid in _entry_segment_ids(existing):
        if status_map.get(sid) == "assigned":
            status_map[sid] = "available"
    day.pop(ukey, None)
    return existing


def _corridor_overlap_warning(
    user_id: int,
    date: str,
    picked_ids: list[str],
) -> dict | None:
    """Soft notice when another VG already has overlapping segments today (does not block)."""
    picked = {str(x) for x in picked_ids}
    for other in _other_assignments_today(user_id, date):
        other_ids = _entry_segment_ids(other)
        overlap = sorted(picked & other_ids)
        if not overlap:
            continue
        other_start = other.get("start") or {}
        other_end = other.get("end") or {}
        legs = other.get("legs") or []
        if not other_start and legs:
            other_start = (legs[0] or {}).get("start") or {}
        if not other_end and legs:
            other_end = (legs[-1] or {}).get("end") or {}
        area_bits = []
        if other.get("district_name"):
            area_bits.append(str(other["district_name"]))
        o_label = (other_start.get("label") or "")[:80]
        e_label = (other_end.get("label") or "")[:80]
        if o_label or e_label:
            area_bits.append(f"{o_label or 'start'} → {e_label or 'end'}")
        area = " · ".join(area_bits) or "another corridor"
        return {
            "overlap_warning": True,
            "other_user_id": other.get("user_id"),
            "area": area,
            "overlap_count": len(overlap),
            "message": (
                f"Note: {len(overlap)} segment(s) also appear on another videographer's "
                f"route near {area}. Assignment allowed — already-covered roads still "
                f"won't count toward quota."
            ),
        }
    return None


def _corridor_conflict(
    user_id: int,
    date: str,
    picked_ids: list[str],
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    conflict_km: float = CONFLICT_CORRIDOR_KM,
) -> dict | None:
    """Deprecated hard-block helper — overlaps are warnings only (see _corridor_overlap_warning)."""
    _ = (user_id, date, picked_ids, start, end, conflict_km)
    return None

    if not existing:
        return 0.0
    legs_prev = existing.get("legs") or []
    if legs_prev:
        km = 0.0
        for leg in legs_prev:
            km += _km_for_segment_ids(
                leg.get("state_key") or existing.get("state_key") or fallback_state_key,
                str(leg.get("district_id") or existing.get("district_id") or fallback_district_id),
                list(leg.get("segment_ids") or []),
            )
        return km
    return _km_for_segment_ids(
        existing.get("state_key") or fallback_state_key,
        str(existing.get("district_id") or fallback_district_id),
        list(existing.get("segment_ids") or []),
    )


def _access_error_for_points(
    *,
    state_key: str,
    district_id: str,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    allowed_district_ids: list | set | None = None,
) -> str | None:
    """Build a clear missing-district message when corridor fails (out-of-scope only)."""
    allowed = list(allowed_district_ids) if allowed_district_ids is not None else [district_id]
    msgs = []
    for label, lat, lon in (("Start", start_lat, start_lon), ("End", end_lat, end_lon)):
        chk = check_location_access(
            lat, lon,
            state_key=state_key,
            allowed_district_ids=allowed,
            selected_district_id=district_id,
            require_allowed=True,
        )
        # Only report real access denials — success "Located in X" must not become a 400 body
        if chk.get("in_scope") is False and chk.get("message"):
            msgs.append(f"{label}: {chk['message']}")
    if msgs:
        return " ".join(msgs)
    return None


def _state_key_for_district_id(district_id: str | int | None) -> str | None:
    if district_id is None or str(district_id).strip() == "":
        return None
    dist = get_district(district_id)
    return (dist or {}).get("state_key")


def _districts_near_route(
    district_ids: list[str],
    latlon: list[tuple[float, float]],
    *,
    max_center_km: float = 55.0,
    hard_cap: int = 6,
) -> list[str]:
    """Districts whose centers lie near the driven path (samples along the whole polyline).

    Using only start/mid/end drops middle districts on long corridors (e.g. Tirupati→Palakonda),
    which makes snap miss roads and the map line look like it cuts corners.
    """
    ids = [str(x) for x in district_ids if x is not None and str(x).strip() != ""]
    if not ids or not latlon:
        return ids[:hard_cap]
    route_km = _polyline_length_km(latlon)
    # Probe every ~20–40 km along the path so long corridors still catch every district
    probe_step = 20.0 if route_km < 200 else (30.0 if route_km < 500 else 40.0)
    probes = _sample_polyline(latlon, step_km=probe_step)
    if len(probes) < 2:
        probes = [latlon[0], latlon[-1]]

    scored: list[tuple[float, str]] = []
    for did in ids:
        dist = get_district(did)
        c = (dist or {}).get("center") if dist else None
        if not c or c[0] is None:
            continue
        cy, cx = float(c[0]), float(c[1])
        best = min(_haversine_km(p[0], p[1], cy, cx) for p in probes)
        if best <= max_center_km:
            scored.append((best, did))
    scored.sort(key=lambda t: t[0])
    out = [d for _, d in scored[:hard_cap]]
    if not out:
        # Fallback: closest to start/end only
        slat, slon = float(latlon[0][0]), float(latlon[0][1])
        elat, elon = float(latlon[-1][0]), float(latlon[-1][1])
        all_scored = []
        for did in ids:
            dist = get_district(did)
            c = (dist or {}).get("center") if dist else None
            if not c or c[0] is None:
                continue
            best = min(
                _haversine_km(slat, slon, float(c[0]), float(c[1])),
                _haversine_km(elat, elon, float(c[0]), float(c[1])),
            )
            all_scored.append((best, did))
        all_scored.sort(key=lambda t: t[0])
        out = [d for _, d in all_scored[:hard_cap]]
    return out or ids[:hard_cap]


def _persist_leg(
    *,
    state: dict,
    user_id: int,
    date: str,
    state_key: str,
    district_id: str,
    dist: dict,
    picked: list[str],
    total: float,
    start: dict,
    end: dict,
    corridor_km: float | None,
    route_meta: dict,
    already_km: float,
    already_ids: list[str],
    existing: dict | None,
    mode: str = "corridor",
) -> tuple[dict, float]:
    """Write a new leg into daily_assignments; return (day_entry, combined_km)."""
    daily = state.setdefault("daily_assignments", {})
    day = daily.setdefault(date, {})
    ukey = _user_day_key(user_id)
    status_map = state.setdefault("segment_status", {})
    for sid in picked:
        # Keep historically completed roads as completed; only mark fresh legs assigned
        if status_map.get(sid) not in ("completed", "verified"):
            status_map[sid] = "assigned"

    leg = {
        "district_id": district_id,
        "district_name": dist.get("name"),
        "state_key": state_key,
        "segment_ids": picked,
        "km": round(total, 2),
        "start": start,
        "end": end,
        "corridor_km": corridor_km,
        "preferred_km": route_meta.get("preferred_km", 0),
        "connector_km": route_meta.get("connector_km", 0),
        "mode": mode,
        "created_at": datetime.now(IST).isoformat(),
    }

    if existing and existing.get("segment_ids"):
        legs = list(existing.get("legs") or [])
        if not legs:
            legs.append({
                "district_id": existing.get("district_id"),
                "district_name": existing.get("district_name"),
                "state_key": existing.get("state_key"),
                "segment_ids": list(existing.get("segment_ids") or []),
                "km": round(already_km, 2),
                "start": existing.get("start"),
                "end": existing.get("end"),
                "corridor_km": existing.get("corridor_km"),
                "created_at": existing.get("created_at"),
            })
        legs.append(leg)
        merged_ids = list(dict.fromkeys([*already_ids, *picked]))
        # Keep primary district as the first leg's district for stable UI default
        primary_did = str(legs[0].get("district_id") or district_id)
        primary_dist = get_district(primary_did, legs[0].get("state_key") or state_key) or dist
        existing["segment_ids"] = merged_ids
        existing["legs"] = legs
        existing["district_id"] = primary_did
        existing["district_name"] = primary_dist.get("name") or existing.get("district_name")
        existing["state_key"] = primary_dist.get("state_key") or state_key
        existing["state_id"] = primary_dist.get("state_id") or dist.get("state_id")
        if not existing.get("start"):
            existing["start"] = start
        existing["end"] = end
        existing["continuous"] = mode != "manual"
        existing["preferred_km"] = round(
            float(existing.get("preferred_km") or 0) + float(route_meta.get("preferred_km") or 0), 2
        )
        existing["connector_km"] = round(
            float(existing.get("connector_km") or 0) + float(route_meta.get("connector_km") or 0), 2
        )
        day[ukey] = existing
        return existing, already_km + total

    entry = {
        "user_id": user_id,
        "district_id": district_id,
        "district_name": dist.get("name"),
        "state_key": state_key,
        "state_id": dist.get("state_id"),
        "segment_ids": picked,
        "legs": [leg],
        "start": start,
        "end": end,
        "corridor_km": corridor_km,
        "continuous": mode != "manual",
        "preferred_km": route_meta.get("preferred_km", 0),
        "connector_km": route_meta.get("connector_km", 0),
        "covered_km": 0.0,
        "covered_by_class": {"nh": 0.0, "sh": 0.0, "mdr": 0.0, "other": 0.0},
        "created_at": datetime.now(IST).isoformat(),
    }
    day[ukey] = entry
    return entry, total


def _path_budget_km(start_lat, start_lon, end_lat, end_lon, leg_km: float | None = None) -> float:
    """Budget for A→B routing: enough for realistic detours, never the daily quota."""
    straight = _haversine_km(start_lat, start_lon, end_lat, end_lon)
    budget = max(straight * 3.5, straight + 3.0, 6.0)
    budget = min(budget, max(straight * 6.0, 40.0), 60.0)
    if leg_km is not None:
        try:
            budget = min(budget, max(0.5, float(leg_km)))
        except (TypeError, ValueError):
            pass
    return budget


def _route_geojson_for_ids(state_key: str, sid_district: dict[str, str], segment_ids: list[str]) -> dict:
    """Build a FeatureCollection for map polyline display of a route option."""
    features = []
    by_district: dict[str, set[str]] = {}
    for sid in segment_ids:
        did = str(sid_district.get(sid) or sid.split("_", 1)[0])
        by_district.setdefault(did, set()).add(sid)
    order = {sid: i for i, sid in enumerate(segment_ids)}
    for did, id_set in by_district.items():
        sk = _state_key_for_district_id(did) or state_key
        for feat in _segment_features(sk, did):
            props = dict(feat.get("properties") or {})
            sid = _segment_id(props, did)
            if sid not in id_set:
                continue
            props["id"] = sid
            props["segment_id"] = sid
            props["district_id"] = did
            props["state_key"] = sk
            props["length_km"] = round(float(props.get("length_km") or 0), 3)
            features.append({
                "type": "Feature",
                "properties": props,
                "geometry": feat.get("geometry"),
            })
    features.sort(key=lambda f: order.get(f["properties"]["segment_id"], 10**9))
    return {"type": "FeatureCollection", "features": features}


def _polyline_feature(latlon: list[tuple[float, float]], props: dict | None = None) -> dict:
    """Single continuous LineString GeoJSON (lon,lat) for Maps-style display."""
    return {
        "type": "Feature",
        "properties": props or {},
        "geometry": {
            "type": "LineString",
            "coordinates": [[lon, lat] for lat, lon in latlon],
        },
    }


def _sample_polyline(latlon: list[tuple[float, float]], step_km: float = 0.06) -> list[tuple[float, float]]:
    if not latlon:
        return []
    out = [latlon[0]]
    acc = 0.0
    for i in range(1, len(latlon)):
        a, b = latlon[i - 1], latlon[i]
        d = _haversine_km(a[0], a[1], b[0], b[1])
        acc += d
        if acc >= step_km:
            out.append(b)
            acc = 0.0
    if out[-1] != latlon[-1]:
        out.append(latlon[-1])
    return out


def _downsample_latlon(latlon: list[tuple[float, float]], max_pts: int = 320) -> list[tuple[float, float]]:
    """Thin a polyline to at most max_pts (keeps endpoints)."""
    n = len(latlon)
    if n <= max_pts or max_pts < 3:
        return latlon
    out = [latlon[0]]
    step = (n - 1) / (max_pts - 1)
    for i in range(1, max_pts - 1):
        idx = int(round(i * step))
        out.append(latlon[idx])
    out.append(latlon[-1])
    return out


def _point_seg_dist_km(
    lat: float, lon: float,
    a_lat: float, a_lon: float,
    b_lat: float, b_lon: float,
) -> float:
    """Approx distance from point to segment AB (km)."""
    # Equirectangular metres around mid-lat
    mid = math.radians((a_lat + b_lat) * 0.5)
    cos_m = max(0.2, math.cos(mid))
    ax, ay = a_lon * cos_m, a_lat
    bx, by = b_lon * cos_m, b_lat
    px, py = lon * cos_m, lat
    dx, dy = bx - ax, by - ay
    len2 = dx * dx + dy * dy
    if len2 < 1e-18:
        return _haversine_km(lat, lon, a_lat, a_lon)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len2))
    qx, qy = ax + t * dx, ay + t * dy
    # deg → km (~111)
    return math.hypot(px - qx, py - qy) * 111.0


def _rdp_polyline(latlon: list[tuple[float, float]], epsilon_km: float = 0.012) -> list[tuple[float, float]]:
    """Ramer–Douglas–Peucker — keeps road bends, thins long straight highways."""
    n = len(latlon)
    if n <= 2:
        return list(latlon)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a_lat, a_lon = latlon[i]
        b_lat, b_lon = latlon[j]
        max_d = -1.0
        max_k = i
        for k in range(i + 1, j):
            d = _point_seg_dist_km(latlon[k][0], latlon[k][1], a_lat, a_lon, b_lat, b_lon)
            if d > max_d:
                max_d = d
                max_k = k
        if max_d > epsilon_km:
            keep[max_k] = True
            stack.append((i, max_k))
            stack.append((max_k, j))
    return [latlon[i] for i in range(n) if keep[i]]


def _display_polyline(latlon: list[tuple[float, float]], route_km: float | None = None) -> list[tuple[float, float]]:
    """Map-ready polyline that still follows roads (not giant straight chords).

    Short urban legs keep nearly full OSRM detail; longer corridors use a tight
    Douglas–Peucker (~5–12 m) so bends stay on the carriageway.
    """
    if not latlon:
        return []
    km = float(route_km) if route_km is not None else _polyline_length_km(latlon)
    # City routes: keep almost every vertex (3–5 m). Long highways can thin more.
    if km < 15:
        eps = 0.003
    elif km < 80:
        eps = 0.006
    elif km < 300:
        eps = 0.010
    else:
        eps = 0.012
    simplified = _rdp_polyline(latlon, epsilon_km=eps)
    max_pts = 14000 if km >= 400 else (10000 if km >= 100 else 8000)
    if len(simplified) <= max_pts:
        return simplified
    return _downsample_latlon(simplified, max_pts)


def _snap_step_km(route_km: float) -> float:
    """Snap sample spacing — spatial grid keeps this cheap; stay dense for coverage."""
    if route_km >= 400:
        return 0.35
    if route_km >= 150:
        return 0.28
    if route_km >= 60:
        return 0.22
    if route_km >= 25:
        return 0.18
    return 0.12


def _snap_district_budget(route_km: float) -> tuple[int, float]:
    """(hard_cap, max_center_km) — long corridors need every district along the path."""
    if route_km >= 400:
        return min(48, max(24, int(route_km / 18) + 8)), 70.0
    if route_km >= 150:
        return min(32, max(16, int(route_km / 20) + 6)), 65.0
    if route_km >= 60:
        return 14, 60.0
    if route_km >= 30:
        return 10, 55.0
    return 8, 50.0


def _snap_polyline_to_segments(
    latlon: list[tuple[float, float]],
    *,
    state_key: str,
    district_ids: list[str],
    status_map: dict,
    focus: str | None = None,
    max_dist_km: float = 0.12,
) -> tuple[list[str], dict[str, str], float]:
    """Map an OSRM polyline onto survey segment ids in allowed districts.

    Uses a spatial grid so dense sampling stays fast on long corridors while
    still marking the real road segments for coverage / no-reassign.
    """
    if not latlon or not district_ids:
        return [], {}, 0.0

    route_km = _polyline_length_km(latlon)
    step_km = _snap_step_km(route_km)
    # Long routes: prefer highway/MDR (still enough for coverage; local streets explode size)
    snap_focus = focus
    if route_km >= 35:
        allowed_focus = parse_focus_classes(focus)
        if allowed_focus is None or "other" in allowed_focus:
            snap_focus = "nh,sh,mdr"
    allowed = parse_focus_classes(snap_focus)

    hard_cap, max_center_km = _snap_district_budget(route_km)
    near_ids = _districts_near_route(
        district_ids, latlon,
        max_center_km=max_center_km,
        hard_cap=hard_cap,
    )

    # Corridor pad around the line (not the full start–end rectangle for long diagonals)
    cell = 0.02  # ~2 km — tighter cells = more accurate nearest-road picks
    candidates: list[tuple[str, float, float, float, str, str]] = []
    grid: dict[tuple[int, int], list[int]] = {}

    def _cell(lat: float, lon: float) -> tuple[int, int]:
        return (int(math.floor(lat / cell)), int(math.floor(lon / cell)))

    # Corridor cells from the driven path (dense enough to not miss bends)
    corridor_cells: set[tuple[int, int]] = set()
    for plat, plon in _sample_polyline(latlon, step_km=max(0.35, min(step_km, 0.6))):
        ci, cj = _cell(plat, plon)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                corridor_cells.add((ci + di, cj + dj))

    def _load_meta(did: str):
        sk = _state_key_for_district_id(did) or state_key
        if not sk:
            return did, None, {}
        return did, sk, _segment_meta(sk, str(did))

    # Server can parallel-load district indexes (cold parse is the slow part)
    loaded: list[tuple[str, str | None, dict]] = []
    if len(near_ids) <= 1:
        loaded = [_load_meta(near_ids[0])] if near_ids else []
    else:
        with ThreadPoolExecutor(max_workers=min(10, len(near_ids))) as pool:
            loaded = list(pool.map(_load_meta, near_ids))

    for did, sk, meta in loaded:
        if not sk or not meta:
            continue
        for sid, m in meta.items():
            if allowed is not None and (m.get("road_class") or "other") not in allowed:
                continue
            mid = m.get("mid")
            if not mid:
                continue
            mlat, mlon = float(mid[0]), float(mid[1])
            ck = _cell(mlat, mlon)
            if corridor_cells and ck not in corridor_cells:
                continue
            idx = len(candidates)
            candidates.append((sid, mlat, mlon, float(m.get("length") or 0), str(did), sk))
            grid.setdefault(ck, []).append(idx)

    if not candidates:
        return [], {}, 0.0

    sampled = _sample_polyline(latlon, step_km=step_km)
    # Cap samples for extreme lengths (grid lookup stays O(1) per sample)
    max_samples = 2500 if route_km >= 400 else (1800 if route_km >= 150 else 1200)
    if len(sampled) > max_samples:
        sampled = _downsample_latlon(sampled, max_samples)

    picked: list[str] = []
    sid_district: dict[str, str] = {}
    sid_state: dict[str, str] = {}
    seen: set[str] = set()
    hit_counts: dict[str, int] = {}
    dist_lim = max_dist_km if route_km < 40 else max(max_dist_km, 0.16)

    for plat, plon in sampled:
        best_sid = None
        best_d = dist_lim
        best_did = ""
        best_sk = state_key
        ci, cj = _cell(plat, plon)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for idx in grid.get((ci + di, cj + dj), ()):
                    sid, mlat, mlon, _length, did, sk = candidates[idx]
                    d = _haversine_km(plat, plon, mlat, mlon)
                    if d < best_d:
                        best_d = d
                        best_sid = sid
                        best_did = did
                        best_sk = sk
        if not best_sid:
            continue
        hit_counts[best_sid] = hit_counts.get(best_sid, 0) + 1
        if best_sid not in seen:
            seen.add(best_sid)
            picked.append(best_sid)
            sid_district[best_sid] = best_did
            sid_state[best_sid] = best_sk

    # Stub filter only for short urban corridors (meta lookups add cost on long picks)
    if route_km < 45 and len(sampled) >= 2 and picked:
        route_bear = _bearing_deg(sampled[0][0], sampled[0][1], sampled[-1][0], sampled[-1][1])
        filtered: list[str] = []
        for sid in picked:
            hits = hit_counts.get(sid, 0)
            did = sid_district.get(sid)
            sk = sid_state.get(sid) or _state_key_for_district_id(did) or state_key
            m = _segment_meta(sk, str(did)).get(sid) if did and sk else None
            length = float((m or {}).get("length") or 0)
            if hits < 2 and length > 0.12:
                continue
            if hits == 1 and length > 0.05:
                continue
            sb = _meta_bearing_deg(m) if m else None
            if sb is not None and hits < 4:
                diff = _angle_diff_deg(route_bear, sb)
                if 55.0 < diff < 125.0:
                    continue
            filtered.append(sid)
        if filtered:
            picked = filtered
            sid_district = {s: sid_district[s] for s in picked if s in sid_district}
            sid_state = {s: sid_state[s] for s in picked if s in sid_state}
            hit_counts = {s: hit_counts.get(s, 0) for s in picked}

    return _dedupe_parallel_road_sides(
        picked,
        sid_district,
        state_key=state_key,
        sid_state=sid_state,
        # Dual-carriageway filter needs the driven line; use thinned copy for long routes
        latlon=_display_polyline(latlon, route_km) if route_km >= 80 else latlon,
        hit_counts=hit_counts,
    )

def _format_osrm_instruction(maneuver_type: str, modifier: str, name: str) -> str:
    t = (maneuver_type or "").lower()
    m = (modifier or "").lower().replace("_", " ")
    road = (name or "").strip()
    onto = f" onto {road}" if road else ""
    if t == "depart":
        return f"Start{onto}" if road else "Start"
    if t == "arrive":
        return "Arrive at destination"
    if t == "roundabout":
        return f"Enter roundabout{onto}"
    if t == "rotary":
        return f"Enter rotary{onto}"
    if t == "fork":
        return f"Keep {m or 'straight'} at fork{onto}"
    if t == "end of road":
        return f"At end of road, turn {m or 'left'}{onto}"
    if t == "new name":
        return f"Continue{onto}" if road else "Continue"
    if t in ("turn", "ramp", "merge", "on ramp", "off ramp", "exit rotary", "exit roundabout"):
        if m == "uturn" or m == "u turn":
            return f"Make a U-turn{onto}"
        if m in ("left", "sharp left", "slight left"):
            return f"Turn {m}{onto}"
        if m in ("right", "sharp right", "slight right"):
            return f"Turn {m}{onto}"
        if m in ("straight", ""):
            return f"Continue straight{onto}"
        return f"{t.replace('_', ' ').title()} {m}{onto}".strip()
    if t == "continue":
        if m in ("uturn", "u turn"):
            return f"Make a U-turn{onto}"
        return f"Continue{onto}" if road else "Continue"
    if m in ("uturn", "u turn"):
        return f"Make a U-turn{onto}"
    label = " ".join(x for x in (t.replace("_", " "), m) if x).strip().title()
    return f"{label}{onto}".strip() or "Continue"


def _osrm_fetch_routes(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    *,
    max_alternatives: int = 3,
) -> list[dict]:
    """Call OSRM for driving routes with geometry + turn steps.

    Default public server: https://router.project-osrm.org
    Override with env ``OSRM_BASE_URL`` (e.g. self-hosted) for reliability.
    """
    path = f"{start_lon},{start_lat};{end_lon},{end_lat}"
    params = urllib.parse.urlencode({
        "alternatives": "true" if max_alternatives > 0 else "false",
        "steps": "true",
        "overview": "full",
        "geometries": "geojson",
        "annotations": "false",
    })
    base = os.getenv("OSRM_BASE_URL", "https://router.project-osrm.org").rstrip("/")
    url = f"{base}/route/v1/driving/{path}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": NOMINATIM_UA})
    # Long corridors need more time on the public OSRM; short hops stay snappy
    straight = _haversine_km(start_lat, start_lon, end_lat, end_lon)
    timeout = 45 if straight >= 200 else (25 if straight >= 60 else 12)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, dict) or data.get("code") != "Ok":
            return []
        return list(data.get("routes") or [])[: max(1, max_alternatives + 1 if max_alternatives else 1)]
    except Exception as e:
        raise e


def _osrm_route_steps(route: dict) -> list[dict]:
    steps_out: list[dict] = []
    for leg in route.get("legs") or []:
        for step in leg.get("steps") or []:
            man = step.get("maneuver") or {}
            loc = man.get("location") or [None, None]
            try:
                lon, lat = float(loc[0]), float(loc[1])
            except (TypeError, ValueError, IndexError):
                continue
            mtype = str(man.get("type") or "")
            modifier = str(man.get("modifier") or "")
            name = str(step.get("name") or "")
            instruction = _format_osrm_instruction(mtype, modifier, name)
            if mtype in ("notification",):
                continue
            steps_out.append({
                "instruction": instruction,
                "maneuver": "-".join(x for x in (mtype, modifier) if x),
                "type": mtype,
                "modifier": modifier,
                "name": name,
                "distance_m": int(round(float(step.get("distance") or 0))),
                "duration_s": int(round(float(step.get("duration") or 0))),
                "lat": lat,
                "lon": lon,
            })
    return steps_out


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Forward azimuth in degrees [0, 360)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _angle_diff_deg(a: float, b: float) -> float:
    """Smallest difference between two bearings (0–180)."""
    d = abs(a - b) % 360.0
    return d if d <= 180.0 else 360.0 - d


def _meta_bearing_deg(m: dict) -> float | None:
    ends = m.get("ends") or []
    if not ends:
        return None
    u, v = ends[0][0], ends[0][1]
    # node keys are (lon, lat)
    try:
        return _bearing_deg(float(u[1]), float(u[0]), float(v[1]), float(v[0]))
    except (TypeError, ValueError, IndexError):
        return None


def _name_ref_compat(a: dict, b: dict) -> bool:
    """True when two segments look like the same named/ref road (or both unnamed)."""
    ra = (a.get("ref") or "").strip().upper()
    rb = (b.get("ref") or "").strip().upper()
    if ra and rb:
        return ra == rb or ra in rb or rb in ra
    na = (a.get("name") or "").strip().lower()
    nb = (b.get("name") or "").strip().lower()
    if na and nb:
        return na == nb or na in nb or nb in na
    # Unnamed parallel NH/SH still count as compatible when class matches
    return (a.get("road_class") or "other") == (b.get("road_class") or "other")


def _min_dist_to_polyline_km(lat: float, lon: float, latlon: list[tuple[float, float]]) -> float:
    if not latlon:
        return 999.0
    return min(_haversine_km(lat, lon, p[0], p[1]) for p in latlon)


def _are_parallel_dual_sides(
    ma: dict,
    mb: dict,
    *,
    max_mid_sep_km: float = 0.18,
    max_bearing_diff_deg: float = 35.0,
) -> bool:
    """Detect opposite / adjacent carriageways of the same corridor."""
    mida, midb = ma.get("mid"), mb.get("mid")
    if not mida or not midb:
        return False
    sep = _haversine_km(float(mida[0]), float(mida[1]), float(midb[0]), float(midb[1]))
    # Typical dual median 12–150 m; ignore near-identical mids and far pairs
    if sep < 0.012 or sep > max_mid_sep_km:
        return False

    ca = (ma.get("road_class") or "other")
    cb = (mb.get("road_class") or "other")
    named = _name_ref_compat(ma, mb)
    if not named and ca != cb:
        return False
    # Unnamed / mismatched labels: still dual if fairly close + same class
    if not named and sep > 0.14:
        return False

    ba, bb = _meta_bearing_deg(ma), _meta_bearing_deg(mb)
    if ba is None or bb is None:
        return named or ca == cb
    diff = _angle_diff_deg(ba, bb)
    return diff <= max_bearing_diff_deg or abs(diff - 180.0) <= max_bearing_diff_deg


def _polyline_length_km(latlon: list[tuple[float, float]] | list | None) -> float:
    if not latlon or len(latlon) < 2:
        return 0.0
    total = 0.0
    prev = None
    for p in latlon:
        try:
            if isinstance(p, dict):
                lat, lon = float(p["lat"]), float(p["lon"])
            else:
                lat, lon = float(p[0]), float(p[1])
        except (TypeError, ValueError, KeyError, IndexError):
            continue
        if prev is not None:
            total += _haversine_km(prev[0], prev[1], lat, lon)
        prev = (lat, lon)
    return total


def _dedupe_parallel_road_sides(
    picked: list[str],
    sid_district: dict[str, str],
    *,
    state_key: str,
    sid_state: dict[str, str] | None = None,
    latlon: list[tuple[float, float]] | None = None,
    hit_counts: dict[str, int] | None = None,
) -> tuple[list[str], dict[str, str], float]:
    """Keep one carriageway when snap grabbed both sides of a divided road.

    Prefers the side with more snap hits, then closer to the driven polyline.
    """
    sk_map = sid_state or {}

    def _meta_for(sid: str) -> dict | None:
        did = sid_district.get(sid)
        if not did:
            return None
        sk = sk_map.get(sid) or _state_key_for_district_id(did) or state_key
        if not sk:
            return None
        return _segment_meta(sk, str(did)).get(sid)

    if len(picked) < 2:
        total = 0.0
        for sid in picked:
            m = _meta_for(sid) or {}
            total += float(m.get("length") or 0)
        return picked, sid_district, total

    meta_by_sid: dict[str, dict] = {}
    for sid in picked:
        m = _meta_for(sid)
        if m:
            meta_by_sid[sid] = m

    samples = _sample_polyline(latlon, step_km=0.06) if latlon and len(latlon) >= 2 else []
    hits = hit_counts or {}
    kept: list[str] = []
    dropped: set[str] = set()

    def _score(sid: str, m: dict) -> tuple:
        mid = m.get("mid") or (0.0, 0.0)
        dist = _min_dist_to_polyline_km(float(mid[0]), float(mid[1]), samples) if samples else 0.0
        # Higher hits better, lower dist better
        return (hits.get(sid, 0), -dist)

    for sid in picked:
        if sid in dropped or sid not in meta_by_sid:
            if sid not in dropped and sid in sid_district and sid not in kept:
                kept.append(sid)
            continue
        ma = meta_by_sid[sid]
        rival = None
        for other in kept:
            mb = meta_by_sid.get(other)
            if not mb:
                continue
            if _are_parallel_dual_sides(ma, mb):
                rival = other
                break
        if rival is None:
            kept.append(sid)
            continue
        mb = meta_by_sid[rival]
        if _score(sid, ma) > _score(rival, mb):
            kept = [sid if x == rival else x for x in kept]
            dropped.add(rival)
        else:
            dropped.add(sid)

    # Second pass: catch pairs that entered kept before rivalry was known (order quirks)
    final: list[str] = []
    for sid in kept:
        if sid in dropped or sid not in meta_by_sid:
            if sid not in dropped:
                final.append(sid)
            continue
        ma = meta_by_sid[sid]
        rival = None
        for other in final:
            mb = meta_by_sid.get(other)
            if mb and _are_parallel_dual_sides(ma, mb):
                rival = other
                break
        if rival is None:
            final.append(sid)
            continue
        mb = meta_by_sid[rival]
        if _score(sid, ma) > _score(rival, mb):
            final = [sid if x == rival else x for x in final]
        # else drop sid

    new_district = {sid: sid_district[sid] for sid in final if sid in sid_district}
    total = 0.0
    for sid in final:
        m = meta_by_sid.get(sid) or {}
        total += float(m.get("length") or 0)
    return final, new_district, total



