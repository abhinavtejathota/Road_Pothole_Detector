#!/usr/bin/env python3
"""Seed local demo users + vendor for smartroad_ap."""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

from werkzeug.security import generate_password_hash
import db_utils

USERS = [
    {"username": "devadmin", "password": "SmartRoadDev1!", "full_name": "Dev Administrator", "email": "devadmin@smartroad.local", "role": "DevAdmin"},
    {"username": "admin", "password": "SmartRoadAdmin1!", "full_name": "Field Admin", "email": "admin@smartroad.local", "role": "Admin"},
    {"username": "allocator", "password": "SmartRoadAlloc1!", "full_name": "Work Allocator", "email": "allocator@smartroad.local", "role": "Allocator"},
    {"username": "supervisor", "password": "SmartRoadSuper1!", "full_name": "Field Supervisor", "email": "supervisor@smartroad.local", "role": "Supervisor"},
    {"username": "vendor1", "password": "SmartRoadVendor1!", "full_name": "Demo Vendor", "email": "vendor1@smartroad.local", "role": "Vendor"},
    {"username": "video3", "password": "video3pass12!", "full_name": "Videographer AP", "email": "video3@smartroad.local", "role": "Videographer", "state_id": 1, "district_ids": [506, 510, 520]},
    {"username": "video4", "password": "video4pass12!", "full_name": "Videographer AP+TG", "email": "video4@smartroad.local", "role": "Videographer", "state_id": 1, "district_ids": [506, 510, 520, 681, 686]},
]


def ensure_vendor():
    conn = db_utils._get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM vendors ORDER BY id LIMIT 1")
            row = cur.fetchone()
            if row:
                return int(row[0])
            cur.execute(
                """
                INSERT INTO vendors (company_name, contact_person_name, contact_phone, contact_email, status)
                VALUES (%s,%s,%s,%s,'Active') RETURNING id
                """,
                ("Demo Roadworks Pvt Ltd", "Vendor Contact", "9999999999", "vendor@smartroad.local"),
            )
            vid = int(cur.fetchone()[0])
        conn.commit()
        print(f"[ok] vendor id={vid}")
        return vid
    finally:
        conn.close()


def main():
    print("host", db_utils.resolve_db_host(), "db", __import__("os").getenv("DB_NAME"))
    # deferred mig
    stem = "20260720_complaint_rejection_remark"
    conn = db_utils._get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM schema_migrations WHERE id=%s", (stem,))
            if not cur.fetchone():
                cur.execute(
                    "ALTER TABLE reporter_complaints ADD COLUMN IF NOT EXISTS rejection_remark TEXT"
                )
                cur.execute(
                    "INSERT INTO schema_migrations (id) VALUES (%s) ON CONFLICT DO NOTHING",
                    (stem,),
                )
                conn.commit()
                print(f"[ok] {stem}")
            else:
                print(f"[skip] {stem}")
    except Exception as e:
        conn.rollback()
        print(f"[warn] {stem}: {e}")
    finally:
        conn.close()

    vendor_id = ensure_vendor()
    for spec in USERS:
        existing = db_utils.get_user_by_username(spec["username"])
        if existing:
            if spec["role"] == "Vendor" and vendor_id and not existing.get("vendor_id"):
                conn = db_utils._get_conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE users SET vendor_id=%s WHERE id=%s",
                            (vendor_id, existing["id"]),
                        )
                    conn.commit()
                finally:
                    conn.close()
            print(f"[skip] {spec['username']}")
            continue
        kwargs = dict(
            username=spec["username"],
            password_hash=generate_password_hash(spec["password"]),
            full_name=spec["full_name"],
            email=spec["email"],
            role=spec["role"],
        )
        if spec["role"] == "Vendor":
            kwargs["vendor_id"] = vendor_id
        if spec["role"] == "Videographer":
            kwargs["state_id"] = spec.get("state_id")
            kwargs["district_ids"] = spec.get("district_ids")
        uid = db_utils.create_user(**kwargs)
        print(f"[ok] {spec['role']:14} {spec['username']} / {spec['password']} id={uid}")

    users = db_utils.get_all_users()
    print("users:", [(u["id"], u["username"], u["role"]) for u in users])


if __name__ == "__main__":
    main()
