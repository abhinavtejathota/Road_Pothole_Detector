# Road network & survey GIS setup

**`data/gis_states/` is gitignored and not pushed.** After cloning, every developer/server must download and build GIS data once (or copy it from a shared drive). Without it, Dashboard / Survey maps stay empty.

District-wise Andhra Pradesh + Telangana road networks live under `data/gis_states/`.  
`data/gis/` only holds runtime JSON (`survey_state.json`, `tracking_state.json`) — also gitignored; dual-written to Postgres when DB is configured.

## One-time setup (required after clone)

```powershell
cd d:\Docs\Problem Solving\Forks\smart_road_app
venv\Scripts\activate
pip install -r tools/gis/requirements-gis.txt

python tools/gis/download_districts.py
python tools/gis/download_state_roads.py --state both --all-roads
python tools/gis/clip_roads_to_districts.py --state both
python tools/gis/build_nh_overview.py
```

This downloads ~hundreds of MB of OSM extracts and builds per-district GeoJSON indexes. Expect several minutes (longer on first run / slow network).

Optional: `--source geofabrik` on `download_state_roads.py` uses the southern-zone PBF (~530 MB) then filters to each state.

Output layout:

```
data/gis_states/
  andhra/     districts, roads, road_segments, index/
  telangana/  same
  raw/        PBFs + LGD parquet
```

District ID list: `data/ref/district.txt`.

## Frontend (after pull)

```powershell
cd frontend
npm install
npm run build
cd ..
python web_app.py
```

Open http://localhost:5000 → **Survey Admin** (`/survey/admin`) or Dashboard map (Country → State → District).

## Dev mode (hot reload)

Terminal 1: `python web_app.py`  
Terminal 2: `cd frontend && npm run dev` → http://localhost:5173

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Blank / old UI after pull | `npm run build` in `frontend/` |
| District GIS missing / slow empty maps | Re-run the four `tools/gis` scripts above — data is **not** in git |
| State map shows no NH | Run `python tools/gis/build_nh_overview.py` |
| Local roads missing | Use `--all-roads` on download, re-clip |
| Videographer has no district | Set `state_id` + `district_id` on Users (see `migrations/20260711_district_videographers.sql`) |
| Survey/tracking not in DB | `python scripts/migrate_survey_tracking_to_db.py --init-schema` |
