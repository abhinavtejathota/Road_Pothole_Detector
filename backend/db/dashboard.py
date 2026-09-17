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

# ── Dashboard KPIs ────────────────────────────────────────────────────────────

def get_staff_dashboard_bundle(*, map_limit: int = 0) -> dict:
    """One-connection staff dashboard payload (keeps Supabase pool usage tiny)."""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    COUNT(*) AS total_sessions,
                    SUM(total_potholes) AS total_potholes
                FROM video_sessions
            """)
            sessions_row = dict(cur.fetchone())

            cur.execute("""
                SELECT
                    COUNT(*) AS total_work_orders,
                    COUNT(*) FILTER (WHERE status = 'Created')   AS unassigned,
                    COUNT(*) FILTER (WHERE status = 'Allocated') AS allocated,
                    COUNT(*) FILTER (WHERE status = 'WIP')       AS wip,
                    COUNT(*) FILTER (WHERE status = 'Completed') AS completed,
                    COUNT(*) FILTER (WHERE status = 'Verified')  AS verified,
                    COUNT(*) FILTER (WHERE status = 'Failed')    AS failed,
                    COUNT(*) FILTER (
                        WHERE sla_due_date < NOW()
                        AND status NOT IN ('Verified','Failed')
                    ) AS sla_breached
                FROM work_orders
            """)
            wo_row = dict(cur.fetchone())

            cur.execute("""
                SELECT COUNT(*) AS unassigned_sessions
                FROM video_sessions vs
                WHERE NOT EXISTS (
                    SELECT 1 FROM work_orders wo WHERE wo.session_id = vs.id
                )
            """)
            unassigned_sessions = cur.fetchone()["unassigned_sessions"]
            kpi = {**sessions_row, **wo_row, "unassigned_sessions": unassigned_sessions}

            cur.execute("""
                SELECT wo.id, wo.status, wo.sla_tier,
                       wo.sla_due_date,
                       EXTRACT(EPOCH FROM (NOW() - wo.sla_due_date))/3600 AS hours_overdue,
                       vs.filename,
                       v.company_name AS vendor_name, v.contact_phone,
                       COALESCE(p.city,'') AS city
                FROM work_orders wo
                JOIN video_sessions vs ON vs.id = wo.session_id
                LEFT JOIN vendors v ON v.id = wo.vendor_id
                LEFT JOIN LATERAL (
                    SELECT city FROM potholes WHERE session_id = vs.id LIMIT 1
                ) p ON TRUE
                WHERE wo.sla_due_date < NOW()
                  AND wo.status NOT IN ('Verified','Failed')
                ORDER BY wo.sla_due_date
                LIMIT 100
            """)
            breached = [dict(r) for r in cur.fetchall()]

            cur.execute("""
                SELECT v.id, v.company_name, v.performance_score, v.status,
                       COUNT(wo.id) AS total_tasks,
                       COUNT(wo.id) FILTER (WHERE wo.status IN ('Allocated','WIP')) AS active_tasks,
                       COUNT(wo.id) FILTER (WHERE wo.status = 'Verified') AS verified_tasks,
                       COUNT(wo.id) FILTER (WHERE wo.status = 'Failed') AS failed_tasks,
                       ROUND(AVG(
                           EXTRACT(EPOCH FROM (wo.completed_at - wo.allocated_at))/3600
                       ) FILTER (WHERE wo.completed_at IS NOT NULL)::numeric, 1) AS avg_completion_hours,
                       COUNT(wo.id) FILTER (
                           WHERE wo.status = 'Verified'
                           AND wo.completed_at IS NOT NULL
                           AND wo.sla_due_date IS NOT NULL
                           AND wo.completed_at <= wo.sla_due_date
                       ) AS on_time_tasks
                FROM vendors v
                LEFT JOIN work_orders wo ON wo.vendor_id = v.id
                GROUP BY v.id
                ORDER BY v.performance_score DESC
                LIMIT 50
            """)
            vendors = [dict(r) for r in cur.fetchall()]

            cur.execute("""
                SELECT wr.id, wr.work_order_id, wr.warranty_status,
                       wr.warranty_start_date, wr.warranty_expiry_date,
                       wr.next_maintenance_date, wr.claim_count,
                       vs.filename, v.company_name AS vendor_name,
                       COALESCE(p.city,'') AS city,
                       (wr.warranty_expiry_date - CURRENT_DATE) AS days_remaining
                FROM warranty_records wr
                JOIN work_orders wo ON wo.id = wr.work_order_id
                JOIN video_sessions vs ON vs.id = wo.session_id
                LEFT JOIN vendors v ON v.id = wo.vendor_id
                LEFT JOIN LATERAL (
                    SELECT city FROM potholes WHERE session_id = vs.id LIMIT 1
                ) p ON TRUE
                WHERE wr.warranty_status != 'Expired'
                ORDER BY wr.warranty_expiry_date
                LIMIT 100
            """)
            warranty = [dict(r) for r in cur.fetchall()]

            map_rows: list = []
            if map_limit and map_limit > 0:
                cur.execute("""
                    SELECT p.latitude AS lat, p.longitude AS lon,
                           p.severity, p.city,
                           COALESCE(wo.status, 'Unassigned') AS status
                    FROM potholes p
                    LEFT JOIN work_order_potholes wop ON wop.pothole_id = p.id
                    LEFT JOIN work_orders wo ON wo.id = wop.work_order_id
                    WHERE p.latitude IS NOT NULL AND p.longitude IS NOT NULL
                    ORDER BY p.id DESC
                    LIMIT %s
                """, (int(map_limit),))
                map_rows = [dict(r) for r in cur.fetchall()]

        return {
            "kpi": kpi,
            "breached": breached,
            "vendors": vendors,
            "warranty": warranty,
            "map": map_rows,
        }
    finally:
        conn.close()


def get_kpi_summary() -> dict:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    COUNT(*) AS total_sessions,
                    SUM(total_potholes) AS total_potholes
                FROM video_sessions
            """)
            sessions_row = dict(cur.fetchone())

            cur.execute("""
                SELECT
                    COUNT(*) AS total_work_orders,
                    COUNT(*) FILTER (WHERE status = 'Created')   AS unassigned,
                    COUNT(*) FILTER (WHERE status = 'Allocated') AS allocated,
                    COUNT(*) FILTER (WHERE status = 'WIP')       AS wip,
                    COUNT(*) FILTER (WHERE status = 'Completed') AS completed,
                    COUNT(*) FILTER (WHERE status = 'Verified')  AS verified,
                    COUNT(*) FILTER (WHERE status = 'Failed')    AS failed,
                    COUNT(*) FILTER (
                        WHERE sla_due_date < NOW()
                        AND status NOT IN ('Verified','Failed')
                    ) AS sla_breached
                FROM work_orders
            """)
            wo_row = dict(cur.fetchone())

            # Sessions without any work order = unassigned sessions
            cur.execute("""
                SELECT COUNT(*) AS unassigned_sessions
                FROM video_sessions vs
                WHERE NOT EXISTS (
                    SELECT 1 FROM work_orders wo WHERE wo.session_id = vs.id
                )
            """)
            unassigned_sessions = cur.fetchone()["unassigned_sessions"]

        return {**sessions_row, **wo_row, "unassigned_sessions": unassigned_sessions}
    finally:
        conn.close()


def get_vendor_performance_report() -> list:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT v.id, v.company_name, v.performance_score, v.status,
                       COUNT(wo.id) AS total_tasks,
                       COUNT(wo.id) FILTER (WHERE wo.status IN ('Allocated','WIP')) AS active_tasks,
                       COUNT(wo.id) FILTER (WHERE wo.status = 'Verified') AS verified_tasks,
                       COUNT(wo.id) FILTER (WHERE wo.status = 'Failed') AS failed_tasks,
                       ROUND(AVG(
                           EXTRACT(EPOCH FROM (wo.completed_at - wo.allocated_at))/3600
                       ) FILTER (WHERE wo.completed_at IS NOT NULL)::numeric, 1) AS avg_completion_hours,
                       COUNT(wo.id) FILTER (
                           WHERE wo.status = 'Verified'
                           AND wo.completed_at IS NOT NULL
                           AND wo.sla_due_date IS NOT NULL
                           AND wo.completed_at <= wo.sla_due_date
                       ) AS on_time_tasks
                FROM vendors v
                LEFT JOIN work_orders wo ON wo.vendor_id = v.id
                GROUP BY v.id
                ORDER BY v.performance_score DESC
            """)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_sla_breached_tasks() -> list:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT wo.id, wo.status, wo.sla_tier,
                       wo.sla_due_date,
                       EXTRACT(EPOCH FROM (NOW() - wo.sla_due_date))/3600 AS hours_overdue,
                       vs.filename,
                       v.company_name AS vendor_name, v.contact_phone,
                       COALESCE(p.city,'') AS city
                FROM work_orders wo
                JOIN video_sessions vs ON vs.id = wo.session_id
                LEFT JOIN vendors v ON v.id = wo.vendor_id
                LEFT JOIN LATERAL (
                    SELECT city FROM potholes WHERE session_id = vs.id LIMIT 1
                ) p ON TRUE
                WHERE wo.sla_due_date < NOW()
                  AND wo.status NOT IN ('Verified','Failed')
                ORDER BY wo.sla_due_date
            """)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_warranty_dashboard() -> list:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT wr.id, wr.work_order_id, wr.warranty_status,
                       wr.warranty_start_date, wr.warranty_expiry_date,
                       wr.next_maintenance_date, wr.claim_count,
                       vs.filename, v.company_name AS vendor_name,
                       COALESCE(p.city,'') AS city,
                       (wr.warranty_expiry_date - CURRENT_DATE) AS days_remaining
                FROM warranty_records wr
                JOIN work_orders wo ON wo.id = wr.work_order_id
                JOIN video_sessions vs ON vs.id = wo.session_id
                LEFT JOIN vendors v ON v.id = wo.vendor_id
                LEFT JOIN LATERAL (
                    SELECT city FROM potholes WHERE session_id = vs.id LIMIT 1
                ) p ON TRUE
                WHERE wr.warranty_status != 'Expired'
                ORDER BY wr.warranty_expiry_date
            """)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_pothole_map_data(limit: int | None = 5000) -> list:
    """Return lat/lon/severity/status for potholes (capped — unbounded freeze dashboard)."""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            sql = """
                SELECT p.latitude AS lat, p.longitude AS lon,
                       p.severity, p.city,
                       COALESCE(wo.status, 'Unassigned') AS work_status,
                       p.map_link, p.frame_s3_url
                FROM potholes p
                LEFT JOIN work_order_potholes wop ON wop.pothole_id = p.id
                LEFT JOIN work_orders wo ON wo.id = wop.work_order_id
                WHERE p.latitude IS NOT NULL AND p.longitude IS NOT NULL
                ORDER BY p.id DESC
            """
            if limit is not None and int(limit) > 0:
                cur.execute(sql + " LIMIT %s", (int(limit),))
            else:
                cur.execute(sql)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


