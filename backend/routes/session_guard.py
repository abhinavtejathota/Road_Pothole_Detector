"""Session epoch + idle timeout for **web cookie** sessions only.

Mobile uses Bearer JWT (routes.token_auth) — idle cookies do not apply.
Web default idle: SESSION_IDLE_TIMEOUT_S=3600 (1 hour).
"""
from __future__ import annotations

import os
import time

from flask import jsonify, request, session
from flask_login import current_user, logout_user

_ACTIVITY_KEY = "_activity"
_EPOCH_KEY = "_epoch"


def session_epoch() -> str:
    return (os.getenv("SESSION_EPOCH") or "20260715b").strip() or "20260715b"


def is_mobile_client() -> bool:
    client = (request.headers.get("X-Client") or "").strip().lower()
    return client in ("mobile", "expo", "flutter", "android", "ios")


def idle_limit_s() -> float:
    # Web browser idle (1 hour default). Mobile JWT has its own TTL.
    return float(os.getenv("SESSION_IDLE_TIMEOUT_S", "3600"))


def stamp_session(*, authenticated: bool = True) -> None:
    session.permanent = True
    session[_EPOCH_KEY] = session_epoch()
    if authenticated:
        session[_ACTIVITY_KEY] = time.time()


def clear_session_cookie() -> None:
    try:
        logout_user()
    except Exception:
        pass
    session.clear()


def expire_auth_cookies(response, app_config: dict | None = None) -> None:
    """Force-clear session cookies on the wire (fixes logout not resetting jars)."""
    cfg = app_config or {}
    names = [
        cfg.get("SESSION_COOKIE_NAME") or "sr_session",
        cfg.get("REMEMBER_COOKIE_NAME") or "sr_remember",
        "session",
        "remember_token",
    ]
    for name in names:
        # Path=/ is required; Max-Age=0 + Expires past kills browsers that ignore one or the other.
        response.set_cookie(
            name,
            "",
            max_age=0,
            expires=0,
            path="/",
            httponly=True,
            samesite="Lax",
            secure=False,
        )
        # Some older builds used Secure cookies on HTTP reverse proxies — clear those too.
        response.set_cookie(
            name,
            "",
            max_age=0,
            expires=0,
            path="/",
            httponly=True,
            samesite="Lax",
            secure=True,
        )


def _skip_path(path: str) -> bool:
    p = (path or "").split("?", 1)[0]
    if not p.startswith("/api/"):
        return True
    if p == "/api/health" or p.startswith("/api/health/"):
        return True
    if p == "/api/auth/login":
        return True
    if p.startswith("/api/reporter/"):
        return True
    if request.method == "OPTIONS":
        return True
    return False


def enforce_session_idle():
    """before_request: cookie idle / epoch only — ignore Bearer JWT requests."""
    if _skip_path(request.path or ""):
        return None

    try:
        from routes.token_auth import bearer_from_request

        if bearer_from_request():
            return None
    except Exception:
        pass

    epoch = session_epoch()
    stored_epoch = session.get(_EPOCH_KEY)
    if stored_epoch is not None and stored_epoch != epoch:
        clear_session_cookie()
        if (request.path or "").startswith("/api/"):
            return jsonify({
                "error": "Unauthorized",
                "message": "Session invalidated — please log in again.",
            }), 401
        return None

    if not current_user.is_authenticated:
        return None

    # Bearer already handled above — do not trust X-Client alone to skip idle.
    if stored_epoch is None:
        clear_session_cookie()
        return jsonify({
            "error": "Unauthorized",
            "message": "Session expired — please log in again.",
        }), 401

    now = time.time()
    last = session.get(_ACTIVITY_KEY)
    limit = idle_limit_s()
    if last is None or (now - float(last)) > limit:
        clear_session_cookie()
        return jsonify({
            "error": "Unauthorized",
            "message": "Signed out after inactivity — please log in again.",
        }), 401

    session[_ACTIVITY_KEY] = now
    session[_EPOCH_KEY] = epoch
    return None
