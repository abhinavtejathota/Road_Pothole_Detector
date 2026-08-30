# SmartRoad multi-service deploy (nginx + nohup)

One repo, AceCloud box, **four independent processes** (no systemd required).

| Process | Command | Port | Failure blast radius |
|---------|---------|------|----------------------|
| Portal | `web_app.py` | **5015** internal | Login / SPA / dashboard / survey — **not** upload or YOLO |
| Upload | `upload_app.py` | **5006** | Chunk ingest only |
| Finalize | `smartroad_worker.py` | none | ffmpeg/S3 queue only |
| Detect | `detect_app.py` | **5007** | YOLO / model-bench only |

Public URL stays **`http://45.194.2.247:5005`** (nginx).  
**If one service dies, restart only that one** — do not stop the others.

### GPU isolation (split mode)

| Process | `YOLO_DEVICE` | Handles |
|---------|---------------|---------|
| **portal** (`5015`) | `cpu` (forced) | Dashboard, login, survey, tasks — **no CUDA** |
| **upload** (`5006`) | `cpu` (forced) | Chunk ingest only |
| **finalize** (worker) | n/a | ffmpeg concat / NVENC → S3 (CPU) |
| **detect** (`5007`) | `0` (GPU) | `/api/detection/*` + `/api/model-bench/*` |

nginx routes detection + model-bench to **detect** only (`scripts/install_nginx_smartroad.sh`).

Recommended `.env` on AceCloud (A6000):

```bash
DETECT_YOLO_DEVICE=0
POTHOLE_HALF=1              # FP16 Tensor Cores — required for YOLO12x throughput
POTHOLE_CPU_THREADS=3       # decode/draw; leaves cores for portal
YOLO_WORKERS=0              # train DataLoader stays on CPU; 0 = no extra worker procs
FINALIZE_CONCURRENCY=1      # one ffmpeg finalize at a time
```

Seed large YOLO12 weights on the **server** (not needed on dev laptop):

```bash
source venv/bin/activate
python tools/ml/seed_weights.py --yolo12-only   # n/s/m/l/x
# or full: python tools/ml/seed_weights.py
./scripts/services.sh restart detect
curl -s http://127.0.0.1:5007/api/health | tr ',' '\n' | grep yolo
# expect: "yolo_device":"0"
```

Auth:
- **Web:** cookie + **1h idle** (`SESSION_IDLE_TIMEOUT_S=3600`)
- **Mobile:** Bearer JWT (~7d); logout bumps `users.token_version` (server revoke)

---

## One-time setup

### 0. Code + frontend

```bash
cd /app/Smart_Road_Rec/SmartRoadApp
source venv/bin/activate
cd frontend && npm install && npm run build && cd ..
chmod +x scripts/services.sh scripts/start_services_nohup.sh scripts/stop_services_nohup.sh
```

### 0b. `.env` (shared by every process)

```bash
DB_HOST=127.0.0.1
DB_FORCE_LOCALHOST=1
FLASK_SECRET_KEY=<same-secret-on-all-procs>
# JWT_SECRET=   # optional; defaults to FLASK_SECRET_KEY — MUST match across procs
SESSION_IDLE_TIMEOUT_S=3600
JWT_TTL_S=604800
```

Schema auto-adds `users.token_version` on first `init_db()`. Prefer running explicitly once on AceCloud:

```bash
cd /app/Smart_Road_Rec/SmartRoadApp
source venv/bin/activate
# .env must use DB_HOST=127.0.0.1 on the box
python scripts/apply_db_migrations.py
```

Or SQL only:

```bash
psql -h 127.0.0.1 -U postgres -d smartroad_ap -f migrations/20260715_user_token_version.sql
```

### 1. Nginx (once)

Prefer the script (writes the site file + `nginx -t` + start/reload):

```bash
cd /app/Smart_Road_Rec/SmartRoadApp
sudo bash scripts/install_nginx_smartroad.sh
```

Config lands at `/etc/nginx/sites-available/smartroad` (listen **5005** → 5015/5006/5007).

### 2. Start all (split)

```bash
./scripts/services.sh start
./scripts/services.sh status
./scripts/services.sh smoke
```

Logs: `data/logs/{portal,upload,finalize,detect}.log`  
PIDs: `data/pids/*.pid`

---

## Day-to-day recovery (do NOT reinstall everything)

| Symptom | Action |
|---------|--------|
| Login / SPA dead, upload OK | `./scripts/services.sh restart portal` |
| Phone chunk upload 502 / timeout | `./scripts/services.sh restart upload` |
| Videos stuck pending finalize | `./scripts/services.sh restart finalize` |
| Detection / model-bench dead | `./scripts/services.sh restart detect` |
| Who is up? | `./scripts/services.sh status` |
| Full health | `./scripts/services.sh smoke` |

Rebuild frontend only when UI changed:

```bash
cd frontend && npm run build && cd ..
./scripts/services.sh restart portal
```

Nginx config change:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### Nightly restart (00:00 IST)

Purges wedged DB pools / Waitress threads automatically:

```bash
sudo bash scripts/install_nightly_restart.sh
# or:  ./scripts/services.sh install-nightly
```

Cron file: `/etc/cron.d/smartroad-nightly` (`CRON_TZ=Asia/Kolkata`, `0 0 * * *` → `./scripts/services.sh restart`).  
Log: `data/logs/nightly_restart.log`. Remove with `./scripts/services.sh uninstall-nightly`.

---

## Emergency: monolith (one process, no nginx)

When split is fighting you, fall back:

```bash
./scripts/services.sh stop
sudo fuser -k 5005/tcp 2>/dev/null || true
DEPLOY_MODE=mono BIND_HOST=0.0.0.0 ./scripts/services.sh mono
# or:
# FLASK_PORT=5005 WAITRESS_HOST=0.0.0.0 SMARTROAD_SERVICE=portal \
#   nohup python -u web_app.py >>data/logs/portal.log 2>&1 &
```

Portal in monolith mode still serves `/api/upload` and `/api/detection` itself.

---

## Verify auth once

```bash
# Mobile JWT
curl -s -X POST 'http://127.0.0.1:5005/api/auth/login' \
  -H 'Content-Type: application/json' -H 'X-Client: mobile' \
  -d '{"username":"USER","password":"PASS"}'
# → access_token

# Web cookie (browser) — clear site data once, log in at /login
```

Offline factory smoke (no servers):

```bash
python scripts/smoke_multi_service.py
```

---

## Clients

- **Web:** clear cookies once after cutover.
- **Phone:** rebuild APK (JWT). Old cookie APKs will fail login against JWT-only mobile path.

---

## Related ops notes

- Crypto-miner / compromised `postgres` OS user cleanup: [`MINER_CLEANUP.md`](./MINER_CLEANUP.md)
