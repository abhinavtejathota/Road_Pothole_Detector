from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
import json
import math
import os
import time

# db package module
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)

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

from db.host import resolve_db_host, _local_ipv4_addrs, _localhost_postgres_reachable  # noqa: F401

# ── Connection ────────────────────────────────────────────────────────────────
#
# Every function below follows `conn = _get_conn(); ...; finally: conn.close()`.
# Opening a brand-new TCP connection to Postgres per query (the old behaviour)
# is what caused "FATAL: sorry, too many clients already" under real traffic
# (mobile tracking pings every ~1s from multiple videographers, concurrent
# uploads, etc.) — connections were opened/closed faster than the OS/Postgres
# could reap them, and briefly exceeded the server's max_connections.
#
# Fix: pool real connections and make `.close()` release back to the pool
# instead of dropping the socket, so every one of those existing call sites
# keeps working unmodified while the app now holds a small, bounded number of
# real connections to Postgres regardless of request volume.
#
# Also: AceCloud (Noida) → remote Postgres can leave zombie sockets after NAT
# idle drops. A query on a zombie hangs forever (no statement_timeout fire).
# _get_conn() pings each checkout with a hard thread timeout and discards.
#
# Prefer:  with db_connection() as conn: ...
# so callers cannot forget to return the connection to the pool.

from contextlib import contextmanager


@contextmanager
def db_connection():
    """Checkout → yield → always return to pool (even on exception)."""
    conn = _get_conn()
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass

_POOL = None
_POOL_LOCK = threading.Lock()
_POOL_HOST = None  # host used when _POOL was created
_POOL_OPEN_LOCK = threading.Lock()  # single-flight open (no stampede)
_POOL_FAILS = 0  # consecutive checkout failures before any rare reset
_POOL_LAST_RESET = 0.0
# Dedicated pools so a stuck close/ping cannot starve checkout forever.
# Sized large enough for concurrent Waitress threads waiting on getconn/ping.
_PING_EXEC = ThreadPoolExecutor(max_workers=64, thread_name_prefix="db-ping")
_CLOSE_EXEC = ThreadPoolExecutor(max_workers=8, thread_name_prefix="db-close")


class _PooledConnection(psycopg2.extensions.connection if _HAS_PSYCOPG2 else object):
    """A normal psycopg2 connection whose close() returns it to the shared pool."""

    def close(self):
        pool = getattr(self, "_pool_ref", None)
        if pool is None or self.closed:
            return super().close()
        try:
            if self.get_transaction_status() != psycopg2.extensions.TRANSACTION_STATUS_IDLE:
                self.rollback()
            pool.putconn(self)
        except Exception:
            try:
                pool.putconn(self, close=True)
            except Exception:
                pass


def is_db_configured() -> bool:
    if not _HAS_PSYCOPG2:
        return False
    return bool(os.getenv("DB_NAME")) and bool(os.getenv("DB_USER"))


def _discard_conn(pool, conn) -> None:
    """Drop one bad connection; keep the rest of the sticky pool alive."""
    if conn is None:
        return

    def _do():
        try:
            try:
                conn.cancel()
            except Exception:
                pass
            try:
                pool.putconn(conn, close=True)
            except Exception:
                try:
                    super(_PooledConnection, conn).close()
                except Exception:
                    pass
        except Exception:
            pass

    try:
        _CLOSE_EXEC.submit(_do)
    except Exception:
        pass


def _web_thread_hint() -> int:
    """Best-effort count of concurrent request threads this process may run."""
    hint = 32
    for key in ("WAITRESS_THREADS", "GUNICORN_THREADS", "SMARTROAD_THREADS"):
        raw = os.getenv(key)
        if not raw:
            continue
        try:
            hint = max(hint, int(raw))
        except ValueError:
            pass
    return max(8, hint)


def _pool_bounds() -> tuple[int, int]:
    """
    Size the sticky pool so Waitress/gunicorn threads rarely wait on checkout.

    Defaults: max ≈ web_threads + headroom, capped by DB_POOL_HARD_CAP
    (leave room for sibling portal/upload/detect processes on the same Postgres).
    Explicit DB_POOL_MIN / DB_POOL_MAX always win (still hard-capped).
    """
    threads = _web_thread_hint()
    headroom = int(os.getenv("DB_POOL_HEADROOM", "64"))
    # Hard cap can go to Postgres max_connections; keep default high for multi-user
    # but leave headroom for sibling portal/upload/detect pools on the same DB.
    hard_cap = int(os.getenv("DB_POOL_HARD_CAP", "512"))
    suggested = threads + headroom
    if os.getenv("DB_POOL_MAX"):
        maxconn = int(os.getenv("DB_POOL_MAX"))
    else:
        # At least 64 so a 32-thread box still has spare; grow with Waitress.
        maxconn = max(64, suggested)
    maxconn = max(8, min(maxconn, hard_cap))
    if os.getenv("DB_POOL_MIN"):
        minconn = int(os.getenv("DB_POOL_MIN"))
    else:
        minconn = min(max(16, maxconn // 3), maxconn)
    minconn = max(1, min(minconn, maxconn))
    return minconn, maxconn


def _open_pool(db_host: str):
    # Sticky pool for the whole process — open once, reuse under multi-user load.
    minconn, maxconn = _pool_bounds()
    connect_timeout = int(os.getenv("DB_CONNECT_TIMEOUT", "5"))
    threads = _web_thread_hint()
    print(
        f"[db] opening STICKY pool host={db_host} "
        f"port={os.getenv('DB_PORT', '5432')} "
        f"db={os.getenv('DB_NAME', 'smartroad_ap')} "
        f"min={minconn} max={maxconn} "
        f"(web_threads~{threads} headroom) connect_timeout={connect_timeout}",
        flush=True,
    )
    # No idle_session_timeout — that killed idle pooled sockets and forced reopen storms.
    return psycopg2.pool.ThreadedConnectionPool(
        minconn,
        maxconn,
        connection_factory=_PooledConnection,
        host=db_host,
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME", "smartroad_ap"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
        connect_timeout=connect_timeout,
        keepalives=1,
        keepalives_idle=int(os.getenv("DB_KEEPALIVES_IDLE", "30")),
        keepalives_interval=int(os.getenv("DB_KEEPALIVES_INTERVAL", "10")),
        keepalives_count=int(os.getenv("DB_KEEPALIVES_COUNT", "5")),
        options=(
            f"-c statement_timeout={int(os.getenv('DB_STATEMENT_TIMEOUT_MS', '15000'))} "
            f"-c idle_in_transaction_session_timeout="
            f"{int(os.getenv('DB_IDLE_IN_TX_TIMEOUT_MS', '60000'))}"
        ),
    )


def _pool():
    global _POOL, _POOL_HOST
    db_host = resolve_db_host()
    if _POOL is not None and _POOL_HOST is not None and _POOL_HOST != db_host:
        reset_pool(reason="host-changed", force=True)
    if _POOL is not None:
        return _POOL

    with _POOL_OPEN_LOCK:
        if _POOL is not None:
            return _POOL
        budget = float(os.getenv("DB_POOL_OPEN_TIMEOUT_S", "30"))
        fut = _PING_EXEC.submit(_open_pool, db_host)
        try:
            pool = fut.result(timeout=budget)
        except FuturesTimeout as e:
            print(f"[db] sticky pool open timed out after {budget:.0f}s", flush=True)
            raise TimeoutError(
                f"Opening DB pool timed out after {budget:.0f}s (host={db_host})"
            ) from e
        with _POOL_LOCK:
            _POOL = pool
            _POOL_HOST = db_host
        print("[db] sticky pool ready", flush=True)
        return _POOL


def warm_pool() -> bool:
    """Open the sticky pool at process start so first login isn't cold."""
    if not is_db_configured():
        return False
    try:
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
                try:
                    cur.execute("SHOW max_connections")
                    row = cur.fetchone()
                    pg_max = int(row[0]) if row else 0
                    _minc, pool_max = _pool_bounds()
                    if pg_max > 0 and pool_max > pg_max - 10:
                        print(
                            f"[db] WARNING: pool max={pool_max} vs Postgres "
                            f"max_connections={pg_max} — raise max_connections or "
                            f"lower DB_POOL_HARD_CAP / DB_POOL_MAX "
                            f"(leave headroom for other services)",
                            flush=True,
                        )
                    elif pg_max > 0:
                        print(
                            f"[db] Postgres max_connections={pg_max}; "
                            f"this process pool max={pool_max}",
                            flush=True,
                        )
                except Exception:
                    pass
        finally:
            conn.close()
        print("[db] warm_pool ok", flush=True)
        return True
    except Exception as e:
        print(f"[db] warm_pool deferred: {e}", flush=True)
        return False


def reset_pool(reason: str = "", force: bool = False) -> None:
    """Almost never tear down the sticky pool under multi-user navigation.

    Prefer discarding one bad connection. Force only on host change or
    many consecutive total failures.
    """
    global _POOL, _POOL_HOST, _POOL_FAILS, _POOL_LAST_RESET
    min_gap = float(os.getenv("DB_POOL_RESET_MIN_GAP_S", "60"))
    now = time.monotonic()
    if not force and (now - _POOL_LAST_RESET) < min_gap:
        print(
            f"[db] reset_pool({reason}) skipped — sticky pool "
            f"(last reset {now - _POOL_LAST_RESET:.0f}s ago)",
            flush=True,
        )
        return

    with _POOL_LOCK:
        old = _POOL
        _POOL = None
        _POOL_HOST = None
        _POOL_FAILS = 0
        _POOL_LAST_RESET = now
    print(f"[db] reset_pool({reason or 'manual'})", flush=True)
    if old is None:
        return

    def _closeall():
        try:
            old.closeall()
        except Exception:
            pass

    try:
        _CLOSE_EXEC.submit(_closeall)
    except Exception:
        pass


def _ping_conn(conn, timeout_s: float) -> None:
    """Hard timeout around SELECT 1 — statement_timeout cannot fire on dead TCP."""

    def _do():
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()

    fut = _PING_EXEC.submit(_do)
    try:
        fut.result(timeout=timeout_s)
    except FuturesTimeout as e:
        try:
            conn.cancel()
        except Exception:
            pass
        raise TimeoutError(
            f"Database ping timed out after {timeout_s:.0f}s "
            "(Postgres unreachable or zombie connection)"
        ) from e


def _getconn_timed(pool, timeout_s: float):
    """Wait for a free pooled connection — prefer queueing over failing the request."""
    fut = _PING_EXEC.submit(pool.getconn)
    try:
        return fut.result(timeout=timeout_s)
    except FuturesTimeout as e:
        raise TimeoutError(
            f"Database pool checkout timed out after {timeout_s:.0f}s "
            "(all connections busy — raise DB_POOL_MAX / DB_POOL_HARD_CAP, "
            "or lower WAITRESS_THREADS)"
        ) from e


def _get_conn():
    """Checkout from the sticky pool. Queue generously; never reset the pool here."""
    global _POOL_FAILS
    if not _HAS_PSYCOPG2:
        raise RuntimeError("psycopg2 not installed. Run: pip install psycopg2-binary")
    pool = _pool()
    last_err = None
    # Long waits + retries: under load we queue for a free conn instead of 502/outage.
    attempts = max(1, int(os.getenv("DB_POOL_GET_RETRIES", "10")))
    delay = float(os.getenv("DB_POOL_GET_RETRY_S", "0.2"))
    ping_s = float(os.getenv("DB_PING_TIMEOUT_S", "2.0"))
    get_s = float(os.getenv("DB_POOL_GET_TIMEOUT_S", "45"))
    for _i in range(attempts):
        conn = None
        try:
            conn = _getconn_timed(pool, get_s)
        except (psycopg2.pool.PoolError, TimeoutError) as e:
            last_err = e
            time.sleep(delay * (1.0 + 0.15 * _i))
            continue
        if conn is None:
            continue
        if conn.closed:
            _discard_conn(pool, conn)
            continue
        try:
            _ping_conn(conn, ping_s)
            conn._pool_ref = pool
            _POOL_FAILS = 0
            return conn
        except Exception as e:
            last_err = e
            _discard_conn(pool, conn)
            time.sleep(delay)

    _POOL_FAILS += 1
    # Do not tear down the sticky pool — that causes open-timeout stampedes.
    raise last_err or RuntimeError("Database connection pool exhausted or unreachable")


def ping_db(timeout_s: float | None = None) -> None:
    """Cheap SELECT 1 for deep health — does not tear down the sticky pool."""
    budget = float(timeout_s if timeout_s is not None else os.getenv("DB_HEALTH_TIMEOUT_S", "3"))
    fut = _PING_EXEC.submit(_get_conn)
    try:
        conn = fut.result(timeout=budget)
    except FuturesTimeout as e:
        raise TimeoutError(f"DB health checkout timed out after {budget:.0f}s") from e
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    finally:
        try:
            conn.close()
        except Exception:
            pass


