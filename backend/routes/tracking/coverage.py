"""tracking.coverage — extends trail (includes private _names)."""
from __future__ import annotations

import routes.tracking.trail as _trail

globals().update({k: v for k, v in vars(_trail).items() if not k.startswith('__')})

def resolve_videographer_coverage(
    user_id: int,
    date: str | None = None,
    entry: dict | None = None,
    *,
    resolve_carryover: bool | None = None,
    sync_assignment: bool = False,
    persist: bool = False,
) -> tuple[float, dict, dict | None]:
    """Compute covered km once, optionally persist identically to tracking + assignment.

    persist=False by default — list/history/complete-checks must not rewrite JSON/DB
    on every request (that tore survey_state.json under Waitress and exhausted the pool).
    Callers that own a write (ping, upload, repair, dashboard aggregate) pass persist=True.

    Precedence (trail present):
      1) driven GPS start→end clip (field truth, e.g. 7.1 km)
      2) road-snapped GPS if driven missing
      3) sealed corridor GIS only if no useful trail
      4) stored DB value
    Class mix from snap/driven scaled to that total. Same value written to
    tracking_sessions + survey_daily_assignments when persist=True.
    """
    import db_utils
    from routes import survey_service

    date = date or today_ist()
    uid = int(user_id)
    if resolve_carryover is None:
        resolve_carryover = date == today_ist()

    if entry is None:
        entry = {}
        try:
            if db_utils.is_db_configured():
                day_state = db_utils.tracking_db_load_for_date(date)
                if day_state:
                    entry = (day_state.get("videographers") or {}).get(str(uid)) or {}
        except Exception:
            pass
        if not entry:
            entry = _roll_day((_load().get("videographers") or {}).get(str(uid)) or {}, date)
        else:
            entry = _roll_day(entry, date)

    assign_date = date
    try:
        assign_date = (
            survey_service.resolve_work_date_for_user(uid, date)
            if resolve_carryover
            else date
        )
    except Exception:
        assign_date = date
    persist_day = str(assign_date or date)[:10]

    try:
        st = survey_service._load_state()
        asg_entry = (
            (st.get("daily_assignments", {}).get(assign_date, {}) or {}).get(
                survey_service._user_day_key(uid)
            )
            or {}
        )
        summary_covered = float(asg_entry.get("covered_km") or 0)
        summary_by_class = asg_entry.get("covered_by_class") or _empty_by_class()
    except Exception:
        asg_entry = {}
        summary_covered = 0.0
        summary_by_class = _empty_by_class()

    trail = list(entry.get("trail") or [])

    if resolve_carryover and assign_date != str(date):
        try:
            alt = trail_for_user(uid, assign_date)
            if alt and len(alt) >= len(trail):
                trail = list(alt)
            if db_utils.is_db_configured():
                alt_state = db_utils.tracking_db_load_for_date(assign_date)
                alt_entry = (alt_state or {}).get("videographers", {}).get(str(uid)) or {}
                if alt_entry:
                    for k in ("covered_km", "covered_by_class", "last"):
                        if alt_entry.get(k) is not None:
                            entry[k] = alt_entry.get(k)
        except Exception:
            pass

    track_km = float(entry.get("covered_km") or 0)
    stored_km = max(track_km, summary_covered)
    stored_by_class = entry.get("covered_by_class") or summary_by_class or _empty_by_class()

    covered_km = stored_km
    covered_by_class = stored_by_class
    snap, snap_by = 0.0, _empty_by_class()
    driven, driven_by = 0.0, _empty_by_class()
    sealed_km = 0.0
    route_done = False
    corridor_km = 0.0

    if trail:
        try:
            asg = asg_entry if isinstance(asg_entry, dict) else {}
            driven, driven_by = recompute_driven_trail_coverage(uid, assign_date, trail, entry=asg)
            start_pins, end_pins = survey_service._effective_progress_pins(asg)
            clipped = (
                survey_service._truncate_trail_to_progress_pins(
                    trail, start_pins, end_pins, lock_end=False,
                )
                if start_pins and end_pins
                else survey_service._trail_latlon_points(trail)
            )
            snap_trail = _trail_dicts_from_latlon(clipped) if clipped else trail
            snap, snap_by = recompute_trail_coverage(uid, assign_date, snap_trail)

            try:
                corridor_km = float(asg.get("route_km") or asg.get("corridor_km") or 0)
            except (TypeError, ValueError):
                corridor_km = 0.0
            try:
                sealed_km, _ = survey_service._assigned_sealed_and_total_km(asg)
            except Exception:
                sealed_km = 0.0
            meta = asg.get("meta") if isinstance(asg.get("meta"), dict) else {}
            route_done = bool(
                meta.get("completed")
                or (meta.get("start_reached") and meta.get("end_reached"))
            )

            # Canonical covered = cleaned GPS trail (start→end clip).
            # Prefer driven; if GPS is inflated vs road-snap (zigzag/drift), use
            # snap when it is close but lower. Cars OK at TRAIL_MAX_SPEED_KMH.
            # Sealed GIS is fallback only when there is no useful trail.
            if driven > 0.05:
                covered_km = driven
                if (
                    snap > 0.05
                    and snap < driven
                    and snap >= driven * TRAIL_SNAP_INFLATE_RATIO
                ):
                    covered_km = snap
            elif snap > 0.05:
                covered_km = snap
            elif sealed_km > 0.05 and route_done:
                covered_km = sealed_km
            elif stored_km > 0.05:
                covered_km = stored_km
            else:
                covered_km = 0.0

            if corridor_km > 0.5:
                covered_km = _cap_covered_km_to_corridor(covered_km, corridor_km)

            # Class mix from road-snap (incl. sealed corridor samples); scale to driven total
            mix_candidates = (snap_by, driven_by, stored_by_class, summary_by_class)
            typed_mix = next(
                (c for c in mix_candidates if _by_class_typed_km(c) > 0.05),
                None,
            )
            if typed_mix is not None:
                covered_by_class = _scale_by_class_to_total(typed_mix, covered_km)
            elif sum(float((driven_by or {}).get(k) or 0) for k in _empty_by_class()) > 0.05:
                covered_by_class = _scale_by_class_to_total(driven_by, covered_km)
            else:
                covered_by_class = {**_empty_by_class(), "other": round(float(covered_km), 3)}
        except Exception:
            pass

    covered_km, covered_by_class = _normalize_covered_pair(covered_km, covered_by_class)
    entry["covered_km"] = covered_km
    entry["covered_by_class"] = covered_by_class

    # Always keep both DB tables + JSON mirror identical when asked (default).
    # sync_assignment is legacy alias for persist.
    if persist or sync_assignment:
        try:
            covered_km, covered_by_class = persist_coverage_canonical(
                uid, persist_day, covered_km, covered_by_class,
            )
            entry["covered_km"] = covered_km
            entry["covered_by_class"] = covered_by_class
        except Exception:
            pass

    return covered_km, covered_by_class, None


def _persist_resolved_coverage(user_id: int, entry: dict, date: str | None = None) -> tuple[float, dict]:
    """Resolve + canonical persist (tracking session + assignment)."""
    date = date or today_ist()
    uid = int(user_id)
    km, by_class, _ = resolve_videographer_coverage(
        uid, date, entry, resolve_carryover=False, persist=True,
    )
    entry["covered_km"] = km
    entry["covered_by_class"] = by_class
    try:
        with _STATE_LOCK:
            with _cross_process_lock():
                state = _load(prefer_db=False)
                vids = state.setdefault("videographers", {})
                key = str(uid)
                cur = _roll_day(vids.get(key) or {}, date)
                cur["covered_km"] = km
                cur["covered_by_class"] = by_class
                if entry.get("trail") is not None:
                    cur["trail"] = entry.get("trail")
                vids[key] = cur
                _write_json(state)
    except Exception:
        pass
    return km, by_class


def _open_assignment_totals(uid: int, date: str, daily_default: float) -> tuple[float, dict, int, float]:
    """(assigned_km, assigned_by_class, n_assignments, target_km) for open work today + carryover."""
    from routes import survey_service

    assigned_km = 0.0
    assigned_by_class = _empty_by_class()
    target_km = 0.0
    n_assign = 0
    seen_dates: set[str] = set()

    def _add_summary(s: dict | None) -> None:
        nonlocal assigned_km, target_km, n_assign
        if not s:
            return
        has_work = bool(
            (s.get("segment_count") or 0) > 0
            or s.get("mode") == "auto_track"
            or (s.get("start") and s.get("end"))
        )
        if not has_work:
            return
        dkey = str(s.get("assignment_date") or s.get("date") or "")
        if dkey in seen_dates:
            return
        seen_dates.add(dkey)
        n_assign += 1
        assigned_km += float(s.get("total_km") or 0)
        target_km += float(s.get("target_km") or daily_default)
        for k, v in (s.get("assigned_by_class") or {}).items():
            rc = k if k in assigned_by_class else "other"
            assigned_by_class[rc] += float(v or 0)

    try:
        today_sum = survey_service.assignment_summary_for_user(
            uid, date, resolve_carryover=False,
        )
        if today_sum and not today_sum.get("assignment_complete"):
            _add_summary(today_sum)
        open_asg = survey_service.find_open_incomplete_assignment(uid)
        if open_asg and open_asg.get("date"):
            od = str(open_asg["date"])
            if od not in seen_dates:
                co_sum = survey_service.assignment_summary_for_user(
                    uid, od, resolve_carryover=False,
                )
                _add_summary(co_sum)
    except Exception:
        pass

    return round(assigned_km, 2), assigned_by_class, n_assign, round(target_km, 2)


def _sync_assignment_covered(user_id: int, entry: dict, date: str) -> None:
    """Legacy name — routes through canonical persist (both tables)."""
    try:
        persist_coverage_canonical(
            int(user_id),
            date,
            float(entry.get("covered_km") or 0),
            entry.get("covered_by_class"),
        )
    except Exception:
        pass


