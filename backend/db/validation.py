from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
import json
import math
import os
import time

from db.connection import _get_conn, is_db_configured
try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None

# ── Completion Validation ─────────────────────────────────────────────────────

def save_validation(data: dict) -> int:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO completion_validations
                    (work_order_id, pothole_id, after_photo_s3_url,
                     after_photo_lat, after_photo_lon, after_photo_timestamp,
                     after_photo_hash, gps_distance_meters,
                     gps_check, timestamp_check,
                     yolo_pothole_detected, yolo_confidence, yolo_severity,
                     ai_result, resolution_score, final_result)
                VALUES
                    (%(work_order_id)s, %(pothole_id)s, %(after_photo_s3_url)s,
                     %(after_photo_lat)s, %(after_photo_lon)s, %(after_photo_timestamp)s,
                     %(after_photo_hash)s, %(gps_distance_meters)s,
                     %(gps_check)s, %(timestamp_check)s,
                     %(yolo_pothole_detected)s, %(yolo_confidence)s, %(yolo_severity)s,
                     %(ai_result)s, %(resolution_score)s, %(final_result)s)
                RETURNING id
            """, data)
            vid = cur.fetchone()[0]
        conn.commit()
        return vid
    finally:
        conn.close()


def get_validation(wo_id: int) -> dict | None:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM completion_validations
                WHERE work_order_id = %s ORDER BY validated_at DESC LIMIT 1
            """, (wo_id,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def supervisor_review_validation(val_id: int, final_result: str,
                                  comment: str, reviewed_by: str) -> bool:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE completion_validations
                SET final_result = %s, review_comment = %s, reviewed_by = %s
                WHERE id = %s
            """, (final_result, comment, reviewed_by, val_id))
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    finally:
        conn.close()


def get_pending_supervisor_reviews() -> list:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT cv.id AS val_id, cv.work_order_id, cv.ai_result,
                       cv.resolution_score, cv.after_photo_s3_url,
                       cv.gps_distance_meters, cv.gps_check, cv.timestamp_check,
                       cv.yolo_severity,
                       TO_CHAR(cv.validated_at AT TIME ZONE 'Asia/Kolkata',
                               'YYYY-MM-DD HH24:MI') AS validated_at,
                       wo.status AS wo_status,
                       vs.filename,
                       v.company_name AS vendor_name
                FROM completion_validations cv
                JOIN work_orders wo ON wo.id = cv.work_order_id
                JOIN video_sessions vs ON vs.id = wo.session_id
                LEFT JOIN vendors v ON v.id = wo.vendor_id
                WHERE cv.ai_result = 'PARTIAL' AND cv.final_result IS NULL
                ORDER BY cv.validated_at
            """)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


