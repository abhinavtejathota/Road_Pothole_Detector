"""Citizen reporter API — /api/reporter/* (separate from staff /api/auth)."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from routes.reporter_otp_service import lookup_mobile, request_otp, verify_otp
from routes.reporter_service import (
    COMPLAINT_CATEGORIES,
    get_reporter,
    list_complaints,
    reporter_required,
    submit_complaint,
    upload_reporter_media,
)
from routes.reporter_token_auth import current_reporter_id

reporter_bp = Blueprint("reporter", __name__, url_prefix="/api/reporter")


def _parse_body():
    return request.get_json(silent=True) or {}


def _err(exc: Exception, *, bad=400, unavail=503):
    if isinstance(exc, ValueError):
        return jsonify({"error": "Bad request", "message": str(exc)}), bad
    if isinstance(exc, RuntimeError):
        return jsonify({"error": "Unavailable", "message": str(exc)}), unavail
    return jsonify({"error": "Internal error", "message": str(exc)[:200]}), 500


@reporter_bp.get("/health")
def reporter_health():
    return jsonify({"ok": True, "service": "reporter", "auth": "otp"})


@reporter_bp.get("/categories")
def list_categories():
    return jsonify({"categories": COMPLAINT_CATEGORIES})


@reporter_bp.post("/auth/lookup")
def auth_lookup():
    body = _parse_body()
    mobile = (body.get("mobile") or "").strip()
    if not mobile:
        return jsonify({"error": "Bad request", "message": "Mobile number is required."}), 400
    try:
        return jsonify(lookup_mobile(mobile))
    except Exception as e:
        return _err(e)


@reporter_bp.post("/auth/otp/request")
def otp_request():
    body = _parse_body()
    resend = body.get("resend") in (True, "true", "1", 1, "yes")
    try:
        return jsonify(
            request_otp(body.get("mobile") or "", ip=request.remote_addr, resend=resend)
        )
    except Exception as e:
        return _err(e)


@reporter_bp.post("/auth/otp/verify")
def otp_verify():
    body = _parse_body()
    try:
        return jsonify(verify_otp(body.get("mobile") or "", body.get("otp") or ""))
    except Exception as e:
        return _err(e, bad=401)


@reporter_bp.get("/auth/me")
@reporter_required
def reporter_me():
    rid = current_reporter_id()
    rep = get_reporter(rid) if rid else None
    if not rep:
        return jsonify({"error": "Unauthorized", "message": "Reporter not found."}), 401
    return jsonify(rep)


@reporter_bp.post("/auth/logout")
def reporter_logout():
    # Stateless JWT — client drops token. OTP is reusable and kept for testing.
    return jsonify({"ok": True})


@reporter_bp.post("/filecomplaint")
@reporter_required
def file_complaint():
    rid = current_reporter_id()
    if not rid:
        return jsonify({"error": "Unauthorized"}), 401

    defect_type = (request.form.get("defect_type") or "").strip()
    description = (request.form.get("description") or "").strip() or None
    try:
        latitude = float(request.form.get("latitude") or "")
        longitude = float(request.form.get("longitude") or "")
    except (TypeError, ValueError):
        return jsonify({
            "error": "Bad request",
            "message": "GPS coordinates are required. Enable location and capture again.",
        }), 400

    gps_accuracy_m = None
    raw_acc = request.form.get("gps_accuracy_m")
    if raw_acc not in (None, ""):
        try:
            gps_accuracy_m = float(raw_acc)
        except (TypeError, ValueError):
            pass

    media = request.files.get("media") or request.files.get("photo") or request.files.get("video")
    if not media:
        return jsonify({"error": "Bad request", "message": "Photo or video is required."}), 400

    frame_meta = (
        request.files.get("frame_meta")
        or request.files.get("media_meta")
        or request.files.get("gps_meta")
    )
    captured_at = (request.form.get("captured_at") or "").strip() or None

    try:
        reporter = get_reporter(rid) or {}
        uploaded = upload_reporter_media(
            rid,
            media,
            mobile=reporter.get("mobile"),
            frame_meta_storage=frame_meta,
            latitude=latitude,
            longitude=longitude,
            gps_accuracy_m=gps_accuracy_m,
            defect_type=defect_type,
            description=description,
            captured_at=captured_at,
        )
        photo_key = uploaded["s3_key"] if uploaded["kind"] == "photo" else None
        video_key = uploaded["s3_key"] if uploaded["kind"] == "video" else None
        out = submit_complaint(
            rid,
            defect_type=defect_type,
            latitude=latitude,
            longitude=longitude,
            description=description,
            gps_accuracy_m=gps_accuracy_m,
            photo_s3_key=photo_key,
            video_s3_key=video_key,
            media_content_type=getattr(media, "mimetype", None),
        )
        out["s3_key"] = uploaded["s3_key"]
        out["s3_uri"] = uploaded.get("s3_uri")
        out["s3_url"] = uploaded.get("s3_url")
        out["meta_s3_key"] = uploaded.get("meta_s3_key")
        out["meta_s3_uri"] = uploaded.get("meta_s3_uri")
        return jsonify(out), 201
    except ValueError as e:
        return jsonify({"error": "Bad request", "message": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": "Unavailable", "message": str(e)}), 503
    except Exception as e:
        return jsonify({"error": "Internal error", "message": str(e)[:200]}), 500


@reporter_bp.get("/trackcomplaint")
@reporter_required
def track_complaint():
    rid = current_reporter_id()
    if not rid:
        return jsonify({"error": "Unauthorized"}), 401
    items = list_complaints(rid)
    return jsonify({"complaints": items, "count": len(items)})
