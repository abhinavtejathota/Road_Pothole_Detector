"""API routes: vendors."""
import os
import threading
import time

from flask import jsonify, request, abort
from flask_login import login_required, login_user, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

import db_utils
from routes.api import api_bp
from routes.api._helpers import _serialize, _user_payload, _vendor_from_request, _retry_until, _dev_admin_only, _staff_ops_only
from routes.user_model import User
from routes.constants import VALID_TRANSITIONS
from routes import validation as validation_module
from routes import survey_service
from routes import detection_service
from routes import model_bench_service
from routes import field_upload_service
from routes import tracking_service
from routes import auto_track_service


# # ── Vendors ───────────────────────────────────────────────────────────────────
# ── Vendors ───────────────────────────────────────────────────────────────────

@api_bp.route("/vendors")
@login_required
def vendors_list():
    _staff_ops_only()
    search = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "")

    def _load():
        if current_user.is_vendor_role():
            v = db_utils.get_vendor(current_user.vendor_id) if current_user.vendor_id else None
            return [v] if v else []
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
    _staff_ops_only()
    if current_user.is_vendor_role() and current_user.vendor_id != vendor_id:
        abort(403)

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
    if not current_user.is_dev_admin():
        abort(403)
    try:
        db_utils.delete_vendor(vendor_id)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@api_bp.route("/vendors/suggest")
@login_required
def vendors_suggest():
    _staff_ops_only()
    session_id = request.args.get("session_id", type=int)
    if not session_id:
        return jsonify([])
    return jsonify(_serialize(db_utils.get_suggested_vendors(session_id)))


