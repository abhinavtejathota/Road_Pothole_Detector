#!/usr/bin/env bash
# Diagnose post-login hangs / nginx 502 on AceCloud.
#   bash scripts/diagnose_portal.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== listeners ==="
ss -lntp 2>/dev/null | grep -E ':5005|:5015|:5006|:5007' || netstat -lntp 2>/dev/null | grep -E '5005|5015|5006|5007' || true

echo
echo "=== services.sh status ==="
./scripts/services.sh status 2>/dev/null || true

echo
echo "=== direct backends (bypass nginx) ==="
for url in \
  "http://127.0.0.1:5015/api/health" \
  "http://127.0.0.1:5015/api/health?deep=1" \
  "http://127.0.0.1:5006/api/health" \
  "http://127.0.0.1:5007/api/health"
do
  code="$(curl -s -o /tmp/sr_diag.body -w '%{http_code}' --max-time 8 "$url" 2>/dev/null || echo 000)"
  echo "$code  $url"
  if [[ "$code" != "200" && "$code" != "000" ]]; then
    head -c 200 /tmp/sr_diag.body 2>/dev/null; echo
  fi
  if [[ "$url" == *deep* ]]; then
    head -c 400 /tmp/sr_diag.body 2>/dev/null; echo
  fi
done

echo
echo "=== via nginx :5005 ==="
for url in \
  "http://127.0.0.1:5005/api/health" \
  "http://127.0.0.1:5005/api/health?deep=1" \
  "http://127.0.0.1:5005/login"
do
  code="$(curl -s -o /tmp/sr_diag.body -w '%{http_code}' --max-time 10 "$url" 2>/dev/null || echo 000)"
  t="$(curl -s -o /dev/null -w '%{time_total}' --max-time 10 "$url" 2>/dev/null || echo x)"
  echo "$code  ${t}s  $url"
done

echo
echo "=== DB resolve + TCP ==="
python - <<'PY' 2>&1 | head -40
import os, sys
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv(".env", override=True)
import db_utils
print("resolve_db_host:", db_utils.resolve_db_host())
print("DB_USER:", os.getenv("DB_USER"), "DB_NAME:", os.getenv("DB_NAME"))
try:
    t0 = __import__("time").time()
    row = db_utils.get_user_by_username("__missing__")
    print("db query ok in %.2fs row=%s" % (__import__("time").time() - t0, row))
except Exception as e:
    print("DB FAIL:", type(e).__name__, e)
PY

echo
echo "=== portal log tail ==="
tail -n 40 data/logs/portal.log 2>/dev/null || echo "(no portal.log)"

echo
echo "=== nginx error tail ==="
tail -n 20 /var/log/nginx/error.log 2>/dev/null || true

echo
echo
echo "=== timed dashboard (needs SMARTROAD_DIAG_USER + SMARTROAD_DIAG_PASS) ==="
if [[ -n "${SMARTROAD_DIAG_USER:-}" && -n "${SMARTROAD_DIAG_PASS:-}" ]]; then
  jar="$(mktemp)"
  code="$(curl -s -c "$jar" -b "$jar" -o /tmp/sr_login.json -w '%{http_code}' --max-time 15 \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"${SMARTROAD_DIAG_USER}\",\"password\":\"${SMARTROAD_DIAG_PASS}\"}" \
    http://127.0.0.1:5005/api/auth/login || echo 000)"
  echo "login → HTTP $code  ($(python3 -c 'import json;print(json.load(open(\"/tmp/sr_login.json\")).get(\"username\",\"\") or json.load(open(\"/tmp/sr_login.json\")).get(\"error\",\"\"))' 2>/dev/null || true))"
  for path in /api/auth/me /api/dashboard /api/survey/states; do
    t0="$(date +%s%3N)"
    c="$(curl -s -b "$jar" -o /tmp/sr_diag.body -w '%{http_code}' --max-time 60 \
      "http://127.0.0.1:5005$path" || echo 000)"
    t1="$(date +%s%3N)"
    ms=$((t1 - t0))
    bytes="$(wc -c </tmp/sr_diag.body 2>/dev/null || echo 0)"
    echo "$c  ${ms}ms  ${bytes}B  $path"
  done
  rm -f "$jar"
else
  echo "(skip) export SMARTROAD_DIAG_USER=admin SMARTROAD_DIAG_PASS='…' then re-run"
fi

echo
echo "If portal DOWN or :5015 not listening → ./scripts/services.sh restart portal"
echo "If deep health slow/fails but shallow OK → DB pool / statement hang (see portal.log)"
echo "If nginx 502 → portal crashed or not on 5015; check fuser -v 5015/tcp"
echo "If login OK but /api/dashboard >20s → that is the post-login hang (see portal.log [dashboard])"
