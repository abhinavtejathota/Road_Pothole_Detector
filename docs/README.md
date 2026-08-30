# Documentation & notes layout

**Start here for how the whole system works:** [`../README.md`](../README.md)
(architecture, folder map, request flows, module interactions).

Root of the repo stays lean: only the Flask entrypoint, core Python modules,
schema, and install/config files that imports and deploy scripts expect.

| Folder | Contents |
|--------|----------|
| `docs/ops/` | How to run / deploy / operate the AceCloud server (tracked) |
| `docs/notes/` | Local scratch only — **gitignored** (`todo`, `prompt`, `cred`, build notes, …) |
| `docs/` (root) | Domain notes (`AP_CONSTITUENCY_SURVEY.md`, etc.) |
| `data/ref/` | Static reference data (e.g. LGD district list) |
| `scripts/` | CLI tools: bootstrap users, migrate JSON→DB, systemd, gunicorn config |
| `routes/` | Flask API Blueprint + services |
| `frontend/` | React SPA |
| `mobile_app/` | Field capture app (Expo) |
| `tools/ml/` | Training / bench weights pipeline |
| `tools/gis/` | GIS download/clip tools |
| `migrations/` | SQL migrations |
| `tests/` | Python tests |
| `deprecated_files/` | Old Gradio UIs — do not extend |

## Ops docs (start here on the server)

- [`ops/server.md`](ops/server.md) — always-on systemd, what to run / not run, debug
- [`ops/DATABASE.md`](ops/DATABASE.md) — PostgreSQL + PostGIS schema, spatial usage, roles, migrations
- [`ops/DETECTION_MODEL.md`](ops/DETECTION_MODEL.md) — production detection weights vs model testing bench
- [`ops/DEPLOYMENT.md`](ops/DEPLOYMENT.md) — full deploy
- [`ops/PROJECT_SETUP.md`](ops/PROJECT_SETUP.md) — local setup, GPS log format
- [`ops/MULTI_SERVICE.md`](ops/MULTI_SERVICE.md) — portal / upload / detect split + GPU

## Common script paths (from repo root)

```bash
python scripts/bootstrap_admin.py
python scripts/bootstrap_videographers.py
python scripts/migrate_survey_tracking_to_db.py --init-schema
sudo ./scripts/install_systemd.sh
```

## Left at repo root on purpose

`web_app.py`, `db_utils.py`, `model_loader.py`, `pothole_detector.py`, `s3_utils.py`,
`utils.py`, `ffmpeg_accel.py`, `schema.sql`, `requirements.txt`, `.env.example`,
`.env`, `CLAUDE.md`, and the `download_data.py` shim (forwards to `tools/ml/`).
Moving those would break imports and path resolution.
