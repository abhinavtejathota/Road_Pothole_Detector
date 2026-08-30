"""Ensure videographer accounts video3 (AP-only) and video4 (AP+TG all districts).

Run from repo root:  python scripts/bootstrap_videographers.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from werkzeug.security import generate_password_hash

import db_utils
from routes import survey_service as ss


def all_district_ids(state_key: str) -> list[int]:
    return [int(d["district_id"]) for d in ss.list_districts(state_key)]


def upsert_vg(username: str, password: str, full_name: str, state_id: int, district_ids: list[int]):
    existing = db_utils.get_user_by_username(username)
    if existing:
        if existing.get("role") != "Videographer":
            print(f"SKIP {username}: exists as role={existing.get('role')}")
            return
        row = db_utils.update_user_districts(existing["id"], state_id, district_ids)
        print(f"Updated {username}: state_id={state_id} districts={len(district_ids)}")
        return row
    uid = db_utils.create_user(
        username=username,
        password_hash=generate_password_hash(password),
        full_name=full_name,
        email=f"{username}@smartroad.local",
        role="Videographer",
        state_id=state_id,
        district_ids=district_ids,
    )
    print(f"Created {username} id={uid}: state_id={state_id} districts={len(district_ids)} password={password}")
    return uid


def main():
    if not db_utils.is_db_configured():
        print("Database not configured — set DB_* in .env")
        return
    ap = all_district_ids("andhra")
    tg = all_district_ids("telangana")
    if not ap:
        print("No Andhra districts found — run GIS district download first.")
        return
    print(f"Andhra districts: {len(ap)}; Telangana: {len(tg)}")

    # video3 — Andhra Pradesh only (all AP districts)
    upsert_vg("video3", "video3", "Videographer AP", state_id=1, district_ids=ap)

    # video4 — full AP + TG
    both = ap + [d for d in tg if d not in set(ap)]
    upsert_vg("video4", "video4", "Videographer AP+TG", state_id=1, district_ids=both)


if __name__ == "__main__":
    main()
