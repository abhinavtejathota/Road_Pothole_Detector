# Runtime: Supabase free tier

- Remote DB is Supabase **free** (session pooler). Connection budget is tiny.
- Local run = **one process only**: `python web_app.py`.
- Do **not** start portal + upload + detect separately, or `./scripts/services.sh`, against this DB.
- Keep **pool max ≥ Waitress threads** (e.g. `WAITRESS_THREADS=2`, `DB_POOL_MAX=6`). Do not stampede.
- Checkout uses a semaphore sized to the pool — never abandon timed-out `getconn()` waiters (`backend/db/connection.py`).
- Always use TLS (`DB_SSLMODE=require`). Do not set `DB_FORCE_LOCALHOST=1` for Supabase.
- Schema changes: `python scripts/apply_db_migrations.py` (idempotent). Do not invent parallel DDL.
- GIS lives on **disk** (`data/gis_states/`). Restore with `tools/gis/*.py`. Never run `scripts/seed_roads_registry.py` against free tier.
- Keep `SURVEY_JSON_MIRROR=0` and `TRACKING_JSON_MIRROR=0`. Do not bulk-insert trails/roads/potholes for demos.
- Keep `DASHBOARD_LIGHT=1` + `DASHBOARD_MAP_LIMIT=0` on free tier so DevAdmin dashboard stays fast.
- Multi-service split is for AceCloud / self-hosted Postgres only — see README §10.
