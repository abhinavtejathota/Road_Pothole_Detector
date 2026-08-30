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

# ── Vendors ───────────────────────────────────────────────────────────────────

def create_vendor(data: dict) -> int:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO vendors
                    (company_name, registration_number, gst_number, pan_number,
                     contact_person_name, contact_phone, contact_email,
                     address, city, district, state, pin_code,
                     description, specializations, max_active_tasks, status)
                VALUES
                    (%(company_name)s, %(registration_number)s, %(gst_number)s, %(pan_number)s,
                     %(contact_person_name)s, %(contact_phone)s, %(contact_email)s,
                     %(address)s, %(city)s, %(district)s, %(state)s, %(pin_code)s,
                     %(description)s, %(specializations)s, %(max_active_tasks)s, %(status)s)
                RETURNING id
            """, data)
            vid = cur.fetchone()[0]
        conn.commit()
        return vid
    finally:
        conn.close()


def get_all_vendors(status_filter: str = None) -> list:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if status_filter:
                cur.execute("""
                    SELECT v.*,
                           COUNT(wo.id) FILTER (WHERE wo.status IN ('Allocated','WIP')) AS active_tasks
                    FROM vendors v
                    LEFT JOIN work_orders wo ON wo.vendor_id = v.id
                    WHERE v.status = %s
                    GROUP BY v.id ORDER BY v.company_name
                """, (status_filter,))
            else:
                cur.execute("""
                    SELECT v.*,
                           COUNT(wo.id) FILTER (WHERE wo.status IN ('Allocated','WIP')) AS active_tasks
                    FROM vendors v
                    LEFT JOIN work_orders wo ON wo.vendor_id = v.id
                    GROUP BY v.id ORDER BY v.company_name
                """)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_vendor(vendor_id: int) -> dict | None:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT v.*,
                       COUNT(wo.id) FILTER (WHERE wo.status IN ('Allocated','WIP')) AS active_tasks,
                       COUNT(wo.id) FILTER (WHERE wo.status = 'Verified') AS completed_tasks
                FROM vendors v
                LEFT JOIN work_orders wo ON wo.vendor_id = v.id
                WHERE v.id = %s
                GROUP BY v.id
            """, (vendor_id,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def update_vendor(vendor_id: int, data: dict) -> bool:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE vendors SET
                    company_name = %(company_name)s,
                    registration_number = %(registration_number)s,
                    gst_number = %(gst_number)s,
                    pan_number = %(pan_number)s,
                    contact_person_name = %(contact_person_name)s,
                    contact_phone = %(contact_phone)s,
                    contact_email = %(contact_email)s,
                    address = %(address)s,
                    city = %(city)s,
                    district = %(district)s,
                    state = %(state)s,
                    pin_code = %(pin_code)s,
                    description = %(description)s,
                    specializations = %(specializations)s,
                    max_active_tasks = %(max_active_tasks)s,
                    status = %(status)s,
                    status_reason = %(status_reason)s,
                    updated_at = NOW()
                WHERE id = %(id)s
            """, {**data, "id": vendor_id})
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    finally:
        conn.close()


def delete_vendor(vendor_id: int) -> bool:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM vendors WHERE id = %s", (vendor_id,))
            deleted = cur.rowcount > 0
        conn.commit()
        return deleted
    finally:
        conn.close()


def update_vendor_performance_score(vendor_id: int):
    """Recompute and store the performance score (0–100) for a vendor."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                WITH stats AS (
                    SELECT
                        COUNT(*) FILTER (WHERE status = 'Verified') AS verified,
                        COUNT(*) FILTER (WHERE status IN ('Verified','Failed')) AS closed,
                        COUNT(*) FILTER (
                            WHERE status = 'Verified'
                            AND completed_at IS NOT NULL
                            AND sla_due_date IS NOT NULL
                            AND completed_at <= sla_due_date
                        ) AS on_time,
                        COUNT(*) FILTER (WHERE status = 'Verified') AS total_verified
                    FROM work_orders WHERE vendor_id = %s
                )
                SELECT
                    CASE WHEN closed = 0 THEN 100.0
                         ELSE ROUND((
                             0.6 * (verified::float / NULLIF(closed,0)) * 100
                           + 0.4 * (on_time::float / NULLIF(total_verified,1)) * 100
                         )::numeric, 1)
                    END AS score
                FROM stats
            """, (vendor_id,))
            row = cur.fetchone()
            score = row[0] if row else 100.0
            cur.execute("UPDATE vendors SET performance_score = %s WHERE id = %s",
                        (score, vendor_id))
        conn.commit()
    finally:
        conn.close()


def get_suggested_vendors(session_id: int, top_n: int = 3) -> list:
    """Return top vendors for a session based on capacity + performance score."""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT v.id, v.company_name, v.city, v.state,
                       v.performance_score, v.max_active_tasks,
                       COUNT(wo.id) FILTER (WHERE wo.status IN ('Allocated','WIP')) AS active_tasks,
                       v.max_active_tasks - COUNT(wo.id) FILTER (
                           WHERE wo.status IN ('Allocated','WIP')
                       ) AS available_capacity
                FROM vendors v
                LEFT JOIN work_orders wo ON wo.vendor_id = v.id
                WHERE v.status = 'Active'
                GROUP BY v.id
                HAVING COUNT(wo.id) FILTER (WHERE wo.status IN ('Allocated','WIP'))
                       < v.max_active_tasks
                ORDER BY v.performance_score DESC, available_capacity DESC
                LIMIT %s
            """, (top_n,))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


