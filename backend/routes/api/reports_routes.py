"""API routes: reports_routes."""
import os
import threading
import time

from flask import jsonify, request, abort
from flask_login import login_required, login_user, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

import db_utils
from routes.api import api_bp
from routes.api._helpers import _serialize, _user_payload, _vendor_from_request, _retry_until, _field_ops
from routes.user_model import User
from routes.constants import VALID_TRANSITIONS
from routes import validation as validation_module
from routes import survey_service
from routes import detection_service
from routes import model_bench_service
from routes import field_upload_service
from routes import tracking_service
from routes import auto_track_service


@api_bp.route("/reports")
@login_required
def reports_list():
    _field_ops()
    from routes import report_service
    try:
        return jsonify(report_service.list_reports_for_ui())
    except Exception as e:
        return jsonify({"videographers": [], "total_sessions": 0, "error": str(e)}), 500


@api_bp.route("/reports/<int:session_id>/open")
@login_required
def reports_open(session_id):
    _field_ops()
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
    _field_ops()
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
    _field_ops()
    from routes import report_service
    result = report_service.generate_report_for_session(int(session_id), force=True)
    code = 200 if result.get("ok") else 500
    return jsonify(result), code


