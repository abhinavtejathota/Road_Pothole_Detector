import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tests = [
    ("db_utils", "import db_utils as m; print(hasattr(m,'is_db_configured'), hasattr(m,'create_user'), hasattr(m,'survey_db_load_state'))"),
    ("detector", "import pothole_detector as m; print(hasattr(m,'pothole_detector'), hasattr(m,'load_gps_log'))"),
    ("survey", "from routes import survey_service as m; print(hasattr(m,'today_ist'), hasattr(m,'list_districts'))"),
    ("tracking", "from routes import tracking_service as m; print(hasattr(m,'record_ping'), hasattr(m,'resolve_videographer_coverage'))"),
    ("api", "from routes.api import api_bp; print(api_bp.name, len(list(api_bp.deferred_functions)))"),
]

failed = 0
for name, code in tests:
    print("===", name)
    try:
        exec(code)
    except Exception as e:
        failed += 1
        print("FAIL", type(e).__name__, e)
        traceback.print_exc()

raise SystemExit(failed)
