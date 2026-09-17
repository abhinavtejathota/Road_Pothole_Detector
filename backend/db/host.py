from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
import json
import math
import os
import time

# db package module — load repo-root .env (not backend/.env)
from pathlib import Path
from dotenv import load_dotenv
_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env", override=True)

import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.pool
    import psycopg2.extensions
    _HAS_PSYCOPG2 = True
except ImportError:
    _HAS_PSYCOPG2 = False

def _local_ipv4_addrs() -> set[str]:
    """Addresses that belong to this host (for DB_HOST hairpin detection)."""
    addrs = {"127.0.0.1", "0.0.0.0", "::1"}
    try:
        hostname = socket.gethostname()
        addrs.add(hostname)
        for info in socket.getaddrinfo(hostname, None):
            addrs.add(info[4][0])
    except Exception:
        pass
    try:
        # Primary outbound IP (usually the AceCloud NIC / public bind)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        addrs.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    # Explicit public host often set in deploy docs / .env
    for key in ("PUBLIC_IP", "PUBLIC_HOST", "SERVER_IP", "FLASK_PUBLIC_HOST"):
        v = (os.getenv(key) or "").strip()
        if v:
            addrs.add(v)
    return addrs


def _localhost_postgres_reachable(timeout_s: float = 0.35) -> bool:
    """True when something accepts TCP on 127.0.0.1:DB_PORT (AceCloud co-located DB)."""
    port = int(os.getenv("DB_PORT", "5432"))
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout_s):
            return True
    except OSError:
        return False


def resolve_db_host() -> str:
    """Return DB host, rewriting self-public-IP → 127.0.0.1.

    ``DB_HOST=45.194.2.247`` on the AceCloud box itself hairpins through NAT
    when the VM NIC is a private address and 45.x is only on the floating IP.
    That makes /api/auth/login hang while shallow /api/health still looks fine.
    """
    host = (os.getenv("DB_HOST") or "localhost").strip() or "localhost"
    if host in ("localhost", "127.0.0.1", "::1"):
        return host
    if os.getenv("DB_ALLOW_HAIRPIN", "").lower() in ("1", "true", "yes"):
        return host
    # Set by scripts/run_smartroad.sh when local Postgres is detected.
    if os.getenv("DB_FORCE_LOCALHOST", "").lower() in ("1", "true", "yes"):
        print(f"[db] DB_FORCE_LOCALHOST=1 — using 127.0.0.1 (was DB_HOST={host})", flush=True)
        return "127.0.0.1"
    try:
        locals_ = _local_ipv4_addrs()
        if host in locals_:
            print(
                f"[db] DB_HOST={host} is this machine — using 127.0.0.1 "
                "(avoids NAT hairpin that stalls login)",
                flush=True,
            )
            return "127.0.0.1"
        # Floating/public IP in .env but only private NIC on the VM: still hairpins.
        pub = (os.getenv("PUBLIC_IP") or os.getenv("SERVER_IP") or "").strip()
        if pub and host == pub and _localhost_postgres_reachable():
            print(
                f"[db] DB_HOST={host} is PUBLIC_IP and Postgres is on loopback — "
                "using 127.0.0.1",
                flush=True,
            )
            return "127.0.0.1"
        # AceCloud: run_smartroad.sh sets DB_FORCE_LOCALHOST=1. Optional:
        # DB_COALESCE_LOCAL=1 rewrites any remote host when loopback:5432 accepts.
        if (
            os.getenv("DB_COALESCE_LOCAL", "").lower() in ("1", "true", "yes")
            and _localhost_postgres_reachable()
        ):
            print(
                f"[db] DB_HOST={host} but Postgres listens on 127.0.0.1 — "
                "using loopback (DB_COALESCE_LOCAL=1)",
                flush=True,
            )
            return "127.0.0.1"
    except Exception:
        pass
    return host


