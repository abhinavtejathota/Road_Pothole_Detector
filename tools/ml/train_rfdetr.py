#!/usr/bin/env python3
"""
Fine-tune RF-DETR on the pothole dataset. Outputs ONLY under tools/ml/.

  pip install -r tools/ml/requirements.txt
  python tools/ml/setup_train.py          # seed + download + prepare
  python tools/ml/train_rfdetr.py

Starts from tools/ml/models/rfdetr_small.pt when present.
Best weights → tools/ml/models/rfdetr_pothole.pth
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parent
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from models_registry import trainable_rfdetr_base  # noqa: E402
from paths import (  # noqa: E402
    BENCH_MODELS_DIR,
    RFDETR_RUN_DIR,
    dataset_root_for_rfdetr,
    ensure_dirs,
    env_float,
    env_int,
)

RFDETR_VARIANTS = {
    "small": "RFDETRSmall",
    "base": "RFDETRBase",
    "large": "RFDETRLarge",
}


def _load_rfdetr_class(name: str):
    import rfdetr

    cls_name = RFDETR_VARIANTS.get(name.lower(), "RFDETRSmall")
    return getattr(rfdetr, cls_name)


def main() -> None:
    default_base = trainable_rfdetr_base()
    parser = argparse.ArgumentParser(description="Train RF-DETR (bench only)")
    parser.add_argument("--variant", default="small", choices=list(RFDETR_VARIANTS))
    parser.add_argument(
        "--base",
        default=default_base,
        help=(
            "Local pretrained checkpoint (.pt/.pth). "
            f"Default: {default_base or 'package default (run seed_weights.py)'}"
        ),
    )
    parser.add_argument("--epochs", type=int, default=env_int("RFDETR_EPOCHS", 50))
    parser.add_argument("--batch-size", type=int, default=env_int("RFDETR_BATCH", 4))
    parser.add_argument("--grad-accum", type=int, default=env_int("RFDETR_GRAD_ACCUM", 4))
    parser.add_argument("--lr", type=float, default=env_float("RFDETR_LR", 1e-4))
    parser.add_argument("--resume", default=None, help="Path to checkpoint.pth to resume")
    args = parser.parse_args()

    ensure_dirs()
    dataset_dir = dataset_root_for_rfdetr()
    print(f"Dataset: {dataset_dir}")
    print(f"Output:  {RFDETR_RUN_DIR}")
    print(f"Variant: {args.variant}")
    print(f"Base:    {args.base or '(package default)'}")
    print("Weights → tools/ml/models/rfdetr_pothole.pth")

    try:
        RFDETR = _load_rfdetr_class(args.variant)
    except ImportError as e:
        raise SystemExit(
            'Install RF-DETR first: pip install -r tools/ml/requirements.txt'
        ) from e

    if args.base and Path(args.base).is_file():
        model = RFDETR(pretrain_weights=str(args.base))
    else:
        model = RFDETR()

    train_kw = dict(
        dataset_dir=str(dataset_dir),
        epochs=args.epochs,
        batch_size=args.batch_size,
        grad_accum_steps=args.grad_accum,
        lr=args.lr,
        output_dir=str(RFDETR_RUN_DIR),
        early_stopping=True,
        early_stopping_patience=15,
    )
    if args.resume:
        train_kw["resume"] = args.resume

    model.train(**train_kw)

    candidates = [
        RFDETR_RUN_DIR / "checkpoint_best_total.pth",
        RFDETR_RUN_DIR / "checkpoint_best_ema.pth",
        RFDETR_RUN_DIR / "checkpoint_best_regular.pth",
        RFDETR_RUN_DIR / "checkpoint.pth",
    ]
    best = next((p for p in candidates if p.is_file()), None)
    if not best:
        raise SystemExit(f"No checkpoint found in {RFDETR_RUN_DIR}")

    dest = BENCH_MODELS_DIR / "rfdetr_pothole.pth"
    shutil.copy2(best, dest)
    print(f"Saved bench weights: {dest}")


if __name__ == "__main__":
    main()
