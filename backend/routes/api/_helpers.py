"""Shared API helpers."""
import os
import time
from datetime import date, datetime
from decimal import Decimal

from flask import abort
from flask_login import current_user

from routes import survey_service


def _serialize(value):
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    return value


def _dev_admin_only():
    """Platform DevAdmin only (Users / Detection / Model bench)."""
    if not current_user.is_dev_admin():
        abort(403)


def _field_ops():
    """Field Admin or DevAdmin — tracking / survey admin / reports."""
    if not current_user.can_manage_field_ops():
        abort(403)


def _staff_ops_only():
    """Vendors / tasks staff — not Videographer, not field-only Admin."""
    if current_user.is_videographer():
        abort(403)
    if current_user.is_admin() and not current_user.is_dev_admin():
        abort(403)


def _videographer_only():
    if not current_user.is_videographer():
        abort(403)


def _retry_until(label, fn, *, attempts=None, delay_s=None):
    """Wait and retry until the call succeeds — never return fake empty data.

    Multi-user load may briefly exhaust the pool; we queue/retry instead of
    blanking the UI.
    """
    attempts = max(1, int(attempts if attempts is not None else os.getenv("API_DB_RETRIES", "8")))
    delay_s = float(delay_s if delay_s is not None else os.getenv("API_DB_RETRY_S", "0.35"))
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last = e
            print(f"[retry] {label} attempt {i + 1}/{attempts}: {e}", flush=True)
            if i < attempts - 1:
                time.sleep(delay_s * (1.0 + 0.25 * i))
    raise last or RuntimeError(f"{label} failed after {attempts} attempts")


def _user_payload():
    district_ids = list(getattr(current_user, "district_ids", None) or (
        [current_user.district_id] if getattr(current_user, "district_id", None) is not None else []
    ))
    if current_user.is_videographer() and district_ids:
        survey_service.schedule_prewarm_snap_indexes(district_ids)
    return {
        "id": current_user.id,
        "username": current_user.username,
        "full_name": current_user.full_name,
        "email": current_user.email,
        "role": current_user.role,
        "vendor_id": current_user.vendor_id,
        "is_admin": current_user.is_admin(),
        "is_dev_admin": current_user.is_dev_admin(),
        "is_supervisor": current_user.is_supervisor(),
        "is_allocator": current_user.is_allocator(),
        "is_vendor": current_user.is_vendor_role(),
        "is_videographer": current_user.is_videographer(),
        "state_id": getattr(current_user, "state_id", None),
        "district_id": getattr(current_user, "district_id", None),
        "district_ids": district_ids,
        "state_keys": survey_service.state_keys_for_district_ids(district_ids)
        if current_user.is_videographer() else [],
    }


def _vendor_from_request(data: dict, *, include_status: bool = False) -> dict:
    specs_raw = data.get("specializations", "")
    if isinstance(specs_raw, list):
        specs = [s.strip() for s in specs_raw if str(s).strip()]
    else:
        specs = [s.strip() for s in str(specs_raw).split(",") if s.strip()]
    out = {
        "company_name": (data.get("company_name") or "").strip(),
        "registration_number": (data.get("registration_number") or "").strip() or None,
        "gst_number": (data.get("gst_number") or "").strip() or None,
        "pan_number": (data.get("pan_number") or "").strip() or None,
        "contact_person_name": (data.get("contact_person_name") or "").strip(),
        "contact_phone": (data.get("contact_phone") or "").strip(),
        "contact_email": (data.get("contact_email") or "").strip() or None,
        "address": (data.get("address") or "").strip(),
        "city": (data.get("city") or "").strip(),
        "district": (data.get("district") or "").strip(),
        "state": (data.get("state") or "").strip(),
        "pin_code": (data.get("pin_code") or "").strip(),
        "description": (data.get("description") or "").strip(),
        "specializations": specs,
        "max_active_tasks": int(data.get("max_active_tasks") or 10),
    }
    if include_status:
        out["status"] = data.get("status", "Active")
        out["status_reason"] = (data.get("status_reason") or "").strip() or None
    else:
        out["status"] = "Active"
    return out
