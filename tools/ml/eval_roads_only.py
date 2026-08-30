#!/usr/bin/env python3
"""
Roads-only eval helpers for model_testing (pavement gate + defect boxes).

  python tools/ml/eval_roads_only.py --help

Does not touch production weights. Use after training pavement + pothole heads.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parent
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from purposes import get_purpose, list_purposes  # noqa: E402


def box_pavement_overlap(
    box_xyxy: tuple[float, float, float, float],
    mask,
    *,
    threshold: float = 0.70,
) -> tuple[float, bool]:
    """Fraction of box pixels that are pavement (mask truthy). Keep if ≥ threshold."""
    import numpy as np

    x1, y1, x2, y2 = [int(round(v)) for v in box_xyxy]
    h, w = mask.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return 0.0, False
    region = mask[y1:y2, x1:x2]
    frac = float(np.count_nonzero(region)) / float(region.size)
    return frac, frac >= threshold


def main() -> None:
    parser = argparse.ArgumentParser(description="Roads-only ensemble eval (model_testing)")
    parser.add_argument("--list-purposes", action="store_true")
    parser.add_argument(
        "--overlap",
        type=float,
        default=0.70,
        help="Min pavement mask density inside a defect box (default 0.70)",
    )
    args = parser.parse_args()
    if args.list_purposes:
        for p in list_purposes():
            print(f"{p.id}: {p.label}\n  {p.notes}\n")
        return

    ens = get_purpose("ensemble")
    print("Roads-only stack (model_testing only):")
    print(f"  {ens.role}")
    print(f"  Pavement overlap threshold: {args.overlap:.0%}")
    print("  Promotion gate: Precision_road≥92%  Recall_road≥88%  FPR_off-road≤2%")
    print()
    print("Train heads:")
    print("  python tools/ml/prepare_dataset.py --purpose pavement")
    print("  python tools/ml/train_yolo.py --purpose pavement")
    print("  python tools/ml/prepare_dataset.py --purpose pothole")
    print("  python tools/ml/train_yolo.py --purpose pothole")
    print("  python tools/ml/prepare_dataset.py --purpose crack")
    print("  python tools/ml/train_yolo.py --purpose crack")
    print("  python tools/ml/prepare_dataset.py --purpose rutting")
    print("  python tools/ml/train_yolo.py --purpose rutting")
    print("  python tools/ml/prepare_dataset.py --purpose waterlogging")
    print("  python tools/ml/train_yolo.py --purpose waterlogging")
    print()
    print("Wire mask filter in model_testing.inference / model-bench next;")
    print("do not copy to artifacts/models/ until the gate passes.")


if __name__ == "__main__":
    main()
