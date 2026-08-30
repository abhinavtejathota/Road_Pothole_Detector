"""API routes: tasks."""
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


# # ── Tasks / work orders ───────────────────────────────────────────────────────
# ── Tasks / work orders ───────────────────────────────────────────────────────

@api_bp.route("/tasks")
@login_required
def tasks_list():
    _staff_ops_only()
    filters = {
        "status": request.args.get("status", ""),
        "vendor_id": request.args.get("vendor_id", type=int),
        "city": request.args.get("city", "").strip(),
        "pin_code": request.args.get("pin_code", "").strip(),
        "date_from": request.args.get("date_from", ""),
        "date_to": request.args.get("date_to", ""),
    }
    if current_user.is_vendor_role():
        filters["vendor_id"] = current_user.vendor_id

    def _load():
        work_orders = db_utils.get_work_orders(**{k: v for k, v in filters.items() if v}) or []
        all_sessions = db_utils.get_all_sessions() or []
        # Unassigned must ignore list filters — only sessions with no WO at all.
        existing = {wo["session_id"] for wo in (db_utils.get_work_orders() or [])}
        unassigned = [s for s in all_sessions if s["id"] not in existing]
        vendors = []
        if current_user.is_dev_admin() or current_user.is_allocator() or current_user.is_supervisor():
            vendors = db_utils.get_all_vendors(status_filter="Active") or []
            return work_orders, unassigned, vendors
        # Vendors: no unassigned pool / competitor list
        return work_orders, [], vendors

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
    _staff_ops_only()
    wo = db_utils.get_work_order(wo_id)
    if not wo:
        abort(404)
    if current_user.is_vendor_role() and current_user.vendor_id != wo.get("vendor_id"):
        abort(403)
    vendors = []
    suggestions = []
    if current_user.is_dev_admin() or current_user.is_allocator() or current_user.is_supervisor():
        vendors = db_utils.get_all_vendors(status_filter="Active")
        suggestions = db_utils.get_suggested_vendors(wo["session_id"])
    return jsonify(_serialize({
        "work_order": wo,
        "potholes": db_utils.get_work_order_potholes(wo_id),
        "history": db_utils.get_status_history("work_order", wo_id),
        "vendors": vendors,
        "suggestions": suggestions,
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
    if not current_user.is_dev_admin():
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

    # Role + ownership gates
    if new_status in ("Verified", "Failed"):
        if not (current_user.is_supervisor() or current_user.is_dev_admin()):
            abort(403)
    elif new_status in ("WIP", "Completed"):
        if current_user.is_vendor_role():
            if current_user.vendor_id != wo.get("vendor_id"):
                abort(403)
        elif not (
            current_user.is_dev_admin()
            or current_user.is_supervisor()
            or current_user.is_allocator()
        ):
            abort(403)
    elif new_status in ("Allocated", "Created"):
        if not (current_user.is_allocator() or current_user.is_dev_admin()):
            abort(403)
    else:
        if not (current_user.is_dev_admin() or current_user.is_supervisor() or current_user.is_allocator()):
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
        return jsonify({"error": "Status update failed"}), 400


@api_bp.route("/tasks/potholes/<int:session_id>")
@login_required
def tasks_potholes(session_id):
    if not (
        current_user.is_dev_admin()
        or current_user.is_supervisor()
        or current_user.is_allocator()
        or current_user.is_vendor_role()
    ):
        abort(403)
    return jsonify(_serialize(db_utils.get_potholes_for_session(session_id)))


