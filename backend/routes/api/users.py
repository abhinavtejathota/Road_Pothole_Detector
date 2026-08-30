"""API routes: users."""
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


VALID_USER_ROLES = {"DevAdmin", "Admin", "Allocator", "Supervisor", "Vendor", "Videographer"}


@api_bp.route("/users")
@login_required
def users_list():
    if not current_user.is_dev_admin():
        abort(403)
    return jsonify(_serialize(_retry_until("users", db_utils.get_all_users)))


@api_bp.route("/users", methods=["POST"])
@login_required
def users_create():
    if not current_user.is_dev_admin():
        abort(403)
    data = request.get_json(silent=True) or {}
    role = (data.get("role") or "").strip()
    if role not in VALID_USER_ROLES:
        return jsonify({"error": f"Invalid role (allowed: {', '.join(sorted(VALID_USER_ROLES))})"}), 400
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"error": "username and password required"}), 400
    if len(str(password)) < 8:
        return jsonify({"error": "password must be at least 8 characters"}), 400
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
            username=username,
            password_hash=generate_password_hash(password),
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
        return jsonify({"error": "Could not create user"}), 400


@api_bp.route("/users/<int:user_id>/state", methods=["PATCH", "PUT"])
@login_required
def users_update_state(user_id):
    """Admin: set videographer state (AP/TG) only — districts chosen on mobile."""
    if not current_user.is_dev_admin():
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
    if not current_user.is_dev_admin():
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
    if not current_user.is_dev_admin():
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


