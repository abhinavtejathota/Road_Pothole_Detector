"""Split detection_service into routes/detection package."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
src = ROOT / "routes/detection_service.py"
bak = ROOT / "_detection_service_monolith.py"
text = src.read_text(encoding="utf-8")
if not bak.exists():
    bak.write_text(text, encoding="utf-8")
lines = bak.read_text(encoding="utf-8").splitlines(keepends=True)


def slice_lines(a: int, b: int | None = None) -> str:
    return "".join(lines[a - 1 : (b - 1 if b else None)])


cuts = [
    ("status", 1, 155),
    ("catalog", 155, 494),
    ("run", 494, 658),
    ("sessions", 658, None),
]
pkg = ROOT / "routes" / "detection"
pkg.mkdir(exist_ok=True)
prev = None
for i, (mod, a, b) in enumerate(cuts):
    body = slice_lines(a, b)
    if i == 0:
        content = body
    else:
        content = (
            f'"""detection.{mod}"""\n'
            "from __future__ import annotations\n"
            f"from routes.detection.{prev} import *  # noqa: F401,F403\n\n"
            + body
        )
    (pkg / f"{mod}.py").write_text(content, encoding="utf-8")
    print("wrote", mod, len(content.splitlines()))
    prev = mod

(pkg / "__init__.py").write_text(
    "from routes.detection.sessions import *  # noqa: F401,F403\n",
    encoding="utf-8",
)
src.write_text(
    '"""Compatibility shim — implementation lives in routes.detection."""\n'
    "from routes.detection import *  # noqa: F401,F403\n",
    encoding="utf-8",
)
print("done")
