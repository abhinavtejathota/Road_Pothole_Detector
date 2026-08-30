# Detection model vs model testing

Two separate paths in SmartRoad. Do not confuse them.

| | **Production detection** | **Model testing bench** |
|---|--------------------------|-------------------------|
| **Portal page** | Admin → **Detection** (`/detection`) | Admin → **Model testing** (`/model-bench`) |
| **API** | `/api/detection/*` | `/api/model-bench/*` |
| **Weights** | `MODEL_PATH` → usually `artifacts/models/smartroad_ap.pt` | Dropdown from `tools/ml/models/` via `models_registry.py` |
| **Purpose** | Real pothole pipeline (video upload, DB, work orders) | Compare architectures / ad-hoc weights |
| **Changing model** | Replace prod `.pt` or set `MODEL_PATH` (this doc) | Drop `.pt` in `tools/ml/models/` or use dropdown |

---

## Where does model testing actually run?

The **page** is part of the React SPA (served by **portal** on `:5015`).

The **API** is not handled by portal in split deploy:

```
Browser  →  nginx :5005
              ├─ GET /model-bench        → portal (HTML/JS only)
              └─ POST /api/model-bench/* → detect :5007 (GPU)
```

Same pattern for production detection:

```
              ├─ GET /detection          → portal (SPA)
              └─ POST /api/detection/*   → detect :5007 (GPU)
```

Portal is forced **`YOLO_DEVICE=cpu`** in `scripts/services.sh`.  
Only **`detect_app.py`** (`:5007`) loads CUDA for YOLO.

Verify after deploy:

```bash
curl -s http://127.0.0.1:5015/api/health | tr ',' '\n' | grep yolo   # expect cpu
curl -s http://127.0.0.1:5007/api/health | tr ',' '\n' | grep yolo   # expect 0
```

---

## Change the production video-detection model

Production always loads via `model_loader.load_model()` → `pothole_detector.py` → `/api/detection/run`.

### Option A — replace default file (most common)

On the server:

```bash
cd /app/Smart_Road_Rec/SmartRoadApp   # your repo path
source venv/bin/activate

# Backup current weights
cp artifacts/models/smartroad_ap.pt artifacts/models/smartroad_ap.pt.bak

# Install new weights (from training output)
cp /path/to/best.pt artifacts/models/smartroad_ap.pt

# Reload GPU process only (portal can stay up)
./scripts/services.sh restart detect

# Smoke
python -c "import model_loader; m=model_loader.load_model(); print('ok', model_loader.resolve_yolo_device())"
curl -s http://127.0.0.1:5007/api/health | tr ',' '\n' | grep yolo
```

No frontend rebuild needed. Portal **Detection** page automatically uses the new weights on the next run.

### Option B — point `MODEL_PATH` at another file

In `.env` (forward slashes on Linux):

```bash
MODEL_PATH=artifacts/models/my_new_run.pt
# or absolute:
# MODEL_PATH=/app/Smart_Road_Rec/SmartRoadApp/artifacts/models/my_new_run.pt
```

Then:

```bash
./scripts/services.sh restart detect
```

Resolution order (`model_loader.py`):

1. Explicit path argument (internal)
2. `MODEL_PATH` env var
3. `artifacts/models/smartroad_ap.pt` (default)

### Option C — promote a bench-trained weight to production

After training on the server:

```bash
cp tools/ml/models/yolo_pothole_bench.pt artifacts/models/smartroad_ap.pt
./scripts/services.sh restart detect
```

`tools/ml/` weights stay isolated until you **copy** them into `artifacts/models/` or set `MODEL_PATH`.

---

## GPU / performance env (production detection)

These apply to **`detect`** (both `/api/detection` and `/api/model-bench`):

| Variable | Typical AceCloud value | Effect |
|----------|------------------------|--------|
| `DETECT_YOLO_DEVICE` / `YOLO_DEVICE` | `0` | CUDA device for YOLO |
| `POTHOLE_HALF` | `1` | FP16 on GPU (important for large models) |
| `POTHOLE_CPU_THREADS` | `3` | OpenCV/ffmpeg decode threads; leave cores for portal |
| `POTHOLE_IMGSZ` | (optional, e.g. `640`) | Inference resolution |
| `POTHOLE_FRAME_STRIDE` | (optional, e.g. `5`) | Run YOLO every Nth frame on long video |
| `POTHOLE_CHUNK_SECONDS` | default `18` | Parallel video chunk size (GPU: keep parallel off) |

Portal / upload are **not** affected — they never call `load_model()`.

`FINALIZE_CONCURRENCY=1` affects the **finalize worker** (phone upload concat), not which YOLO weights detection uses.

---

## Model testing–only changes (do not change production model)

These affect the **bench dropdown** and `/api/model-bench` only:

- `tools/ml/models_registry.py` — catalog (yolo12m/l/x, custom `best.pt`, etc.)
- `tools/ml/seed_weights.py` — download COCO bases into `tools/ml/models/`
- `tools/ml/inference.py` — live camera / single-image bench inference
- Weights under `tools/ml/models/*.pt` (except when you explicitly copy to prod)

Recent YOLO12 M/L/X entries and bench GPU wiring **do not** change production detection until you copy weights or set `MODEL_PATH`.

---

## Shared code (both paths)

| Module | Used by |
|--------|---------|
| `model_loader.py` | Production detection |
| `tools/ml/yolo12_aattn_compat.py` | Production + bench (Turbo/V1 `AAttn` fix) |
| `pothole_detector.py` | Production detection only |
| `tools/ml/inference.py` | Model bench only |

If a new `.pt` throws `AAttn … qk/qkv`, pull latest code (compat patch) and restart **detect**.

---

## Monolith fallback

If you run a single process (`DEPLOY_MODE=mono` or `python web_app.py` only):

- Portal UI and detection API share one process.
- `YOLO_DEVICE` from `.env` applies to **everything** (no CPU/GPU split).
- Prefer split deploy on AceCloud so login/dashboard stay on CPU while YOLO uses GPU.

---

## Quick checklist

| Task | Action |
|------|--------|
| New prod model for `/detection` video runs | Replace `artifacts/models/smartroad_ap.pt` or set `MODEL_PATH` → `restart detect` |
| Try `best.pt` without touching prod | Copy to `tools/ml/models/best.pt` → use **Model testing** dropdown |
| Model testing page 502 / timeout | `./scripts/services.sh restart detect` (not portal) |
| Portal slow but detection OK | `./scripts/services.sh restart portal` |
| Confirm GPU in use | `curl …/api/health` on **:5007** → `"yolo_device":"0"` |

See also: [`MULTI_SERVICE.md`](./MULTI_SERVICE.md), [`server.md`](./server.md), [`DEPLOYMENT.md`](./DEPLOYMENT.md) §4.
