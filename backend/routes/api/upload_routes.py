"""API routes: upload_routes."""
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


# # ── Field upload (videographer) ───────────────────────────────────────────────
# ── Field upload (videographer) ───────────────────────────────────────────────

def _videographer_only():
    if not current_user.is_videographer():
        abort(403)


@api_bp.route("/upload/status")
@login_required
def upload_status():
    _videographer_only()
    from s3_utils import get_input_bucket, is_s3_configured

    if is_s3_configured():
        return jsonify({
            "status": field_upload_service._status(
                "ok",
                f"S3 input bucket: {get_input_bucket()}",
            ),
            "bucket": get_input_bucket(),
        })
    return jsonify({
        "status": field_upload_service._status("warn", "S3 not configured."),
        "bucket": None,
    })


@api_bp.route("/upload", methods=["POST"])
@login_required
def upload_field():
    _videographer_only()
    import tempfile

    media = request.files.get("media")
    if not media or not media.filename:
        return jsonify({"status": {"kind": "error", "message": "Select a video or image to upload."}}), 400
    gps = request.files.get("gps_log")
    if not gps or not gps.filename:
        return jsonify({
            "status": {"kind": "error", "message": "GPS log is required — enable location and record before upload."},
        }), 400
    frame_meta = request.files.get("frame_meta")
    media_suffix = os.path.splitext(media.filename)[1] or ".bin"
    fd, media_path = tempfile.mkstemp(prefix="sr_up_", suffix=media_suffix)
    os.close(fd)
    gps_path = None
    frame_path = None
    tmp = [media_path]
    try:
        media.save(media_path)
        gps_suffix = os.path.splitext(gps.filename)[1] or ".csv"
        fd2, gps_path = tempfile.mkstemp(prefix="sr_gps_", suffix=gps_suffix)
        os.close(fd2)
        gps.save(gps_path)
        tmp.append(gps_path)
        try:
            from pathlib import Path as _Path
            raw = _Path(gps_path).read_text(encoding="utf-8", errors="ignore")
            data_lines = [
                ln for ln in raw.splitlines()
                if ln.strip() and not ln.lower().startswith("videosecond")
            ]
            if len(data_lines) < 1:
                return jsonify({
                    "status": {"kind": "error", "message": "GPS log has no points — enable location and retry."},
                }), 400
        except Exception:
            pass
        if frame_meta and frame_meta.filename:
            fd3, frame_path = tempfile.mkstemp(prefix="sr_frames_", suffix=".json")
            os.close(fd3)
            frame_meta.save(frame_path)
            tmp.append(frame_path)
        result = field_upload_service.upload_to_input(
            media_path,
            media.filename,
            gps_path=gps_path,
            gps_filename=gps.filename,
            username=current_user.username,
            frame_meta_path=frame_path,
            frame_meta_filename=frame_meta.filename if frame_meta else None,
            user_id=int(current_user.id),
            route_label=(request.form.get("route_label") or request.form.get("route_folder") or "").strip() or None,
            start_label=(request.form.get("start_label") or "").strip() or None,
            end_label=(request.form.get("end_label") or "").strip() or None,
        )
        if (result.get("status") or {}).get("kind") == "ok":
            capture_sid = (
                request.form.get("capture_session_id")
                or request.form.get("session_id")
                or ""
            ).strip() or None
            _finalize_field_upload_side_effects(
                result,
                user_id=int(current_user.id),
                media_title=media.filename,
                gps_path=gps_path,
                capture_session_id=capture_sid,
            )
        return jsonify(result)
    finally:
        for p in tmp:
            try:
                os.remove(p)
            except OSError:
                pass


def _finalize_field_upload_side_effects(
    result: dict,
    *,
    user_id: int,
    media_title: str | None,
    gps_path: str | None,
    capture_session_id: str | None,
) -> None:
    """Tracking commit + auto_track verify + seal roads (shared by classic + multipart)."""
    from routes.upload_side_effects import after_chunk_finalize

    after_chunk_finalize(
        result,
        user_id=user_id,
        media_title=media_title,
        gps_path=gps_path,
        capture_session_id=capture_session_id,
    )


@api_bp.route("/upload/session/init", methods=["POST"])
@login_required
def upload_session_init():
    """Start a chunked capture session (1‑min clips appended on the Flask host)."""
    _videographer_only()
    data = request.get_json(silent=True) or {}
    sid = (
        data.get("capture_session_id") or data.get("session_id") or ""
    ).strip()
    if not sid:
        return jsonify({"ok": False, "status": {"kind": "error", "message": "capture_session_id required"}}), 400
    out = field_upload_service.init_chunk_session(
        username=current_user.username,
        session_id=sid,
        user_id=int(current_user.id),
    )
    return jsonify(out)


@api_bp.route("/upload/session/chunk", methods=["POST"])
@login_required
def upload_session_chunk():
    """Receive one ~1‑min MP4 segment (multipart FormData). Prefer chunk-bin on field phones."""
    _videographer_only()
    import tempfile
    from routes.upload_guard import (
        UploadBodyTimeout,
        release_chunk_slot,
        try_acquire_chunk_slot,
    )

    if not try_acquire_chunk_slot(wait_s=0.05):
        resp = jsonify({
            "ok": False,
            "status": {
                "kind": "warn",
                "message": "Server is busy receiving other uploads — retry in a few seconds.",
            },
            "retry_after": 5,
        })
        resp.status_code = 503
        resp.headers["Retry-After"] = "5"
        return resp

    tmp_path = None
    try:
        try:
            sid = (
                request.form.get("capture_session_id") or request.form.get("session_id") or ""
            ).strip()
            media = request.files.get("media") or request.files.get("chunk")
            idx_raw = request.form.get("chunk_index")
            lat_raw = request.form.get("lat")
            lon_raw = request.form.get("lon")
            acc_raw = request.form.get("accuracy")
        except UploadBodyTimeout as e:
            return jsonify({
                "ok": False,
                "status": {
                    "kind": "error",
                    "message": f"Upload stalled (mobile network?) — {e}. Retry the same minute.",
                },
            }), 408

        if not sid:
            return jsonify({"ok": False, "status": {"kind": "error", "message": "capture_session_id required"}}), 400
        if not media or not media.filename:
            return jsonify({"ok": False, "status": {"kind": "error", "message": "media (video chunk) required"}}), 400
        try:
            chunk_index = int(idx_raw) if idx_raw is not None and str(idx_raw).strip() != "" else None
        except (TypeError, ValueError):
            return jsonify({"ok": False, "status": {"kind": "error", "message": "Invalid chunk_index"}}), 400

        suffix = os.path.splitext(media.filename)[1] or ".mp4"
        fd, tmp_path = tempfile.mkstemp(prefix="sr_chunk_", suffix=suffix)
        os.close(fd)
        try:
            media.save(tmp_path)
        except UploadBodyTimeout as e:
            return jsonify({
                "ok": False,
                "status": {
                    "kind": "error",
                    "message": f"Upload stalled (mobile network?) — {e}. Retry the same minute.",
                },
            }), 408
        except OSError as e:
            return jsonify({
                "ok": False,
                "status": {"kind": "error", "message": f"Upload interrupted: {e}"},
            }), 408

        if os.path.getsize(tmp_path) < 1:
            return jsonify({
                "ok": False,
                "status": {"kind": "error", "message": "Empty chunk body"},
            }), 400

        return _finish_session_chunk(
            sid=sid,
            tmp_path=tmp_path,
            chunk_index=chunk_index,
            lat_raw=lat_raw,
            lon_raw=lon_raw,
            acc_raw=acc_raw,
        )
    finally:
        release_chunk_slot()
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


@api_bp.route("/upload/session/chunk-bin", methods=["POST"])
@login_required
def upload_session_chunk_bin():
    """Raw video body (no multipart). Faster on mobile-data — phone streams bytes straight in.

    Headers: X-Capture-Session-Id, X-Chunk-Index, optional X-Lat / X-Lon / X-Accuracy.
    Body: video/mp4 octets.
    """
    _videographer_only()
    import tempfile
    from routes.upload_guard import (
        UploadBodyTimeout,
        release_chunk_slot,
        try_acquire_chunk_slot,
    )

    if not try_acquire_chunk_slot(wait_s=0.05):
        resp = jsonify({
            "ok": False,
            "status": {
                "kind": "warn",
                "message": "Server is busy receiving other uploads — retry in a few seconds.",
            },
            "retry_after": 5,
        })
        resp.status_code = 503
        resp.headers["Retry-After"] = "5"
        return resp

    tmp_path = None
    try:
        sid = (
            request.headers.get("X-Capture-Session-Id")
            or request.headers.get("X-Capture-Session-ID")
            or request.args.get("capture_session_id")
            or ""
        ).strip()
        idx_raw = (
            request.headers.get("X-Chunk-Index")
            or request.args.get("chunk_index")
        )
        lat_raw = request.headers.get("X-Lat") or request.args.get("lat")
        lon_raw = request.headers.get("X-Lon") or request.args.get("lon")
        acc_raw = request.headers.get("X-Accuracy") or request.args.get("accuracy")

        if not sid:
            return jsonify({"ok": False, "status": {"kind": "error", "message": "X-Capture-Session-Id required"}}), 400
        try:
            chunk_index = int(idx_raw) if idx_raw is not None and str(idx_raw).strip() != "" else None
        except (TypeError, ValueError):
            return jsonify({"ok": False, "status": {"kind": "error", "message": "Invalid X-Chunk-Index"}}), 400

        fd, tmp_path = tempfile.mkstemp(prefix="sr_chunk_", suffix=".mp4")
        os.close(fd)
        # Stream raw bytes. Do NOT use request.stream when Content-Length is
        # missing — Werkzeug returns empty BytesIO under MAX_CONTENT_LENGTH.
        from flask import current_app
        from routes.upload_stream import drain_stream_to_file, open_upload_body_stream

        try:
            stream = open_upload_body_stream(
                request.environ,
                max_content_length=current_app.config.get("MAX_CONTENT_LENGTH"),
            )
            written = drain_stream_to_file(stream, tmp_path, buf_size=1024 * 1024)
        except UploadBodyTimeout as e:
            return jsonify({
                "ok": False,
                "status": {
                    "kind": "error",
                    "message": f"Upload stalled (mobile network?) — {e}. Retry the same minute.",
                },
            }), 408
        except OSError as e:
            return jsonify({
                "ok": False,
                "status": {"kind": "error", "message": f"Upload interrupted: {e}"},
            }), 408

        if written < 1 or os.path.getsize(tmp_path) < 1:
            return jsonify({
                "ok": False,
                "status": {
                    "kind": "error",
                    "message": "Empty chunk body — send Content-Length or use /upload/session/chunk multipart.",
                },
            }), 400

        return _finish_session_chunk(
            sid=sid,
            tmp_path=tmp_path,
            chunk_index=chunk_index,
            lat_raw=lat_raw,
            lon_raw=lon_raw,
            acc_raw=acc_raw,
        )
    finally:
        release_chunk_slot()
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _finish_session_chunk(*, sid, tmp_path, chunk_index, lat_raw, lon_raw, acc_raw):
    """Shared append + optional GPS piggyback for multipart and binary chunk paths."""
    out = field_upload_service.append_chunk(
        username=current_user.username,
        session_id=sid,
        chunk_path=tmp_path,
        chunk_index=chunk_index,
    )
    code = 200 if out.get("ok") else 400
    if out.get("ok"):
        try:
            if lat_raw is not None and lon_raw is not None:
                lat = float(lat_raw)
                lon = float(lon_raw)
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    try:
                        accuracy = float(acc_raw) if acc_raw is not None else None
                    except (TypeError, ValueError):
                        accuracy = None
                    ping_kwargs = dict(
                        user_id=int(current_user.id),
                        username=current_user.username,
                        full_name=getattr(current_user, "full_name", None),
                        state_id=getattr(current_user, "state_id", None),
                        district_id=getattr(current_user, "district_id", None),
                        lat=lat,
                        lon=lon,
                        accuracy=accuracy,
                        recording=True,
                        capture_session_id=sid,
                    )

                    def _bg_ping():
                        try:
                            tracking_service.record_ping(**ping_kwargs)
                        except Exception:
                            pass

                    import threading
                    threading.Thread(target=_bg_ping, daemon=True, name="chunk-ping").start()
        except (TypeError, ValueError):
            pass
    return jsonify(out), code


@api_bp.route("/upload/session/finalize", methods=["POST"])
@login_required
def upload_session_finalize():
    """Accept capture on server immediately; assemble + S3 + seal in background."""
    _videographer_only()
    import tempfile

    sid = (
        request.form.get("capture_session_id") or request.form.get("session_id") or ""
    ).strip()
    if not sid:
        return jsonify({"status": {"kind": "error", "message": "capture_session_id required"}}), 400
    gps = request.files.get("gps_log")
    if not gps or not gps.filename:
        return jsonify({
            "status": {"kind": "error", "message": "GPS log is required to finalize."},
        }), 400
    frame_meta = request.files.get("frame_meta")
    gps_path = None
    frame_path = None
    tmp: list[str] = []
    try:
        gps_suffix = os.path.splitext(gps.filename)[1] or ".csv"
        fd, gps_path = tempfile.mkstemp(prefix="sr_gps_", suffix=gps_suffix)
        os.close(fd)
        gps.save(gps_path)
        tmp.append(gps_path)
        if frame_meta and frame_meta.filename:
            fd2, frame_path = tempfile.mkstemp(prefix="sr_frames_", suffix=".json")
            os.close(fd2)
            frame_meta.save(frame_path)
            tmp.append(frame_path)

        uid = int(current_user.id)

        def _on_complete(result, staged_gps):
            # Only used when FINALIZE_EXTERNAL_WORKER=0 (legacy in-process).
            _finalize_field_upload_side_effects(
                result,
                user_id=uid,
                media_title=f"chunked:{sid}",
                gps_path=staged_gps,
                capture_session_id=sid,
            )

        result = field_upload_service.queue_chunk_finalize(
            username=current_user.username,
            session_id=sid,
            gps_path=gps_path,
            gps_filename=gps.filename,
            frame_meta_path=frame_path,
            frame_meta_filename=frame_meta.filename if frame_meta else None,
            user_id=uid,
            route_label=(request.form.get("route_label") or request.form.get("route_folder") or "").strip() or None,
            start_label=(request.form.get("start_label") or "").strip() or None,
            end_label=(request.form.get("end_label") or "").strip() or None,
            on_complete=_on_complete,
            media_title=f"chunked:{sid}",
            capture_session_id=sid,
        )
        code = 200 if result.get("ok") else 400
        return jsonify(result), code
    finally:
        for p in tmp:
            try:
                os.remove(p)
            except OSError:
                pass


@api_bp.route("/upload/session/discard", methods=["POST"])
@login_required
def upload_session_discard():
    """Delete streamed chunks on the server + provisional GPS trail."""
    _videographer_only()
    data = request.get_json(silent=True) or {}
    sid = (
        data.get("capture_session_id") or data.get("session_id")
        or request.form.get("capture_session_id") or ""
    ).strip()
    if not sid:
        return jsonify({"ok": False, "status": {"kind": "error", "message": "capture_session_id required"}}), 400
    out = field_upload_service.discard_chunk_session(
        username=current_user.username,
        session_id=sid,
    )
    try:
        from routes import tracking_service as _ts
        _ts.discard_capture_session(int(current_user.id), sid)
    except Exception:
        pass
    return jsonify(out)


@api_bp.route("/upload/multipart/init", methods=["POST"])
@login_required
def upload_multipart_init():
    """Start S3 multipart upload for large field videos (2–10 GB)."""
    _videographer_only()
    data = request.get_json(silent=True) or {}
    filename = (data.get("filename") or data.get("media_filename") or "capture.mp4").strip()
    size_bytes = data.get("size_bytes") or data.get("size")
    content_type = (data.get("content_type") or "video/mp4").strip()
    capture_sid = (data.get("capture_session_id") or data.get("session_id") or "").strip() or None
    try:
        size_i = int(size_bytes) if size_bytes is not None else None
    except (TypeError, ValueError):
        size_i = None
    out = field_upload_service.init_multipart_video(
        username=current_user.username,
        media_filename=filename,
        size_bytes=size_i,
        content_type=content_type,
        capture_session_id=capture_sid,
        user_id=int(current_user.id),
        route_label=(data.get("route_label") or data.get("route_folder") or "").strip() or None,
        start_label=(data.get("start_label") or "").strip() or None,
        end_label=(data.get("end_label") or "").strip() or None,
    )
    if not out.get("ok"):
        return jsonify(out), 400
    return jsonify(out)


@api_bp.route("/upload/multipart/presign", methods=["POST"])
@login_required
def upload_multipart_presign():
    """Presign one or many part PUT URLs."""
    _videographer_only()
    data = request.get_json(silent=True) or {}
    key = (data.get("key") or "").strip()
    upload_id = (data.get("upload_id") or "").strip()
    if not key or not upload_id:
        return jsonify({"error": "key and upload_id required"}), 400
    nums = data.get("part_numbers") or data.get("parts")
    if nums is None and data.get("part_number") is not None:
        nums = [data.get("part_number")]
    if not isinstance(nums, list) or not nums:
        return jsonify({"error": "part_numbers list required"}), 400
    try:
        nums = [int(n) for n in nums]
    except (TypeError, ValueError):
        return jsonify({"error": "invalid part_numbers"}), 400
    if len(nums) > 100:
        return jsonify({"error": "max 100 part URLs per request"}), 400
    try:
        return jsonify(field_upload_service.presign_parts(
            key,
            upload_id,
            nums,
            username=current_user.username,
            bucket=None,
        ))
    except ValueError as e:
        return jsonify({"error": str(e)}), 403
    except Exception as e:
        return jsonify({"error": "presign failed"}), 400


@api_bp.route("/upload/multipart/part", methods=["POST"])
@login_required
def upload_multipart_part_relay():
    """Relay one video part phone→Flask→S3 when the device cannot PUT to AWS directly."""
    _videographer_only()
    import tempfile

    key = (request.form.get("key") or "").strip()
    upload_id = (request.form.get("upload_id") or "").strip()
    part_raw = request.form.get("part_number") or request.form.get("PartNumber")
    part_file = request.files.get("part") or request.files.get("file")
    if not key or not upload_id or part_raw is None or not part_file or not part_file.filename:
        return jsonify({"ok": False, "error": "key, upload_id, part_number, and part file required"}), 400
    try:
        part_number = int(part_raw)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid part_number"}), 400
    fd, part_path = tempfile.mkstemp(prefix="sr_mp_", suffix=".bin")
    os.close(fd)
    try:
        part_file.save(part_path)
        out = field_upload_service.relay_part(
            username=current_user.username,
            key=key,
            upload_id=upload_id,
            part_number=part_number,
            part_path=part_path,
            bucket=(request.form.get("bucket") or None),
        )
        status = 200 if out.get("ok") else 400
        return jsonify(out), status
    finally:
        try:
            os.remove(part_path)
        except OSError:
            pass


@api_bp.route("/upload/multipart/complete", methods=["POST"])
@login_required
def upload_multipart_complete():
    """Complete multipart video + attach GPS/frame sidecars (small form upload)."""
    _videographer_only()
    import tempfile
    import json as _json

    # JSON body OR multipart form (gps/frame files + fields)
    data = request.get_json(silent=True) or {}
    if request.form:
        data = {**data, **{k: request.form.get(k) for k in request.form}}
        if request.form.get("parts_json"):
            try:
                data["parts"] = _json.loads(request.form.get("parts_json"))
            except Exception:
                pass

    key = (data.get("key") or "").strip()
    upload_id = (data.get("upload_id") or "").strip()
    parts = data.get("parts") or []
    if not key or not upload_id or not parts:
        return jsonify({"error": "key, upload_id, and parts required"}), 400

    gps = request.files.get("gps_log")
    if not gps or not gps.filename:
        return jsonify({
            "status": {"kind": "error", "message": "GPS log is required with multipart complete."},
        }), 400

    frame_meta = request.files.get("frame_meta")
    gps_path = None
    frame_path = None
    tmp = []
    try:
        gps_suffix = os.path.splitext(gps.filename)[1] or ".csv"
        fd, gps_path = tempfile.mkstemp(prefix="sr_gps_", suffix=gps_suffix)
        os.close(fd)
        gps.save(gps_path)
        tmp.append(gps_path)
        if frame_meta and frame_meta.filename:
            fd2, frame_path = tempfile.mkstemp(prefix="sr_frames_", suffix=".json")
            os.close(fd2)
            frame_meta.save(frame_path)
            tmp.append(frame_path)

        result = field_upload_service.finish_multipart_video(
            username=current_user.username,
            key=key,
            upload_id=upload_id,
            parts=parts,
            gps_path=gps_path,
            gps_filename=gps.filename,
            frame_meta_path=frame_path,
            frame_meta_filename=frame_meta.filename if frame_meta else None,
            bucket=data.get("bucket"),
        )
        kind = (result.get("status") or {}).get("kind")
        # ok = full success; warn = video landed but a sidecar failed — still seal/match from local GPS
        if kind in ("ok", "warn"):
            capture_sid = (
                data.get("capture_session_id")
                or data.get("session_id")
                or ""
            )
            if isinstance(capture_sid, str):
                capture_sid = capture_sid.strip() or None
            else:
                capture_sid = None
            _finalize_field_upload_side_effects(
                result,
                user_id=int(current_user.id),
                media_title=os.path.basename(key),
                gps_path=gps_path,
                capture_session_id=capture_sid,
            )
        return jsonify(result)
    finally:
        for p in tmp:
            try:
                os.remove(p)
            except OSError:
                pass


@api_bp.route("/upload/multipart/abort", methods=["POST"])
@login_required
def upload_multipart_abort():
    _videographer_only()
    data = request.get_json(silent=True) or {}
    key = (data.get("key") or "").strip()
    upload_id = (data.get("upload_id") or "").strip()
    if not key or not upload_id:
        return jsonify({"error": "key and upload_id required"}), 400
    return jsonify(field_upload_service.abort_multipart(
        key, upload_id, username=current_user.username, bucket=None,
    ))


@api_bp.route("/upload/keys")
@login_required
def upload_keys():
    _videographer_only()
    return jsonify(field_upload_service.list_recent_keys())


@api_bp.route("/survey/districts/<district_id>/reopen-roads", methods=["POST"])
@login_required
def survey_reopen_district_roads(district_id):
    """Admin: reopen completed roads in a district so they can be assigned again."""
    if not current_user.can_manage_field_ops():
        abort(403)
    data = request.get_json(silent=True) or {}
    state_key = (data.get("state_key") or request.args.get("state_key") or "").strip()
    if not state_key and getattr(current_user, "state_id", None):
        state_key = survey_service.resolve_state_key(state_id=current_user.state_id)
    if not state_key:
        return jsonify({"error": "state_key required"}), 400
    return jsonify(survey_service.reopen_district_roads(
        state_key=state_key,
        district_id=district_id,
        only_completed=True,
    ))


