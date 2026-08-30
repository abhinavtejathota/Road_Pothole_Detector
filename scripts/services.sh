#!/usr/bin/env bash
# SmartRoad multi-service control (nohup + pid files + per-service recovery).
#
# Usage:
#   ./scripts/services.sh start          # all (split mode)
#   ./scripts/services.sh start upload   # one service only
#   ./scripts/services.sh restart        # all
#   ./scripts/services.sh restart detect # one
#   ./scripts/services.sh stop           # all
#   ./scripts/services.sh stop finalize  # one
#   ./scripts/services.sh status
#   ./scripts/services.sh smoke
#   ./scripts/services.sh mono           # single portal on :5005 (no split)
#   ./scripts/services.sh install-nightly  # cron: restart all at 00:00 IST
#   ./scripts/services.sh uninstall-nightly
#
# Env:
#   DEPLOY_MODE=split|mono   (default split)
#   BIND_HOST=127.0.0.1      (split) or 0.0.0.0 (mono/public)
#   PORTAL_PORT UPLOAD_PORT DETECT_PORT
#   SMARTROAD_SERVER=waitress (default via start_one)
#   WAITRESS_THREADS=32
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="${ROOT}:${ROOT}/backend${PYTHONPATH:+:${PYTHONPATH}}"
mkdir -p data/logs data/finalize_queue/{pending,running,done,failed} data/pids

if [[ -f venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
elif [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

# Load .env keys (portable — no process substitution)
if [[ -f .env ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
    export "$line"
  done < .env
fi

DEPLOY_MODE="${DEPLOY_MODE:-split}"
BIND_HOST="${BIND_HOST:-127.0.0.1}"
PORTAL_PORT="${PORTAL_PORT:-5015}"
UPLOAD_PORT="${UPLOAD_PORT:-5006}"
DETECT_PORT="${DETECT_PORT:-5007}"
PID_DIR=data/pids
LOG_DIR=data/logs

_pidfile() { echo "$PID_DIR/$1.pid"; }
_logfile() { echo "$LOG_DIR/$1.log"; }

_is_running() {
  local name="$1" pf pid
  pf="$(_pidfile "$name")"
  [[ -f "$pf" ]] || return 1
  pid="$(cat "$pf" 2>/dev/null || true)"
  [[ -n "${pid:-}" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

_stop_one() {
  local name="$1" pf pid
  pf="$(_pidfile "$name")"
  if [[ -f "$pf" ]]; then
    pid="$(cat "$pf" 2>/dev/null || true)"
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      # wait up to ~8s
      local i
      for i in 1 2 3 4 5 6 7 8; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
      done
      kill -9 "$pid" 2>/dev/null || true
      echo "stopped $name (was $pid)"
    else
      echo "$name not running (stale pidfile)"
    fi
    rm -f "$pf"
  else
    echo "$name already stopped"
  fi
}

_start_one() {
  local name="$1"
  if _is_running "$name"; then
    echo "$name already running pid=$(cat "$(_pidfile "$name")")"
    return 0
  fi

  # Isolate env per process so upload's YOLO_DEVICE=cpu cannot leak into detect.
  case "$name" in
    portal)
      env \
        SMARTROAD_SERVICE=portal \
        SMARTROAD_SERVER="${SMARTROAD_SERVER:-waitress}" \
        WAITRESS_THREADS="${WAITRESS_THREADS:-128}" \
        DB_POOL_MIN="${DB_POOL_MIN:-64}" \
        DB_POOL_MAX="${DB_POOL_MAX:-256}" \
        DB_POOL_HARD_CAP="${DB_POOL_HARD_CAP:-512}" \
        DB_STATEMENT_TIMEOUT_MS="${DB_STATEMENT_TIMEOUT_MS:-8000}" \
        DB_CONNECT_TIMEOUT="${DB_CONNECT_TIMEOUT:-3}" \
        DB_PING_TIMEOUT_S="${DB_PING_TIMEOUT_S:-1}" \
        DB_POOL_RESET_AFTER_FAILS="${DB_POOL_RESET_AFTER_FAILS:-25}" \
        DB_POOL_RESET_MIN_GAP_S="${DB_POOL_RESET_MIN_GAP_S:-60}" \
        FLASK_PORT="$PORTAL_PORT" \
        WAITRESS_HOST="$BIND_HOST" \
        DB_HOST="${DB_HOST:-127.0.0.1}" \
        DB_FORCE_LOCALHOST="${DB_FORCE_LOCALHOST:-1}" \
        SESSION_IDLE_TIMEOUT_S="${SESSION_IDLE_TIMEOUT_S:-3600}" \
        YOLO_DEVICE=cpu \
        nohup python -u web_app.py >>"$(_logfile portal)" 2>&1 &
      ;;
    upload)
      env \
        SMARTROAD_SERVICE=upload \
        SMARTROAD_SERVER="${SMARTROAD_SERVER:-waitress}" \
        WAITRESS_THREADS="${WAITRESS_THREADS:-64}" \
        DB_POOL_MIN="${DB_POOL_MIN:-16}" \
        DB_POOL_MAX="${DB_POOL_MAX:-128}" \
        DB_POOL_HARD_CAP="${DB_POOL_HARD_CAP:-512}" \
        FLASK_PORT="$UPLOAD_PORT" \
        WAITRESS_HOST="$BIND_HOST" \
        DB_HOST="${DB_HOST:-127.0.0.1}" \
        DB_FORCE_LOCALHOST="${DB_FORCE_LOCALHOST:-1}" \
        YOLO_DEVICE=cpu \
        nohup python -u upload_app.py >>"$(_logfile upload)" 2>&1 &
      ;;
    finalize)
      env \
        DB_HOST="${DB_HOST:-127.0.0.1}" \
        DB_FORCE_LOCALHOST="${DB_FORCE_LOCALHOST:-1}" \
        FFMPEG_HW="${FFMPEG_HW:-auto}" \
        FFMPEG_PREFER_SYSTEM="${FFMPEG_PREFER_SYSTEM:-1}" \
        FINALIZE_CONCURRENCY="${FINALIZE_CONCURRENCY:-1}" \
        POTHOLE_CPU_THREADS="${POTHOLE_CPU_THREADS:-6}" \
        nohup python -u scripts/smartroad_worker.py >>"$(_logfile finalize)" 2>&1 &
      ;;
    detect)
      env \
        SMARTROAD_SERVICE=detect \
        SMARTROAD_SERVER="${SMARTROAD_SERVER:-waitress}" \
        WAITRESS_THREADS="${DETECT_WAITRESS_THREADS:-8}" \
        DB_POOL_MIN="${DB_POOL_MIN_DETECT:-4}" \
        DB_POOL_MAX="${DB_POOL_MAX_DETECT:-64}" \
        DB_POOL_HARD_CAP="${DB_POOL_HARD_CAP:-512}" \
        FLASK_PORT="$DETECT_PORT" \
        WAITRESS_HOST="$BIND_HOST" \
        DB_HOST="${DB_HOST:-127.0.0.1}" \
        DB_FORCE_LOCALHOST="${DB_FORCE_LOCALHOST:-1}" \
        YOLO_DEVICE="${DETECT_YOLO_DEVICE:-0}" \
        POTHOLE_HALF="${POTHOLE_HALF:-1}" \
        POTHOLE_CPU_THREADS="${POTHOLE_CPU_THREADS:-6}" \
        CUDA_VISIBLE_DEVICES="${DETECT_CUDA_VISIBLE_DEVICES:-0}" \
        nohup python -u detect_app.py >>"$(_logfile detect)" 2>&1 &
      ;;
    *)
      echo "Unknown service: $name (portal|upload|finalize|detect)" >&2
      return 1
      ;;
  esac
  echo $! >"$(_pidfile "$name")"
  echo "started $name pid=$(cat "$(_pidfile "$name")") log=$(_logfile "$name")"
}

_wait_http() {
  local url="$1" label="$2" i code
  for i in $(seq 1 40); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$url" 2>/dev/null || echo 000)"
    if [[ "$code" == "200" ]]; then
      echo "OK  $label → HTTP $code"
      return 0
    fi
    sleep 0.5
  done
  echo "FAIL $label → last HTTP $code  (see $(_logfile "${label%%:*}"))" >&2
  return 1
}

_status_one() {
  local name="$1"
  if _is_running "$name"; then
    echo "UP   $name pid=$(cat "$(_pidfile "$name")")"
  else
    echo "DOWN $name"
  fi
}

cmd_smoke() {
  local fail=0
  echo "=== smoke (HTTP) ==="
  if [[ "$DEPLOY_MODE" == "mono" ]]; then
    _wait_http "http://127.0.0.1:${MONO_PORT:-5005}/api/health" "portal" || fail=1
    _wait_http "http://127.0.0.1:${MONO_PORT:-5005}/api/health?deep=1" "portal-deep" || fail=1
  else
    _wait_http "http://127.0.0.1:${PORTAL_PORT}/api/health" "portal" || fail=1
    _wait_http "http://127.0.0.1:${UPLOAD_PORT}/api/health" "upload" || fail=1
    _wait_http "http://127.0.0.1:${DETECT_PORT}/api/health" "detect" || fail=1
    # finalize has no HTTP — check pid only
    if _is_running finalize; then echo "OK  finalize process up"; else echo "FAIL finalize DOWN"; fail=1; fi
    # nginx public front (optional)
    if curl -s --max-time 2 "http://127.0.0.1:5005/api/health" >/dev/null 2>&1; then
      _wait_http "http://127.0.0.1:5005/api/health" "nginx:5005" || true
    else
      echo "WARN nginx :5005 not answering (ok if not installed yet)"
    fi
  fi
  echo "=== smoke (python roles) ==="
  python scripts/smoke_multi_service.py || fail=1
  return "$fail"
}

cmd_start_all() {
  if [[ "$DEPLOY_MODE" == "mono" ]]; then
    cmd_mono
    return
  fi
  local fail=0
  for s in portal upload finalize detect; do
    _start_one "$s" || fail=1
  done
  sleep 2
  cmd_smoke || fail=1
  if [[ "$fail" -ne 0 ]]; then
    echo "Some checks failed — other services left running. Fix the FAIL line and: $0 restart <name>" >&2
    return 1
  fi
  echo "All services healthy."
}

cmd_mono() {
  _stop_one portal 2>/dev/null || true
  BIND_HOST="${BIND_HOST:-0.0.0.0}"
  PORTAL_PORT="${MONO_PORT:-5005}"
  _start_one portal
  sleep 2
  _wait_http "http://127.0.0.1:${PORTAL_PORT}/api/health" "portal"
}

CMD="${1:-status}"
TARGET="${2:-}"

case "$CMD" in
  start)
    if [[ -n "$TARGET" ]]; then
      _start_one "$TARGET"
      sleep 1
      case "$TARGET" in
        portal)  _wait_http "http://127.0.0.1:${PORTAL_PORT}/api/health" portal || true ;;
        upload)  _wait_http "http://127.0.0.1:${UPLOAD_PORT}/api/health" upload || true ;;
        detect)  _wait_http "http://127.0.0.1:${DETECT_PORT}/api/health" detect || true ;;
        finalize) _status_one finalize ;;
      esac
    else
      cmd_start_all
    fi
    ;;
  stop)
    if [[ -n "$TARGET" ]]; then
      _stop_one "$TARGET"
    else
      for s in detect finalize upload portal; do _stop_one "$s"; done
    fi
    ;;
  restart)
    if [[ -z "$TARGET" ]]; then
      echo "=== restart all ==="
      for s in detect finalize upload portal; do _stop_one "$s"; done
      sleep 1
      cmd_start_all
    else
      _stop_one "$TARGET"
      sleep 1
      _start_one "$TARGET"
      sleep 1
      case "$TARGET" in
        portal)  _wait_http "http://127.0.0.1:${PORTAL_PORT}/api/health" portal ;;
        upload)  _wait_http "http://127.0.0.1:${UPLOAD_PORT}/api/health" upload ;;
        detect)  _wait_http "http://127.0.0.1:${DETECT_PORT}/api/health" detect ;;
        finalize) _status_one finalize ;;
      esac
    fi
    ;;
  status)
    for s in portal upload finalize detect; do _status_one "$s"; done
    ;;
  smoke) cmd_smoke ;;
  mono)  cmd_mono ;;
  install-nightly)
    exec bash "$ROOT/scripts/install_nightly_restart.sh"
    ;;
  uninstall-nightly)
    exec bash "$ROOT/scripts/install_nightly_restart.sh" --remove
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|smoke|mono|install-nightly|uninstall-nightly} [service]"
    exit 1
    ;;
esac
