"""API routes: validation_routes."""
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


# # ── Validation ────────────────────────────────────────────────────────────────
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


