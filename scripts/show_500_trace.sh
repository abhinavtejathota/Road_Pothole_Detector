#!/usr/bin/env bash
# Pull the real traceback for browser "500 Internal Server Error".
#   bash scripts/show_500_trace.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== services ==="
./scripts/services.sh status || true

echo
echo "=== curl local (bypass browser cache) ==="
for u in \
  "http://127.0.0.1:5005/" \
  "http://127.0.0.1:5015/" \
  "http://127.0.0.1:5005/api/health" \
  "http://127.0.0.1:5015/api/health" \
  "http://127.0.0.1:5005/api/auth/me"
do
  code="$(curl -s -o /tmp/sr500.body -w '%{http_code}' --max-time 5 "$u" 2>/dev/null || echo 000)"
  echo "$code  $u"
  if [[ "$code" == "500" ]]; then
    head -c 400 /tmp/sr500.body; echo
  fi
done

echo
echo "=== portal.log last Traceback ==="
if [[ -f data/logs/portal.log ]]; then
  grep -n "Traceback\|Error\|Exception\|500" data/logs/portal.log | tail -n 40 || true
  echo "---- last 60 lines ----"
  tail -n 60 data/logs/portal.log
else
  echo "(no data/logs/portal.log)"
fi

echo
echo "=== nginx error (if any) ==="
tail -n 30 /var/log/nginx/error.log 2>/dev/null || true
