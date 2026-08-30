#!/usr/bin/env python3
"""
Convert Pascal VOC (.xml) annotations → YOLO (.txt) labels.

Example (your India RDD-style set):

  python tools/ml/voc_xml_to_yolo.py ^
    --images "C:/Users/akshay teja thota/Downloads/potholes/train/India/images" ^
    --xmls   "C:/Users/akshay teja thota/Downloads/potholes/train/India/annotations/xmls" ^
    --out    tools/ml/datasets/roboflow/india-potholes

By default only **pothole-like** classes are kept (D40, pothole, … → class 0).
Use --all-classes to keep every VOC class as separate YOLO ids.
"""
from __future__ import annotations

import argparse
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

# RDD / common aliases → treat as single "pothole" class (0)
POTHOLE_NAMES = {
    "d40",
    "pothole",
    "potholes",
    "pot_hole",
    "pot-hole",
    "alligator",  # sometimes mislabeled; drop if you want strict D40 only
}


def _text(el: ET.Element | None, default: str = "") -> str:
    if el is None or el.text is None:
        return default
    return el.text.strip()


def voc_box_to_yolo(
    xmin: float, ymin: float, xmax: float, ymax: float, w: float, h: float
) -> tuple[float, float, float, float]:
    bw = max(0.0, xmax - xmin)
    bh = max(0.0, ymax - ymin)
    xc = xmin + bw / 2.0
    yc = ymin + bh / 2.0
    return xc / w, yc / h, bw / w, bh / h


def convert_one(
    xml_path: Path,
    *,
    class_map: dict[str, int],
    pothole_only: bool,
) -> tuple[list[str], dict[str, int]]:
    """Returns (yolo_lines, name_counts_seen)."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    size = root.find("size")
    w = float(_text(size.find("width") if size is not None else None, "0"))
    h = float(_text(size.find("height") if size is not None else None, "0"))
    if w <= 0 or h <= 0:
        return [], {}

    lines: list[str] = []
    seen: dict[str, int] = {}
    for obj in root.findall("object"):
        name = _text(obj.find("name")).lower()
        if not name:
            continue
        seen[name] = seen.get(name, 0) + 1
        box = obj.find("bndbox")
        if box is None:
            continue
        xmin = float(_text(box.find("xmin"), "0"))
        ymin = float(_text(box.find("ymin"), "0"))
        xmax = float(_text(box.find("xmax"), "0"))
        ymax = float(_text(box.find("ymax"), "0"))
        if xmax <= xmin or ymax <= ymin:
            continue

        if pothole_only:
            if name not in POTHOLE_NAMES:
                continue
            cls_id = 0
        else:
            if name not in class_map:
                class_map[name] = len(class_map)
            cls_id = class_map[name]

        xc, yc, bw, bh = voc_box_to_yolo(xmin, ymin, xmax, ymax, w, h)
        # clamp
        xc, yc = min(1.0, max(0.0, xc)), min(1.0, max(0.0, yc))
        bw, bh = min(1.0, max(0.0, bw)), min(1.0, max(0.0, bh))
        lines.append(f"{cls_id} {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}")

    return lines, seen


def main() -> None:
    parser = argparse.ArgumentParser(description="VOC XML → YOLO txt")
    parser.add_argument("--images", required=True, type=Path, help="Folder of .jpg/.png images")
    parser.add_argument("--xmls", required=True, type=Path, help="Folder of Pascal VOC .xml files")
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Output dataset root (creates images/ + labels/ + data.yaml)",
    )
    parser.add_argument(
        "--all-classes",
        action="store_true",
        help="Keep every VOC class (not just pothole/D40). Default: pothole-only → class 0",
    )
    parser.add_argument(
        "--copy-images",
        action="store_true",
        help="Copy images into out/images (default: symlink when possible, else copy)",
    )
    parser.add_argument(
        "--skip-empty",
        action="store_true",
        help="Skip images with no kept boxes (default: write empty .txt so negatives are kept)",
    )
    args = parser.parse_args()

    images_dir = args.images.resolve()
    xmls_dir = args.xmls.resolve()
    out = args.out.resolve()
    out_img = out / "images"
    out_lbl = out / "labels"
    out_img.mkdir(parents=True, exist_ok=True)
    out_lbl.mkdir(parents=True, exist_ok=True)

    if not images_dir.is_dir():
        raise SystemExit(f"--images not found: {images_dir}")
    if not xmls_dir.is_dir():
        raise SystemExit(f"--xmls not found: {xmls_dir}")

    pothole_only = not args.all_classes
    class_map: dict[str, int] = {}
    name_totals: dict[str, int] = {}
    n_img = n_with = n_boxes = n_skip = 0

    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    for img in sorted(images_dir.iterdir()):
        if img.suffix.lower() not in exts:
            continue
        n_img += 1
        xml_path = xmls_dir / f"{img.stem}.xml"
        if not xml_path.is_file():
            n_skip += 1
            continue

        lines, seen = convert_one(xml_path, class_map=class_map, pothole_only=pothole_only)
        for k, v in seen.items():
            name_totals[k] = name_totals.get(k, 0) + v

        if args.skip_empty and not lines:
            continue

        # link/copy image
        dest_img = out_img / img.name
        if not dest_img.exists():
            try:
                if args.copy_images:
                    shutil.copy2(img, dest_img)
                else:
                    dest_img.symlink_to(img)
            except OSError:
                shutil.copy2(img, dest_img)

        (out_lbl / f"{img.stem}.txt").write_text(
            ("\n".join(lines) + ("\n" if lines else "")),
            encoding="utf-8",
        )
        if lines:
            n_with += 1
            n_boxes += len(lines)

    # YOLO data.yaml (single split — use as train; set val=train for quick bench)
    if pothole_only:
        names = ["pothole"]
        nc = 1
    else:
        # invert class_map
        inv = [""] * len(class_map)
        for name, i in class_map.items():
            inv[i] = name
        names = inv
        nc = len(names)

    yaml_text = (
        f"path: {out.as_posix()}\n"
        f"train: images\n"
        f"val: images\n"
        f"test: images\n"
        f"nc: {nc}\n"
        f"names: {names}\n"
    )
    (out / "data.yaml").write_text(yaml_text, encoding="utf-8")

    print(f"Images scanned:     {n_img}")
    print(f"XML missing:        {n_skip}")
    print(f"Images with boxes:  {n_with}")
    print(f"Boxes written:      {n_boxes}")
    print(f"VOC names seen:     {dict(sorted(name_totals.items(), key=lambda x: -x[1]))}")
    print(f"YOLO classes:       {names}")
    print(f"Output:             {out}")
    print(f"data.yaml:          {out / 'data.yaml'}")
    print()
    print("Train with:")
    print(
        f'  python tools/ml/train_yolo.py --base tools/ml/models/yolo12l.pt '
        f'--data "{(out / "data.yaml").as_posix()}"'
    )


if __name__ == "__main__":
    main()
