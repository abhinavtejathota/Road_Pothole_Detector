"""API blueprint package — route modules register onto api_bp."""
from flask import Blueprint

api_bp = Blueprint("api", __name__, url_prefix="/api")

from routes.api._helpers import _serialize, _user_payload, _vendor_from_request  # noqa: E402,F401

from routes.api import auth  # noqa: E402,F401
from routes.api import complaints  # noqa: E402,F401
from routes.api import dashboard  # noqa: E402,F401
from routes.api import detection_routes  # noqa: E402,F401
from routes.api import model_bench_routes  # noqa: E402,F401
from routes.api import reports_routes  # noqa: E402,F401
from routes.api import survey_routes  # noqa: E402,F401
from routes.api import tasks  # noqa: E402,F401
from routes.api import admin_dashboard  # noqa: E402,F401
from routes.api import vg_details  # noqa: E402,F401
from routes.api import tracking_routes  # noqa: E402,F401
from routes.api import upload_routes  # noqa: E402,F401
from routes.api import users  # noqa: E402,F401
from routes.api import validation_routes  # noqa: E402,F401
from routes.api import vendors  # noqa: E402,F401

__all__ = ["api_bp", "_serialize", "_user_payload", "_vendor_from_request"]
