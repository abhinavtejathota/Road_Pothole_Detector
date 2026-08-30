#!/usr/bin/env python3
"""
Save webcam or file frames into tools/ml/incoming/images/ for labeling.

After labeling (Roboflow / CVAT → YOLO txt), put labels in incoming/labels/
and run: python tools/ml/prepare_dataset.py
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2

_PKG = Path(__file__).resolve().parent
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from paths import INCOMING_DIR, ensure_dirs  # noqa: E402


def capture_webcam(count: int, interval_sec: float) -> list[Path]:
    ensure_dirs()
    out_dir = INCOMING_DIR / "images"
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        raise RuntimeError("Could not open webcam (index 0).")

    saved: list[Path] = []
    print(f"Saving {count} frames to {out_dir} — press q to stop early.")
    for i in range(count):
        ok, frame = cap.read()
        if not ok:
            break
        path = out_dir / f"capture_{int(time.time())}_{i:03d}.jpg"
        cv2.imwrite(str(path), frame)
        saved.append(path)
        print(" ", path.name)
        if interval_sec > 0:
            time.sleep(interval_sec)

    cap.release()
    return saved


def copy_files(paths: list[str]) -> list[Path]:
    ensure_dirs()
    out_dir = INCOMING_DIR / "images"
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for p in paths:
        src = Path(p)
        if not src.is_file():
            continue
        dest = out_dir / src.name
        dest.write_bytes(src.read_bytes())
        saved.append(dest)
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect images for bench training")
    parser.add_argument("--webcam", type=int, default=0, help="Capture N frames from webcam")
    parser.add_argument("--interval", type=float, default=0.5, help="Seconds between webcam frames")
    parser.add_argument("files", nargs="*", help="Image files to copy into incoming/")
    args = parser.parse_args()

    if args.webcam > 0:
        paths = capture_webcam(args.webcam, args.interval)
    elif args.files:
        paths = copy_files(args.files)
    else:
        parser.print_help()
        print("\nExample:")
        print("  python tools/ml/collect_images.py --webcam 30")
        print("  python tools/ml/collect_images.py photo1.jpg photo2.jpg")
        return

    print(f"\n{len(paths)} image(s) in {INCOMING_DIR / 'images'}")
    print("Next: label boxes in Roboflow → export YOLO → copy .txt to incoming/labels/")
    print("Then: python tools/ml/prepare_dataset.py && python tools/ml/train_rfdetr.py")


if __name__ == "__main__":
    main()
