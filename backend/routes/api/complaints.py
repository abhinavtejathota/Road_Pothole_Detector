"""API routes: complaints."""
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


# # ── Citizen complaints (admin review) ─────────────────────────────────────────
# ── Citizen complaints (admin review) ─────────────────────────────────────────

@api_bp.route("/complaints")
@login_required
def complaints_list():
    _dev_admin_only()
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
    _dev_admin_only()
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
    _dev_admin_only()
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


