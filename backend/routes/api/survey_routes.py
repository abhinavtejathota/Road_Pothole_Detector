"""API routes: survey_routes."""
import os
import threading
import time

from flask import jsonify, request, abort
from flask_login import login_required, login_user, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

import db_utils
from routes.api import api_bp
from routes.api._helpers import _serialize, _user_payload, _vendor_from_request, _retry_until, _dev_admin_only
from routes.user_model import User
from routes.constants import VALID_TRANSITIONS
from routes import validation as validation_module
from routes import survey_service
from routes import detection_service
from routes import model_bench_service
from routes import field_upload_service
from routes import tracking_service
from routes import auto_track_service


# # ── Survey (district road network — Andhra / Telangana) ───────────────────────
# ── Survey (district road network — Andhra / Telangana) ───────────────────────

@api_bp.route("/survey/states")
@login_required
def survey_states():
    return jsonify(survey_service.list_states())


@api_bp.route("/survey/districts")
@login_required
def survey_districts():
    state_key = request.args.get("state_key") or None
    state_id = request.args.get("state_id", type=int)
    if state_id and not state_key:
        state_key = survey_service.resolve_state_key(state_id=state_id)
    try:
        return jsonify(survey_service.list_districts(state_key))
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 503


@api_bp.route("/survey/road-lengths")
@login_required
def survey_road_lengths():
    state_key = request.args.get("state_key") or None
    state_id = request.args.get("state_id", type=int)
    district_id = request.args.get("district_id") or None
    if state_id and not state_key:
        state_key = survey_service.resolve_state_key(state_id=state_id)
    try:
        return jsonify(survey_service.road_length_summary(state_key, district_id=district_id))
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 503
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@api_bp.route("/survey/nh-overview")
@login_required
def survey_nh_overview():
    state_key = survey_service.resolve_state_key(
        state_id=request.args.get("state_id", type=int),
        state_key=request.args.get("state_key"),
    )
    if not state_key:
        return jsonify({"error": "state_key or state_id required"}), 400
    try:
        return jsonify(survey_service.state_nh_geojson(state_key))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 503


@api_bp.route("/survey/overview")
@login_required
def survey_overview():
    state_key = survey_service.resolve_state_key(
        state_key=request.args.get("state_key") or "andhra",
    ) or "andhra"
    try:
        return jsonify(survey_service.state_nh_geojson(state_key))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 503


@api_bp.route("/survey/segments")
@login_required
def survey_segments():
    state_key = survey_service.resolve_state_key(
        state_id=request.args.get("state_id", type=int),
        state_key=request.args.get("state_key"),
    )
    district_raw = request.args.get("district_id")
    if not state_key or not district_raw:
        return jsonify({"error": "state_key and district_id required"}), 400
    try:
        district_id = str(int(str(district_raw).strip()))
    except (TypeError, ValueError):
        return jsonify({"error": "district_id must be numeric"}), 400
    classes = request.args.get("classes", "").strip()
    road_classes = {c.strip() for c in classes.split(",") if c.strip()} or None
    try:
        return jsonify(survey_service.segments_geojson(
            state_key, district_id, road_classes=road_classes
        ))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except FileNotFoundError:
        return jsonify({
            "error": "District GIS index not found. Run: python tools/gis/clip_roads_to_districts.py",
        }), 503


@api_bp.route("/survey/geocode")
@login_required
def survey_geocode():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"error": "q required"}), 400
    state_key = (request.args.get("state_key") or "").strip() or None
    district_id = request.args.get("district_id")
    raw_ids = request.args.get("district_ids") or ""
    district_ids = [x.strip() for x in raw_ids.split(",") if x.strip()]
    discover = (request.args.get("discover") or "1").strip().lower() not in ("0", "false", "no")
    allowed = []
    if hasattr(current_user, "allowed_district_ids"):
        allowed = list(current_user.allowed_district_ids() or [])
    elif getattr(current_user, "district_id", None) is not None:
        allowed = [current_user.district_id]

    if not state_key and getattr(current_user, "state_id", None):
        state_key = survey_service.resolve_state_key(state_id=current_user.state_id)

    # Videographers: always search their assigned districts first (fast local
    # road index). discover=1 still allows Nominatim for landmarks, but must
    # NOT clear district_ids — that forced dual-state Nominatim-only search
    # and made mobile route picking feel broken/slow.
    state_keys = None
    search_district_ids = district_ids or ([str(x) for x in allowed] if allowed else None)
    if current_user.is_videographer():
        if not search_district_ids:
            search_district_ids = [str(x) for x in allowed]
        if discover:
            # Keep local districts; also allow sibling-state Nominatim via state_keys
            # only when the query looks like a place (not a highway ref).
            sks = survey_service.state_keys_for_district_ids(search_district_ids or allowed)
            if sks:
                state_keys = sks
            elif state_key:
                state_keys = [state_key]
        elif not state_key and getattr(current_user, "state_id", None):
            state_key = survey_service.resolve_state_key(state_id=current_user.state_id)

    try:
        results = survey_service.geocode_search(
            q,
            state_key=state_key if not state_keys else None,
            district_id=district_id,
            district_ids=search_district_ids,
            state_keys=state_keys,
            limit=8,
        )
        if current_user.is_videographer():
            results = survey_service.annotate_geocode_access(
                results, allowed, require_allowed=True,
            )
            # Prefer in-district hits; hide out-of-scope / wrong-state teasers when
            # the VG already has usable recommendations (stops "jumped to AP/TG").
            in_scope = [r for r in results if r.get("access_ok")]
            if in_scope:
                results = in_scope
        else:
            results = survey_service.annotate_geocode_access(
                results, allowed or None, require_allowed=False,
            )
        return jsonify({"results": results})
    except Exception as e:
        return jsonify({"error": f"Geocode failed: {e}"}), 502


@api_bp.route("/survey/locate")
@login_required
def survey_locate():
    """Resolve lat/lon to district + access check for the current user."""
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lon required"}), 400
    state_key = (request.args.get("state_key") or "").strip() or None
    if not state_key and getattr(current_user, "state_id", None):
        state_key = survey_service.resolve_state_key(state_id=current_user.state_id)
    selected = request.args.get("district_id")
    allowed = []
    if hasattr(current_user, "allowed_district_ids"):
        allowed = list(current_user.allowed_district_ids() or [])
    elif getattr(current_user, "district_id", None) is not None:
        allowed = [int(current_user.district_id)]
    if allowed:
        survey_service.schedule_prewarm_snap_indexes(allowed)
    # Multi-state VG (e.g. AP+TG): check against all states that own their districts
    state_keys = survey_service.state_keys_for_district_ids(allowed) if allowed else None
    if not state_keys:
        state_keys = [state_key] if state_key else list(survey_service.STATE_ID_BY_KEY.keys())
    chk = survey_service.check_location_access(
        lat, lon,
        state_key=state_key,
        state_keys=state_keys,
        allowed_district_ids=allowed,
        selected_district_id=selected,
        require_allowed=bool(current_user.is_videographer()),
    )
    want_rev = (request.args.get("reverse") or "1").strip().lower() not in ("0", "false", "no")
    # Nominatim reverse is slow (~5–15s) and often wrong on TG district borders.
    # Default: local road name only; pass nominatim=1 when a placename is required.
    want_nom = (request.args.get("nominatim") or "0").strip().lower() in ("1", "true", "yes")
    if want_rev:
        try:
            rev = survey_service.reverse_geocode(
                lat, lon,
                district_ids=allowed,
                state_keys=state_keys,
                allow_nominatim=want_nom,
            )
        except Exception:
            rev = {"display_name": f"{lat:.5f}, {lon:.5f}", "lat": lat, "lon": lon}
    else:
        rev = {"display_name": f"{lat:.5f}, {lon:.5f}", "lat": lat, "lon": lon}
    # Prefer access-check district over reverse-geocode admin labels (Medchal vs RR).
    if chk.get("located"):
        loc = chk["located"]
        rev = dict(rev or {})
        rev.setdefault("district_id", loc.get("district_id"))
        rev["district_id"] = loc.get("district_id") or rev.get("district_id")
        rev["district_name"] = loc.get("district_name") or rev.get("district_name")
        rev["state_key"] = loc.get("state_key") or rev.get("state_key")
    return jsonify({**chk, "reverse": rev})


@api_bp.route("/survey/reverse-geocode")
@login_required
def survey_reverse_geocode():
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lon required"}), 400
    allowed = []
    if hasattr(current_user, "allowed_district_ids"):
        allowed = list(current_user.allowed_district_ids() or [])
    elif getattr(current_user, "district_id", None) is not None:
        allowed = [int(current_user.district_id)]
    state_keys = survey_service.state_keys_for_district_ids(allowed) if allowed else None
    prefer_places = str(request.args.get("prefer_places") or "").strip().lower() in (
        "1", "true", "yes", "places",
    )
    try:
        return jsonify(survey_service.reverse_geocode(
            lat, lon,
            district_ids=allowed or None,
            state_keys=state_keys,
            allow_nominatim=True,
            prefer_places=prefer_places,
        ))
    except Exception as e:
        return jsonify({"error": f"Reverse geocode failed: {e}"}), 502


@api_bp.route("/survey/settings")
@login_required
def survey_settings_get():
    return jsonify(survey_service.get_settings())


@api_bp.route("/survey/settings", methods=["PUT"])
@login_required
def survey_settings_put():
    if not current_user.can_manage_field_ops():
        abort(403)
    data = request.get_json(silent=True) or {}
    payload = {}
    daily_km = data.get("daily_km")
    if daily_km is not None:
        daily_km = float(daily_km)
        if daily_km <= 0 or daily_km > 500:
            return jsonify({"error": "daily_km must be between 1 and 500"}), 400
        payload["daily_km"] = daily_km
    focus = data.get("focus_road_class")
    focus_list = data.get("focus_road_classes")
    if focus_list is not None and focus is None:
        focus = focus_list
    if focus is not None:
        try:
            stored = survey_service.focus_to_storage(focus)
        except Exception:
            return jsonify({"error": "Invalid focus_road_class"}), 400
        # Must be all or a non-empty subset of nh|sh|mdr|other
        if stored != "all":
            parts = set(stored.split(","))
            if not parts or not parts.issubset(survey_service.VALID_FOCUS_CLASSES):
                return jsonify({"error": "focus_road_class must be all or combination of nh,sh,mdr,other"}), 400
        payload["focus_road_class"] = stored
    if not payload:
        return jsonify(survey_service.get_settings())
    return jsonify(survey_service.update_settings(**payload))


@api_bp.route("/survey/assignments")
@login_required
def survey_assignments():
    date = request.args.get("date") or survey_service.today_ist()
    user_id = request.args.get("user_id", type=int)

    if current_user.is_videographer():
        user_id = int(current_user.id)
        # light=True: skip district GeoJSON scan + trail recompute (was pegging
        # CPU and timing out mobile / Survey page loads at ~12s).
        return jsonify({
            "assignments": survey_service.assignments_for_user(
                user_id, date, light=True,
            ),
            "summary": survey_service.assignment_summary_for_user(
                user_id, date, light=True, sync_coverage=False,
            ),
        })

    if not current_user.can_manage_field_ops():
        abort(403)

    if not user_id:
        overview = survey_service.assignments_overview(date)
        users_by_id = {}
        if db_utils.is_db_configured():
            for u in db_utils.get_all_users():
                if u.get("role") == "Videographer":
                    users_by_id[int(u["id"])] = u
        for item in overview.get("items", []):
            v = users_by_id.get(int(item.get("user_id") or item.get("videographer_user_id") or 0))
            if v:
                item["videographer_name"] = v.get("full_name") or v.get("username")
                item["videographer_username"] = v.get("username")
        for item in overview.get("carryover_items", []):
            v = users_by_id.get(int(item.get("user_id") or item.get("videographer_user_id") or 0))
            if v:
                item["videographer_name"] = v.get("full_name") or v.get("username")
                item["videographer_username"] = v.get("username")
        history = survey_service.assignment_history_summary()
        for day in history:
            for item in day.get("items") or []:
                v = users_by_id.get(int(item.get("user_id") or item.get("videographer_user_id") or 0))
                if v:
                    item["videographer_name"] = v.get("full_name") or v.get("username")
                    item["videographer_username"] = v.get("username")
        overview["history"] = history
        return jsonify(overview)

    return jsonify({
        "assignments": survey_service.assignments_for_user(user_id, date),
        "summary": survey_service.assignment_summary_for_user(user_id, date),
    })


@api_bp.route("/survey/assignments/geojson")
@login_required
def survey_assignments_geojson():
    """Assigned road geometries for the current videographer (mobile map)."""
    if not current_user.is_videographer() and not current_user.can_manage_field_ops():
        abort(403)
    date = request.args.get("date") or survey_service.today_ist()
    user_id = request.args.get("user_id", type=int)
    if current_user.is_videographer():
        user_id = int(current_user.id)
    elif not user_id:
        return jsonify({"error": "user_id required"}), 400
    return jsonify(survey_service.assigned_segments_geojson_for_user(user_id, date))


@api_bp.route("/survey/routes/preview", methods=["POST"])
@login_required
def survey_routes_preview():
    """Maps-style alternate routes between start and end (shortest first)."""
    if current_user.can_manage_field_ops():
        return jsonify({"error": "Videographers preview routes on Survey."}), 403
    if not current_user.is_videographer():
        abort(403)
    data = request.get_json(silent=True) or {}
    state_id = getattr(current_user, "state_id", None)
    allowed = current_user.allowed_district_ids() if hasattr(current_user, "allowed_district_ids") else []
    if not allowed and getattr(current_user, "district_id", None) is not None:
        allowed = [int(current_user.district_id)]
    state_key = survey_service.resolve_state_key(state_id=state_id) or data.get("state_key")
    requested = data.get("district_id")
    if requested is not None and requested != "":
        try:
            district_id = int(requested)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid district_id"}), 400
    else:
        district_id = allowed[0] if allowed else None
    if not state_key or district_id is None:
        return jsonify({"error": "No district linked to your account"}), 400
    # Prefer GIS state of the requested/located district (multi-state VGs)
    sk_from_did = survey_service._state_key_for_district_id(district_id)
    if sk_from_did:
        state_key = sk_from_did
    if data.get("state_key") in survey_service.STATE_ID_BY_KEY:
        # Client hint from locate — only accept if that state owns an allowed district
        hint = data.get("state_key")
        if any(survey_service._state_key_for_district_id(x) == hint for x in allowed):
            state_key = hint
    try:
        start_lat = float(data["start_lat"])
        start_lon = float(data["start_lon"])
        end_lat = float(data["end_lat"])
        end_lon = float(data["end_lon"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "start_lat, start_lon, end_lat, end_lon required"}), 400
    try:
        return jsonify(survey_service.preview_corridor_routes(
            state_key=state_key,
            district_id=str(district_id),
            start_lat=start_lat,
            start_lon=start_lon,
            end_lat=end_lat,
            end_lon=end_lon,
            start_label=(data.get("start_label") or "").strip(),
            end_label=(data.get("end_label") or "").strip(),
            allowed_district_ids=allowed,
            max_options=int(data.get("max_options") or 4),
        ))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@api_bp.route("/survey/assign", methods=["POST"])
@login_required
def survey_assign():
    data = request.get_json(silent=True) or {}

    if current_user.can_manage_field_ops():
        return jsonify({
            "error": "Daily assignments are generated by videographers (start→end corridor). Admins set the km target on Survey admin.",
        }), 403

    if not current_user.is_videographer():
        abort(403)

    state_id = getattr(current_user, "state_id", None)
    allowed = current_user.allowed_district_ids() if hasattr(current_user, "allowed_district_ids") else []
    if not allowed and getattr(current_user, "district_id", None) is not None:
        allowed = [int(current_user.district_id)]
    state_key = survey_service.resolve_state_key(state_id=state_id) or data.get("state_key")
    requested = data.get("district_id")
    if requested is not None and requested != "":
        try:
            district_id = int(requested)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid district_id"}), 400
    else:
        district_id = allowed[0] if allowed else None
    if not state_key or district_id is None:
        return jsonify({"error": "No district linked to your account"}), 400
    if allowed and int(district_id) not in {int(x) for x in allowed}:
        return jsonify({"error": "That district is not assigned to your account"}), 403
    sk_from_did = survey_service._state_key_for_district_id(district_id)
    if sk_from_did:
        state_key = sk_from_did
    if data.get("state_key") in survey_service.STATE_ID_BY_KEY:
        hint = data.get("state_key")
        if any(survey_service._state_key_for_district_id(x) == hint for x in allowed):
            state_key = hint

    mode = (data.get("mode") or "corridor").strip().lower()

    try:
        if mode == "nearest":
            lat = data.get("lat", data.get("start_lat"))
            lon = data.get("lon", data.get("start_lon"))
            result = survey_service.generate_nearest_assignment(
                int(current_user.id),
                state_key=state_key,
                district_id=str(district_id),
                lat=float(lat) if lat not in (None, "") else None,
                lon=float(lon) if lon not in (None, "") else None,
                leg_km=float(data["leg_km"]) if data.get("leg_km") not in (None, "") else None,
                allowed_district_ids=allowed,
            )
        elif mode == "manual":
            result = survey_service.generate_manual_assignment(
                int(current_user.id),
                state_key=state_key,
                district_id=str(district_id),
                segment_ids=list(data.get("segment_ids") or []),
                start_label=(data.get("start_label") or "Manual start").strip(),
                end_label=(data.get("end_label") or "Manual end").strip(),
                allowed_district_ids=allowed,
            )
        elif mode == "auto_track":
            try:
                start_lat = float(data["start_lat"])
                start_lon = float(data["start_lon"])
                end_lat = float(data["end_lat"])
                end_lon = float(data["end_lon"])
            except (KeyError, TypeError, ValueError):
                return jsonify({
                    "error": "start_lat, start_lon, end_lat, end_lon required for custom route",
                }), 400
            result = survey_service.generate_auto_track_assignment(
                int(current_user.id),
                state_key=state_key,
                district_id=str(district_id),
                start_lat=start_lat,
                start_lon=start_lon,
                end_lat=end_lat,
                end_lon=end_lon,
                start_label=(data.get("start_label") or "").strip(),
                end_label=(data.get("end_label") or "").strip(),
                allowed_district_ids=allowed,
            )
        else:
            try:
                start_lat = float(data["start_lat"])
                start_lon = float(data["start_lon"])
                end_lat = float(data["end_lat"])
                end_lon = float(data["end_lon"])
            except (KeyError, TypeError, ValueError):
                return jsonify({
                    "error": "start_lat, start_lon, end_lat, end_lon required (corridor start → end)",
                }), 400
            result = survey_service.generate_corridor_assignment(
                int(current_user.id),
                state_key=state_key,
                district_id=str(district_id),
                start_lat=start_lat,
                start_lon=start_lon,
                end_lat=end_lat,
                end_lon=end_lon,
                start_label=(data.get("start_label") or "").strip(),
                end_label=(data.get("end_label") or "").strip(),
                corridor_km=float(data.get("corridor_km") or 2.5),
                leg_km=float(data["leg_km"]) if data.get("leg_km") not in (None, "") else None,
                allowed_district_ids=allowed,
                segment_ids=list(data["segment_ids"]) if data.get("segment_ids") else None,
                replace=data.get("replace", True) is not False,
                polyline=data.get("polyline") if isinstance(data.get("polyline"), list) else None,
            )
        # Hard 409 conflicts removed — overlaps are soft warnings on the JSON body
        result.pop("conflict", None)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@api_bp.route("/survey/assignments/clear", methods=["POST"])
@login_required
def survey_assignments_clear():
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    date = data.get("date") or None
    if current_user.can_manage_field_ops():
        if not user_id:
            return jsonify({"error": "user_id required"}), 400
        # Admin may target a specific day; otherwise clear whatever is active for that VG.
        if date:
            return jsonify(survey_service.clear_daily_assignment_for_user(int(user_id), date))
        return jsonify(survey_service.clear_active_assignment_for_user(int(user_id)))
    if not current_user.is_videographer():
        abort(403)
    # VG self-clear: always clear the assignment shown on their dashboard (today or carryover).
    return jsonify(
        survey_service.clear_active_assignment_for_user(int(current_user.id), date)
    )


@api_bp.route("/survey/my-districts", methods=["PATCH", "PUT"])
@login_required
def survey_my_districts():
    if not current_user.is_videographer():
        abort(403)
    data = request.get_json(silent=True) or {}
    district_ids = db_utils.normalize_district_ids(
        data.get("district_ids"), data.get("district_id"),
    )
    if not district_ids:
        return jsonify({"error": "Select at least one district"}), 400
    try:
        result = survey_service.update_videographer_districts_self(
            int(current_user.id),
            district_ids,
            state_id=current_user.state_id,
        )
        return jsonify(_serialize(result))
    except ValueError as e:
        return jsonify({"error": str(e), "loading": True}), 409
    except Exception as e:
        return jsonify({"error": str(e)}), 400


