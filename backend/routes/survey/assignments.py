"""survey.assignments — extends progress (includes private _names)."""
from __future__ import annotations

import routes.survey.progress as _progress

globals().update({k: v for k, v in vars(_progress).items() if not k.startswith('__')})

def preview_corridor_routes(
    *,
    state_key: str,
    district_id: str,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    start_label: str = "",
    end_label: str = "",
    allowed_district_ids: list | set | None = None,
    max_options: int = 4,
    focus: str | None = None,
) -> dict:
    """Maps-style A→B routes via OSRM (continuous path + turns), snapped to survey segments."""
    district_id = str(district_id)
    settings = get_settings()
    focus = focus_to_storage(focus if focus is not None else (settings.get("focus_road_class") or "all"))
    status_map = _load_state().get("segment_status", {})

    allowed_list: list[str] = []
    for x in (allowed_district_ids or [district_id]):
        d = str(x)
        if d and d not in allowed_list:
            allowed_list.append(d)
    if district_id not in allowed_list:
        allowed_list.insert(0, district_id)

    # Access: match against the VG's districts only (not global district-center nearest).
    # Ranga Reddy points near Hyderabad used to be labelled 507 and wrongly rejected.
    start_loc = locate_point_in_allowed_districts(start_lat, start_lon, allowed_list)
    end_loc = locate_point_in_allowed_districts(end_lat, end_lon, allowed_list)
    for label, loc, lat, lon in (
        ("Start", start_loc, start_lat, start_lon),
        ("End", end_loc, end_lat, end_lon),
    ):
        if loc:
            continue
        # Outside allowed — name the nearest global district for a clear error
        other = locate_point_any_state(lat, lon, allow_center_fallback=True, max_center_km=40.0)
        if not other:
            raise ValueError(
                f"{label}: could not match this point to your assigned district road network."
            )
        did = str(other.get("district_id") or "")
        name = other.get("district_name") or did
        sk = other.get("state_key") or ""
        state_label = STATE_BY_ID.get(STATE_ID_BY_KEY.get(sk, -1), {}).get("label") or sk
        raise ValueError(
            f"{label}: You do not have access to {name}"
            + (f" ({state_label})" if state_label else "")
            + f" (district {did})."
        )
    if start_loc:
        district_id = str(start_loc["district_id"])
        if start_loc.get("state_key"):
            state_key = start_loc["state_key"]
    elif end_loc:
        district_id = str(end_loc["district_id"])
        if end_loc.get("state_key"):
            state_key = end_loc["state_key"]
    else:
        sk = _state_key_for_district_id(district_id)
        if sk:
            state_key = sk

    straight = round(_haversine_km(start_lat, start_lon, end_lat, end_lon), 2)
    path_budget = _path_budget_km(start_lat, start_lon, end_lat, end_lon)
    # Corridor graph fallback only for short hops — long A→B must use OSRM + snap on server
    near_for_graph = _districts_near_route(
        allowed_list,
        [(start_lat, start_lon), (end_lat, end_lon)],
        max_center_km=80.0,
        hard_cap=8,
    )
    extras_near = [d for d in near_for_graph if d != district_id]

    options: list[dict] = []
    seen_sig: set[tuple] = set()

    osrm_routes: list[dict] = []
    try:
        osrm_routes = _osrm_fetch_routes(
            start_lat, start_lon, end_lat, end_lon, max_alternatives=1
        )
    except Exception:
        osrm_routes = []

    for route in osrm_routes:
        if len(options) >= max_options:
            break
        # Long corridors: snap only the primary OSRM route (alts double server time)
        if options and straight >= 50.0:
            break
        geom = (route.get("geometry") or {}).get("coordinates") or []
        if len(geom) < 2:
            continue
        # OSRM: [lon, lat] → (lat, lon)
        latlon = [(float(c[1]), float(c[0])) for c in geom if isinstance(c, (list, tuple)) and len(c) >= 2]
        if len(latlon) < 2:
            continue
        dist_m = float(route.get("distance") or 0)
        dur_s = float(route.get("duration") or 0)
        km = round(dist_m / 1000.0, 1) if dist_m else round(
            sum(_haversine_km(latlon[i][0], latlon[i][1], latlon[i + 1][0], latlon[i + 1][1])
                for i in range(len(latlon) - 1)),
            1,
        )
        if km >= 60.0 and options:
            break
        steps = _osrm_route_steps(route)
        picked, sid_district, snap_km = _snap_polyline_to_segments(
            latlon,
            state_key=state_key,
            district_ids=allowed_list,
            status_map=status_map,
            focus=focus,
        )
        # Prefer OSRM distance for label; keep snapped ids for assignment
        if not picked:
            continue
        sig = (
            round(latlon[0][0], 4),
            round(latlon[0][1], 4),
            round(latlon[len(latlon) // 2][0], 4),
            round(latlon[-1][0], 4),
            round(km, 1),
        )
        if sig in seen_sig:
            continue
        seen_sig.add(sig)
        draw = _display_polyline(latlon, km)
        line_feat = _polyline_feature(draw, {
            "route_kind": "osrm",
            "km": km,
            "segment_count": len(picked),
        })
        options.append({
            "id": f"route_{len(options) + 1}",
            "rank": 0,
            "label": "",
            "km": km,
            "drive_km": km,
            "snap_km": round(snap_km, 1),
            "segment_count": len(picked),
            "segment_ids": picked,
            "district_ids": sorted(set(sid_district.values())),
            "duration_min_est": max(1, int(round(dur_s / 60.0))) if dur_s else max(1, int(round(km / 0.35))),
            "steps": steps[:80],
            "polyline": [[lat, lon] for lat, lon in draw],
            "geojson": {"type": "FeatureCollection", "features": [line_feat]},
            "source": "osrm",
            "start": {"lat": start_lat, "lon": start_lon, "label": start_label},
            "end": {"lat": end_lat, "lon": end_lon, "label": end_label},
            **_already_covered_stats(picked, sid_district, status_map),
        })
        if km >= 60.0:
            break

    # Fallback: internal corridor graph ONLY for short legs (long ones hang for minutes)
    if not options and straight <= 35.0:
        max_attempts = 2
        blocked: set[str] = set()
        penalized: dict[str, float] = {}
        for attempt in range(max_attempts):
            if len(options) >= min(2, max_options):
                break
            corridor = 2.5 + 1.5 * (attempt % 3)
            picked, total, meta = _pick_continuous_corridor(
                state_key,
                district_id,
                (start_lat, start_lon),
                (end_lat, end_lon),
                corridor_km=corridor,
                focus=focus,
                target_km=path_budget,
                status_map=status_map,
                extra_district_ids=extras_near,
                blocked_sids=blocked or None,
                penalized_sids=penalized or None,
            )
            if not picked or total < 0.25:
                continue
            sig = (picked[0], picked[len(picked) // 2], picked[-1], round(total, 1))
            if sig in seen_sig:
                continue
            seen_sig.add(sig)
            sid_district = meta.get("sid_district") or {}
            geo = _route_geojson_for_ids(state_key, sid_district, picked)
            options.append({
                "id": f"route_{len(options) + 1}",
                "rank": 0,
                "label": "",
                "km": round(total, 1),
                "segment_count": len(picked),
                "segment_ids": picked,
                "district_ids": meta.get("district_ids_used") or sorted(set(sid_district.values())),
                "duration_min_est": max(1, int(round(total / 0.35))),
                "steps": [],
                "geojson": geo,
                "source": "corridor",
                "start": {"lat": start_lat, "lon": start_lon, "label": start_label},
                "end": {"lat": end_lat, "lon": end_lon, "label": end_label},
                **_already_covered_stats(picked, sid_district, status_map),
            })
            mid = picked[len(picked) // 5: 4 * len(picked) // 5] or picked
            step = max(1, len(mid) // 6)
            for i, sid in enumerate(mid[::step]):
                if i < 4:
                    blocked.add(sid)
                else:
                    penalized[sid] = penalized.get(sid, 0) + 8.0

    options.sort(key=lambda o: (o["km"], o["segment_count"]))
    for i, opt in enumerate(options):
        opt["id"] = f"route_{i + 1}"
        opt["rank"] = i + 1
        if i == 0:
            opt["label"] = f"Fastest · {opt['km']} km"
        else:
            delta = round(opt["km"] - options[0]["km"], 1)
            opt["label"] = f"Alt {i} · {opt['km']} km" + (f" (+{delta} km)" if delta > 0 else "")

    if not options:
        if straight > 35.0 and not osrm_routes:
            raise ValueError(
                "Could not reach the routing service for this long corridor. "
                "Check server internet access to OSRM, then try Find routes again."
            )
        access_msg = _access_error_for_points(
            state_key=state_key,
            district_id=district_id,
            start_lat=start_lat,
            start_lon=start_lon,
            end_lat=end_lat,
            end_lon=end_lon,
            allowed_district_ids=allowed_district_ids,
        )
        raise ValueError(
            access_msg
            or "No road routes found between those points in your districts. Try closer places or a shorter corridor."
        )

    return {
        "straight_km": straight,
        "path_budget_km": round(path_budget, 1),
        "count": len(options),
        "routes": options,
        "start": {"lat": start_lat, "lon": start_lon, "label": start_label},
        "end": {"lat": end_lat, "lon": end_lon, "label": end_label},
        "state_key": state_key,
        "district_id": district_id,
    }


def generate_corridor_assignment(
    user_id: int,
    *,
    state_key: str,
    district_id: str,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    start_label: str = "",
    end_label: str = "",
    date: str | None = None,
    corridor_km: float = 2.5,
    leg_km: float | None = None,
    allowed_district_ids: list | set | None = None,
    segment_ids: list[str] | None = None,
    replace: bool = True,
    polyline: list | None = None,
) -> dict:
    """Assign an A→B corridor (actual path length — does not pad to daily quota).

    ``replace=True`` (default) replaces today's assignment for this user so
    Change route never self-conflicts. Cross-VG overlaps are soft warnings only.
    """
    assert_can_create_assignment(user_id, allow_same_day_replace=bool(replace))
    date = date or today_ist()
    user_id = int(user_id)
    district_id = str(district_id)
    settings = get_settings()
    daily_target = float(settings.get("daily_km", 100))
    focus = focus_to_storage(settings.get("focus_road_class") or "all")

    dist = get_district(district_id, state_key)
    if not dist:
        raise ValueError(f"Unknown district {district_id} in {state_key}")

    state = _load_state()
    daily = state.setdefault("daily_assignments", {})
    day = daily.setdefault(date, {})
    ukey = _user_day_key(user_id)

    if replace:
        _release_assigned_day_for_user(state, user_id, date)

    existing = day.get(ukey) if isinstance(day.get(ukey), dict) else None
    already_ids = list((existing or {}).get("segment_ids") or [])
    already_km = _already_km_for_entry(existing, state_key, district_id)

    # Daily quota is informational for adding more legs later — a single A→B is not padded to it
    remaining = max(0.0, daily_target - already_km)

    status_map = state.get("segment_status", {})
    extras = []
    for x in (allowed_district_ids or []):
        d = str(x)
        if d and d != district_id and d not in extras:
            extras.append(d)
    start_loc = locate_point_in_state(start_lat, start_lon, state_key)
    if start_loc and str(start_loc.get("district_id")) in {str(x) for x in (allowed_district_ids or [district_id])}:
        resolved = str(start_loc["district_id"])
        if resolved != district_id:
            if district_id not in extras:
                extras.append(district_id)
            district_id = resolved
            dist = get_district(district_id, state_key) or dist

    path_budget = _path_budget_km(start_lat, start_lon, end_lat, end_lon, leg_km)

    if segment_ids or polyline:
        poly_latlon: list[tuple[float, float]] = []
        if polyline:
            for p in polyline:
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    try:
                        poly_latlon.append((float(p[0]), float(p[1])))
                    except (TypeError, ValueError):
                        continue

        # Always re-fetch full OSRM geometry for medium+ legs so map follows roads
        # (client may send an over-thinned preview polyline).
        drive_guess = _polyline_length_km(poly_latlon)
        pts = len(poly_latlon)
        spacing = (drive_guess / max(pts - 1, 1)) if pts >= 2 else 999.0
        need_osrm = pts < 2 or drive_guess >= 20.0 or spacing > 0.08
        if need_osrm:
            try:
                osrm_routes = _osrm_fetch_routes(
                    start_lat, start_lon, end_lat, end_lon, max_alternatives=0,
                )
            except Exception:
                osrm_routes = []
            if osrm_routes:
                geom = (osrm_routes[0].get("geometry") or {}).get("coordinates") or []
                rebuilt = [
                    (float(c[1]), float(c[0]))
                    for c in geom
                    if isinstance(c, (list, tuple)) and len(c) >= 2
                ]
                if len(rebuilt) >= 2:
                    poly_latlon = rebuilt
                    polyline = [[lat, lon] for lat, lon in _display_polyline(rebuilt)]

        # Prefer re-snap from the driven polyline so dual carriageways are
        # filtered server-side (client segment_ids often still include both sides).
        if len(poly_latlon) >= 2:
            # Resolve GIS state from the path (video4 AP primary + TG corridor)
            path_sk = state_key
            start_quick = locate_point_by_district_center(start_lat, start_lon)
            if start_quick and start_quick.get("state_key"):
                path_sk = start_quick["state_key"]
            picked, sid_district, total = _snap_polyline_to_segments(
                poly_latlon,
                state_key=path_sk,
                district_ids=[district_id, *extras],
                status_map=status_map,
                focus=focus,
            )
            if not picked:
                raise ValueError(
                    "Could not snap that route onto district roads. Try Find Routes again."
                )
            # Assigned km should match the driven path, not sum of both OSM sides
            drive_km = _polyline_length_km(poly_latlon)
            if drive_km > 0.2:
                total = drive_km
            # Persist a road-accurate polyline for the map (not sparse chords)
            polyline = [[lat, lon] for lat, lon in _display_polyline(poly_latlon, drive_km)]
        else:
            picked = [str(s) for s in (segment_ids or []) if s]
            sid_district = {}
            total = 0.0
            for did in [district_id, *extras]:
                meta = _segment_meta(state_key, str(did))
                for sid in picked:
                    if sid in meta and sid not in sid_district:
                        sid_district[sid] = str(did)
                        total += float(meta[sid]["length"])
            missing = [s for s in picked if s not in sid_district]
            if missing:
                raise ValueError(f"{len(missing)} selected segment(s) are not in your district roads.")
            picked, sid_district, total = _dedupe_parallel_road_sides(
                picked,
                sid_district,
                state_key=state_key,
                latlon=[(start_lat, start_lon), (end_lat, end_lon)],
            )
            if not picked:
                raise ValueError("No road segments left after filtering dual-carriageway duplicates.")

        for sid in picked:
            if sid in already_ids:
                raise ValueError("That route overlaps roads already in today's assignment.")
        route_meta = {
            "preferred_km": round(total, 2),
            "connector_km": 0,
            "sid_district": sid_district,
            "district_ids_used": sorted(set(sid_district.values())),
            "corridor_km_used": corridor_km,
        }
    else:
        picked, total, route_meta = _pick_continuous_corridor(
            state_key,
            district_id,
            (start_lat, start_lon),
            (end_lat, end_lon),
            corridor_km=corridor_km,
            focus=focus,
            target_km=path_budget,
            status_map=status_map,
            extra_district_ids=extras,
        )
        if not picked or total < 0.2:
            access_msg = _access_error_for_points(
                state_key=state_key,
                district_id=district_id,
                start_lat=start_lat,
                start_lon=start_lon,
                end_lat=end_lat,
                end_lon=end_lon,
                allowed_district_ids=allowed_district_ids,
            )
            if access_msg:
                raise ValueError(access_msg)
            raise ValueError(
                "No continuous road route found between those points in your districts."
            )

    overlap_note = _corridor_overlap_warning(user_id, date, picked)

    sid_district = route_meta.get("sid_district") or {}
    groups: list[tuple[str, list[str]]] = []
    for sid in picked:
        did = str(sid_district.get(sid) or district_id)
        if not groups or groups[-1][0] != did:
            groups.append((did, [sid]))
        else:
            groups[-1][1].append(sid)

    cur_existing = existing
    cur_already_ids = list(already_ids)
    cur_already_km = already_km
    for i, (did, group_ids) in enumerate(groups):
        gdist = get_district(did, state_key) or dist
        gkm = _km_for_segment_ids(state_key, did, group_ids)
        is_first, is_last = i == 0, i == len(groups) - 1
        _persist_leg(
            state=state,
            user_id=user_id,
            date=date,
            state_key=state_key,
            district_id=str(gdist.get("district_id") or did),
            dist=gdist,
            picked=group_ids,
            total=gkm,
            start={"lat": start_lat, "lon": start_lon, "label": start_label} if is_first else {
                "lat": start_lat, "lon": start_lon, "label": f"Continue ({gdist.get('name') or did})",
            },
            end={"lat": end_lat, "lon": end_lon, "label": end_label} if is_last else {
                "lat": end_lat, "lon": end_lon, "label": f"To {gdist.get('name') or did}",
            },
            corridor_km=route_meta.get("corridor_km_used", corridor_km),
            route_meta=route_meta if is_first else {"preferred_km": 0, "connector_km": 0},
            already_km=cur_already_km,
            already_ids=cur_already_ids,
            existing=cur_existing,
            mode="corridor",
        )
        cur_existing = (state.get("daily_assignments", {}).get(date, {}) or {}).get(_user_day_key(user_id))
        cur_already_ids = list((cur_existing or {}).get("segment_ids") or [])
        cur_already_km += gkm

    entry = (state.get("daily_assignments", {}).get(date, {}) or {}).get(_user_day_key(user_id))
    combined_km = cur_already_km
    if isinstance(entry, dict) and polyline:
        clean = []
        for p in polyline:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                try:
                    clean.append([float(p[0]), float(p[1])])
                except (TypeError, ValueError):
                    continue
        if len(clean) >= 2:
            entry["polyline"] = clean
            pk = _polyline_length_km(clean)
            if pk > 0.2:
                entry["route_km"] = round(pk, 2)
                # Report driven path length (matches preview), not dual-side OSM sum
                combined_km = pk if replace else (already_km + pk)
    _save_state(state)

    try:
        from routes import tracking_service
        tracking_service._ASSIGN_CLASS_CACHE.pop((user_id, date), None)
    except Exception:
        pass

    out = {
        "assigned_count": len(picked),
        "leg_km": round(total, 1),
        "total_km": round(combined_km, 1),
        "target_km": daily_target,
        "remaining_km": round(max(0.0, daily_target - combined_km), 1),
        "focus_road_class": focus,
        "corridor_shortfall": combined_km < daily_target,
        "continuous": True,
        "replaced": bool(replace),
        "leg_added": not bool(replace),
        "preferred_km": route_meta.get("preferred_km", 0),
        "connector_km": route_meta.get("connector_km", 0),
        "path_budget_km": round(path_budget, 1),
        "summary": assignment_summary_for_user(user_id, date),
    }
    if overlap_note:
        out["overlap_warning"] = overlap_note
        out["message"] = overlap_note.get("message")
    return out


def generate_auto_track_assignment(
    user_id: int,
    *,
    state_key: str,
    district_id: str,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    start_label: str = "",
    end_label: str = "",
    date: str | None = None,
    allowed_district_ids: list | set | None = None,
) -> dict:
    """Custom free-drive: store start/end only; VG drives any path; GPS vs video verified later."""
    assert_can_create_assignment(user_id, allow_same_day_replace=True)
    date = date or today_ist()
    user_id = int(user_id)
    district_id = str(district_id)
    dist = get_district(district_id, state_key)
    if not dist:
        raise ValueError(f"Unknown district {district_id} in {state_key}")

    # Prefer district of the start point when in allowed set
    allowed_list = [str(x) for x in (allowed_district_ids or [district_id]) if x is not None]
    start_loc = locate_point_in_state(start_lat, start_lon, state_key)
    if start_loc and str(start_loc.get("district_id")) in set(allowed_list):
        district_id = str(start_loc["district_id"])
        sk = start_loc.get("state_key") or state_key
        state_key = sk
        dist = get_district(district_id, state_key) or dist

    state = _load_state()
    _release_assigned_day_for_user(state, user_id, date)
    day = state.setdefault("daily_assignments", {}).setdefault(date, {})
    ukey = _user_day_key(user_id)
    settings = get_settings()
    daily_target = float(settings.get("daily_km", 100))
    straight = round(_haversine_km(start_lat, start_lon, end_lat, end_lon), 2)

    start = {
        "lat": float(start_lat),
        "lon": float(start_lon),
        "label": (start_label or "Start").strip() or "Start",
    }
    end = {
        "lat": float(end_lat),
        "lon": float(end_lon),
        "label": (end_label or "End").strip() or "End",
    }
    entry = {
        "user_id": user_id,
        "district_id": district_id,
        "district_name": dist.get("name"),
        "state_key": state_key,
        "state_id": dist.get("state_id"),
        "segment_ids": [],
        "legs": [],
        "start": start,
        "end": end,
        "polyline": [[start_lat, start_lon], [end_lat, end_lon]],
        "route_km": straight,
        "mode": "auto_track",
        "continuous": False,
        "covered_km": 0.0,
        "covered_by_class": {"nh": 0.0, "sh": 0.0, "mdr": 0.0, "other": 0.0},
        "meta": {"custom": True, "completed": False},
        "created_at": datetime.now(IST).isoformat(),
    }
    day[ukey] = entry
    _save_state(state)

    try:
        from routes import auto_track_service
        auto_track_service.upsert_gps_track(
            user_id,
            gps_points=[{"lat": start_lat, "lon": start_lon}, {"lat": end_lat, "lon": end_lon}],
            start_lat=start_lat,
            start_lon=start_lon,
            end_lat=end_lat,
            end_lon=end_lon,
            start_label=start["label"],
            end_label=end["label"],
            track_date=date,
            append=False,
        )
    except Exception:
        pass

    try:
        from routes import tracking_service
        tracking_service._ASSIGN_CLASS_CACHE.pop((user_id, date), None)
    except Exception:
        pass

    return {
        "assigned_count": 0,
        "leg_km": straight,
        "total_km": straight,
        "target_km": daily_target,
        "mode": "auto_track",
        "custom_route": True,
        "message": (
            "Custom route assigned (start → end). Drive any path between them, "
            "then Capture + upload. GPS trail will be verified against the video GPS log."
        ),
        "summary": assignment_summary_for_user(user_id, date),
        "go_to_capture": True,
    }


def generate_nearest_assignment(
    user_id: int,
    *,
    state_key: str,
    district_id: str,
    lat: float | None = None,
    lon: float | None = None,
    date: str | None = None,
    leg_km: float | None = None,
    allowed_district_ids: list | set | None = None,
) -> dict:
    """Assign a continuous walk from the nearest available (not completed) road."""
    assert_can_create_assignment(user_id, allow_same_day_replace=True)
    date = date or today_ist()
    user_id = int(user_id)
    district_id = str(district_id)
    settings = get_settings()
    target_km = float(settings.get("daily_km", 100))
    focus = focus_to_storage(settings.get("focus_road_class") or "all")
    dist = get_district(district_id, state_key)
    if not dist:
        raise ValueError(f"Unknown district {district_id} in {state_key}")

    state = _load_state()
    day = state.setdefault("daily_assignments", {}).setdefault(date, {})
    ukey = _user_day_key(user_id)
    existing = day.get(ukey) if isinstance(day.get(ukey), dict) else None
    already_ids = list((existing or {}).get("segment_ids") or [])
    already_km = _already_km_for_entry(existing, state_key, district_id)
    remaining = max(0.0, target_km - already_km)
    if remaining <= 0.05:
        return {
            "already_complete": True,
            "message": f"Daily target of {target_km} km already assigned.",
            "summary": assignment_summary_for_user(user_id, date),
        }

    # Nearest adds a modest chunk toward the daily target (not the full remainder in one go)
    budget = min(remaining, 25.0)
    if leg_km is not None:
        try:
            budget = min(remaining, max(0.5, float(leg_km)))
        except (TypeError, ValueError):
            budget = min(remaining, 25.0)

    if lat is None or lon is None:
        prev_end = (existing or {}).get("end") or {}
        if prev_end.get("lat") is not None:
            lat = float(prev_end["lat"])
            lon = float(prev_end["lon"])
        else:
            c = dist.get("center") or STATE_CENTER.get(state_key) or [17.4, 78.5]
            lat, lon = float(c[0]), float(c[1])

    status_map = state.get("segment_status", {})
    meta = _segment_meta(state_key, district_id)
    focus_set = parse_focus_classes(focus)

    candidates = []
    for sid, m in meta.items():
        if status_map.get(sid) in _UNAVAILABLE:
            continue
        ml, mo = m["mid"]
        d = _haversine_km(lat, lon, ml, mo)
        preferred = focus_set is None or m["road_class"] in focus_set
        candidates.append((0 if preferred else 1, d, sid, m))
    if not candidates:
        raise ValueError(
            "No available (not completed/assigned) roads left in this district. "
            "Try another of your districts or wait for admin reset."
        )
    candidates.sort()
    picked = []
    total = 0.0
    route_meta = {}
    seed_sid = None
    seed = None
    for _, _, cand_sid, cand in candidates[:8]:
        start = (cand["mid"][0], cand["mid"][1])
        for bearing in (0.0, 2.1, 4.2):
            end = (
                start[0] + 0.18 * math.cos(bearing),
                start[1] + 0.18 * math.sin(bearing),
            )
            p, t, meta_r = _pick_continuous_corridor(
                state_key, district_id, start, end,
                corridor_km=5.0, focus=focus, target_km=budget, status_map=status_map,
                extra_district_ids=[str(x) for x in (allowed_district_ids or []) if str(x) != district_id],
            )
            if t > total:
                picked, total, route_meta = p, t, meta_r
                seed_sid, seed = cand_sid, cand
            if total >= budget * 0.85:
                break
        if total >= budget * 0.85:
            break

    if not picked or seed is None:
        raise ValueError(
            "Could not grow a continuous route from the nearest available road. "
            "Try picking start and end places instead."
        )

    start = {"lat": seed["mid"][0], "lon": seed["mid"][1], "label": f"Nearest available near {lat:.4f},{lon:.4f}"}
    end_m = meta.get(picked[-1]) or seed
    end = {"lat": end_m["mid"][0], "lon": end_m["mid"][1], "label": f"Auto route ({round(total, 1)} km)"}

    conflict = _corridor_conflict(
        user_id, date, picked, (start["lat"], start["lon"]), (end["lat"], end["lon"]),
    )
    if conflict:
        return {"conflict": True, **conflict, "summary": assignment_summary_for_user(user_id, date)}

    _persist_leg(
        state=state,
        user_id=user_id,
        date=date,
        state_key=state_key,
        district_id=district_id,
        dist=dist,
        picked=picked,
        total=total,
        start=start,
        end=end,
        corridor_km=route_meta.get("corridor_km_used"),
        route_meta=route_meta,
        already_km=already_km,
        already_ids=already_ids,
        existing=existing,
        mode="nearest",
    )
    combined_km = already_km + total
    _save_state(state)
    try:
        from routes import tracking_service
        tracking_service._ASSIGN_CLASS_CACHE.pop((user_id, date), None)
    except Exception:
        pass
    return {
        "assigned_count": len(picked),
        "leg_km": round(total, 1),
        "total_km": round(combined_km, 1),
        "target_km": target_km,
        "remaining_km": round(max(0.0, target_km - combined_km), 1),
        "focus_road_class": focus,
        "leg_added": True,
        "mode": "nearest",
        "seed_segment_id": seed_sid,
        "summary": assignment_summary_for_user(user_id, date),
    }


def generate_manual_assignment(
    user_id: int,
    *,
    state_key: str,
    district_id: str,
    segment_ids: list[str],
    start_label: str = "Manual start",
    end_label: str = "Manual end",
    date: str | None = None,
    allowed_district_ids: list | set | None = None,
) -> dict:
    """Assign an explicitly selected ordered list of road segments (map-drawn route)."""
    assert_can_create_assignment(user_id, allow_same_day_replace=True)
    date = date or today_ist()
    user_id = int(user_id)
    district_id = str(district_id)
    ids = [str(s) for s in (segment_ids or []) if s]
    if len(ids) < 1:
        raise ValueError("Select at least one road segment on the map.")

    dist = get_district(district_id, state_key)
    if not dist:
        raise ValueError(f"Unknown district {district_id} in {state_key}")

    settings = get_settings()
    target_km = float(settings.get("daily_km", 100))
    state = _load_state()
    day = state.setdefault("daily_assignments", {}).setdefault(date, {})
    ukey = _user_day_key(user_id)
    existing = day.get(ukey) if isinstance(day.get(ukey), dict) else None
    already_ids = list((existing or {}).get("segment_ids") or [])
    already_km = _already_km_for_entry(existing, state_key, district_id)
    remaining = max(0.0, target_km - already_km)
    if remaining <= 0.05:
        return {
            "already_complete": True,
            "message": f"Daily target of {target_km} km already assigned.",
            "summary": assignment_summary_for_user(user_id, date),
        }

    status_map = state.get("segment_status", {})
    meta = _segment_meta(state_key, district_id)
    allowed = {str(x) for x in (allowed_district_ids or [district_id])}

    # Allow segments from any allowed district (multi-district manual)
    metas: dict[str, dict] = dict(meta)
    for did in allowed:
        if str(did) == district_id:
            continue
        metas.update(_segment_meta(state_key, str(did)))

    picked = []
    total = 0.0
    preferred_km = 0.0
    connector_km = 0.0
    leg_district = district_id
    for sid in ids:
        if sid in already_ids:
            raise ValueError(f"Segment {sid} is already in today's assignment.")
        if status_map.get(sid) in _UNAVAILABLE:
            raise ValueError(f"Segment {sid} is not available (already assigned/completed).")
        m = metas.get(sid)
        if not m:
            raise ValueError(
                f"Segment {sid} is not in your accessible district road index. "
                "Missing district access may be required."
            )
        seg_did = str(m.get("district_id") or district_id)
        if seg_did not in allowed:
            dname = m.get("district_name") or seg_did
            raise ValueError(
                f"Road is in {dname} (district {seg_did}), which is not on your account. "
                "Ask an admin to grant that district access."
            )
        picked.append(sid)
        total += float(m["length"])
        leg_district = seg_did
        if total >= remaining + 0.5:
            break

    if not picked:
        raise ValueError("No valid segments to assign.")

    start_m = metas[picked[0]]
    end_m = metas[picked[-1]]
    start = {"lat": start_m["mid"][0], "lon": start_m["mid"][1], "label": start_label}
    end = {"lat": end_m["mid"][0], "lon": end_m["mid"][1], "label": end_label}

    conflict = _corridor_conflict(
        user_id, date, picked, (start["lat"], start["lon"]), (end["lat"], end["lon"]),
    )
    if conflict:
        return {"conflict": True, **conflict, "summary": assignment_summary_for_user(user_id, date)}

    leg_dist = get_district(leg_district, state_key) or dist
    route_meta = {"preferred_km": preferred_km, "connector_km": connector_km}
    _persist_leg(
        state=state,
        user_id=user_id,
        date=date,
        state_key=state_key,
        district_id=str(leg_dist.get("district_id") or district_id),
        dist=leg_dist,
        picked=picked,
        total=total,
        start=start,
        end=end,
        corridor_km=None,
        route_meta=route_meta,
        already_km=already_km,
        already_ids=already_ids,
        existing=existing,
        mode="manual",
    )
    combined_km = already_km + total
    _save_state(state)
    try:
        from routes import tracking_service
        tracking_service._ASSIGN_CLASS_CACHE.pop((user_id, date), None)
    except Exception:
        pass
    return {
        "assigned_count": len(picked),
        "leg_km": round(total, 1),
        "total_km": round(combined_km, 1),
        "target_km": target_km,
        "remaining_km": round(max(0.0, target_km - combined_km), 1),
        "leg_added": True,
        "mode": "manual",
        "summary": assignment_summary_for_user(user_id, date),
    }


def apply_covered_km_mirror(
    user_id: int,
    date: str,
    covered_km: float,
    covered_by_class: dict | None = None,
) -> bool:
    """Update in-memory survey cache for covered km (Postgres already written).

    Used by tracking_service.persist_coverage_canonical after DB updates.
    """
    date = date or today_ist()
    state = _load_state()
    entry = (state.get("daily_assignments", {}).get(date, {}) or {}).get(_user_day_key(user_id))
    if not entry or isinstance(entry, list):
        return False
    entry["covered_km"] = round(float(covered_km), 2)
    if covered_by_class is not None:
        entry["covered_by_class"] = {
            k: round(float((covered_by_class or {}).get(k) or 0), 3)
            for k in ("nh", "sh", "mdr", "other")
        }
    _cache_survey_state(state)
    if _json_mirror_enabled():
        _save_state(state, persist_db=False)
    return True


def update_covered_km(
    user_id: int,
    covered_km: float,
    date: str | None = None,
    covered_by_class: dict | None = None,
) -> dict:
    """Update covered distance — delegates to canonical persist (both DB tables)."""
    date = date or today_ist()
    state = _load_state()
    entry = (state.get("daily_assignments", {}).get(date, {}) or {}).get(_user_day_key(user_id))
    if not entry or isinstance(entry, list):
        return {"updated": False}

    new_km = round(float(covered_km), 2)
    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
    route_done = bool(
        meta.get("completed")
        or (meta.get("start_reached") and meta.get("end_reached"))
    )
    prev_km = float(entry.get("covered_km") or 0)
    if route_done and new_km + 0.05 < prev_km:
        new_km = prev_km
        if covered_by_class is None:
            covered_by_class = entry.get("covered_by_class")

    cbc = covered_by_class if covered_by_class is not None else entry.get("covered_by_class")
    try:
        from routes import tracking_service as _ts
        new_km, cbc = _ts.persist_coverage_canonical(int(user_id), date, new_km, cbc)
    except Exception:
        apply_covered_km_mirror(int(user_id), date, new_km, cbc)

    _maybe_mark_assignment_complete(user_id, date, entry)
    return {"updated": True, "summary": assignment_summary_for_user(user_id, date)}


def _entry_has_assignment_work(entry: dict | None) -> bool:
    if not isinstance(entry, dict):
        return False
    if _entry_segment_ids(entry):
        return True
    if entry.get("start") and entry.get("end"):
        return True
    poly = entry.get("polyline")
    return isinstance(poly, list) and len(poly) >= 2


def assignments_overview(date: str | None = None) -> dict:
    date = date or today_ist()
    today = today_ist()
    state = _load_state()
    day = state.get("daily_assignments", {}).get(date, {}) or {}
    target_km = float(get_settings().get("daily_km", 100))
    items = []
    seen_uids: set[int] = set()

    for key, entry in day.items():
        if not _entry_has_assignment_work(entry):
            continue
        uid = int(entry.get("user_id") or key.lstrip("u"))
        summary = assignment_summary_for_user(uid, date)
        items.append({
            **summary,
            "videographer_user_id": uid,
            "assignment_date": date,
            "is_carryover": False,
        })
        seen_uids.add(uid)

    carryover_items = []
    # When viewing today, also surface incomplete prior-day quotas so admins
    # (and the day table) see work that still blocks the VG.
    if date == today:
        daily = state.get("daily_assignments", {}) or {}
        for date_key in sorted(daily.keys()):
            if date_key >= today:
                continue
            prior_day = daily.get(date_key, {}) or {}
            for key, entry in prior_day.items():
                if not _entry_has_assignment_work(entry):
                    continue
                uid = int(entry.get("user_id") or key.lstrip("u"))
                probe = dict(entry)
                probe["assignment_date"] = date_key
                probe["date"] = date_key
                probe["user_id"] = uid
                if is_assignment_complete(probe):
                    continue
                summary = assignment_summary_for_user(uid, date_key)
                row = {
                    **summary,
                    "videographer_user_id": uid,
                    "assignment_date": date_key,
                    "is_carryover": True,
                    "date": date_key,
                }
                carryover_items.append(row)
                if uid not in seen_uids:
                    items.append(row)
                    seen_uids.add(uid)

    return {
        "date": date,
        "target_km": target_km,
        "items": items,
        "carryover_items": carryover_items,
        "active_count": len(items),
    }


def assignment_history_summary(*, include_items: bool = True) -> list[dict]:
    """Per-day rollup. Set include_items=False for fast Tracking date lists."""
    state = _load_state()
    daily = state.get("daily_assignments", {})
    out = []
    for date_key in sorted(daily.keys(), reverse=True):
        users = daily.get(date_key) or {}
        user_rows = []
        seg_count = 0
        user_count = 0
        for k, v in list(users.items()):
            if not _entry_has_assignment_work(v):
                continue
            user_count += 1
            seg_count += len(_entry_segment_ids(v))
            if include_items:
                uid = int(v.get("user_id") or str(k).lstrip("u") or 0)
                summary = assignment_summary_for_user(
                    uid, date_key, resolve_carryover=False, light=True,
                )
                user_rows.append({
                    **summary,
                    "videographer_user_id": uid,
                    "assignment_date": date_key,
                })
        if user_count == 0:
            continue
        row = {
            "date": date_key,
            "user_count": user_count,
            "segment_count": seg_count,
        }
        if include_items:
            row["items"] = user_rows
        out.append(row)
    return out


def clear_daily_assignment_for_user(user_id: int, date: str | None = None) -> dict:
    """Remove the active assignment for the day, but archive it for later upload seal.

    Soft-clear: keep start/end/polyline/segments in ``survey_cleared_assignments``
    so a video recorded on this route (saved offline) can still seal/cover against
    *this* corridor after the VG assigns a new route.
    """
    date = date or today_ist()
    user_id = int(user_id)
    state = _load_state()
    daily = state.setdefault("daily_assignments", {})
    day = daily.get(date, {})
    ukey = _user_day_key(user_id)
    entry = day.pop(ukey, None)

    if not entry or not _entry_has_assignment_work(entry if isinstance(entry, dict) else None):
        return {
            "cleared": False,
            "user_id": user_id,
            "date": date,
            "message": "No assignment found for this videographer on the selected date.",
        }

    segment_ids = list(_entry_segment_ids(entry)) if isinstance(entry, dict) else list(entry or [])
    status_map = state.setdefault("segment_status", {})
    reset_count = 0
    for sid in segment_ids:
        # Only free "assigned" back to available — completed stays completed until admin reopen
        if status_map.get(sid) == "assigned":
            status_map[sid] = "available"
            reset_count += 1

    archive_id = None
    if isinstance(entry, dict):
        try:
            import db_utils
            archive_id = db_utils.survey_db_archive_cleared_assignment(user_id, date, entry)
        except Exception:
            archive_id = None
        # JSON mirror for environments without the new table yet / offline seal match
        archives = state.setdefault("cleared_archives", {})
        ulist = archives.setdefault(str(user_id), [])
        if not isinstance(ulist, list):
            ulist = []
            archives[str(user_id)] = ulist
        ulist.append({
            "id": archive_id,
            "date": date,
            "cleared_at": datetime.now(IST).isoformat(),
            "entry": entry,
            "consumed": False,
        })
        # Cap history so survey_state.json does not grow forever
        if len(ulist) > 30:
            archives[str(user_id)] = ulist[-30:]

    if day:
        daily[date] = day
    elif date in daily:
        del daily[date]
    _save_state(state)
    return {
        "cleared": True,
        "user_id": user_id,
        "date": date,
        "segment_count": len(segment_ids),
        "segments_reset": reset_count,
        "archive_id": archive_id,
        "message": (
            f"Cleared {len(segment_ids)} segment(s) for user {user_id} on {date}."
            if segment_ids
            else f"Cleared custom route for user {user_id} on {date}."
        ) + (
            " Route kept for later upload coverage."
            if archive_id or isinstance(entry, dict)
            else ""
        ),
    }


def clear_active_assignment_for_user(user_id: int, date: str | None = None) -> dict:
    """Clear the assignment the VG is working on (explicit date, open carryover, or today)."""
    user_id = int(user_id)
    candidates: list[str] = []
    if date:
        candidates.append(str(date)[:10])
    open_asg = find_open_incomplete_assignment(user_id)
    if open_asg and open_asg.get("date"):
        candidates.append(str(open_asg["date"])[:10])
    try:
        work = resolve_work_date_for_user(user_id, today_ist())
        if work:
            candidates.append(str(work)[:10])
    except Exception:
        pass
    candidates.append(today_ist())

    seen: set[str] = set()
    last = {
        "cleared": False,
        "user_id": user_id,
        "date": (str(date)[:10] if date else today_ist()),
        "message": "No assignment found for this videographer.",
    }
    for d in candidates:
        if not d or d in seen:
            continue
        seen.add(d)
        last = clear_daily_assignment_for_user(user_id, d)
        if last.get("cleared"):
            return last
    return last


# Soft-clear / upload match: 250 m buffer around corridor + endpoints
MATCH_CORRIDOR_BUFFER_KM = 0.25
MATCH_ENDPOINT_BUFFER_KM = 0.25
MATCH_CLEARED_MIN_SCORE = 0.22


def _gps_points_for_seal(
    user_id: int,
    date: str,
    *,
    gps_log_path: str | None = None,
    gps_points: list | None = None,
    include_live_trail: bool = False,
) -> list[tuple[float, float]]:
    """Build GPS points for sealing roads.

    When an upload GPS log is present it is the source of truth (saved-local
    re-upload and direct upload). Live trail is only merged when explicitly
    requested — provisional discarded sessions must not seal roads.
    """
    out: list[tuple[float, float]] = []
    seen: set[tuple[float, float]] = set()

    def _add(lat, lon):
        try:
            la, lo = float(lat), float(lon)
        except (TypeError, ValueError):
            return
        if not (-90 <= la <= 90 and -180 <= lo <= 180):
            return
        key = (round(la, 5), round(lo, 5))
        if key in seen:
            return
        seen.add(key)
        out.append((la, lo))

    if gps_points:
        for p in gps_points:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                _add(p[0], p[1])
            elif isinstance(p, dict):
                _add(p.get("lat"), p.get("lon") if p.get("lon") is not None else p.get("lng"))

    if gps_log_path:
        try:
            from pothole_detector import load_gps_log
            for lat, lon, _ts in (load_gps_log(gps_log_path) or {}).values():
                _add(lat, lon)
        except Exception:
            pass

    # Only fall back to committed trail when no upload GPS was provided
    if include_live_trail or not (gps_log_path or gps_points):
        try:
            from routes import tracking_service
            tr = tracking_service._load()
            entry = (tr.get("videographers") or {}).get(str(int(user_id))) or {}
            if entry.get("date") == date:
                for p in entry.get("trail") or []:
                    if not isinstance(p, dict):
                        continue
                    # Skip provisional (not yet uploaded) points
                    if p.get("committed") is False:
                        continue
                    _add(p.get("lat"), p.get("lon"))
        except Exception:
            pass

    return out


def _assigned_segment_geoms(entry: dict, ids: list[str]) -> dict[str, tuple[dict | None, float]]:
    """sid -> (geometry, length_km) for assigned segments."""
    id_set = {str(s) for s in ids}
    out: dict[str, tuple[dict | None, float]] = {}
    for state_key, district_id in _assignment_scopes(entry):
        for feat in _segment_features(state_key, district_id):
            props = feat.get("properties") or {}
            sid = _segment_id(props, district_id)
            if sid not in id_set or sid in out:
                continue
            out[sid] = (feat.get("geometry"), float(props.get("length_km") or 0))
    return out


def _gps_assignment_match_score(
    entry: dict | None,
    gps_pts: list[tuple[float, float]],
    *,
    snap_km: float | None = None,
    endpoint_km: float | None = None,
) -> float:
    """0..1 how well an uploaded GPS trail matches an assignment corridor.

    Default buffers are 250 m (corridor + endpoints) so GPS jitter / parallel
    carriageways still match a soft-cleared route after Clear survey.
    """
    if not isinstance(entry, dict) or len(gps_pts) < 2:
        return 0.0
    snap = float(MATCH_CORRIDOR_BUFFER_KM if snap_km is None else snap_km)
    end_buf = float(MATCH_ENDPOINT_BUFFER_KM if endpoint_km is None else endpoint_km)

    ref: list[tuple[float, float]] = []
    poly = entry.get("polyline") or []
    if isinstance(poly, list):
        for p in poly:
            try:
                if isinstance(p, dict):
                    ref.append((float(p["lat"]), float(p["lon"])))
                elif isinstance(p, (list, tuple)) and len(p) >= 2:
                    ref.append((float(p[0]), float(p[1])))
            except (TypeError, ValueError, KeyError):
                continue
    if len(ref) < 2:
        start = _entry_pin_latlon(entry, "start")
        end = _entry_pin_latlon(entry, "end")
        if start and end:
            ref = [start, end]
    if len(ref) < 2:
        return 0.0

    step = max(1, len(gps_pts) // 80)
    samples = gps_pts[::step]
    if samples[-1] != gps_pts[-1]:
        samples.append(gps_pts[-1])

    hits = 0
    for glat, glon in samples:
        best = 1e9
        for i in range(len(ref) - 1):
            a_lat, a_lon = ref[i]
            b_lat, b_lon = ref[i + 1]
            d = min(
                _haversine_km(glat, glon, a_lat, a_lon),
                _haversine_km(glat, glon, b_lat, b_lon),
                _haversine_km(glat, glon, (a_lat + b_lat) / 2, (a_lon + b_lon) / 2),
            )
            if d < best:
                best = d
            if best <= snap:
                break
        if best <= snap:
            hits += 1
    frac = hits / max(1, len(samples))

    bonus = 0.0
    start = _entry_pin_latlon(entry, "start")
    end = _entry_pin_latlon(entry, "end")
    if start and _haversine_km(gps_pts[0][0], gps_pts[0][1], start[0], start[1]) <= end_buf:
        bonus += 0.08
    if end and _haversine_km(gps_pts[-1][0], gps_pts[-1][1], end[0], end[1]) <= end_buf:
        bonus += 0.08
    return min(1.0, frac + bonus)


def _list_cleared_assignment_candidates(user_id: int) -> list[dict]:
    """Open soft-cleared archives (DB + JSON mirror)."""
    out: list[dict] = []
    seen_ids: set[int] = set()
    try:
        import db_utils
        for row in db_utils.survey_db_list_open_cleared_assignments(int(user_id)) or []:
            aid = row.get("id")
            if aid is not None:
                seen_ids.add(int(aid))
            out.append(row)
    except Exception:
        pass
    try:
        state = _load_state()
        for row in (state.get("cleared_archives") or {}).get(str(int(user_id)), []) or []:
            if not isinstance(row, dict) or row.get("consumed"):
                continue
            aid = row.get("id")
            if aid is not None and int(aid) in seen_ids:
                continue
            entry = row.get("entry")
            if not isinstance(entry, dict):
                continue
            out.append({
                "id": aid,
                "date": str(row.get("date") or "")[:10],
                "cleared_at": row.get("cleared_at"),
                "entry": entry,
                "from_json": True,
            })
    except Exception:
        pass
    return out


def _resolve_seal_target(
    user_id: int,
    date: str,
    gps_pts: list[tuple[float, float]],
    active_entry: dict | None,
) -> dict:
    """Pick active vs soft-cleared assignment for this GPS upload."""
    candidates: list[tuple[str, int | None, str, dict, float]] = []
    if isinstance(active_entry, dict) and _entry_has_assignment_work(active_entry):
        score = _gps_assignment_match_score(active_entry, gps_pts)
        candidates.append(("active", None, date, active_entry, score))

    for row in _list_cleared_assignment_candidates(user_id):
        entry = row.get("entry")
        if not isinstance(entry, dict):
            continue
        score = _gps_assignment_match_score(entry, gps_pts)
        aid = row.get("id")
        try:
            aid_i = int(aid) if aid is not None else None
        except (TypeError, ValueError):
            aid_i = None
        adate = str(row.get("date") or date)[:10] or date
        candidates.append(("cleared", aid_i, adate, entry, score))

    if not candidates:
        return {"kind": "none", "archive_id": None, "date": date, "entry": None, "score": 0.0}

    candidates.sort(key=lambda t: t[4], reverse=True)
    kind, aid, adate, entry, score = candidates[0]

    # Prefer a strong cleared match over a weak active match (A uploaded after B assigned).
    if kind == "active" and score < MATCH_CLEARED_MIN_SCORE:
        for k, a, d, e, s in candidates[1:]:
            if k == "cleared" and s >= MATCH_CLEARED_MIN_SCORE and s > score + 0.05:
                kind, aid, adate, entry, score = k, a, d, e, s
                break

    return {
        "kind": kind,
        "archive_id": aid,
        "date": adate,
        "entry": entry,
        "score": round(float(score), 3),
    }


def _mark_cleared_archive_consumed(user_id: int, archive_id: int | None, note: str) -> None:
    if archive_id is not None:
        try:
            import db_utils
            db_utils.survey_db_consume_cleared_assignment(int(archive_id), note)
        except Exception:
            pass
    try:
        state = _load_state()
        ulist = (state.get("cleared_archives") or {}).get(str(int(user_id)), [])
        if not isinstance(ulist, list):
            return
        changed = False
        for row in ulist:
            if not isinstance(row, dict):
                continue
            rid = row.get("id")
            if archive_id is not None and rid is not None and int(rid) == int(archive_id):
                row["consumed"] = True
                row["consumed_note"] = note
                changed = True
                break
            # JSON-only archives (no db id): consume by matching first open row
            if archive_id is None and not row.get("consumed") and not row.get("id"):
                row["consumed"] = True
                row["consumed_note"] = note
                changed = True
                break
        if changed:
            _save_state(state, persist_db=False)
    except Exception:
        pass


def mark_user_assignment_completed(
    user_id: int,
    date: str | None = None,
    *,
    gps_log_path: str | None = None,
    gps_points: list | None = None,
) -> dict:
    """Seal only assigned segments that today's GPS actually covered.

    Uncovered assigned segments stay ``assigned`` so the VG can finish and
    upload again. Already ``completed`` / ``verified`` roads are left alone.

    Soft-cleared routes: if GPS matches an archived (cleared) assignment better
    than the current active one, seal that corridor and consume the archive.
    """
    date = date or today_ist()
    user_id = int(user_id)
    state = _load_state()
    active = (state.get("daily_assignments", {}).get(date, {}) or {}).get(_user_day_key(user_id))
    if active is not None and not isinstance(active, dict):
        active = None

    gps_pts = _gps_points_for_seal(
        user_id, date, gps_log_path=gps_log_path, gps_points=gps_points
    )
    if not gps_pts:
        return {
            "updated": False,
            "count": 0,
            "total_assigned": len(list(_entry_segment_ids(active))) if isinstance(active, dict) else 0,
            "skipped": 0,
            "gps_points": 0,
            "date": date,
            "user_id": user_id,
            "message": "No GPS points to seal roads — upload a GPS log or record while capturing.",
        }

    target = _resolve_seal_target(user_id, date, gps_pts, active)
    entry = target.get("entry")
    seal_date = str(target.get("date") or date)[:10]
    if not isinstance(entry, dict):
        return {
            "updated": False,
            "count": 0,
            "total_assigned": 0,
            "skipped": 0,
            "gps_points": len(gps_pts),
            "date": date,
            "user_id": user_id,
            "match_kind": target.get("kind"),
            "match_score": target.get("score"),
            "message": "No assignment found for today.",
        }

    ids = list(_entry_segment_ids(entry))
    start = _entry_pin_latlon(entry, "start")
    end = _entry_pin_latlon(entry, "end")
    if start and end:
        fake = [{"lat": a, "lon": b} for a, b in gps_pts]
        trunc = _truncate_trail_start_to_end(fake, start, end)
        if len(trunc) >= 2:
            gps_pts = trunc

    geoms = _assigned_segment_geoms(entry, ids)
    status_map = state.setdefault("segment_status", {})
    sealed: list[str] = []
    already = 0
    for sid in ids:
        st = status_map.get(sid)
        if st in ("completed", "verified"):
            already += 1
            continue
        geom, length_km = geoms.get(sid, (None, 0.0))
        if not _segment_covered_by_gps(geom, length_km, gps_pts):
            continue
        status_map[sid] = "completed"
        sealed.append(sid)

    if sealed:
        # If sealing the active day entry, keep meta on that object in state
        if target.get("kind") == "active" and isinstance(active, dict):
            day_map = state.setdefault("daily_assignments", {}).setdefault(date, {})
            day_map[_user_day_key(user_id)] = active
        _save_state(state, persist_db=False)
        _schedule_survey_db_sync(state, delay_s=2.5)

    # Driven-path seal only for the *active* corridor (avoid greying B from A's GPS midpoints)
    if target.get("kind") == "active":
        try:
            driven = seal_driven_path_until_endpoint(user_id, date)
            if driven.get("count"):
                sealed = list(dict.fromkeys(list(sealed) + list(driven.get("segment_ids") or [])))
        except Exception:
            pass

    skipped = len(ids) - len([s for s in sealed if s in ids]) - already
    if target.get("kind") == "active" and isinstance(entry, dict):
        sealed_km, total_km = _assigned_sealed_and_total_km(entry)
        if (
            ids
            and total_km >= 0.5
            and sealed_km >= max(0.0, total_km - ASSIGN_COMPLETE_REMAINING_KM)
        ):
            meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
            meta = dict(meta)
            meta["completed"] = True
            meta["completed_at"] = datetime.now(IST).isoformat()
            meta["complete_reason"] = "seal_within_1km"
            entry["meta"] = meta
            _save_state(state, persist_db=False)
            _schedule_survey_db_sync(state, delay_s=2.5)
        else:
            _maybe_mark_assignment_complete(user_id, date, entry)

    if target.get("kind") == "cleared" and float(target.get("score") or 0) >= MATCH_CLEARED_MIN_SCORE:
        _mark_cleared_archive_consumed(
            user_id,
            target.get("archive_id"),
            note=f"sealed={len(sealed)} score={target.get('score')} gps={len(gps_pts)}",
        )

    kind = target.get("kind")
    score = target.get("score")
    msg = (
        f"Sealed {len(sealed)} road(s) from GPS"
        + (" (matched cleared route)" if kind == "cleared" else " (assigned + driven path to end)")
        + (f" ({max(0, skipped)} assigned still open)." if skipped > 0 else ".")
    )
    return {
        "updated": bool(sealed),
        "count": len(sealed),
        "total_assigned": len(ids),
        "skipped": max(0, skipped),
        "already_completed": already,
        "segment_ids": sealed,
        "gps_points": len(gps_pts),
        "date": seal_date,
        "user_id": user_id,
        "match_kind": kind,
        "match_score": score,
        "archive_id": target.get("archive_id"),
        "message": msg,
    }


def reopen_district_roads(
    *,
    state_key: str,
    district_id: str | int,
    only_completed: bool = True,
) -> dict:
    """Admin: reopen district roads so they can be assigned again."""
    district_id = str(district_id)
    state = _load_state()
    status_map = state.setdefault("segment_status", {})
    prefix = f"{district_id}_"
    reset = 0
    for sid, st in list(status_map.items()):
        if not (str(sid).startswith(prefix) or str(sid).startswith(f"{district_id}-")):
            # Also match ids that embed district another way: "{district_id}_..."
            meta_ok = False
            try:
                # segment ids are typically "{districtId}_{osmWayId}_..."
                if str(sid).split("_", 1)[0] == district_id:
                    meta_ok = True
            except Exception:
                pass
            if not meta_ok:
                continue
        if only_completed and st not in ("completed", "verified"):
            continue
        if st in ("completed", "assigned", "verified"):
            status_map[sid] = "available"
            reset += 1
    _save_state(state)
    dist = get_district(district_id, state_key) or {}
    return {
        "ok": True,
        "district_id": district_id,
        "district_name": dist.get("name"),
        "segments_reopened": reset,
        "message": (
            f"Reopened {reset} road segment(s) in {dist.get('name') or district_id} "
            f"for new assignments."
        ),
    }


def is_segment_completed(segment_id: str, status_map: dict | None = None) -> bool:
    if status_map is None:
        status_map = _load_state().get("segment_status", {})
    return (status_map.get(segment_id) or status_map.get(str(segment_id))) in ("completed", "verified")


def all_assigned_routes_geojson_today(date: str | None = None) -> dict:
    """FeatureCollection of every VG's assigned roads for the date (Tracking overview).

    When ``date`` is today, also includes incomplete prior-day (carryover) routes.
    """
    date = date or today_ist()
    today = today_ist()
    state = _load_state()
    features = []
    seen: set[tuple[int, str]] = set()

    def _append_day(day_date: str, *, carryover: bool = False) -> None:
        day = state.get("daily_assignments", {}).get(day_date, {}) or {}
        for entry in day.values():
            if not isinstance(entry, dict):
                continue
            uid = entry.get("user_id")
            if not uid:
                continue
            uid = int(uid)
            if not _entry_has_assignment_work(entry):
                continue
            if carryover:
                probe = dict(entry)
                probe["assignment_date"] = day_date
                probe["date"] = day_date
                probe["user_id"] = uid
                if is_assignment_complete(probe):
                    continue
            key = (uid, day_date)
            if key in seen:
                continue
            seen.add(key)
            try:
                geo = assigned_segments_geojson_for_user(uid, day_date, resolve_carryover=False)
            except Exception:
                continue
            for f in geo.get("features") or []:
                props = dict(f.get("properties") or {})
                props["videographer_user_id"] = uid
                props["videographer_name"] = entry.get("videographer_name") or entry.get("username")
                props["assignment_date"] = day_date
                props["is_carryover"] = bool(carryover)
                features.append({**f, "properties": props})

    _append_day(date, carryover=False)
    if date == today:
        daily = state.get("daily_assignments", {}) or {}
        for date_key in sorted(daily.keys()):
            if date_key >= today:
                continue
            _append_day(date_key, carryover=True)

    return {"type": "FeatureCollection", "features": features, "date": date}
