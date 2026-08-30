#!/usr/bin/env python3
"""
Remove image+label pairs with no pothole boxes (empty .txt).

Works on YOLO folders that already have images/ + labels/ (no XML needed).

  python tools/ml/prune_empty_labels.py --root "D:/data/my-set" --dry-run
  python tools/ml/prune_empty_labels.py --root "D:/data/my-set"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parent
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _resolve_dirs(root: Path) -> tuple[Path, Path]:
    root = root.resolve()
    candidates = [
        (root / "images", root / "labels"),
        (root / "train" / "images", root / "train" / "labels"),
    ]
    for img_dir, lbl_dir in candidates:
        if img_dir.is_dir() and lbl_dir.is_dir():
            return img_dir, lbl_dir
    raise FileNotFoundError(
        f"Could not find images/ + labels/ under {root} "
        "(expected root/images+labels or root/train/images+labels)."
    )


def _has_boxes(label_path: Path) -> bool:
    if not label_path.is_file():
        return False
    for line in label_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            return True
    return False


def prune_empty_labels(
    root: Path,
    *,
    dry_run: bool = False,
    delete_orphan_labels: bool = True,
) -> dict[str, int]:
    img_dir, lbl_dir = _resolve_dirs(root)
    images = sorted(
        p for p in img_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )

    kept = removed = no_label = 0
    for img in images:
        lbl = lbl_dir / f"{img.stem}.txt"
        if _has_boxes(lbl):
            kept += 1
            continue
        no_label += int(not lbl.is_file())
        removed += 1
        if dry_run:
            print(f"  remove {img.name}")
            continue
        img.unlink(missing_ok=True)
        lbl.unlink(missing_ok=True)

    orphan_labels = 0
    if delete_orphan_labels:
        image_stems = {p.stem for p in images}
        for lbl in sorted(lbl_dir.glob("*.txt")):
            if lbl.stem in image_stems:
                continue
            if not _has_boxes(lbl):
                orphan_labels += 1
                if not dry_run:
                    lbl.unlink(missing_ok=True)

    return {
        "images_before": len(images),
        "kept": kept,
        "removed": removed,
        "missing_label_file": no_label,
        "orphan_empty_labels": orphan_labels,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete images (and labels) with empty YOLO txt — pothole-only cleanup"
    )
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Dataset root (images/ + labels/, or train/images + train/labels)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be removed without deleting files",
    )
    parser.add_argument(
        "--keep-orphan-labels",
        action="store_true",
        help="Do not delete empty .txt files that have no matching image",
    )
    args = parser.parse_args()

    stats = prune_empty_labels(
        args.root,
        dry_run=args.dry_run,
        delete_orphan_labels=not args.keep_orphan_labels,
    )

    action = "Would remove" if args.dry_run else "Removed"
    print(f"{action} empty pairs from {args.root.resolve()}")
    print(f"  images before: {stats['images_before']}")
    print(f"  kept (has boxes): {stats['kept']}")
    print(f"  removed (no pothole): {stats['removed']}")
    if stats["missing_label_file"]:
        print(f"  removed with no .txt file: {stats['missing_label_file']}")
    if stats["orphan_empty_labels"]:
        print(f"  orphan empty labels: {stats['orphan_empty_labels']}")

    if not args.dry_run and stats["kept"] > 0:
        print("\nNext:")
        print(
            f'  python tools/ml/split_dataset.py --root "{args.root.resolve()}" --ratio 0.15 --copy'
        )


if __name__ == "__main__":
    main()
