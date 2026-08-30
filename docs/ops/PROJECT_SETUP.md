# SmartRoad – Operations Portal + Pothole Detection

Flask + React portal for road operations, field survey, and admin pothole detection (YOLOv12 + GPS + S3 + PostgreSQL).

---

## Prerequisites

| Requirement | Version / Notes |
|---|---|
| Python | 3.10+ recommended |
| Node.js | 18+ for `frontend/` build |
| CUDA (optional) | GPU inference is faster; CPU works too |
| AWS account | S3 input/processed buckets |
| PostgreSQL | Work orders, sessions, potholes |
| MediaInfo (optional) | Video GPS metadata via `pymediainfo` |

---

## 1. Clone / Copy the Project

Minimum layout:

```
smart_road_ap/
├── web_app.py              ← main entry (portal + API)
├── pothole_detector.py
├── routes/
├── frontend/               ← React SPA (npm run build)
├── artifacts/models/
│   └── smartroad_ap.pt
├── tools/ml/            ← bench inference + optional training
└── .env
```

> **Important:** `artifacts/models/smartroad_ap.pt` must exist for detection.

---

## 1b. Road network / GIS data (not in git)

Maps on Dashboard / Survey need `data/gis_states/` (Andhra + Telangana district road indexes). This folder is **gitignored** — clone alone is not enough.

```bash
pip install -r tools/gis/requirements-gis.txt
python tools/gis/download_districts.py
python tools/gis/download_state_roads.py --state both --all-roads
python tools/gis/clip_roads_to_districts.py --state both
python tools/gis/build_nh_overview.py
```

Full notes: `tools/gis/README.md`.

---

## 2. Create a Virtual Environment

```bash
python -m venv vnev
# Windows
vnev\Scripts\activate
# macOS / Linux
source vnev/bin/activate
```

---

## 3. Install YOLOv12

YOLOv12 is not yet on PyPI — install it directly from GitHub **before** installing other requirements:

```bash
pip install git+https://github.com/sunsmarterjie/yolov12.git
```

---

## 4. Install Python Dependencies

```bash
pip install -r requirements.txt
```

Full dependency list (from `requirements.txt`):

```
ultralytics
opencv-python
numpy
matplotlib
scipy
roboflow
supervision
exifread
pymediainfo
boto3>=1.34.0
python-dotenv>=1.0.0
```

> Gradio is not required for the portal.

> If you need to read GPS logs in CSV/XLSX format, also install:
> ```bash
> pip install pandas openpyxl
> ```

---

## 5. Configure the `.env` File

Copy the template and fill in secrets:

```bash
cp .env.example .env    # Linux/macOS
copy .env.example .env  # Windows
```

Minimum for a working portal: **`FLASK_SECRET_KEY`**, **`DB_*`**, and (optionally) **`AWS_*` / `S3_*`** for cloud media. See `.env.example` for the full list with comments.

Key variables:

```env
# Path to the fine-tuned YOLOv12 model weights
MODEL_PATH=artifacts/models/smartroad_ap.pt

# Postgres
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=smartroad_ap
DB_USER=postgres
DB_PASSWORD=your_password_here

# Roboflow API key (only needed to re-download / re-train)
ROBOFLOW_API_KEY=your_roboflow_private_key_here

# ---- AWS credentials (required for S3 integration) ----
AWS_ACCESS_KEY_ID=YOUR_AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY=YOUR_AWS_SECRET_ACCESS_KEY

# ---- Optional AWS settings ----
# AWS_SESSION_TOKEN=
# AWS_REGION=ap-south-1

# ---- S3 Bucket names ----
S3_INPUT_BUCKET=smart-road-videos
S3_PROCESSED_BUCKET=smart-road-videos-processed

# Optional: scope the S3 file listing to a prefix (folder), e.g. "videos/"
S3_INPUT_PREFIX=

# ---- Behaviour flags (optional) ----
# S3_PRESIGN_EXPIRY=604800    # presigned URL lifetime in seconds (default: 7 days)
# S3_PUBLIC=false             # true => public-read uploads (skips presigning)
# S3_MOVE_ON_SUCCESS=true     # move processed source to processed bucket after success
```

> **S3 is optional.** If you leave `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` empty, the app still works with local file uploads — you just won't see the S3 dropdown.

---

## 6. Run the portal

```bash
cd frontend && npm install && npm run build && cd ..
python web_app.py
```

Open http://127.0.0.1:5000 — log in, then use **Detection** / **Model testing** (admin) or **Upload** (videographer).

Legacy Gradio UIs: see `deprecated_files/README.md`.

---

## 7. Using detection (portal)

### Source options
| Option | How |
|---|---|
| **S3 file** | Click **Refresh** to list files from `S3_INPUT_BUCKET`, then pick one from the dropdown |
| **Local upload** | Drag-and-drop an image (`.jpg`, `.png`) or video (`.mp4`, `.avi`, `.mov`, `.mkv`) |

### GPS log (videos only)
- **Local video:** you **must** upload a GPS log (CSV, XLSX, or JSON) — see format below.
- **S3 video:** the app auto-detects a sibling GPS file with the same filename stem (e.g. `VID_001.mp4` → `VID_001_log.json` or `VID_001.csv`). Uploading a GPS log here overrides the sibling.

### GPS log format
One row per second of video. Accepted column names are case-insensitive.

**CSV / XLSX:**

| VideoSecond | Latitude | Longitude | Timestamp (optional) |
|---|---|---|---|
| 1 | 17.4125 | 78.5123 | 2024-01-15T10:00:01 |
| 2 | 17.4126 | 78.5124 | 2024-01-15T10:00:02 |

**JSON (array of objects):**
```json
[
  {"VideoSecond": 1, "Latitude": 17.4125, "Longitude": 78.5123, "Timestamp": "2024-01-15T10:00:01"},
  {"VideoSecond": 2, "Latitude": 17.4126, "Longitude": 78.5124}
]
```

**JSON (dict keyed by second):**
```json
{
  "1": {"latitude": 17.4125, "longitude": 78.5123},
  "2": {"latitude": 17.4126, "longitude": 78.5124}
}
```

### Capture mode
- **walking** – stronger de-duplication (slower camera movement, lots of overlapping frames).
- **vehicle** – looser de-duplication (faster movement, fewer overlapping frames).

### Output
| Output | Description |
|---|---|
| Annotated image / video | Bounding boxes with class, confidence, and severity label |
| Detections table | Per-detection: class, confidence, severity, coordinates, GPS lat/lon, timestamp, Google Maps link, S3 URL |
| Marked frames (video only) | Up to 25 JPEG frames with detections, downloadable as a ZIP |
| Map links | Clickable Google Maps links for each detection |

---

## 8. Severity Classification

Severity is computed from bounding-box area and vertical position (proximity to camera):

| Score | Severity |
|---|---|
| < 0.20 | Low |
| 0.20 – 0.40 | Medium |
| ≥ 0.40 | High |

Formula: `score = 0.6 × (box_area / frame_height²) + 0.4 × (y_center / frame_height)`

---

## 9. (Optional) Re-train the Model

Only needed if you want to train on new data.

### 9a. Download the dataset

```bash
python download_data.py
```

Requires `ROBOFLOW_API_KEY` in `.env`. Downloads the [Roboflow Pothole Detection v2](https://universe.roboflow.com/aegis/pothole-detection-i00zy/dataset/2) dataset into:

```
Pothole-Detection--2/
├── data.yaml
├── train/
├── valid/
└── test/
```

### 9b. Train

```bash
python tools/ml/train_yolo.py
# or
python tools/ml/train_rfdetr.py
```

Default YOLO settings are in `tools/ml/train_yolo.py`. Legacy root script: `deprecated_files/legacy_root_train.py`.

After training, copy the best weights to the expected path:

```bash
# After training completes:
copy runs\detect\train\weights\best.pt artifacts/models\smartroad_ap.pt
# macOS / Linux:
cp runs/detect/train/weights/best.pt artifacts/models/smartroad_ap.pt
```

---

## 10. Project File Overview

| File | Purpose |
|---|---|
| `web_app.py` | Flask + React portal — main entry |
| `routes/` | JSON API (detection, survey, tasks, …) |
| `deprecated_files/` | Legacy Gradio UIs and old scripts |
| `pothole_detector.py` | Core detection pipeline (image + video, GPS mapping, S3 upload) |
| `model_loader.py` | Loads the YOLOv12 `.pt` weights, respects `MODEL_PATH` env var |
| `utils.py` | Severity heuristic (`severity_from_area_and_position`) |
| `s3_utils.py` | All AWS S3 operations (list, download, upload, presign, move) |
| `download_data.py` | Downloads Roboflow dataset for training |
| `tools/ml/train_yolo.py` | Bench / production training — see `tools/ml/TRAINING.md` |
| `artifacts/models/smartroad_ap.pt` | Trained model weights (must be present to run the app) |
| `.env` | Local config — credentials and paths (never commit this file) |
| `outputs/` | Local output directory for annotated images/videos and frames |

---

## 11. AWS S3 Bucket Setup

Create two S3 buckets in the `ap-south-1` (Mumbai) region, or whichever region you set in `.env`:

| Bucket | Purpose |
|---|---|
| `smart-road-videos` | Upload source videos/images here before processing |
| `smart-road-videos-processed` | App writes annotated outputs here; source files are moved here after success |

**IAM permissions required** for the credentials in `.env`:
- `s3:ListBucket` on both buckets
- `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` on both buckets
- `s3:CopyObject` on the processed bucket

---

## 12. Troubleshooting

| Problem | Fix |
|---|---|
| `FileNotFoundError: Model file not found` | Check that `artifacts/models/smartroad_ap.pt` exists; verify `MODEL_PATH` in `.env` |
| `S3 not configured` banner | Add `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` to `.env` |
| `No GPS log` error on video | Upload a GPS log CSV/XLSX/JSON or place a sibling file in S3 |
| `pandas is required` error | Run `pip install pandas openpyxl` |
| `pymediainfo` not working | Install the native MediaInfo binary for your OS (not just the Python package) |
| CUDA out of memory | Reduce batch in `tools/ml/train_yolo.py` or use CPU inference |
| YOLOv12 import error | Re-run `pip install git+https://github.com/sunsmarterjie/yolov12.git` |
