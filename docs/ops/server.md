# SmartRoad — AceCloud server runbook

This is the ops guide for keeping `web_app.py` **always on** on the AceCloud box
(~16 CPU / ~62 GB RAM / RTX A6000). Follow this file whenever the portal feels
dead, login hangs after an upload, or you are deciding between `systemd` and
the bash script.

---

## Sessions (web idle logout)

Web cookies use `sr_session` (not Flask’s default `session`). Deploying that
rename **invalidates every old browser/phone login** immediately.

| Knob (in `.env`) | Default | Meaning |
|------------------|---------|---------|
| `SESSION_IDLE_TIMEOUT_S` | `1800` | Web: logout after this many seconds idle |
| `SESSION_IDLE_TIMEOUT_MOBILE_S` | `604800` (7d) | Phone apps (`X-Client: mobile`) |
| `SESSION_EPOCH` | `20260715b` | Bump + restart to force everyone to re-login |

The SPA also logs the browser out after the same idle window. Rebuild frontend after pulling: `cd frontend && npm run build`.

---

## What not to run

- **Do not** leave a long-lived SSH terminal with `python web_app.py` in the
  foreground and rely on that — closing SSH kills (or orphans) the app.
- **Do not** “spin down” the app when idle. Cold start + DB/GPU warm-up is what
  made first login/upload feel broken. Idle listening is cheap; keep it up.
- **Do not** source `.env.example` into the shell as an overlay on top of
  bash/gunicorn/waitress experiments — those OMP/`SMARTROAD_SERVER` overlays
  made the bash path feel ~10× slower than bare Werkzeug. The run script loads
  **`.env` only** and applies AceCloud defaults itself.
- **Do not** use multi-worker gunicorn for field uploads. Stalled cellular body
  reads pin workers; Werkzeug threaded (what we run) is the supported model.
- **Do not** run `./scripts/run_smartroad.sh restart` **after** systemd is
  installed (see golden rule).

---

## One-time install (recommended)

On the Linux AceCloud host, from the repo root:

```bash
cd /path/to/smart_road_app
sudo chmod +x scripts/run_smartroad.sh scripts/install_systemd.sh scripts/smartroad_worker.py
sudo ./scripts/install_systemd.sh
```

That installs **two** services (same repo, separate processes):

1. `smartroad.service` — Flask portal (login, `/detection`, phone→disk chunks)  
2. `smartroad-worker.service` — finalize queue + **sequential auto-detect** (`Nice=10`, separate from Flask)

```
Phone ──chunks──► Flask (disk) ──job──► data/finalize_queue/pending/
Browser /detection ◄── Flask (light)              │
                              smartroad-worker ◄──┘  (finalize + S3 + YOLO queue)
                                                    │
                    S3 input scan every 60s ────────┘  data/detect_queue/pending/
```

### Verify

```bash
sudo systemctl status smartroad smartroad-worker
curl -s http://127.0.0.1:5005/api/health
# expect finalize_queue + detect_queue + auto_detect flags
```

Healthy JSON should include `"yolo_device":"0"`, `"ffmpeg_encode":"nvenc"`, `finalize_queue`, and `detect_queue` (pending/running/done/failed). If `detect_queue.pending` grows and `running` stays 0, **`smartroad-worker` is not running** or crashed on startup.

You can close SSH. After reboot the service comes back by itself.

---

## Day-to-day commands (systemd installed)

| Action | Command |
|--------|---------|
| Status | `sudo systemctl status smartroad smartroad-worker` |
| Restart both | `sudo systemctl restart smartroad smartroad-worker` |
| Stop (maintenance) | `sudo systemctl stop smartroad smartroad-worker` |
| Start | `sudo systemctl start smartroad smartroad-worker` |
| Portal logs | `sudo journalctl -u smartroad -f` |
| Finalize worker logs | `sudo journalctl -u smartroad-worker -f` |
| App log file | `tail -f data/logs/smartroad.log` |
| Watchdog log | `tail -f data/logs/watchdog.log` |
| Queue depth | `curl -s http://127.0.0.1:5005/api/health \| jq '{finalize:.finalize_queue,detect:.detect_queue,auto:.auto_detect}'` |
| Health (local) | `curl -s http://127.0.0.1:5005/api/health` |
| Health + DB | `curl -s 'http://127.0.0.1:5005/api/health?deep=1'` |
| Public smoke | `curl -s http://45.194.2.247:5005/api/health` |

Port comes from `.env` `FLASK_PORT` (default **5005**).

---

## Fallback: bash only (no systemd)

Use this **only** if you have not installed the unit (or after
`systemctl disable --now smartroad`):

```bash
./scripts/run_smartroad.sh restart
./scripts/run_smartroad.sh status
./scripts/run_smartroad.sh logs    # ctrl-c leaves app running
```

- Starts `nohup python -u web_app.py` + a background health watchdog  
- Survives closing the terminal, **does not** survive a full host reboot unless
  you also install systemd  

---

## Architecture (why things behave this way)

```
Phone (LTE) ──chunk-bin──► Flask (Werkzeug threaded)
                              │
                              ├─ light always: /api/health, /api/auth/login
                              ├─ heavy gated: uploads / survey / detection
                              │
                              ├─ finalize (bg): ffmpeg copy → else NVENC → S3
                              └─ detection: YOLO on CUDA (FP16), CPU decode/draw
```

Recovery layers:

1. **Admission control** — login/health never wait behind saturated uploads  
2. **Supervise / watchdog** — dead process → immediate restart; hung health →
   soft-restart (keeps logs)  
3. **systemd `Restart=always`** — if the supervisor itself dies, systemd brings
   it back; also starts on boot  

Heavy work is capped so one upload/finalize cannot melt login:

| Env (defaults set by runner) | Role |
|------------------------------|------|
| `YOLO_DEVICE=0` | YOLO on first GPU |
| `POTHOLE_HALF=1` | FP16 infer on A6000 |
| `FFMPEG_HW=auto` / `FFMPEG_PREFER_SYSTEM=1` | Prefer system ffmpeg with NVENC |
| `FINALIZE_CONCURRENCY=1` | One ffmpeg finalize at a time (portal stays responsive) |
| `CHUNK_UPLOAD_CONCURRENCY=8` | Concurrent phone→Flask body reads |
| `SMARTROAD_HEAVY_CONCURRENCY=24` | Cap on non-light API work |
| `S3_UPLOAD_CONCURRENCY=48` | Parallel multipart parts to AWS |
| `POTHOLE_CPU_THREADS=3` | OpenCV/BLAS cores for decode; leave headroom for portal |

**Split deploy (`./scripts/services.sh start`):** portal and upload **force `YOLO_DEVICE=cpu`**;
only the **detect** process (`:5007`) runs YOLO on CUDA for `/api/detection` and
`/api/model-bench`. See [`MULTI_SERVICE.md`](./MULTI_SERVICE.md) and [`DETECTION_MODEL.md`](./DETECTION_MODEL.md).

Overrides belong in **`.env`** (never commit secrets). See `.env.example`
as a reference only — the bash/systemd path does **not** auto-source it.

---

## Debug checklist

### 1. Is the service up?

```bash
sudo systemctl is-active smartroad
sudo systemctl status smartroad --no-pager
ss -lptn 'sport = :5005'    # or: lsof -iTCP:5005 -sTCP:LISTEN
curl -s -o /dev/null -w '%{http_code}\n' --max-time 5 http://127.0.0.1:5005/api/health
```

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `inactive` / `failed` | Crash loop, bad `.env`, missing venv | `journalctl -u smartroad -n 100 --no-pager` then fix and `sudo systemctl restart smartroad` |
| Port free, systemd active | Supervise waiting / start retry | `tail -f data/logs/watchdog.log` |
| Port busy, health `000` | Hung Werkzeug / wedged workers | `sudo systemctl restart smartroad` |
| Health `200` but browser fails | Client URL / firewall / wrong host | Hit public IP:`5005`; check security group |

### 2. Login dies after an upload

This was the classic failure mode: finalize (ffmpeg/S3) + stalled LTE reads
starved the same process login uses.

1. Confirm you are on current code + systemd supervise  
2. `curl -s http://127.0.0.1:5005/api/health` — if this hangs, restart service  
3. Check `heavy_inflight` in health JSON — high is OK briefly; login must still work  
4. `tail -100 data/logs/smartroad.log` — look for `[chunk]`, NVENC, S3 errors  
5. If it keeps recurring: `sudo systemctl restart smartroad` and note
   `data/logs/watchdog.log` for `RESTART` lines (auto-recovery is working)

Heavy endpoints may return **503** `server_busy` under load — clients should
retry; login/health stay on the light path.

### 3. GPU / NVENC

```bash
nvidia-smi
ffmpeg -hide_banner -encoders | grep nvenc
# Expect h264_nvenc (AceCloud already has this)
curl -s http://127.0.0.1:5005/api/health | tr ',' '\n' | grep -E 'yolo|ffmpeg'
```

| Health field | Bad value | Fix |
|--------------|-----------|-----|
| `yolo_device` | `cpu` | Install CUDA torch; set `YOLO_DEVICE=0` in `.env`; restart service |
| `ffmpeg_encode` | `libx264` | Ensure **system** `ffmpeg` with NVENC is first on `PATH` (pip `imageio-ffmpeg` often has no NVENC). `FFMPEG_PREFER_SYSTEM=1`. Restart |

On boot the app logs a line like:

```text
Accel: YOLO_DEVICE=0 ffmpeg=nvenc cpus=16
```

### 4. Deep DB check

```bash
curl -s 'http://127.0.0.1:5005/api/health?deep=1'
```

- `"db":"ok"` — pool reachable  
- `"db":"error"` — fix `.env` DB_* / network / Postgres; login will 503 until fixed  

### 5. Conflicting processes

```bash
# See who owns the port
sudo lsof -iTCP:5005 -sTCP:LISTEN

# If both systemd and an old nohup exist — pick ONE:
sudo systemctl stop smartroad
./scripts/run_smartroad.sh stop
# then only:
sudo systemctl start smartroad
```

---

## Fix recipes

### “Nothing works / connection refused”

```bash
sudo systemctl restart smartroad
sleep 3
curl -s http://127.0.0.1:5005/api/health
```

### “systemd keep failing”

```bash
sudo journalctl -u smartroad -n 200 --no-pager
tail -n 200 data/logs/smartroad.log
# Common: wrong WorkingDirectory, missing venv, bad MODEL_PATH, DB auth
# Fix .env / code, then:
sudo systemctl restart smartroad
```

### “I only have bash, no systemd yet”

```bash
./scripts/run_smartroad.sh restart
./scripts/run_smartroad.sh status
# Then schedule installing systemd when you can use sudo
sudo ./scripts/install_systemd.sh
```

### “I want a clean stop (maintenance)”

```bash
sudo systemctl stop smartroad
# when done:
sudo systemctl start smartroad
```

### “Reinstall unit after moving the repo”

```bash
sudo systemctl disable --now smartroad
sudo ./scripts/install_systemd.sh    # rewrites paths for current ROOT
```

### “Disable systemd and go back to bash”

```bash
sudo systemctl disable --now smartroad
sudo rm -f /etc/systemd/system/smartroad.service
sudo systemctl daemon-reload
./scripts/run_smartroad.sh start
```

---

## Log map

| Path | Contents |
|------|----------|
| `data/logs/smartroad.log` | Flask / Werkzeug / app stdout (chunk, accel, errors) |
| `data/logs/watchdog.log` | Health misses, `DEAD`, `RESTART`, soft-restart |
| `journalctl -u smartroad` | systemd supervise lifecycle |
| `nohup.out` | Should stay empty/missing when using the scripts |

User `stop` (bash) truncates app logs on purpose; systemd soft-restarts **keep**
logs for post-mortem.

---

## Related files

| File | Role |
|------|------|
| `scripts/run_smartroad.sh` | start / stop / supervise / watchdog |
| `scripts/install_systemd.sh` | installs + enables `smartroad.service` |
| `scripts/smartroad.service.in` | unit template (`Restart=always`) |
| `scripts/gunicorn.conf.py` | optional waitress/gunicorn knobs (not used by default bash path) |
| `ffmpeg_accel.py` (repo root) | NVENC vs libx264 selection |
| `.env.example` (repo root) | optional env reference (not auto-loaded) |
| `.env` (repo root) | secrets + overrides (loaded by runner) |
| `docs/ops/server.md` | this runbook |

---

## Short cheat sheet

```bash
# ✅ after install — use these
sudo systemctl status smartroad
sudo systemctl restart smartroad
curl -s http://127.0.0.1:5005/api/health

# ❌ after install — do not use these for normal ops
./scripts/run_smartroad.sh restart
python web_app.py          # foreground / SSH-tied
```

---

## S3 ↔ AceCloud distance / latency

| Side | Where |
|------|--------|
| AceCloud VM `45.194.2.247` | **Noida** (CtrlS / RTDS — AceCloud India) |
| Default S3 (`s3_utils.DEFAULT_REGION`) | **`ap-south-1` Mumbai** |
| Buckets | `smart-road-videos`, `smart-road-videos-processed` |

That is roughly **Noida → Mumbai** (~1,100+ km). Inter-city India RTT is often
**~20–80 ms** on a good path, but can spike to **100–300+ ms** depending on ISP
peering — enough to make large multipart finalize feel “latency heavy” even when
NVENC is fast locally.

`.env` does **not** need to set `AWS_REGION` if Mumbai is correct (code default).
If the bucket was created in another region, set `AWS_REGION` to that region.

**Measure from the AceCloud box** (not from your laptop):

```bash
cd /path/to/smart_road_app
source venv/bin/activate   # or your venv
python scripts/probe_s3_latency.py
```

Interpretation:

- Tiny Put+Get **&lt; 150 ms** total → healthy Noida↔Mumbai path  
- **150–400 ms** → typical; keep multipart concurrency (`S3_UPLOAD_CONCURRENCY`)  
- **&gt; 400 ms** → poor path; consider AceCloud **Mumbai** VM next to S3, or ask
  AceCloud about better peering to AWS `ap-south-1`

---

## Portal slow after uploads (`/detection` hangs)

Uploads + finalize used to run ffmpeg/S3 **inside** the Flask process, so
[the Detection page](http://45.194.2.247:5005/detection) waited behind that work.

Now:

1. Finalize runs in a **child process** (`scripts/finalize_chunk_job.py`, `nice -n 15`)
2. UI **GET** APIs are admission-light (Detection page loads while finalize continues)
3. Chunk body reads use `CHUNK_UPLOAD_CONCURRENCY` only (default **3**), not the heavy gate
4. Only **one** finalize at a time (`FINALIZE_CONCURRENCY=1`)

If the portal is still stuck:

```bash
sudo systemctl restart smartroad
curl -s http://127.0.0.1:5005/api/health
# optional: see finalize workers
ps aux | grep -E 'finalize_chunk|ffmpeg' | grep -v grep
```

Phone “check the server is up” usually means health timed out while the old
in-process finalize was pegging CPU — restart + pull this isolation fix.

---

## Login hangs / never succeeds (credentials look fine)

**Symptom:** page loads, `/api/health` is OK, but login spinner never finishes.

**Root cause (confirmed):** `.env` had `DB_HOST=45.194.2.247` (the AceCloud
**public** IP) while Postgres runs **on the same VM**. Connecting to your own
public IP hairpins NAT/firewall → every login SQL hangs. Credentials are fine.

**Fix:** `db_utils.resolve_db_host()` rewrites a self-IP to `127.0.0.1`
automatically. Still set this explicitly on AceCloud for clarity:

```bash
# On AceCloud .env — Postgres is local:
DB_HOST=127.0.0.1
# (laptop talking to AceCloud Postgres remotely can keep DB_HOST=45.194.2.247)

sudo systemctl restart smartroad smartroad-worker
curl -s --max-time 5 'http://127.0.0.1:5005/api/health?deep=1'
# expect "db":"ok" in a fraction of a second
```

| Probe | Meaning |
|-------|---------|
| `curl -s http://127.0.0.1:5005/api/health` | Process up (no DB) |
| `curl -s 'http://127.0.0.1:5005/api/health?deep=1'` | Must show `"db":"ok"` fast |

---

## Phone upload: “Unauthorized”

Uploads require a Flask-Login session. Mobile clients send both `Cookie` and
`X-Session-Cookie` (Expo `uploadAsync` often strips `Cookie` alone).

| Check | Action |
|-------|--------|
| Phone not logged in / session expired | Log out → log in again on the phone → retry Upload |
| Server restarted with a **new** `FLASK_SECRET_KEY` | Sessions invalidate — log in again; keep `FLASK_SECRET_KEY` stable in `.env` |
| Old APK without `X-Session-Cookie` | Rebuild/reinstall Expo or Flutter app after pulling this fix |
| Server not redeployed | `sudo systemctl restart smartroad` after pulling `web_app.py` that accepts `X-Session-Cookie` |

Quick server-side proof (replace `SESSION=…` with the value from a fresh login):

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:5005/api/upload/session/init \
  -H 'Content-Type: application/json' \
  -H 'X-Session-Cookie: session=SESSION_VALUE' \
  -d '{"capture_session_id":"test-auth"}'
# Expect 200 when session is valid; 401 when missing/expired
```

---

## Auto-detect not running (S3 videos sit idle)

**Symptom:** Videos visible in S3 / Detection catalog, but no new `video_sessions` rows and nothing in worker logs for 30+ minutes after deploy.

**Cause:** Auto-detect is **not** handled by Flask. It runs only in `scripts/smartroad_worker.py` (systemd `smartroad-worker.service`). Restarting `smartroad` alone leaves the detect queue untouched.

### Checklist (on AceCloud)

```bash
# 1. Both services must be active
sudo systemctl status smartroad smartroad-worker

# 2. Worker must be on new code — restart BOTH after git pull
sudo systemctl restart smartroad smartroad-worker

# 3. Queue + flags (no auth required)
curl -s http://127.0.0.1:5005/api/health | jq '{detect:.detect_queue,auto:.auto_detect,finalize:.finalize_queue}'
# auto_detect.on_upload and auto_detect.scan_s3 should be true (unless disabled in .env)

# 4. Worker logs — expect s3_scan= and detect start/done lines
sudo journalctl -u smartroad-worker -n 80 --no-pager

# 5. On-disk queue (same paths the worker uses)
ls -la data/detect_queue/pending data/detect_queue/running data/detect_queue/failed

# 6. One-shot backfill (safe to run while worker is up)
cd /path/to/smart_road_app
source venv/bin/activate   # or vnev/Scripts/activate on Windows dev
python scripts/enqueue_s3_detect.py
# prints {queued, skipped, skip_no_gps, ...}
```

| Observation | Likely fix |
|---------------|------------|
| `smartroad-worker` inactive / failed | `sudo systemctl start smartroad-worker`; read `journalctl -u smartroad-worker -f` for import/model errors |
| `detect_queue.pending` > 0, `running` = 0 forever | Worker not running or stuck; restart worker |
| `skip_no_gps` high in scan summary | Each video in **input** bucket needs a sibling GPS file (`.csv`, `.xlsx`, or `_log.json` next to the `.mp4`) — scan skips bare videos |
| Jobs only in `detect_queue/failed/` | Read `*.json` for `"error"`; fix model/S3/DB issue; re-run with `DETECT_S3_REQUEUE_FAILED=1 python scripts/enqueue_s3_detect.py --requeue-failed` (worker logs failed keys on startup when `skip_failed` > 0) |
| `auto_detect.on_upload: false` | Expected locally (no `SMARTROAD_ENV=production`). On server: set `SMARTROAD_ENV=production` and `AUTO_DETECT_ON_UPLOAD=1`, restart worker |
| Media already in **processed** bucket only | Scan lists **input** bucket only — already-detected sources won't re-queue unless you re-upload to input |

Detection runs **one video at a time** (GPU). A long clip can take many minutes; you should still see `[smartroad-worker] detect start …` in journalctl when it is working.
