"""tracking.admin — extends capture (includes private _names)."""
from __future__ import annotations

import routes.tracking.capture as _capture

globals().update({k: v for k, v in vars(_capture).items() if not k.startswith('__')})

def list_videographers_for_admin(date: str | None = None) -> list[dict]:
    import db_utils
    from routes import survey_service

    date = date or today_ist()
    track = {}
    # Prefer Postgres day snapshot so past dates work even if JSON only mirrors "latest"
    try:
        if db_utils.is_db_configured():
            day_state = db_utils.tracking_db_load_for_date(date)
            if day_state is not None:
                track = day_state.get("videographers") or {}
    except Exception:
        track = {}
    if not track:
        state = _load()
        raw = state.get("videographers") or {}
        for uid, entry in raw.items():
            rolled = _roll_day(entry if isinstance(entry, dict) else {}, date)
            if rolled.get("date") == date and (
                rolled.get("trail") or rolled.get("last") or rolled.get("covered_km")
            ):
                track[str(uid)] = rolled
            elif isinstance(entry, dict) and entry.get("date") == date:
                track[str(uid)] = entry

    users = []
    if db_utils.is_db_configured():
        for u in db_utils.get_all_users():
            if (u.get("role") or "").lower() != "videographer":
                continue
            if u.get("is_active") is False:
                continue
            users.append(u)

    out = []
    for u in users:
        uid = int(u["id"])
        entry = _roll_day(track.get(str(uid)) or {}, date)
        district_id = u.get("district_id") or entry.get("district_id")
        state_id = u.get("state_id") or entry.get("state_id")
        state_key = survey_service.resolve_state_key(state_id=state_id)
        dist_name = None
        if district_id and state_key:
            d = survey_service.get_district(district_id, state_key)
            if d:
                dist_name = d.get("name")
        summary = None
        try:
            # On today, resolve carryover so incomplete prior-day work is visible.
            # On historical dates, keep the exact day.
            summary = survey_service.assignment_summary_for_user(
                uid,
                date,
                resolve_carryover=(date == today_ist()),
            )
        except Exception:
            summary = None
        last = entry.get("last")
        has_route = bool(
            summary
            and (
                (summary.get("segment_count") or 0) > 0
                or summary.get("mode") == "auto_track"
                or (summary.get("start") and summary.get("end"))
            )
        )
        trail_points = len(entry.get("trail") or [])
        covered_km, covered_by_class, _ = resolve_videographer_coverage(
            uid, date, entry, resolve_carryover=(date == today_ist()), persist=False,
        )
        if summary is None:
            try:
                summary = survey_service.assignment_summary_for_user(
                    uid, date, resolve_carryover=(date == today_ist()),
                )
            except Exception:
                summary = None
        # Always surface resolved (road-snapped) coverage — never keep a higher
        # raw-GPS stored value on the assignment summary card.
        if summary is not None:
            summary = dict(summary)
            summary["covered_km"] = covered_km
            summary["covered_by_class"] = covered_by_class
        assign_date = (summary or {}).get("assignment_date") or (summary or {}).get("date")
        if assign_date and str(assign_date) != str(date):
            try:
                alt = trail_for_user(uid, str(assign_date))
                if alt:
                    trail_points = len(alt)
            except Exception:
                pass
        out.append({
            "user_id": uid,
            "username": u.get("username"),
            "full_name": u.get("full_name") or u.get("username"),
            "state_id": state_id,
            "district_id": district_id,
            "district_name": dist_name,
            "live": _is_live(entry) if date == today_ist() else False,
            "recording": bool(entry.get("recording")) if date == today_ist() else False,
            "last": last,
            "trail_points": trail_points,
            "covered_km": covered_km,
            "covered_by_class": covered_by_class,
            "assignment": {
                "segment_count": summary.get("segment_count", 0) if summary else 0,
                "total_km": summary.get("total_km", 0) if summary else 0,
                "target_km": summary.get("target_km") if summary else None,
                "quota_incomplete": summary.get("quota_incomplete") if summary else None,
                "assignment_complete": summary.get("assignment_complete") if summary else None,
                "mode": summary.get("mode") if summary else None,
                "start": summary.get("start") if summary else None,
                "end": summary.get("end") if summary else None,
                "assignment_date": (
                    (summary.get("assignment_date") or summary.get("date") or date)
                    if summary else date
                ),
                "is_carryover": bool(summary.get("is_carryover")) if summary else False,
                "has_route": has_route,
            },
        })

    out.sort(key=lambda x: (not x["live"], (x.get("full_name") or "").lower()))
    return out


def get_videographer_track(user_id: int, date: str | None = None) -> dict:
    import db_utils
    from routes import survey_service

    date = date or today_ist()
    entry = {}
    try:
        if db_utils.is_db_configured():
            day_state = db_utils.tracking_db_load_for_date(date)
            if day_state:
                entry = (day_state.get("videographers") or {}).get(str(int(user_id))) or {}
    except Exception:
        entry = {}
    if not entry:
        state = _load()
        entry = _roll_day((state.get("videographers") or {}).get(str(int(user_id))) or {}, date)
    else:
        entry = _roll_day(entry, date)

    user_row = None
    if db_utils.is_db_configured():
        for u in db_utils.get_all_users():
            if int(u["id"]) == int(user_id):
                user_row = u
                break

    district_id = (user_row or {}).get("district_id") or entry.get("district_id")
    state_id = (user_row or {}).get("state_id") or entry.get("state_id")
    state_key = survey_service.resolve_state_key(state_id=state_id)
    dist_name = None
    if district_id and state_key:
        d = survey_service.get_district(district_id, state_key)
        if d:
            dist_name = d.get("name")

    try:
        use_carry = date == today_ist()
        assignments = survey_service.assignments_for_user(
            int(user_id), date, resolve_carryover=use_carry,
        )
        summary = survey_service.assignment_summary_for_user(
            int(user_id), date, resolve_carryover=use_carry,
        )
        features = (
            survey_service.assigned_segments_geojson_for_user(
                int(user_id), date, resolve_carryover=use_carry,
            ).get("features") or []
        )
    except Exception as e:
        return {"error": str(e), "user_id": int(user_id)}

    live = _is_live(entry) if date == today_ist() else False
    trail = list(entry.get("trail") or [])
    # Carryover: always prefer the assignment day's trail (today may be a short post-end wander)
    assign_date = (
        (summary or {}).get("assignment_date")
        or (summary or {}).get("date")
        or date
    )
    if assign_date and str(assign_date) != str(date):
        try:
            alt = trail_for_user(int(user_id), str(assign_date))
            if alt and (len(alt) >= len(trail) or not trail):
                trail = list(alt)
                try:
                    if db_utils.is_db_configured():
                        alt_state = db_utils.tracking_db_load_for_date(str(assign_date))
                        alt_entry = (alt_state or {}).get("videographers", {}).get(str(int(user_id))) or {}
                        if alt_entry:
                            for k in ("covered_km", "covered_by_class", "last"):
                                if alt_entry.get(k) is not None:
                                    entry[k] = alt_entry.get(k)
                except Exception:
                    pass
        except Exception:
            pass

    # Replay pins on the assignment day so start→end complete works for historical trails
    try:
        survey_service.backfill_progress_from_trail(int(user_id), str(assign_date or date), trail)
        summary = survey_service.assignment_summary_for_user(
            int(user_id), date, resolve_carryover=use_carry,
        )
        features = (
            survey_service.assigned_segments_geojson_for_user(
                int(user_id), date, resolve_carryover=use_carry,
            ).get("features") or []
        )
    except Exception:
        pass

    stored_km = float(entry.get("covered_km") or 0)
    covered_km, covered_by_class, summary = resolve_videographer_coverage(
        int(user_id),
        date,
        entry,
        resolve_carryover=use_carry,
        sync_assignment=True,
    )
    if summary is None:
        try:
            summary = survey_service.assignment_summary_for_user(
                int(user_id), date, resolve_carryover=use_carry,
            )
            features = (
                survey_service.assigned_segments_geojson_for_user(
                    int(user_id), date, resolve_carryover=use_carry,
                ).get("features") or []
            )
        except Exception:
            pass

    # Assignment entry drives road-hugging for cyan display only (covered_km unchanged).
    asg_entry = {}
    try:
        st = survey_service._load_state()
        day_key = str(assign_date or date)[:10]
        asg_entry = (
            (st.get("daily_assignments", {}).get(day_key, {}) or {}).get(
                survey_service._user_day_key(int(user_id))
            )
            or {}
        )
    except Exception:
        asg_entry = {}
    display_trail = sanitize_trail_for_display(
        trail, entry=asg_entry if isinstance(asg_entry, dict) else None,
    )

    return {
        "user_id": int(user_id),
        "username": (user_row or {}).get("username") or entry.get("username"),
        "full_name": (user_row or {}).get("full_name") or entry.get("full_name"),
        "state_id": state_id,
        "district_id": district_id,
        "district_name": dist_name,
        "date": date,
        "assignment_date": assign_date,
        "is_carryover": bool((summary or {}).get("is_carryover")),
        "live": live,
        "recording": bool(entry.get("recording")) and live,
        "last": entry.get("last"),
        "trail": display_trail,
        "trail_raw_count": len(trail),
        "covered_km": covered_km,
        "covered_by_class": covered_by_class,
        "summary": summary,
        "assignments": assignments,
        "features": features,
    }


def _empty_coverage_bucket() -> dict:
    return {
        "by_class": _empty_by_class(),
        "total": 0.0,
        "assigned_by_class": _empty_by_class(),
        "assigned_km": 0.0,
        "n_active": 0,
        "n_live": 0,
        "n_with_assignment": 0,
        "n_videographers": 0,
        "target_km": 0.0,
    }


def _finalize_coverage_bucket(bucket: dict, *, date: str, state_key: str | None = None) -> dict:
    by_class = dict(bucket["by_class"])
    total = float(bucket["total"])
    assigned_by_class = bucket["assigned_by_class"]
    assigned_km = float(bucket.get("assigned_km") or 0)
    if assigned_km <= 0:
        assigned_km = sum(assigned_by_class.values())
    target_km = float(bucket["target_km"])
    class_sum = sum(float(v or 0) for v in by_class.values())
    # Keep class cards consistent with covered_km (never inflate Local beyond total)
    if total > 0.01 and class_sum < 0.01:
        by_class = {**_empty_by_class(), "other": round(total, 3)}
        class_sum = total
    elif abs(class_sum - total) > 0.05 and total > 0.01:
        by_class = _scale_by_class_to_total(by_class, total)
        class_sum = sum(float(v or 0) for v in by_class.values())
    pct = {k: round((v / total) * 100, 1) if total > 0 else 0.0 for k, v in by_class.items()}
    # % of total assigned corridor (what dashboard KPI cards should show) — keep 1dp even when tiny
    pct_of_corridor = {
        k: round((v / assigned_km) * 100, 1) if assigned_km > 0 else 0.0
        for k, v in by_class.items()
    }
    pct_of_assigned = {
        k: round((v / (assigned_by_class.get(k) or 0)) * 100, 1) if (assigned_by_class.get(k) or 0) > 0 else 0.0
        for k, v in by_class.items()
    }
    return {
        "date": date,
        "state_key": state_key,
        "covered_km": round(total, 2),
        "by_class": {k: round(v, 2) for k, v in by_class.items()},
        "pct_of_covered": pct,
        "pct_of_corridor": pct_of_corridor,
        "assigned_by_class": {k: round(v, 2) for k, v in assigned_by_class.items()},
        "assigned_km": round(assigned_km, 2),
        "target_km": round(target_km, 2),
        "quota_pct": round((total / target_km) * 100, 1) if target_km > 0 else (
            round((total / assigned_km) * 100, 1) if assigned_km > 0 else 0.0
        ),
        "pct_of_assigned_total": round((total / assigned_km) * 100, 1) if assigned_km > 0 else 0.0,
        "pct_of_assigned": pct_of_assigned,
        "class_attributed_km": round(class_sum, 2),
        "videographers": bucket["n_videographers"],
        "videographers_with_gps": bucket["n_active"],
        "videographers_live": bucket["n_live"],
        "videographers_with_assignment": bucket["n_with_assignment"],
    }


def _accumulate_coverage_for_user(bucket: dict, *, uid: int, entry: dict, date: str, daily_default: float) -> None:
    covered_km, cbc, _summary = resolve_videographer_coverage(
        uid, date, entry, resolve_carryover=(date == today_ist()),
    )

    bucket["n_videographers"] += 1
    if _is_live(entry):
        bucket["n_live"] += 1

    if covered_km > 0 or any(float(cbc.get(k) or 0) for k in bucket["by_class"]):
        bucket["n_active"] += 1
    bucket["total"] += covered_km
    for k in bucket["by_class"]:
        bucket["by_class"][k] += float(cbc.get(k) or 0)

    try:
        assigned_km, assigned_by_class, n_assign, target_km = _open_assignment_totals(
            uid, date, daily_default,
        )
        if n_assign > 0:
            bucket["n_with_assignment"] += 1
            bucket["target_km"] += target_km
            bucket["assigned_km"] = float(bucket.get("assigned_km") or 0) + assigned_km
            for k in bucket["assigned_by_class"]:
                bucket["assigned_by_class"][k] += float(assigned_by_class.get(k) or 0)
    except Exception:
        pass


def aggregate_gps_coverage(*, state_key: str | None = None, user_id: int | None = None) -> dict:
    """Sum overall (all-time) GPS covered km by road class (optionally filter by state or one user)."""
    bundle = aggregate_gps_coverage_bundle(user_id=user_id)
    if state_key:
        return bundle["by_state"].get(str(state_key).lower()) or _finalize_coverage_bucket(
            _empty_coverage_bucket(), date=bundle["date"], state_key=state_key
        )
    return bundle["all"]


def aggregate_gps_coverage_bundle(*, user_id: int | None = None) -> dict:
    """
    Overall coverage for India + each state.

    Prefers survey_daily_assignments covered_km (same source as Tracking day
    panels) so deleted prior-day assignments do not inflate KPI cards via
    orphan tracking_sessions rows.
    """
    from routes import survey_service
    import db_utils

    date = today_ist()
    daily_default = float(survey_service.get_settings().get("daily_km", 100))

    assigned_overall = None
    stored_floor: dict | None = None
    try:
        if db_utils.is_db_configured():
            assigned_overall = db_utils.survey_db_aggregate_assigned_km()
            # Canonical KPI floor = assignment covered (matches OVERALL / VG cards)
            stored_floor = db_utils.survey_db_aggregate_covered_km()
            if not stored_floor:
                stored_floor = db_utils.tracking_db_aggregate_overall_coverage()
    except Exception:
        assigned_overall = None
        stored_floor = None

    # Today's track — live / recording only (one day load)
    track_today = {}
    try:
        if db_utils.is_db_configured():
            day_state = db_utils.tracking_db_load_for_date(date)
            if day_state is not None:
                track_today = day_state.get("videographers") or {}
    except Exception:
        track_today = {}
    if not track_today:
        raw = _load().get("videographers") or {}
        track_today = {k: _roll_day(v or {}, date) for k, v in raw.items()}

    resolved_by_user: dict[str, dict] = {}

    # Prefer stored DB aggregates (cheap). For a single VG, optionally refresh
    # in-memory from resolve without persisting (write paths own persistence).
    if user_id is not None:
        key = str(int(user_id))
        km, cbc = 0.0, _empty_by_class()
        try:
            km, cbc, _ = resolve_videographer_coverage(
                int(user_id), date, resolve_carryover=True, persist=False,
            )
        except Exception:
            floor_u = ((stored_floor or {}).get("by_user") or {}).get(key) or {}
            km = float(floor_u.get("covered_km") or 0)
            cbc = dict(floor_u.get("by_class") or _empty_by_class())
        # Prefer assignment-day covered (canonical). Do not inflate with orphan
        # tracking days that no longer have an assignment row.
        floor_u = ((stored_floor or {}).get("by_user") or {}).get(key) or {}
        floor_km = float(floor_u.get("covered_km") or 0)
        if floor_km > 0.01:
            km = floor_km
            cbc = dict(floor_u.get("by_class") or cbc)
        resolved_by_user[key] = {
            "covered_km": float(km or 0),
            "by_class": dict(cbc or _empty_by_class()),
            "state_id": floor_u.get("state_id"),
        }
    elif stored_floor:
        for key, floor_u in (stored_floor.get("by_user") or {}).items():
            resolved_by_user[key] = {
                "covered_km": float(floor_u.get("covered_km") or 0),
                "by_class": dict(floor_u.get("by_class") or _empty_by_class()),
                "state_id": floor_u.get("state_id"),
            }

    all_b = _empty_coverage_bucket()
    by_state = {k: _empty_coverage_bucket() for k in survey_service.STATE_ID_BY_KEY}

    users_by_id = {}
    if db_utils.is_db_configured():
        try:
            for u in db_utils.get_all_users():
                if (u.get("role") or "").lower() == "videographer":
                    if u.get("is_active") is False:
                        continue
                    users_by_id[int(u["id"])] = u
        except Exception:
            users_by_id = {}

    keys = set()
    if user_id is not None:
        keys.add(str(int(user_id)))
    else:
        keys.update(str(uid) for uid in users_by_id)
        keys.update(resolved_by_user.keys())
        if assigned_overall:
            keys.update((assigned_overall.get("by_user") or {}).keys())
        keys.update(track_today.keys())

    by_user_asg = (assigned_overall or {}).get("by_user") or {}

    for key in keys:
        uid = int(key)
        if user_id is not None and uid != int(user_id):
            continue
        u = users_by_id.get(uid) or {}
        cov = resolved_by_user.get(key) or {}
        asg = by_user_asg.get(key) or {}
        entry_today = _roll_day(track_today.get(key) or {}, date)

        if not cov:
            _accumulate_coverage_for_user(
                all_b, uid=uid, entry=entry_today, date=date, daily_default=daily_default,
            )
            sid = u.get("state_id") or entry_today.get("state_id")
            state_key = survey_service.resolve_state_key(state_id=sid)
            if state_key and state_key in by_state:
                _accumulate_coverage_for_user(
                    by_state[state_key], uid=uid, entry=entry_today, date=date, daily_default=daily_default,
                )
            continue

        covered_km = float(cov.get("covered_km") or 0)
        cbc = cov.get("by_class") or _empty_by_class()
        assigned_km = float(asg.get("assigned_km") or 0)
        n_asg = int(asg.get("n_assignments") or 0)

        sid = u.get("state_id") or asg.get("state_id") or entry_today.get("state_id")
        state_key = survey_service.resolve_state_key(state_id=sid)
        if asg.get("state_key") and not state_key:
            state_key = str(asg.get("state_key")).lower()

        def _acc(bucket: dict, *, _ck=covered_km, _cbc=cbc, _ak=assigned_km, _na=n_asg) -> None:
            bucket["n_videographers"] += 1
            if _is_live(entry_today):
                bucket["n_live"] += 1
            if _ck > 0.01 or any(float(_cbc.get(k) or 0) for k in bucket["by_class"]):
                bucket["n_active"] += 1
            bucket["total"] += _ck
            for k in bucket["by_class"]:
                bucket["by_class"][k] += float(_cbc.get(k) or 0)
            if _na > 0:
                bucket["n_with_assignment"] += 1
                bucket["assigned_km"] = float(bucket.get("assigned_km") or 0) + _ak
                bucket["target_km"] += _ak if _ak > 0 else daily_default

        _acc(all_b)
        if state_key and state_key in by_state:
            _acc(by_state[state_key])

    finalized_all = _finalize_coverage_bucket(all_b, date=date, state_key=None)
    finalized_all["scope"] = "overall"
    by_state_out = {}
    for k, v in by_state.items():
        fin = _finalize_coverage_bucket(v, date=date, state_key=k)
        fin["scope"] = "overall"
        by_state_out[k] = fin

    return {
        "date": date,
        "scope": "overall",
        "all": finalized_all,
        "by_state": by_state_out,
    }

