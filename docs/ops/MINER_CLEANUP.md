# AceCloud Postgres miner cleanup & harden

Commands used when the box showed high CPU as **`upowerd` owned by OS user `postgres`**, login/dashboard hung, and `5432` was open in UFW.

**This is not a SmartRoad application bug.** It was malware respawned via `postgres` crontab every minute from `/var/tmp/`.

---

## What it looked like

```bash
ps -u postgres -o pid,%cpu,cmd | head -30
uptime
```

Suspicious (kill these):

- `upowerd` as user `postgres` at hundreds/% CPU and huge `TIME+`
- `sshd` as user `postgres` (backdoor)

Legitimate (keep):

```text
/usr/lib/postgresql/14/bin/postgres -D ...
postgres: 14/main: checkpointer
postgres: 14/main: background writer
...
```

Persistence we found:

```bash
sudo crontab -u postgres -l
# */1 * * * * /var/tmp/vVBZEQZb >/dev/null 2>&1
# */1 * * * * /var/tmp/NPXjP0 >/dev/null 2>&1
```

---

## 1. Close public Postgres (UFW)

App uses `DB_HOST=127.0.0.1` only — do **not** allow world `5432`.

```bash
sudo ufw status numbered
# Find lines like: [49] 5432  ALLOW  Anywhere  and  [104] 5432 (v6) ...
# Delete higher number first (v6), then IPv4 — numbers shift after each delete:
sudo ufw delete 104
sudo ufw delete 49
# Or:
sudo ufw delete allow 5432

sudo ufw status | grep 5432 || echo "5432 closed (good)"
```

Keep **`5005/tcp`** open for SmartRoad nginx.

---

## 2. Kill miner + clear crontab (required order)

Respawn runs every minute — **clear cron before or with the kill**, or it comes back.

```bash
cd /app/Smart_Road_Rec/SmartRoadApp

# Scripted path (preferred):
sudo bash scripts/kill_cpu_hogs_and_idle_db.sh

# Manual path:
sudo crontab -u postgres -r
sudo crontab -u postgres -l    # expect: no crontab for postgres

sudo pkill -9 -u postgres upowerd 2>/dev/null || true
sudo pkill -9 -u postgres -f '/var/tmp/' 2>/dev/null || true
sudo kill -9 $(ps -u postgres -o pid=,cmd= | awk '!/postgres:|\/usr\/lib\/postgresql|postgres -D/{print $1}') 2>/dev/null || true

sudo rm -f /var/tmp/vVBZEQZb /var/tmp/NPXjP0
sudo ls -la /var/tmp/ | head -50

# Confirm clean:
ps -u postgres -o pid,%cpu,cmd
uptime
# Wait ~2 minutes and re-check crontab + ps
sudo crontab -u postgres -l
```

Extra inspect (if it returns):

```bash
sudo ls -la /var/lib/postgresql/ /var/lib/postgresql/.ssh/ /tmp /dev/shm 2>/dev/null | head -80
sudo find /var/lib/postgresql /tmp /dev/shm /var/tmp -user postgres -type f 2>/dev/null | head -40
# Remove unknown authorized_keys under postgres if present
```

---

## 3. Optional: dump → reinstall Postgres → restore

Only after CPU is calm. Dump first.

### Dump (postgres cannot write `/root`)

```bash
cd /app/Smart_Road_Rec/SmartRoadApp
./scripts/services.sh stop

sudo -u postgres pg_dump -Fc -f /tmp/smartroad_ap_$(date +%F).dump smartroad_ap
sudo mv /tmp/smartroad_ap_*.dump /root/
sudo chmod 600 /root/smartroad_ap_*.dump
sudo ls -lh /root/smartroad_ap_*.dump

# Optional full cluster:
sudo -u postgres pg_dumpall -f /tmp/pg_all_$(date +%F).sql
sudo mv /tmp/pg_all_*.sql /root/
sudo chmod 600 /root/pg_all_*.sql
```

### Reinstall / recreate cluster (destructive to local DBs)

```bash
sudo systemctl stop postgresql
sudo apt-get update
sudo apt-get install --reinstall -y postgresql-14 postgresql-contrib-14 postgresql-14-postgis-3

# Nuclear empty cluster (ONLY after dump verified >0 bytes):
# sudo pg_dropcluster --stop 14 main
# sudo pg_createcluster 14 main --start
```

### Password (bash `!` breaks inside double quotes)

```bash
sudo -u postgres psql
# then:
# ALTER USER postgres PASSWORD 'YOUR_STRONG_PASSWORD';
# \q
```

Or:

```bash
sudo -u postgres psql -c 'ALTER USER postgres PASSWORD '"'YOUR_STRONG_PASSWORD'"';'
```

Mirror into `.env`: `DB_PASSWORD=...`, `DB_HOST=127.0.0.1`, `DB_FORCE_LOCALHOST=1`.

### Secure listen + hba

In `postgresql.conf`:

```text
listen_addresses = '127.0.0.1'
```

In `pg_hba.conf` — **no** `0.0.0.0/0` allows; localhost only, e.g.:

```text
local   all  postgres  peer
local   all  all       peer
host    all  all  127.0.0.1/32  scram-sha-256
host    all  all  ::1/128       scram-sha-256
```

Or use the project helper (inserts localhost allows above any reject):

```bash
sudo bash scripts/fix_pg_hba_localhost.sh
```

```bash
sudo systemctl restart postgresql
```

### Restore custom-format dump (`.dump` from `pg_dump -Fc`)

`psql -f` will **not** work on `-Fc` files.

```bash
sudo cp /root/smartroad_ap_2026-07-15.dump /tmp/smartroad_ap.dump
sudo chmod 644 /tmp/smartroad_ap.dump

sudo -u postgres createdb smartroad_ap 2>/dev/null || true
sudo -u postgres psql -d smartroad_ap -c "CREATE EXTENSION IF NOT EXISTS postgis;"

sudo -u postgres pg_restore -d smartroad_ap --clean --if-exists --no-owner \
  /tmp/smartroad_ap.dump

sudo -u postgres psql -d smartroad_ap -c '\dt'
```

Expect core tables: `users`, `vendors`, `work_orders`, `video_sessions`, `potholes`, survey/tracking/`auto_track_*`, etc.

Sanity counts:

```bash
sudo -u postgres psql -d smartroad_ap -c "
SELECT 'users' t, count(*) FROM users
UNION ALL SELECT 'vendors', count(*) FROM vendors
UNION ALL SELECT 'work_orders', count(*) FROM work_orders
UNION ALL SELECT 'video_sessions', count(*) FROM video_sessions
UNION ALL SELECT 'potholes', count(*) FROM potholes;
"
```

If `users` is empty: `python bootstrap_admin.py`.

---

## 4. App-side DB schema + services (after DB is healthy)

```bash
cd /app/Smart_Road_Rec/SmartRoadApp
source venv/bin/activate

sudo bash scripts/ensure_secret_and_migrate.sh
# and/or:
python scripts/apply_db_migrations.py

# After any Postgres reinstall, localhost TCP often rejects again:
sudo bash scripts/fix_pg_hba_localhost.sh

sudo bash scripts/install_nginx_smartroad.sh

./scripts/services.sh stop
./scripts/services.sh start
./scripts/services.sh smoke

sudo bash scripts/install_nightly_restart.sh

./scripts/services.sh status
bash scripts/diagnose_portal.sh   # optional
```

Verify:

```bash
curl -s http://127.0.0.1:5005/api/health
curl -s 'http://127.0.0.1:5005/api/health?deep=1'
ps -u postgres -o pid,cmd
sudo crontab -u postgres -l
```

Browser: hard-refresh [http://45.194.2.247:5005/login](http://45.194.2.247:5005/login) (clear site cookies if session secret rotated).

---

## 5. Related scripts in this repo

| Script | Purpose |
|--------|---------|
| `scripts/kill_cpu_hogs_and_idle_db.sh` | Kill non-root `upowerd` + terminate idle PG backends |
| `scripts/fix_pg_hba_localhost.sh` | Allow app TCP on `127.0.0.1` (first-match above rejects) |
| `scripts/ensure_secret_and_migrate.sh` | `FLASK_SECRET_KEY` + `token_version` via socket |
| `scripts/install_nginx_smartroad.sh` | Public `:5005` → portal/upload/detect |
| `scripts/services.sh` | start/stop/restart/smoke |
| `scripts/install_nightly_restart.sh` | Cron `00:00` IST → `services.sh restart` |
| `scripts/diagnose_portal.sh` | Local health / log snapshot |
| `scripts/show_500_trace.sh` | Find Flask 500 traceback |

Day-to-day multi-service ops: see [`MULTI_SERVICE.md`](./MULTI_SERVICE.md).

---

## 6. If dashboard 500 after restart

Known bug (fixed in tree): `/api/dashboard` used a thread pool and read `current_user` off-thread → `AttributeError: 'NoneType'...is_videographer'`. Ensure latest `routes/api.py` is deployed, then:

```bash
./scripts/services.sh restart portal
```

---

## Checklist (short)

1. [ ] UFW: no public `5432`
2. [ ] `crontab -u postgres` empty
3. [ ] No `upowerd` / `/var/tmp/*` miners under postgres
4. [ ] Postgres `listen_addresses=127.0.0.1` + localhost `pg_hba`
5. [ ] Strong `postgres` password + matching `.env`
6. [ ] Dump restored / `\dt` complete
7. [ ] `ensure_secret_and_migrate` + `fix_pg_hba_localhost` if needed
8. [ ] nginx + `./scripts/services.sh start` + `smoke`
9. [ ] Nightly restart installed
10. [ ] Login works after cookie clear
