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

# ── Users / Auth ──────────────────────────────────────────────────────────────

def normalize_district_ids(district_ids=None, district_id=None) -> list[int]:
    """Accept list/tuple/CSV/single id → unique int list (order preserved)."""
    raw = district_ids
    if raw is None or raw == "" or raw == []:
        raw = [district_id] if district_id not in (None, "") else []
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.replace("|", ",").split(",") if p.strip()]
        raw = parts
    elif isinstance(raw, (int, float)):
        raw = [int(raw)]
    out: list[int] = []
    seen: set[int] = set()
    for x in raw or []:
        try:
            v = int(x)
        except (TypeError, ValueError):
            continue
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def enrich_user_districts(row: dict | None) -> dict | None:
    """Ensure row has district_ids list and district_id = first (legacy primary)."""
    if not row:
        return row
    out = dict(row)
    ids = normalize_district_ids(out.get("district_ids"), out.get("district_id"))
    out["district_ids"] = ids
    out["district_id"] = ids[0] if ids else None
    return out


def create_user(username: str, password_hash: str, full_name: str,
                email: str, role: str, vendor_id: int = None,
                state_id: int = None, district_id: int = None,
                district_ids=None) -> int:
    """Create user. Videographers use state_id + one or more district_ids (LGD)."""
    ids = normalize_district_ids(district_ids, district_id)
    primary = ids[0] if ids else None
    arr = ids or None
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users (
                    username, password_hash, full_name, email, role, vendor_id,
                    state_id, district_id, district_ids
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """, (
                username, password_hash, full_name, email, role, vendor_id,
                state_id, primary, arr,
            ))
            uid = cur.fetchone()[0]
        conn.commit()
        return uid
    finally:
        conn.close()


def get_user_by_username(username: str) -> dict | None:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM users WHERE username = %s AND is_active = TRUE",
                (username,)
            )
            row = cur.fetchone()
            return enrich_user_districts(dict(row) if row else None)
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> dict | None:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            return enrich_user_districts(dict(row) if row else None)
    finally:
        conn.close()


def update_last_login(user_id: int):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET last_login = NOW() WHERE id = %s", (user_id,))
        conn.commit()
    finally:
        conn.close()


def get_user_token_version(user_id: int) -> int:
    """Return token_version (0 if column/user missing — never break auth cold-start)."""
    try:
        conn = _get_conn()
    except Exception:
        return 0
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "SELECT COALESCE(token_version, 0) FROM users WHERE id = %s",
                    (user_id,),
                )
            except Exception:
                # Column not migrated yet
                try:
                    conn.rollback()
                except Exception:
                    pass
                return 0
            row = cur.fetchone()
            return int(row[0]) if row else 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def bump_user_token_version(user_id: int) -> int:
    """Invalidate all mobile JWTs for this user (logout)."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    UPDATE users
                    SET token_version = COALESCE(token_version, 0) + 1
                    WHERE id = %s
                    RETURNING token_version
                    """,
                    (user_id,),
                )
            except Exception:
                # Ensure column then retry once
                conn.rollback()
                cur.execute(
                    """
                    ALTER TABLE users
                    ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0
                    """
                )
                cur.execute(
                    """
                    UPDATE users
                    SET token_version = COALESCE(token_version, 0) + 1
                    WHERE id = %s
                    RETURNING token_version
                    """,
                    (user_id,),
                )
            row = cur.fetchone()
        conn.commit()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def get_all_users() -> list:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, username, full_name, email, role, vendor_id,
                       state_id, district_id, district_ids, is_active,
                       TO_CHAR(created_at AT TIME ZONE 'Asia/Kolkata', 'YYYY-MM-DD') AS created_at
                FROM users ORDER BY id
            """)
            return [enrich_user_districts(dict(r)) for r in cur.fetchall()]
    finally:
        conn.close()


def update_user_districts(user_id: int, state_id: int | None, district_ids) -> dict | None:
    """Update videographer state + districts. Keeps at least one district."""
    ids = normalize_district_ids(district_ids, None)
    if not ids:
        raise ValueError("At least one district is required")
    primary = ids[0]
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE users
                SET state_id = %s, district_id = %s, district_ids = %s
                WHERE id = %s AND role = 'Videographer'
                RETURNING id, username, full_name, email, role, vendor_id,
                          state_id, district_id, district_ids, is_active
                """,
                (state_id, primary, ids, int(user_id)),
            )
            row = cur.fetchone()
        conn.commit()
        return enrich_user_districts(dict(row) if row else None)
    finally:
        conn.close()


