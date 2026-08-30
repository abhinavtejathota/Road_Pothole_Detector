"""tracking.capture — extends coverage (includes private _names)."""
from __future__ import annotations

import routes.tracking.coverage as _coverage

globals().update({k: v for k, v in vars(_coverage).items() if not k.startswith('__')})

def record_ping(
    *,
    user_id: int,
    username: str,
    full_name: str | None,
    state_id: int | None = None,
    district_id: int | None = None,
    lat: float,
    lon: float,
    accuracy: float | None = None,
    recording: bool = True,
    capture_session_id: str | None = None,
) -> dict:
    """Live GPS ping.

    While ``recording`` with a ``capture_session_id``, trail points are
    *provisional* (``committed=False``). Discard/save removes them; upload
    commits them. Distance only counts when snapped to incomplete assigned roads.

    Pings for a capture session whose chunk finalize is queued/running/done are
    ignored so late keepalive or offline retries cannot overwrite final metrics.
    """
    date = today_ist()
    point = {
        "lat": float(lat),
        "lon": float(lon),
        "accuracy": float(accuracy) if accuracy is not None else None,
        "ts": _now_iso(),
    }
    sid = (capture_session_id or "").strip() or None
    key = str(int(user_id))

    if recording and sid:
        try:
            from routes import field_upload_service as _fus
            if _fus.is_chunk_session_finalizing(username=username, session_id=sid):
                return {
                    "ok": True,
                    "ignored": True,
                    "reason": "session_finalized",
                    "date": date,
                    "capture_session_id": sid,
                    "last": point,
                }
        except Exception:
            pass

    # Unlocked peek is fine for snap geometry; mutate path re-checks under lock.
    last_trail = None
    session_tail: list = []
    if recording and sid:
        try:
            entry_peek = _entry_for_mutation(int(user_id), date)
            for p in list(entry_peek.get("trail") or []):
                if isinstance(p, dict) and p.get("capture_session_id") == sid:
                    session_tail.append(p)
            if session_tail:
                last_trail = session_tail[-1]
        except Exception:
            last_trail = None
            session_tail = []

    new_point = None
    if recording and sid:
        if not last_trail or last_trail.get("ts") != point["ts"]:
            delta = 0.0
            rc = None
            try:
                from routes import survey_service as _sv
                _sv.note_start_pin_if_near(int(user_id), date, point["lat"], point["lon"])
                flags = _sv.assignment_coverage_flags(int(user_id), date)
            except Exception:
                flags = {"allow_count": True, "has_assignment": False}

            if session_tail:
                try:
                    delta, a_lat, a_lon = _live_ping_delta_km(
                        session_tail,
                        point["lat"],
                        point["lon"],
                        _parse_ts(point.get("ts")),
                    )
                except (TypeError, ValueError):
                    delta, a_lat, a_lon = 0.0, None, None
                if delta > 0 and a_lat is not None and a_lon is not None:
                    mid_lat = (a_lat + point["lat"]) / 2
                    mid_lon = (a_lon + point["lon"]) / 2
                    allow = flags.get("allow_count", True)
                    if flags.get("has_assignment") and not allow:
                        delta = 0.0
                        rc = None
                    else:
                        rc = _live_road_class_or_custom(int(user_id), date, mid_lat, mid_lon)
                        if not rc:
                            delta = 0.0
                else:
                    delta = 0.0
            new_point = {
                "lat": point["lat"],
                "lon": point["lon"],
                "ts": point["ts"],
                "capture_session_id": sid,
                "committed": False,
                "delta_km": round(float(delta or 0), 5),
                "road_class": rc,
            }

    append_points: list = []
    with _STATE_LOCK:
        with _cross_process_lock():
            entry = _entry_for_mutation(int(user_id), date)
            entry.update({
                "user_id": int(user_id),
                "username": username,
                "full_name": full_name or username,
                "state_id": int(state_id) if state_id else entry.get("state_id"),
                "district_id": int(district_id) if district_id else entry.get("district_id"),
                "date": date,
                "recording": bool(recording),
                "last": point,
            })
            trail = list(entry.get("trail") or [])
            for p in trail:
                if isinstance(p, dict) and "committed" not in p:
                    p["committed"] = True
                    p.setdefault("delta_km", 0.0)

            if new_point is not None:
                # Re-check under lock so a concurrent ping for the same ts is not duplicated.
                if not trail or (isinstance(trail[-1], dict) and trail[-1].get("ts") != new_point["ts"]):
                    trail.append(new_point)
                    append_points = [new_point]
                if len(trail) > MAX_TRAIL_POINTS:
                    while len(trail) > MAX_TRAIL_POINTS:
                        drop_i = next(
                            (
                                i for i, p in enumerate(trail)
                                if isinstance(p, dict) and not p.get("committed")
                            ),
                            0,
                        )
                        trail.pop(drop_i)

            entry["trail"] = trail
            covered, by_class = _sum_trail_coverage(trail)
            entry["covered_km"] = covered
            entry["covered_by_class"] = by_class
            if _tracking_json_mirror_enabled():
                state = _read_json()
                vids = state.setdefault("videographers", {})
                vids[key] = entry
                _write_json(state)

    # Persist DB + survey mirror outside locks so uploads are not blocked.
    try:
        import db_utils
        if db_utils.is_db_configured():
            db_utils.tracking_db_append_ping(entry, append_points)
    except Exception:
        pass

    # Survey + auto_track are best-effort — never delay the ping ACK.
    uid = int(user_id)
    covered_km = float(entry.get("covered_km") or 0)
    covered_by_class = dict(entry.get("covered_by_class") or {})
    gps_one = {
        "lat": point["lat"],
        "lon": point["lon"],
        "ts": point["ts"],
        "accuracy": point.get("accuracy"),
    }

    def _bg_side_effects():
        try:
            from routes import survey_service as _sv
            _sv.note_assignment_pin_visit(
                uid, date, point["lat"], point["lon"], trail=list(entry.get("trail") or []),
            )
        except Exception:
            pass
        # Persist road-snapped coverage (not raw GPS trail sum) for KPIs / DB
        try:
            resolved_km, resolved_by = _persist_resolved_coverage(uid, entry, date)
            covered_km = resolved_km
            covered_by_class = resolved_by
        except Exception:
            try:
                _sync_assignment_covered(uid, {
                    "covered_km": covered_km,
                    "covered_by_class": covered_by_class,
                }, date)
            except Exception:
                pass
        if recording:
            try:
                from routes import auto_track_service
                auto_track_service.upsert_gps_track(
                    uid,
                    gps_points=[gps_one],
                    track_date=date,
                    append=True,
                )
            except Exception:
                pass

    _ping_side_pool().submit(_bg_side_effects)

    return {
        "ok": True,
        "live": _is_live(entry),
        "points": len(trail),
        "covered_km": entry["covered_km"],
        "covered_by_class": entry["covered_by_class"],
        "capture_session_id": sid,
        "last": point,
    }


def discard_capture_session(user_id: int, capture_session_id: str) -> dict:
    """Remove provisional trail for a capture session (discard or save-local).

    Committed (uploaded) points are never removed.
    """
    sid = (capture_session_id or "").strip()
    if not sid:
        return {"ok": False, "error": "capture_session_id required", "removed": 0}

    date = today_ist()
    with _STATE_LOCK:
        with _cross_process_lock():
            entry = _entry_for_mutation(int(user_id), date)
            trail = list(entry.get("trail") or [])
            kept = []
            removed = 0
            for p in trail:
                if not isinstance(p, dict):
                    kept.append(p)
                    continue
                if p.get("capture_session_id") == sid and not p.get("committed"):
                    removed += 1
                    continue
                kept.append(p)

            entry["trail"] = kept
            entry["recording"] = False
            covered, by_class = _sum_trail_coverage(kept)
            entry["covered_km"] = covered
            entry["covered_by_class"] = by_class
            if _tracking_json_mirror_enabled():
                state = _read_json()
                state.setdefault("videographers", {})[str(int(user_id))] = entry
                _write_json(state)
    try:
        covered, by_class = _persist_resolved_coverage(int(user_id), entry, date)
    except Exception:
        try:
            import db_utils
            if db_utils.is_db_configured():
                db_utils.tracking_db_save_user_entry(entry)
        except Exception:
            pass
        _sync_assignment_covered(int(user_id), entry, date)
    _ASSIGN_CLASS_CACHE.clear()

    return {
        "ok": True,
        "removed": removed,
        "points": len(kept),
        "covered_km": covered,
        "covered_by_class": by_class,
        "capture_session_id": sid,
        "message": (
            f"Removed provisional coverage for this capture ({removed} GPS point(s)). "
            "Upload the saved video + GPS later to count it again."
            if removed
            else "No provisional points for this capture session."
        ),
    }


def commit_capture_session(user_id: int, capture_session_id: str | None = None) -> dict:
    """Mark a capture session's trail as committed after a successful upload.

    Must be serialized with record_ping() to avoid read/modify/write races
    that can leave points provisional and break assignment completion.
    """
    date = today_ist()
    with _STATE_LOCK:
        with _cross_process_lock():
            entry = _entry_for_mutation(int(user_id), date)
            trail = list(entry.get("trail") or [])
            sid = (capture_session_id or "").strip() or None
            marked = 0
            for p in trail:
                if not isinstance(p, dict):
                    continue
                if sid and p.get("capture_session_id") != sid:
                    continue
                if sid is None and p.get("committed"):
                    continue
                if not p.get("committed"):
                    p["committed"] = True
                    marked += 1
            entry["trail"] = trail
            entry["recording"] = False
            covered, by_class = _sum_trail_coverage(trail)
            entry["covered_km"] = covered
            entry["covered_by_class"] = by_class
            if _tracking_json_mirror_enabled():
                state = _read_json()
                state.setdefault("videographers", {})[str(int(user_id))] = entry
                _write_json(state)
    try:
        covered, by_class = _persist_resolved_coverage(int(user_id), entry, date)
    except Exception:
        try:
            import db_utils
            if db_utils.is_db_configured():
                db_utils.tracking_db_save_user_entry(entry)
        except Exception:
            pass
        _sync_assignment_covered(int(user_id), entry, date)

    return {
        "ok": True,
        "marked": marked,
        "covered_km": covered,
        "covered_by_class": by_class,
        "capture_session_id": sid,
    }


def ingest_gps_log_coverage(user_id: int, gps_log_path: str) -> dict:
    """Add committed coverage from an uploaded GPS log (saved-local → later upload).

    Used when there is no live capture_session_id (Field Upload / re-upload).
    """
    date = today_ist()
    pts: list[tuple[float, float]] = []
    try:
        from pothole_detector import load_gps_log
        for lat, lon, _ts in (load_gps_log(gps_log_path) or {}).values():
            try:
                pts.append((float(lat), float(lon)))
            except (TypeError, ValueError):
                continue
    except Exception:
        pts = []

    if len(pts) < 2:
        return {"ok": False, "added_km": 0, "points": len(pts), "message": "GPS log too short to count coverage."}

    # Build trail deltas outside the lock (road snap can hit survey GIS).
    built: list[dict] = []
    added_km = 0.0
    # Filter/simplify the uploaded path first, then snap each hop.
    cleaned_runs = _prepare_trail_runs_for_distance(
        [(lat, lon, None) for lat, lon in pts]
    )
    for run in cleaned_runs:
        prev = None
        for lat, lon, _ts in run:
            delta = 0.0
            rc = None
            if prev is not None:
                delta = _haversine_km(prev[0], prev[1], lat, lon)
                if 0 < delta < TRAIL_JUMP_KM:
                    mid_lat = (prev[0] + lat) / 2
                    mid_lon = (prev[1] + lon) / 2
                    rc = _live_road_class_or_custom(int(user_id), date, mid_lat, mid_lon)
                    if not rc:
                        delta = 0.0
                else:
                    delta = 0.0
            built.append({
                "lat": lat,
                "lon": lon,
                "ts": _now_iso(),
                "capture_session_id": None,
                "committed": True,
                "delta_km": round(float(delta or 0), 5),
                "road_class": rc,
                "from_upload": True,
            })
            added_km += float(delta or 0)
            prev = (lat, lon)

    with _STATE_LOCK:
        with _cross_process_lock():
            entry = _entry_for_mutation(int(user_id), date)
            # Prefer GPS-log trail when live trail is empty/sparse (recover wiped pings).
            existing = list(entry.get("trail") or [])
            if len(existing) < 5:
                trail = built
            else:
                trail = existing + built
            if len(trail) > MAX_TRAIL_POINTS:
                trail = trail[-MAX_TRAIL_POINTS:]
            entry["trail"] = trail
            covered, by_class = _sum_trail_coverage(trail)
            entry["covered_km"] = covered
            entry["covered_by_class"] = by_class
            entry["recording"] = False
            if _tracking_json_mirror_enabled():
                state = _read_json()
                state.setdefault("videographers", {})[str(int(user_id))] = entry
                _write_json(state)
    try:
        covered, by_class = _persist_resolved_coverage(int(user_id), entry, date)
    except Exception:
        try:
            import db_utils
            if db_utils.is_db_configured():
                db_utils.tracking_db_save_user_entry(entry)
        except Exception:
            pass
        _sync_assignment_covered(int(user_id), entry, date)
    _ASSIGN_CLASS_CACHE.clear()

    return {
        "ok": True,
        "added_km": round(added_km, 3),
        "points": len(built),
        "covered_km": covered,
        "covered_by_class": by_class,
        "message": f"Counted {round(added_km, 2)} km from uploaded GPS log.",
    }


