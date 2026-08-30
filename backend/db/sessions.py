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

# ── Write ─────────────────────────────────────────────────────────────────────

def save_session(
    filename: str,
    s3_key: str,
    run_id: str,
    total_potholes: int,
    video_duration_sec: float = None,
    *,
    user_id: int | None = None,
    username: str | None = None,
    display_name: str | None = None,
    start_label: str | None = None,
    end_label: str | None = None,
    is_legacy: bool = False,
) -> int:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO video_sessions
                    (filename, s3_key, run_id, total_potholes, video_duration_sec,
                     user_id, username, display_name, start_label, end_label, is_legacy)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                filename, s3_key or "", run_id, total_potholes, video_duration_sec,
                user_id, username, display_name or filename, start_label, end_label,
                bool(is_legacy),
            ))
            session_id = cur.fetchone()[0]
        conn.commit()
        return session_id
    finally:
        conn.close()


def update_session_report_meta(
    session_id: int,
    *,
    filename: str | None = None,
    display_name: str | None = None,
    username: str | None = None,
    user_id: int | None = None,
    start_label: str | None = None,
    end_label: str | None = None,
    report_s3_key: str | None = None,
    is_legacy: bool | None = None,
    s3_key: str | None = None,
) -> None:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE video_sessions SET
                    filename = COALESCE(%s, filename),
                    display_name = COALESCE(%s, display_name),
                    username = COALESCE(%s, username),
                    user_id = COALESCE(%s, user_id),
                    start_label = COALESCE(%s, start_label),
                    end_label = COALESCE(%s, end_label),
                    report_s3_key = COALESCE(%s, report_s3_key),
                    report_generated_at = CASE WHEN %s IS NOT NULL THEN NOW() ELSE report_generated_at END,
                    is_legacy = COALESCE(%s, is_legacy),
                    s3_key = COALESCE(%s, s3_key)
                WHERE id = %s
                """,
                (
                    filename, display_name, username, user_id,
                    start_label, end_label, report_s3_key, report_s3_key,
                    is_legacy, s3_key, int(session_id),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def get_session_by_id(session_id: int) -> dict | None:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, filename, s3_key, run_id,
                       TO_CHAR(processed_at AT TIME ZONE 'Asia/Kolkata',
                               'YYYY-MM-DD HH24:MI:SS') AS processed_at,
                       total_potholes, video_duration_sec,
                       user_id, username, display_name,
                       start_label, end_label, report_s3_key,
                       is_legacy
                FROM video_sessions
                WHERE id = %s
            """, (int(session_id),))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def get_session_by_run_id(run_id: str) -> dict | None:
    if not run_id:
        return None
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, filename, s3_key, run_id, total_potholes,
                       report_s3_key, username, is_legacy
                FROM video_sessions
                WHERE run_id = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (str(run_id),),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def delete_user(user_id: int) -> bool:
    """Hard-delete a user row (admin only)."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (int(user_id),))
            deleted = cur.rowcount > 0
        conn.commit()
        return deleted
    finally:
        conn.close()


def update_user_state(user_id: int, state_id: int | None) -> dict | None:
    """Admin: set videographer state (AP/TG) without changing districts."""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE users
                SET state_id = %s
                WHERE id = %s AND role = 'Videographer'
                RETURNING id, username, full_name, email, role, vendor_id,
                          state_id, district_id, district_ids, is_active
                """,
                (state_id, int(user_id)),
            )
            row = cur.fetchone()
        conn.commit()
        return enrich_user_districts(dict(row) if row else None)
    finally:
        conn.close()


def get_all_sessions_for_reports() -> list:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id,
                       filename,
                       display_name,
                       username,
                       is_legacy,
                       report_s3_key,
                       start_label,
                       end_label,
                       TO_CHAR(processed_at AT TIME ZONE 'Asia/Kolkata',
                               'YYYY-MM-DD HH24:MI:SS') AS processed_at,
                       total_potholes
                FROM video_sessions
                ORDER BY processed_at DESC
            """)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def save_potholes(session_id: int, rows, gps_extras_map: dict = None):
    """
    rows          : List[DetectionRow]
    gps_extras_map: {second_int: full GPS record dict} for extra fields from JSON log
    """
    if not rows:
        return
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            for r in rows:
                extras = {}
                if gps_extras_map and hasattr(r, "gps_second"):
                    extras = gps_extras_map.get(r.gps_second, {})
                geo = getattr(r, "_geo", None) or {}

                geom_ewkt = None
                if r.lat is not None and r.lon is not None:
                    geom_ewkt = f"SRID=4326;POINT({r.lon} {r.lat})"

                cur.execute("""
                    INSERT INTO potholes
                        (session_id, class_name, confidence, severity,
                         x1, y1, x2, y2,
                         latitude, longitude, location,
                         captured_at, map_link, frame_s3_url,
                         altitude, accuracy, speed, cumulative_distance_meters,
                         street_name, city, state, country, zip_code, full_address)
                    VALUES
                        (%s,%s,%s,%s,
                         %s,%s,%s,%s,
                         %s,%s, ST_GeomFromEWKT(%s),
                         %s,%s,%s,
                         %s,%s,%s,%s,
                         %s,%s,%s,%s,%s,%s)
                """, (
                    session_id, r.cls, float(r.conf), r.severity,
                    r.x1, r.y1, r.x2, r.y2,
                    r.lat, r.lon, geom_ewkt,
                    r.captured_at, r.map_link, r.s3_url or "",
                    extras.get("altitude"),
                    extras.get("accuracy"),
                    extras.get("speed"),
                    extras.get("cumulativeDistanceMeters"),
                    extras.get("streetName") or geo.get("street_name") or "",
                    extras.get("city") or geo.get("city") or "",
                    extras.get("state") or geo.get("state") or "",
                    extras.get("country") or geo.get("country") or "",
                    extras.get("zipCode", ""),
                    extras.get("fullAddress") or geo.get("full_address") or "",
                ))
        conn.commit()
    finally:
        conn.close()


# ── Read (Dashboard) ──────────────────────────────────────────────────────────

def get_all_sessions() -> list:
    """Returns list of dicts ordered by most recent first."""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id,
                       COALESCE(display_name, filename) AS filename,
                       filename AS original_filename,
                       display_name,
                       username,
                       is_legacy,
                       report_s3_key,
                       s3_key,
                       start_label,
                       end_label,
                       TO_CHAR(processed_at AT TIME ZONE 'Asia/Kolkata',
                               'YYYY-MM-DD HH24:MI:SS') AS processed_at,
                       total_potholes,
                       run_id
                FROM video_sessions
                ORDER BY processed_at DESC
            """)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_potholes_for_session(session_id: int) -> list:
    """Returns list of dicts for the pothole detail view."""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id,
                       class_name      AS class,
                       ROUND(confidence::numeric, 4) AS conf,
                       severity,
                       x1, y1, x2, y2,
                       latitude        AS lat,
                       longitude       AS lon,
                       captured_at,
                       street_name,
                       city, state, full_address,
                       altitude, speed,
                       map_link,
                       frame_s3_url
                FROM potholes
                WHERE session_id = %s
                ORDER BY id
            """, (session_id,))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
