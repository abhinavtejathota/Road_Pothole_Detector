from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
import json
import math
import os
import time

from db.connection import _get_conn, is_db_configured, _HAS_PSYCOPG2
import psycopg2
from pathlib import Path

# ── Schema init ───────────────────────────────────────────────────────────────

# Arbitrary constant key for init_db()'s advisory lock — only needs to be
# unique within this app, not globally.
_SCHEMA_INIT_LOCK_KEY = 8817231


def init_db():
    """Create tables if they don't exist (idempotent).

    Wrapped in a Postgres advisory lock so concurrent callers serialize
    instead of deadlocking on the schema DDL — e.g. multiple gunicorn workers
    each running create_app() -> init_db() at boot without --preload used to
    hit "deadlock detected ... AccessExclusiveLock ... blocked by process X".
    """
    schema_path = Path(__file__).resolve().parents[1] / "schema.sql"
    sql = schema_path.read_text(encoding="utf-8")
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (_SCHEMA_INIT_LOCK_KEY,))
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                # Existing DBs: add mobile JWT revoke column if missing.
                cur.execute("""
                    ALTER TABLE users
                    ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0
                """)
                cur.execute("""
                    ALTER TABLE video_sessions
                    ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE SET NULL
                """)
                cur.execute("""
                    ALTER TABLE video_sessions
                    ADD COLUMN IF NOT EXISTS username TEXT
                """)
                cur.execute("""
                    ALTER TABLE video_sessions
                    ADD COLUMN IF NOT EXISTS display_name TEXT
                """)
                cur.execute("""
                    ALTER TABLE video_sessions
                    ADD COLUMN IF NOT EXISTS start_label TEXT
                """)
                cur.execute("""
                    ALTER TABLE video_sessions
                    ADD COLUMN IF NOT EXISTS end_label TEXT
                """)
                cur.execute("""
                    ALTER TABLE video_sessions
                    ADD COLUMN IF NOT EXISTS report_s3_key TEXT
                """)
                cur.execute("""
                    ALTER TABLE video_sessions
                    ADD COLUMN IF NOT EXISTS report_generated_at TIMESTAMP WITH TIME ZONE
                """)
                cur.execute("""
                    ALTER TABLE video_sessions
                    ADD COLUMN IF NOT EXISTS is_legacy BOOLEAN NOT NULL DEFAULT FALSE
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_video_sessions_username
                    ON video_sessions(username)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_video_sessions_user_id
                    ON video_sessions(user_id)
                """)
                # Soft FK if users table exists (fresh installs apply via schema.sql DO block)
                cur.execute("""
                    DO $$
                    BEGIN
                        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'users')
                           AND NOT EXISTS (
                               SELECT 1 FROM pg_constraint WHERE conname = 'video_sessions_user_id_fkey'
                           ) THEN
                            ALTER TABLE video_sessions
                                ADD CONSTRAINT video_sessions_user_id_fkey
                                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL;
                        END IF;
                    END $$;
                """)
                cur.execute("""
                    INSERT INTO schema_migrations (id) VALUES ('20260717_video_session_reports')
                    ON CONFLICT (id) DO NOTHING
                """)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (_SCHEMA_INIT_LOCK_KEY,))
            conn.commit()
    finally:
        conn.close()


