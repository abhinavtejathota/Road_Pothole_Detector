"""API routes: detection_routes."""
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


# ── Detection (admin) — migrated from Gradio app.py ───────────────────────────

@api_bp.route("/detection/status")
@login_required
def detection_status():
    _dev_admin_only()
    return jsonify(detection_service.get_banners())


@api_bp.route("/detection/s3/catalog")
@login_required
def detection_s3_catalog():
    _dev_admin_only()
    source = (request.args.get("source") or "videographer").strip().lower()
    return jsonify(detection_service.list_s3_catalog(source=source))


@api_bp.route("/detection/s3/keys")
@login_required
def detection_s3_keys():
    _dev_admin_only()
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
    _dev_admin_only()
    data = request.get_json(silent=True) or {}
    s3_key = data.get("s3_key")
    source = (data.get("source") or "").strip() or None
    return jsonify(detection_service.delete_s3_media(s3_key, source=source))


@api_bp.route("/detection/s3/preview")
@login_required
def detection_s3_preview():
    _dev_admin_only()
    s3_key = request.args.get("s3_key")
    return jsonify(detection_service.preview_s3_selection(s3_key))


@api_bp.route("/detection/run", methods=["POST"])
@login_required
def detection_run():
    _dev_admin_only()
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
            rotation=0,
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
    _dev_admin_only()
    from flask import send_file

    path = detection_service.resolve_serve_path(rel_path)
    if not path:
        abort(404)
    as_download = (request.args.get("download") or "").strip().lower() in ("1", "true", "yes")
    return send_file(
        path,
        as_attachment=as_download,
        download_name=path.name if as_download else None,
    )


@api_bp.route("/detection/sessions")
@login_required
def detection_sessions():
    _dev_admin_only()
    source = (request.args.get("source") or "").strip() or None
    return jsonify(detection_service.load_sessions(source=source))


@api_bp.route("/detection/sessions/<int:session_id>/potholes")
@login_required
def detection_session_potholes(session_id):
    _dev_admin_only()
    return jsonify(detection_service.load_session_potholes(session_id))


@api_bp.route("/detection/sessions/<int:session_id>/detail")
@login_required
def detection_session_detail(session_id):
    _dev_admin_only()
    return jsonify(detection_service.load_session_detail(session_id))


