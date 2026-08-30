#!/usr/bin/env bash
# Fix AceCloud Postgres so the app can connect on 127.0.0.1:5432.
#
# Stock/AceCloud pg_hba often has an EARLIER "host ... reject" for loopback.
# Rules are first-match — appending allow lines at the bottom does nothing.
#
# Run:
#   sudo bash scripts/fix_pg_hba_localhost.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: run as root → sudo bash scripts/fix_pg_hba_localhost.sh" >&2
  exit 1
fi

HBA=""
if [[ -f /etc/postgresql/14/main/pg_hba.conf ]]; then
  HBA=/etc/postgresql/14/main/pg_hba.conf
else
  HBA="$(sudo -u postgres psql -tAc 'SHOW hba_file' 2>/dev/null | tr -d '[:space:]' || true)"
fi
if [[ -z "$HBA" || ! -f "$HBA" ]]; then
  echo "ERROR: could not find pg_hba.conf" >&2
  exit 1
fi

echo "Using hba: $HBA"
echo "--- relevant lines BEFORE fix ---"
grep -nE '^(#)?\s*(local|host|hostssl|hostnossl)' "$HBA" | head -60 || true
echo "---------------------------------"

cp -a "$HBA" "${HBA}.bak.$(date +%Y%m%d%H%M%S)"

# Comment out ANY active host/hostssl/hostnossl line that targets loopback with reject
# or that would otherwise block before we insert allows.
tmp="$(mktemp)"
python3 - "$HBA" "$tmp" <<'PY'
import re, sys
src, dst = sys.argv[1], sys.argv[2]
lines = open(src, encoding="utf-8", errors="replace").read().splitlines(True)

BLOCK = """# --- smartroad localhost TCP (FIRST-MATCH; must be above any reject) ---
# TYPE  DATABASE        USER            ADDRESS                 METHOD
host    all             all             127.0.0.1/32            scram-sha-256
host    all             all             127.0.0.1/32            md5
host    all             all             127.0.0.1/32            trust
host    all             all             ::1/128                 scram-sha-256
host    all             all             ::1/128                 md5
host    all             all             ::1/128                 trust
# --- end smartroad localhost TCP ---
"""

# Strip previous smartroad block(s)
out = []
skip = False
for line in lines:
    if "smartroad localhost TCP" in line and "FIRST-MATCH" in line:
        skip = True
        continue
    if skip:
        if "end smartroad localhost TCP" in line:
            skip = False
        continue
    # Drop old append-only marker block from previous script version
    if line.strip() == "# smartroad: allow app TCP localhost":
        skip = True
        continue
    if skip and re.match(r"^\s*host\s+", line) and ("127.0.0.1" in line or "::1" in line):
        continue
    if skip and line.strip() == "":
        skip = False
        out.append(line)
        continue
    if skip and not re.match(r"^\s*host\s+", line):
        skip = False
    # Comment active reject/host lines that mention loopback (except our block)
    stripped = line.lstrip()
    if not stripped.startswith("#"):
        low = line.lower()
        if re.match(r"^\s*host(ssl|nossl)?\s+", line) and (
            "127.0.0.1" in line or "::1" in line or "localhost" in low
        ):
            # Keep non-reject allow lines for other methods? Safer: comment ALL prior
            # loopback host rules so ONLY our block decides.
            line = "# smartroad-disabled: " + line
    out.append(line)

# Insert block after any leading comment header, before first active local/host rule
insert_at = 0
for i, line in enumerate(out):
    s = line.lstrip()
    if not s or s.startswith("#"):
        insert_at = i + 1
        continue
    if re.match(r"^(local|host)", s):
        insert_at = i
        break
    insert_at = i + 1

final = out[:insert_at] + [BLOCK if BLOCK.endswith("\n") else BLOCK + "\n"] + out[insert_at:]
open(dst, "w", encoding="utf-8").writelines(final)
print(f"Wrote fixed hba → {dst} (insert_at={insert_at})")
PY

mv "$tmp" "$HBA"
chmod 640 "$HBA"
chown postgres:postgres "$HBA" 2>/dev/null || true

echo "--- relevant lines AFTER fix ---"
grep -nE 'smartroad|127\.0\.0\.1|::1|reject|^local|^host' "$HBA" | head -80 || true
echo "--------------------------------"

# Reload
if command -v pg_ctlcluster >/dev/null 2>&1; then
  pg_ctlcluster 14 main reload || systemctl reload postgresql || sudo -u postgres psql -c "SELECT pg_reload_conf();"
else
  sudo -u postgres psql -c "SELECT pg_reload_conf();" >/dev/null
fi
echo "OK  Postgres reloaded hba"

# Confirm server sees our lines
echo "--- SHOW from live Postgres (first matching style) ---"
sudo -u postgres psql -c "SELECT type, database, user_name, address, auth_method FROM pg_hba_file_rules WHERE address IN ('127.0.0.1','::1') OR auth_method='reject' ORDER BY rule_number;" 2>/dev/null \
  || sudo -u postgres psql -c "SELECT line_number, type, database, user_name, address, auth_method FROM pg_hba_file_rules LIMIT 40;" 2>/dev/null \
  || true

DB_NAME=smartroad_ap
DB_USER=postgres
DB_PASSWORD=""
DB_PORT=5432
if [[ -f "$ENV_FILE" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      DB_NAME=*|DB_USER=*|DB_PASSWORD=*|DB_PORT=*)
        key="${line%%=*}"
        val="${line#*=}"
        val="${val%$'\r'}"
        export "$key=$val"
        ;;
    esac
  done <"$ENV_FILE"
  DB_NAME="${DB_NAME:-smartroad_ap}"
  DB_USER="${DB_USER:-postgres}"
  DB_PASSWORD="${DB_PASSWORD:-}"
  DB_PORT="${DB_PORT:-5432}"
fi

# Ensure .env loopback
if [[ -f "$ENV_FILE" ]]; then
  grep -qE '^DB_HOST=' "$ENV_FILE" && sed -i 's/^DB_HOST=.*/DB_HOST=127.0.0.1/' "$ENV_FILE" || echo 'DB_HOST=127.0.0.1' >>"$ENV_FILE"
  grep -qE '^DB_FORCE_LOCALHOST=' "$ENV_FILE" && sed -i 's/^DB_FORCE_LOCALHOST=.*/DB_FORCE_LOCALHOST=1/' "$ENV_FILE" || echo 'DB_FORCE_LOCALHOST=1' >>"$ENV_FILE"
fi

echo "Testing TCP (sslmode=disable first)…"
export PGPASSWORD="$DB_PASSWORD"
ok=0
if psql "host=127.0.0.1 port=${DB_PORT} user=${DB_USER} dbname=${DB_NAME} sslmode=disable" -c "SELECT 1 AS ok;" >/dev/null 2>&1; then
  echo "OK  TCP works (sslmode=disable)"
  ok=1
elif psql "host=127.0.0.1 port=${DB_PORT} user=${DB_USER} dbname=${DB_NAME} sslmode=prefer" -c "SELECT 1 AS ok;" >/dev/null 2>&1; then
  echo "OK  TCP works (sslmode=prefer)"
  ok=1
else
  echo "Attempting as peer via socket for comparison…"
  sudo -u postgres psql -d "$DB_NAME" -c "SELECT 1 AS socket_ok;" || true
  echo "FAIL TCP. Dumping live error:" >&2
  psql "host=127.0.0.1 port=${DB_PORT} user=${DB_USER} dbname=${DB_NAME} sslmode=disable" -c "SELECT 1;" 2>&1 || true
  echo >&2
  echo "If still reject: check for include_dir / second hba, or firewall:" >&2
  echo "  sudo -u postgres psql -c 'SHOW hba_file; SHOW config_file;'" >&2
  echo "  sudo ss -lntp | grep 5432" >&2
  exit 1
fi

echo
echo "Next:"
echo "  cd $ROOT && ./scripts/services.sh restart"
echo "  ./scripts/services.sh smoke"
