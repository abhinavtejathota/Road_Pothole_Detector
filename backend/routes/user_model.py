"""Flask-Login User model wrapping the users DB table."""
from __future__ import annotations

import time
import threading

from flask_login import UserMixin
import db_utils

# Remote Postgres (e.g. 45.x) adds ~50–200ms per query. Every authenticated
# request hits User.get via flask-login — cache briefly so Find routes / ping /
# upload don't pay a full round-trip for the same session user each time.
_USER_CACHE: dict[int, tuple[float, "User"]] = {}
_USER_CACHE_LOCK = threading.Lock()
_USER_CACHE_TTL_SEC = float(__import__("os").getenv("USER_CACHE_TTL_S", "60"))


def invalidate_user_cache(user_id: int | None = None) -> None:
    with _USER_CACHE_LOCK:
        if user_id is None:
            _USER_CACHE.clear()
        else:
            _USER_CACHE.pop(int(user_id), None)


class User(UserMixin):
    def __init__(self, row: dict):
        self.id = row["id"]
        self.username = row["username"]
        self.full_name = row["full_name"]
        self.email = row["email"]
        self.role = row["role"]
        self.vendor_id = row.get("vendor_id")
        self.state_id = row.get("state_id")
        ids = row.get("district_ids") or []
        if not ids and row.get("district_id") is not None:
            ids = [row.get("district_id")]
        try:
            self.district_ids = [int(x) for x in ids]
        except (TypeError, ValueError):
            self.district_ids = []
        self.district_id = self.district_ids[0] if self.district_ids else row.get("district_id")

    def allowed_district_ids(self) -> list[int]:
        return list(self.district_ids or ([] if self.district_id is None else [int(self.district_id)]))

    @staticmethod
    def get(user_id: int):
        uid = int(user_id)
        now = time.monotonic()
        with _USER_CACHE_LOCK:
            hit = _USER_CACHE.get(uid)
            if hit and (now - hit[0]) < _USER_CACHE_TTL_SEC:
                return hit[1]
            stale = hit[1] if hit else None
        try:
            row = db_utils.get_user_by_id(uid)
        except Exception as e:
            # Never wedge flask-login / login page on a hung pool — prefer stale
            # cache, else anonymous so /api/auth/login can still authenticate.
            print(f"[user] get({uid}) failed soft: {e}", flush=True)
            return stale
        if not row:
            with _USER_CACHE_LOCK:
                _USER_CACHE.pop(uid, None)
            return None
        user = User(row)
        with _USER_CACHE_LOCK:
            _USER_CACHE[uid] = (now, user)
        return user

    def is_admin(self):
        """Field Admin (field-ops dashboard, tracking, survey admin)."""
        return self.role == "Admin"

    def is_dev_admin(self):
        """DevAdmin (platform: Users, Detection, Model bench)."""
        return self.role == "DevAdmin"

    def can_manage_field_ops(self):
        """Admin (field) or DevAdmin — tracking, survey admin, reports."""
        return self.is_admin() or self.is_dev_admin()

    def is_supervisor(self):
        return self.role in ("DevAdmin", "Supervisor")

    def is_allocator(self):
        return self.role in ("DevAdmin", "Allocator")

    def is_vendor_role(self):
        return self.role == "Vendor"

    def is_videographer(self):
        return self.role == "Videographer"
