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

# ── Work Orders ───────────────────────────────────────────────────────────────

_SLA_HOURS = {"High": 48, "Medium": 168, "Low": 720}  # 48h / 7d / 30d


def _compute_sla(session_id: int, conn) -> tuple[str, datetime | None]:
    """Return (sla_tier, sla_due_date) based on worst severity in the session."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT severity FROM potholes WHERE session_id = %s
        """, (session_id,))
        severities = {r[0] for r in cur.fetchall()}

    tier = "Routine"
    hours = _SLA_HOURS["Low"]
    if "High" in severities:
        tier, hours = "Critical", _SLA_HOURS["High"]
    elif "Medium" in severities:
        tier, hours = "Standard", _SLA_HOURS["Medium"]
    due = datetime.utcnow() + timedelta(hours=hours)
    return tier, due


def create_work_order(session_id: int, created_by: str) -> int:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            # Prevent duplicate work orders for same session
            cur.execute("SELECT id FROM work_orders WHERE session_id = %s", (session_id,))
            existing = cur.fetchone()
            if existing:
                return existing[0]
            cur.execute("""
                INSERT INTO work_orders (session_id, status, created_by)
                VALUES (%s, 'Created', %s) RETURNING id
            """, (session_id, created_by))
            wo_id = cur.fetchone()[0]
            # Create work_order_potholes rows
            cur.execute("""
                INSERT INTO work_order_potholes (work_order_id, pothole_id, repair_sequence)
                SELECT %s, id,
                       ROW_NUMBER() OVER (ORDER BY
                           CASE severity WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END,
                           confidence DESC)
                FROM potholes WHERE session_id = %s
            """, (wo_id, session_id))
            # Audit
            cur.execute("""
                INSERT INTO status_history (entity_type, entity_id, from_status, to_status, changed_by)
                VALUES ('work_order', %s, NULL, 'Created', %s)
            """, (wo_id, created_by))
        conn.commit()
        return wo_id
    finally:
        conn.close()


def delete_work_order(wo_id: int) -> bool:
    """Remove a work order and related audit rows. Cascades cover WO children."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM status_history WHERE entity_type = 'work_order' AND entity_id = %s",
                (wo_id,),
            )
            cur.execute("DELETE FROM work_orders WHERE id = %s", (wo_id,))
            deleted = cur.rowcount > 0
        conn.commit()
        return deleted
    finally:
        conn.close()


def allocate_work_order(wo_id: int, vendor_id: int, estimated_budget: float,
                        remarks: str, allocated_by: str) -> bool:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT session_id, status FROM work_orders WHERE id = %s", (wo_id,))
            row = cur.fetchone()
            if not row:
                return False
            session_id, current_status = row
            sla_tier, sla_due = _compute_sla(session_id, conn)
            cur.execute("""
                UPDATE work_orders SET
                    vendor_id = %s, status = 'Allocated',
                    sla_tier = %s, sla_due_date = %s,
                    estimated_budget = %s, remarks = %s,
                    allocated_by = %s, allocated_at = NOW(), updated_at = NOW()
                WHERE id = %s
            """, (vendor_id, sla_tier, sla_due, estimated_budget, remarks,
                  allocated_by, wo_id))
            cur.execute("""
                INSERT INTO status_history
                    (entity_type, entity_id, from_status, to_status, comment, changed_by)
                VALUES ('work_order', %s, %s, 'Allocated', %s, %s)
            """, (wo_id, current_status, remarks, allocated_by))
        conn.commit()
        return True
    finally:
        conn.close()


def update_work_order_status(wo_id: int, new_status: str, comment: str,
                             changed_by: str, actual_spend: float = None) -> bool:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM work_orders WHERE id = %s", (wo_id,))
            row = cur.fetchone()
            if not row:
                return False
            current = row[0]
            ts_field = {
                "WIP": "started_at",
                "Completed": "completed_at",
                "Verified": "verified_at",
            }.get(new_status)
            ts_clause = f", {ts_field} = NOW()" if ts_field else ""
            spend_clause = ", actual_spend = %s" if actual_spend is not None else ""
            params = []
            if actual_spend is not None:
                params.append(actual_spend)
            params += [new_status, wo_id]
            cur.execute(f"""
                UPDATE work_orders
                SET status = %s, updated_at = NOW() {ts_clause} {spend_clause}
                WHERE id = %s
            """, [new_status] + ([actual_spend] if actual_spend is not None else []) + [wo_id])
            cur.execute("""
                INSERT INTO status_history
                    (entity_type, entity_id, from_status, to_status, comment, changed_by)
                VALUES ('work_order', %s, %s, %s, %s, %s)
            """, (wo_id, current, new_status, comment, changed_by))
            # Auto-create warranty when Verified
            if new_status == "Verified":
                cur.execute("""
                    INSERT INTO warranty_records
                        (work_order_id, warranty_period_months, warranty_start_date,
                         warranty_expiry_date, next_maintenance_date)
                    VALUES (%s, 12, CURRENT_DATE,
                            CURRENT_DATE + INTERVAL '12 months',
                            CURRENT_DATE + INTERVAL '6 months')
                    ON CONFLICT (work_order_id) DO NOTHING
                """, (wo_id,))
        conn.commit()
        update_vendor_performance_score(
            get_work_order(wo_id)["vendor_id"]
        )
        return True
    finally:
        conn.close()


def get_work_orders(status: str = None, vendor_id: int = None,
                    city: str = None, pin_code: str = None,
                    date_from: str = None, date_to: str = None) -> list:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            clauses, vals = [], []
            if status:
                clauses.append("wo.status = %s"); vals.append(status)
            if vendor_id:
                clauses.append("wo.vendor_id = %s"); vals.append(vendor_id)
            if city:
                clauses.append("p.city ILIKE %s"); vals.append(f"%{city}%")
            if pin_code:
                clauses.append("p.zip_code = %s"); vals.append(pin_code)
            if date_from:
                clauses.append("vs.processed_at >= %s"); vals.append(date_from)
            if date_to:
                clauses.append("vs.processed_at <= %s"); vals.append(date_to)
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            cur.execute(f"""
                SELECT wo.id, wo.status, wo.sla_tier, wo.sla_due_date,
                       wo.estimated_budget, wo.actual_spend,
                       wo.allocated_at, wo.completed_at, wo.verified_at,
                       wo.created_at, wo.allocated_by, wo.remarks,
                       vs.id AS session_id, vs.filename, vs.total_potholes,
                       TO_CHAR(vs.processed_at AT TIME ZONE 'Asia/Kolkata',
                               'YYYY-MM-DD HH24:MI') AS processed_at,
                       v.id AS vendor_id, v.company_name AS vendor_name,
                       v.contact_phone AS vendor_phone,
                       COALESCE(p.city, '') AS city,
                       COALESCE(p.zip_code, '') AS zip_code,
                       CASE WHEN wo.sla_due_date < NOW() AND wo.status NOT IN ('Verified','Failed')
                            THEN TRUE ELSE FALSE END AS sla_breached
                FROM work_orders wo
                JOIN video_sessions vs ON vs.id = wo.session_id
                LEFT JOIN vendors v ON v.id = wo.vendor_id
                LEFT JOIN LATERAL (
                    SELECT city, zip_code FROM potholes
                    WHERE session_id = vs.id LIMIT 1
                ) p ON TRUE
                {where}
                ORDER BY wo.created_at DESC
            """, vals)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_work_order(wo_id: int) -> dict | None:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT wo.*,
                       vs.filename, vs.total_potholes,
                       TO_CHAR(vs.processed_at AT TIME ZONE 'Asia/Kolkata',
                               'YYYY-MM-DD HH24:MI') AS processed_at,
                       v.company_name AS vendor_name,
                       v.contact_phone AS vendor_phone,
                       v.contact_email AS vendor_email,
                       CASE WHEN wo.sla_due_date < NOW() AND wo.status NOT IN ('Verified','Failed')
                            THEN TRUE ELSE FALSE END AS sla_breached
                FROM work_orders wo
                JOIN video_sessions vs ON vs.id = wo.session_id
                LEFT JOIN vendors v ON v.id = wo.vendor_id
                WHERE wo.id = %s
            """, (wo_id,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def get_work_order_potholes(wo_id: int) -> list:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT wop.id, wop.pothole_id, wop.status AS pothole_status,
                       wop.repair_sequence,
                       p.class_name, p.severity,
                       ROUND(p.confidence::numeric, 4) AS conf,
                       p.latitude AS lat, p.longitude AS lon,
                       p.full_address, p.city, p.zip_code,
                       p.map_link, p.frame_s3_url
                FROM work_order_potholes wop
                JOIN potholes p ON p.id = wop.pothole_id
                WHERE wop.work_order_id = %s
                ORDER BY wop.repair_sequence
            """, (wo_id,))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_status_history(entity_type: str, entity_id: int) -> list:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT from_status, to_status, comment, changed_by,
                       TO_CHAR(changed_at AT TIME ZONE 'Asia/Kolkata',
                               'YYYY-MM-DD HH24:MI:SS') AS changed_at
                FROM status_history
                WHERE entity_type = %s AND entity_id = %s
                ORDER BY changed_at
            """, (entity_type, entity_id))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


