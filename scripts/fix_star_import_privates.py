"""Fix private-name re-exports without circular package imports."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def rewrite_chain(pkg: str, modules: list[str]) -> None:
    base = ROOT / "routes" / pkg
    for i, mod in enumerate(modules):
        if i == 0:
            continue
        prev = modules[i - 1]
        path = base / f"{mod}.py"
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        # Drop our previous preamble until real body (first def/class/assign after imports we control)
        j = 0
        # Remove leading docstring + future + any reexport preamble
        while j < len(lines):
            l = lines[j]
            if j == 0 and (l.startswith('"""') or l.startswith("'''")):
                quote = '"""' if l.startswith('"""') else "'''"
                if l.count(quote) >= 2:
                    j += 1
                    continue
                j += 1
                while j < len(lines) and quote not in lines[j]:
                    j += 1
                j += 1
                continue
            if l.startswith("from __future__"):
                j += 1
                continue
            if l.startswith(f"from routes.{pkg}") or l.startswith(f"import routes.{pkg}"):
                j += 1
                continue
            if l.startswith("globals().update"):
                j += 1
                continue
            if l.strip() == "":
                j += 1
                continue
            break
        body = "".join(lines[j:])
        new = (
            f'"""{pkg}.{mod} — extends {prev} (includes private _names)."""\n'
            "from __future__ import annotations\n\n"
            f"import routes.{pkg}.{prev} as _{prev}\n\n"
            f"globals().update({{k: v for k, v in vars(_{prev}).items() if not k.startswith('__')}})\n\n"
            + body
        )
        path.write_text(new, encoding="utf-8")
        print("rewrote", path.relative_to(ROOT))

    # Package init: import modules in order (side-effect chain), re-export last
    parts = [
        f'"""{pkg} service package — full re-export including private helpers."""\n',
        "from __future__ import annotations\n\n",
    ]
    for m in modules:
        parts.append(f"import routes.{pkg}.{m} as _{m}\n")
    parts.append("\n")
    last = modules[-1]
    parts.append(
        f"globals().update({{k: v for k, v in vars(_{last}).items() if not k.startswith('__')}})\n"
    )
    (base / "__init__.py").write_text("".join(parts), encoding="utf-8")
    print("rewrote", f"routes/{pkg}/__init__.py")


def rewrite_db_init() -> None:
    mods = [
        "host", "connection", "schema", "sessions", "users", "vendors",
        "work_orders", "validation", "warranty", "dashboard", "gis_state",
    ]
    parts = [
        '"""PostgreSQL helpers — re-export including private names like _get_conn."""\n',
        "from __future__ import annotations\n\n",
    ]
    for m in mods:
        parts.append(f"import db.{m} as _{m}\n")
    parts.append("\n")
    for m in mods:
        parts.append(
            f"globals().update({{k: v for k, v in vars(_{m}).items() if not k.startswith('__')}})\n"
        )
    (ROOT / "db" / "__init__.py").write_text("".join(parts), encoding="utf-8")
    print("rewrote db/__init__.py")


def rewrite_detector_modules() -> None:
    """Replace star-imports between detector modules with private-aware reexport."""
    from textwrap import dedent

    util = ROOT / "detector" / "_importutil.py"
    if not util.is_file():
        util.write_text(
            dedent(
                '''\
                """Import helpers for the detector package.

                ``from module import *`` intentionally skips names starting with ``_``.
                Use ``reexport(module)`` instead — it pulls public *and* single-underscore names.
                """
                from __future__ import annotations

                from types import ModuleType


                def reexport(mod: ModuleType, dest: dict) -> None:
                    """Copy names from ``mod`` into ``dest`` (typically ``globals()``), including ``_foo``."""
                    for key, value in vars(mod).items():
                        if key.startswith("__"):
                            continue
                        dest[key] = value
                '''
            ),
            encoding="utf-8",
        )
        print("wrote detector/_importutil.py")

    # types ← gps
    types_path = ROOT / "detector" / "types.py"
    text = types_path.read_text(encoding="utf-8")
    text2 = text
    for bad in (
        "from detector.gps import *  # noqa: F401,F403\n",
        "from detector.gps import *\n",
    ):
        text2 = text2.replace(bad, "")
    if "from detector._importutil import reexport" not in text2:
        # Insert after the last plain import block before DetectionRow / first def
        needle = "# ============================================================\n# Detection types"
        block = (
            "from detector._importutil import reexport\n"
            "import detector.gps as _gps\n\n"
            "reexport(_gps, globals())\n\n"
        )
        if needle in text2:
            text2 = text2.replace(needle, block + needle, 1)
        else:
            text2 = block + text2
    if text2 != text:
        types_path.write_text(text2, encoding="utf-8")
        print("rewrote detector/types.py imports")

    # parallel ← types, track, video_io, gps
    par_path = ROOT / "detector" / "parallel.py"
    text = par_path.read_text(encoding="utf-8")
    text2 = text
    for bad in (
        "from detector.types import *  # noqa: F401,F403\n",
        "from detector.track import *  # noqa: F401,F403\n",
        "from detector.video_io import *  # noqa: F401,F403\n",
        "from detector.gps import *  # noqa: F401,F403\n",
        "from detector.types import *\n",
        "from detector.track import *\n",
        "from detector.video_io import *\n",
        "from detector.gps import *\n",
    ):
        text2 = text2.replace(bad, "")
    if "from detector._importutil import reexport" not in text2:
        needle = "# ============================================================\n# Parallel chunked video processing"
        block = (
            "from detector._importutil import reexport\n"
            "import detector.types as _types\n"
            "import detector.track as _track\n"
            "import detector.video_io as _video_io\n"
            "import detector.gps as _gps\n\n"
            "reexport(_types, globals())\n"
            "reexport(_track, globals())\n"
            "reexport(_video_io, globals())\n"
            "reexport(_gps, globals())\n\n"
        )
        if needle in text2:
            text2 = text2.replace(needle, block + needle, 1)
        else:
            text2 = block + text2
    if text2 != text:
        par_path.write_text(text2, encoding="utf-8")
        print("rewrote detector/parallel.py imports")

    # pipeline ← gps, types, track, video_io, parallel
    pipe_path = ROOT / "detector" / "pipeline.py"
    text = pipe_path.read_text(encoding="utf-8")
    text2 = text
    for bad in (
        "from detector.gps import *  # noqa: F401,F403\n",
        "from detector.types import *  # noqa: F401,F403\n",
        "from detector.track import *  # noqa: F401,F403\n",
        "from detector.video_io import *  # noqa: F401,F403\n",
        "from detector.parallel import *  # noqa: F401,F403\n",
        "from detector.gps import *\n",
        "from detector.types import *\n",
        "from detector.track import *\n",
        "from detector.video_io import *\n",
        "from detector.parallel import *\n",
    ):
        text2 = text2.replace(bad, "")
    if "from detector._importutil import reexport" not in text2:
        needle = "def pothole_detector("
        block = (
            "from detector._importutil import reexport\n"
            "import detector.gps as _gps\n"
            "import detector.types as _types\n"
            "import detector.track as _track\n"
            "import detector.video_io as _video_io\n"
            "import detector.parallel as _parallel\n\n"
            "reexport(_gps, globals())\n"
            "reexport(_types, globals())\n"
            "reexport(_track, globals())\n"
            "reexport(_video_io, globals())\n"
            "reexport(_parallel, globals())\n\n"
        )
        if needle in text2:
            text2 = text2.replace(needle, block + needle, 1)
        else:
            text2 = block + text2
    if text2 != text:
        pipe_path.write_text(text2, encoding="utf-8")
        print("rewrote detector/pipeline.py imports")


def rewrite_detector_init() -> None:
    mods = ["gps", "types", "track", "video_io", "parallel", "pipeline"]
    parts = [
        '"""Detector package — full re-export."""\n',
        "from __future__ import annotations\n\n",
    ]
    for m in mods:
        parts.append(f"import detector.{m} as _{m}\n")
    parts.append("from detector._importutil import reexport\n\n")
    for m in mods:
        parts.append(f"reexport(_{m}, globals())\n")
    (ROOT / "detector" / "__init__.py").write_text("".join(parts), encoding="utf-8")
    print("rewrote detector/__init__.py")


if __name__ == "__main__":
    rewrite_chain("survey", ["state", "geocode", "geometry", "progress", "assignments"])
    rewrite_chain("tracking", ["store", "trail", "coverage", "capture", "admin"])
    rewrite_chain("detection", ["status", "catalog", "run", "sessions"])
    rewrite_db_init()
    rewrite_detector_modules()
    rewrite_detector_init()
    print("done")
