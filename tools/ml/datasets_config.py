"""
Dataset & Roboflow sources for model_testing — edit links here.

All downloads land under tools/ml/datasets/ (never production weights).
Use ``purpose`` to bind a dataset to pavement / pothole / crack / rutting / waterlogging pipelines.
"""
from __future__ import annotations

# ── Primary pothole dataset (YOLO export) ────────────────────────────────────
# Universe: https://universe.roboflow.com/aegis/pothole-detection-i00zy/dataset/2
ROBOFLOW_DATASETS = [
    {
        "id": "aegis-pothole-v2",
        "name": "Aegis Pothole Detection v2",
        "url": "https://universe.roboflow.com/aegis/pothole-detection-i00zy/dataset/2",
        "format": "yolov12",  # YOLO txt labels; also works for RF-DETR after prepare
        "classes": ["pothole"],
        "purpose": "pothole",
        "notes": "Default base set used by prepare_dataset.py + train_*.py",
        "default": True,
    },
    # Add pavement / crack / rutting / waterlogging Universe links when available, e.g.:
    # {
    #     "id": "idd-pavement-v1",
    #     "name": "IDD / Cityscapes pavement masks (export YOLO-seg)",
    #     "url": "https://universe.roboflow.com/<ws>/<project>/dataset/<ver>",
    #     "format": "yolov8",
    #     "classes": ["pavement"],
    #     "purpose": "pavement",
    #     "notes": "Spatial gate Head A",
    #     "default": False,
    # },
]

# Folder name under tools/ml/datasets/roboflow/ for the default download
ROBOFLOW_LOCAL_DIRNAME = "Pothole-Detection--2"

# RF-DETR Small COCO pretrained (official Roboflow GCS)
RFDETR_SMALL_URL = "https://storage.googleapis.com/rfdetr/small_coco/checkpoint_best_regular.pth"
RFDETR_SMALL_MD5 = "fb37061c1af7bace359c91b723a8d5c1"
RFDETR_SMALL_LOCAL = "rfdetr_small.pt"


def default_dataset(purpose: str | None = None) -> dict:
    purpose = (purpose or "pothole").strip().lower()
    for d in ROBOFLOW_DATASETS:
        if d.get("purpose", "pothole") == purpose and d.get("default"):
            return d
    for d in ROBOFLOW_DATASETS:
        if d.get("purpose", "pothole") == purpose:
            return d
    if purpose == "pothole":
        return ROBOFLOW_DATASETS[0]
    raise KeyError(
        f"No Roboflow dataset configured for purpose={purpose!r}. "
        "Add an entry with that purpose in datasets_config.py "
        f"(or drop a YOLO export under datasets/{purpose}/roboflow/)."
    )


def get_dataset(dataset_id: str | None, purpose: str | None = None) -> dict:
    if not dataset_id:
        return default_dataset(purpose)
    for d in ROBOFLOW_DATASETS:
        if d["id"] == dataset_id:
            return d
    known = ", ".join(x["id"] for x in ROBOFLOW_DATASETS)
    raise KeyError(f"Unknown dataset id {dataset_id!r}. Known: {known}")


def datasets_for_purpose(purpose: str) -> list[dict]:
    purpose = (purpose or "pothole").strip().lower()
    return [d for d in ROBOFLOW_DATASETS if d.get("purpose", "pothole") == purpose]
