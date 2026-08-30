"""Shared Flask app factory for portal / upload / detect processes.

SMARTROAD_SERVICE=portal|upload|detect|all
  portal — SPA + all APIs (bare `python web_app.py` monolith still works)
  upload — only /api/upload/* + auth/health (nginx-backed)
  detect — only /api/detection/* + /api/model-bench/* + auth/health
  all    — same as portal
"""
from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

import smartroad_path

_ROOT = smartroad_path.ROOT
load_dotenv(_ROOT / ".env", override=True)

from flask import Flask, jsonify, request, send_from_directory
from flask_login import LoginManager

import db_utils

FRONTEND_DIST = _ROOT / "frontend" / "dist"

_INSECURE_SECRET = "smartroad-phase3-secret-change-me"


def _is_production_env() -> bool:
    return (os.getenv("SMARTROAD_ENV") or "").strip().lower() in ("production", "prod")


def _resolve_flask_secret() -> str:
    raw = (os.getenv("FLASK_SECRET_KEY") or "").strip()
    if not raw or raw == _INSECURE_SECRET:
        if _is_production_env():
            raise RuntimeError(
                "FLASK_SECRET_KEY must be set to a strong unique value in production "
                "(SMARTROAD_ENV=production)."
            )
        print(
            "[security] WARNING: FLASK_SECRET_KEY missing or insecure default — "
            "set a strong secret before any shared/deployed use.",
            flush=True,
        )
        return raw or _INSECURE_SECRET
    if len(raw) < 16:
        print(
            "[security] WARNING: FLASK_SECRET_KEY is short; use ≥32 random bytes.",
            flush=True,
        )
    return raw


def _cookie_secure_default() -> bool:
    env = (os.getenv("SESSION_COOKIE_SECURE") or "").strip().lower()
    if env in ("1", "true", "yes"):
        return True
    if env in ("0", "false", "no"):
        return False
    return _is_production_env()


def _cors_allowed_origins() -> set[str]:
    raw = (os.getenv("CORS_ALLOWED_ORIGINS") or "").strip()
    if raw:
        return {o.strip().rstrip("/") for o in raw.split(",") if o.strip()}
    # Local/dev defaults — production must set CORS_ALLOWED_ORIGINS explicitly.
    return {
        "http://127.0.0.1:5000",
        "http://localhost:5000",
        "http://127.0.0.1:5005",
        "http://localhost:5005",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    }


def _serve_index():
    index = FRONTEND_DIST / "index.html"
    if not index.is_file():
        return (
            "<h2>Frontend not built</h2>"
            "<p>Run: <code>cd frontend && npm install && npm run build</code></p>",
            503,
            {"Content-Type": "text/html"},
        )
    resp = send_from_directory(FRONTEND_DIST, "index.html")
    # Never cache the SPA shell — hashed /assets/* can be immutable.
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


def _path_allowed_for_service(service: str, path: str) -> bool:
    p = (path or "").split("?", 1)[0]
    if service in ("portal", "all", ""):
        return True
    if p == "/api/health" or p.startswith("/api/health/"):
        return True
    if p.startswith("/api/auth/"):
        return True
    if service == "upload":
        return p.startswith("/api/upload")
    if service == "detect":
        return p.startswith("/api/detection") or p.startswith("/api/model-bench")
    return True


def create_app(service: str | None = None):
    service = (service or os.getenv("SMARTROAD_SERVICE") or "portal").strip().lower()
    if service not in ("portal", "upload", "detect", "all"):
        service = "portal"

    app = Flask(__name__)
    app.config["SMARTROAD_SERVICE"] = service
    app.secret_key = _resolve_flask_secret()
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
        days=max(1, int(os.getenv("SESSION_DAYS", "7")))
    )
    app.config["REMEMBER_COOKIE_DURATION"] = timedelta(
        days=max(1, int(os.getenv("SESSION_DAYS", "7")))
    )
    app.config["SESSION_COOKIE_NAME"] = os.getenv("SESSION_COOKIE_NAME", "sr_session")
    app.config["REMEMBER_COOKIE_NAME"] = os.getenv("REMEMBER_COOKIE_NAME", "sr_remember")
    app.config["SESSION_REFRESH_EACH_REQUEST"] = True
    # Harden cookies (Lax mitigates CSRF for most cross-site POSTs; Secure in prod).
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["REMEMBER_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
    app.config["REMEMBER_COOKIE_SAMESITE"] = os.getenv("REMEMBER_COOKIE_SAMESITE", "Lax")
    _secure = _cookie_secure_default()
    app.config["SESSION_COOKIE_SECURE"] = _secure
    app.config["REMEMBER_COOKIE_SECURE"] = _secure
    max_mb = int(os.getenv("DETECTION_MAX_UPLOAD_MB", "4096"))
    app.config["MAX_CONTENT_LENGTH"] = max_mb * 1024 * 1024

    @app.before_request
    def _prepare_auth_cookies():
        cookie_name = (app.config.get("SESSION_COOKIE_NAME") or "sr_session").lower()
        remember_name = (app.config.get("REMEMBER_COOKIE_NAME") or "sr_remember").lower()
        changed = False

        raw = request.environ.get("HTTP_COOKIE") or ""
        if raw and ("session=" in raw.lower() or "remember_token=" in raw.lower()):
            kept = []
            for part in raw.split(";"):
                name = part.strip().split("=", 1)[0].strip().lower()
                if name in ("session", "remember_token"):
                    changed = True
                    continue
                if part.strip():
                    kept.append(part.strip())
            request.environ["HTTP_COOKIE"] = "; ".join(kept)
            raw = request.environ["HTTP_COOKIE"]

        # Mobile JWT era: only inject legacy X-Session-Cookie if no Bearer present.
        from routes.token_auth import bearer_from_request

        if (request.path or "").startswith("/api/") and not bearer_from_request():
            x = (request.headers.get("X-Session-Cookie") or "").strip()
            if x:
                if "=" not in x:
                    x = f"{cookie_name}={x}"
                else:
                    head, _, rest = x.partition("=")
                    if head.strip().lower() in ("session", cookie_name, "sr_session"):
                        x = f"{cookie_name}={rest}"
                    elif head.strip().lower() in ("remember_token", remember_name, "sr_remember"):
                        x = f"{remember_name}={rest}"
                low = (raw or "").lower()
                if f"{cookie_name}=" not in low and f"{remember_name}=" not in low:
                    request.environ["HTTP_COOKIE"] = f"{raw}; {x}" if raw else x
                    changed = True

        if changed:
            request.__dict__.pop("cookies", None)
        return None

    login_manager = LoginManager()
    login_manager.login_view = None
    login_manager.login_message_category = "warning"
    login_manager.init_app(app)

    from routes.user_model import User

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return User.get(int(user_id))
        except Exception:
            return None

    @login_manager.request_loader
    def load_user_from_request(req):
        """Mobile Bearer JWT (and optional X-Access-Token)."""
        try:
            from routes.token_auth import bearer_from_request, verify_access_token

            tok = bearer_from_request()
            if not tok:
                return None
            payload = verify_access_token(tok)
            if not payload:
                return None
            uid = int(payload["uid"])
            ver = int(payload.get("ver") or 0)
            if db_utils.get_user_token_version(uid) != ver:
                return None
            return User.get(uid)
        except Exception:
            return None

    @login_manager.unauthorized_handler
    def unauthorized():
        if request.path.startswith("/api/"):
            return jsonify({
                "error": "Unauthorized",
                "message": "Session expired or missing — log in again.",
            }), 401
        return _serve_index()

    @app.errorhandler(Exception)
    def _unhandled(exc):
        """Never turn a session/decode blip into a blank Werkzeug 500 HTML for SPA."""
        from werkzeug.exceptions import HTTPException

        if isinstance(exc, HTTPException):
            return exc
        # BadSignature / tampered cookie after FLASK_SECRET_KEY rotate
        name = type(exc).__name__
        msg = str(exc)
        if name in ("BadSignature", "BadData", "SignatureExpired") or "Signature" in name:
            try:
                from routes.session_guard import clear_session_cookie, expire_auth_cookies
                from flask import make_response

                clear_session_cookie()
                if request.path.startswith("/api/"):
                    resp = make_response(jsonify({
                        "error": "Unauthorized",
                        "message": "Session invalidated — please log in again.",
                    }), 401)
                else:
                    resp = make_response(_serve_index())
                expire_auth_cookies(resp, app.config)
                return resp
            except Exception:
                pass
        # DB pool / statement timeouts — 503 so SPA keeps retrying until real data
        if isinstance(exc, TimeoutError) or name == "TimeoutError" or "pool" in msg.lower() or "timed out" in msg.lower():
            if request.path.startswith("/api/"):
                return jsonify({
                    "error": "Busy",
                    "message": "Database is busy — retry in a moment.",
                    "detail": msg[:200],
                    "retry_after": 2,
                }), 503
        app.logger.exception("Unhandled error on %s", request.path)
        if request.path.startswith("/api/"):
            return jsonify({"error": "Internal error"}), 500
        # Prefer SPA shell over Werkzeug HTML so the user can still reach /login
        try:
            return _serve_index(), 200
        except Exception:
            return ("Internal Server Error", 500)

    from routes.api import api_bp
    from routes.reporter_api import reporter_bp

    app.register_blueprint(api_bp)
    app.register_blueprint(reporter_bp)

    @app.before_request
    def _service_gate():
        path = request.path or ""
        if request.method == "OPTIONS":
            return None
        if not path.startswith("/api/"):
            # SPA only on portal
            if service not in ("portal", "all"):
                return jsonify({"error": "Not found", "service": service}), 404
            return None
        if not _path_allowed_for_service(service, path):
            return jsonify({
                "error": "Wrong service",
                "message": f"This path is not handled by SMARTROAD_SERVICE={service}",
                "service": service,
            }), 404
        return None

    @app.before_request
    def _session_idle_guard():
        from routes.session_guard import enforce_session_idle

        return enforce_session_idle()

    @app.before_request
    def _api_cors_preflight():
        if request.method == "OPTIONS" and request.path.startswith("/api/"):
            return ("", 204)

    @app.before_request
    def _admission_control():
        from flask import g

        from routes.admission import (
            is_light_path,
            is_upload_body_path,
            overload_response,
            try_enter_heavy,
        )

        g._smartroad_heavy = False
        path = request.path or ""
        if request.method == "OPTIONS" or is_light_path(path, request.method):
            return None
        if is_upload_body_path(path):
            return None
        if not try_enter_heavy():
            return overload_response()
        g._smartroad_heavy = True
        return None

    @app.teardown_request
    def _admission_leave(_exc=None):
        from flask import g

        if getattr(g, "_smartroad_heavy", False):
            try:
                from routes.admission import leave_heavy

                leave_heavy()
            except Exception:
                pass
            g._smartroad_heavy = False

    @app.before_request
    def _arm_chunk_upload_timeouts():
        if service not in ("portal", "upload", "all"):
            return None
        path = request.path or ""
        if request.method == "POST" and (
            path.endswith("/upload/session/chunk")
            or path.endswith("/upload/session/chunk-bin")
            or path.rstrip("/").endswith("/api/upload")
            or path.endswith("/upload/multipart/part")
            or "/upload/session/finalize" in path
        ):
            try:
                from routes.upload_guard import arm_chunk_body_timeouts

                arm_chunk_body_timeouts(request.environ)
            except Exception:
                pass
        return None

    @app.after_request
    def _api_cors_headers(response):
        if not request.path.startswith("/api/"):
            return response
        for hop in ("Connection", "Keep-Alive", "Transfer-Encoding", "Upgrade"):
            try:
                del response.headers[hop]
            except KeyError:
                pass
        origin = (request.headers.get("Origin") or "").strip().rstrip("/")
        allowed = _cors_allowed_origins()
        if origin and origin in allowed:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Vary"] = "Origin"
        elif not origin:
            # Non-browser clients (mobile) — no ACAO needed for same-host APIs.
            pass
        # Never reflect arbitrary Origin + credentials (session theft).
        response.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, Authorization, Cookie, X-Client, X-Session-Cookie, "
            "X-Access-Token, X-Capture-Session-Id, X-Chunk-Index, X-Lat, X-Lon, "
            "X-Accuracy, Content-Length"
        )
        response.headers["Access-Control-Allow-Methods"] = (
            "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        )
        # Do not expose session cookies to JavaScript (removed X-Session-Cookie echo).
        response.headers["Access-Control-Expose-Headers"] = "X-Access-Token"
        return response

    if service in ("portal", "all"):

        @app.route("/assets/<path:filename>")
        def spa_assets(filename):
            resp = send_from_directory(FRONTEND_DIST / "assets", filename)
            # Vite content-hashes filenames — long cache is safe.
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return resp

        @app.route("/", defaults={"path": ""})
        @app.route("/<path:path>")
        def spa(path):
            if path.startswith("api/"):
                return jsonify({"error": "Not found"}), 404
            if path:
                target = (FRONTEND_DIST / path).resolve()
                dist_root = FRONTEND_DIST.resolve()
                if str(target).startswith(str(dist_root)) and target.is_file():
                    resp = send_from_directory(FRONTEND_DIST, path)
                    # HTML / service worker shells must not stick as stale.
                    if str(path).endswith((".html", ".json")):
                        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
                    return resp
            return _serve_index()

    if db_utils.is_db_configured():
        try:
            lock_path = _ROOT / "data" / ".schema_init.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with open(lock_path, "a+", encoding="utf-8") as lf:
                try:
                    import fcntl

                    fcntl.flock(lf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    exclusive = True
                except BlockingIOError:
                    exclusive = False
                except Exception:
                    exclusive = True
                if exclusive:
                    try:
                        db_utils.init_db()
                    finally:
                        try:
                            import fcntl

                            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
                        except Exception:
                            pass
        except Exception as e:
            app.logger.warning(f"DB init warning: {e}")
        # Keep DB pool open for the life of this process (multi-user sidebar safe).
        try:
            db_utils.warm_pool()
        except Exception as e:
            app.logger.warning(f"DB warm_pool warning: {e}")

    print(f"[smartroad] service={service}", flush=True)
    return app


def run_service(service: str, default_port: int) -> None:
    application = create_app(service)
    port = int(os.getenv("FLASK_PORT", str(default_port)))
    debug = os.getenv("FLASK_DEBUG", "false").lower() in ("1", "true", "yes")
    host = os.getenv("WAITRESS_HOST", "0.0.0.0")
    engine = (os.getenv("SMARTROAD_SERVER") or "waitress").strip().lower()
    threads = max(8, int(os.getenv("WAITRESS_THREADS", "32")))

    if debug and host not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError(
            "Refusing FLASK_DEBUG with non-loopback WAITRESS_HOST "
            f"({host!r}) — Werkzeug debugger is remote RCE. "
            "Use WAITRESS_HOST=127.0.0.1 or disable FLASK_DEBUG."
        )
    if debug and _is_production_env():
        raise RuntimeError("FLASK_DEBUG is forbidden when SMARTROAD_ENV=production.")

    if os.getenv("SMARTROAD_QUIET_LOGS", "1").lower() in ("1", "true", "yes"):
        import logging

        logging.getLogger("werkzeug").setLevel(logging.WARNING)

    try:
        from ffmpeg_accel import accel_label
        from model_loader import resolve_yolo_device

        print(
            f"Accel: YOLO_DEVICE={resolve_yolo_device()} "
            f"ffmpeg={accel_label()} cpus={os.cpu_count() or '?'}",
            flush=True,
        )
    except Exception as e:
        print(f"Accel probe skipped: {e}", flush=True)

    if debug or engine in ("werkzeug", "flask", "dev"):
        print(f"Werkzeug threaded on {host}:{port} service={service}", flush=True)
        application.run(host=host, port=port, debug=debug, threaded=True)
        return

    try:
        from waitress import serve
    except ImportError:
        print(
            "waitress not installed — falling back to Werkzeug threaded. "
            "pip install waitress",
            flush=True,
        )
        application.run(host=host, port=port, debug=False, threaded=True)
        return

    print(
        f"Waitress threads={threads} on {host}:{port} service={service}",
        flush=True,
    )
    try:
        import db_utils
        if db_utils.is_db_configured():
            pmin, pmax = db_utils._pool_bounds()
            print(
                f"[db] pool bounds for this process: min={pmin} max={pmax} "
                f"(set DB_POOL_MAX / DB_POOL_HARD_CAP to raise)",
                flush=True,
            )
            if pmax < threads:
                print(
                    f"[db] WARNING: DB_POOL_MAX={pmax} < Waitress threads={threads} "
                    f"— requests will queue on checkout; raise DB_POOL_MAX",
                    flush=True,
                )
    except Exception:
        pass
    serve(
        application,
        host=host,
        port=port,
        threads=threads,
        channel_timeout=int(os.getenv("WAITRESS_CHANNEL_TIMEOUT", "180")),
        cleanup_interval=30,
        asyncore_use_poll=True,
    )
