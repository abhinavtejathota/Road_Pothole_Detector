"""API routes: tracking_routes."""
import os
import threading
import time

from flask import jsonify, request, abort
from flask_login import login_required, login_user, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

import db_utils
from routes.api import api_bp
from routes.api._helpers import _serialize, _user_payload, _vendor_from_request, _retry_until, _dev_admin_only, _videographer_only
from routes.user_model import User
from routes.constants import VALID_TRANSITIONS
from routes import validation as validation_module
from routes import survey_service
from routes import detection_service
from routes import model_bench_service
from routes import field_upload_service
from routes import tracking_service
from routes import auto_track_service


# # ── Live tracking (Capture → admin Tracking) ──────────────────────────────────
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
    if not current_user.can_manage_field_ops():
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
    if not current_user.can_manage_field_ops():
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
    if not current_user.can_manage_field_ops():
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
    elif current_user.can_manage_field_ops():
        uid = request.args.get("user_id", type=int)
        if not uid:
            return jsonify({"error": "user_id required"}), 400
    else:
        abort(403)
    return jsonify({
        "date": day or auto_track_service.today_ist(),
        "uploads": auto_track_service.list_uploads_for_user(uid, day),
    })
