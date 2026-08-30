#!/usr/bin/env bash
# Ensure FLASK_SECRET_KEY exists + apply token_version migration via Postgres socket
# (bypasses pg_hba TCP rejection for 127.0.0.1).
#
# Run on AceCloud:
#   sudo bash scripts/ensure_secret_and_migrate.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
ENV_FILE="$ROOT/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: missing $ENV_FILE" >&2
  exit 1
fi

ensure_key() {
  local key="$1"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    local val
    val="$(grep -E "^${key}=" "$ENV_FILE" | tail -n1 | cut -d= -f2- | tr -d '\r')"
    if [[ -n "$val" && "$val" != "<same-secret-on-all-procs>" ]]; then
      echo "OK  $key already set"
      return 0
    fi
  fi
  local gen
  gen="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${gen}|" "$ENV_FILE"
  else
    echo "${key}=${gen}" >>"$ENV_FILE"
  fi
  echo "OK  generated $key"
}

ensure_key FLASK_SECRET_KEY
# JWT can share the same secret (token_auth falls back to FLASK_SECRET_KEY).
if ! grep -qE '^JWT_SECRET=' "$ENV_FILE"; then
  # Keep JWT_SECRET identical to FLASK so all procs agree even if JWT_SECRET is set later.
  FK="$(grep -E '^FLASK_SECRET_KEY=' "$ENV_FILE" | tail -n1 | cut -d= -f2- | tr -d '\r')"
  echo "JWT_SECRET=${FK}" >>"$ENV_FILE"
  echo "OK  JWT_SECRET synced from FLASK_SECRET_KEY"
else
  echo "OK  JWT_SECRET already set"
fi

DB_NAME="$(grep -E '^DB_NAME=' "$ENV_FILE" | tail -n1 | cut -d= -f2- | tr -d '\r' || true)"
DB_NAME="${DB_NAME:-smartroad_ap}"

echo "Applying token_version via Unix socket (as postgres OS user)…"
sudo -u postgres psql -d "$DB_NAME" -v ON_ERROR_STOP=1 \
  -f "$ROOT/migrations/20260715_user_token_version.sql"

sudo -u postgres psql -d "$DB_NAME" -c \
  "SELECT column_name, data_type, column_default
   FROM information_schema.columns
   WHERE table_name='users' AND column_name='token_version';"

echo
echo "Done. FLASK_SECRET_KEY is in .env (do not paste it into chat)."
echo "Next: ./scripts/services.sh start"
