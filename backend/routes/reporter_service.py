"""Citizen reporter portal — OTP auth + complaints (separate from staff users)."""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from flask import jsonify, request

import db_utils
from routes.reporter_token_auth import current_reporter_id

# S3 layout (separate from DB ticket / status fields):
#   user/{mobileno}/{YYYYMMDD}_{HHMM}/{sha256}.jpg|.mp4
#   user/{mobileno}/{YYYYMMDD}_{HHMM}/{sha256}_log.json  (required GPS meta)
CITIZEN_S3_ROOT = "user"
_IST = ZoneInfo("Asia/Kolkata")

COMPLAINT_CATEGORIES = [
    "Pothole",
    "Water Logging",
    "Crack",
    "Rutting",
    "Other",
]

_MOBILE_RE = re.compile(r"^[6-9]\d{9}$")
_IN_COUNTRY = "91"


def _national_digits(raw: str) -> str:
    """Strip +91 / 91 / 0 prefixes; return 10-digit national number or ''."""
    digits = re.sub(r"\D", "", str(raw or "").strip())
    while digits:
        if digits.startswith("0091") and len(digits) >= 13:
            digits = digits[4:]
            continue
        if digits.startswith(_IN_COUNTRY) and len(digits) > 10:
            digits = digits[2:]
            continue
        if digits.startswith("0") and len(digits) == 11:
            digits = digits[1:]
            continue
        break
    return digits[:10] if len(digits) <= 10 else digits


def normalize_mobile(raw: str) -> str:
    """India (+91) only — accepts +91, 91, or 10-digit national forms."""
    digits = _national_digits(raw)
    if len(digits) != 10 or not _MOBILE_RE.match(digits):
        raise ValueError("Enter a valid 10-digit Indian mobile number (+91).")
    return _IN_COUNTRY + digits


def get_reporter(reporter_id: int) -> dict | None:
    if not db_utils.is_db_configured():
        return None
    conn = db_utils._get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, mobile, created_at, last_login_at FROM reporter_users WHERE id = %s AND is_active",
                (reporter_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            mobile = row[1]
            return {
                "id": row[0],
                "mobile": mobile,
                "mobile_masked": mobile[:2] + "******" + mobile[-2:],
                "created_at": row[2].isoformat() if row[2] else None,
                "last_login_at": row[3].isoformat() if row[3] else None,
            }
    finally:
        conn.close()


def _snap_road_id(lat: float, lon: float) -> tuple[str | None, float | None]:
    """Optional snap to roads registry when present."""
    conn = db_utils._get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'roads'
                """
            )
            if not cur.fetchone():
                return None, None
            cur.execute(
                """
                SELECT r.id,
                       ST_Distance(
                           ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                           r.geom::geography
                       ) AS dist_m
                FROM roads r
                WHERE r.geom IS NOT NULL
                  AND ST_DWithin(
                      ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                      r.geom,
                      0.0002
                  )
                ORDER BY ST_SetSRID(ST_MakePoint(%s, %s), 4326) <-> r.geom
                LIMIT 1
                """,
                (lon, lat, lon, lat, lon, lat),
            )
            hit = cur.fetchone()
            if not hit:
                return None, None
            rid, dist = hit[0], float(hit[1])
            if dist > float(os.getenv("REPORTER_ROAD_SNAP_M", "25")):
                return None, None
            return str(rid), dist
    except Exception:
        return None, None
    finally:
        conn.close()


def _tracking_number(complaint_id: int) -> str:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"TKT-{day}-{complaint_id:06d}"


def submit_complaint(
    reporter_id: int,
    *,
    defect_type: str,
    latitude: float,
    longitude: float,
    description: str | None = None,
    gps_accuracy_m: float | None = None,
    photo_s3_key: str | None = None,
    video_s3_key: str | None = None,
    media_content_type: str | None = None,
) -> dict:
    defect = (defect_type or "").strip()
    if defect not in COMPLAINT_CATEGORIES:
        raise ValueError(f"Invalid category. Choose one of: {', '.join(COMPLAINT_CATEGORIES)}")

    road_id, snap_m = _snap_road_id(latitude, longitude)

    conn = db_utils._get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO reporter_complaints (
                    tracking_number, reporter_id, defect_type, description,
                    latitude, longitude, location, gps_accuracy_m,
                    photo_s3_key, video_s3_key, media_content_type,
                    road_id, snap_distance_m, status
                ) VALUES (
                    'PENDING', %s, %s, %s,
                    %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s,
                    %s, %s, %s,
                    %s, %s, 'Submitted'
                )
                RETURNING id
                """,
                (
                    reporter_id, defect, description,
                    latitude, longitude, longitude, latitude, gps_accuracy_m,
                    photo_s3_key, video_s3_key, media_content_type,
                    road_id, snap_m,
                ),
            )
            cid = int(cur.fetchone()[0])
            tnum = _tracking_number(cid)
            cur.execute(
                "UPDATE reporter_complaints SET tracking_number = %s, updated_at = NOW() WHERE id = %s",
                (tnum, cid),
            )
        conn.commit()
    finally:
        conn.close()

    return {
        "id": cid,
        "tracking_number": tnum,
        "defect_type": defect,
        "road_id": road_id,
        "snap_distance_m": snap_m,
        "status": "Submitted",
        "message": "Complaint submitted successfully.",
    }


def citizen_mobile_folder_part(mobile: str | None) -> str:
    """10-digit national mobile for S3 folder names (DB may store 91XXXXXXXXXX)."""
    digits = re.sub(r"\D", "", str(mobile or ""))
    if digits.startswith("91") and len(digits) >= 12:
        digits = digits[-10:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError("Reporter mobile is missing or invalid for S3 naming.")
    return digits


def build_citizen_upload_folder(mobile: str | None, when: datetime | None = None) -> str:
    """``{YYYYMMDD}_{HHMM}`` under ``user/{mobileno}/``."""
    stamp = when or datetime.now(_IST)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=_IST)
    else:
        stamp = stamp.astimezone(_IST)
    return f"{stamp.strftime('%Y%m%d')}_{stamp.strftime('%H%M')}"


def build_citizen_media_key(mobile: str | None, media_name: str, *, when: datetime | None = None) -> str:
    folder = build_citizen_upload_folder(mobile, when)
    return f"{CITIZEN_S3_ROOT}/{citizen_mobile_folder_part(mobile)}/{folder}/{media_name}"


def _hash_media_name(path: str, *, is_video: bool) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return f"{h.hexdigest()}.{'mp4' if is_video else 'jpg'}"


def build_citizen_frame_meta(
    *,
    latitude: float,
    longitude: float,
    gps_accuracy_m: float | None = None,
    defect_type: str | None = None,
    description: str | None = None,
    captured_at: str | None = None,
    is_video: bool = False,
    extra: dict | None = None,
) -> dict:
    """Same shape as mobile field ``*_log.json`` (assumed_fps + geo + frames)."""
    from datetime import datetime, timezone

    lat_f = float(latitude)
    lon_f = float(longitude)
    captured = (captured_at or "").strip() or datetime.now(timezone.utc).isoformat()
    payload: dict = {
        "assumed_fps": 30,
        "country": "India",
        "country_code": "IN",
        "source": "citizen",
        "media_kind": "video" if is_video else "photo",
        "captured_at": captured,
        "frames": [
            {
                "frame": 0,
                "video_second": 0,
                "t_ms": 0,
                "lat": lat_f,
                "lon": lon_f,
                "accuracy_m": float(gps_accuracy_m) if gps_accuracy_m is not None else None,
            }
        ],
    }
    if defect_type:
        payload["defect_type"] = str(defect_type).strip()
    if description:
        payload["description"] = str(description).strip()
    if extra and isinstance(extra, dict):
        for k, v in extra.items():
            if v is not None and k not in ("frames",):
                payload.setdefault(k, v)

    # Enrich place names from reverse geocode when possible
    try:
        from routes import survey_service as _ss

        rev = _ss.reverse_geocode(lat_f, lon_f, allow_nominatim=True, prefer_places=True)
        addr = rev.get("address") or {}
        if rev.get("district_name"):
            payload.setdefault("district", rev["district_name"])
        if rev.get("district_id"):
            payload.setdefault("district_id", rev["district_id"])
        if rev.get("state_key"):
            payload.setdefault("state_key", rev["state_key"])
        state = (
            addr.get("state")
            or rev.get("state")
            or payload.get("state")
        )
        if state:
            payload.setdefault("state", state)
        country = addr.get("country")
        if country:
            payload["country"] = country
        payload.setdefault("place", rev.get("display_name"))
    except Exception:
        pass
    # Drop null accuracy
    if payload["frames"][0].get("accuracy_m") is None:
        payload["frames"][0].pop("accuracy_m", None)
    return payload


def _write_citizen_meta_json(path: str, data: dict) -> None:
    import json

    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def upload_reporter_media(
    reporter_id: int,
    file_storage,
    *,
    mobile: str | None = None,
    prefix: str | None = None,
    frame_meta_storage=None,
    latitude: float | None = None,
    longitude: float | None = None,
    gps_accuracy_m: float | None = None,
    defect_type: str | None = None,
    description: str | None = None,
    captured_at: str | None = None,
) -> dict:
    """Upload citizen media + required GPS JSON sidecar.

    Layout:
      ``user/{mobile}/{YYYYMMDD}_{HHMM}/{sha256}.jpg|.mp4``
      ``user/{mobile}/{YYYYMMDD}_{HHMM}/{sha256}_log.json``
    """
    from s3_utils import get_reporter_bucket, is_s3_configured, upload_file

    _ = prefix  # legacy unused
    if not file_storage or not getattr(file_storage, "filename", None):
        raise ValueError("Photo or video file is required.")
    if not is_s3_configured():
        raise RuntimeError("S3 is not configured on the server.")

    if not mobile:
        reporter = get_reporter(reporter_id)
        mobile = (reporter or {}).get("mobile")
    if not mobile:
        raise ValueError("Reporter mobile is required for upload naming.")

    name = file_storage.filename or "upload.bin"
    ext = os.path.splitext(name)[1].lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".mkv", ".avi"):
        raise ValueError("Unsupported file type. Use a photo or video.")

    is_video = ext in (".mp4", ".mov", ".mkv", ".avi")
    mobile_part = citizen_mobile_folder_part(mobile)
    folder = build_citizen_upload_folder(mobile)
    bucket = get_reporter_bucket()

    fd, tmp = tempfile.mkstemp(prefix="sr_rep_", suffix=ext)
    os.close(fd)
    meta_tmp = None
    try:
        file_storage.save(tmp)
        media_name = _hash_media_name(tmp, is_video=is_video)
        stem, _ = os.path.splitext(media_name)
        key = f"{CITIZEN_S3_ROOT}/{mobile_part}/{folder}/{media_name}"
        meta_key = f"{CITIZEN_S3_ROOT}/{mobile_part}/{folder}/{stem}_log.json"

        # Prefer client JSON; otherwise synthesize from GPS form fields.
        meta_data = None
        if frame_meta_storage and getattr(frame_meta_storage, "filename", None):
            import json

            mfd, meta_tmp = tempfile.mkstemp(prefix="sr_rep_meta_", suffix=".json")
            os.close(mfd)
            frame_meta_storage.save(meta_tmp)
            try:
                parsed = json.loads(Path(meta_tmp).read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    meta_data = parsed
                elif isinstance(parsed, list):
                    meta_data = {"frames": parsed}
            except Exception:
                meta_data = None

        if meta_data is None:
            if latitude is None or longitude is None:
                raise ValueError("GPS coordinates are required for the media JSON log.")
            meta_data = build_citizen_frame_meta(
                latitude=latitude,
                longitude=longitude,
                gps_accuracy_m=gps_accuracy_m,
                defect_type=defect_type,
                description=description,
                captured_at=captured_at,
                is_video=is_video,
            )
        else:
            # Ensure frames exist; fill from form GPS if empty
            frames = meta_data.get("frames")
            if not isinstance(frames, list) or not frames:
                if latitude is None or longitude is None:
                    raise ValueError("Media JSON is missing GPS frames.")
                meta_data = {
                    **build_citizen_frame_meta(
                        latitude=latitude,
                        longitude=longitude,
                        gps_accuracy_m=gps_accuracy_m,
                        defect_type=defect_type,
                        description=description,
                        captured_at=captured_at,
                        is_video=is_video,
                        extra=meta_data,
                    ),
                }
            else:
                meta_data.setdefault("assumed_fps", 30)
                meta_data.setdefault("country", "India")
                meta_data.setdefault("country_code", "IN")
                meta_data.setdefault("source", "citizen")
                meta_data.setdefault("media_kind", "video" if is_video else "photo")
                if defect_type:
                    meta_data.setdefault("defect_type", defect_type)
                if description:
                    meta_data.setdefault("description", description)

        if not meta_tmp:
            mfd, meta_tmp = tempfile.mkstemp(prefix="sr_rep_meta_", suffix=".json")
            os.close(mfd)
        _write_citizen_meta_json(meta_tmp, meta_data)

        upload_file(tmp, key, bucket=bucket)
        upload_file(meta_tmp, meta_key, bucket=bucket)
    finally:
        for p in (tmp, meta_tmp):
            if not p:
                continue
            try:
                os.remove(p)
            except OSError:
                pass

    out = {
        "s3_key": key,
        "meta_s3_key": meta_key,
        "bucket": bucket,
        "s3_uri": f"s3://{bucket}/{key}",
        "meta_s3_uri": f"s3://{bucket}/{meta_key}",
        "kind": "video" if is_video else "photo",
        "folder": folder,
        "mobile": mobile_part,
    }
    try:
        from s3_utils import presign_url

        out["s3_url"] = presign_url(key, bucket=bucket)
        out["meta_s3_url"] = presign_url(meta_key, bucket=bucket)
    except Exception:
        out["s3_url"] = None
        out["meta_s3_url"] = None
    return out


def list_complaints(reporter_id: int) -> list[dict]:
    if not db_utils.is_db_configured():
        return []
    conn = db_utils._get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, tracking_number, defect_type, description, status,
                       latitude, longitude, gps_accuracy_m,
                       photo_s3_key, video_s3_key, media_content_type,
                       road_id, created_at, updated_at, rejection_remark
                FROM reporter_complaints
                WHERE reporter_id = %s
                ORDER BY created_at DESC
                LIMIT 100
                """,
                (reporter_id,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    from s3_utils import get_reporter_bucket, presign_url

    bucket = get_reporter_bucket()
    items = []
    for row in rows:
        photo_key = row[8]
        video_key = row[9]
        media_key = photo_key or video_key
        s3_url = None
        if media_key:
            try:
                s3_url = presign_url(media_key, bucket=bucket)
            except Exception:
                s3_url = None
        items.append({
            "id": row[0],
            "tracking_number": row[1],
            "defect_type": row[2],
            "description": row[3],
            "status": row[4],
            "latitude": row[5],
            "longitude": row[6],
            "gps_accuracy_m": row[7],
            "photo_s3_key": photo_key,
            "video_s3_key": video_key,
            "s3_key": media_key,
            "s3_uri": f"s3://{bucket}/{media_key}" if media_key else None,
            "s3_url": s3_url,
            "media_content_type": row[10],
            "road_id": row[11],
            "created_at": row[12].isoformat() if row[12] else None,
            "updated_at": row[13].isoformat() if row[13] else None,
            "rejection_remark": row[14],
        })
    return items


def verify_complaint_by_media_key(s3_key: str | None) -> dict | None:
    """Mark a Submitted citizen complaint as Verified when its media is run in Detection.

    Matches photo_s3_key or video_s3_key. Returns complaint summary if updated, else None.
    """
    key = (s3_key or "").strip()
    if not key or not db_utils.is_db_configured():
        return None

    conn = db_utils._get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE reporter_complaints
                SET status = 'Verified', updated_at = NOW()
                WHERE status = 'Submitted'
                  AND (photo_s3_key = %s OR video_s3_key = %s)
                RETURNING id, tracking_number, defect_type, status
                """,
                (key, key),
            )
            row = cur.fetchone()
            conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    if not row:
        return None
    return {
        "id": row[0],
        "tracking_number": row[1],
        "defect_type": row[2],
        "status": row[3],
    }


def _mask_mobile(mobile: str | None) -> str:
    digits = re.sub(r"\D", "", str(mobile or ""))
    if len(digits) >= 10:
        d = digits[-10:]
        return f"+91 ******{d[-4:]}"
    return "—"


def list_complaints_admin(status: str | None = None, limit: int = 100) -> list[dict]:
    """Staff list of citizen complaints (all reporters)."""
    if not db_utils.is_db_configured():
        return []

    limit = max(1, min(int(limit or 100), 200))
    status_filter = (status or "").strip()
    allowed = {
        "Submitted", "Verified", "Rejected",
        "WorkOrder_Created", "In_Progress", "Resolved", "Closed",
    }

    conn = db_utils._get_conn()
    try:
        with conn.cursor() as cur:
            if status_filter and status_filter in allowed:
                cur.execute(
                    """
                    SELECT c.id, c.tracking_number, c.defect_type, c.description, c.status,
                           c.latitude, c.longitude, c.gps_accuracy_m,
                           c.photo_s3_key, c.video_s3_key, c.media_content_type,
                           c.road_id, c.created_at, c.updated_at,
                           r.mobile, c.rejection_remark
                    FROM reporter_complaints c
                    JOIN reporter_users r ON r.id = c.reporter_id
                    WHERE c.status = %s
                    ORDER BY c.created_at DESC
                    LIMIT %s
                    """,
                    (status_filter, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT c.id, c.tracking_number, c.defect_type, c.description, c.status,
                           c.latitude, c.longitude, c.gps_accuracy_m,
                           c.photo_s3_key, c.video_s3_key, c.media_content_type,
                           c.road_id, c.created_at, c.updated_at,
                           r.mobile, c.rejection_remark
                    FROM reporter_complaints c
                    JOIN reporter_users r ON r.id = c.reporter_id
                    ORDER BY
                      CASE c.status
                        WHEN 'Submitted' THEN 0
                        WHEN 'Verified' THEN 1
                        WHEN 'WorkOrder_Created' THEN 2
                        WHEN 'In_Progress' THEN 3
                        WHEN 'Resolved' THEN 4
                        WHEN 'Closed' THEN 5
                        WHEN 'Rejected' THEN 6
                        ELSE 7
                      END,
                      c.created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            rows = cur.fetchall()
    finally:
        conn.close()

    from s3_utils import get_reporter_bucket, presign_url

    bucket = get_reporter_bucket()
    items = []
    for row in rows:
        photo_key = row[8]
        video_key = row[9]
        media_key = photo_key or video_key
        s3_url = None
        if media_key:
            try:
                s3_url = presign_url(media_key, bucket=bucket)
            except Exception:
                s3_url = None
        items.append({
            "id": row[0],
            "tracking_number": row[1],
            "defect_type": row[2],
            "description": row[3],
            "status": row[4],
            "latitude": row[5],
            "longitude": row[6],
            "gps_accuracy_m": row[7],
            "photo_s3_key": photo_key,
            "video_s3_key": video_key,
            "s3_key": media_key,
            "s3_url": s3_url,
            "media_content_type": row[10],
            "road_id": row[11],
            "created_at": row[12].isoformat() if row[12] else None,
            "updated_at": row[13].isoformat() if row[13] else None,
            "reporter_mobile_masked": _mask_mobile(row[14]),
            "rejection_remark": row[15],
            "map_link": (
                f"https://www.google.com/maps?q={row[5]},{row[6]}"
                if row[5] is not None and row[6] is not None
                else None
            ),
        })
    return items


def set_complaint_review_status(
    complaint_id: int,
    new_status: str,
    remark: str | None = None,
) -> dict:
    """Admin Accept (Verified) or Reject from Submitted only. Reject requires a remark."""
    if new_status not in ("Verified", "Rejected"):
        raise ValueError("Status must be Verified or Rejected.")
    if not db_utils.is_db_configured():
        raise RuntimeError("Database is not configured.")

    note = (remark or "").strip()
    if new_status == "Rejected" and len(note) < 3:
        raise ValueError("Please enter a rejection remark (why this report is unwanted).")

    cid = int(complaint_id)
    conn = db_utils._get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE reporter_complaints
                SET status = %s,
                    rejection_remark = %s,
                    updated_at = NOW()
                WHERE id = %s AND status = 'Submitted'
                RETURNING id, tracking_number, defect_type, status, rejection_remark
                """,
                (
                    new_status,
                    note if new_status == "Rejected" else None,
                    cid,
                ),
            )
            row = cur.fetchone()
            if not row:
                cur.execute(
                    "SELECT id, tracking_number, status FROM reporter_complaints WHERE id = %s",
                    (cid,),
                )
                existing = cur.fetchone()
                conn.commit()
                if not existing:
                    raise LookupError("Complaint not found.")
                raise ValueError(
                    f"Complaint {existing[1]} is already {existing[2]}; only Submitted can be reviewed."
                )
            conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    return {
        "id": row[0],
        "tracking_number": row[1],
        "defect_type": row[2],
        "status": row[3],
        "rejection_remark": row[4],
        "message": f"Complaint marked {new_status}.",
    }


def reporter_required(fn: Callable):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        rid = current_reporter_id()
        if not rid:
            return jsonify({"error": "Unauthorized", "message": "Reporter login required."}), 401
        if not get_reporter(rid):
            return jsonify({"error": "Unauthorized", "message": "Reporter account inactive."}), 401
        return fn(*args, **kwargs)

    return wrapper
