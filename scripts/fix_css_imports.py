"""Fix CSS imports that were incorrectly inserted inside multi-line import { } blocks."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FILES = [
    "frontend/src/pages/Dashboard.jsx",
    "frontend/src/pages/Survey.jsx",
    "frontend/src/pages/Tracking.jsx",
    "frontend/src/pages/Detection.jsx",
    "frontend/src/pages/ModelBench.jsx",
    "frontend/src/pages/FieldCapture.jsx",
    "frontend/src/pages/FieldUpload.jsx",
    "frontend/src/pages/Complaints.jsx",
    "frontend/src/pages/Login.jsx",
    "frontend/src/components/Layout.jsx",
    "frontend/src/components/CoveredRibbonModal.jsx",
]


def fix(text: str) -> tuple[str, str | None]:
    m = re.search(r'import\s+"(\./[^"]+\.css)";?\s*\n?', text)
    if not m:
        return text, None
    css_path = m.group(1)
    # Remove all occurrences of the css import line
    cleaned = re.sub(rf'^\s*import\s+"{re.escape(css_path)}";?\s*\n', "", text, flags=re.M)
    # Also remove if it got jammed without newline after {
    cleaned = re.sub(rf'import\s+"{re.escape(css_path)}";?\s*', "", cleaned)
    imp = f'import "{css_path}";\n'
    if imp.strip() in cleaned:
        return cleaned, css_path
    # Insert after the last complete import statement
    # Find end of import region: lines starting with import or continuation of braces
    lines = cleaned.splitlines(keepends=True)
    last_import_end = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("import "):
            # consume until semicolon balance for braces
            buf = line
            j = i
            while ";" not in buf and j + 1 < len(lines):
                j += 1
                buf += lines[j]
            last_import_end = j + 1
            i = j + 1
            continue
        if line.startswith("export ") or line.startswith("function ") or line.startswith("const ") or line.startswith("class "):
            break
        i += 1
    lines.insert(last_import_end, imp)
    return "".join(lines), css_path


def main() -> None:
    for rel in FILES:
        p = ROOT / rel
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        new, css = fix(text)
        if new != text:
            p.write_text(new, encoding="utf-8")
            print("fixed", rel, "->", css)
        else:
            print("ok", rel)


if __name__ == "__main__":
    main()
