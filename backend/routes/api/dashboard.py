"""API routes: dashboard."""
import os
import threading
import time

from flask import jsonify, request, abort
from flask_login import login_required, login_user, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

import db_utils
from routes.api import api_bp
from routes.api._helpers import _serialize, _user_payload, _vendor_from_request, _retry_until, _dev_admin_only
from routes.user_model import User
from routes.constants import VALID_TRANSITIONS
from routes import validation as validation_module
from routes import survey_service
from routes import detection_service
from routes import model_bench_service
from routes import field_upload_service
from routes import tracking_service
from routes import auto_track_service


# # ── Dashboard ─────────────────────────────────────────────────────────────────
# ── Dashboard ─────────────────────────────────────────────────────────────────

_DASHBOARD_WARRANTY_TS = 0.0
_DASHBOARD_WARRANTY_LOCK = threading.Lock()
_GPS_COVERAGE_CACHE = {"ts": 0.0, "data": None}
_GPS_COVERAGE_LOCK = threading.Lock()
_GPS_COVERAGE_TTL = float(os.getenv("GPS_COVERAGE_CACHE_S", "15"))


def _dashboard_light() -> bool:
    """Supabase free / tiny pools: soft-fail fast, skip map blobs."""
    return (os.getenv("DASHBOARD_LIGHT") or "").strip().lower() in ("1", "true", "yes")


def _dashboard_section(label, fn):
    """Dashboard section: short retry, then soft-empty — never stampede the pool."""
    attempts = 1 if _dashboard_light() else 2
    try:
        return _retry_until(label, fn, attempts=attempts, delay_s=0.15)
    except Exception as e:
        print(f"[dashboard] {label} gave up: {e}", flush=True)
        return None


def _gps_coverage_cached():
    from routes import tracking_service

    now = time.time()
    with _GPS_COVERAGE_LOCK:
        hit = _GPS_COVERAGE_CACHE.get("data")
        if hit is not None and (now - float(_GPS_COVERAGE_CACHE.get("ts") or 0)) < _GPS_COVERAGE_TTL:
            return hit
    data = _retry_until(
        "gps_coverage",
        tracking_service.aggregate_gps_coverage_bundle,
        attempts=1 if _dashboard_light() else 2,
        delay_s=0.15,
    )
    with _GPS_COVERAGE_LOCK:
        _GPS_COVERAGE_CACHE["ts"] = now
        _GPS_COVERAGE_CACHE["data"] = data
    return data


def _maybe_refresh_warranty():
    """At most once per hour — never block the dashboard request on the pool."""
    global _DASHBOARD_WARRANTY_TS

    if _dashboard_light():
        return  # free-tier: skip background UPDATE entirely

    now = time.time()
    with _DASHBOARD_WARRANTY_LOCK:
        if now - _DASHBOARD_WARRANTY_TS < 3600:
            return
        _DASHBOARD_WARRANTY_TS = now

    def _run():
        try:
            _retry_until("refresh_warranty", db_utils.refresh_warranty_statuses, attempts=1, delay_s=0.1)
        except Exception as e:
            print(f"[dashboard] refresh_warranty gave up: {e}", flush=True)

    threading.Thread(target=_run, name="warranty-refresh", daemon=True).start()


@api_bp.route("/dashboard")
@login_required
def dashboard_all():
    """Build dashboard on the request thread — retry each section until real data."""
    u = current_user
    user_id = int(u.id)
    username = (u.username or "").strip()
    is_vg = bool(u.is_videographer())
    is_dev_admin = bool(u.is_dev_admin())
    is_supervisor = bool(u.is_supervisor())
    is_vendor = bool(u.is_vendor_role())
    state_id = getattr(u, "state_id", None)
    district_id_attr = getattr(u, "district_id", None)
    district_ids = list(getattr(u, "district_ids", None) or [])
    if not district_ids and district_id_attr is not None:
        district_ids = [int(district_id_attr)]

    _maybe_refresh_warranty()

    # Staff ops dashboard — do not build KPI/vendor/map payloads for videographers.
    is_staff = bool(
        is_dev_admin or is_supervisor or u.is_allocator() or (is_vendor and not is_vg)
    )
    if is_vg and not is_dev_admin:
        payload = {
            "kpi": None,
            "breached": None,
            "vendors": None,
            "warranty": None,
            "map": None,
            "gps_coverage": {},
            "gps_coverage_by_state": {},
            "role_view": "videographer",
        }
    elif is_staff:
        map_limit = int(os.getenv("DASHBOARD_MAP_LIMIT", "0" if _dashboard_light() else "1500"))
        t0 = time.perf_counter()
        print(
            f"[dashboard] staff light={_dashboard_light()} map_limit={map_limit}",
            flush=True,
        )
        if _dashboard_light():
            # Free-tier: avoid multi-statement staff_bundle + GPS rollups (pool contention
            # with flask-login user loads). Empty KPIs are correct until real ops data exists.
            bundle = {
                "kpi": {
                    "total_sessions": 0,
                    "total_potholes": 0,
                    "total_work_orders": 0,
                    "unassigned": 0,
                    "allocated": 0,
                    "wip": 0,
                    "completed": 0,
                    "verified": 0,
                    "failed": 0,
                    "sla_breached": 0,
                    "unassigned_sessions": 0,
                },
                "breached": [],
                "vendors": [],
                "warranty": [],
                "map": [],
            }
            gps_bundle = {"all": {}, "by_state": {}, "date": None}
            print(f"[dashboard] staff light skip-db {time.perf_counter()-t0:.2f}s", flush=True)
        else:
            bundle = _dashboard_section(
                "staff_bundle",
                lambda: db_utils.get_staff_dashboard_bundle(map_limit=map_limit),
            ) or {}
            print(f"[dashboard] staff_bundle {time.perf_counter()-t0:.2f}s", flush=True)
            t1 = time.perf_counter()
            gps_bundle = _dashboard_section("gps_coverage", _gps_coverage_cached) or {
                "all": {}, "by_state": {}, "date": None,
            }
            print(f"[dashboard] gps {time.perf_counter()-t1:.2f}s", flush=True)
        payload = {
            "kpi": bundle.get("kpi"),
            "breached": bundle.get("breached"),
            "vendors": bundle.get("vendors"),
            "warranty": bundle.get("warranty"),
            "map": bundle.get("map") if map_limit > 0 else [],
            "gps_coverage": gps_bundle.get("all") or {},
            "gps_coverage_by_state": gps_bundle.get("by_state") or {},
            "role_view": "admin",
        }
        print(f"[dashboard] staff total {time.perf_counter()-t0:.2f}s", flush=True)
    else:
        payload = {"role_view": "unknown"}

    if is_vg and not is_dev_admin:
        district_id = district_ids[0] if district_ids else district_id_attr
        state_key = survey_service.resolve_state_key(state_id=state_id)
        field = {
            "state_id": state_id,
            "district_id": district_id,
            "district_ids": district_ids,
            "district_name": None,
            "district_names": [],
            "state_name": None,
            "videos_uploaded": 0,
            "km_today": 0,
            "covered_km": 0,
            "covered_by_class": {"nh": 0, "sh": 0, "mdr": 0, "other": 0},
            "segments_today": 0,
            "target_km": float(survey_service.get_settings().get("daily_km", 100)),
            "focus_road_class": survey_service.get_settings().get("focus_road_class", "all"),
            "quota_incomplete": True,
        }
        if district_ids and state_key:
            names = []
            for did in district_ids:
                dist = survey_service.get_district(did, state_key) or survey_service.get_district(did)
                if dist:
                    names.append(dist.get("name") or str(did))
                    if not field["district_name"]:
                        field["district_name"] = dist.get("name")
                        field["state_name"] = dist.get("state_name")
            field["district_names"] = names
            field["state_keys"] = survey_service.state_keys_for_district_ids(district_ids)
            summary = _dashboard_section(
                "field_assignment",
                lambda: survey_service.assignment_summary_for_user(
                    user_id, light=True, sync_coverage=False,
                ),
            ) or {}
            field["km_today"] = summary.get("total_km", 0)
            field["covered_km"] = summary.get("covered_km", 0)
            field["covered_by_class"] = summary.get("covered_by_class") or field["covered_by_class"]
            field["segments_today"] = summary.get("segment_count", 0)
            field["target_km"] = summary.get("target_km", field["target_km"])
            field["quota_incomplete"] = summary.get("quota_incomplete", True)
        field["gps_coverage"] = _dashboard_section(
            "field_gps",
            lambda: tracking_service.aggregate_gps_coverage(user_id=user_id),
        ) or {}
        # Prefer resolved GPS aggregate (same source as Tracking) over stale
        # assignment-row covered / all-"other" class blobs.
        gc = field.get("gps_coverage") or {}
        gc_km = float(gc.get("covered_km") or 0)
        gc_by = gc.get("by_class") or {}
        if gc_km > 0.01 or any(float(gc_by.get(k) or 0) > 0.01 for k in ("nh", "sh", "mdr", "other")):
            field["covered_km"] = gc_km if gc_km > 0.01 else field.get("covered_km")
            field["covered_by_class"] = gc_by or field["covered_by_class"]
            if float(gc.get("assigned_km") or 0) > 0.01:
                field["km_today"] = gc.get("assigned_km")
        try:
            keys = field_upload_service.list_recent_keys(limit=50).get("keys") or []
            media_ext = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".jpg", ".jpeg", ".png")
            field["videos_uploaded"] = sum(
                1
                for k in keys
                if username
                and (f"/{username}/" in f"/{k}" or k.startswith(f"{username}/"))
                and str(k).lower().endswith(media_ext)
            )
        except Exception:
            field["videos_uploaded"] = 0
        payload["field"] = field
        payload["role_view"] = "videographer"
    elif is_supervisor and not is_dev_admin:
        payload["role_view"] = "supervisor"
    elif is_vendor:
        payload["role_view"] = "vendor"
    return jsonify(_serialize(payload))


@api_bp.route("/dashboard/map-data")
@login_required
def dashboard_map():
    if current_user.is_videographer() and not current_user.is_dev_admin():
        abort(403)
    data = _retry_until(
        "map-data",
        lambda: db_utils.get_pothole_map_data(
            limit=int(os.getenv("DASHBOARD_MAP_LIMIT", "1500")),
        ),
    )
    return jsonify(_serialize(data))


