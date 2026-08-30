"""Isolated paths — all training data & outputs stay under tools/ml/."""
from __future__ import annotations

import os
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PKG_ROOT.parent.parent

BENCH_MODELS_DIR = PKG_ROOT / "models"
RUNS_DIR = PKG_ROOT / "runs"
INCOMING_DIR = PKG_ROOT / "incoming"
DATASETS_DIR = PKG_ROOT / "datasets"
ROBOFLOW_DIR = DATASETS_DIR / "roboflow"
COMBINED_DATASET = DATASETS_DIR / "combined"
COMBINED_DATA_YAML = COMBINED_DATASET / "data.yaml"

RFDETR_RUN_DIR = RUNS_DIR / "rfdetr"
YOLO_RUN_DIR = RUNS_DIR / "yolo"

# Preferred: dataset downloaded by tools/ml/download_data.py
ROBOFLOW_DATA_YAML = ROBOFLOW_DIR / "Pothole-Detection--2" / "data.yaml"
# Legacy fallbacks (older root download_data.py)
_LEGACY_ROBOFLOW_YAML = PROJECT_ROOT / "Pothole-Detection--2" / "data.yaml"


def purpose_paths(purpose: str = "pothole") -> dict[str, Path]:
    """Per-purpose dirs (pothole keeps legacy flat layout for compatibility)."""
    pid = (purpose or "pothole").strip().lower() or "pothole"
    if pid == "pothole":
        return {
            "purpose": Path(pid),
            "incoming": INCOMING_DIR,
            "roboflow": ROBOFLOW_DIR,
            "combined": COMBINED_DATASET,
            "combined_yaml": COMBINED_DATA_YAML,
            "models": BENCH_MODELS_DIR,
            "runs_yolo": YOLO_RUN_DIR,
            "runs_rfdetr": RFDETR_RUN_DIR,
        }
    base = DATASETS_DIR / pid
    return {
        "purpose": Path(pid),
        "incoming": PKG_ROOT / "incoming" / pid,
        "roboflow": base / "roboflow",
        "combined": base / "combined",
        "combined_yaml": base / "combined" / "data.yaml",
        "models": BENCH_MODELS_DIR / pid,
        "runs_yolo": RUNS_DIR / pid / "yolo",
        "runs_rfdetr": RUNS_DIR / pid / "rfdetr",
    }


def ensure_dirs(purpose: str | None = None) -> None:
    dirs = [
        BENCH_MODELS_DIR,
        RUNS_DIR,
        INCOMING_DIR,
        INCOMING_DIR / "images",
        INCOMING_DIR / "labels",
        DATASETS_DIR,
        ROBOFLOW_DIR,
        COMBINED_DATASET,
        RFDETR_RUN_DIR,
        YOLO_RUN_DIR,
    ]
    if purpose:
        pp = purpose_paths(purpose)
        dirs.extend([
            pp["incoming"],
            pp["incoming"] / "images",
            pp["incoming"] / "labels",
            pp["roboflow"],
            pp["combined"],
            pp["models"],
            pp["runs_yolo"],
            pp["runs_rfdetr"],
        ])
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def resolve_roboflow_yaml(purpose: str = "pothole") -> Path | None:
    pp = purpose_paths(purpose)
    preferred = pp["roboflow"] / "Pothole-Detection--2" / "data.yaml"
    if purpose == "pothole" and ROBOFLOW_DATA_YAML.is_file():
        return ROBOFLOW_DATA_YAML
    if preferred.is_file():
        return preferred
    if purpose == "pothole" and _LEGACY_ROBOFLOW_YAML.is_file():
        return _LEGACY_ROBOFLOW_YAML
    root = pp["roboflow"]
    if root.is_dir():
        matches = sorted(root.glob("*/data.yaml"))
        if matches:
            return matches[0]
    return None


def dataset_yaml_path(purpose: str = "pothole") -> Path:
    """Prefer merged bench dataset; fall back to Roboflow export."""
    pp = purpose_paths(purpose)
    if pp["combined_yaml"].is_file():
        return pp["combined_yaml"]
    yaml_path = resolve_roboflow_yaml(purpose)
    if yaml_path and yaml_path.is_file():
        return yaml_path
    raise FileNotFoundError(
        f"No dataset for purpose={purpose!r}. From project root run:\n"
        f"  python tools/ml/setup_train.py --purpose {purpose}\n"
        f"or:\n"
        f"  python tools/ml/download_data.py --purpose {purpose}\n"
        f"  python tools/ml/prepare_dataset.py --purpose {purpose}\n"
        "Edit Roboflow links in tools/ml/datasets_config.py"
    )


def dataset_root_for_rfdetr(purpose: str = "pothole") -> Path:
    """
    RF-DETR expects a folder with train/, valid/ (and optional test/).
    Prefers merged bench dataset, then local Roboflow download.
    """
    pp = purpose_paths(purpose)
    if (pp["combined"] / "train").is_dir():
        return pp["combined"]
    yaml_path = resolve_roboflow_yaml(purpose)
    if yaml_path:
        root = yaml_path.parent
        if (root / "train").is_dir():
            return root
    if purpose == "pothole" and (PROJECT_ROOT / "train").is_dir():
        return PROJECT_ROOT
    raise FileNotFoundError(
        f"No train/valid folders for RF-DETR (purpose={purpose}). Run:\n"
        f"  python tools/ml/download_data.py --purpose {purpose}\n"
        f"  python tools/ml/prepare_dataset.py --purpose {purpose}"
    )


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw else default
