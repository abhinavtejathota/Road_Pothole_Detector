"""API routes: model_bench_routes."""
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


# # ── Model bench (admin) ───────────────────────────────────────────────────────
# ── Model bench (admin) ───────────────────────────────────────────────────────

@api_bp.route("/model-bench/models")
@login_required
def model_bench_models():
    _dev_admin_only()
    return jsonify(model_bench_service.list_models())


@api_bp.route("/model-bench/run-image", methods=["POST"])
@login_required
def model_bench_run_image():
    _dev_admin_only()
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
    _dev_admin_only()
    model_id = request.form.get("model_id") or model_bench_service.get_default_model_id()
    conf = float(request.form.get("confidence") or 0.35)
    active = request.form.get("active", "true").lower() in ("1", "true", "yes")
    frame = request.files.get("frame")
    data = frame.read() if frame else b""
    return jsonify(model_bench_service.run_frame_bytes(data, model_id, conf, active=active))


@api_bp.route("/model-bench/run-video", methods=["POST"])
@login_required
def model_bench_run_video():
    """Ephemeral annotated video — not persisted; discarded after download/replace/leave."""
    _dev_admin_only()
    import tempfile

    model_id = request.form.get("model_id") or model_bench_service.get_default_model_id()
    conf = float(request.form.get("confidence") or 0.35)
    video = request.files.get("video")
    if not video or not video.filename:
        return jsonify({"status": {"kind": "error", "message": "Upload a video."}}), 400
    suffix = os.path.splitext(video.filename)[1] or ".mp4"
    fd, path = tempfile.mkstemp(prefix="sr_bench_in_", suffix=suffix)
    os.close(fd)
    try:
        video.save(path)
        result = model_bench_service.run_video_file(
            path, model_id, conf, user_id=int(current_user.id),
        )
        return jsonify(result), (200 if result.get("status", {}).get("kind") == "ok" else 400)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


@api_bp.route("/model-bench/video/<token>", methods=["GET", "DELETE"])
@login_required
def model_bench_video(token):
    _dev_admin_only()
    if request.method == "DELETE":
        model_bench_service.discard_video(token)
        return jsonify({"ok": True})

    path = model_bench_service.get_video_path(token)
    if not path:
        abort(404)
    from flask import send_file

    download = (request.args.get("download") or "").strip().lower() in ("1", "true", "yes")
    with model_bench_service._VIDEO_LOCK:
        job = model_bench_service._VIDEO_JOBS.get(str(token)) or {}
        filename = job.get("filename") or "annotated.mp4"
    if download:
        # octet-stream + attachment so browsers save the file instead of inline-playing
        return send_file(
            path,
            mimetype="application/octet-stream",
            as_attachment=True,
            download_name=filename,
            conditional=True,
        )
    return send_file(
        path,
        mimetype="video/mp4",
        as_attachment=False,
        conditional=True,
    )


@api_bp.route("/model-bench/video", methods=["DELETE"])
@login_required
def model_bench_video_discard_all():
    _dev_admin_only()
    model_bench_service.discard_user_videos(int(current_user.id))
    return jsonify({"ok": True})


