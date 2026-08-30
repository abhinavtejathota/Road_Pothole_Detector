#!/usr/bin/env python3
"""
Download Roboflow datasets into tools/ml/datasets/roboflow/.

  # Uses ROBOFLOW_API_KEY from project .env (or tools/ml/.env)
  python tools/ml/download_data.py
  python tools/ml/download_data.py --list
  python tools/ml/download_data.py --dataset aegis-pothole-v2

Edit links in tools/ml/datasets_config.py.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parent
_ROOT = _PKG.parent
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from datasets_config import (  # noqa: E402
    ROBOFLOW_DATASETS,
    ROBOFLOW_LOCAL_DIRNAME,
    get_dataset,
)
from paths import ensure_dirs, purpose_paths  # noqa: E402
from purposes import list_purposes  # noqa: E402


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(_ROOT / ".env")
    load_dotenv(_PKG / ".env")


def list_datasets() -> None:
    print("Configured Roboflow datasets (edit datasets_config.py):\n")
    for d in ROBOFLOW_DATASETS:
        flag = " [default]" if d.get("default") else ""
        print(f"  {d['id']}{flag}  purpose={d.get('purpose', 'pothole')}")
        print(f"    {d['name']}")
        print(f"    {d['url']}")
        print(f"    format={d['format']}  classes={d.get('classes')}")
        if d.get("notes"):
            print(f"    notes: {d['notes']}")
        print()
    print("Purposes:")
    for p in list_purposes():
        print(f"  {p.id}: {p.label}")


def download(dataset_id: str | None = None, purpose: str = "pothole") -> Path:
    _load_env()
    ensure_dirs(purpose)
    spec = get_dataset(dataset_id, purpose=purpose)
    purpose = str(spec.get("purpose") or purpose)

    key = os.getenv("ROBOFLOW_API_KEY")
    if not key:
        raise RuntimeError(
            "ROBOFLOW_API_KEY not found.\n"
            "Add to project .env or tools/ml/.env:\n"
            "  ROBOFLOW_API_KEY=rf_...\n"
        )

    dest_root = purpose_paths(purpose)["roboflow"]
    dest_root.mkdir(parents=True, exist_ok=True)

    print(f"Purpose: {purpose}")
    print(f"Dataset: {spec['name']}")
    print(f"URL:     {spec['url']}")
    print(f"Format:  {spec['format']}")
    print(f"Into:    {dest_root}")

    from roboflow import download_dataset

    prev = Path.cwd()
    try:
        os.chdir(dest_root)
        dataset = download_dataset(spec["url"], spec["format"])
        location = Path(dataset.location).resolve()
    finally:
        os.chdir(prev)

    # Normalize default folder name for prepare_dataset / paths (pothole only)
    if purpose == "pothole":
        canonical = dest_root / ROBOFLOW_LOCAL_DIRNAME
        if location != canonical:
            if canonical.exists():
                shutil.rmtree(canonical)
            if location.is_dir() and location.parent == dest_root:
                location.rename(canonical)
                location = canonical
            else:
                shutil.copytree(location, canonical, dirs_exist_ok=True)
                location = canonical

    yaml_path = location / "data.yaml"
    print(f"Downloaded to: {location}")
    if yaml_path.is_file():
        print(f"data.yaml:     {yaml_path}")
    print("\nNext:")
    print(f"  python tools/ml/prepare_dataset.py --purpose {purpose}")
    print(f"  python tools/ml/train_yolo.py --purpose {purpose}")
    return location


def main() -> None:
    parser = argparse.ArgumentParser(description="Download Roboflow data into tools/ml/")
    parser.add_argument("--list", action="store_true", help="List configured dataset links")
    parser.add_argument("--dataset", default=None, help="Dataset id from datasets_config.py")
    parser.add_argument(
        "--purpose",
        default="pothole",
        help="pavement | pothole | crack | rutting | waterlogging — selects default dataset + download folder",
    )
    args = parser.parse_args()

    if args.list:
        list_datasets()
        return
    download(args.dataset, purpose=args.purpose)


if __name__ == "__main__":
    main()
