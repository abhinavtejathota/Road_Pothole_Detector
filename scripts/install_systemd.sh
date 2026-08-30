#!/usr/bin/env bash
# Install SmartRoad portal + finalize worker as systemd services.
#
# Usage (AceCloud):
#   sudo ./scripts/install_systemd.sh
#
# Units:
#   smartroad.service         — Flask portal (light)
#   smartroad-worker.service  — ffmpeg/S3 finalize queue (heavy, Nice=10)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORTAL_SRC="$ROOT/scripts/smartroad.service.in"
WORKER_SRC="$ROOT/scripts/smartroad-worker.service.in"
PORTAL_DST="/etc/systemd/system/smartroad.service"
WORKER_DST="/etc/systemd/system/smartroad-worker.service"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root:  sudo $0" >&2
  exit 1
fi

for f in "$PORTAL_SRC" "$WORKER_SRC"; do
  if [[ ! -f "$f" ]]; then
    echo "Missing $f" >&2
    exit 1
  fi
done

OWNER="$(stat -c '%U' "$ROOT" 2>/dev/null || echo root)"
GROUP="$(stat -c '%G' "$ROOT" 2>/dev/null || echo root)"
if [[ "$OWNER" == "root" ]] && [[ -n "${SUDO_USER:-}" ]]; then
  OWNER="$SUDO_USER"
  GROUP="$(id -gn "$SUDO_USER")"
fi

chmod +x "$ROOT/scripts/run_smartroad.sh" "$ROOT/scripts/smartroad_worker.py" \
  "$ROOT/scripts/finalize_chunk_job.py" "$ROOT/scripts/probe_s3_latency.py" 2>/dev/null || true

# Pick python for portal + worker
PY="$ROOT/venv/bin/python"
if [[ ! -x "$PY" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PY="$ROOT/.venv/bin/python"
  else
    PY="$(command -v python3 || command -v python)"
  fi
fi

if [[ -x "$ROOT/scripts/run_smartroad.sh" ]]; then
  sudo -u "$OWNER" bash "$ROOT/scripts/run_smartroad.sh" stop 2>/dev/null || true
fi
systemctl stop smartroad.service 2>/dev/null || true
systemctl stop smartroad-worker.service 2>/dev/null || true

# Free :5005 if a manual python web_app.py is holding it
PORT="$(grep -E '^FLASK_PORT=' "$ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r' || true)"
PORT="${PORT:-5005}"
if command -v fuser >/dev/null 2>&1; then
  fuser -k "${PORT}/tcp" 2>/dev/null || true
elif command -v ss >/dev/null 2>&1; then
  for p in $(ss -lptn "sport = :$PORT" 2>/dev/null | grep -oP 'pid=\K[0-9]+' || true); do
    kill "$p" 2>/dev/null || true
  done
fi
sleep 1

install_unit() {
  local src="$1" dst="$2"
  local tmp
  tmp="$(mktemp)"
  sed \
    -e "s|__ROOT__|$ROOT|g" \
    -e "s|__USER__|$OWNER|g" \
    -e "s|__GROUP__|$GROUP|g" \
    "$src" >"$tmp"
  # Always pin the resolved interpreter (venv may not be named venv/).
  if [[ "$dst" == "$PORTAL_DST" ]]; then
    sed -i "s|^ExecStart=.*|ExecStart=$PY -u $ROOT/web_app.py|" "$tmp"
  elif [[ "$dst" == "$WORKER_DST" ]]; then
    sed -i "s|^ExecStart=.*|ExecStart=$PY -u $ROOT/scripts/smartroad_worker.py|" "$tmp"
  fi
  install -m 644 "$tmp" "$dst"
  rm -f "$tmp"
}

install_unit "$PORTAL_SRC" "$PORTAL_DST"
install_unit "$WORKER_SRC" "$WORKER_DST"

mkdir -p "$ROOT/data/finalize_queue"/{pending,running,done,failed}
chown -R "$OWNER:$GROUP" "$ROOT/data/finalize_queue" 2>/dev/null || true

systemctl daemon-reload
systemctl enable smartroad.service smartroad-worker.service
systemctl restart smartroad.service
systemctl restart smartroad-worker.service

echo
echo "Installed:"
echo "  $PORTAL_DST   (portal)"
echo "  $WORKER_DST   (finalize worker)  python=$PY"
echo "  User=$OWNER  ROOT=$ROOT"
sleep 2
systemctl --no-pager --full status smartroad.service || true
echo
systemctl --no-pager --full status smartroad-worker.service || true
echo
PORT="$(grep -E '^FLASK_PORT=' "$ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r' || true)"
PORT="${PORT:-5005}"
if command -v curl >/dev/null 2>&1; then
  echo "Health:"
  curl -s --max-time 10 "http://127.0.0.1:${PORT}/api/health" || echo "(not ready)"
  echo
fi
echo "Portal + worker enabled on boot."
echo "  journalctl -u smartroad -f"
echo "  journalctl -u smartroad-worker -f"
