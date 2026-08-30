"""One-off script: create the first Admin user for the web app.

Run once from repo root:
  set BOOTSTRAP_ADMIN_USERNAME=...
  set BOOTSTRAP_ADMIN_PASSWORD=...   # min 12 chars
  python scripts/bootstrap_admin.py

Never commit real credentials. Defaults are disabled in production.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from werkzeug.security import generate_password_hash

import db_utils

_PROD = (os.getenv("SMARTROAD_ENV") or "").strip().lower() in ("production", "prod")


def main():
    username = (os.getenv("BOOTSTRAP_ADMIN_USERNAME") or "").strip()
    password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD") or ""
    full_name = (os.getenv("BOOTSTRAP_ADMIN_FULL_NAME") or "Administrator").strip()
    email = (os.getenv("BOOTSTRAP_ADMIN_EMAIL") or "admin@smartroad.local").strip()

    if not username or not password:
        print(
            "Set BOOTSTRAP_ADMIN_USERNAME and BOOTSTRAP_ADMIN_PASSWORD "
            "(password ≥ 12 chars). Refusing hardcoded defaults.",
            file=sys.stderr,
        )
        sys.exit(1)
    if len(password) < 12:
        print("BOOTSTRAP_ADMIN_PASSWORD must be at least 12 characters.", file=sys.stderr)
        sys.exit(1)
    if _PROD and password.lower() in (username.lower(), "password", "admin", "changeme"):
        print("Refusing weak bootstrap password in production.", file=sys.stderr)
        sys.exit(1)

    if db_utils.get_user_by_username(username):
        print(f"User '{username}' already exists — nothing to do.")
        return
    db_utils.create_user(
        username=username,
        password_hash=generate_password_hash(password),
        full_name=full_name,
        email=email,
        role="DevAdmin",
    )
    print(f"Created DevAdmin user: username={username!r} (password not printed)")


if __name__ == "__main__":
    main()
