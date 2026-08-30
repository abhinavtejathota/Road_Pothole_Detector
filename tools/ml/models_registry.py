"""
Catalog of models for the isolated model-testing bench.
Training outputs live in tools/ml/models/ — production artifacts/models/ is never written.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PKG_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PKG_ROOT.parent
BENCH_MODELS_DIR = PKG_ROOT / "models"

ModelKind = Literal["pothole", "general"]
ModelBackend = Literal["yolo", "rfdetr"]

FINETUNED_COPY = "yolo_finetuned_copy.pt"
BENCH_YOLO = "yolo_pothole_bench.pt"
BENCH_RFDETR = "rfdetr_pothole.pth"
RFDETR_SMALL = "rfdetr_small.pt"


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    path: str
    kind: ModelKind
    backend: ModelBackend
    description: str
    rfdetr_variant: str = "small"
    params_m: float | None = None
    notes: str = ""

    def resolve_path(self) -> str:
        p = Path(self.path)
        if p.is_absolute() and p.is_file():
            return str(p)
        if self.path:
            bench = (BENCH_MODELS_DIR / p.name).resolve()
            if bench.is_file():
                return str(bench)
            candidate = (PROJECT_ROOT / self.path).resolve()
            if candidate.is_file():
                return str(candidate)
        return self.path  # ultralytics hub name / empty


def _bench_weight(name: str) -> Path:
    return BENCH_MODELS_DIR / name


def _build_registry() -> list[ModelSpec]:
    specs: list[ModelSpec] = []
    known_names: set[str] = set()

    def add(spec: ModelSpec) -> None:
        specs.append(spec)
        if spec.path:
            known_names.add(Path(spec.path).name)

    # ── Bench-trained weights (priority) ─────────────────────────────────────
    if _bench_weight(BENCH_RFDETR).is_file():
        add(
            ModelSpec(
                id="bench-rfdetr",
                label="Bench · RF-DETR (fine-tuned)",
                path=BENCH_RFDETR,
                kind="pothole",
                backend="rfdetr",
                rfdetr_variant="small",
                description=(
                    "RF-DETR trained in tools/ml/ on pothole data — transformer detector "
                    "(DINOv2 backbone); often stronger on small/hard objects than YOLO."
                ),
            )
        )

    if _bench_weight(BENCH_YOLO).is_file():
        add(
            ModelSpec(
                id="bench-yolov12",
                label="Bench · YOLOv12 (fine-tuned)",
                path=BENCH_YOLO,
                kind="pothole",
                backend="yolo",
                description=(
                    "Legacy yolo_pothole_bench.pt. Prefer per-base outputs like "
                    "yolo12m_trained.pt from train_yolo.py --base yolo12m.pt."
                ),
            )
        )

    # ── Per-base train_yolo outputs: <stem>_trained.pt ───────────────────────
    if BENCH_MODELS_DIR.is_dir():
        for w in sorted(BENCH_MODELS_DIR.glob("*_trained.pt")):
            base_stem = w.stem[: -len("_trained")] if w.stem.endswith("_trained") else w.stem
            add(
                ModelSpec(
                    id=f"trained-{base_stem}",
                    label=f"Trained · {base_stem}",
                    path=str(w),
                    kind="pothole",
                    backend="yolo",
                    description=(
                        f"Fine-tuned from `{base_stem}.pt` via train_yolo.py. "
                        f"Run folder: tools/ml/runs/yolo/{w.stem}/"
                    ),
                    notes=f"models/{w.name}",
                )
            )

    # ── Trainable duplicate of production (local only; never writes prod) ────
    if _bench_weight(FINETUNED_COPY).is_file():
        add(
            ModelSpec(
                id="yolo-finetuned-copy",
                label="YOLOv12 fine-tuned (trainable copy)",
                path=FINETUNED_COPY,
                kind="pothole",
                backend="yolo",
                description=(
                    "Local duplicate of production smartroad_ap.pt under tools/ml/models/. "
                    "Use as the train starting point; production weights stay untouched."
                ),
                notes="Seeded by seed_weights.py. train_yolo.py defaults to this file.",
            )
        )
    else:
        # Fallback: show production as read-only reference until seed_weights is run
        prod = PROJECT_ROOT / "artifacts/models" / "smartroad_ap.pt"
        if prod.is_file():
            add(
                ModelSpec(
                    id="prod-yolov12-ref",
                    label="Production YOLOv12 (reference only)",
                    path=str(prod),
                    kind="pothole",
                    backend="yolo",
                    description=(
                        "Production smartroad_ap.pt — comparison only. "
                        "Run `python tools/ml/seed_weights.py` to create a trainable copy "
                        "inside tools/ml/models/."
                    ),
                    notes="Read-only. Prefer seeding a local copy for training.",
                )
            )

    # ── RF-DETR Small pretrained (local .pt from seed_weights.py) ────────────
    if _bench_weight(RFDETR_SMALL).is_file():
        add(
            ModelSpec(
                id="rfdetr-small",
                label="RF-DETR Small (pretrained)",
                path=RFDETR_SMALL,
                kind="pothole",
                backend="rfdetr",
                rfdetr_variant="small",
                description=(
                    "Local rfdetr_small.pt (COCO pretrained). "
                    "Fine-tune with train_rfdetr.py → rfdetr_pothole.pth."
                ),
                notes="Seeded by seed_weights.py / setup_train.py.",
            )
        )
    else:
        add(
            ModelSpec(
                id="rfdetr-small",
                label="RF-DETR Small (pretrained — run seed_weights)",
                path=RFDETR_SMALL,
                kind="pothole",
                backend="rfdetr",
                rfdetr_variant="small",
                description=(
                    "Missing local models/rfdetr_small.pt. "
                    "Run: python tools/ml/seed_weights.py"
                ),
                notes="Requires: pip install -r tools/ml/requirements.txt",
            )
        )

    # ── COCO / architecture baselines (prefer local .pt under models/) ───────
    for mid, label, filename, pm in [
        ("yolov12n", "YOLOv12 Nano (COCO)", "yolo12n.pt", 2.5),
        ("yolov11n", "YOLOv11 Nano (COCO)", "yolo11n.pt", 2.6),
        ("yolov8n", "YOLOv8 Nano (COCO)", "yolov8n.pt", 3.2),
        ("yolov8s", "YOLOv8 Small (COCO)", "yolov8s.pt", 11.2),
        ("yolov12s", "YOLOv12 Small (COCO / train base)", "yolo12s.pt", 9.0),
        ("yolov12m", "YOLOv12 Medium (COCO)", "yolo12m.pt", 20.0),
        ("yolov12l", "YOLOv12 Large (COCO)", "yolo12l.pt", 26.0),
        ("yolov12x", "YOLOv12 XLarge (COCO)", "yolo12x.pt", 59.0),
    ]:
        local = _bench_weight(filename)
        path = filename if local.is_file() else filename  # hub name == filename
        notes = (
            f"Local file: models/{filename}"
            if local.is_file()
            else "Will download from Ultralytics on first load — run seed_weights.py to cache locally."
        )
        add(
            ModelSpec(
                id=mid,
                label=label,
                path=path,
                kind="general",
                backend="yolo",
                params_m=pm,
                description="COCO pretrained — latency / architecture baseline.",
                notes=notes,
            )
        )

    # ── Extra .pt / .pth in models/ ─────────────────────────────────────────
    if BENCH_MODELS_DIR.is_dir():
        skip = {
            FINETUNED_COPY,
            BENCH_YOLO,
            BENCH_RFDETR,
            RFDETR_SMALL,
            "yolo12n.pt",
            "yolo11n.pt",
            "yolov8n.pt",
            "yolov8s.pt",
            "yolo12s.pt",
            "yolo12m.pt",
            "yolo12l.pt",
            "yolo12x.pt",
        }
        for w in sorted(BENCH_MODELS_DIR.glob("*")):
            if w.name in known_names or w.name in skip or w.suffix not in {".pt", ".pth"}:
                continue
            # *_trained.pt already registered above
            if w.suffix == ".pt" and w.stem.endswith("_trained"):
                continue
            backend: ModelBackend = "rfdetr" if w.suffix == ".pth" else "yolo"
            add(
                ModelSpec(
                    id=f"custom-{w.stem}",
                    label=f"Custom · {w.stem}",
                    path=str(w),
                    kind="pothole",
                    backend=backend,
                    description=f"Local bench weights `{w.name}`.",
                )
            )

    return specs


REGISTRY: list[ModelSpec] = _build_registry()


def refresh_registry() -> list[ModelSpec]:
    global REGISTRY
    REGISTRY = _build_registry()
    return REGISTRY


def get_model(model_id: str) -> ModelSpec:
    refresh_registry()
    for spec in REGISTRY:
        if spec.id == model_id:
            return spec
    raise KeyError(f"Unknown model id: {model_id}")


def choices() -> list[tuple[str, str]]:
    refresh_registry()
    return [(s.label, s.id) for s in REGISTRY]


def default_model_id() -> str:
    refresh_registry()
    for pref in (
        "bench-rfdetr",
        "bench-yolov12",
        "trained-yolo12m",
        "trained-yolo12s",
        "yolo-finetuned-copy",
        "rfdetr-small",
    ):
        for s in REGISTRY:
            if s.id == pref:
                return s.id
    for s in REGISTRY:
        if s.id.startswith("trained-"):
            return s.id
    for s in REGISTRY:
        if s.id not in ("prod-yolov12-ref",):
            return s.id
    return REGISTRY[0].id if REGISTRY else "yolov8n"


def description_html(model_id: str) -> str:
    try:
        spec = get_model(model_id)
    except KeyError:
        return "<p class='mt-desc'>Select a model.</p>"
    badge = "Pothole" if spec.kind == "pothole" else "General COCO"
    backend = spec.backend.upper()
    params = f" · ~{spec.params_m}M params" if spec.params_m else ""
    return (
        f"<p class='mt-desc'><strong>{spec.label}</strong> "
        f"<span class='mt-badge'>{badge}</span> "
        f"<span class='mt-badge'>{backend}</span>{params}<br/>"
        f"{spec.description}</p>"
    )


def trainable_rfdetr_base() -> str | None:
    """Local RF-DETR Small .pt for train_rfdetr --base."""
    p = _bench_weight(RFDETR_SMALL)
    return str(p) if p.is_file() else None


def trainable_yolo_base() -> str:
    """Preferred --base for train_yolo: local fine-tuned copy, else yolo12s.pt, else yaml."""
    copy = _bench_weight(FINETUNED_COPY)
    if copy.is_file():
        return str(copy)
    s = _bench_weight("yolo12s.pt")
    if s.is_file():
        return str(s)
    return "yolov12s.yaml"
