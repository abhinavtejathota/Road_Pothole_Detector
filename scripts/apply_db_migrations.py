#!/usr/bin/env python3
"""Apply SmartRoad DB migrations idempotently.

  python scripts/apply_db_migrations.py

Runs every ``migrations/*.sql`` in filename order (safe to re-run: scripts use
IF NOT EXISTS / ON CONFLICT). Records each file stem in ``schema_migrations``.
Also runs ``db_utils.init_db()`` so ``schema.sql`` stays in sync.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

import db_utils


def _split_sql(sql: str) -> list[str]:
    """Naive split on semicolons outside dollar-quotes — enough for our mig files."""
    parts: list[str] = []
    buf: list[str] = []
    in_dollar = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if sql.startswith("$$", i):
            in_dollar = not in_dollar
            buf.append("$$")
            i += 2
            continue
        if ch == ";" and not in_dollar:
            stmt = "".join(buf).strip()
            if stmt and not all(line.strip().startswith("--") or not line.strip() for line in stmt.splitlines()):
                parts.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def apply_file(path: Path) -> tuple[str, str]:
    """Returns (status, detail). status: ok|warn|skip."""
    sql = path.read_text(encoding="utf-8")
    stem = path.stem
    conn = db_utils._get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM schema_migrations WHERE id = %s", (stem,))
            if cur.fetchone():
                return "skip", f"{stem} (already applied)"
            # Prefer executing whole file (supports DO $$ blocks better as one batch).
            try:
                cur.execute(sql)
            except Exception:
                conn.rollback()
                # Fall back to statement split for mixed files
                for stmt in _split_sql(sql):
                    cur.execute(stmt)
            cur.execute(
                "INSERT INTO schema_migrations (id) VALUES (%s) ON CONFLICT (id) DO NOTHING",
                (stem,),
            )
        conn.commit()
        return "ok", stem
    except Exception as e:
        conn.rollback()
        return "warn", f"{stem}: {e}"
    finally:
        conn.close()


def verify() -> list[str]:
    issues: list[str] = []
    checks = {
        "survey_daily_assignments": ["legs", "polyline", "mode", "meta"],
        "users": ["state_id", "district_id", "district_ids", "token_version"],
        "reporter_otp_challenges": ["otp_hash", "plain_otp"],
        "survey_cleared_assignments": [
            "assignment_date", "user_id", "polyline", "entry_snapshot", "consumed_at",
        ],
    }
    conn = db_utils._get_conn()
    try:
        with conn.cursor() as cur:
            for table, cols in checks.items():
                cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
                if not cur.fetchone()[0]:
                    issues.append(f"missing table {table}")
                    continue
                cur.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema='public' AND table_name=%s
                    """,
                    (table,),
                )
                have = {r[0] for r in cur.fetchall()}
                for c in cols:
                    if c not in have:
                        issues.append(f"missing {table}.{c}")
            cur.execute("SELECT id FROM schema_migrations ORDER BY id")
            applied = [r[0] for r in cur.fetchall()]
            print(f"schema_migrations ({len(applied)}): {applied}")
    finally:
        conn.close()
    return issues


def main() -> int:
    if not db_utils.is_db_configured():
        print("ERROR: DB not configured (.env missing DB_NAME/DB_USER)")
        return 1

    host = db_utils.resolve_db_host()
    print(f"DB host resolved -> {host}")

    print("Running init_db() (schema.sql)...")
    db_utils.init_db()
    print("init_db OK")

    mig_dir = ROOT / "migrations"
    files = sorted(mig_dir.glob("*.sql"))
    print(f"Applying {len(files)} migration file(s)...")
    for path in files:
        status, detail = apply_file(path)
        print(f"  [{status}] {detail}")

    print("Verifying critical tables/columns...")
    issues = verify()
    if issues:
        print("ISSUES:")
        for line in issues:
            print(f"  - {line}")
        return 1

    print("Database migrations complete - schema OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
