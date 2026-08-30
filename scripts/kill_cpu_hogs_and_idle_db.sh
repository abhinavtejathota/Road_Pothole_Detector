#!/usr/bin/env bash
# AceCloud: kill fake "upowerd" miners (often owned by postgres OS user) +
# terminate idle Postgres backends. NOT related to SmartRoad app bugs.
#
#   sudo bash scripts/kill_cpu_hogs_and_idle_db.sh
#
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: run as root → sudo bash scripts/kill_cpu_hogs_and_idle_db.sh" >&2
  exit 1
fi

echo "=== load / top CPU (before) ==="
uptime || true
ps -eo pid,user,%cpu,%mem,etime,cmd --sort=-%cpu | head -n 25

echo
echo "=== suspicious: upowerd as non-root (often crypto-miner) ==="
# Real upowerd is a system daemon under root/messagebus — never as postgres.
mapfile -t BAD < <(ps -eo pid=,user=,cmd= | awk '
  $2!="root" && $0 ~ /upowerd/ { print $1 }
')
if [[ ${#BAD[@]} -eq 0 ]]; then
  echo "(none found)"
else
  echo "Killing PIDs: ${BAD[*]}"
  for pid in "${BAD[@]}"; do
    echo "  kill -9 $pid  ($(ps -p "$pid" -o user=,cmd= 2>/dev/null || true))"
    kill -9 "$pid" 2>/dev/null || true
  done
fi

# Also catch renamed clones that eat a whole core under postgres user
echo
echo "=== postgres OS user processes that are NOT postgres DB ==="
ps -eo pid,user,cmd | awk '
  $2=="postgres" && $0 !~ /postgres:/ && $0 !~ /\/usr\/lib\/postgresql/ && $0 !~ /postgres -D/ {
    print
  }
' || true

echo
echo "=== real Postgres backends (db sessions) ==="
if command -v sudo >/dev/null && id postgres &>/dev/null; then
  sudo -u postgres psql -d postgres -c "
    SELECT pid, usename, datname, state, wait_event_type, wait_event,
           now()-state_change AS idle_for, left(query, 80) AS query
    FROM pg_stat_activity
    WHERE pid <> pg_backend_pid()
    ORDER BY state_change NULLS LAST;
  " 2>/dev/null || echo "(psql failed — is cluster up?)"

  echo
  echo "Terminating idle / idle-in-transaction older than 5 minutes…"
  sudo -u postgres psql -d postgres -v ON_ERROR_STOP=0 -c "
    SELECT pg_terminate_backend(pid)
    FROM pg_stat_activity
    WHERE pid <> pg_backend_pid()
      AND state IN ('idle', 'idle in transaction', 'idle in transaction (aborted)')
      AND state_change < now() - interval '5 minutes';
  " 2>/dev/null || true
else
  echo "(no postgres OS user / psql)"
fi

echo
echo "=== load (after) ==="
sleep 1
uptime || true
ps -eo pid,user,%cpu,%mem,cmd --sort=-%cpu | head -n 15

echo
echo "Next: restart SmartRoad so it can use the freed CPUs:"
echo "  cd /app/Smart_Road_Rec/SmartRoadApp && ./scripts/services.sh restart"
echo "  ./scripts/services.sh smoke"
