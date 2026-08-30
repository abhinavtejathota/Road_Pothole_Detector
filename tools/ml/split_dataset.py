#!/usr/bin/env python3
"""
Split a single train/ folder into train/ + valid/ for YOLO training.

Moves (or copies) ~ratio of image+label pairs from train/ into valid/.
Updates data.yaml when present.

  python tools/ml/split_dataset.py --root tools/ml/datasets/my-set
  python tools/ml/split_dataset.py --root D:/data/potholes --ratio 0.15 --copy
"""
from __future__ import annotations

import argparse
import random
import shutil
import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parent
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _list_images(images_dir: Path) -> list[Path]:
    if not images_dir.is_dir():
        return []
    return sorted(
        p for p in images_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )


def _update_data_yaml(root: Path, *, test_same_as_val: bool = True) -> None:
    yaml_path = root / "data.yaml"
    if not yaml_path.is_file():
        return
    lines = yaml_path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("val:"):
            out.append("val: valid/images")
            seen.add("val")
            continue
        if stripped.startswith("test:") and test_same_as_val:
            out.append("test: valid/images")
            seen.add("test")
            continue
        out.append(line)
    if "val" not in seen:
        out.append("val: valid/images")
    if test_same_as_val and "test" not in seen:
        out.append("test: valid/images")
    yaml_path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def split_dataset(
    root: Path,
    *,
    ratio: float = 0.2,
    seed: int = 42,
    copy: bool = False,
    dry_run: bool = False,
    update_yaml: bool = True,
) -> tuple[int, int]:
    """Return (train_count, valid_count) after split."""
    root = root.resolve()
    train_img = root / "train" / "images"
    train_lbl = root / "train" / "labels"
    valid_img = root / "valid" / "images"
    valid_lbl = root / "valid" / "labels"

    if not train_img.is_dir():
        raise FileNotFoundError(f"train/images not found: {train_img}")

    images = _list_images(train_img)
    if not images:
        raise SystemExit(f"No images in {train_img}")

    n_val = max(1, int(len(images) * ratio))
    if n_val >= len(images):
        raise SystemExit(
            f"ratio {ratio} would move all {len(images)} images to valid; "
            "use a smaller ratio or add more images."
        )

    random.seed(seed)
    picked = set(random.sample(images, n_val))

    if not dry_run:
        valid_img.mkdir(parents=True, exist_ok=True)
        valid_lbl.mkdir(parents=True, exist_ok=True)

    moved = 0
    for img in sorted(picked):
        lbl = train_lbl / f"{img.stem}.txt"
        dest_img = valid_img / img.name
        dest_lbl = valid_lbl / f"{img.stem}.txt"

        if dry_run:
            print(f"  {'copy' if copy else 'move'} {img.name}")
            moved += 1
            continue

        if copy:
            shutil.copy2(img, dest_img)
            if lbl.is_file():
                shutil.copy2(lbl, dest_lbl)
            else:
                dest_lbl.write_text("", encoding="utf-8")
        else:
            shutil.move(str(img), dest_img)
            if lbl.is_file():
                shutil.move(str(lbl), dest_lbl)
            else:
                dest_lbl.write_text("", encoding="utf-8")
        moved += 1

    if not dry_run and update_yaml:
        _update_data_yaml(root)

    train_count = len(_list_images(train_img))
    valid_count = len(_list_images(valid_img)) if valid_img.is_dir() else moved
    return train_count, valid_count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Split train/images+labels into train/ + valid/ for YOLO"
    )
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Dataset root (contains train/images, train/labels, optional data.yaml)",
    )
    parser.add_argument(
        "--ratio",
        type=float,
        default=0.2,
        help="Fraction of images to move to valid (default: 0.2)",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument(
        "--copy",
        action="store_true",
        help="Copy pairs instead of moving (keeps originals in train/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be split without moving files",
    )
    parser.add_argument(
        "--no-update-yaml",
        action="store_true",
        help="Do not patch data.yaml val:/test: paths",
    )
    args = parser.parse_args()

    if not 0 < args.ratio < 1:
        raise SystemExit("--ratio must be between 0 and 1 (exclusive)")

    train_n, valid_n = split_dataset(
        args.root,
        ratio=args.ratio,
        seed=args.seed,
        copy=args.copy,
        dry_run=args.dry_run,
        update_yaml=not args.no_update_yaml,
    )

    action = "Would split" if args.dry_run else "Split"
    print(f"{action} {args.root.resolve()}")
    print(f"  train: {train_n} images")
    print(f"  valid: {valid_n} images")
    if not args.dry_run and not args.no_update_yaml and (args.root / "data.yaml").is_file():
        print(f"  updated: {args.root / 'data.yaml'}")
    print("\nNext:")
    print(f'  python tools/ml/train_yolo.py --data "{(args.root / "data.yaml").resolve()}"')


if __name__ == "__main__":
    main()
