"""One-shot modularization: CSS + api + db + survey + tracking + detector.

Creates packages/files with compatibility shims. Safe to re-run only on a clean tree.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {path.relative_to(ROOT)} ({len(text.splitlines())} lines)")


def slice_lines(src: Path, start: int, end: int | None = None) -> str:
    """1-based inclusive start; end exclusive if given, else to EOF."""
    lines = src.read_text(encoding="utf-8").splitlines(keepends=True)
    a = start - 1
    b = end - 1 if end is not None else len(lines)
    return "".join(lines[a:b])


def strip_trailing_blank(s: str) -> str:
    return s.rstrip() + "\n"


# ── CSS ───────────────────────────────────────────────────────────────────────

def split_css() -> None:
    src = ROOT / "frontend/src/styles/global.css"
    lines = src.read_text(encoding="utf-8").splitlines(keepends=True)

    def chunk(a: int, b: int) -> str:
        return "".join(lines[a - 1 : b - 1])

    # tokens + reset + app-shell shell start (lines 1-52)
    write(ROOT / "frontend/src/styles/tokens.css", chunk(1, 53))

    # Layout: sidebar + main shell (53-403)
    write(ROOT / "frontend/src/components/Layout.css", chunk(53, 404))

    # Shared UI: cards/grid/kpi through badges (404-740) + empty/misc without login
    # Keep shared primitives together
    write(ROOT / "frontend/src/styles/ui.css", chunk(404, 741) + chunk(922, 1321) + chunk(1321, 1440))

    write(ROOT / "frontend/src/pages/Login.css", chunk(741, 922))

    # Survey map section — split by prefixes with a simple pass later; first cut whole then carve
    survey_block = chunk(1440, 1868)
    write(ROOT / "frontend/src/pages/Survey.css", survey_block)

    # Detection + bench
    det_block = chunk(1868, 2409)
    # Split bench: find .bench-
    det_lines = det_block.splitlines(keepends=True)
    bench_start = None
    for i, l in enumerate(det_lines):
        if ".bench-" in l or l.strip().startswith("/*") and "bench" in l.lower():
            # find first .bench- rule
            pass
        if re.match(r"^\.bench-", l) or re.match(r"^/\*.*bench", l, re.I):
            bench_start = i
            break
    # Fallback: known ~2219 absolute → relative within block = 2219-1868
    if bench_start is None:
        bench_start = max(0, 2219 - 1868)
    write(ROOT / "frontend/src/pages/Detection.css", "".join(det_lines[:bench_start]))
    write(ROOT / "frontend/src/pages/ModelBench.css", "".join(det_lines[bench_start:]))

    write(ROOT / "frontend/src/pages/Tracking.css", chunk(2409, 2438))
    # covered ribbon + field
    write(ROOT / "frontend/src/components/CoveredRibbonModal.css", chunk(2438, 2510))
    write(ROOT / "frontend/src/pages/FieldCapture.css", chunk(2510, 2619))

    # mobile block + complaints
    write(ROOT / "frontend/src/styles/responsive.css", chunk(2619, 2767))
    write(ROOT / "frontend/src/pages/Complaints.css", chunk(2767, len(lines) + 1))

    # Carve Dashboard / Tracking rules out of Survey.css by prefix
    survey_path = ROOT / "frontend/src/pages/Survey.css"
    survey_text = survey_path.read_text(encoding="utf-8")
    dash_rules = []
    track_extra = []
    keep = []
    # crude: split on top-level selectors starting at column 0
    parts = re.split(r"(?=\n(?![ \t/*]))", "\n" + survey_text)
    # Simpler line-based: assign blocks starting with .dashboard- / .road-length- / .tracking-
    current = []
    current_kind = "survey"

    def flush():
        nonlocal current, current_kind
        text = "".join(current)
        if not text.strip():
            current = []
            return
        if current_kind == "dashboard":
            dash_rules.append(text)
        elif current_kind == "tracking":
            track_extra.append(text)
        else:
            keep.append(text)
        current = []

    for line in survey_text.splitlines(keepends=True):
        if line.startswith(".dashboard-") or line.startswith(".road-length-") or line.startswith(".road-network-legend") or line.startswith(".kpi-"):
            flush()
            current_kind = "dashboard"
        elif line.startswith(".tracking-"):
            flush()
            current_kind = "tracking"
        elif line.startswith(".") or line.startswith("@") or (line.startswith("/*") and "──" in line):
            # new top-level comment/section for survey
            if line.startswith(".") and not line.startswith((".dashboard-", ".road-length-", ".road-network", ".tracking-", ".kpi-")):
                flush()
                current_kind = "survey"
            elif line.startswith("/*"):
                flush()
                current_kind = "survey"
        current.append(line)
    flush()
    write(survey_path, "".join(keep) if keep else survey_text)
    if dash_rules:
        write(ROOT / "frontend/src/pages/Dashboard.css", "".join(dash_rules))
    if track_extra:
        track_path = ROOT / "frontend/src/pages/Tracking.css"
        track_path.write_text(
            track_path.read_text(encoding="utf-8") + "\n" + "".join(track_extra),
            encoding="utf-8",
        )

    # New global.css = imports only
    imports = """/* Staff portal styles — split by page/component; shared layers first. */
@import "./tokens.css";
@import "./ui.css";
@import "./responsive.css";
@import "../components/Layout.css";
@import "../components/CoveredRibbonModal.css";
@import "../pages/Login.css";
@import "../pages/Survey.css";
@import "../pages/Dashboard.css";
@import "../pages/Tracking.css";
@import "../pages/Detection.css";
@import "../pages/ModelBench.css";
@import "../pages/FieldCapture.css";
@import "../pages/Complaints.css";
"""
    # Dashboard.css may not exist if carve failed
    if not (ROOT / "frontend/src/pages/Dashboard.css").exists():
        write(ROOT / "frontend/src/pages/Dashboard.css", "/* dashboard styles (extracted from survey block) */\n")

    write(ROOT / "frontend/src/styles/global.css", imports)

    # Also add direct imports in key JSX files for co-location clarity
    jsx_imports = {
        "frontend/src/components/Layout.jsx": 'import "./Layout.css";\n',
        "frontend/src/pages/Login.jsx": 'import "./Login.css";\n',
        "frontend/src/pages/Survey.jsx": 'import "./Survey.css";\n',
        "frontend/src/pages/Dashboard.jsx": 'import "./Dashboard.css";\n',
        "frontend/src/pages/Tracking.jsx": 'import "./Tracking.css";\n',
        "frontend/src/pages/Detection.jsx": 'import "./Detection.css";\n',
        "frontend/src/pages/ModelBench.jsx": 'import "./ModelBench.css";\n',
        "frontend/src/pages/FieldCapture.jsx": 'import "./FieldCapture.css";\n',
        "frontend/src/pages/FieldUpload.jsx": 'import "./FieldCapture.css";\n',
        "frontend/src/pages/Complaints.jsx": 'import "./Complaints.css";\n',
        "frontend/src/components/CoveredRibbonModal.jsx": 'import "./CoveredRibbonModal.css";\n',
    }
    for rel, imp in jsx_imports.items():
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if imp.strip() in text:
            continue
        # after last import
        m = list(re.finditer(r"^import .+$", text, re.M))
        if m:
            pos = m[-1].end()
            text = text[:pos] + "\n" + imp + text[pos:]
        else:
            text = imp + text
        write(path, text)


# ── API ───────────────────────────────────────────────────────────────────────

API_HEADER = '''"""JSON API for the React SPA — split across routes/api/*.py."""
'''

HELPERS = None  # filled from file


def split_api() -> None:
    src = ROOT / "routes/api.py"
    if not src.exists():
        # already packaged?
        if (ROOT / "routes/api/__init__.py").exists():
            print("api package already exists, skip")
            return
        raise SystemExit("routes/api.py missing")

    text = src.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    # Find section starts
    sections = []
    for i, l in enumerate(lines):
        if l.startswith("# ──"):
            name = re.sub(r"[─\s#]+", " ", l).strip().lower()
            sections.append((i, name, l.strip()))

    # Map to module names
    def mod_for(title: str) -> str:
        t = title.lower()
        if "auth" in t:
            return "auth"
        if "dashboard" in t:
            return "dashboard"
        if "vendor" in t:
            return "vendors"
        if "task" in t:
            return "tasks"
        if "validation" in t:
            return "validation_routes"
        if "survey" in t:
            return "survey_routes"
        if "detection" in t:
            return "detection_routes"
        if "complaint" in t or "citizen" in t:
            return "complaints"
        if "model" in t or "bench" in t:
            return "model_bench_routes"
        if "field" in t or "upload" in t:
            return "upload_routes"
        if "tracking" in t or "live" in t:
            return "tracking_routes"
        return "misc_routes"

    # helpers = lines before first section (after imports we'll rebuild)
    first_sec = sections[0][0]
    preamble = "".join(lines[:first_sec])

    # Extract helpers functions from preamble — keep imports in __init__
    pkg = ROOT / "routes/api_pkg_tmp"

    # Build package at routes/api/ — move old api.py aside first
    bak = ROOT / "routes/_api_monolith.py"
    if not bak.exists():
        bak.write_text(text, encoding="utf-8")
        print(f"backed up api.py -> {bak.name}")

    # Write helpers
    # preamble has imports + api_bp + helpers. We'll recreate cleanly.
    helpers_src = slice_lines(bak, 26, first_sec + 1)  # from _serialize through before Auth
    # Remove blank / trailing
    write(
        ROOT / "routes/api/_helpers.py",
        '"""Shared API helpers."""\n'
        "from datetime import date, datetime\n"
        "from decimal import Decimal\n\n"
        "from flask_login import current_user\n\n"
        "from routes import survey_service\n\n"
        + "".join(
            l
            for l in helpers_src.splitlines(keepends=True)
            if not l.startswith("api_bp")
        ),
    )

    # Common imports for route modules
    common_imports = '''from flask import jsonify, request, abort
from flask_login import login_required, login_user, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

import db_utils
from routes.api import api_bp
from routes.api._helpers import _serialize, _user_payload, _vendor_from_request
from routes.user_model import User
from routes.constants import VALID_TRANSITIONS
from routes import validation as validation_module
from routes import survey_service
from routes import detection_service
from routes import model_bench_service
from routes import field_upload_service
from routes import tracking_service
from routes import auto_track_service
'''

    # Split body into modules
    ranges = []
    for idx, (start, _name, title) in enumerate(sections):
        end = sections[idx + 1][0] if idx + 1 < len(sections) else len(lines)
        ranges.append((mod_for(title), start, end, title))

    # Merge consecutive same module
    merged = []
    for mod, start, end, title in ranges:
        if merged and merged[-1][0] == mod:
            merged[-1] = (mod, merged[-1][1], end, merged[-1][3] + " / " + title)
        else:
            merged.append([mod, start, end, title])

    # dashboard module also has users — split users out if we can find /users
    bodies: dict[str, list[str]] = {}
    for mod, start, end, title in merged:
        bodies.setdefault(mod, [])
        bodies[mod].append(f"# {title}\n")
        bodies[mod].extend(lines[start:end])

    # Special: split users from dashboard body
    if "dashboard" in bodies:
        dash = bodies["dashboard"]
        users_start = None
        for i, l in enumerate(dash):
            if '@api_bp.route("/users"' in l or "@api_bp.route('/users'" in l:
                users_start = i
                break
        if users_start is not None:
            bodies["users"] = dash[users_start:]
            bodies["dashboard"] = dash[:users_start]

    # Special: split reports from complaints
    if "complaints" in bodies:
        comp = bodies["complaints"]
        rep_start = None
        for i, l in enumerate(comp):
            if "/reports" in l and "@api_bp.route" in l:
                rep_start = i
                break
        if rep_start is not None:
            bodies["reports_routes"] = comp[rep_start:]
            bodies["complaints"] = comp[:rep_start]

    for mod, body_lines in bodies.items():
        body = "".join(body_lines)
        # Fix relative imports already in monolith if any
        write(
            ROOT / f"routes/api/{mod}.py",
            f'"""API routes: {mod}."""\nimport os\nimport threading\nimport time\n\n{common_imports}\n\n{body}',
        )

    init = '''"""API blueprint package — route modules register onto api_bp."""
from flask import Blueprint

api_bp = Blueprint("api", __name__, url_prefix="/api")

# Import helpers for external use
from routes.api._helpers import _serialize, _user_payload, _vendor_from_request  # noqa: E402,F401

# Register routes (side-effect imports)
from routes.api import (  # noqa: E402,F401
    auth,
    dashboard,
    users,
    vendors,
    tasks,
    validation_routes,
    survey_routes,
    detection_routes,
    complaints,
    reports_routes,
    model_bench_routes,
    upload_routes,
    tracking_routes,
)

__all__ = ["api_bp", "_serialize", "_user_payload", "_vendor_from_request"]
'''
    # Only import modules that exist
    existing = [p.stem for p in (ROOT / "routes/api").glob("*.py") if p.stem not in ("__init__", "_helpers")]
    init = '''"""API blueprint package — route modules register onto api_bp."""
from flask import Blueprint

api_bp = Blueprint("api", __name__, url_prefix="/api")

from routes.api._helpers import _serialize, _user_payload, _vendor_from_request  # noqa: E402,F401

'''
    for m in existing:
        init += f"from routes.api import {m}  # noqa: E402,F401\n"
    init += '\n__all__ = ["api_bp", "_serialize", "_user_payload", "_vendor_from_request"]\n'
    write(ROOT / "routes/api/__init__.py", init)

    # Remove old api.py so package takes precedence
    src.unlink()
    print("removed routes/api.py (package routes/api/ now)")


# ── DB ────────────────────────────────────────────────────────────────────────

def split_db() -> None:
    src = ROOT / "db_utils.py"
    text = src.read_text(encoding="utf-8")
    bak = ROOT / "_db_utils_monolith.py"
    if not bak.exists():
        bak.write_text(text, encoding="utf-8")

    lines = bak.read_text(encoding="utf-8").splitlines(keepends=True)

    # Section markers from explore
    cuts = [
        ("host", 1, 110),
        ("connection", 110, 487),
        ("schema", 487, 586),
        ("sessions", 586, 824),
        ("dashboard_read", 824, 883),  # merge into sessions later
        ("users", 890, 1100),
        ("vendors", 1100, 1279),
        ("work_orders", 1279, 1548),
        ("validation", 1548, 1635),
        ("warranty", 1635, 1674),
        ("dashboard", 1674, 1826),
        ("survey_state", 1826, 2162),
        ("tracking_state", 2162, 3256),
    ]

    # Fix: lines 883-889 are banner; 586-823 includes misplaced user fns — keep as-is for first pass
    # Prepend dotenv + imports to each file that needs them

    base_imports = '''# db package module
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)

import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.pool
    import psycopg2.extensions
    _HAS_PSYCOPG2 = True
except ImportError:
    _HAS_PSYCOPG2 = False
'''

    # host.py needs the preamble imports without load_dotenv twice - simplify:
    # connection imports from host; others import from connection

    write(ROOT / "db/host.py", base_imports + "\n" + slice_lines(bak, 22, 110))
    # connection includes pool stuff — needs host helpers
    conn_body = slice_lines(bak, 110, 487)
    write(
        ROOT / "db/connection.py",
        base_imports
        + "\nfrom db.host import resolve_db_host, _local_ipv4_addrs, _localhost_postgres_reachable  # noqa: F401\n\n"
        + conn_body,
    )
    write(
        ROOT / "db/schema.py",
        "from db.connection import _get_conn, is_db_configured, _HAS_PSYCOPG2\nimport psycopg2\n\n"
        + slice_lines(bak, 487, 586),
    )

    def db_mod(name: str, start: int, end: int, extra: str = "") -> None:
        write(
            ROOT / f"db/{name}.py",
            "from db.connection import _get_conn, is_db_configured\n"
            "try:\n    import psycopg2\n    import psycopg2.extras\nexcept ImportError:\n    psycopg2 = None\n\n"
            + extra
            + slice_lines(bak, start, end),
        )

    # sessions = write section + dashboard read getters
    write(
        ROOT / "db/sessions.py",
        "from db.connection import _get_conn, is_db_configured\n"
        "try:\n    import psycopg2\n    import psycopg2.extras\nexcept ImportError:\n    psycopg2 = None\n\n"
        + slice_lines(bak, 586, 883),
    )
    db_mod("users", 890, 1100)
    db_mod("vendors", 1100, 1279)
    db_mod("work_orders", 1279, 1548)
    db_mod("validation", 1548, 1635)
    db_mod("warranty", 1635, 1674)
    db_mod("dashboard", 1674, 1826)
    db_mod("survey_state", 1826, 2162)
    # tracking from 2162 includes survey assignment bits + tracking — split at 2761 if needed
    # Keep survey assignment with survey_state extension
    write(
        ROOT / "db/survey_state.py",
        "from db.connection import _get_conn, is_db_configured\n"
        "try:\n    import psycopg2\n    import psycopg2.extras\nexcept ImportError:\n    psycopg2 = None\n\n"
        + slice_lines(bak, 1826, 2761),
    )
    write(
        ROOT / "db/tracking_state.py",
        "from db.connection import _get_conn, is_db_configured\n"
        "try:\n    import psycopg2\n    import psycopg2.extras\nexcept ImportError:\n    psycopg2 = None\n\n"
        + slice_lines(bak, 2162, 2357)  # coverage persist cluster overlaps — take tracking exclusive
        + slice_lines(bak, 2761, 3256),
    )
    # Actually tracking_state overlapping survey is messy. Simpler: one gis_state file for 1826-end
    write(
        ROOT / "db/gis_state.py",
        "from db.connection import _get_conn, is_db_configured\n"
        "try:\n    import psycopg2\n    import psycopg2.extras\nexcept ImportError:\n    psycopg2 = None\n\n"
        + slice_lines(bak, 1826, 3256),
    )
    # Remove the partial survey_state/tracking_state to avoid duplicate defs
    for p in [ROOT / "db/survey_state.py", ROOT / "db/tracking_state.py"]:
        if p.exists():
            p.unlink()
            print(f"removed overlapping {p.name}")

    # __init__ re-exports everything by importing modules and star-exporting public names
    init = '''"""PostgreSQL / PostGIS helpers — split modules with db_utils shim compatibility."""
from db.host import *  # noqa: F401,F403
from db.connection import *  # noqa: F401,F403
from db.schema import *  # noqa: F401,F403
from db.sessions import *  # noqa: F401,F403
from db.users import *  # noqa: F401,F403
from db.vendors import *  # noqa: F401,F403
from db.work_orders import *  # noqa: F401,F403
from db.validation import *  # noqa: F401,F403
from db.warranty import *  # noqa: F401,F403
from db.dashboard import *  # noqa: F401,F403
from db.gis_state import *  # noqa: F401,F403
'''
    write(ROOT / "db/__init__.py", init)
    write(ROOT / "db_utils.py", '"""Compatibility shim — implementation lives in the db package."""\nfrom db import *  # noqa: F401,F403\n')


# ── Detector ──────────────────────────────────────────────────────────────────

def split_detector() -> None:
    src = ROOT / "pothole_detector.py"
    bak = ROOT / "_pothole_detector_monolith.py"
    text = src.read_text(encoding="utf-8")
    if not bak.exists():
        bak.write_text(text, encoding="utf-8")

    # Line ranges from explore
    # 1-44 imports
    # 45-388 gps
    # 389-577 types
    # 578-606 track
    # 607-744 video_io
    # 745-1123 parallel
    # 1125-end pipeline

    imports = slice_lines(bak, 1, 45)
    write(ROOT / "detector/gps.py", imports + "\n" + slice_lines(bak, 45, 389))
    write(
        ROOT / "detector/types.py",
        imports
        + "\nfrom detector.gps import *  # noqa: F401,F403\n\n"
        + slice_lines(bak, 389, 578),
    )
    write(ROOT / "detector/track.py", imports + "\n" + slice_lines(bak, 578, 607))
    write(
        ROOT / "detector/video_io.py",
        imports + "\n" + slice_lines(bak, 607, 745),
    )
    write(
        ROOT / "detector/parallel.py",
        imports
        + "\nfrom detector.types import *  # noqa: F401,F403\n"
        + "from detector.track import *  # noqa: F401,F403\n"
        + "from detector.video_io import *  # noqa: F401,F403\n"
        + "from detector.gps import *  # noqa: F401,F403\n\n"
        + slice_lines(bak, 745, 1125),
    )
    write(
        ROOT / "detector/pipeline.py",
        imports
        + "\nfrom detector.gps import *  # noqa: F401,F403\n"
        + "from detector.types import *  # noqa: F401,F403\n"
        + "from detector.track import *  # noqa: F401,F403\n"
        + "from detector.video_io import *  # noqa: F401,F403\n"
        + "from detector.parallel import *  # noqa: F401,F403\n\n"
        + slice_lines(bak, 1125, None),
    )
    write(
        ROOT / "detector/__init__.py",
        '''"""Pothole detection pipeline — split modules."""
from detector.gps import *  # noqa: F401,F403
from detector.types import *  # noqa: F401,F403
from detector.track import *  # noqa: F401,F403
from detector.video_io import *  # noqa: F401,F403
from detector.parallel import *  # noqa: F401,F403
from detector.pipeline import *  # noqa: F401,F403
''',
    )
    write(
        ROOT / "pothole_detector.py",
        '"""Compatibility shim — implementation lives in the detector package."""\nfrom detector import *  # noqa: F401,F403\n',
    )


# ── Survey / tracking services ────────────────────────────────────────────────

def split_service(name: str, monolith_rel: str, cuts: list[tuple[str, int, int | None]]) -> None:
    """Split service into chained modules: each file star-imports the previous."""
    src = ROOT / monolith_rel
    bak = ROOT / f"_{Path(monolith_rel).stem}_monolith.py"
    text = src.read_text(encoding="utf-8")
    if not bak.exists():
        bak.write_text(text, encoding="utf-8")

    pkg = ROOT / "routes" / name
    prev = None
    for idx, (mod, start, end) in enumerate(cuts):
        body = slice_lines(bak, start, end)
        if idx == 0:
            content = body  # includes original imports
        else:
            content = (
                f'"""{name}.{mod} — extends {prev}."""\n'
                f"from __future__ import annotations\n\n"
                f"from routes.{name}.{prev} import *  # noqa: F401,F403\n\n"
                + body
            )
        write(pkg / f"{mod}.py", content)
        prev = mod

    last = cuts[-1][0]
    write(
        pkg / "__init__.py",
        f'"""{name} service package."""\n'
        f"from routes.{name}.{last} import *  # noqa: F401,F403\n",
    )
    write(
        ROOT / f"routes/{name}_service.py",
        f'"""Compatibility shim — implementation lives in routes.{name}."""\n'
        f"from routes.{name} import *  # noqa: F401,F403\n",
    )


def split_survey() -> None:
    split_service(
        "survey",
        "routes/survey_service.py",
        [
            ("state", 1, 850),
            ("geocode", 850, 2217),
            ("geometry", 2217, 2698),
            ("progress", 2698, 5035),
            ("assignments", 5035, None),
        ],
    )


def split_tracking() -> None:
    split_service(
        "tracking",
        "routes/tracking_service.py",
        [
            ("store", 1, 407),
            ("trail", 407, 1088),
            ("coverage", 1088, 1367),
            ("capture", 1367, 1796),
            ("admin", 1796, None),
        ],
    )


def split_detection_service() -> None:
    print("detection_service left as single file (already ~787 lines)")


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["css", "api", "db", "detector", "survey", "tracking", "all"], default="all")
    args = ap.parse_args()
    only = args.only
    if only in ("css", "all"):
        print("=== CSS ===")
        split_css()
    if only in ("api", "all"):
        print("=== API ===")
        split_api()
    if only in ("db", "all"):
        print("=== DB ===")
        split_db()
    if only in ("detector", "all"):
        print("=== DETECTOR ===")
        split_detector()
    if only in ("survey", "all"):
        print("=== SURVEY ===")
        split_survey()
    if only in ("tracking", "all"):
        print("=== TRACKING ===")
        split_tracking()
    if only in ("all",):
        split_detection_service()
    print("done")


if __name__ == "__main__":
    main()
