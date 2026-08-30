"""Create videographer video3 with access to all Andhra Pradesh districts."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from werkzeug.security import generate_password_hash

import db_utils
from routes import survey_service as s

USERNAME = "video3"
PASSWORD = "video3"
FULL_NAME = "Videographer Three"
EMAIL = "video3@smartroad.local"
STATE_ID = 1  # Andhra Pradesh


def main():
    districts = s.list_districts("andhra")
    ids = [int(d["district_id"] or d["id"]) for d in districts]
    print(f"Andhra Pradesh districts: {len(ids)}")

    existing = db_utils.get_user_by_username(USERNAME)
    if existing:
        uid = int(existing["id"])
        print(f"User '{USERNAME}' already exists (id={uid}) — updating password + districts")
        conn = db_utils._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users
                    SET password_hash = %s,
                        full_name = %s,
                        email = %s,
                        role = %s,
                        state_id = %s,
                        district_id = %s,
                        district_ids = %s,
                        is_active = TRUE
                    WHERE id = %s
                    """,
                    (
                        generate_password_hash(PASSWORD),
                        FULL_NAME,
                        EMAIL,
                        "Videographer",
                        STATE_ID,
                        ids[0] if ids else None,
                        ids or None,
                        uid,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    else:
        uid = db_utils.create_user(
            username=USERNAME,
            password_hash=generate_password_hash(PASSWORD),
            full_name=FULL_NAME,
            email=EMAIL,
            role="Videographer",
            state_id=STATE_ID,
            district_ids=ids,
        )
        print(f"Created user id={uid}")

    u = db_utils.get_user_by_username(USERNAME)
    print(
        f"OK username={u['username']} id={u['id']} role={u['role']} "
        f"state_id={u['state_id']} districts={len(u.get('district_ids') or [])}"
    )


if __name__ == "__main__":
    main()
