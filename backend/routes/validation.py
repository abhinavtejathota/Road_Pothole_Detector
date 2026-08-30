"""
Completion validation — GPS geofence + timestamp + YOLO re-detection.
Used by routes/api.py for React SPA uploads.
"""
import hashlib
import math
import io
import os
import tempfile
from datetime import datetime

import exifread

import db_utils
import s3_utils

_GPS_GEOFENCE_M = 30
_YOLO_CONF_THRESHOLD = 0.35


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _exif_gps(image_bytes: bytes) -> tuple[float | None, float | None, datetime | None]:
    tags = exifread.process_file(io.BytesIO(image_bytes), details=False)

    def _dms_to_dd(dms_tag, ref_tag):
        try:
            dms = tags[dms_tag].values
            ref = str(tags[ref_tag].values)
            d = float(dms[0].num) / dms[0].den
            m = float(dms[1].num) / dms[1].den
            s = float(dms[2].num) / dms[2].den
            dd = d + m / 60 + s / 3600
            if ref in ("S", "W"):
                dd = -dd
            return dd
        except Exception:
            return None

    lat = _dms_to_dd("GPS GPSLatitude", "GPS GPSLatitudeRef")
    lon = _dms_to_dd("GPS GPSLongitude", "GPS GPSLongitudeRef")
    ts = None
    for key in ("EXIF DateTimeOriginal", "Image DateTime"):
        if key in tags:
            try:
                ts = datetime.strptime(str(tags[key]), "%Y:%m:%d %H:%M:%S")
            except Exception:
                pass
            break
    return lat, lon, ts


def _run_yolo_on_image(image_bytes: bytes) -> tuple[bool, float, str]:
    from model_loader import load_model
    from utils import severity_from_area_and_position

    model = load_model()
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp.write(image_bytes)
        tmp_path = tmp.name
    try:
        results = model.predict(tmp_path, verbose=False)
        if not results or not results[0].boxes:
            return False, 0.0, "None"
        confs = results[0].boxes.conf.tolist()
        max_conf = max(confs) if confs else 0.0
        if max_conf < _YOLO_CONF_THRESHOLD:
            return False, max_conf, "None"
        boxes = results[0].boxes.xyxy.tolist()
        img_h = results[0].orig_shape[0]
        best_box = boxes[confs.index(max_conf)]
        x1, y1, x2, y2 = best_box
        area = (x2 - x1) * (y2 - y1)
        y_center = (y1 + y2) / 2
        severity = severity_from_area_and_position(area, y_center, img_h)
        return True, max_conf, severity
    finally:
        os.unlink(tmp_path)


def run_validation(wo: dict, image_bytes: bytes, filename: str,
                   manual_lat: float | None, manual_lon: float | None,
                   username: str) -> dict:
    wo_id = wo["id"]
    photo_hash = hashlib.sha256(image_bytes).hexdigest()

    exif_lat, exif_lon, exif_ts = _exif_gps(image_bytes)
    photo_lat = exif_lat if exif_lat is not None else manual_lat
    photo_lon = exif_lon if exif_lon is not None else manual_lon
    photo_ts = exif_ts or datetime.utcnow()

    potholes = db_utils.get_potholes_for_session(wo["session_id"])
    ref_lat = ref_lon = None
    for p in potholes:
        if p.get("lat") and p.get("lon"):
            ref_lat, ref_lon = p["lat"], p["lon"]
            break

    gps_distance = None
    if photo_lat and photo_lon and ref_lat and ref_lon:
        gps_distance = _haversine_m(photo_lat, photo_lon, ref_lat, ref_lon)
        gps_check = "PASS" if gps_distance <= _GPS_GEOFENCE_M else "FAIL"
    else:
        gps_check = "FAIL"

    allocated_at = wo.get("allocated_at")
    if allocated_at and photo_ts:
        if isinstance(allocated_at, str):
            allocated_at = datetime.fromisoformat(allocated_at)
        ts_check = "PASS" if photo_ts >= allocated_at.replace(tzinfo=None) else "FAIL"
    else:
        ts_check = "PASS"

    after_s3_url = ""
    if s3_utils.is_s3_configured():
        s3_key = f"validations/wo_{wo_id}/{photo_hash[:8]}_{filename}"
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(image_bytes)
            tmp_path = tmp.name
        try:
            s3_utils.upload_file(
                tmp_path, s3_key,
                os.getenv("S3_PROCESSED_BUCKET", "smart-road-videos-processed"),
            )
            after_s3_url = s3_utils.presign_url(
                s3_key, os.getenv("S3_PROCESSED_BUCKET", "smart-road-videos-processed"),
            )
        finally:
            os.unlink(tmp_path)

    try:
        pothole_detected, yolo_conf, yolo_severity = _run_yolo_on_image(image_bytes)
    except Exception:
        pothole_detected, yolo_conf, yolo_severity = False, 0.0, "None"

    if gps_check == "FAIL" or ts_check == "FAIL":
        ai_result = "FAIL"
        resolution_score = 0.0
    elif not pothole_detected:
        ai_result = "PASS"
        resolution_score = round((1 - yolo_conf) * 100, 1)
    elif pothole_detected and yolo_conf < 0.5:
        ai_result = "PARTIAL"
        resolution_score = round((1 - yolo_conf) * 60, 1)
    else:
        ai_result = "FAIL"
        resolution_score = round((1 - yolo_conf) * 30, 1)

    final_result = ai_result if ai_result in ("PASS", "FAIL") else None

    val_data = {
        "work_order_id": wo_id,
        "pothole_id": None,
        "after_photo_s3_url": after_s3_url,
        "after_photo_lat": photo_lat,
        "after_photo_lon": photo_lon,
        "after_photo_timestamp": photo_ts,
        "after_photo_hash": photo_hash,
        "gps_distance_meters": gps_distance,
        "gps_check": gps_check,
        "timestamp_check": ts_check,
        "yolo_pothole_detected": pothole_detected,
        "yolo_confidence": yolo_conf,
        "yolo_severity": yolo_severity,
        "ai_result": ai_result,
        "resolution_score": resolution_score,
        "final_result": final_result,
    }
    db_utils.save_validation(val_data)
    if wo["status"] == "WIP":
        db_utils.update_work_order_status(
            wo_id, "Completed",
            f"Completion photo submitted. AI result: {ai_result}",
            username,
        )
    return val_data
