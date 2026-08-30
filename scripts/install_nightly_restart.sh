#!/usr/bin/env bash
# Install (or remove) a cron job that restarts all SmartRoad services at 00:00 IST nightly.
#
#   sudo bash scripts/install_nightly_restart.sh
#   sudo bash scripts/install_nightly_restart.sh --remove
#   ./scripts/services.sh install-nightly
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CRON_FILE="/etc/cron.d/smartroad-nightly"
SERVICES="$ROOT/scripts/services.sh"
LOG="$ROOT/data/logs/nightly_restart.log"

REMOVE=0
if [[ "${1:-}" == "--remove" ]]; then
  REMOVE=1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: run as root → sudo bash scripts/install_nightly_restart.sh" >&2
  exit 1
fi

mkdir -p "$ROOT/data/logs"
chmod +x "$SERVICES" "$ROOT/scripts/install_nightly_restart.sh" 2>/dev/null || true

if [[ "$REMOVE" -eq 1 ]]; then
  rm -f "$CRON_FILE"
  echo "OK  removed $CRON_FILE"
  exit 0
fi

# Detect an app user that owns the repo (prefer non-root for running python/venv).
RUN_AS="${SMARTROAD_CRON_USER:-}"
if [[ -z "$RUN_AS" ]]; then
  if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    RUN_AS="$SUDO_USER"
  else
    owner="$(stat -c '%U' "$ROOT" 2>/dev/null || true)"
    if [[ -n "$owner" && "$owner" != "root" ]]; then
      RUN_AS="$owner"
    else
      RUN_AS="root"
    fi
  fi
fi

cat >"$CRON_FILE" <<EOF
# SmartRoad — restart portal/upload/finalize/detect every night at 00:00 Asia/Kolkata
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
CRON_TZ=Asia/Kolkata

0 0 * * * ${RUN_AS} cd ${ROOT} && /bin/bash ${SERVICES} restart >>${LOG} 2>&1
EOF
chmod 644 "$CRON_FILE"

echo "OK  installed $CRON_FILE"
echo "    schedule: 00:00 IST (CRON_TZ=Asia/Kolkata)"
echo "    user:     $RUN_AS"
echo "    command:  $SERVICES restart"
echo "    log:      $LOG"
echo
echo "Verify: sudo cat $CRON_FILE"
echo "Manual: cd $ROOT && ./scripts/services.sh restart"
