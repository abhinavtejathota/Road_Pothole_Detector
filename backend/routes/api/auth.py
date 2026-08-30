"""API routes: auth."""
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


# # ── Auth ──────────────────────────────────────────────────────────────────────
# ── Auth ──────────────────────────────────────────────────────────────────────

@api_bp.route("/health")
def health():
    """Liveness (+ optional deep DB check). Watchdog uses the shallow path.

    Shallow responses omit host/pid details to reduce reconnaissance.
    """
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
        "service": svc,
    }
    deep = (request.args.get("deep") or "").strip().lower() in ("1", "true", "yes")
    if not deep:
        return jsonify(out)

    # Deep path: still avoid leaking infra details to anonymous callers.
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
        from routes.detect_queue import auto_detect_enabled, queue_counts as detect_counts, s3_scan_enabled

        out["detect_queue"] = detect_counts()
        out["auto_detect"] = {
            "on_upload": auto_detect_enabled(),
            "scan_s3": s3_scan_enabled(),
        }
    except Exception:
        pass
    try:
        if db_utils.is_db_configured():
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
            "is_dev_admin": user.is_dev_admin(),
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


