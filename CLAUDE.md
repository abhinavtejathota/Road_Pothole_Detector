# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

SmartRoad AP — a Flask + React portal for road-maintenance operations in Andhra Pradesh: AI pothole detection (YOLOv12), vendor/work-order management, field survey tracking, and GIS road-network visualization. Single Flask process (`web_app.py`) serves both the JSON API and the built React SPA.

## Commands

### Backend
```bash
# Windows
vnev\Scripts\activate
python web_app.py                          # runs on http://127.0.0.1:5000

# Create DB tables (idempotent; also runs automatically on app startup if DB is configured)
python -c "import db_utils; db_utils.init_db()"

# Create the first admin user (change password immediately)
python scripts/bootstrap_admin.py
```

### Frontend
```bash
cd frontend
npm install
npm run build      # production build -> frontend/dist, served by Flask
npm run dev         # Vite dev server on :5173 (proxies API to Flask on :5000; run both concurrently)
```
There is no lint/test script configured in `frontend/package.json` (`dev`, `build`, `preview` only) and no Python test suite in this repo — verify changes by running the app.

### Model training (optional, only needed to produce new weights)
```bash
pip install git+https://github.com/sunsmarterjie/yolov12.git   # not on PyPI, install before requirements.txt
pip install -r requirements.txt
python download_data.py                     # needs ROBOFLOW_API_KEY in .env
python tools/ml/train_yolo.py          # or train_rfdetr.py — see tools/ml/TRAINING.md
```
Production inference model must land at `artifacts/models/smartroad_ap.pt` (resolution order: explicit path arg → `MODEL_PATH` env var → that default, see `model_loader.py`). Bench/testing weights for the in-app "Model testing" page go in `tools/ml/models/` instead and are picked up automatically via `tools/ml/models_registry.py`.

### Road network / GIS data (one-time, `data/gis_states/` is gitignored)
```bash
pip install -r tools/gis/requirements-gis.txt
python tools/gis/download_districts.py
python tools/gis/download_state_roads.py --state both --all-roads
python tools/gis/clip_roads_to_districts.py --state both
python tools/gis/build_nh_overview.py
```
Videographers are linked by `users.state_id` (1=AP, 2=TG) + `users.district_id` (LGD code). See `data/ref/district.txt` and `migrations/20260711_district_videographers.sql`.

Daily survey assignments + GPS tracking dual-write to Postgres (`survey_daily_assignments`, `tracking_sessions`, …) and JSON mirrors under `data/gis/`. One-shot import: `python scripts/migrate_survey_tracking_to_db.py --init-schema`.

Live tracking pings are lightweight by design: the mobile app piggybacks the current GPS fix on each ~60s field-capture chunk upload (`routes/api.py: upload_session_chunk` calls `tracking_service.record_ping`) and only sends a separate `/api/tracking/ping` as an infrequent (~20s) keepalive — don't reintroduce a high-frequency standalone ping loop, it previously caused CLOSE_WAIT socket buildup under gunicorn.

## Architecture

**Entry point**: `web_app.py` — imports `smartroad_path` (adds `backend/` to sys.path), then `routes.app_factory.create_app()`. Flask-Login, `routes.api.api_bp` at `/api`, built SPA from `frontend/dist`. DB schema init on startup when configured.

**Backend layout** (`backend/routes/`): `api.py` is a single Blueprint for all `/api/*`; thin handlers delegate to `*_service.py` modules:
- `detection_service.py` — pothole detection pipeline orchestration (S3 or local upload → `pothole_detector.py` → DB)
- `model_bench_service.py` — ad-hoc model testing/comparison against `tools/ml/` weights
- `survey_service.py` — field survey / road-segment coverage tracking
- `field_upload_service.py` — videographer video/GPS-log uploads, including the chunked capture session flow (`init_chunk_session`/`append_chunk`/`finalize_chunk_session`) the mobile app streams ~60s clips through. `append_chunk` accepts chunks regardless of arrival order (a dropped/retried chunk must not desync the rest of the session) and tracks `missing_indices`, surfaced in the finalize response rather than failing silently
- `validation.py` — completion-validation logic (GPS distance + timestamp + re-run YOLO on "after" photos)
- `constants.py` — `VALID_TRANSITIONS`, the work-order status state machine (Created → Allocated → WIP → Completed → Verified/Failed)
- `user_model.py` — Flask-Login `User` wrapping the `users` table, with role-check helpers (`is_admin`, `is_supervisor`, `is_allocator`, `is_vendor_role`, `is_videographer`)

**Data layer**: `db_utils.py` (shim → `backend/db/`) is the only Postgres access. Schema: `backend/schema.sql`.

**Tools**: `tools/gis/` (road network download/clip), `tools/ml/` (training & model bench). Clean regeneratable data: `python scripts/clean_downloaded_data.py --yes`.

**Mobile app** (`mobile_app/`): Expo field-capture — ~60s chunks to `/api/upload/session/*`. See `mobile_app/AGENTS.md` before camera changes.

**Config**: `.env` at repo root (template: `.env.example`). Never commit secrets.

## Notes for changes here

- `model_loader.py` resolves weights from `artifacts/models/smartroad_ap.pt` (or `MODEL_PATH`). Anchor new path-resolution code to repo root via `smartroad_path.ROOT`.
- The work-order status machine (`routes/constants.py: VALID_TRANSITIONS`) is enforced server-side in the `/api/tasks/<id>/status` handler — any new status must be added there and to `schema.sql`'s comment, and any transition change needs both backend and frontend (`TaskDetail.jsx`/`StatusBadge.jsx`) updates.
- GPS log parsing (CSV/XLSX/JSON, case-insensitive columns) is handled in the detection pipeline — see `docs/ops/PROJECT_SETUP.md` §"GPS log format" for the accepted shapes before changing upload handling.
- `db_utils.init_db()` runs on every `create_app()` call and takes a Postgres advisory lock so concurrent callers (multiple gunicorn workers at boot) serialize instead of deadlocking on the schema DDL; production should still run gunicorn with `--preload` (see `docs/ops/DEPLOYMENT.md`) so it only executes once.
