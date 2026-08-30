"""Citizen reporter OTP auth — challenges table (hash + plain_otp for testing).

Testing:
  - Active OTP stored in `reporter_otp_challenges` (otp_hash + plain_otp).
  - Mirrored to `data/reporter_otps.json` for quick inspection.
  - Set REPORTER_OTP_RETURN=1 to echo OTP in API responses.
  - Drop plain_otp column before production SMS.

Flow:
  - First request: create OTP (hash + plain_otp), send/log SMS.
  - Return visits: reuse active OTP until expiry (not consumed on verify).
  - Forgot / resend: new OTP, old challenges marked consumed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import db_utils
from routes.reporter_service import normalize_mobile
from routes.reporter_token_auth import issue_reporter_token, reporter_token_ttl_s

_ROOT = Path(__file__).resolve().parents[2]
_OTP_JSON = Path(os.getenv("REPORTER_OTP_JSON", str(_ROOT / "data" / "reporter_otps.json")))


def _otp_pepper() -> str:
    return os.getenv("REPORTER_OTP_PEPPER") or os.getenv("FLASK_SECRET_KEY") or "reporter-otp"


def _hash_otp(mobile: str, otp: str) -> str:
    blob = f"{_otp_pepper()}:{mobile}:{otp}".encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _otp_length() -> int:
    return max(4, min(8, int(os.getenv("REPORTER_OTP_LENGTH", "6"))))


def _otp_ttl_s() -> int:
    # Default 5 minutes (was 24h) — reduce replay window.
    return max(60, int(os.getenv("REPORTER_OTP_TTL_S", "300")))


def _otp_max_attempts() -> int:
    return max(3, int(os.getenv("REPORTER_OTP_MAX_ATTEMPTS", "5")))


def _generate_otp() -> str:
    n = _otp_length()
    return "".join(str(secrets.randbelow(10)) for _ in range(n))


def _mask_mobile(mobile: str) -> str:
    return mobile[:2] + "******" + mobile[-2:] if len(mobile) >= 4 else "****"


def _return_otp_in_response() -> bool:
    # Never echo OTP when SMARTROAD_ENV=production even if REPORTER_OTP_RETURN=1.
    if (os.getenv("SMARTROAD_ENV") or "").strip().lower() in ("production", "prod"):
        return False
    return os.getenv("REPORTER_OTP_RETURN", "").strip().lower() in ("1", "true", "yes")


def _mirror_otp_json_enabled() -> bool:
    if (os.getenv("SMARTROAD_ENV") or "").strip().lower() in ("production", "prod"):
        return False
    return os.getenv("REPORTER_OTP_JSON_MIRROR", "").strip().lower() in ("1", "true", "yes")


def _load_otp_json() -> dict[str, Any]:
    try:
        if _OTP_JSON.is_file():
            return json.loads(_OTP_JSON.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_otp_json(data: dict[str, Any]) -> None:
    _OTP_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = _OTP_JSON.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(_OTP_JSON)


def _json_set(mobile: str, otp: str) -> None:
    if not _mirror_otp_json_enabled():
        return
    data = _load_otp_json()
    data[mobile] = {
        "otp": otp,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_otp_json(data)


def _send_otp_sms(mobile: str, otp: str) -> None:
    provider = (os.getenv("REPORTER_OTP_PROVIDER") or "log").strip().lower()
    if provider in ("", "log", "dev", "console"):
        if (os.getenv("SMARTROAD_ENV") or "").strip().lower() in ("production", "prod"):
            raise RuntimeError("REPORTER_OTP_PROVIDER=log is forbidden in production.")
        # Mask in logs unless explicitly enabled
        if os.getenv("REPORTER_OTP_LOG", "").strip().lower() in ("1", "true", "yes"):
            print(f"[reporter-otp] mobile={_mask_mobile(mobile)} otp={otp} (dev)", flush=True)
        else:
            print(f"[reporter-otp] mobile={_mask_mobile(mobile)} otp=****** (dev)", flush=True)
        return
    raise RuntimeError(
        f"REPORTER_OTP_PROVIDER={provider!r} is not implemented yet. "
        "Set REPORTER_OTP_PROVIDER=log for development."
    )


def _ensure_reporter(mobile: str) -> dict:
    if not db_utils.is_db_configured():
        raise RuntimeError("Database not configured.")
    conn = db_utils._get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO reporter_users (mobile)
                VALUES (%s)
                ON CONFLICT (mobile) DO UPDATE SET mobile = EXCLUDED.mobile
                RETURNING id, mobile
                """,
                (mobile,),
            )
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    return {"id": int(row[0]), "mobile": row[1]}


def _touch_login(reporter_id: int) -> None:
    if not db_utils.is_db_configured():
        return
    conn = db_utils._get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE reporter_users SET last_login_at = NOW() WHERE id = %s",
                (reporter_id,),
            )
        conn.commit()
    finally:
        conn.close()


def _active_challenge(cur, mobile: str) -> tuple | None:
    cur.execute(
        """
        SELECT id, otp_hash, plain_otp, expires_at, attempt_count
        FROM reporter_otp_challenges
        WHERE mobile = %s
          AND consumed_at IS NULL
          AND expires_at > NOW()
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (mobile,),
    )
    return cur.fetchone()


def _consume_active_challenges(cur, mobile: str) -> None:
    cur.execute(
        """
        UPDATE reporter_otp_challenges
        SET consumed_at = NOW()
        WHERE mobile = %s AND consumed_at IS NULL
        """,
        (mobile,),
    )


def _insert_challenge(cur, mobile: str, otp: str, *, ip: str | None) -> None:
    expires = datetime.now(timezone.utc) + timedelta(seconds=_otp_ttl_s())
    # Store hash only — never persist plaintext OTP in the DB.
    cur.execute(
        """
        INSERT INTO reporter_otp_challenges
          (mobile, otp_hash, plain_otp, expires_at, ip_address)
        VALUES (%s, %s, NULL, %s, %s)
        """,
        (mobile, _hash_otp(mobile, otp), expires, ip),
    )


def _create_challenge(mobile: str, *, ip: str | None, resend: bool) -> tuple[str, bool]:
    """Return (otp, created_new). Always issues a fresh code when resend or no active challenge."""
    conn = db_utils._get_conn()
    try:
        with conn.cursor() as cur:
            active = _active_challenge(cur, mobile)
            if active and not resend:
                # Do not re-surface prior OTP (no plaintext stored). Tell client to reuse.
                return "", False

            _consume_active_challenges(cur, mobile)
            otp = _generate_otp()
            _insert_challenge(cur, mobile, otp, ip=ip)
            _json_set(mobile, otp)
        conn.commit()
        return otp, True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def request_otp(mobile_raw: str, *, ip: str | None = None, resend: bool = False) -> dict:
    """Request OTP. Reuses active challenge window unless resend=True (forgot OTP)."""
    if not db_utils.is_db_configured():
        raise RuntimeError("Database not configured.")
    mobile = normalize_mobile(mobile_raw)
    otp, created = _create_challenge(mobile, ip=ip, resend=resend)

    if created:
        _send_otp_sms(mobile, otp)
        message = "OTP sent to your mobile number."
    else:
        message = "An OTP is already active for this number. Use Forgot OTP to get a new code."

    out: dict[str, Any] = {
        "mobile": mobile,
        "mobile_masked": _mask_mobile(mobile),
        "created": created,
        "resent": bool(resend and created),
        "message": message,
        "otp_length": _otp_length(),
        "expires_in": _otp_ttl_s(),
    }
    if created and _return_otp_in_response():
        out["dev_otp"] = otp
    return out


def verify_otp(mobile_raw: str, otp_raw: str) -> dict:
    """Verify OTP against active challenge and consume it on success."""
    if not db_utils.is_db_configured():
        raise RuntimeError("Database not configured.")
    mobile = normalize_mobile(mobile_raw)
    otp = re.sub(r"\D", "", str(otp_raw or ""))
    if not otp:
        raise ValueError("OTP is required.")

    conn = db_utils._get_conn()
    try:
        with conn.cursor() as cur:
            active = _active_challenge(cur, mobile)
            if not active:
                raise ValueError("No active OTP. Request a new code.")
            cid, otp_hash, _plain_otp, _expires_at, attempts = active
            if attempts >= _otp_max_attempts():
                raise ValueError("Too many attempts. Use Forgot OTP to get a new code.")
            expected_hash = _hash_otp(mobile, otp)
            if otp_hash != expected_hash:
                cur.execute(
                    """
                    UPDATE reporter_otp_challenges
                    SET attempt_count = attempt_count + 1
                    WHERE id = %s
                    """,
                    (cid,),
                )
                conn.commit()
                raise ValueError("Invalid OTP.")
            # Consume so the code cannot be replayed.
            cur.execute(
                """
                UPDATE reporter_otp_challenges
                SET consumed_at = NOW()
                WHERE id = %s AND consumed_at IS NULL
                """,
                (cid,),
            )
        conn.commit()
    except ValueError:
        conn.rollback()
        raise
    finally:
        conn.close()

    rep = _ensure_reporter(mobile)
    _touch_login(rep["id"])
    token = issue_reporter_token(rep["id"], mobile)
    return {
        "id": rep["id"],
        "mobile": mobile,
        "mobile_masked": _mask_mobile(mobile),
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": reporter_token_ttl_s(),
    }


def lookup_mobile(mobile_raw: str) -> dict:
    """Always return the same shape — do not leak whether a citizen exists."""
    if not db_utils.is_db_configured():
        raise RuntimeError("Database not configured.")
    mobile = normalize_mobile(mobile_raw)
    return {
        "mobile": mobile,
        "mobile_masked": _mask_mobile(mobile),
        "ok": True,
        "message": "If this number is registered, continue with OTP.",
    }
