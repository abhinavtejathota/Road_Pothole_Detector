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

# ── Warranty ──────────────────────────────────────────────────────────────────

def get_warranty_by_work_order(wo_id: int) -> dict | None:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT wr.*, vs.filename, v.company_name AS vendor_name
                FROM warranty_records wr
                JOIN work_orders wo ON wo.id = wr.work_order_id
                JOIN video_sessions vs ON vs.id = wo.session_id
                LEFT JOIN vendors v ON v.id = wo.vendor_id
                WHERE wr.work_order_id = %s
            """, (wo_id,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def refresh_warranty_statuses():
    """Update warranty_status based on expiry date — call periodically."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE warranty_records SET warranty_status =
                    CASE
                        WHEN warranty_expiry_date < CURRENT_DATE THEN 'Expired'
                        WHEN warranty_expiry_date <= CURRENT_DATE + INTERVAL '30 days' THEN 'Expiring'
                        ELSE 'Valid'
                    END
                WHERE warranty_status != 'Claimed'
            """)
        conn.commit()
    finally:
        conn.close()


