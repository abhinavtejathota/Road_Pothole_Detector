#!/usr/bin/env python3
"""
Build purpose-specific combined datasets under tools/ml/.

  python tools/ml/prepare_dataset.py
  python tools/ml/prepare_dataset.py --purpose pavement
  python tools/ml/prepare_dataset.py --purpose crack
  python tools/ml/prepare_dataset.py --purpose rutting
  python tools/ml/prepare_dataset.py --purpose waterlogging

Pothole (default) keeps legacy datasets/combined/.
Other purposes use datasets/<purpose>/combined/ + incoming/<purpose>/.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parent
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from paths import (  # noqa: E402
    PROJECT_ROOT,
    ensure_dirs,
    purpose_paths,
    resolve_roboflow_yaml,
)
from purposes import get_purpose  # noqa: E402

SPLITS = ("train", "valid", "test")


def _read_yaml_paths(yaml_path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in yaml_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        out[key.strip()] = val.strip()
    return out


def _resolve_split_dirs(yaml_path: Path, split: str) -> tuple[Path, Path | None]:
    data = _read_yaml_paths(yaml_path)
    key = "val" if split == "valid" else split
    rel = data.get(key) or data.get(split)
    if not rel:
        return Path(), None
    img_dir = (yaml_path.parent / rel).resolve()
    labels_dir = img_dir.parent.parent / "labels" if img_dir.name == "images" else img_dir.parent / "labels"
    if not labels_dir.is_dir():
        alt = img_dir.parent / "labels"
        labels_dir = alt if alt.is_dir() else None
    return img_dir, labels_dir


def _copy_pair(img: Path, labels_dir: Path | None, dest_images: Path, dest_labels: Path) -> None:
    dest_images.mkdir(parents=True, exist_ok=True)
    dest_labels.mkdir(parents=True, exist_ok=True)
    shutil.copy2(img, dest_images / img.name)
    if labels_dir:
        lbl = labels_dir / f"{img.stem}.txt"
        if lbl.is_file():
            shutil.copy2(lbl, dest_labels / lbl.name)


def _ingest_folder(
    images_dir: Path,
    labels_dir: Path | None,
    split: str,
    combined: Path,
) -> int:
    if not images_dir.is_dir():
        return 0
    dest_img = combined / split / "images"
    dest_lbl = combined / split / "labels"
    count = 0
    for img in sorted(images_dir.glob("*")):
        if img.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
            continue
        _copy_pair(img, labels_dir, dest_img, dest_lbl)
        count += 1
    return count


def _write_data_yaml(combined: Path, classes: list[str]) -> Path:
    yaml_path = combined / "data.yaml"
    names = ", ".join(repr(c) for c in classes)
    yaml_path.write_text(
        "\n".join([
            "path: .",
            "train: train/images",
            "val: valid/images",
            "test: test/images",
            f"nc: {len(classes)}",
            f"names: [{names}]",
            "",
        ]),
        encoding="utf-8",
    )
    return yaml_path


def prepare(purpose: str = "pothole") -> Path:
    spec = get_purpose(purpose)
    ensure_dirs(purpose)
    pp = purpose_paths(purpose)
    combined: Path = pp["combined"]
    incoming: Path = pp["incoming"]

    if combined.exists():
        shutil.rmtree(combined)
    combined.mkdir(parents=True, exist_ok=True)

    yaml_path = resolve_roboflow_yaml(purpose)
    total = 0
    if yaml_path is None and not (PROJECT_ROOT / "train").is_dir():
        print(
            f"[{purpose}] No Roboflow data.yaml yet — "
            f"using incoming/{purpose if purpose != 'pothole' else ''} only "
            "(or add a dataset in datasets_config.py)."
        )
    elif yaml_path:
        print(f"[{purpose}] Base dataset: {yaml_path}")
        for split in SPLITS:
            img_dir, lbl_dir = _resolve_split_dirs(yaml_path, split)
            n = _ingest_folder(img_dir, lbl_dir, split if split != "valid" else "valid", combined)
            # normalize valid folder name
            if split == "valid" and n == 0:
                img_dir, lbl_dir = _resolve_split_dirs(yaml_path, "val")
                n = _ingest_folder(img_dir, lbl_dir, "valid", combined)
            total += n
            print(f"  {split}: {n} images")
    else:
        # legacy root train/
        for split, folder in (("train", "train"), ("valid", "valid"), ("test", "test")):
            root = PROJECT_ROOT / folder
            img = root / "images" if (root / "images").is_dir() else root
            lbl = root / "labels" if (root / "labels").is_dir() else None
            n = _ingest_folder(img, lbl, split, combined)
            total += n

    # Incoming labels for this purpose
    inc_img = incoming / "images"
    inc_lbl = incoming / "labels"
    if inc_img.is_dir():
        n = _ingest_folder(inc_img, inc_lbl if inc_lbl.is_dir() else None, "train", combined)
        total += n
        print(f"  incoming→train: {n} images")

    classes = list(spec.classes)
    out_yaml = _write_data_yaml(combined, classes)
    print(f"[{purpose}] Combined → {combined} ({total} images)")
    print(f"  data.yaml → {out_yaml}")
    print(f"  classes: {classes}  task={spec.task}")
    return out_yaml


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare purpose-specific combined dataset")
    parser.add_argument(
        "--purpose",
        default="pothole",
        help="pavement | pothole | crack | rutting | waterlogging (default: pothole)",
    )
    args = parser.parse_args()
    prepare(args.purpose)


if __name__ == "__main__":
    main()
