"""JSON API for the React SPA."""
import os
import threading
import time
from datetime import date, datetime
from decimal import Decimal

from flask import Blueprint, jsonify, request, abort
from flask_login import login_required, login_user, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

import db_utils
from routes.user_model import User
from routes.constants import VALID_TRANSITIONS
from routes import validation as validation_module
from routes import survey_service
from routes import detection_service
from routes import model_bench_service
from routes import field_upload_service
from routes import tracking_service
from routes import auto_track_service

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _serialize(value):
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    return value


def _user_payload():
    district_ids = list(getattr(current_user, "district_ids", None) or (
        [current_user.district_id] if getattr(current_user, "district_id", None) is not None else []
    ))
    if current_user.is_videographer() and district_ids:
        survey_service.schedule_prewarm_snap_indexes(district_ids)
    return {
        "id": current_user.id,
        "username": current_user.username,
        "full_name": current_user.full_name,
        "email": current_user.email,
        "role": current_user.role,
        "vendor_id": current_user.vendor_id,
        "is_admin": current_user.is_admin(),
        "is_supervisor": current_user.is_supervisor(),
        "is_allocator": current_user.is_allocator(),
        "is_vendor": current_user.is_vendor_role(),
        "is_videographer": current_user.is_videographer(),
        "state_id": getattr(current_user, "state_id", None),
        "district_id": getattr(current_user, "district_id", None),
        "district_ids": district_ids,
        "state_keys": survey_service.state_keys_for_district_ids(district_ids)
        if current_user.is_videographer() else [],
    }


def _vendor_from_request(data: dict, *, include_status: bool = False) -> dict:
    specs_raw = data.get("specializations", "")
    if isinstance(specs_raw, list):
        specs = [s.strip() for s in specs_raw if str(s).strip()]
    else:
        specs = [s.strip() for s in str(specs_raw).split(",") if s.strip()]
    out = {
        "company_name": (data.get("company_name") or "").strip(),
        "registration_number": (data.get("registration_number") or "").strip() or None,
        "gst_number": (data.get("gst_number") or "").strip() or None,
        "pan_number": (data.get("pan_number") or "").strip() or None,
        "contact_person_name": (data.get("contact_person_name") or "").strip(),
        "contact_phone": (data.get("contact_phone") or "").strip(),
        "contact_email": (data.get("contact_email") or "").strip() or None,
        "address": (data.get("address") or "").strip(),
        "city": (data.get("city") or "").strip(),
        "district": (data.get("district") or "").strip(),
        "state": (data.get("state") or "").strip(),
        "pin_code": (data.get("pin_code") or "").strip(),
        "description": (data.get("description") or "").strip(),
        "specializations": specs,
        "max_active_tasks": int(data.get("max_active_tasks") or 10),
    }
    if include_status:
        out["status"] = data.get("status", "Active")
        out["status_reason"] = (data.get("status_reason") or "").strip() or None
    else:
        out["status"] = "Active"
    return out


# ── Auth ──────────────────────────────────────────────────────────────────────

@api_bp.route("/health")
def health():
    """Liveness (+ optional deep DB check). Watchdog uses the shallow path."""
    from datetime import datetime, timezone

    from routes.admission import heavy_inflight

    now = datetime.now(timezone.utc)
    from flask import current_app, has_app_context

    svc = "portal"
    if has_app_context():
        svc = current_app.config.get("SMARTROAD_SERVICE") or os.getenv("SMARTROAD_SERVICE") or "portal"
    else:
        svc = os.getenv("SMARTROAD_SERVICE") or "portal"
    out = {
        "ok": True,
        "utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "epoch": int(now.timestamp()),
        "heavy_inflight": heavy_inflight(),
        "pid": os.getpid(),
        "service": svc,
    }
    try:
        from ffmpeg_accel import accel_label
        from model_loader import resolve_yolo_device

        out["yolo_device"] = resolve_yolo_device()
        out["ffmpeg_encode"] = accel_label()
    except Exception:
        pass
    try:
        from routes.finalize_queue import queue_counts

        out["finalize_queue"] = queue_counts()
    except Exception:
        pass
    try:
        out["db_host"] = db_utils.resolve_db_host()
    except Exception:
        pass
    if (request.args.get("deep") or "").strip() in ("1", "true", "yes"):
        try:
            if db_utils.is_db_configured():
                # Bounded wall-clock — never let deep health take Waitress hostage.
                db_utils.ping_db()
                out["db"] = "ok"
            else:
                out["db"] = "unconfigured"
        except Exception as e:
            out["ok"] = False
            out["db"] = "error"
            out["db_error"] = str(e)[:200]
            return jsonify(out), 503
    return jsonify(out)


@api_bp.route("/auth/me")
@login_required
def auth_me():
    return jsonify(_user_payload())


@api_bp.route("/auth/login", methods=["POST"])
def auth_login():
    """Always verify username/password from the body.

    Never short-circuit on an existing cookie/JWT — that left users "stuck"
    as the previous account (e.g. senxxel → logout → video still returns senxxel).
    """
    from flask import current_app, make_response
    from routes.session_guard import (
        clear_session_cookie,
        expire_auth_cookies,
        is_mobile_client,
        stamp_session,
    )
    from routes.token_auth import (
        issue_access_token,
        token_ttl_s,
        wants_bearer_auth,
    )

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400

    try:
        row = db_utils.get_user_by_username(username)
    except Exception as e:
        return jsonify({
            "error": "Could not reach the database — retry in a moment.",
            "detail": str(e)[:200],
        }), 503
    if not row or not check_password_hash(row["password_hash"], password):
        return jsonify({"error": "Invalid credentials"}), 401

    # Drop any prior identity before switching users.
    prev_id = None
    try:
        if current_user.is_authenticated:
            prev_id = int(current_user.id)
    except Exception:
        prev_id = None
    clear_session_cookie()
    if prev_id is not None and prev_id != int(row["id"]):
        try:
            db_utils.bump_user_token_version(prev_id)
        except Exception:
            pass
        try:
            from routes.user_model import invalidate_user_cache

            invalidate_user_cache(prev_id)
        except Exception:
            pass
    try:
        from routes.user_model import invalidate_user_cache

        invalidate_user_cache(int(row["id"]))
    except Exception:
        pass

    user = User(row)
    try:
        db_utils.update_last_login(user.id)
    except Exception:
        pass

    use_jwt = wants_bearer_auth() or is_mobile_client()
    if use_jwt:
        # Mobile: JWT only — do not plant remember/session cookies.
        ver = int(row.get("token_version") or 0)
        try:
            ver = db_utils.get_user_token_version(user.id)
        except Exception:
            pass
        body = {
            "id": user.id,
            "username": user.username,
            "full_name": user.full_name,
            "email": user.email,
            "role": user.role,
            "vendor_id": user.vendor_id,
            "state_id": user.state_id,
            "district_id": user.district_id,
            "district_ids": user.district_ids,
            "is_admin": user.is_admin(),
            "is_supervisor": user.is_supervisor(),
            "is_allocator": user.is_allocator(),
            "is_vendor": user.is_vendor_role(),
            "is_videographer": user.is_videographer(),
            "access_token": issue_access_token(user.id, ver),
            "token_type": "Bearer",
            "expires_in": token_ttl_s(),
        }
        if user.is_videographer():
            survey_service.schedule_prewarm_snap_indexes(
                user.district_ids
                or ([user.district_id] if user.district_id is not None else [])
            )
        resp = make_response(jsonify(body))
        expire_auth_cookies(resp, current_app.config)
        return resp

    login_user(user, remember=False)
    stamp_session(authenticated=True)
    return jsonify(_user_payload())


@api_bp.route("/auth/logout", methods=["POST"])
def auth_logout():
    """Clear web cookies hard + bump token_version so mobile JWTs die."""
    from flask import current_app, make_response
    from routes.session_guard import clear_session_cookie, expire_auth_cookies
    from routes.token_auth import bearer_from_request, verify_access_token

    uid = None
    if current_user.is_authenticated:
        uid = int(current_user.id)
    else:
        tok = bearer_from_request()
        if tok:
            payload = verify_access_token(tok)
            if payload:
                uid = int(payload["uid"])
    if uid is not None:
        try:
            db_utils.bump_user_token_version(uid)
        except Exception as e:
            print(f"[auth] token bump failed: {e}", flush=True)
        try:
            from routes.user_model import invalidate_user_cache

            invalidate_user_cache(uid)
        except Exception:
            pass
    clear_session_cookie()
    resp = make_response(jsonify({"ok": True}))
    expire_auth_cookies(resp, current_app.config)
    return resp


# ── Dashboard ─────────────────────────────────────────────────────────────────

_DASHBOARD_WARRANTY_TS = 0.0
_DASHBOARD_WARRANTY_LOCK = threading.Lock()
_GPS_COVERAGE_CACHE = {"ts": 0.0, "data": None}
_GPS_COVERAGE_LOCK = threading.Lock()
_GPS_COVERAGE_TTL = float(os.getenv("GPS_COVERAGE_CACHE_S", "15"))


def _retry_until(label, fn, *, attempts=None, delay_s=None):
    """Wait and retry until the call succeeds — never return fake empty data.

    Multi-user load may briefly exhaust the pool; we queue/retry instead of
    blanking the UI.
    """
    attempts = max(1, int(attempts if attempts is not None else os.getenv("API_DB_RETRIES", "8")))
    delay_s = float(delay_s if delay_s is not None else os.getenv("API_DB_RETRY_S", "0.35"))
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last = e
            print(f"[retry] {label} attempt {i + 1}/{attempts}: {e}", flush=True)
            if i < attempts - 1:
                time.sleep(delay_s * (1.0 + 0.25 * i))
    raise last or RuntimeError(f"{label} failed after {attempts} attempts")


def _dashboard_section(label, fn):
    """Dashboard section: short retry, then soft-empty — never stampede the pool."""
    try:
        return _retry_until(label, fn, attempts=2, delay_s=0.2)
    except Exception as e:
        print(f"[dashboard] {label} gave up: {e}", flush=True)
        return None


def _gps_coverage_cached():
    from routes import tracking_service

    now = time.time()
    with _GPS_COVERAGE_LOCK:
        hit = _GPS_COVERAGE_CACHE.get("data")
        if hit is not None and (now - float(_GPS_COVERAGE_CACHE.get("ts") or 0)) < _GPS_COVERAGE_TTL:
            return hit
    data = _retry_until("gps_coverage", tracking_service.aggregate_gps_coverage_bundle)
    with _GPS_COVERAGE_LOCK:
        _GPS_COVERAGE_CACHE["ts"] = now
        _GPS_COVERAGE_CACHE["data"] = data
    return data


def _maybe_refresh_warranty():
    """At most once per hour — full UPDATE on every page load made dashboard feel dead."""
    global _DASHBOARD_WARRANTY_TS

    now = time.time()
    with _DASHBOARD_WARRANTY_LOCK:
        if now - _DASHBOARD_WARRANTY_TS < 3600:
            return
        _DASHBOARD_WARRANTY_TS = now
    try:
        _retry_until("refresh_warranty", db_utils.refresh_warranty_statuses, attempts=3)
    except Exception as e:
        # Background refresh — do not block the whole dashboard paint.
        print(f"[dashboard] refresh_warranty gave up: {e}", flush=True)


@api_bp.route("/dashboard")
@login_required
def dashboard_all():
    """Build dashboard on the request thread — retry each section until real data."""
    u = current_user
    user_id = int(u.id)
    username = (u.username or "").strip()
    is_vg = bool(u.is_videographer())
    is_admin = bool(u.is_admin())
    is_supervisor = bool(u.is_supervisor())
    is_vendor = bool(u.is_vendor_role())
    state_id = getattr(u, "state_id", None)
    district_id_attr = getattr(u, "district_id", None)
    district_ids = list(getattr(u, "district_ids", None) or [])
    if not district_ids and district_id_attr is not None:
        district_ids = [int(district_id_attr)]

    _maybe_refresh_warranty()

    gps_bundle = _dashboard_section("gps_coverage", _gps_coverage_cached) or {
        "all": {}, "by_state": {}, "date": None,
    }

    payload = {
        "kpi": _dashboard_section("kpi", db_utils.get_kpi_summary),
        "breached": _dashboard_section("breached", db_utils.get_sla_breached_tasks),
        "vendors": _dashboard_section("vendors", db_utils.get_vendor_performance_report),
        "warranty": _dashboard_section("warranty", db_utils.get_warranty_dashboard),
        "map": _dashboard_section(
            "map",
            lambda: db_utils.get_pothole_map_data(limit=int(os.getenv("DASHBOARD_MAP_LIMIT", "1500"))),
        ),
        "gps_coverage": gps_bundle.get("all") or {},
        "gps_coverage_by_state": gps_bundle.get("by_state") or {},
        "role_view": "admin",
    }
    if is_vg and not is_admin:
        district_id = district_ids[0] if district_ids else district_id_attr
        state_key = survey_service.resolve_state_key(state_id=state_id)
        field = {
            "state_id": state_id,
            "district_id": district_id,
            "district_ids": district_ids,
            "district_name": None,
            "district_names": [],
            "state_name": None,
            "videos_uploaded": 0,
            "km_today": 0,
            "covered_km": 0,
            "covered_by_class": {"nh": 0, "sh": 0, "mdr": 0, "other": 0},
            "segments_today": 0,
            "target_km": float(survey_service.get_settings().get("daily_km", 100)),
            "focus_road_class": survey_service.get_settings().get("focus_road_class", "all"),
            "quota_incomplete": True,
        }
        if district_ids and state_key:
            names = []
            for did in district_ids:
                dist = survey_service.get_district(did, state_key) or survey_service.get_district(did)
                if dist:
                    names.append(dist.get("name") or str(did))
                    if not field["district_name"]:
                        field["district_name"] = dist.get("name")
                        field["state_name"] = dist.get("state_name")
            field["district_names"] = names
            field["state_keys"] = survey_service.state_keys_for_district_ids(district_ids)
            summary = _dashboard_section(
                "field_assignment",
                lambda: survey_service.assignment_summary_for_user(
                    user_id, light=True, sync_coverage=False,
                ),
            ) or {}
            field["km_today"] = summary.get("total_km", 0)
            field["covered_km"] = summary.get("covered_km", 0)
            field["covered_by_class"] = summary.get("covered_by_class") or field["covered_by_class"]
            field["segments_today"] = summary.get("segment_count", 0)
            field["target_km"] = summary.get("target_km", field["target_km"])
            field["quota_incomplete"] = summary.get("quota_incomplete", True)
        field["gps_coverage"] = _dashboard_section(
            "field_gps",
            lambda: tracking_service.aggregate_gps_coverage(user_id=user_id),
        ) or {}
        # Prefer resolved GPS aggregate (same source as Tracking) over stale
        # assignment-row covered / all-"other" class blobs.
        gc = field.get("gps_coverage") or {}
        gc_km = float(gc.get("covered_km") or 0)
        gc_by = gc.get("by_class") or {}
        if gc_km > 0.01 or any(float(gc_by.get(k) or 0) > 0.01 for k in ("nh", "sh", "mdr", "other")):
            field["covered_km"] = gc_km if gc_km > 0.01 else field.get("covered_km")
            field["covered_by_class"] = gc_by or field["covered_by_class"]
            if float(gc.get("assigned_km") or 0) > 0.01:
                field["km_today"] = gc.get("assigned_km")
        try:
            keys = field_upload_service.list_recent_keys(limit=50).get("keys") or []
            media_ext = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".jpg", ".jpeg", ".png")
            field["videos_uploaded"] = sum(
                1
                for k in keys
                if username
                and (f"/{username}/" in f"/{k}" or k.startswith(f"{username}/"))
                and str(k).lower().endswith(media_ext)
            )
        except Exception:
            field["videos_uploaded"] = 0
        payload["field"] = field
        payload["role_view"] = "videographer"
    elif is_supervisor and not is_admin:
        payload["role_view"] = "supervisor"
    elif is_vendor:
        payload["role_view"] = "vendor"
    return jsonify(_serialize(payload))


@api_bp.route("/dashboard/map-data")
@login_required
def dashboard_map():
    data = _retry_until(
        "map-data",
        lambda: db_utils.get_pothole_map_data(
            limit=int(os.getenv("DASHBOARD_MAP_LIMIT", "1500")),
        ),
    )
    return jsonify(_serialize(data))


@api_bp.route("/users")
@login_required
def users_list():
    if not current_user.is_admin():
        abort(403)
    return jsonify(_serialize(_retry_until("users", db_utils.get_all_users)))


@api_bp.route("/users", methods=["POST"])
@login_required
def users_create():
    if not current_user.is_admin():
        abort(403)
    data = request.get_json(silent=True) or {}
    role = (data.get("role") or "").strip()
    state_id = data.get("state_id")
    district_ids = db_utils.normalize_district_ids(
        data.get("district_ids"), data.get("district_id"),
    )
    if role == "Videographer" and not state_id:
        return jsonify({"error": "state_id required for Videographer role (AP=1, TG=2)"}), 400
    if role == "Videographer":
        district_ids = district_ids or []
    try:
        uid = db_utils.create_user(
            username=data["username"].strip(),
            password_hash=generate_password_hash(data["password"]),
            full_name=(data.get("full_name") or "").strip(),
            email=(data.get("email") or "").strip(),
            role=role,
            vendor_id=int(data["vendor_id"]) if data.get("vendor_id") else None,
            state_id=int(state_id) if state_id else None,
            district_id=district_ids[0] if district_ids else None,
            district_ids=district_ids or None,
        )
        return jsonify({"id": uid}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@api_bp.route("/users/<int:user_id>/state", methods=["PATCH", "PUT"])
@login_required
def users_update_state(user_id):
    """Admin: set videographer state (AP/TG) only — districts chosen on mobile."""
    if not current_user.is_admin():
        abort(403)
    data = request.get_json(silent=True) or {}
    state_id = data.get("state_id")
    if state_id is None:
        return jsonify({"error": "state_id required"}), 400
    try:
        row = db_utils.update_user_state(user_id, int(state_id))
        if not row:
            return jsonify({"error": "Videographer not found"}), 404
        return jsonify(_serialize(row))
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@api_bp.route("/users/<int:user_id>", methods=["DELETE"])
@login_required
def users_delete(user_id):
    if not current_user.is_admin():
        abort(403)
    if int(user_id) == int(current_user.id):
        return jsonify({"error": "Cannot delete your own account"}), 400
    try:
        ok = db_utils.delete_user(int(user_id))
        if not ok:
            return jsonify({"error": "User not found"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@api_bp.route("/users/<int:user_id>/districts", methods=["PATCH", "PUT"])
@login_required
def users_update_districts(user_id):
    """Admin: set videographer state + one or more districts (cannot clear all)."""
    if not current_user.is_admin():
        abort(403)
    data = request.get_json(silent=True) or {}
    state_id = data.get("state_id")
    district_ids = db_utils.normalize_district_ids(
        data.get("district_ids"), data.get("district_id"),
    )
    if not district_ids:
        return jsonify({"error": "Select at least one district (cannot remove the last one)"}), 400
    if not state_id:
        keys = survey_service.state_keys_for_district_ids(district_ids)
        if keys:
            state_id = survey_service.STATE_ID_BY_KEY[keys[0]]
    if not state_id:
        return jsonify({"error": "state_id required"}), 400
    try:
        row = db_utils.update_user_districts(user_id, int(state_id), district_ids)
        if not row:
            return jsonify({"error": "Videographer not found"}), 404
        return jsonify(_serialize(row))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ── Vendors ───────────────────────────────────────────────────────────────────

@api_bp.route("/vendors")
@login_required
def vendors_list():
    search = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "")

    def _load():
        vendors = db_utils.get_all_vendors(status_filter=status_filter or None) or []
        if search:
            sl = search.lower()
            vendors = [
                v for v in vendors
                if sl in (v.get("company_name") or "").lower()
                or sl in (v.get("city") or "").lower()
                or sl in (v.get("pin_code") or "").lower()
            ]
        return vendors

    return jsonify(_serialize(_retry_until("vendors", _load)))


@api_bp.route("/vendors/<int:vendor_id>")
@login_required
def vendors_get(vendor_id):
    def _load():
        vendor = db_utils.get_vendor(vendor_id)
        if not vendor:
            return None
        work_orders = db_utils.get_work_orders(vendor_id=vendor_id) or []
        return {"vendor": vendor, "work_orders": work_orders}

    payload = _retry_until(f"vendor:{vendor_id}", _load)
    if not payload:
        abort(404)
    return jsonify(_serialize(payload))


@api_bp.route("/vendors", methods=["POST"])
@login_required
def vendors_create():
    if not current_user.is_allocator():
        abort(403)
    data = _vendor_from_request(request.get_json(silent=True) or {})
    try:
        vid = db_utils.create_vendor(data)
        return jsonify({"id": vid}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@api_bp.route("/vendors/<int:vendor_id>", methods=["PUT"])
@login_required
def vendors_update(vendor_id):
    if not current_user.is_allocator():
        abort(403)
    if not db_utils.get_vendor(vendor_id):
        abort(404)
    data = _vendor_from_request(request.get_json(silent=True) or {}, include_status=True)
    try:
        db_utils.update_vendor(vendor_id, data)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@api_bp.route("/vendors/<int:vendor_id>", methods=["DELETE"])
@login_required
def vendors_delete(vendor_id):
    if not current_user.is_admin():
        abort(403)
    try:
        db_utils.delete_vendor(vendor_id)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@api_bp.route("/vendors/suggest")
@login_required
def vendors_suggest():
    session_id = request.args.get("session_id", type=int)
    if not session_id:
        return jsonify([])
    return jsonify(_serialize(db_utils.get_suggested_vendors(session_id)))


# ── Tasks / work orders ───────────────────────────────────────────────────────

@api_bp.route("/tasks")
@login_required
def tasks_list():
    filters = {
        "status": request.args.get("status", ""),
        "vendor_id": request.args.get("vendor_id", type=int),
        "city": request.args.get("city", "").strip(),
        "pin_code": request.args.get("pin_code", "").strip(),
        "date_from": request.args.get("date_from", ""),
        "date_to": request.args.get("date_to", ""),
    }

    def _load():
        work_orders = db_utils.get_work_orders(**{k: v for k, v in filters.items() if v}) or []
        all_sessions = db_utils.get_all_sessions() or []
        # Unassigned must ignore list filters — only sessions with no WO at all.
        existing = {wo["session_id"] for wo in (db_utils.get_work_orders() or [])}
        unassigned = [s for s in all_sessions if s["id"] not in existing]
        vendors = db_utils.get_all_vendors(status_filter="Active") or []
        return work_orders, unassigned, vendors

    work_orders, unassigned, vendors = _retry_until("tasks", _load)
    return jsonify(_serialize({
        "work_orders": work_orders,
        "unassigned_sessions": unassigned,
        "vendors": vendors,
        "filters": filters,
    }))


@api_bp.route("/tasks/<int:wo_id>")
@login_required
def tasks_get(wo_id):
    wo = db_utils.get_work_order(wo_id)
    if not wo:
        abort(404)
    return jsonify(_serialize({
        "work_order": wo,
        "potholes": db_utils.get_work_order_potholes(wo_id),
        "history": db_utils.get_status_history("work_order", wo_id),
        "vendors": db_utils.get_all_vendors(status_filter="Active"),
        "suggestions": db_utils.get_suggested_vendors(wo["session_id"]),
        "validation": db_utils.get_validation(wo_id),
        "warranty": db_utils.get_warranty_by_work_order(wo_id),
        "next_statuses": VALID_TRANSITIONS.get(wo["status"], []),
    }))


@api_bp.route("/tasks/create/<int:session_id>", methods=["POST"])
@login_required
def tasks_create(session_id):
    if not current_user.is_allocator():
        abort(403)
    try:
        wo_id = db_utils.create_work_order(session_id, current_user.username)
        return jsonify({"id": wo_id}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@api_bp.route("/tasks/<int:wo_id>", methods=["DELETE"])
@login_required
def tasks_delete(wo_id):
    if not current_user.is_admin():
        abort(403)
    if not db_utils.get_work_order(wo_id):
        abort(404)
    try:
        ok = db_utils.delete_work_order(wo_id)
        if not ok:
            abort(404)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@api_bp.route("/tasks/<int:wo_id>/allocate", methods=["POST"])
@login_required
def tasks_allocate(wo_id):
    if not current_user.is_allocator():
        abort(403)
    data = request.get_json(silent=True) or {}
    vendor_id = data.get("vendor_id")
    if not vendor_id:
        return jsonify({"error": "vendor_id required"}), 400
    try:
        db_utils.allocate_work_order(
            wo_id, int(vendor_id),
            data.get("estimated_budget"),
            (data.get("remarks") or "").strip(),
            current_user.username,
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@api_bp.route("/tasks/<int:wo_id>/status", methods=["POST"])
@login_required
def tasks_status(wo_id):
    data = request.get_json(silent=True) or {}
    new_status = (data.get("new_status") or "").strip()
    comment = (data.get("comment") or "").strip()
    actual_spend = data.get("actual_spend")

    wo = db_utils.get_work_order(wo_id)
    if not wo:
        abort(404)

    allowed = VALID_TRANSITIONS.get(wo["status"], [])
    if new_status not in allowed:
        return jsonify({"error": f"Cannot transition {wo['status']} → {new_status}"}), 400
    if current_user.is_vendor_role() and new_status not in ("WIP", "Completed"):
        abort(403)
    if new_status == "Verified":
        val = db_utils.get_validation(wo_id)
        if not val or val.get("final_result") != "PASS":
            return jsonify({"error": "Passing validation required"}), 400
    if not comment:
        return jsonify({"error": "Comment required"}), 400

    try:
        db_utils.update_work_order_status(
            wo_id, new_status, comment, current_user.username, actual_spend
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@api_bp.route("/tasks/potholes/<int:session_id>")
@login_required
def tasks_potholes(session_id):
    return jsonify(_serialize(db_utils.get_potholes_for_session(session_id)))


# ── Validation ────────────────────────────────────────────────────────────────

@api_bp.route("/validate/<int:wo_id>", methods=["POST"])
@login_required
def validate_upload(wo_id):
    wo = db_utils.get_work_order(wo_id)
    if not wo:
        abort(404)
    if current_user.is_vendor_role() and current_user.vendor_id != wo.get("vendor_id"):
        abort(403)
    if wo["status"] not in ("Completed", "WIP"):
        return jsonify({"error": "WO must be WIP or Completed"}), 400

    photo = request.files.get("after_photo")
    if not photo or photo.filename == "":
        return jsonify({"error": "Photo required"}), 400

    manual_lat = request.form.get("manual_lat", type=float)
    manual_lon = request.form.get("manual_lon", type=float)
    image_bytes = photo.read()

    try:
        result = validation_module.run_validation(
            wo, image_bytes, photo.filename, manual_lat, manual_lon, current_user.username
        )
        return jsonify(_serialize(result))
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@api_bp.route("/validate/<int:wo_id>/context")
@login_required
def validate_context(wo_id):
    wo = db_utils.get_work_order(wo_id)
    if not wo:
        abort(404)
    potholes = db_utils.get_potholes_for_session(wo["session_id"])
    ref = next((p for p in potholes if p.get("lat")), None)
    return jsonify(_serialize({"work_order": wo, "ref_pothole": ref}))


@api_bp.route("/validate/review/<int:val_id>", methods=["POST"])
@login_required
def validate_review(val_id):
    if not current_user.is_supervisor():
        abort(403)
    data = request.get_json(silent=True) or {}
    final = data.get("final_result")
    comment = (data.get("comment") or "").strip()
    wo_id = data.get("wo_id")
    if final not in ("PASS", "FAIL"):
        return jsonify({"error": "Invalid result"}), 400
    if not comment:
        return jsonify({"error": "Comment required"}), 400
    db_utils.supervisor_review_validation(val_id, final, comment, current_user.username)
    return jsonify({"ok": True})


@api_bp.route("/validate/queue")
@login_required
def validate_queue():
    if not current_user.is_supervisor():
        abort(403)
    return jsonify(_serialize(db_utils.get_pending_supervisor_reviews()))


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
    state_key = request.args.get("state_key")
    state_id = request.args.get("state_id", type=int)
    if not state_key and state_id:
        state_key = survey_service.resolve_state_key(state_id=state_id)
    if not state_key:
        return jsonify({"error": "state_key or state_id required"}), 400
    try:
        return jsonify(survey_service.state_nh_geojson(state_key))
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 503


@api_bp.route("/survey/overview")
@login_required
def survey_overview():
    state_key = request.args.get("state_key") or "andhra"
    try:
        return jsonify(survey_service.state_nh_geojson(state_key))
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 503


@api_bp.route("/survey/segments")
@login_required
def survey_segments():
    state_key = request.args.get("state_key")
    state_id = request.args.get("state_id", type=int)
    district_id = request.args.get("district_id")
    if not state_key and state_id:
        state_key = survey_service.resolve_state_key(state_id=state_id)
    if not state_key or not district_id:
        return jsonify({"error": "state_key and district_id required"}), 400
    classes = request.args.get("classes", "").strip()
    road_classes = {c.strip() for c in classes.split(",") if c.strip()} or None
    try:
        return jsonify(survey_service.segments_geojson(
            state_key, str(district_id), road_classes=road_classes
        ))
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
    if not current_user.is_admin():
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

    if not current_user.is_admin():
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
    if not current_user.is_videographer() and not current_user.is_admin():
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
    if current_user.is_admin():
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

    if current_user.is_admin():
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
    if current_user.is_admin():
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


# ── Detection (admin) — migrated from Gradio app.py ───────────────────────────

def _admin_only():
    if not current_user.is_admin():
        abort(403)


@api_bp.route("/detection/status")
@login_required
def detection_status():
    _admin_only()
    return jsonify(detection_service.get_banners())


@api_bp.route("/detection/s3/catalog")
@login_required
def detection_s3_catalog():
    _admin_only()
    source = (request.args.get("source") or "videographer").strip().lower()
    return jsonify(detection_service.list_s3_catalog(source=source))


@api_bp.route("/detection/s3/keys")
@login_required
def detection_s3_keys():
    _admin_only()
    source = (request.args.get("source") or "videographer").strip().lower()
    catalog = detection_service.list_s3_catalog(source=source)
    keys = catalog.get("keys") or []
    if not keys:
        return jsonify({"keys": [], "catalog": catalog, "status": catalog.get("status")})
    return jsonify({
        "keys": keys,
        "catalog": catalog,
        "status": catalog.get("status"),
    })


@api_bp.route("/detection/s3", methods=["DELETE"])
@login_required
def detection_s3_delete():
    _admin_only()
    data = request.get_json(silent=True) or {}
    s3_key = data.get("s3_key")
    source = (data.get("source") or "").strip() or None
    return jsonify(detection_service.delete_s3_media(s3_key, source=source))


@api_bp.route("/detection/s3/preview")
@login_required
def detection_s3_preview():
    _admin_only()
    s3_key = request.args.get("s3_key")
    return jsonify(detection_service.preview_s3_selection(s3_key))


@api_bp.route("/detection/run", methods=["POST"])
@login_required
def detection_run():
    _admin_only()
    s3_key = (request.form.get("s3_key") or "").strip() or None
    capture_mode = request.form.get("capture_mode") or "walking"
    local_path = None
    gps_path = None

    media = request.files.get("media")
    gps = request.files.get("gps_log")
    import tempfile

    tmp_paths = []
    try:
        if media and media.filename:
            suffix = os.path.splitext(media.filename)[1] or ".bin"
            fd, local_path = tempfile.mkstemp(prefix="sr_media_", suffix=suffix)
            os.close(fd)
            media.save(local_path)
            tmp_paths.append(local_path)
        if gps and gps.filename:
            suffix = os.path.splitext(gps.filename)[1] or ".csv"
            fd, gps_path = tempfile.mkstemp(prefix="sr_gps_", suffix=suffix)
            os.close(fd)
            gps.save(gps_path)
            tmp_paths.append(gps_path)

        result = detection_service.run_detection(
            s3_key=s3_key,
            local_path=local_path if not s3_key else None,
            gps_path=gps_path,
            capture_mode=capture_mode,
        )
        return jsonify(result)
    finally:
        for p in tmp_paths:
            try:
                os.remove(p)
            except OSError:
                pass


@api_bp.route("/detection/files/<path:rel_path>")
@login_required
def detection_serve_file(rel_path):
    _admin_only()
    from flask import send_file

    path = detection_service.resolve_serve_path(rel_path)
    if not path:
        abort(404)
    return send_file(path)


@api_bp.route("/detection/sessions")
@login_required
def detection_sessions():
    _admin_only()
    source = (request.args.get("source") or "").strip() or None
    return jsonify(detection_service.load_sessions(source=source))


@api_bp.route("/detection/sessions/<int:session_id>/potholes")
@login_required
def detection_session_potholes(session_id):
    _admin_only()
    return jsonify(detection_service.load_session_potholes(session_id))


@api_bp.route("/detection/sessions/<int:session_id>/detail")
@login_required
def detection_session_detail(session_id):
    _admin_only()
    return jsonify(detection_service.load_session_detail(session_id))


# ── Citizen complaints (admin review) ─────────────────────────────────────────

@api_bp.route("/complaints")
@login_required
def complaints_list():
    _admin_only()
    from routes import reporter_service

    status = (request.args.get("status") or "").strip() or None
    try:
        items = reporter_service.list_complaints_admin(status=status)
        return jsonify({"complaints": items})
    except Exception as e:
        return jsonify({"complaints": [], "error": str(e)}), 500


@api_bp.route("/complaints/<int:complaint_id>/verify", methods=["POST"])
@login_required
def complaints_verify(complaint_id):
    _admin_only()
    from routes import reporter_service

    try:
        out = reporter_service.set_complaint_review_status(complaint_id, "Verified")
        return jsonify(out)
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/complaints/<int:complaint_id>/reject", methods=["POST"])
@login_required
def complaints_reject(complaint_id):
    _admin_only()
    from routes import reporter_service

    data = request.get_json(silent=True) or {}
    remark = data.get("remark") or data.get("rejection_remark") or ""
    try:
        out = reporter_service.set_complaint_review_status(
            complaint_id, "Rejected", remark=remark
        )
        return jsonify(out)
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/reports")
@login_required
def reports_list():
    _admin_only()
    from routes import report_service
    try:
        return jsonify(report_service.list_reports_for_ui())
    except Exception as e:
        return jsonify({"videographers": [], "total_sessions": 0, "error": str(e)}), 500


@api_bp.route("/reports/<int:session_id>/open")
@login_required
def reports_open(session_id):
    _admin_only()
    from routes import report_service
    import db_utils

    session = db_utils.get_session_by_id(int(session_id))
    if not session:
        abort(404)
    force = (request.args.get("force") or request.args.get("refresh") or "").strip().lower() in (
        "1", "true", "yes",
    )
    key = session.get("report_s3_key")
    if not key or force:
        # Generate on demand (force=1 refreshes stale DOCX with current length rules)
        result = report_service.generate_report_for_session(int(session_id), force=True)
        if not result.get("ok"):
            return jsonify(result), 500
        key = result.get("report_s3_key")
        session = db_utils.get_session_by_id(int(session_id)) or session
    if str(key or "").startswith("local:"):
        return jsonify({
            "ok": True,
            "url": f"/api/reports/{int(session_id)}/download",
            "report_s3_key": key,
            "filename": session.get("display_name") or session.get("filename"),
        })
    url = report_service.presign_report(key)
    if not url:
        return jsonify({"ok": False, "error": "Report not available"}), 404
    return jsonify({"ok": True, "url": url, "report_s3_key": key, "filename": session.get("display_name") or session.get("filename")})


@api_bp.route("/reports/<int:session_id>/download")
@login_required
def reports_download(session_id):
    _admin_only()
    import db_utils
    from flask import send_file

    session = db_utils.get_session_by_id(int(session_id))
    if not session:
        abort(404)
    key = session.get("report_s3_key") or ""
    if str(key).startswith("local:"):
        path = str(key)[len("local:"):]
        if not os.path.isfile(path):
            abort(404)
        return send_file(path, as_attachment=True, download_name=os.path.basename(path))
    # Stream from S3 via temporary download
    import s3_utils
    import tempfile
    from flask import after_this_request

    if not s3_utils.is_s3_configured() or not key:
        abort(404)
    fd, tmp = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    try:
        s3_utils.download_file(s3_utils.get_reports_bucket(), key, tmp)

        @after_this_request
        def _cleanup(response):
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return response

        return send_file(tmp, as_attachment=True, download_name=os.path.basename(key))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        abort(404)


@api_bp.route("/reports/<int:session_id>/generate", methods=["POST"])
@login_required
def reports_generate(session_id):
    _admin_only()
    from routes import report_service
    result = report_service.generate_report_for_session(int(session_id), force=True)
    code = 200 if result.get("ok") else 500
    return jsonify(result), code


# ── Model bench (admin) ───────────────────────────────────────────────────────

@api_bp.route("/model-bench/models")
@login_required
def model_bench_models():
    _admin_only()
    return jsonify(model_bench_service.list_models())


@api_bp.route("/model-bench/run-image", methods=["POST"])
@login_required
def model_bench_run_image():
    _admin_only()
    import tempfile

    model_id = request.form.get("model_id") or model_bench_service.get_default_model_id()
    conf = float(request.form.get("confidence") or 0.35)
    image = request.files.get("image")
    if not image or not image.filename:
        return jsonify({"status": {"kind": "error", "message": "Upload an image."}}), 400
    suffix = os.path.splitext(image.filename)[1] or ".jpg"
    fd, path = tempfile.mkstemp(prefix="sr_bench_", suffix=suffix)
    os.close(fd)
    try:
        image.save(path)
        return jsonify(model_bench_service.run_image_file(path, model_id, conf))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


@api_bp.route("/model-bench/run-frame", methods=["POST"])
@login_required
def model_bench_run_frame():
    _admin_only()
    model_id = request.form.get("model_id") or model_bench_service.get_default_model_id()
    conf = float(request.form.get("confidence") or 0.35)
    active = request.form.get("active", "true").lower() in ("1", "true", "yes")
    frame = request.files.get("frame")
    data = frame.read() if frame else b""
    return jsonify(model_bench_service.run_frame_bytes(data, model_id, conf, active=active))


# ── Field upload (videographer) ───────────────────────────────────────────────

def _videographer_only():
    if not current_user.is_videographer():
        abort(403)


@api_bp.route("/upload/status")
@login_required
def upload_status():
    _videographer_only()
    from s3_utils import get_input_bucket, is_s3_configured

    if is_s3_configured():
        return jsonify({
            "status": field_upload_service._status(
                "ok",
                f"S3 input bucket: {get_input_bucket()}",
            ),
            "bucket": get_input_bucket(),
        })
    return jsonify({
        "status": field_upload_service._status("warn", "S3 not configured."),
        "bucket": None,
    })


@api_bp.route("/upload", methods=["POST"])
@login_required
def upload_field():
    _videographer_only()
    import tempfile

    media = request.files.get("media")
    if not media or not media.filename:
        return jsonify({"status": {"kind": "error", "message": "Select a video or image to upload."}}), 400
    gps = request.files.get("gps_log")
    if not gps or not gps.filename:
        return jsonify({
            "status": {"kind": "error", "message": "GPS log is required — enable location and record before upload."},
        }), 400
    frame_meta = request.files.get("frame_meta")
    media_suffix = os.path.splitext(media.filename)[1] or ".bin"
    fd, media_path = tempfile.mkstemp(prefix="sr_up_", suffix=media_suffix)
    os.close(fd)
    gps_path = None
    frame_path = None
    tmp = [media_path]
    try:
        media.save(media_path)
        gps_suffix = os.path.splitext(gps.filename)[1] or ".csv"
        fd2, gps_path = tempfile.mkstemp(prefix="sr_gps_", suffix=gps_suffix)
        os.close(fd2)
        gps.save(gps_path)
        tmp.append(gps_path)
        try:
            from pathlib import Path as _Path
            raw = _Path(gps_path).read_text(encoding="utf-8", errors="ignore")
            data_lines = [
                ln for ln in raw.splitlines()
                if ln.strip() and not ln.lower().startswith("videosecond")
            ]
            if len(data_lines) < 1:
                return jsonify({
                    "status": {"kind": "error", "message": "GPS log has no points — enable location and retry."},
                }), 400
        except Exception:
            pass
        if frame_meta and frame_meta.filename:
            fd3, frame_path = tempfile.mkstemp(prefix="sr_frames_", suffix=".json")
            os.close(fd3)
            frame_meta.save(frame_path)
            tmp.append(frame_path)
        result = field_upload_service.upload_to_input(
            media_path,
            media.filename,
            gps_path=gps_path,
            gps_filename=gps.filename,
            username=current_user.username,
            frame_meta_path=frame_path,
            frame_meta_filename=frame_meta.filename if frame_meta else None,
            user_id=int(current_user.id),
            route_label=(request.form.get("route_label") or request.form.get("route_folder") or "").strip() or None,
            start_label=(request.form.get("start_label") or "").strip() or None,
            end_label=(request.form.get("end_label") or "").strip() or None,
        )
        if (result.get("status") or {}).get("kind") == "ok":
            capture_sid = (
                request.form.get("capture_session_id")
                or request.form.get("session_id")
                or ""
            ).strip() or None
            _finalize_field_upload_side_effects(
                result,
                user_id=int(current_user.id),
                media_title=media.filename,
                gps_path=gps_path,
                capture_session_id=capture_sid,
            )
        return jsonify(result)
    finally:
        for p in tmp:
            try:
                os.remove(p)
            except OSError:
                pass


def _finalize_field_upload_side_effects(
    result: dict,
    *,
    user_id: int,
    media_title: str | None,
    gps_path: str | None,
    capture_session_id: str | None,
) -> None:
    """Tracking commit + auto_track verify + seal roads (shared by classic + multipart)."""
    from routes.upload_side_effects import after_chunk_finalize

    after_chunk_finalize(
        result,
        user_id=user_id,
        media_title=media_title,
        gps_path=gps_path,
        capture_session_id=capture_session_id,
    )


@api_bp.route("/upload/session/init", methods=["POST"])
@login_required
def upload_session_init():
    """Start a chunked capture session (1‑min clips appended on the Flask host)."""
    _videographer_only()
    data = request.get_json(silent=True) or {}
    sid = (
        data.get("capture_session_id") or data.get("session_id") or ""
    ).strip()
    if not sid:
        return jsonify({"ok": False, "status": {"kind": "error", "message": "capture_session_id required"}}), 400
    out = field_upload_service.init_chunk_session(
        username=current_user.username,
        session_id=sid,
        user_id=int(current_user.id),
    )
    return jsonify(out)


@api_bp.route("/upload/session/chunk", methods=["POST"])
@login_required
def upload_session_chunk():
    """Receive one ~1‑min MP4 segment (multipart FormData). Prefer chunk-bin on field phones."""
    _videographer_only()
    import tempfile
    from routes.upload_guard import (
        UploadBodyTimeout,
        release_chunk_slot,
        try_acquire_chunk_slot,
    )

    if not try_acquire_chunk_slot(wait_s=0.05):
        resp = jsonify({
            "ok": False,
            "status": {
                "kind": "warn",
                "message": "Server is busy receiving other uploads — retry in a few seconds.",
            },
            "retry_after": 5,
        })
        resp.status_code = 503
        resp.headers["Retry-After"] = "5"
        return resp

    tmp_path = None
    try:
        try:
            sid = (
                request.form.get("capture_session_id") or request.form.get("session_id") or ""
            ).strip()
            media = request.files.get("media") or request.files.get("chunk")
            idx_raw = request.form.get("chunk_index")
            lat_raw = request.form.get("lat")
            lon_raw = request.form.get("lon")
            acc_raw = request.form.get("accuracy")
        except UploadBodyTimeout as e:
            return jsonify({
                "ok": False,
                "status": {
                    "kind": "error",
                    "message": f"Upload stalled (mobile network?) — {e}. Retry the same minute.",
                },
            }), 408

        if not sid:
            return jsonify({"ok": False, "status": {"kind": "error", "message": "capture_session_id required"}}), 400
        if not media or not media.filename:
            return jsonify({"ok": False, "status": {"kind": "error", "message": "media (video chunk) required"}}), 400
        try:
            chunk_index = int(idx_raw) if idx_raw is not None and str(idx_raw).strip() != "" else None
        except (TypeError, ValueError):
            return jsonify({"ok": False, "status": {"kind": "error", "message": "Invalid chunk_index"}}), 400

        suffix = os.path.splitext(media.filename)[1] or ".mp4"
        fd, tmp_path = tempfile.mkstemp(prefix="sr_chunk_", suffix=suffix)
        os.close(fd)
        try:
            media.save(tmp_path)
        except UploadBodyTimeout as e:
            return jsonify({
                "ok": False,
                "status": {
                    "kind": "error",
                    "message": f"Upload stalled (mobile network?) — {e}. Retry the same minute.",
                },
            }), 408
        except OSError as e:
            return jsonify({
                "ok": False,
                "status": {"kind": "error", "message": f"Upload interrupted: {e}"},
            }), 408

        if os.path.getsize(tmp_path) < 1:
            return jsonify({
                "ok": False,
                "status": {"kind": "error", "message": "Empty chunk body"},
            }), 400

        return _finish_session_chunk(
            sid=sid,
            tmp_path=tmp_path,
            chunk_index=chunk_index,
            lat_raw=lat_raw,
            lon_raw=lon_raw,
            acc_raw=acc_raw,
        )
    finally:
        release_chunk_slot()
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


@api_bp.route("/upload/session/chunk-bin", methods=["POST"])
@login_required
def upload_session_chunk_bin():
    """Raw video body (no multipart). Faster on mobile-data — phone streams bytes straight in.

    Headers: X-Capture-Session-Id, X-Chunk-Index, optional X-Lat / X-Lon / X-Accuracy.
    Body: video/mp4 octets.
    """
    _videographer_only()
    import tempfile
    from routes.upload_guard import (
        UploadBodyTimeout,
        release_chunk_slot,
        try_acquire_chunk_slot,
    )

    if not try_acquire_chunk_slot(wait_s=0.05):
        resp = jsonify({
            "ok": False,
            "status": {
                "kind": "warn",
                "message": "Server is busy receiving other uploads — retry in a few seconds.",
            },
            "retry_after": 5,
        })
        resp.status_code = 503
        resp.headers["Retry-After"] = "5"
        return resp

    tmp_path = None
    try:
        sid = (
            request.headers.get("X-Capture-Session-Id")
            or request.headers.get("X-Capture-Session-ID")
            or request.args.get("capture_session_id")
            or ""
        ).strip()
        idx_raw = (
            request.headers.get("X-Chunk-Index")
            or request.args.get("chunk_index")
        )
        lat_raw = request.headers.get("X-Lat") or request.args.get("lat")
        lon_raw = request.headers.get("X-Lon") or request.args.get("lon")
        acc_raw = request.headers.get("X-Accuracy") or request.args.get("accuracy")

        if not sid:
            return jsonify({"ok": False, "status": {"kind": "error", "message": "X-Capture-Session-Id required"}}), 400
        try:
            chunk_index = int(idx_raw) if idx_raw is not None and str(idx_raw).strip() != "" else None
        except (TypeError, ValueError):
            return jsonify({"ok": False, "status": {"kind": "error", "message": "Invalid X-Chunk-Index"}}), 400

        fd, tmp_path = tempfile.mkstemp(prefix="sr_chunk_", suffix=".mp4")
        os.close(fd)
        # Stream raw bytes. Do NOT use request.stream when Content-Length is
        # missing — Werkzeug returns empty BytesIO under MAX_CONTENT_LENGTH.
        from flask import current_app
        from routes.upload_stream import drain_stream_to_file, open_upload_body_stream

        try:
            stream = open_upload_body_stream(
                request.environ,
                max_content_length=current_app.config.get("MAX_CONTENT_LENGTH"),
            )
            written = drain_stream_to_file(stream, tmp_path, buf_size=1024 * 1024)
        except UploadBodyTimeout as e:
            return jsonify({
                "ok": False,
                "status": {
                    "kind": "error",
                    "message": f"Upload stalled (mobile network?) — {e}. Retry the same minute.",
                },
            }), 408
        except OSError as e:
            return jsonify({
                "ok": False,
                "status": {"kind": "error", "message": f"Upload interrupted: {e}"},
            }), 408

        if written < 1 or os.path.getsize(tmp_path) < 1:
            return jsonify({
                "ok": False,
                "status": {
                    "kind": "error",
                    "message": "Empty chunk body — send Content-Length or use /upload/session/chunk multipart.",
                },
            }), 400

        return _finish_session_chunk(
            sid=sid,
            tmp_path=tmp_path,
            chunk_index=chunk_index,
            lat_raw=lat_raw,
            lon_raw=lon_raw,
            acc_raw=acc_raw,
        )
    finally:
        release_chunk_slot()
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _finish_session_chunk(*, sid, tmp_path, chunk_index, lat_raw, lon_raw, acc_raw):
    """Shared append + optional GPS piggyback for multipart and binary chunk paths."""
    out = field_upload_service.append_chunk(
        username=current_user.username,
        session_id=sid,
        chunk_path=tmp_path,
        chunk_index=chunk_index,
    )
    code = 200 if out.get("ok") else 400
    if out.get("ok"):
        try:
            if lat_raw is not None and lon_raw is not None:
                lat = float(lat_raw)
                lon = float(lon_raw)
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    try:
                        accuracy = float(acc_raw) if acc_raw is not None else None
                    except (TypeError, ValueError):
                        accuracy = None
                    ping_kwargs = dict(
                        user_id=int(current_user.id),
                        username=current_user.username,
                        full_name=getattr(current_user, "full_name", None),
                        state_id=getattr(current_user, "state_id", None),
                        district_id=getattr(current_user, "district_id", None),
                        lat=lat,
                        lon=lon,
                        accuracy=accuracy,
                        recording=True,
                        capture_session_id=sid,
                    )

                    def _bg_ping():
                        try:
                            tracking_service.record_ping(**ping_kwargs)
                        except Exception:
                            pass

                    import threading
                    threading.Thread(target=_bg_ping, daemon=True, name="chunk-ping").start()
        except (TypeError, ValueError):
            pass
    return jsonify(out), code


@api_bp.route("/upload/session/finalize", methods=["POST"])
@login_required
def upload_session_finalize():
    """Accept capture on server immediately; assemble + S3 + seal in background."""
    _videographer_only()
    import tempfile

    sid = (
        request.form.get("capture_session_id") or request.form.get("session_id") or ""
    ).strip()
    if not sid:
        return jsonify({"status": {"kind": "error", "message": "capture_session_id required"}}), 400
    gps = request.files.get("gps_log")
    if not gps or not gps.filename:
        return jsonify({
            "status": {"kind": "error", "message": "GPS log is required to finalize."},
        }), 400
    frame_meta = request.files.get("frame_meta")
    gps_path = None
    frame_path = None
    tmp: list[str] = []
    try:
        gps_suffix = os.path.splitext(gps.filename)[1] or ".csv"
        fd, gps_path = tempfile.mkstemp(prefix="sr_gps_", suffix=gps_suffix)
        os.close(fd)
        gps.save(gps_path)
        tmp.append(gps_path)
        if frame_meta and frame_meta.filename:
            fd2, frame_path = tempfile.mkstemp(prefix="sr_frames_", suffix=".json")
            os.close(fd2)
            frame_meta.save(frame_path)
            tmp.append(frame_path)

        uid = int(current_user.id)

        def _on_complete(result, staged_gps):
            # Only used when FINALIZE_EXTERNAL_WORKER=0 (legacy in-process).
            _finalize_field_upload_side_effects(
                result,
                user_id=uid,
                media_title=f"chunked:{sid}",
                gps_path=staged_gps,
                capture_session_id=sid,
            )

        result = field_upload_service.queue_chunk_finalize(
            username=current_user.username,
            session_id=sid,
            gps_path=gps_path,
            gps_filename=gps.filename,
            frame_meta_path=frame_path,
            frame_meta_filename=frame_meta.filename if frame_meta else None,
            user_id=uid,
            route_label=(request.form.get("route_label") or request.form.get("route_folder") or "").strip() or None,
            start_label=(request.form.get("start_label") or "").strip() or None,
            end_label=(request.form.get("end_label") or "").strip() or None,
            on_complete=_on_complete,
            media_title=f"chunked:{sid}",
            capture_session_id=sid,
        )
        code = 200 if result.get("ok") else 400
        return jsonify(result), code
    finally:
        for p in tmp:
            try:
                os.remove(p)
            except OSError:
                pass


@api_bp.route("/upload/session/discard", methods=["POST"])
@login_required
def upload_session_discard():
    """Delete streamed chunks on the server + provisional GPS trail."""
    _videographer_only()
    data = request.get_json(silent=True) or {}
    sid = (
        data.get("capture_session_id") or data.get("session_id")
        or request.form.get("capture_session_id") or ""
    ).strip()
    if not sid:
        return jsonify({"ok": False, "status": {"kind": "error", "message": "capture_session_id required"}}), 400
    out = field_upload_service.discard_chunk_session(
        username=current_user.username,
        session_id=sid,
    )
    try:
        from routes import tracking_service as _ts
        _ts.discard_capture_session(int(current_user.id), sid)
    except Exception:
        pass
    return jsonify(out)


@api_bp.route("/upload/multipart/init", methods=["POST"])
@login_required
def upload_multipart_init():
    """Start S3 multipart upload for large field videos (2–10 GB)."""
    _videographer_only()
    data = request.get_json(silent=True) or {}
    filename = (data.get("filename") or data.get("media_filename") or "capture.mp4").strip()
    size_bytes = data.get("size_bytes") or data.get("size")
    content_type = (data.get("content_type") or "video/mp4").strip()
    capture_sid = (data.get("capture_session_id") or data.get("session_id") or "").strip() or None
    try:
        size_i = int(size_bytes) if size_bytes is not None else None
    except (TypeError, ValueError):
        size_i = None
    out = field_upload_service.init_multipart_video(
        username=current_user.username,
        media_filename=filename,
        size_bytes=size_i,
        content_type=content_type,
        capture_session_id=capture_sid,
        user_id=int(current_user.id),
        route_label=(data.get("route_label") or data.get("route_folder") or "").strip() or None,
        start_label=(data.get("start_label") or "").strip() or None,
        end_label=(data.get("end_label") or "").strip() or None,
    )
    if not out.get("ok"):
        return jsonify(out), 400
    return jsonify(out)


@api_bp.route("/upload/multipart/presign", methods=["POST"])
@login_required
def upload_multipart_presign():
    """Presign one or many part PUT URLs."""
    _videographer_only()
    data = request.get_json(silent=True) or {}
    key = (data.get("key") or "").strip()
    upload_id = (data.get("upload_id") or "").strip()
    if not key or not upload_id:
        return jsonify({"error": "key and upload_id required"}), 400
    nums = data.get("part_numbers") or data.get("parts")
    if nums is None and data.get("part_number") is not None:
        nums = [data.get("part_number")]
    if not isinstance(nums, list) or not nums:
        return jsonify({"error": "part_numbers list required"}), 400
    try:
        nums = [int(n) for n in nums]
    except (TypeError, ValueError):
        return jsonify({"error": "invalid part_numbers"}), 400
    if len(nums) > 100:
        return jsonify({"error": "max 100 part URLs per request"}), 400
    try:
        return jsonify(field_upload_service.presign_parts(
            key, upload_id, nums, bucket=data.get("bucket"),
        ))
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@api_bp.route("/upload/multipart/part", methods=["POST"])
@login_required
def upload_multipart_part_relay():
    """Relay one video part phone→Flask→S3 when the device cannot PUT to AWS directly."""
    _videographer_only()
    import tempfile

    key = (request.form.get("key") or "").strip()
    upload_id = (request.form.get("upload_id") or "").strip()
    part_raw = request.form.get("part_number") or request.form.get("PartNumber")
    part_file = request.files.get("part") or request.files.get("file")
    if not key or not upload_id or part_raw is None or not part_file or not part_file.filename:
        return jsonify({"ok": False, "error": "key, upload_id, part_number, and part file required"}), 400
    try:
        part_number = int(part_raw)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid part_number"}), 400
    fd, part_path = tempfile.mkstemp(prefix="sr_mp_", suffix=".bin")
    os.close(fd)
    try:
        part_file.save(part_path)
        out = field_upload_service.relay_part(
            username=current_user.username,
            key=key,
            upload_id=upload_id,
            part_number=part_number,
            part_path=part_path,
            bucket=(request.form.get("bucket") or None),
        )
        status = 200 if out.get("ok") else 400
        return jsonify(out), status
    finally:
        try:
            os.remove(part_path)
        except OSError:
            pass


@api_bp.route("/upload/multipart/complete", methods=["POST"])
@login_required
def upload_multipart_complete():
    """Complete multipart video + attach GPS/frame sidecars (small form upload)."""
    _videographer_only()
    import tempfile
    import json as _json

    # JSON body OR multipart form (gps/frame files + fields)
    data = request.get_json(silent=True) or {}
    if request.form:
        data = {**data, **{k: request.form.get(k) for k in request.form}}
        if request.form.get("parts_json"):
            try:
                data["parts"] = _json.loads(request.form.get("parts_json"))
            except Exception:
                pass

    key = (data.get("key") or "").strip()
    upload_id = (data.get("upload_id") or "").strip()
    parts = data.get("parts") or []
    if not key or not upload_id or not parts:
        return jsonify({"error": "key, upload_id, and parts required"}), 400

    gps = request.files.get("gps_log")
    if not gps or not gps.filename:
        return jsonify({
            "status": {"kind": "error", "message": "GPS log is required with multipart complete."},
        }), 400

    frame_meta = request.files.get("frame_meta")
    gps_path = None
    frame_path = None
    tmp = []
    try:
        gps_suffix = os.path.splitext(gps.filename)[1] or ".csv"
        fd, gps_path = tempfile.mkstemp(prefix="sr_gps_", suffix=gps_suffix)
        os.close(fd)
        gps.save(gps_path)
        tmp.append(gps_path)
        if frame_meta and frame_meta.filename:
            fd2, frame_path = tempfile.mkstemp(prefix="sr_frames_", suffix=".json")
            os.close(fd2)
            frame_meta.save(frame_path)
            tmp.append(frame_path)

        result = field_upload_service.finish_multipart_video(
            username=current_user.username,
            key=key,
            upload_id=upload_id,
            parts=parts,
            gps_path=gps_path,
            gps_filename=gps.filename,
            frame_meta_path=frame_path,
            frame_meta_filename=frame_meta.filename if frame_meta else None,
            bucket=data.get("bucket"),
        )
        kind = (result.get("status") or {}).get("kind")
        # ok = full success; warn = video landed but a sidecar failed — still seal/match from local GPS
        if kind in ("ok", "warn"):
            capture_sid = (
                data.get("capture_session_id")
                or data.get("session_id")
                or ""
            )
            if isinstance(capture_sid, str):
                capture_sid = capture_sid.strip() or None
            else:
                capture_sid = None
            _finalize_field_upload_side_effects(
                result,
                user_id=int(current_user.id),
                media_title=os.path.basename(key),
                gps_path=gps_path,
                capture_session_id=capture_sid,
            )
        return jsonify(result)
    finally:
        for p in tmp:
            try:
                os.remove(p)
            except OSError:
                pass


@api_bp.route("/upload/multipart/abort", methods=["POST"])
@login_required
def upload_multipart_abort():
    _videographer_only()
    data = request.get_json(silent=True) or {}
    key = (data.get("key") or "").strip()
    upload_id = (data.get("upload_id") or "").strip()
    if not key or not upload_id:
        return jsonify({"error": "key and upload_id required"}), 400
    return jsonify(field_upload_service.abort_multipart(
        key, upload_id, bucket=data.get("bucket"),
    ))


@api_bp.route("/upload/keys")
@login_required
def upload_keys():
    _videographer_only()
    return jsonify(field_upload_service.list_recent_keys())


@api_bp.route("/survey/districts/<district_id>/reopen-roads", methods=["POST"])
@login_required
def survey_reopen_district_roads(district_id):
    """Admin: reopen completed roads in a district so they can be assigned again."""
    if not current_user.is_admin():
        abort(403)
    data = request.get_json(silent=True) or {}
    state_key = (data.get("state_key") or request.args.get("state_key") or "").strip()
    if not state_key and getattr(current_user, "state_id", None):
        state_key = survey_service.resolve_state_key(state_id=current_user.state_id)
    if not state_key:
        return jsonify({"error": "state_key required"}), 400
    return jsonify(survey_service.reopen_district_roads(
        state_key=state_key,
        district_id=district_id,
        only_completed=True,
    ))


# ── Live tracking (Capture → admin Tracking) ──────────────────────────────────

@api_bp.route("/tracking/ping", methods=["POST"])
@login_required
def tracking_ping():
    _videographer_only()
    data = request.get_json(silent=True) or {}
    try:
        lat = float(data.get("lat"))
        lon = float(data.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lon are required"}), 400
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return jsonify({"error": "Invalid coordinates"}), 400
    accuracy = data.get("accuracy")
    try:
        accuracy = float(accuracy) if accuracy is not None else None
    except (TypeError, ValueError):
        accuracy = None
    recording = data.get("recording", True)
    if isinstance(recording, str):
        recording = recording.lower() in ("1", "true", "yes")
    capture_session_id = (
        data.get("capture_session_id") or data.get("session_id") or None
    )
    if capture_session_id is not None:
        capture_session_id = str(capture_session_id).strip() or None
    return jsonify(tracking_service.record_ping(
        user_id=int(current_user.id),
        username=current_user.username,
        full_name=getattr(current_user, "full_name", None),
        state_id=getattr(current_user, "state_id", None),
        district_id=getattr(current_user, "district_id", None),
        lat=lat,
        lon=lon,
        accuracy=accuracy,
        recording=bool(recording),
        capture_session_id=capture_session_id,
    ))


@api_bp.route("/tracking/discard-session", methods=["POST"])
@login_required
def tracking_discard_session():
    """Drop provisional GPS coverage for a capture (discard or save-local)."""
    _videographer_only()
    data = request.get_json(silent=True) or {}
    sid = (data.get("capture_session_id") or data.get("session_id") or "").strip()
    if not sid:
        return jsonify({"error": "capture_session_id is required"}), 400
    return jsonify(tracking_service.discard_capture_session(int(current_user.id), sid))


@api_bp.route("/tracking/sync-coverage", methods=["POST"])
@login_required
def tracking_sync_coverage():
    """Admin: recompute coverage for all (or one) VG days and write both DB tables."""
    if not current_user.is_admin():
        abort(403)
    data = request.get_json(silent=True) or {}
    uid = data.get("user_id")
    try:
        uid = int(uid) if uid is not None else None
    except (TypeError, ValueError):
        return jsonify({"error": "invalid user_id"}), 400
    result = tracking_service.repair_all_coverage(user_id=uid)
    return jsonify(_serialize(result))


@api_bp.route("/tracking/videographers")
@login_required
def tracking_videographers():
    if not current_user.is_admin():
        abort(403)
    date = request.args.get("date") or tracking_service.today_ist()
    history = []
    try:
        history = [
            {
                "date": h.get("date"),
                "user_count": h.get("user_count"),
                "segment_count": h.get("segment_count"),
            }
            for h in (survey_service.assignment_history_summary(include_items=False) or [])
        ]
    except Exception:
        history = []
    return jsonify({
        "date": date,
        "videographers": tracking_service.list_videographers_for_admin(date),
        "assignments_geojson": survey_service.all_assigned_routes_geojson_today(date),
        "history": history,
    })


@api_bp.route("/tracking/videographers/<int:user_id>")
@login_required
def tracking_videographer_detail(user_id: int):
    if not current_user.is_admin():
        abort(403)
    date = request.args.get("date") or tracking_service.today_ist()
    detail = tracking_service.get_videographer_track(user_id, date)
    if detail.get("error") and not detail.get("features") and not detail.get("last"):
        return jsonify(detail), 400
    return jsonify(_serialize(detail))


@api_bp.route("/auto-track/gps", methods=["POST"])
@login_required
def auto_track_gps_upsert():
    """Videographer: set start/end and/or append free-drive GPS points (no fixed route)."""
    _videographer_only()
    data = request.get_json(silent=True) or {}
    try:
        res = auto_track_service.upsert_gps_track(
            int(current_user.id),
            gps_points=data.get("gps_points") or data.get("points") or [],
            start_lat=data.get("start_lat"),
            start_lon=data.get("start_lon"),
            end_lat=data.get("end_lat"),
            end_lon=data.get("end_lon"),
            start_label=data.get("start_label"),
            end_label=data.get("end_label"),
            track_date=data.get("date"),
            append=bool(data.get("append", True)),
        )
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@api_bp.route("/auto-track/uploads")
@login_required
def auto_track_uploads_list():
    day = request.args.get("date")
    if current_user.is_videographer():
        uid = int(current_user.id)
    elif current_user.is_admin():
        uid = request.args.get("user_id", type=int)
        if not uid:
            return jsonify({"error": "user_id required"}), 400
    else:
        abort(403)
    return jsonify({
        "date": day or auto_track_service.today_ist(),
        "uploads": auto_track_service.list_uploads_for_user(uid, day),
    })
