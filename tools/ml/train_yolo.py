#!/usr/bin/env python3
"""
Fine-tune YOLOv12 on the pothole dataset. Outputs ONLY under tools/ml/.
Does NOT touch artifacts/models/smartroad_ap.pt.

  python tools/ml/seed_weights.py
  python tools/ml/prepare_dataset.py
  python tools/ml/train_yolo.py --base tools/ml/models/yolo12m.pt

Each base gets its own run folder and weight file, e.g.:
  runs/yolo/yolo12m_trained/weights/best.pt
  models/yolo12m_trained.pt
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parent
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from models_registry import trainable_yolo_base  # noqa: E402
from paths import (  # noqa: E402
    ensure_dirs,
    env_int,
    purpose_paths,
    dataset_yaml_path,
)
from purposes import get_purpose, list_purposes  # noqa: E402


def run_name_from_base(base: str, override: str | None = None) -> str:
    """Stable run folder / output stem: <base_stem>_trained."""
    if override:
        name = re.sub(r"[^\w\-]+", "_", override.strip()).strip("_")
        if name:
            return name
    stem = Path(base).stem
    # Ultralytics yaml names like yolov12s.yaml → yolov12s
    if stem.endswith("_trained"):
        return stem
    return f"{stem}_trained"


def main() -> None:
    default_base = trainable_yolo_base()
    parser = argparse.ArgumentParser(
        description="Train YOLO bench model per purpose (pavement|pothole|crack|rutting|waterlogging)"
    )
    parser.add_argument(
        "--purpose",
        default="pothole",
        help="Training purpose: pavement | pothole | crack | rutting | waterlogging (default: pothole)",
    )
    parser.add_argument(
        "--list-purposes",
        action="store_true",
        help="List purposes and exit",
    )
    parser.add_argument(
        "--base",
        default=None,
        help=(
            "Starting weights or Ultralytics yaml. "
            f"Default: purpose default or {default_base}"
        ),
    )
    parser.add_argument(
        "--name",
        default=None,
        help=(
            "Run folder and output models/<purpose>/<name>.pt. "
            "Default: purpose output_stem or <base_stem>_trained."
        ),
    )
    parser.add_argument(
        "--data",
        default=None,
        help="Path to YOLO data.yaml (default: purpose combined dataset)",
    )
    parser.add_argument("--epochs", type=int, default=env_int("YOLO_EPOCHS", 80))
    parser.add_argument("--imgsz", type=int, default=env_int("YOLO_IMGSZ", 640))
    parser.add_argument("--batch", type=int, default=env_int("YOLO_BATCH", 16))
    parser.add_argument(
        "--device",
        default=(__import__("os").getenv("YOLO_DEVICE") or "0").strip() or "0",
        help="Ultralytics device: 0 / cuda:0 / cpu (default YOLO_DEVICE or 0)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=env_int("YOLO_WORKERS", 0),
        help="DataLoader CPU workers (0=main process only; cannot run on GPU)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from last.pt in this run's folder (same --base/--name)",
    )
    args = parser.parse_args()

    # Cap BLAS/OpenCV/torch CPU threads before Ultralytics spawns workers.
    import os
    _cpu_n = max(1, min(4, int(os.getenv("POTHOLE_CPU_THREADS") or os.getenv("YOLO_CPU_THREADS") or "3")))
    for _k in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        os.environ[_k] = str(_cpu_n)
    try:
        import torch
        torch.set_num_threads(_cpu_n)
        if hasattr(torch, "set_num_interop_threads"):
            torch.set_num_interop_threads(1)
        if str(args.device).lower() not in ("cpu",) and not torch.cuda.is_available():
            print("WARN: CUDA not available — training will use CPU (slow, high CPU).")
            print("      Install CUDA torch on the server, or pass --device cpu explicitly.")
        elif str(args.device).lower() not in ("cpu",):
            print(f"Training on GPU device={args.device} (CUDA ok); CPU threads={_cpu_n} workers={args.workers}")
    except Exception as e:
        print(f"WARN: torch thread/device check failed: {e}")

    if args.list_purposes:
        for p in list_purposes():
            print(f"  {p.id:10}  train={p.train_enabled}  task={p.task}  {p.label}")
            print(f"             {p.role}")
        return

    purpose = get_purpose(args.purpose)
    if not purpose.train_enabled:
        raise SystemExit(
            f"Purpose {purpose.id!r} is eval-only (ensemble). "
            "Train pavement / pothole / crack / rutting / waterlogging heads separately."
        )

    ensure_dirs(purpose.id)
    pp = purpose_paths(purpose.id)
    base = args.base or purpose.default_base or default_base
    run_name = args.name or purpose.output_stem or run_name_from_base(base, None)
    if not args.name and args.base:
        run_name = run_name_from_base(base, None)
    run_dir = pp["runs_yolo"] / run_name
    dest = pp["models"] / f"{run_name}.pt"

    if args.data:
        data_yaml = Path(args.data)
        if not data_yaml.is_file():
            raise SystemExit(
                f"--data not found: {data_yaml}\n"
                "Pass a YOLO data.yaml (folder with train/val images + labels)."
            )
        data_yaml = data_yaml.resolve()
    else:
        data_yaml = dataset_yaml_path(purpose.id)

    print(f"Purpose:   {purpose.id} ({purpose.task}) — {purpose.role}")
    print(f"Classes:   {list(purpose.classes)}")
    print(f"data.yaml: {data_yaml}")
    print(f"Base:      {base}")
    print(f"Run name:  {run_name}")
    print(f"Runs dir:  {run_dir}")
    print(f"Output:    {dest}")
    print("Production artifacts/models/ is NOT modified.")

    from ultralytics import YOLO

    last_pt = run_dir / "weights" / "last.pt"
    if args.resume and last_pt.is_file():
        print(f"Resuming:  {last_pt}")
        model = YOLO(str(last_pt))
    else:
        if args.resume:
            print(f"No last.pt at {last_pt} — starting fresh from --base")
        model = YOLO(base)

    results = model.train(
        data=str(data_yaml),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        workers=max(0, int(args.workers)),
        patience=20,
        project=str(pp["runs_yolo"]),
        name=run_name,
        exist_ok=True,
        optimizer="AdamW",
        lr0=0.001,
        mosaic=1.0,
        mixup=0.1,
        amp=str(args.device).lower() not in ("cpu",),
    )

    best = Path(results.save_dir) / "weights" / "best.pt"
    if not best.is_file():
        best = run_dir / "weights" / "best.pt"
    if not best.is_file():
        raise SystemExit(
            f"Training finished but best.pt not found under {run_dir / 'weights'}"
        )

    shutil.copy2(best, dest)
    print(f"Saved trained weights: {dest}")
    print(f"Ultralytics run:       {results.save_dir}")


if __name__ == "__main__":
    main()
