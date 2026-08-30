"""
SmartRoad production server settings.

Default engine for the AceCloud field host is **waitress** (same threading model
that worked well with ``python web_app.py``), not gunicorn gthread.

Gunicorn gthread uses a fixed thread pool: stalled/slow cellular uploads pin
those slots and the API looks dead until restart. Waitress + Werkzeug threaded
accept slow roadside uploads without that starvation failure mode.

Optional reference when SMARTROAD_SERVER=waitress|gunicorn (default AceCloud path
is Werkzeug via scripts/run_smartroad.sh supervise / systemd).
"""
import os

# --- Waitress (default) ---
WAITRESS_HOST = os.getenv("WAITRESS_HOST", "0.0.0.0")
WAITRESS_PORT = int(os.getenv("FLASK_PORT", os.getenv("WAITRESS_PORT", "5005")))
WAITRESS_THREADS = int(os.getenv("WAITRESS_THREADS", "32"))
# Seconds a connection may sit idle between read/write activity. Must be high
# enough for a 1‑min video chunk on slow cellular (not office Wi‑Fi).
WAITRESS_CHANNEL_TIMEOUT = int(os.getenv("WAITRESS_CHANNEL_TIMEOUT", "900"))
WAITRESS_CONNECTION_LIMIT = int(os.getenv("WAITRESS_CONNECTION_LIMIT", "200"))
WAITRESS_CLEANUP_INTERVAL = int(os.getenv("WAITRESS_CLEANUP_INTERVAL", "30"))
WAITRESS_SEND_BYTES = int(os.getenv("WAITRESS_SEND_BYTES", "18000"))

# --- Gunicorn (optional; set SMARTROAD_SERVER=gunicorn) ---
bind = os.getenv("GUNICORN_BIND", f"0.0.0.0:{WAITRESS_PORT}")
workers = int(os.getenv("GUNICORN_WORKERS", "3"))
threads = int(os.getenv("GUNICORN_THREADS", "4"))
worker_class = "gthread"
timeout = int(os.getenv("GUNICORN_TIMEOUT", "300"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "60"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "0"))
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "800"))
max_requests_jitter = int(os.getenv("GUNICORN_MAX_REQUESTS_JITTER", "80"))
preload_app = os.getenv("GUNICORN_PRELOAD", "false").lower() in ("1", "true", "yes")
worker_tmp_dir = os.getenv("GUNICORN_WORKER_TMP", "/dev/shm")
accesslog = os.getenv("GUNICORN_ACCESS_LOG", "-")
errorlog = os.getenv("GUNICORN_ERROR_LOG", "-")
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")
capture_output = True
limit_request_line = 8190
limit_request_fields = 100
