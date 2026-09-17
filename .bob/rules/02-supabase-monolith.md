# Runtime: Supabase free tier

- Remote DB is Supabase **free** (session pooler). Connection budget is tiny.
- Local run = **one process only**: `python web_app.py`.
- Do **not** start portal + upload + detect separately, or `./scripts/services.sh`, against this DB.
- Keep pools small (`DB_POOL_MAX` / `DB_POOL_HARD_CAP` ≤ 4, low `WAITRESS_THREADS`). Do not "tune up" pools.
- Always use TLS (`DB_SSLMODE=require`). Do not set `DB_FORCE_LOCALHOST=1` for Supabase.
- Schema changes: `python scripts/apply_db_migrations.py` (idempotent). Do not invent parallel DDL.
- Multi-service split is for AceCloud / self-hosted Postgres only — see README §10.
