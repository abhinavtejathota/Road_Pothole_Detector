"""Bearer token auth for mobile (HMAC signed, stdlib only — no PyJWT dep).

Web keeps Flask-Login cookie sessions. Mobile sends:
  Authorization: Bearer <token>
  X-Client: mobile

Token payload: user_id, iat, exp, ver (must match users.token_version).
Logout increments token_version so old JWTs die immediately.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from flask import request


def _secret() -> bytes:
    raw = (
        os.getenv("JWT_SECRET")
        or os.getenv("FLASK_SECRET_KEY")
        or ""
    ).strip()
    if not raw or raw == "smartroad-phase3-secret-change-me":
        env = (os.getenv("SMARTROAD_ENV") or "").strip().lower()
        if env in ("production", "prod"):
            raise RuntimeError("JWT_SECRET or FLASK_SECRET_KEY required in production")
        raw = "smartroad-phase3-secret-change-me"
    return raw.encode("utf-8")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def token_ttl_s() -> int:
    return max(300, int(os.getenv("JWT_TTL_S", str(7 * 24 * 3600))))


def issue_access_token(user_id: int, token_version: int = 0) -> str:
    now = int(time.time())
    payload = {
        "uid": int(user_id),
        "ver": int(token_version or 0),
        "iat": now,
        "exp": now + token_ttl_s(),
    }
    body = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64url_encode(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_access_token(token: str) -> dict[str, Any] | None:
    try:
        body, sig = token.strip().split(".", 1)
    except ValueError:
        return None
    expect = _b64url_encode(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(expect, sig):
        return None
    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except Exception:
        return None
    if int(payload.get("exp") or 0) < int(time.time()):
        return None
    if not payload.get("uid"):
        return None
    return payload


def bearer_from_request() -> str | None:
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        tok = auth[7:].strip()
        return tok or None
    # Optional fallback header for stacks that mangle Authorization
    tok = (request.headers.get("X-Access-Token") or "").strip()
    return tok or None


def wants_bearer_auth() -> bool:
    """Mobile clients prefer JWT; presence of Bearer also counts."""
    if bearer_from_request():
        return True
    client = (request.headers.get("X-Client") or "").strip().lower()
    return client in ("mobile", "expo", "flutter", "android", "ios")
