# SmartRoad AP — Deployment Guide (Windows & Unix)

Two processes in production:

| Process | Framework | Default port | Purpose |
|---|---|---|---|
| `web_app.py` | Flask + React SPA | 5000 | Portal — vendors, tasks, survey, **Detection**, **Model testing**, videographer upload |

Legacy Gradio UIs (`deprecated_files/`) are optional and not deployed alongside the portal.

Both use the same PostgreSQL + PostGIS database when configured.

Steps that are identical on both platforms are written once. Where a step differs, it's split into **Windows** / **Unix (Ubuntu/Debian)** subsections — RHEL/Alma/Rocky package-name differences are called out inline.

---

## 1. Install OS-level prerequisites

### Windows
1. Python 3.10+ from [python.org](https://www.python.org/downloads/) — check **"Add python.exe to PATH"** during install.
2. [Git for Windows](https://git-scm.com/download/win).
3. [Visual C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) — needed if any pip package falls back to building from source (most ship prebuilt wheels, but this avoids surprises).
4. [PostgreSQL Windows installer](https://www.postgresql.org/download/windows/) (EnterpriseDB) — this bundles **Stack Builder**, which you'll use to add PostGIS (step 6).
5. [MediaInfo](https://mediaarea.net/en/MediaInfo/Download/Windows) — install the **CLI** or **DLL** package (needed natively by `pymediainfo`, the pip package alone isn't enough).

### Unix (Ubuntu/Debian)
```bash
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-dev python3-pip \
  git build-essential \
  libpq-dev \
  libgl1 libglib2.0-0 \
  mediainfo \
  postgresql postgresql-contrib postgis
```

| Package | Why |
|---|---|
| `python3-venv`, `python3-dev`, `build-essential` | Build native Python extensions (some deps don't ship Linux wheels for every arch) |
| `libpq-dev` | Fallback build headers for `psycopg2` if the `-binary` wheel isn't available for your platform |
| `libgl1`, `libglib2.0-0` | **Required for `opencv-python` to import at all on a headless server** — see Issue #1 below |
| `mediainfo` | Native binary backend for `pymediainfo` |
| `postgresql`, `postgresql-contrib`, `postgis` | Database + spatial extension |

RHEL/Alma/Rocky: `sudo dnf install python3 python3-devel git gcc gcc-c++ libpq-devel mesa-libGL glib2 mediainfo postgresql-server postgis` (PostGIS usually needs the [PostGIS Yum repo](https://postgis.net/install/) enabled first).

---

## 2. Copy the project & create a virtual environment

### Windows
```powershell
cd D:\master\projects\smart_road_ap
python -m venv vnev
vnev\Scripts\activate
python -m pip install --upgrade pip
```

### Unix
```bash
cd /opt/smartroad/smart_road_ap
python3 -m venv vnev
source vnev/bin/activate
python -m pip install --upgrade pip
```

---

## 3. Install YOLOv12 + Python dependencies

Same commands on both platforms once the venv is active.

If the server has an **NVIDIA GPU**, install a CUDA-matched torch build *before* everything else (otherwise pip pulls the CPU-only wheel):
```bash
# check your driver/CUDA version with `nvidia-smi` first, example is for CUDA 12.1
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
```

Then:
```bash
pip install git+https://github.com/sunsmarterjie/yolov12.git
pip install -r requirements.txt
```

For production process management on **Unix only**, also install:
```bash
pip install gunicorn
```
`gunicorn` doesn't run on Windows (it depends on the `fcntl` module). The Windows equivalent, `waitress`, is covered in step 11.

---

## 4. Model weights — `smartroad_ap.pt`

Copy the trained weights to the exact path the app expects (identical on both OSes — `model_loader.py` uses `pathlib`, which normalizes separators):

```
smart_road_ap/artifacts/models/smartroad_ap.pt
```

Resolution order: explicit arg → `MODEL_PATH` env var → `artifacts/models/smartroad_ap.pt` relative to the repo.

---

## 5. Using Roboflow (dataset download, optional)

Only needed if you're re-training rather than deploying an existing `smartroad_ap.pt`. Same on both OSes.

1. Get a private API key from your Roboflow account → project → **Settings → API Keys**.
2. Put it in `.env` as `ROBOFLOW_API_KEY=rf_...`.
3. Run:
   ```bash
   python download_data.py
   ```
   Pulls the dataset (`Pothole-Detection--2/{train,valid,test}` + `data.yaml`) into the current directory.

---

## 6. PostgreSQL + PostGIS setup

### Windows
1. Run the PostgreSQL installer; note the port (default `5432`) and the `postgres` superuser password you set.
2. Launch **Stack Builder** (installed alongside PostgreSQL, or via Start menu) → pick your PostgreSQL install → under **Spatial Extensions** check **PostGIS** → install.
3. Create the role and database (Start → **SQL Shell (psql)**, or any GUI like pgAdmin):
   ```sql
   CREATE ROLE smartroad WITH LOGIN PASSWORD 'CHANGE_ME_STRONG_PASSWORD';
   CREATE DATABASE smartroad_ap OWNER smartroad;
   \c smartroad_ap
   CREATE EXTENSION IF NOT EXISTS postgis;
   ```

### Unix
```bash
sudo systemctl enable --now postgresql

# find your PG major version (needed for the postgis package name on some distros)
psql --version

sudo -u postgres psql -c "CREATE ROLE smartroad WITH LOGIN PASSWORD 'CHANGE_ME_STRONG_PASSWORD';"
sudo -u postgres psql -c "CREATE DATABASE smartroad_ap OWNER smartroad;"
sudo -u postgres psql -d smartroad_ap -c "CREATE EXTENSION IF NOT EXISTS postgis;"
```

If `postgis` extension creation fails with "could not open extension control file" on Unix, the `postgis` package didn't install for your installed PostgreSQL major version — install the version-matched package, e.g. `postgresql-16-postgis-3`.

> Don't run the app as the `postgres` superuser role in production (the dev `.env` uses `DB_USER=postgres`, which is fine for a local box but over-privileged for a server). Use a dedicated role like `smartroad` with only the privileges it needs on `smartroad_ap`.

---

## 7. Create the schema

The schema is idempotent (`CREATE TABLE IF NOT EXISTS`), safe to re-run. Same command on both OSes:

```bash
psql -h localhost -U smartroad -d smartroad_ap -f schema.sql
```

Or let the app do it (already wired up in `web_app.py`, which calls this automatically on every startup if `is_db_configured()` is true):
```bash
python -c "import db_utils; db_utils.init_db()"
```

Creates all Phase 3 tables (`users`, `vendors`, `vendor_zones`, `work_orders`, `work_order_potholes`, `status_history`, `completion_validations`, `warranty_records`, `escalations`) plus the Phase 1/2 tables (`video_sessions`, `potholes`).

---

## 8. Configure `.env`

Identical file on both platforms — use forward slashes in `MODEL_PATH` so the same `.env` works unmodified on Windows and Unix (Windows accepts `/` in paths fine via Python's file APIs):

```env
# ---- PostgreSQL + PostGIS ----
DB_HOST=localhost
DB_PORT=5432
DB_NAME=smartroad_ap
DB_USER=smartroad
DB_PASSWORD=CHANGE_ME_STRONG_PASSWORD

# ---- Model ----
MODEL_PATH=artifacts/models/smartroad_ap.pt

# ---- Roboflow (only needed to re-download/re-train) ----
ROBOFLOW_API_KEY=your_roboflow_private_key_here

# ---- AWS (required for S3 integration) ----
AWS_ACCESS_KEY_ID=YOUR_AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY=YOUR_AWS_SECRET_ACCESS_KEY
# AWS_REGION=ap-south-1
S3_INPUT_BUCKET=smart-road-videos
S3_PROCESSED_BUCKET=smart-road-videos-processed

# ---- Flask ----
FLASK_SECRET_KEY=generate_with_python_secrets_token_hex_32
FLASK_PORT=5000
FLASK_DEBUG=false
```

Generate a real secret key:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Unix: `chmod 600 .env`. Both: keep `.env` out of version control.

---

## 9. Create the first Admin user

Same on both OSes:
```bash
python scripts/bootstrap_admin.py
```
Idempotent (skips if `admin` already exists). Creates `username=admin / password=admin123` — **change this password immediately after first login** via the Users page.

---

## 10. Retrain the model (optional)

```bash
python download_data.py
python tools/ml/train_yolo.py
```

Copy bench weights to production if desired, or use `tools/ml/TRAINING.md` for RF-DETR. Legacy root script: `deprecated_files/legacy_root_train.py`.

---

## 11. Running the portal

### Windows — quick start
```powershell
venv\Scripts\activate
cd frontend; npm run build; cd ..
python web_app.py
```

### Windows — production (NSSM + waitress)
```powershell
pip install waitress
nssm install SmartRoadWeb "D:\path\to\venv\Scripts\waitress-serve.exe" "--host=127.0.0.1 --port=5000 --call web_app:create_app"
nssm set SmartRoadWeb AppDirectory "D:\path\to\smart_road_app"
nssm start SmartRoadWeb
```

### Unix — AceCloud / production (recommended)

```bash
cd /app/Smart_Road_Rec/SmartRoadApp   # your deploy path
# merge non-secret tunables once (keeps DB/AWS secrets already in .env):
#   grep -vE '^(#|$)' .env.example >> .env

chmod +x scripts/run_smartroad.sh
./scripts/run_smartroad.sh restart
./scripts/run_smartroad.sh status
```

Profile targets a shared **16 CPU / 62 GiB / RTX A6000** host: gunicorn 3×4, keepalive off, YOLO on GPU 0 with frame stride (multiprocess detection off on GPU). See `.env.example`.

### Unix — production (systemd)

`/etc/systemd/system/smartroad-web.service`:
```ini
[Unit]
Description=SmartRoad portal (Flask + React)
After=network.target postgresql.service

[Service]
Type=simple
User=smartroad
WorkingDirectory=/opt/smartroad/smart_road_ap
EnvironmentFile=/opt/smartroad/smart_road_ap/.env
ExecStart=/opt/smartroad/smart_road_ap/venv/bin/gunicorn --workers 3 --threads 4 --timeout 300 --keep-alive 2 --preload --bind 0.0.0.0:5005 "web_app:create_app()"
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now smartroad-web
sudo systemctl status smartroad-web
```

`--preload` loads the app (and runs `db_utils.init_db()`) once in the gunicorn master before forking workers, instead of once per worker. Without it, multiple workers each ran the schema DDL concurrently at boot and could deadlock (`AccessExclusiveLock ... blocked by process X` in the log) — `init_db()` also takes a Postgres advisory lock now as defense in depth, but prefer `--preload` so it only runs once.

> Legacy Gradio systemd/nginx examples: see `deprecated_files/README.md` if you still run those UIs locally.

### Reverse proxy (recommended on both OSes)

Bind the app to `127.0.0.1` only and put nginx in front with TLS:

```nginx
server {
    listen 443 ssl;
    server_name portal.yourdomain.com;
    location / { proxy_pass http://127.0.0.1:5000; proxy_set_header Host $host; }
}
```

Remove separate `:7860` Gradio proxy blocks — detection is served from the same portal.

If you skip the reverse proxy, bind `web_app.py` to `127.0.0.1` only and use a firewall — do not expose port 5000 publicly without TLS.

Unix firewall:
```bash
sudo ufw allow 22/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

---

## 12. Known issues in the current code + fixes

Items 2–4 below are **already fixed** in the current code. The rest are deployment/config items to handle per environment.

| # | Issue | Platform affected | Impact | Fix |
|---|---|---|---|---|
| 1 | `opencv-python` (not `-headless`) needs `libGL.so.1` to even `import cv2` | Unix (headless servers) | `ImportError: libGL.so.1: cannot open shared object file` on first run | `apt install libgl1 libglib2.0-0` (step 1), or swap `opencv-python` → `opencv-python-headless` in `requirements.txt` |
| 2 | ~~Legacy `train.py` hardcoded paths~~ **Fixed** | Both | — | Use `tools/ml/train_yolo.py`; old script in `deprecated_files/legacy_root_train.py` |
| 3 | ~~`web_app.py` hardcoded `debug=True`~~ **Fixed** | Both | — | `debug` now reads from `FLASK_DEBUG` env var (default `false`); still run under gunicorn (Unix) / waitress (Windows) in production regardless |
| 4 | ~~`FLASK_SECRET_KEY` fell back to a hardcoded string baked into `web_app.py`~~ **Fixed** | Both | — | Missing `FLASK_SECRET_KEY` now generates a random ephemeral key at startup with a logged warning (sessions won't survive a restart) instead of a predictable static fallback. Still set `FLASK_SECRET_KEY` in `.env` for production (step 8) so sessions persist across restarts |
| 5 | `.env` currently in the repo has live DB/AWS/Roboflow credentials | Both | Anyone with repo/file access has production secrets | Treat `.env` as per-environment, never commit it, rotate the credentials that were already exposed during development |
| 6 | `scripts/bootstrap_admin.py` has a hardcoded weak default password | Both | Predictable admin credentials if the password isn't changed | Change the password immediately after first login; can be updated to require an `ADMIN_PASSWORD` env var instead |
| 7 | `DB_USER=postgres` (superuser) in dev `.env` | Both | Over-privileged DB role for a server process | Use a dedicated least-privilege role (step 6) |
| 8 | No `gunicorn`/`waitress` in `requirements.txt` | Both | `pip install -r requirements.txt` alone won't give you a production WSGI server | Install the platform-appropriate one separately (step 3/11) — kept out of `requirements.txt` since `gunicorn` doesn't install on Windows |
| 9 | `pymediainfo` needs the native `mediainfo`/`MediaInfo.dll` binary, not just the pip package | Both | GPS-log-from-video-metadata extraction silently fails without it | Install the native package (step 1) |

---

## 13. Post-deploy checklist

- [ ] `psql -h localhost -U smartroad -d smartroad_ap -c "\dt"` shows all Phase 3 + Phase 1/2 tables
- [ ] `python scripts/bootstrap_admin.py` ran once, then admin password changed via Users page
- [ ] `artifacts/models/smartroad_ap.pt` present and loads (`python -c "import model_loader; model_loader.load_model()"`)
- [ ] `.env` has a real `FLASK_SECRET_KEY`, non-default DB credentials, and rotated AWS/Roboflow keys
- [ ] Windows: `smartroad-web` service (NSSM + waitress) or Task Scheduler. Unix: `smartroad-web` systemd unit `enabled` and `active`
- [ ] Only 443 (or 80/443) open externally; port 5000 bound to `127.0.0.1` behind a reverse proxy
- [ ] `FLASK_DEBUG` not set to `true` in production
