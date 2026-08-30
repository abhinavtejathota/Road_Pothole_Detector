#!/usr/bin/env bash
# One-shot: stop hairpin login hangs on AceCloud (Postgres on same VM).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="$ROOT/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: no $ENV_FILE" >&2
  exit 1
fi

prev="$(grep -E '^DB_HOST=' "$ENV_FILE" | tail -n1 || true)"
if grep -qE '^DB_HOST=' "$ENV_FILE"; then
  sed -i 's/^DB_HOST=.*/DB_HOST=127.0.0.1/' "$ENV_FILE"
else
  echo 'DB_HOST=127.0.0.1' >>"$ENV_FILE"
fi
if grep -qE '^DB_FORCE_LOCALHOST=' "$ENV_FILE"; then
  sed -i 's/^DB_FORCE_LOCALHOST=.*/DB_FORCE_LOCALHOST=1/' "$ENV_FILE"
else
  echo 'DB_FORCE_LOCALHOST=1' >>"$ENV_FILE"
fi

echo "Was:  ${prev:-DB_HOST unset}"
echo "Now:  $(grep -E '^DB_HOST=|^DB_FORCE_LOCALHOST=' "$ENV_FILE")"
echo
echo "Quick Postgres check:"
timeout 3 psql -h 127.0.0.1 -U "${DB_USER:-postgres}" -d "${DB_NAME:-smartroad_ap}" -c 'SELECT 1 AS ok;' 2>&1 | head -20 || true
echo

if systemctl is-active --quiet smartroad 2>/dev/null; then
  echo "Restarting systemd smartroad …"
  sudo systemctl restart smartroad
  sudo systemctl restart smartroad-worker 2>/dev/null || true
else
  echo "Restarting via run_smartroad.sh …"
  ./scripts/run_smartroad.sh restart
fi

sleep 2
echo
echo "=== /api/health ==="
curl -s --max-time 5 "http://127.0.0.1:${FLASK_PORT:-5005}/api/health" || true
echo
echo "=== /api/health?deep=1  (must show db:ok and db_host:127.0.0.1) ==="
curl -s --max-time 8 "http://127.0.0.1:${FLASK_PORT:-5005}/api/health?deep=1" || true
echo
echo
echo "If db is ok: open http://SERVER:5005/login and sign in (clear old cookies first)."
