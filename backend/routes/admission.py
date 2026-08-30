"""Admit light traffic even when uploads/finalize are saturating the process.

After field uploads, ffmpeg/S3 work used to make /detection and login feel dead.
Rules:
  - SPA + static → always light
  - /api/health + /api/auth/* → always light
  - GET /api/* → light (UI reads: Detection page, dashboards)
  - Phone chunk body paths → not gated here (CHUNK_UPLOAD_CONCURRENCY only)
  - Other POST/PUT/PATCH/DELETE → heavy (fail fast 503 when full)
"""
from __future__ import annotations

import os
import threading

_HEAVY_SLOTS = threading.BoundedSemaphore(
    max(2, int(os.getenv("SMARTROAD_HEAVY_CONCURRENCY", "6")))
)

_HEAVY_INFLIGHT = 0
_HEAVY_LOCK = threading.Lock()

_LIGHT_ALWAYS = (
    "/api/health",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/me",
)

# Phone→Flask body reads: capped by upload_guard, must not fill heavy slots
# (otherwise /detection GETs wait behind multi-minute LTE uploads).
_UPLOAD_BODY_SUFFIXES = (
    "/upload/session/chunk",
    "/upload/session/chunk-bin",
    "/upload/multipart/part",
)


def is_upload_body_path(path: str) -> bool:
    p = (path or "").split("?", 1)[0]
    return any(p.endswith(suf) for suf in _UPLOAD_BODY_SUFFIXES) or p.rstrip("/").endswith(
        "/api/upload"
    )


def is_light_path(path: str, method: str = "GET") -> bool:
    p = (path or "").split("?", 1)[0]
    m = (method or "GET").upper()
    if m == "OPTIONS":
        return True
    if p in ("/",) or not p.startswith("/api/"):
        return True
    for pref in _LIGHT_ALWAYS:
        if p == pref or p.startswith(pref + "/"):
            return True
    if p.startswith("/api/auth/"):
        return True
    # Keep portal pages responsive while finalize runs in a child process.
    if m in ("GET", "HEAD"):
        return True
    return False


def try_enter_heavy(*, wait_s: float = 0.05) -> bool:
    global _HEAVY_INFLIGHT
    ok = _HEAVY_SLOTS.acquire(blocking=True, timeout=wait_s)
    if ok:
        with _HEAVY_LOCK:
            _HEAVY_INFLIGHT += 1
    return ok


def leave_heavy() -> None:
    global _HEAVY_INFLIGHT
    try:
        _HEAVY_SLOTS.release()
    except ValueError:
        return
    with _HEAVY_LOCK:
        _HEAVY_INFLIGHT = max(0, _HEAVY_INFLIGHT - 1)


def heavy_inflight() -> int:
    with _HEAVY_LOCK:
        return _HEAVY_INFLIGHT


def overload_response():
    from flask import jsonify

    resp = jsonify({
        "ok": False,
        "error": "server_busy",
        "status": {
            "kind": "warn",
            "message": (
                "Server is busy finishing video uploads — "
                "portal reads stay available; retry this action in a few seconds."
            ),
        },
        "retry_after": 5,
        "heavy_inflight": heavy_inflight(),
    })
    resp.status_code = 503
    resp.headers["Retry-After"] = "5"
    return resp
