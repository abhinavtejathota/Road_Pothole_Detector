#!/usr/bin/env bash
# SmartRoad — always-on runner (nohup + watchdog, or systemd supervise)
#
# Same process model as manual `nohup python -u web_app.py` (Werkzeug threaded).
# No gunicorn / waitress / .env.example overlays.
#
# Keep the process listening forever. Do NOT spin down when idle.
# Recovery layers:
#   1) Watchdog / supervise — restart if process dies or /api/health hangs
#   2) systemd (optional)  — Restart=always + boot on reboot
#      sudo ./scripts/install_systemd.sh
#
# Usage:
#   ./scripts/run_smartroad.sh start|stop|restart|status|logs|supervise|install-hint

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${FLASK_PORT:-${PORT:-5005}}"
PID_FILE="${PID_FILE:-$ROOT/data/smartroad.pid}"
WATCH_PID_FILE="${WATCH_PID_FILE:-$ROOT/data/smartroad.watchdog.pid}"
WORKER_PID_FILE="${WORKER_PID_FILE:-$ROOT/data/smartroad.worker.pid}"
LOG_DIR="${LOG_DIR:-$ROOT/data/logs}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/smartroad.log}"
WATCH_LOG="${WATCH_LOG:-$LOG_DIR/watchdog.log}"
WORKER_LOG="${WORKER_LOG:-$LOG_DIR/worker.log}"
NOHUP_OUT="${NOHUP_OUT:-$ROOT/nohup.out}"
LOG_MAX_BYTES="${LOG_MAX_BYTES:-52428800}"
VENV="${VENV:-$ROOT/venv}"
WATCH_INTERVAL_S="${WATCH_INTERVAL_S:-15}"
WATCH_FAILS="${WATCH_FAILS:-2}"
WATCH_CURL_S="${WATCH_CURL_S:-6}"
WATCH_COOLDOWN_S="${WATCH_COOLDOWN_S:-45}"
# After a failed start, keep trying forever (always-on).
START_RETRY_S="${START_RETRY_S:-8}"

mkdir -p "$(dirname "$PID_FILE")" "$LOG_DIR"

logw() {
  echo "$(date -Is) $*" | tee -a "$WATCH_LOG" >/dev/null
  echo "$(date -Is) $*"
}

load_dotenv() {
  local f="$ROOT/.env"
  if [[ ! -f "$f" ]]; then
    echo "WARN: no $f — DB/S3 secrets may be missing" >&2
    return 0
  fi
  local tmp
  tmp="$(mktemp)"
  grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$f" | tr -d '\r' >"$tmp" || true
  set -a
  # shellcheck disable=SC1090
  source "$tmp"
  set +a
  rm -f "$tmp"
  echo "Loaded .env"
}

activate_venv() {
  if [[ -f "$VENV/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "$VENV/bin/activate"
  elif [[ -f "$ROOT/.venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "$ROOT/.venv/bin/activate"
  fi
}

export_accel_defaults() {
  export FLASK_PORT="${FLASK_PORT:-$PORT}"
  export PORT="$FLASK_PORT"
  PORT="$FLASK_PORT"
  export SMARTROAD_SERVER=werkzeug
  export FLASK_DEBUG="${FLASK_DEBUG:-false}"
  # Portal stays light; assemble/S3 runs in smartroad-worker (separate process).
  export CHUNK_UPLOAD_CONCURRENCY="${CHUNK_UPLOAD_CONCURRENCY:-3}"
  export FINALIZE_EXTERNAL_WORKER="${FINALIZE_EXTERNAL_WORKER:-1}"
  export FINALIZE_CONCURRENCY="${FINALIZE_CONCURRENCY:-1}"
  export FINALIZE_SUBPROCESS="${FINALIZE_SUBPROCESS:-1}"
  export FINALIZE_FFMPEG_THREADS="${FINALIZE_FFMPEG_THREADS:-2}"
  export FINALIZE_S3_CONCURRENCY="${FINALIZE_S3_CONCURRENCY:-8}"
  export SMARTROAD_HEAVY_CONCURRENCY="${SMARTROAD_HEAVY_CONCURRENCY:-6}"
  export DB_POOL_MAX="${DB_POOL_MAX:-24}"
  # Fail fast — never let hairpin/zombie DB pin Werkzeug threads for 20–45s.
  export DB_CONNECT_TIMEOUT="${DB_CONNECT_TIMEOUT:-2}"
  export DB_PING_TIMEOUT_S="${DB_PING_TIMEOUT_S:-2}"
  export DB_POOL_GET_TIMEOUT_S="${DB_POOL_GET_TIMEOUT_S:-2}"
  export DB_POOL_GET_RETRIES="${DB_POOL_GET_RETRIES:-2}"
  export DB_STATEMENT_TIMEOUT_MS="${DB_STATEMENT_TIMEOUT_MS:-8000}"
  export DB_KEEPALIVES_IDLE="${DB_KEEPALIVES_IDLE:-10}"
  export DB_KEEPALIVES_INTERVAL="${DB_KEEPALIVES_INTERVAL:-5}"
  # Critical: AceCloud Postgres is on this VM. Public IP hairpins NAT and
  # freezes login. Override even if .env still has DB_HOST=45.194.2.247.
  if [[ "${DB_ALLOW_HAIRPIN:-}" != "1" ]]; then
    if ss -lnt 2>/dev/null | grep -q ':5432' || ss -lntp 2>/dev/null | grep -q ':5432'; then
      if [[ "${DB_HOST:-}" != "127.0.0.1" && "${DB_HOST:-}" != "localhost" ]]; then
        echo "WARN: overriding DB_HOST=${DB_HOST:-unset} → 127.0.0.1 (local :5432 detected)"
      fi
      export DB_HOST=127.0.0.1
      export DB_FORCE_LOCALHOST=1
    fi
  fi
  export YOLO_DEVICE="${YOLO_DEVICE:-0}"
  export POTHOLE_HALF="${POTHOLE_HALF:-1}"
  export POTHOLE_CPU_THREADS="${POTHOLE_CPU_THREADS:-8}"
  export FFMPEG_HW="${FFMPEG_HW:-auto}"
  export FFMPEG_PREFER_SYSTEM="${FFMPEG_PREFER_SYSTEM:-1}"
  export FFMPEG_NVENC_PRESET="${FFMPEG_NVENC_PRESET:-p4}"
  export S3_UPLOAD_CONCURRENCY="${S3_UPLOAD_CONCURRENCY:-32}"
  export S3_MULTIPART_CHUNK_MB="${S3_MULTIPART_CHUNK_MB:-16}"
}

pids_on_port() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true
  elif command -v fuser >/dev/null 2>&1; then
    fuser -n tcp "$PORT" 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+$' || true
  else
    ss -lptn "sport = :$PORT" 2>/dev/null | grep -oP 'pid=\K[0-9]+' || true
  fi
}

app_listening() {
  [[ -n "$(pids_on_port)" ]]
}

health_code() {
  if ! command -v curl >/dev/null 2>&1; then
    # Without curl, treat listening process as healthy enough.
    if app_listening; then echo 200; else echo 000; fi
    return 0
  fi
  curl -s -o /dev/null -w '%{http_code}' --max-time "$WATCH_CURL_S" \
    "http://127.0.0.1:${PORT}/api/health" 2>/dev/null || echo 000
}

rotate_log_if_needed() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  local sz
  sz="$(wc -c <"$f" 2>/dev/null | tr -d ' ' || echo 0)"
  if [[ "${sz:-0}" -lt "$LOG_MAX_BYTES" ]]; then
    return 0
  fi
  local stamp archived
  stamp="$(date +%Y%m%d_%H%M%S)"
  archived="${f}.${stamp}"
  mv -f "$f" "$archived" 2>/dev/null || true
  if command -v gzip >/dev/null 2>&1 && [[ -f "$archived" ]]; then
    gzip -f "$archived" 2>/dev/null || true
  fi
  ls -1t "${f}".*.gz "${f}".[0-9]* 2>/dev/null | tail -n +6 | xargs -r rm -f || true
  echo "Rotated oversized log → ${archived}.gz (was ${sz} bytes)"
}

clear_logs() {
  rotate_log_if_needed "$LOG_FILE"
  : >"$LOG_FILE" 2>/dev/null || true
  rm -f "$NOHUP_OUT" 2>/dev/null || true
  rm -f "$ROOT/nohup.out" 2>/dev/null || true
}

stop_app_only() {
  local master=""
  if [[ -f "$PID_FILE" ]]; then
    master="$(cat "$PID_FILE" 2>/dev/null || true)"
  fi
  if [[ -n "${master:-}" ]] && kill -0 "$master" 2>/dev/null; then
    kill -TERM "$master" 2>/dev/null || true
    for _ in $(seq 1 40); do
      kill -0 "$master" 2>/dev/null || break
      sleep 0.25
    done
    kill -KILL "$master" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"

  local pids
  pids="$(pids_on_port)"
  if [[ -n "${pids:-}" ]]; then
    echo "$pids" | xargs -r kill -TERM 2>/dev/null || true
    sleep 1
    pids="$(pids_on_port)"
    if [[ -n "${pids:-}" ]]; then
      echo "$pids" | xargs -r kill -KILL 2>/dev/null || true
    fi
  fi
  pkill -f "python[0-9]* -[u]* *$ROOT/web_app.py" 2>/dev/null || true
  pkill -f "python[0-9]* -[u]* *web_app.py" 2>/dev/null || true
  sleep 0.4
}

stop_watchdog() {
  local wpid=""
  if [[ -f "$WATCH_PID_FILE" ]]; then
    wpid="$(cat "$WATCH_PID_FILE" 2>/dev/null || true)"
  fi
  if [[ -n "${wpid:-}" ]] && kill -0 "$wpid" 2>/dev/null; then
    kill -TERM "$wpid" 2>/dev/null || true
    sleep 0.5
    kill -KILL "$wpid" 2>/dev/null || true
  fi
  rm -f "$WATCH_PID_FILE"
  pkill -f "smartroad.watchdog.$PORT" 2>/dev/null || true
}

stop_worker() {
  local wpid=""
  if [[ -f "$WORKER_PID_FILE" ]]; then
    wpid="$(cat "$WORKER_PID_FILE" 2>/dev/null || true)"
  fi
  if [[ -n "${wpid:-}" ]] && kill -0 "$wpid" 2>/dev/null; then
    kill -TERM "$wpid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$wpid" 2>/dev/null || break
      sleep 0.3
    done
    kill -KILL "$wpid" 2>/dev/null || true
  fi
  rm -f "$WORKER_PID_FILE"
  pkill -f "smartroad_worker.py" 2>/dev/null || true
}

start_worker() {
  # When systemd owns the worker, do not double-start.
  if systemctl is-active --quiet smartroad-worker 2>/dev/null; then
    echo "Finalize worker: systemd smartroad-worker (active)"
    return 0
  fi
  load_dotenv
  activate_venv
  export_accel_defaults
  mkdir -p "$ROOT/data/finalize_queue"/{pending,running,done,failed} "$LOG_DIR"
  touch "$WORKER_LOG"
  if [[ -f "$WORKER_PID_FILE" ]]; then
    local old
    old="$(cat "$WORKER_PID_FILE" 2>/dev/null || true)"
    if [[ -n "${old:-}" ]] && kill -0 "$old" 2>/dev/null; then
      echo "Finalize worker already running (PID $old)"
      return 0
    fi
  fi
  echo "Starting finalize worker → python -u scripts/smartroad_worker.py"
  echo "  log: $WORKER_LOG"
  nohup python -u "$ROOT/scripts/smartroad_worker.py" >>"$WORKER_LOG" 2>&1 &
  local pid=$!
  echo "$pid" >"$WORKER_PID_FILE"
  disown "$pid" 2>/dev/null || true
  sleep 0.5
  if kill -0 "$pid" 2>/dev/null; then
    echo "OK — worker PID $pid (ffmpeg/S3 out of portal process)"
  else
    echo "ERROR: worker did not stay up — see $WORKER_LOG" >&2
    return 1
  fi
}

stop_cmd() {
  echo "Stopping SmartRoad on :$PORT …"
  stop_watchdog
  stop_worker
  stop_app_only
  clear_logs
  echo "Stopped. Logs cleared ($LOG_FILE / nohup.out)."
}

start_app_only() {
  load_dotenv
  activate_venv
  export_accel_defaults

  if app_listening; then
    echo "Port :$PORT already in use by PID(s): $(pids_on_port)"
    echo "Run: $0 restart   (or soft-restart from watchdog)"
    return 1
  fi

  rotate_log_if_needed "$LOG_FILE"
  touch "$LOG_FILE"
  rm -f "$NOHUP_OUT" "$ROOT/nohup.out" 2>/dev/null || true

  echo "Starting SmartRoad → nohup python -u web_app.py  (0.0.0.0:${PORT})"
  echo "  log: $LOG_FILE"
  echo "  caps: chunk_upload=$CHUNK_UPLOAD_CONCURRENCY external_worker=$FINALIZE_EXTERNAL_WORKER heavy=$SMARTROAD_HEAVY_CONCURRENCY"
  echo "  accel: YOLO_DEVICE=$YOLO_DEVICE POTHOLE_HALF=$POTHOLE_HALF FFMPEG_HW=$FFMPEG_HW"

  nohup python -u "$ROOT/web_app.py" >>"$LOG_FILE" 2>&1 &
  local pid=$!
  echo "$pid" >"$PID_FILE"
  disown "$pid" 2>/dev/null || true

  local ok=0
  local i
  for i in $(seq 1 80); do
    if app_listening; then
      ok=1
      break
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      break
    fi
    sleep 0.4
  done

  if [[ "$ok" -ne 1 ]]; then
    echo "ERROR: server did not stay up. Tail of log:" >&2
    tail -n 80 "$LOG_FILE" >&2 || true
    rm -f "$PID_FILE"
    return 1
  fi

  local listen
  listen="$(pids_on_port | head -n1 || true)"
  if [[ -n "${listen:-}" ]]; then
    echo "$listen" >"$PID_FILE"
  fi
  echo "OK — PID $(cat "$PID_FILE")  (engine=werkzeug / nohup python)"

  local code
  code="$(health_code)"
  echo "Health /api/health → HTTP $code"
  if [[ "$code" != "200" ]]; then
    sleep 2
    code="$(health_code)"
    echo "Health retry → HTTP $code"
  fi
  # Listening is enough to hand off; watchdog/supervise recover if health stays bad.
  if app_listening; then
    return 0
  fi
  return 1
}

# Keep trying until the app is up (used by supervise + soft-restart).
ensure_app_up() {
  local attempt=0
  while true; do
    attempt=$((attempt + 1))
    if app_listening; then
      local code
      code="$(health_code)"
      if [[ "$code" == "200" ]]; then
        return 0
      fi
      logw "port up but health=$code — soft restart (attempt $attempt)"
      stop_app_only
    else
      logw "app not listening — start (attempt $attempt)"
    fi
    if start_app_only; then
      return 0
    fi
    logw "start failed — retry in ${START_RETRY_S}s"
    stop_app_only
    sleep "$START_RETRY_S"
  done
}

soft_restart_cmd() {
  echo "$(date -Is) soft-restart begin" >>"$WATCH_LOG"
  stop_app_only
  # Keep logs for post-mortem of the hang/crash.
  if ensure_app_up; then
    echo "$(date -Is) soft-restart OK pid=$(cat "$PID_FILE" 2>/dev/null || echo ?)" >>"$WATCH_LOG"
    return 0
  fi
  # ensure_app_up only returns on success (infinite loop); unreachable.
  return 1
}

watch_once_tick() {
  # Returns 0 if healthy, 1 if should count a failure / restart.
  if ! app_listening; then
    logw "DEAD — nothing listening on :$PORT"
    return 1
  fi
  local code
  code="$(health_code)"
  if [[ "$code" == "200" ]]; then
    return 0
  fi
  logw "health HTTP $code"
  return 1
}

start_watchdog() {
  stop_watchdog
  touch "$WATCH_LOG"
  nohup bash -c '
    MARK="smartroad.watchdog.'"$PORT"'"
    ROOT="'"$ROOT"'"
    PORT="'"$PORT"'"
    PID_FILE="'"$PID_FILE"'"
    WATCH_PID_FILE="'"$WATCH_PID_FILE"'"
    LOG_FILE="'"$LOG_FILE"'"
    WATCH_LOG="'"$WATCH_LOG"'"
    WATCH_INTERVAL_S="'"$WATCH_INTERVAL_S"'"
    WATCH_FAILS="'"$WATCH_FAILS"'"
    WATCH_CURL_S="'"$WATCH_CURL_S"'"
    WATCH_COOLDOWN_S="'"$WATCH_COOLDOWN_S"'"
    START_RETRY_S="'"$START_RETRY_S"'"
    SCRIPT="'"$ROOT"'/scripts/run_smartroad.sh"
    fails=0
    echo "$(date -Is) watchdog start mark=$MARK interval=${WATCH_INTERVAL_S}s fails=${WATCH_FAILS}" >>"$WATCH_LOG"
    while true; do
      sleep "$WATCH_INTERVAL_S"
      # Dead process → restart immediately (do not wait for N health fails).
      listen=""
      if command -v lsof >/dev/null 2>&1; then
        listen="$(lsof -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
      elif command -v fuser >/dev/null 2>&1; then
        listen="$(fuser -n tcp "$PORT" 2>/dev/null | tr " " "\n" | grep -E "^[0-9]+$" || true)"
      else
        listen="$(ss -lptn "sport = :$PORT" 2>/dev/null | grep -oP "pid=\K[0-9]+" || true)"
      fi
      code="000"
      if command -v curl >/dev/null 2>&1; then
        code="$(curl -s -o /dev/null -w "%{http_code}" --max-time "$WATCH_CURL_S" "http://127.0.0.1:${PORT}/api/health" 2>/dev/null || echo 000)"
      elif [[ -n "$listen" ]]; then
        code="200"
      fi
      if [[ -n "$listen" && "$code" == "200" ]]; then
        fails=0
        continue
      fi
      if [[ -z "$listen" ]]; then
        echo "$(date -Is) DEAD — nothing on :$PORT — immediate soft-restart" >>"$WATCH_LOG"
        fails="$WATCH_FAILS"
      else
        fails=$((fails + 1))
        echo "$(date -Is) health HTTP $code fail=$fails/$WATCH_FAILS" >>"$WATCH_LOG"
      fi
      if [[ "$fails" -lt "$WATCH_FAILS" ]]; then
        continue
      fi
      echo "$(date -Is) RESTART — recovering always-on service" >>"$WATCH_LOG"
      bash "$SCRIPT" soft-restart >>"$WATCH_LOG" 2>&1 || true
      fails=0
      sleep "$WATCH_COOLDOWN_S"
    done
  ' smartroad.watchdog."$PORT" >>"$WATCH_LOG" 2>&1 &
  local wpid=$!
  echo "$wpid" >"$WATCH_PID_FILE"
  disown "$wpid" 2>/dev/null || true
  echo "Watchdog PID $wpid (every ${WATCH_INTERVAL_S}s; dead→instant / health ${WATCH_FAILS} misses) → $WATCH_LOG"
}

start_cmd() {
  # If already healthy, refresh watchdog + worker — never leave a gap.
  if app_listening && [[ "$(health_code)" == "200" ]]; then
    echo "Already healthy on :$PORT — ensuring watchdog + finalize worker"
    start_watchdog
    start_worker || true
    status_cmd
    return 0
  fi
  ensure_app_up
  start_watchdog
  start_worker || true
}

# Foreground supervisor for systemd: never exits unless SIGTERM.
# systemd Restart=always covers supervisor death; this covers app hang/crash.
supervise_cmd() {
  touch "$WATCH_LOG"
  logw "supervise start (foreground always-on) port=$PORT"
  trap 'logw "supervise got signal — stopping app"; stop_app_only; exit 0' TERM INT

  ensure_app_up
  local fails=0
  while true; do
    sleep "$WATCH_INTERVAL_S"
    if watch_once_tick; then
      fails=0
      continue
    fi
    fails=$((fails + 1))
    # Immediate if dead; else accumulate health fails.
    if ! app_listening; then
      fails="$WATCH_FAILS"
    fi
    if [[ "$fails" -lt "$WATCH_FAILS" ]]; then
      continue
    fi
    logw "supervise RESTART"
    stop_app_only
    ensure_app_up
    fails=0
    sleep "$WATCH_COOLDOWN_S"
  done
}

status_cmd() {
  load_dotenv
  PORT="${FLASK_PORT:-$PORT}"
  local master=""
  [[ -f "$PID_FILE" ]] && master="$(cat "$PID_FILE" 2>/dev/null || true)"
  echo "PID file: ${master:-none}"
  if [[ -n "${master:-}" ]] && kill -0 "$master" 2>/dev/null; then
    echo "Master:  running ($master)"
  else
    echo "Master:  not running"
  fi
  local wpid=""
  [[ -f "$WATCH_PID_FILE" ]] && wpid="$(cat "$WATCH_PID_FILE" 2>/dev/null || true)"
  if [[ -n "${wpid:-}" ]] && kill -0 "$wpid" 2>/dev/null; then
    echo "Watchdog: running ($wpid)"
  else
    echo "Watchdog: not running"
  fi
  if systemctl is-active --quiet smartroad 2>/dev/null; then
    echo "systemd portal:  active (smartroad.service)"
  elif systemctl list-unit-files smartroad.service >/dev/null 2>&1; then
    echo "systemd portal:  installed but inactive"
  else
    echo "systemd portal:  not installed — sudo ./scripts/install_systemd.sh"
  fi
  if systemctl is-active --quiet smartroad-worker 2>/dev/null; then
    echo "systemd worker:  active (smartroad-worker.service)"
  else
    local wpid2=""
    [[ -f "$WORKER_PID_FILE" ]] && wpid2="$(cat "$WORKER_PID_FILE" 2>/dev/null || true)"
    if [[ -n "${wpid2:-}" ]] && kill -0 "$wpid2" 2>/dev/null; then
      echo "Finalize worker: running ($wpid2) log=$WORKER_LOG"
    else
      echo "Finalize worker: not running — uploads queue but will not assemble until worker starts"
    fi
  fi
  echo "Engine:  werkzeug portal + separate finalize worker"
  echo "Port:    $PORT"
  echo "Log:     $LOG_FILE"
  echo "Watch:   $WATCH_LOG"
  echo "Worker:  $WORKER_LOG"
  if [[ -f "$LOG_FILE" ]]; then
    echo "Log size: $(wc -c <"$LOG_FILE" | tr -d ' ') bytes"
  fi
  echo "LISTEN PIDs on :$PORT:"
  pids_on_port | sed 's/^/  /' || echo "  (none)"
  if command -v curl >/dev/null 2>&1; then
    curl -s --max-time 5 "http://127.0.0.1:${PORT}/api/health" || echo "(health unreachable)"
    echo
  fi
}

logs_cmd() {
  mkdir -p "$LOG_DIR"
  touch "$LOG_FILE"
  rotate_log_if_needed "$LOG_FILE"
  touch "$LOG_FILE"
  tail -n "${LINES:-100}" -f "$LOG_FILE"
}

install_hint_cmd() {
  cat <<EOF
Recommended for AceCloud (survives SSH disconnect + reboot):

  sudo ./scripts/install_systemd.sh
  sudo systemctl status smartroad
  curl -s http://127.0.0.1:${PORT}/api/health

Without systemd (same terminal session not required):

  ./scripts/run_smartroad.sh restart
  ./scripts/run_smartroad.sh status

Expect health JSON with: "yolo_device":"0","ffmpeg_encode":"nvenc"
EOF
}

CMD="${1:-start}"
case "$CMD" in
  start|"")       start_cmd ;;
  stop)           stop_cmd ;;
  soft-restart)   soft_restart_cmd ;;
  supervise)      supervise_cmd ;;
  restart|--restart)
    stop_cmd
    start_cmd
    ;;
  status)         status_cmd ;;
  logs)           logs_cmd ;;
  install-hint)   install_hint_cmd ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|logs|supervise|install-hint}"
    echo "  start       — app + watchdog (survives closed terminal)"
    echo "  supervise   — foreground always-on loop (for systemd)"
    echo "  stop        — kill app + watchdog"
    echo "  restart     — stop then start"
    echo "  Prefer: sudo ./scripts/install_systemd.sh"
    exit 1
    ;;
esac
