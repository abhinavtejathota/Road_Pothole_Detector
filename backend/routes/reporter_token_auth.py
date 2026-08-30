"""Bearer JWT for citizen reporters (separate from staff users.token_version)."""
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
        os.getenv("REPORTER_JWT_SECRET")
        or os.getenv("JWT_SECRET")
        or os.getenv("FLASK_SECRET_KEY")
        or ""
    ).strip()
    if not raw or raw == "smartroad-reporter-secret-change-me":
        env = (os.getenv("SMARTROAD_ENV") or "").strip().lower()
        if env in ("production", "prod"):
            raise RuntimeError("REPORTER_JWT_SECRET or FLASK_SECRET_KEY required in production")
        raw = "smartroad-reporter-secret-change-me"
    return raw.encode("utf-8")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def reporter_token_ttl_s() -> int:
    # Default 24h (was 30d) — shorten blast radius of stolen reporter JWTs.
    return max(3600, int(os.getenv("REPORTER_JWT_TTL_S", str(24 * 3600))))


def issue_reporter_token(reporter_id: int, mobile: str) -> str:
    now = int(time.time())
    payload = {
        "kind": "reporter",
        "rid": int(reporter_id),
        "mob": str(mobile)[-4:],  # last 4 digits only in token (not full PII)
        "iat": now,
        "exp": now + reporter_token_ttl_s(),
    }
    body = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64url_encode(sig)}"


def decode_reporter_token(token: str) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None
    body, _, sig = token.partition(".")
    try:
        expected = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64url_encode(expected), sig):
            return None
        payload = json.loads(_b64url_decode(body))
    except Exception:
        return None
    if payload.get("kind") != "reporter":
        return None
    if int(payload.get("exp") or 0) < int(time.time()):
        return None
    if not payload.get("rid"):
        return None
    return payload


def bearer_reporter_token() -> str | None:
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


def current_reporter_id() -> int | None:
    tok = bearer_reporter_token()
    if not tok:
        return None
    payload = decode_reporter_token(tok)
    if not payload:
        return None
    try:
        return int(payload["rid"])
    except (TypeError, ValueError, KeyError):
        return None
