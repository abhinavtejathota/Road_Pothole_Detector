#!/usr/bin/env python3
"""Remove downloaded / generated data artifacts; keep all source code.

Targets what road_network / download_data / training / field-upload pipelines
produce under the repo (GIS extracts, datasets, caches, runtime uploads,
train/infer outputs). Does **not** touch application source, docs, migrations,
``data/ref/``, or placeholder ``.gitkeep`` / ``README.md`` files.

Usage:
  python scripts/clean_downloaded_data.py --dry-run
  python scripts/clean_downloaded_data.py --yes
  python scripts/clean_downloaded_data.py --yes --weights   # also delete .pt files
  python scripts/clean_downloaded_data.py --yes --only gis,datasets

Rebuild GIS after cleaning:
  python tools/gis/download_districts.py
  python tools/gis/download_state_roads.py --state both --all-roads
  python tools/gis/clip_roads_to_districts.py --state both
  python tools/gis/build_nh_overview.py
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

KEEP_NAMES = frozenset({".gitkeep", ".gitignore", "README.md", "README.rst", "README.txt"})
WEIGHT_SUFFIXES = frozenset({".pt", ".pth", ".onnx", ".weights"})
SKIP_DIR_NAMES = frozenset({".git", "venv", "vnev", ".venv", "node_modules", "__pycache__"})

CATEGORIES: dict[str, list[str]] = {
    # OSM / LGD downloads + per-district GeoJSON / snap indexes
    "gis": [
        "data/gis_states",
        "data/gis",
        "cache",
    ],
    # Local field / finalize / detect queues + locks / OTP cache
    "runtime": [
        "data/field_uploads",
        "data/finalize_queue/pending",
        "data/finalize_queue/running",
        "data/finalize_queue/done",
        "data/finalize_queue/failed",
        "data/detect_queue",
        "data/logs",
        "data/reporter_otps.json",
        "data/smartroad.pid",
        "data/gunicorn.pid",
        "data/.schema_init.lock",
    ],
    # Roboflow / merged training datasets + local label drops
    "datasets": [
        "tools/ml/datasets",
        "tools/ml/incoming/images",
        "tools/ml/incoming/labels",
        "Pothole-Detection--2",
    ],
    # Train / infer run trees (not weights)
    "outputs": [
        "runs",
        "outputs",
        "wandb",
        "tools/ml/runs",
    ],
    # Model weights (opt-in)
    "weights": [
        "artifacts/models",
        "tools/ml/models",
    ],
}

DEFAULT_CATEGORIES = ("gis", "runtime", "datasets", "outputs")


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def _is_keeper(path: Path) -> bool:
    return path.name in KEEP_NAMES


def _dir_has_keeper(path: Path, *, max_depth: int = 4) -> bool:
    """True if a placeholder README/.gitkeep exists within max_depth."""
    if not path.is_dir():
        return False
    root_depth = len(path.parts)
    for dirpath, dirnames, filenames in os.walk(path):
        depth = len(Path(dirpath).parts) - root_depth
        if any(name in KEEP_NAMES for name in filenames):
            return True
        if depth >= max_depth:
            dirnames.clear()
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
    return False


def _tree_stats(path: Path) -> tuple[int, int]:
    """Return (file_count, total_bytes) under path (fast path via os.scandir walk)."""
    if path.is_file():
        try:
            return 1, path.stat().st_size
        except OSError:
            return 1, 0
    n, b = 0, 0
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
        for name in filenames:
            n += 1
            try:
                b += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return n, b


def _human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _collect_targets(categories: list[str]) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for cat in categories:
        for rel in CATEGORIES[cat]:
            p = (ROOT / rel).resolve()
            if p in seen or not p.exists():
                continue
            seen.add(p)
            found.append((cat, p))

    if "gis" in categories:
        for p in ROOT.rglob("*.osm.pbf"):
            if any(part in SKIP_DIR_NAMES for part in p.parts):
                continue
            rp = p.resolve()
            if rp in seen:
                continue
            under = False
            for _, t in found:
                if t.is_dir():
                    try:
                        rp.relative_to(t)
                        under = True
                        break
                    except ValueError:
                        pass
            if under:
                continue
            seen.add(rp)
            found.append(("gis", rp))

    if "outputs" in categories:
        for p in ROOT.glob("smartroad_dl_*"):
            rp = p.resolve()
            if rp in seen or not p.is_dir():
                continue
            seen.add(rp)
            found.append(("outputs", rp))
    return found


def _plan_actions(cat: str, path: Path) -> list[tuple[str, Path]]:
    """
    Return actions as (op, path) where op is 'rmtree' or 'unlink'.
    Prefer whole-directory rmtree for speed.
    """
    actions: list[tuple[str, Path]] = []

    if path.is_file():
        if cat == "weights" and path.suffix.lower() not in WEIGHT_SUFFIXES:
            return []
        return [("unlink", path)]

    if not path.is_dir():
        return []

    if cat == "weights":
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
            for name in filenames:
                if name in KEEP_NAMES:
                    continue
                fp = Path(dirpath) / name
                if fp.suffix.lower() in WEIGHT_SUFFIXES:
                    actions.append(("unlink", fp))
        return actions

    # Keepers only appear as top-level placeholders in this repo
    # (e.g. tools/ml/datasets/README.md, data/field_uploads/.gitkeep).
    top_keepers = [c for c in path.iterdir() if c.is_file() and _is_keeper(c)]
    if not top_keepers and not _dir_has_keeper(path, max_depth=2):
        return [("rmtree", path)]

    for child in sorted(path.iterdir()):
        if _is_keeper(child):
            continue
        if child.is_file():
            actions.append(("unlink", child))
        elif child.is_dir():
            # Nested placeholders are rare; wipe child trees wholesale.
            actions.append(("rmtree", child))
    return actions


def clean(*, categories: list[str], dry_run: bool) -> int:
    targets = _collect_targets(categories)
    if not targets:
        print("Nothing to clean (no matching downloaded artifacts found).")
        return 0

    print(f"Repo root: {ROOT}")
    print(f"Categories: {', '.join(categories)}")
    print()

    all_actions: list[tuple[str, str, Path]] = []  # cat, op, path
    total_files = 0
    total_bytes = 0

    for cat, path in targets:
        actions = _plan_actions(cat, path)
        if not actions:
            print(f"  [{cat}] {_rel(path)}: (nothing to remove / only keepers)")
            continue
        # Stats: for rmtree use whole-tree stats; for unlink count each file
        cat_files = 0
        cat_bytes = 0
        for op, ap in actions:
            n, b = _tree_stats(ap) if op == "rmtree" else (1, ap.stat().st_size if ap.exists() else 0)
            cat_files += n
            cat_bytes += b
            all_actions.append((cat, op, ap))
        total_files += cat_files
        total_bytes += cat_bytes
        print(f"  [{cat}] {_rel(path)}: ~{cat_files} file(s), {_human_size(cat_bytes)}")

    print()
    print(f"Total: ~{total_files} file(s), {_human_size(total_bytes)} across {len(all_actions)} delete action(s)")

    if dry_run:
        print()
        print("[dry-run] sample actions:")
        for cat, op, ap in all_actions[:40]:
            print(f"  {op:7} {_rel(ap)}")
        if len(all_actions) > 40:
            print(f"  ... +{len(all_actions) - 40} more")
        print()
        print("No files deleted. Re-run with --yes to delete.")
        return 0

    deleted_actions = 0
    errors = 0
    for cat, op, ap in all_actions:
        try:
            if op == "rmtree":
                shutil.rmtree(ap)
            else:
                ap.unlink(missing_ok=True)
            deleted_actions += 1
        except OSError as e:
            print(f"  FAIL {op} {_rel(ap)}: {e}")
            errors += 1

    print()
    print(f"Completed {deleted_actions} delete action(s). Errors: {errors}.")
    print("Kept: source code, data/ref/, .gitkeep, README.md placeholders.")
    if "weights" not in categories:
        print("Note: model weights kept (pass --weights to also remove .pt/.pth/.onnx).")
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove downloaded GIS/datasets/runtime artifacts; keep source code.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted.")
    parser.add_argument("--yes", action="store_true", help="Required to actually delete.")
    parser.add_argument(
        "--weights",
        action="store_true",
        help="Also delete model weight files under artifacts/models and tools/ml/models.",
    )
    parser.add_argument(
        "--only",
        type=str,
        default="",
        help=f"Comma-separated categories. Choices: {', '.join(CATEGORIES)}. "
        f"Default: {', '.join(DEFAULT_CATEGORIES)}.",
    )
    parser.add_argument(
        "--list-categories",
        action="store_true",
        help="Print categories and target paths, then exit.",
    )
    args = parser.parse_args()

    if args.list_categories:
        for cat, paths in CATEGORIES.items():
            print(f"{cat}:")
            for p in paths:
                print(f"  {p}")
        return 0

    if args.only.strip():
        categories = [c.strip().lower() for c in args.only.split(",") if c.strip()]
    else:
        categories = list(DEFAULT_CATEGORIES)
    if args.weights and "weights" not in categories:
        categories.append("weights")

    unknown = [c for c in categories if c not in CATEGORIES]
    if unknown:
        print(f"Unknown categor(ies): {', '.join(unknown)}", file=sys.stderr)
        print(f"Valid: {', '.join(CATEGORIES)}", file=sys.stderr)
        return 2

    if not args.dry_run and not args.yes:
        print("Refusing to delete without --yes (or use --dry-run / --list-categories).")
        return 1

    return clean(categories=categories, dry_run=bool(args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
