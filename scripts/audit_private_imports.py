#!/usr/bin/env python3
"""Audit private-name imports across modular packages.

Problem: ``from module import *`` does NOT bind names starting with ``_``.
After the detector/db/routes splits, helpers like ``_open_video_capture`` and
``_get_conn`` must be re-exported via ``globals().update(...)`` or
``detector._importutil.reexport``, not star-imports.

Run from repo root:
  python scripts/audit_private_imports.py

Exit code 1 if any issue is found.
"""
from __future__ import annotations

import ast
import dis
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Runtime trees that must not rely on star-import for private helpers.
RUNTIME_DIRS = (
    ROOT / "detector",
    ROOT / "db",
    ROOT / "routes",
)
RUNTIME_SHIMS = (
    ROOT / "pothole_detector.py",
    ROOT / "db_utils.py",
    ROOT / "web_app.py",
)

# Packages / modules to import and bytecode-scan for missing LOAD_GLOBAL.
IMPORT_TARGETS = [
    "detector",
    "detector.gps",
    "detector.types",
    "detector.track",
    "detector.video_io",
    "detector.parallel",
    "detector.pipeline",
    "pothole_detector",
    "db",
    "db.host",
    "db.connection",
    "db.schema",
    "db.sessions",
    "db.users",
    "db.vendors",
    "db.work_orders",
    "db.validation",
    "db.warranty",
    "db.dashboard",
    "db.gis_state",
    "db_utils",
    "routes.detection",
    "routes.detection.status",
    "routes.detection.catalog",
    "routes.detection.run",
    "routes.detection.sessions",
    "routes.survey",
    "routes.survey.state",
    "routes.survey.geocode",
    "routes.survey.geometry",
    "routes.survey.progress",
    "routes.survey.assignments",
    "routes.tracking",
    "routes.tracking.store",
    "routes.tracking.trail",
    "routes.tracking.coverage",
    "routes.tracking.capture",
    "routes.tracking.admin",
    "routes.detection_service",
    "routes.survey_service",
    "routes.tracking_service",
    "routes.field_upload_service",
    "routes.app_factory",
    "routes.api",
    "routes.api.auth",
    "routes.api.tasks",
    "routes.api.vendors",
    "routes.api.dashboard",
    "routes.api.users",
    "routes.api.survey_routes",
    "routes.api.upload_routes",
    "routes.api.detection_routes",
    "routes.api.tracking_routes",
]

# Known false positives: dynamic / optional / injected names.
ALLOW_MISSING = {
    # multiprocessing / worker payloads sometimes resolve via __main__
    "_",
    # @contextmanager decorators reference this typing helper in bytecode
    "_GeneratorContextManager",
}


def _iter_py_files() -> list[Path]:
    files: list[Path] = []
    for d in RUNTIME_DIRS:
        if not d.is_dir():
            continue
        files.extend(p for p in d.rglob("*.py") if "__pycache__" not in p.parts)
    for p in RUNTIME_SHIMS:
        if p.is_file():
            files.append(p)
    return sorted(files)


def find_star_imports() -> list[str]:
    """Any ``from … import *`` in runtime code is a private-name footgun."""
    issues: list[str] = []
    for path in _iter_py_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as e:
            issues.append(f"SYNTAX {path.relative_to(ROOT)}: {e}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == "*" for alias in (node.names or [])
            ):
                mod = node.module or ""
                rel = path.relative_to(ROOT).as_posix()
                issues.append(
                    f"STAR_IMPORT {rel}:{node.lineno}  from {mod} import *  "
                    "(skips _private names — use reexport/globals().update)"
                )
    return issues


def _code_objects(code):
    stack = [code]
    while stack:
        c = stack.pop()
        yield c
        for const in c.co_consts:
            if hasattr(const, "co_code"):
                stack.append(const)


def globals_loaded_by(fn) -> set[str]:
    """Names loaded as globals from ``fn`` and nested code objects."""
    names: set[str] = set()
    try:
        code = fn.__code__
    except AttributeError:
        return names
    for c in _code_objects(code):
        for instr in dis.get_instructions(c):
            if instr.opname in ("LOAD_GLOBAL", "LOAD_NAME") and isinstance(instr.argval, str):
                names.add(instr.argval)
    return names


def find_missing_privates(mod) -> list[str]:
    """Functions defined in ``mod`` that LOAD_GLOBAL a missing ``_name``."""
    issues: list[str] = []
    mod_name = getattr(mod, "__name__", "?")
    ns = vars(mod)
    builtins_ns = __builtins__ if isinstance(__builtins__, dict) else vars(__builtins__)

    for attr, obj in list(ns.items()):
        if not callable(obj):
            continue
        # Only check callables defined in this module (skip re-exports from elsewhere
        # when their __module__ differs — still check if same package family).
        obj_mod = getattr(obj, "__module__", None)
        if obj_mod and obj_mod != mod_name:
            # Re-exported helper: still ensure *this* module can see what *its own*
            # functions need; skip foreign function bodies.
            continue
        try:
            used = globals_loaded_by(obj)
        except Exception:
            continue
        for name in sorted(used):
            if name in ALLOW_MISSING:
                continue
            if not (name.startswith("_") and not name.startswith("__")):
                continue
            if name in ns or name in builtins_ns:
                continue
            issues.append(
                f"MISSING_PRIVATE {mod_name}.{attr} uses {name!r} but it is not in module globals"
            )
    return issues


def find_shim_star_imports() -> list[str]:
    """Compatibility shims should re-export privates, not star-import."""
    issues: list[str] = []
    for path, pkg in (
        (ROOT / "pothole_detector.py", "detector"),
        (ROOT / "db_utils.py", "db"),
        (ROOT / "routes" / "detection_service.py", "routes.detection"),
        (ROOT / "routes" / "survey_service.py", "routes.survey"),
        (ROOT / "routes" / "tracking_service.py", "routes.tracking"),
    ):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "import *" in text and "globals().update" not in text:
            issues.append(
                f"SHIM_STAR {path.relative_to(ROOT)} — prefer globals().update from {pkg}"
            )
    return issues


def required_detector_symbols() -> list[str]:
    """Hard checks for symbols known to be required during detection runs."""
    issues: list[str] = []
    required = {
        "detector.pipeline": [
            "_open_video_capture",
            "_open_video_writer",
            "_transcode_to_h264",
            "_infer_on_frame",
            "_draw_box",
            "_safe_upload",
            "_process_video_parallel",
            "_want_annotated_video",
            "_want_h264_transcode",
            "_resolve_frame_stride",
            "_resolve_write_stride",
            "_limit_native_threads",
            "_zip_folder",
            "_gps_for_frame_second",
            "apply_input_rotation",
            "normalize_rotation_deg",
            "load_gps_log",
            "DetectionRow",
        ],
        "detector.parallel": [
            "_open_video_capture",
            "_open_video_writer",
            "_infer_on_frame",
            "_draw_box",
            "_safe_upload",
            "_gps_for_frame_second",
            "DetectionRow",
            "Track",
        ],
        "pothole_detector": [
            "pothole_detector",
            "_open_video_capture",
            "apply_input_rotation",
            "normalize_rotation_deg",
        ],
        "db_utils": [
            "_get_conn",
            "is_db_configured",
            "init_db",
        ],
    }
    for mod_name, names in required.items():
        try:
            mod = importlib.import_module(mod_name)
        except Exception as e:
            issues.append(f"IMPORT_FAIL {mod_name}: {e}")
            continue
        for n in names:
            if not hasattr(mod, n):
                issues.append(f"REQUIRED_MISSING {mod_name}.{n}")
    return issues


def main() -> int:
    print("=== audit_private_imports ===")
    print(f"root: {ROOT}")
    issues: list[str] = []

    print("\n[1/4] Scanning runtime trees for star-imports…")
    star = find_star_imports()
    issues.extend(star)
    print(f"  star-import issues: {len(star)}")

    print("\n[2/4] Checking compatibility shims…")
    shim = find_shim_star_imports()
    issues.extend(shim)
    print(f"  shim issues: {len(shim)}")

    print("\n[3/4] Required detection/db symbols…")
    req = required_detector_symbols()
    issues.extend(req)
    print(f"  required-symbol issues: {len(req)}")

    print("\n[4/4] Bytecode scan of imported modules for missing _privates…")
    miss_count = 0
    for name in IMPORT_TARGETS:
        try:
            mod = importlib.import_module(name)
        except Exception as e:
            issues.append(f"IMPORT_FAIL {name}: {e}")
            print(f"  FAIL import {name}: {e}")
            continue
        found = find_missing_privates(mod)
        if found:
            miss_count += len(found)
            issues.extend(found)
            print(f"  {name}: {len(found)} missing")
        else:
            print(f"  {name}: ok")
    print(f"  missing-private issues: {miss_count}")

    print("\n=== summary ===")
    if not issues:
        print("OK — no star-import / private-name gaps found.")
        return 0

    print(f"FOUND {len(issues)} issue(s):")
    for line in issues:
        print(f"  - {line}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
