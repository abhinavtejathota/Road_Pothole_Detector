# Model Testing — full train setup

Self-contained bench under **`tools/ml/`**: local weights, Roboflow links, dataset merge, YOLO + RF-DETR training, **per-purpose** pipelines (`pavement` / `pothole` / `crack` / `rutting` / `waterlogging` / `ensemble`).  
Production `artifacts/models/smartroad_ap.pt` is **never** modified.

## Quick start

```powershell
pip install -r requirements.txt
pip install -r tools/ml/requirements.txt

python tools/ml/train_yolo.py --list-purposes

# Pothole (default — same as before)
python tools/ml/setup_train.py
python tools/ml/train_yolo.py --purpose pothole

# Pavement gate / crack / rutting / waterlogging heads (add datasets in datasets_config.py first)
python tools/ml/prepare_dataset.py --purpose pavement
python tools/ml/train_yolo.py --purpose pavement
python tools/ml/prepare_dataset.py --purpose rutting
python tools/ml/train_yolo.py --purpose rutting
python tools/ml/prepare_dataset.py --purpose waterlogging
python tools/ml/train_yolo.py --purpose waterlogging
python tools/ml/eval_roads_only.py
```

Needs `ROBOFLOW_API_KEY=rf_...` in project `.env` (or `tools/ml/.env`).

## Roboflow links

Edit **`datasets_config.py`** — add / change Universe URLs there.

```powershell
python tools/ml/download_data.py --list
python tools/ml/download_data.py
python tools/ml/download_data.py --dataset aegis-pothole-v2
```

Default: [Aegis Pothole Detection v2](https://universe.roboflow.com/aegis/pothole-detection-i00zy/dataset/2)

Downloads go to `tools/ml/datasets/roboflow/` (not the repo root).

## Local weight files (`models/`)

| File | Role |
|------|------|
| `rfdetr_small.pt` | RF-DETR Small pretrained (dropdown + train base) |
| `yolo_finetuned_copy.pt` | Copy of production YOLO (train without touching prod) |
| `yolo_pothole_bench.pt` | After `train_yolo.py` |
| `rfdetr_pothole.pth` | After `train_rfdetr.py` |
| `yolo12n.pt` … `yolo12s.pt` | COCO / architecture baselines |
| `yolo12m.pt` / `yolo12l.pt` / `yolo12x.pt` | Larger YOLO12 COCO (GPU bench on server) |

```powershell
python tools/ml/seed_weights.py
# YOLO12 sizes only (good on AceCloud before model-bench):
python tools/ml/seed_weights.py --yolo12-only
```

## Layout

```
tools/ml/
├── datasets_config.py   ← Roboflow URLs / formats
├── download_data.py     ← pull datasets into datasets/roboflow/
├── seed_weights.py      ← local .pt files including rfdetr_small.pt
├── setup_train.py       ← seed + download + prepare
├── prepare_dataset.py   ← merge Roboflow + incoming/ → datasets/combined/
├── collect_images.py
├── train_yolo.py
├── train_rfdetr.py
├── models_registry.py
├── inference.py
├── models/              ← .pt / .pth slots
├── datasets/
│   ├── README.md
│   ├── roboflow/
│   └── combined/
├── incoming/            ← your photos + YOLO labels
├── runs/
└── TRAINING.md          ← labeling detail
```

## Portal

```powershell
cd frontend && npm run build && cd ..
python web_app.py
```

Admin → **Model testing** (sidebar bottom).

Legacy Gradio: `deprecated_files/gradio_model_bench_run.py`


