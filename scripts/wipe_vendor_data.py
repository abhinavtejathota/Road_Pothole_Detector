#!/usr/bin/env python3
"""Wipe all vendor rows from PostgreSQL (vendor_zones first for FK safety)."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=True)
    except Exception:
        pass


def wipe_vendors(*, dry_run: bool) -> dict:
    import db_utils

    if not db_utils.is_db_configured():
        return {"error": "DB not configured"}

    conn = db_utils._get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM vendors")
            n = int(cur.fetchone()[0] or 0)
            if dry_run:
                print(f"[dry-run] would delete {n} vendor(s) (+ related vendor_zones)")
                return {"vendors": n, "dry_run": True}
            cur.execute("UPDATE work_orders SET vendor_id = NULL WHERE vendor_id IS NOT NULL")
            cur.execute("UPDATE users SET vendor_id = NULL WHERE vendor_id IS NOT NULL")
            cur.execute("DELETE FROM vendor_zones")
            zones = cur.rowcount
            cur.execute("DELETE FROM vendors")
            vendors = cur.rowcount
        conn.commit()
        print(f"deleted {vendors} vendor(s), {zones} vendor_zone row(s)")
        return {"vendors": vendors, "vendor_zones": zones}
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete all vendor data from Postgres.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Required to execute deletes")
    args = parser.parse_args()
    _load_env()
    os.chdir(ROOT)
    if not args.dry_run and not args.yes:
        print("Refusing to delete without --yes (or use --dry-run).")
        return 1
    wipe_vendors(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
