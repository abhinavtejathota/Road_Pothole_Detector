# Training guide — `tools/ml/` (full pipeline)

## Train on GPU (AceCloud) — use this

After deploy, restart **detect** so YOLO + ffmpeg pick up `.env`. Then train with GPU for the model and **no** DataLoader worker processes (keeps portal/login responsive):

```bash
# Restart detect after pulling .env / code (split deploy):
./scripts/services.sh restart detect

# Train — GPU device 0, workers=0 (DataLoader stays on CPU by design; 0 = no extra procs)
python tools/ml/train_yolo.py --device 0 --workers 0

# Same via env (copy into project-root .env — see block below):
#   YOLO_DEVICE=0
#   YOLO_WORKERS=0
#   POTHOLE_CPU_THREADS=3
python tools/ml/train_yolo.py --purpose pothole
```

**What runs on GPU vs CPU**

| Work | Device |
|------|--------|
| YOLO forward / backward | GPU (`--device 0` / `YOLO_DEVICE=0`) |
| H.264 re-encode | GPU NVENC (+ NVDEC decode when `FFMPEG_HW=auto`) |
| OpenCV frame draw / JPEG load / augment | CPU (cannot move to GPU in this stack) |
| DataLoader workers | CPU — keep `--workers 0` (or `YOLO_WORKERS=0`) so training does not peg all cores |

**`.env` knobs (copy-paste into project root `.env`, then restart detect):**

```env
# GPU: detect + train (portal/upload still forced CPU in services.sh)
DETECT_YOLO_DEVICE=0
DETECT_CUDA_VISIBLE_DEVICES=0
YOLO_DEVICE=0
POTHOLE_HALF=1
POTHOLE_CPU_THREADS=3
YOLO_WORKERS=0

# ffmpeg: NVDEC decode + NVENC encode when system ffmpeg has CUDA
FFMPEG_HW=auto
FFMPEG_PREFER_SYSTEM=1
FFMPEG_NVENC_PRESET=p4
FINALIZE_FFMPEG_THREADS=2
```

---

Everything in this guide stays under **`tools/ml/`**.  
Training scripts **never** write `artifacts/models/smartroad_ap.pt`. Promotion to production is always a **manual** copy (see §10).

**Where to train:** label / prepare on a laptop; train **m / l / x** (and preferably all runs) on AceCloud GPU; live camera check on the server **Model testing** page (`/model-bench`). Ops detail: [`docs/ops/DETECTION_MODEL.md`](../docs/ops/DETECTION_MODEL.md).

---

## 0. Big picture (what you are building)

```
                    ┌─────────────────────────────────────────┐
                    │         tools/ml/ (bench only)      │
                    │                                         │
  images+labels ──► │  prepare → train → models/*.pt          │
                    │       │                                 │
                    │       ▼                                 │
                    │  portal Model testing (/model-bench)    │
                    │       │                                 │
                    │       ▼  (manual, after metrics gate)   │
                    └───────┼─────────────────────────────────┘
                            ▼
              artifacts/models/smartroad_ap.pt
              (production Detection page only)
```

**Agreed multi-head stack** (roads-only):

| Order | Purpose | Role | Train? |
|------:|---------|------|--------|
| 1 | `pavement` | Head A — spatial gate (pavement mask / roadness). Keep defect boxes only if ≥ ~70% overlap with pavement | Yes |
| 2 | `pothole` | Head B — pothole detector (existing default pipeline) | Yes |
| 3 | `crack` | Head C — optional crack defect | Yes (optional) |
| 4 | `rutting` | Head D — optional rutting / wheel-path depression | Yes (optional) |
| 5 | `waterlogging` | Head E — optional standing water on carriageway | Yes (optional) |
| 6 | `ensemble` | Accept iff `roadness ≥ τ` **and** (pothole \| crack \| rutting \| waterlogging). Eval only | No train |

```powershell
python tools/ml/train_yolo.py --list-purposes
python tools/ml/eval_roads_only.py --list-purposes
```

**Principle:** ensemble improves defect taxonomy; **roads-only** = pavement mask (primary) + optional GIS >15 m off-corridor (secondary). Do not promote until:

- Precision_road ≥ 92%  
- Recall_road ≥ 88%  
- FPR_off-road ≤ 2%

---

## 1. Prerequisites (once per machine)

### 1.1 Repo + Python env

```powershell
cd <repo_root>   # folder that contains web_app.py and tools/ml/

# Prefer the project venv used by the portal
# Windows:
vnev\Scripts\activate
# Linux:
# source venv/bin/activate

pip install -r requirements.txt
pip install -r tools/ml/requirements.txt
```

YOLOv12 is not always on PyPI alone — follow project install notes if `ultralytics` / yolov12 import fails (see root `CLAUDE.md` / `DETECTION_MODEL.md`).

### 1.2 API key for Roboflow downloads

In **project root** `.env` (or `tools/ml/.env`):

```env
ROBOFLOW_API_KEY=rf_...
```

Only needed for `download_data.py` / `setup_train.py` (not for manual folder drops).

### 1.3 Optional training knobs (env)

Prefer the copy-paste block at the **top of this file**. Summary:

| Env | Default | Used by |
|-----|---------|---------|
| `YOLO_DEVICE` | `0` | `train_yolo.py --device`, detect service |
| `YOLO_WORKERS` | `0` | DataLoader CPU workers (`--workers`) |
| `POTHOLE_CPU_THREADS` | `3` | OpenCV/BLAS cap during detect/train |
| `YOLO_EPOCHS` | 80 | `train_yolo.py` |
| `YOLO_IMGSZ` | 640 | `train_yolo.py` |
| `YOLO_BATCH` | 16 | `train_yolo.py` |
| `RFDETR_EPOCHS` | 50 | `train_rfdetr.py` |
| `RFDETR_BATCH` | 4 | `train_rfdetr.py` |
| `RFDETR_GRAD_ACCUM` | 4 | `train_rfdetr.py` |
| `RFDETR_LR` | 1e-4 | `train_rfdetr.py` |

---

## 2. Folder layout (micro)

```
tools/ml/
├── purposes.py              # purpose registry (pavement/pothole/crack/rutting/waterlogging/ensemble)
├── datasets_config.py       # Roboflow Universe URLs + purpose= tag
├── paths.py                 # all paths (purpose-aware)
├── models_registry.py       # portal Model testing dropdown
├── seed_weights.py          # download baseline .pt into models/
├── download_data.py         # Roboflow → datasets/.../roboflow/
├── prepare_dataset.py       # merge Roboflow + incoming → combined/
├── setup_train.py           # seed + download + prepare (pothole one-shot)
├── collect_images.py        # grab frames into incoming/
├── split_dataset.py         # train → valid split
├── prune_empty_labels.py    # drop empty YOLO txt pairs
├── voc_xml_to_yolo.py       # VOC XML → YOLO txt
├── train_yolo.py            # --purpose … fine-tune YOLO
├── train_rfdetr.py          # RF-DETR (pothole combined by default)
├── eval_roads_only.py       # roads-only checklist + overlap helper
├── inference.py             # bench inference helpers
├── incoming/                # POTHOLE legacy: your photos + YOLO labels
│   ├── images/
│   └── labels/
├── incoming/<purpose>/      # pavement | crack | rutting | waterlogging (same images/labels shape)
├── datasets/
│   ├── roboflow/            # POTHOLE Roboflow downloads
│   ├── combined/            # POTHOLE prepare output + data.yaml
│   └── <purpose>/
│       ├── roboflow/
│       └── combined/
├── models/
│   ├── yolo12n.pt … yolo12x.pt
│   ├── yolo_finetuned_copy.pt   # copy of prod (train without touching prod)
│   ├── rfdetr_small.pt
│   ├── yolo_pothole_bench.pt / *_trained.pt   # pothole outputs (flat)
│   └── <purpose>/…          # pavement / crack / rutting / waterlogging weight slots
└── runs/
    ├── yolo/                # pothole Ultralytics runs
    ├── rfdetr/
    └── <purpose>/yolo/…
```

**Path rule:** `purpose=pothole` keeps the **legacy flat** layout (`datasets/combined/`, `incoming/`, `models/*.pt`).  
Other purposes use nested folders under `datasets/<purpose>/`, `incoming/<purpose>/`, `models/<purpose>/`.

---

## 3. End-to-end pipeline (do these in order)

Each purpose follows the **same** micro pipeline:

```
0) list purpose
1) seed baseline weights          (once / when missing)
2) get labeled data               (Roboflow and/or manual / incoming)
3) (optional) convert / prune / split
4) prepare_dataset                → combined/data.yaml
5) train_yolo (or train_rfdetr)   → models/… .pt
6) test in portal Model testing
7) (roads-only) eval gate metrics
8) (optional) promote to production manually
```

---

## 4. Pipeline A — Pothole (default, current production path)

This is the path that already powers the pothole bench.

### 4.0 One-shot (seed + download + prepare)

```powershell
python tools/ml/setup_train.py
# options:
#   --skip-seed
#   --skip-download
#   --skip-prepare
#   --force-seed
```

Equivalent manual steps: §4.1 → §4.2 → §4.4.

### 4.1 Seed baseline weights

```powershell
python tools/ml/seed_weights.py
python tools/ml/seed_weights.py --yolo12-only    # n/s/m/l/x only
python tools/ml/seed_weights.py --force          # re-download
```

Creates under `tools/ml/models/` (among others):

| File | Role |
|------|------|
| `yolo_finetuned_copy.pt` | Trainable copy of production YOLO |
| `rfdetr_small.pt` | RF-DETR Small pretrained |
| `yolo12n.pt` … `yolo12x.pt` | COCO baselines |

Manual fallback if seed fails:

```powershell
cd model_testing\models
curl -L -o yolo12x.pt https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo12x.pt
```

| File | URL |
|------|-----|
| `yolo12m.pt` | https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo12m.pt |
| `yolo12l.pt` | https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo12l.pt |
| `yolo12x.pt` | https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo12x.pt |

### 4.2 Get labeled pothole data

#### Option A — Roboflow (configured default)

Edit / add links in `tools/ml/datasets_config.py` (`purpose: "pothole"`).

```powershell
python tools/ml/download_data.py --list
python tools/ml/download_data.py
python tools/ml/download_data.py --dataset aegis-pothole-v2
python tools/ml/download_data.py --purpose pothole
```

Default Universe set: [Aegis Pothole Detection v2](https://universe.roboflow.com/aegis/pothole-detection-i00zy/dataset/2).

Lands in: `tools/ml/datasets/roboflow/`  
Canonical folder name: `Pothole-Detection--2/data.yaml` (preferred by `paths.resolve_roboflow_yaml`).

#### Option B — Drop a YOLO export manually

Put any YOLO export under e.g. `tools/ml/datasets/roboflow/my-potholes-v1/`.

Lookup order for pothole base:

1. `datasets/roboflow/Pothole-Detection--2/data.yaml`
2. Else first `datasets/roboflow/*/data.yaml` alphabetically

If missing `data.yaml`, add:

```yaml
path: .
train: train/images
val: valid/images
test: test/images
nc: 1
names: ['pothole']
```

Set `train` / `val` / `test` to match your real folders (`train`, `valid`, `images/train`, …).

#### Option C — Your own photos via `incoming/`

1. Collect:

```powershell
python tools/ml/collect_images.py --webcam 30
python tools/ml/collect_images.py D:\photos\road1.jpg
ffmpeg -i survey.mp4 -vf fps=1 tools/ml/incoming/images/frame_%04d.jpg
```

2. Label (YOLO txt, class `0` = pothole):

| Tool | Notes |
|------|-------|
| [Roboflow](https://roboflow.com) | Export YOLOv8 / YOLOv12 |
| CVAT | Export YOLO 1.1 |
| LabelImg | YOLO txt |

Place files in:

- `tools/ml/incoming/images/<stem>.jpg`
- `tools/ml/incoming/labels/<stem>.txt`

Each label line: `class x_center y_center width height` (normalized 0–1).

#### Option D — Pascal VOC XML → YOLO

```powershell
python tools/ml/voc_xml_to_yolo.py `
  --images "D:\path\to\images" `
  --xmls   "D:\path\to\annotations\xmls" `
  --out    tools/ml/datasets/roboflow/india-potholes
```

- Default keeps **pothole-like** boxes (`D40`, `pothole`, … → class `0`).
- Empty XMLs → empty `.txt` (background — useful).
- Only images with boxes: `--skip-empty`, or run `prune_empty_labels.py` after.
- `--all-classes` keeps every VOC name (usually not wanted for pothole-only).

### 4.3 Optional hygiene before prepare / train

**Only `train/`, no `valid/` yet** — split ~15–20%:

```powershell
python tools/ml/split_dataset.py --root "D:\path\to\your\dataset" --dry-run
python tools/ml/split_dataset.py --root "D:\path\to\your\dataset" --ratio 0.15 --copy
```

`--copy` keeps originals in `train/`; omit to **move** into `valid/images` + `valid/labels`.

**Drop empty labels** (images with no boxes):

```powershell
python tools/ml/prune_empty_labels.py --root "D:\path\to\your\dataset" --dry-run
python tools/ml/prune_empty_labels.py --root "D:\path\to\your\dataset"
```

Works on `root/images`+`labels` or `root/train/images`+`labels`.

### 4.4 Prepare combined dataset (Roboflow + incoming)

```powershell
python tools/ml/prepare_dataset.py
python tools/ml/prepare_dataset.py --purpose pothole
```

Writes:

- `tools/ml/datasets/combined/train|valid|test/{images,labels}/`
- `tools/ml/datasets/combined/data.yaml`  
  (`nc` / `names` from purpose → `['pothole']`)

### 4.5 Train YOLO (pothole)

```powershell
# Default purpose=pothole; default base from models_registry.trainable_yolo_base()
python tools/ml/train_yolo.py --purpose pothole

# Explicit base / data / name
python tools/ml/train_yolo.py `
  --purpose pothole `
  --base tools/ml/models/yolo12m.pt `
  --data tools/ml/datasets/combined/data.yaml `
  --epochs 80 --imgsz 640 --batch 16

# Resume same run
python tools/ml/train_yolo.py --purpose pothole --base tools/ml/models/yolo12m.pt --resume
```

**Default `--base` picker** (`trainable_yolo_base()`), first existing wins:

1. `models/yolo_finetuned_copy.pt`
2. else `models/yolo12s.pt`
3. else Ultralytics `yolov12s.yaml`

**Outputs (pothole / flat):**

| `--base` | Run folder | Weight |
|----------|------------|--------|
| `yolo12m.pt` | `runs/yolo/yolo12m_trained/` | `models/yolo12m_trained.pt` |
| `yolo12l.pt` | `runs/yolo/yolo12l_trained/` | `models/yolo12l_trained.pt` |
| `yolo_finetuned_copy.pt` | `runs/yolo/yolo_finetuned_copy_trained/` | `models/yolo_finetuned_copy_trained.pt` |
| `--name my_run` | `runs/yolo/my_run/` | `models/my_run.pt` |

Purpose default stem for pothole: `yolo_pothole_bench` when you rely on purpose `output_stem` (override with `--base` / `--name` as above).

**`--data` auto-pick** (`dataset_yaml_path("pothole")`):

1. `datasets/combined/data.yaml` if present  
2. else first Roboflow `data.yaml`

Script prints `Purpose`, `data.yaml`, `Base`, `Run name`, `Output` at start — always check those lines.

Laptop vs GPU:

```powershell
# laptop / small
python tools/ml/train_yolo.py --purpose pothole --base tools/ml/models/yolo12s.pt --batch 8

# AceCloud GPU
python tools/ml/train_yolo.py --purpose pothole --base tools/ml/models/yolo12m.pt
python tools/ml/train_yolo.py --purpose pothole --base tools/ml/models/yolo12l.pt
python tools/ml/train_yolo.py --purpose pothole --base tools/ml/models/yolo12x.pt --batch 4
```

### 4.6 Train RF-DETR (pothole, optional)

```powershell
python tools/ml/train_rfdetr.py
python tools/ml/train_rfdetr.py --variant small --epochs 50 --batch-size 4
python tools/ml/train_rfdetr.py --variant base   # needs more VRAM
python tools/ml/train_rfdetr.py --resume tools/ml/runs/rfdetr/checkpoint.pth
```

- Dataset root: `datasets/combined/` (or Roboflow folder with `train/`).
- Starts from `models/rfdetr_small.pt` when present.
- Best → `models/rfdetr_pothole.pth`.

### 4.7 Test in portal (Model testing)

1. Sync code + weights to the **server** (if you trained elsewhere).
2. Restart detect (GPU process): `./scripts/services.sh restart detect`
3. Portal → Admin → **Model testing** (`/model-bench`)
4. Dropdown: `Trained · yolo12m` for `yolo12m_trained.pt`, RF-DETR bench, COCO baselines, or any custom `.pt` dropped in `models/`
5. **Live back camera** or **Single image** → Run

API is on **detect** (`:5007`), not portal CPU.  
Copy one weight:

```bash
scp yolo12m_trained.pt user@server:/path/to/repo/tools/ml/models/yolo12m_trained.pt
```

---

## 5. Pipeline B — Pavement (Head A / spatial gate)

Same micro steps as pothole; different folders + classes.

### 5.1 Configure a dataset

In `datasets_config.py` add (example — replace with your Universe URL):

```python
{
    "id": "my-pavement-v1",
    "name": "Pavement masks v1",
    "url": "https://universe.roboflow.com/<ws>/<project>/dataset/<ver>",
    "format": "yolov8",          # or yolov12; YOLO-seg export if using seg
    "classes": ["pavement"],
    "purpose": "pavement",
    "notes": "Head A spatial gate",
    "default": False,
},
```

Or drop a YOLO / YOLO-seg export under:

`tools/ml/datasets/pavement/roboflow/<any-name>/data.yaml`

Or label into:

`tools/ml/incoming/pavement/images/` + `…/labels/`

### 5.2 Download → prepare → train

```powershell
python tools/ml/download_data.py --purpose pavement --dataset my-pavement-v1
# (skip download if you dropped files manually)

python tools/ml/prepare_dataset.py --purpose pavement
# → datasets/pavement/combined/data.yaml   names: ['pavement']

python tools/ml/train_yolo.py --purpose pavement
# default base: yolo12n-seg.pt (seg task) — override with --base if needed
# → models/pavement/yolo_pavement_gate.pt
# → runs/pavement/yolo/…
```

### 5.3 Gate rule (eval)

```powershell
python tools/ml/eval_roads_only.py --overlap 0.70
```

Keep a defect box only if pavement-mask density inside the box ≥ 70% (`box_pavement_overlap` in `eval_roads_only.py`).  
Wire this into `model_testing.inference` / model-bench **before** any production copy.

---

## 6. Pipeline C — Crack (Head C, optional)

```powershell
# Add purpose=crack entry in datasets_config.py OR drop YOLO export under
#   datasets/crack/roboflow/…  OR  incoming/crack/{images,labels}

python tools/ml/prepare_dataset.py --purpose crack
python tools/ml/train_yolo.py --purpose crack
# default base yolo12s.pt → models/crack/yolo_crack_bench.pt
```

Train **after** pavement gate is usable. Fuse only behind roadness (`ensemble`).

---

## 6b. Pipeline C2 — Rutting (Head D, optional)

Same steps as crack; class `rutting` (not pavement — rutting is a surface defect **on** paved roads).

```powershell
# Drop YOLO export under datasets/rutting/roboflow/…  OR  incoming/rutting/{images,labels}

python tools/ml/prepare_dataset.py --purpose rutting
python tools/ml/train_yolo.py --purpose rutting
# default base yolo12s.pt → models/rutting/yolo_rutting_bench.pt
```

---

## 6c. Pipeline C3 — Waterlogging (Head E, optional)

Same steps as crack/rutting; class `waterlogging` (standing water on or beside the road surface).

```powershell
# Drop YOLO export under datasets/waterlogging/roboflow/…  OR  incoming/waterlogging/{images,labels}

python tools/ml/prepare_dataset.py --purpose waterlogging
python tools/ml/train_yolo.py --purpose waterlogging
# default base yolo12s.pt → models/waterlogging/yolo_waterlogging_bench.pt
```

Train **after** pavement gate is usable. Fuse only behind roadness (`ensemble`).

---

## 7. Pipeline D — Ensemble (eval only, no train)

```powershell
python tools/ml/eval_roads_only.py
```

Logic:

```
Head A (pavement)  →  roadness / mask
Head B (pothole)   →  boxes
Head C (crack)     →  optional boxes
Head D (rutting)      →  optional boxes
Head E (waterlogging) →  optional boxes

Accept detection ⇔  (mask overlap ≥ τ)  AND  (pothole | crack | rutting | waterlogging passes)
```

Optional secondary guardrail (prototype in eval later): drop frame if GPS > ~15 m from assigned corridor centerline.

**Do not** call `train_yolo.py --purpose ensemble` (script exits: eval-only).

---

## 8. Accuracy knobs (after the pipeline works)

### YOLO

| Knob | Default | Try |
|------|---------|-----|
| `--base` | purpose default / finetuned copy | `yolo12s` → `m` → `l` → `x` |
| `--epochs` | 80 | 100–150 |
| `--imgsz` | 640 | 768 / 960 |
| `--batch` | 16 | lower on laptop / for x |

### RF-DETR

| Knob | Default | Try |
|------|---------|-----|
| `--variant` | small | `base` / `large` if GPU ≥ 8 GB |
| `--epochs` | 50 | 80–100 |
| `--batch-size` | 4 | 2 if OOM |

### Data (usually wins over knobs)

- **500+** labeled images from **your** roads beats hyperparameter chasing.
- Hard negatives (shoulders, dirt, shadows) belong in the **off-road holdout** for FPR, and/or pavement labels — not only more pothole boxes.
- Crop-only pothole training **does not** fix full-frame off-road FPs; use the pavement gate.

---

## 9. How the portal sees your weights

`models_registry.py` scans `tools/ml/models/`:

- `*_trained.pt` → **Trained · \<stem\>**
- `yolo_pothole_bench.pt` / `rfdetr_pothole.pth` → bench slots
- Any other `.pt` → **Custom · \<name\>**
- Purpose subfolders (`models/pavement/…`) — drop / register as you add them for bench dropdown (extend registry when wiring pavement gate UI)

Production **Detection** page never reads these until you promote (§10).

---

## 10. Promote to production (manual only)

Only after roads-only (or pothole-only) metrics pass. Example for a YOLO pothole head:

```powershell
# Windows
copy model_testing\models\yolo12m_trained.pt artifacts/models\smartroad_ap.pt

# Linux (AceCloud)
cp tools/ml/models/yolo12m_trained.pt artifacts/models/smartroad_ap.pt
./scripts/services.sh restart detect
```

Or set `MODEL_PATH=…` in `.env` and restart **detect**.  
Full ops: [`docs/ops/DETECTION_MODEL.md`](../docs/ops/DETECTION_MODEL.md).

Pavement mask + ensemble filter must be wired into the **production** inference path (`pothole_detector` / detection service) in a **separate** change — training alone does not enable the gate.

---

## 11. Cheat sheet (copy-paste)

```powershell
# --- Pothole (legacy / default) ---
python tools/ml/setup_train.py
python tools/ml/train_yolo.py --purpose pothole --base tools/ml/models/yolo12m.pt
python tools/ml/train_rfdetr.py

# --- Pavement gate ---
python tools/ml/prepare_dataset.py --purpose pavement
python tools/ml/train_yolo.py --purpose pavement

# --- Crack (optional) ---
python tools/ml/prepare_dataset.py --purpose crack
python tools/ml/train_yolo.py --purpose crack

# --- Rutting (optional) ---
python tools/ml/prepare_dataset.py --purpose rutting
python tools/ml/train_yolo.py --purpose rutting

# --- Waterlogging (optional) ---
python tools/ml/prepare_dataset.py --purpose waterlogging
python tools/ml/train_yolo.py --purpose waterlogging

# --- Roads-only checklist ---
python tools/ml/eval_roads_only.py --overlap 0.70

# --- List everything ---
python tools/ml/download_data.py --list
python tools/ml/train_yolo.py --list-purposes
```

---

## 12. Troubleshooting

| Symptom | Check |
|---------|--------|
| `ROBOFLOW_API_KEY not found` | Root `.env` or `tools/ml/.env` |
| `No dataset for purpose=…` | `prepare_dataset` / drop `data.yaml` under that purpose’s `roboflow/` or `combined/` |
| `best.pt not found` | Look under `runs/.../weights/`; OOM → lower `--batch` |
| Dropdown missing weight | File under `tools/ml/models/`; restart detect; refresh portal |
| Live camera slow / CPU | Confirm detect service GPU (`DETECTION_MODEL.md`) |
| Off-road FPs in prod | Pavement gate not in production yet — train + eval in bench first |
| Report corridor `—` | Separate from training — reports show corridor as ad-hoc / `0 km` GPS covered when unassigned; analysed length uses S3 GPS log (regen via Reports → Generate) |
