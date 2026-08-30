#!/usr/bin/env python3
"""
One-shot setup for model_testing training:

  1) Seed local .pt / .pth weights (YOLO + RF-DETR Small + fine-tuned copy)
  2) Download Roboflow base dataset into tools/ml/datasets/
  3) Merge with incoming/ labels → datasets/combined/

  python tools/ml/setup_train.py
  python tools/ml/setup_train.py --skip-download   # weights only
  python tools/ml/setup_train.py --skip-seed
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parent


def _run(script: str, extra: list[str] | None = None) -> None:
    cmd = [sys.executable, str(_PKG / script), *(extra or [])]
    print(f"\n>>> {' '.join(cmd)}\n")
    subprocess.check_call(cmd, cwd=str(_PKG.parent))


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed weights + download data + prepare dataset")
    parser.add_argument("--skip-seed", action="store_true")
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--skip-prepare", action="store_true")
    parser.add_argument("--force-seed", action="store_true", help="Re-download weight files")
    args = parser.parse_args()

    if not args.skip_seed:
        extra = ["--force"] if args.force_seed else []
        _run("seed_weights.py", extra)

    if not args.skip_download:
        try:
            _run("download_data.py")
        except subprocess.CalledProcessError as e:
            print(
                "\n[warn] Roboflow download failed (need ROBOFLOW_API_KEY in .env). "
                "You can retry: python tools/ml/download_data.py"
            )
            raise SystemExit(e.returncode) from e

    if not args.skip_prepare:
        _run("prepare_dataset.py")

    print("\nSetup complete. Train with:")
    print("  python tools/ml/train_yolo.py")
    print("  python tools/ml/train_rfdetr.py")
    print("Then open portal → Model testing.")


if __name__ == "__main__":
    main()
