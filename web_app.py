"""
SmartRoad portal — Flask + React SPA (default monolith on one port).

Split deploy (see docs/ops/MULTI_SERVICE.md):
  ./scripts/services.sh start
"""
import os

import smartroad_path  # noqa: F401 — repo root + backend/ on sys.path

from routes.app_factory import create_app, run_service


def create_application():
    return create_app(os.getenv("SMARTROAD_SERVICE") or "portal")


# WSGI entry for waitress/gunicorn only (not used by `python web_app.py` __main__).
application = create_application() if __name__ != "__main__" else None


if __name__ == "__main__":
    os.environ.setdefault("SMARTROAD_SERVICE", "portal")
    os.environ.setdefault("FLASK_PORT", os.getenv("FLASK_PORT", "5005"))
    run_service(
        os.environ.get("SMARTROAD_SERVICE") or "portal",
        default_port=int(os.getenv("FLASK_PORT", "5005")),
    )
