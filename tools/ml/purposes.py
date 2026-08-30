"""
Training purposes under tools/ml/ — each has its own dataset + weight slot.

Purposes mirror the agreed stack:
  pavement  — roadness / seg gate (Head A)
  pothole   — current defect detector (Head B)  [default, existing pipeline]
  crack     — optional extra defect head (Head C)
  rutting   — optional rutting defect head (Head D)
  waterlogging — optional standing-water defect head (Head E)
  ensemble  — eval-only fusion config (no separate train dataset required)

Layout (per purpose):
  datasets/<purpose>/roboflow/     ← downloads
  datasets/<purpose>/combined/     ← prepare_dataset output
  incoming/<purpose>/{images,labels}
  models/<purpose>/                ← trained weights
  runs/<purpose>/yolo|rfdetr/

Legacy paths (pothole only) remain valid:
  datasets/roboflow/, datasets/combined/, incoming/, models/*.pt
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

PurposeId = Literal["pavement", "pothole", "crack", "rutting", "waterlogging", "ensemble"]


@dataclass(frozen=True)
class PurposeSpec:
    id: PurposeId
    label: str
    role: str
    task: Literal["detect", "segment", "ensemble"]
    classes: tuple[str, ...]
    default_base: str
    output_stem: str
    notes: str = ""
    train_enabled: bool = True
    # Optional Roboflow Universe entries (id must exist in datasets_config.ROBOFLOW_DATASETS
    # or purpose-local list below)
    dataset_ids: tuple[str, ...] = field(default_factory=tuple)


PURPOSES: dict[str, PurposeSpec] = {
    "pavement": PurposeSpec(
        id="pavement",
        label="Pavement / roadness (spatial gate)",
        role="Head A - binary pavement vs background (seg or box-crop classifier)",
        task="segment",
        classes=("pavement",),
        default_base="yolo12n-seg.pt",
        output_stem="yolo_pavement_gate",
        notes=(
            "Keep defect boxes only if >=70% overlap with pavement mask. "
            "Train/eval only under model_testing - not production until promotion gate."
        ),
        dataset_ids=(),
    ),
    "pothole": PurposeSpec(
        id="pothole",
        label="Pothole detector",
        role="Head B - primary defect model (existing pipeline)",
        task="detect",
        classes=("pothole",),
        default_base="yolo_finetuned_copy.pt",
        output_stem="yolo_pothole_bench",
        notes="Default purpose; uses legacy datasets/combined when purpose folders absent.",
        dataset_ids=("aegis-pothole-v2",),
    ),
    "crack": PurposeSpec(
        id="crack",
        label="Crack / surface defect",
        role="Head C - optional extra defect head",
        task="detect",
        classes=("crack",),
        default_base="yolo12s.pt",
        output_stem="yolo_crack_bench",
        notes="Train only after pavement gate is stable; fuse behind roadness.",
        dataset_ids=(),
    ),
    "rutting": PurposeSpec(
        id="rutting",
        label="Rutting / wheel-path depression",
        role="Head D - optional rutting defect head",
        task="detect",
        classes=("rutting",),
        default_base="yolo12s.pt",
        output_stem="yolo_rutting_bench",
        notes=(
            "Surface distress on paved wheel paths - not pavement/roadness (Head A). "
            "Train after pavement gate; fuse behind roadness with pothole/crack."
        ),
        dataset_ids=(),
    ),
    "waterlogging": PurposeSpec(
        id="waterlogging",
        label="Waterlogging / standing water",
        role="Head E - optional waterlogging defect head",
        task="detect",
        classes=("waterlogging",),
        default_base="yolo12s.pt",
        output_stem="yolo_waterlogging_bench",
        notes=(
            "Standing water on or beside the carriageway - not pavement/roadness (Head A). "
            "Train after pavement gate; fuse behind roadness with other defect heads."
        ),
        dataset_ids=(),
    ),
    "ensemble": PurposeSpec(
        id="ensemble",
        label="Roadness + defect ensemble (eval)",
        role="Accept iff roadness >= threshold AND (pothole | crack | rutting | waterlogging) passes",
        task="ensemble",
        classes=("pavement", "pothole", "crack", "rutting", "waterlogging"),
        default_base="",
        output_stem="ensemble_roads_only",
        notes="No standalone train - wires pavement gate + defect weights at eval time.",
        train_enabled=False,
        dataset_ids=(),
    ),
}


def get_purpose(purpose_id: str | None) -> PurposeSpec:
    pid = (purpose_id or "pothole").strip().lower() or "pothole"
    if pid not in PURPOSES:
        known = ", ".join(PURPOSES)
        raise KeyError(f"Unknown purpose {purpose_id!r}. Known: {known}")
    return PURPOSES[pid]


def list_purposes() -> list[PurposeSpec]:
    return list(PURPOSES.values())
