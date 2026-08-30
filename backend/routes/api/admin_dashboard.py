"""API routes: field Admin dashboard KPIs + video drill-down."""
from __future__ import annotations

import os
import re
import tempfile
import zipfile
from datetime import date, datetime, timedelta
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

from flask import jsonify, request, send_file
from flask_login import login_required, current_user

import db_utils
from routes.api import api_bp
from routes.api._helpers import _serialize

IST = ZoneInfo("Asia/Kolkata")
PAGE_SIZE = 10
_DRILLDOWN_RANGES = frozenset({"today", "yesterday", "overall", "custom"})
_SHA_MP4_RE = re.compile(r"^[0-9a-f]{64}\.mp4$", re.IGNORECASE)


def _today_ist() -> date:
    return datetime.now(IST).date()


def _parse_range():
    """Return (start_date, end_date) from ?range= or ?start=&end= params."""
    r = (request.args.get("range") or "today").lower()
    today = _today_ist()
    if r == "yesterday":
        d = today - timedelta(days=1)
        return d, d
    if r == "overall":
        return None, None
    if r == "custom":
        try:
            s = date.fromisoformat(request.args["start"])
            e = date.fromisoformat(request.args["end"])
            return s, e
        except (KeyError, ValueError):
            return today, today
    return today, today


def _drilldown_range_or_error():
    """Validate drill-down range; return (range_key, error_response_or_None)."""
    r = (request.args.get("range") or "today").lower()
    if r not in _DRILLDOWN_RANGES:
        return r, (jsonify({"error": "Invalid range for drill-down"}), 400)
    if r == "custom":
        try:
            date.fromisoformat(request.args["start"])
            date.fromisoformat(request.args["end"])
        except (KeyError, ValueError):
            return r, (jsonify({"error": "Custom range requires start and end dates"}), 400)
    return r, None


def _vs_processed_clause(alias: str = "vs") -> tuple[str, list]:
    """Optional date filter on video_sessions.processed_at (IST day)."""
    start, end = _parse_range()
    if start and end:
        return (
            f"AND ({alias}.processed_at AT TIME ZONE 'Asia/Kolkata')::date BETWEEN %s AND %s",
            [start, end],
        )
    return "", []


def _ts_track_clause(alias: str = "ts") -> tuple[str, list]:
    """Optional date filter on tracking_sessions.track_date."""
    start, end = _parse_range()
    if start and end:
        return f"AND {alias}.track_date BETWEEN %s AND %s", [start, end]
    return "", []


def _looks_like_sha_mp4(name: str | None) -> bool:
    return bool(_SHA_MP4_RE.match((name or "").strip()))


def _friendly_video_name(
    session_id: int,
    display_name: str | None,
    filename: str | None,
    username: str | None = None,
) -> str:
    """Prefer human display_name; never surface raw sha256.mp4 keys in Admin UI."""
    for cand in (display_name, filename):
        name = (cand or "").strip()
        if name and not _looks_like_sha_mp4(name):
            return name
    try:
        from routes.report_service import build_display_names

        video, _ = build_display_names(
            username=username or "user",
            session_id=int(session_id),
        )
        return video
    except Exception:
        return f"session_{int(session_id)}.mp4"


def _s3_key_from_frame_url(url: str | None) -> str | None:
    raw = (url or "").strip()
    if not raw:
        return None
    if "://" not in raw and ("/" in raw or raw.endswith(".jpg")):
        return raw.split("?", 1)[0].lstrip("/")
    path = unquote(urlparse(raw).path or "").lstrip("/")
    if not path:
        return None
    # Virtual-hosted: /runs/...  Path-style: /bucket/runs/...
    if path.startswith(("runs/", "sources/")):
        return path
    parts = path.split("/", 1)
    if len(parts) == 2 and parts[1].startswith(("runs/", "sources/")):
        return parts[1]
    return path


def _clean_frame_key(overlay_key: str) -> str:
    """Legacy run-scoped clean path (pre sources/.../frames/)."""
    if "/outputs/frames/" in overlay_key:
        return overlay_key.replace("/outputs/frames/", "/outputs/frames_clean/", 1)
    return overlay_key


def _sources_clean_frame_keys(
    *,
    overlay_key: str,
    s3_key: str | None,
) -> list[str]:
    """Candidate keys for the no-overlay twin of an overlay frame JPG."""
    base = os.path.basename(overlay_key or "")
    if not base:
        return []
    keys: list[str] = []
    if s3_key:
        try:
            from routes.field_upload_service import processed_source_frames_prefix

            prefix = processed_source_frames_prefix(s3_key)
            if prefix:
                keys.append(f"{prefix}/{base}")
        except Exception:
            pass
    # Derive sources/{user}/{route}/frames/ from runs/{user}/{route}/{run}/outputs/frames/
    parts = (overlay_key or "").replace("\\", "/").split("/")
    if (
        len(parts) >= 7
        and parts[0] == "runs"
        and parts[-2] == "frames"
        and parts[-3] == "outputs"
    ):
        keys.append(f"sources/{parts[1]}/{parts[2]}/frames/{base}")
    # Older detects uploaded clean twins next to run outputs
    legacy = _clean_frame_key(overlay_key)
    if legacy and legacy != overlay_key:
        keys.append(legacy)
    seen = set()
    out = []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _looks_like_coords(label: str | None) -> bool:
    s = (label or "").strip()
    if not s:
        return True
    # bare "12.34, 78.90" style — treat as missing name
    parts = [p.strip() for p in s.replace(";", ",").split(",") if p.strip()]
    if len(parts) == 2:
        try:
            float(parts[0])
            float(parts[1])
            return True
        except ValueError:
            return False
    return False


def _place_from_pothole(row: dict | None) -> str | None:
    if not row:
        return None
    for key in ("street_name", "full_address", "city"):
        val = (row.get(key) or "").strip()
        if val and not _looks_like_coords(val):
            city = (row.get("city") or "").strip()
            if key == "street_name" and city and city.lower() not in val.lower():
                return f"{val}, {city}"
            return val
    return None


def _reverse_place(lat, lon) -> str | None:
    if lat is None or lon is None:
        return None
    try:
        from routes import survey_service
        rev = survey_service.reverse_geocode(
            float(lat), float(lon),
            allow_nominatim=True,
            prefer_places=True,
        ) or {}
        name = (
            rev.get("display_name")
            or rev.get("name")
            or rev.get("road")
            or ""
        ).strip()
        if name and not _looks_like_coords(name):
            return name
    except Exception:
        pass
    try:
        from routes import geocode_utils
        geo = geocode_utils.reverse_geocode(float(lat), float(lon)) or {}
        street = (geo.get("street_name") or "").strip()
        city = (geo.get("city") or "").strip()
        if street and city:
            return f"{street}, {city}"
        if street or city or (geo.get("full_address") or "").strip():
            return street or city or geo.get("full_address")
    except Exception:
        pass
    return None


def _endpoint_labels(session_id: int, start_label: str | None, end_label: str | None) -> tuple[str, str]:
    from_name = None if _looks_like_coords(start_label) else (start_label or "").strip() or None
    to_name = None if _looks_like_coords(end_label) else (end_label or "").strip() or None
    if from_name and to_name:
        return from_name, to_name

    first = last = None
    try:
        with db_utils.db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT street_name, city, full_address, latitude, longitude
                FROM potholes
                WHERE session_id = %s
                  AND latitude IS NOT NULL AND longitude IS NOT NULL
                ORDER BY id ASC
                LIMIT 1
                """,
                (session_id,),
            )
            row = cur.fetchone()
            if row:
                first = {
                    "street_name": row[0], "city": row[1], "full_address": row[2],
                    "lat": row[3], "lon": row[4],
                }
            cur.execute(
                """
                SELECT street_name, city, full_address, latitude, longitude
                FROM potholes
                WHERE session_id = %s
                  AND latitude IS NOT NULL AND longitude IS NOT NULL
                ORDER BY id DESC
                LIMIT 1
                """,
                (session_id,),
            )
            row = cur.fetchone()
            if row:
                last = {
                    "street_name": row[0], "city": row[1], "full_address": row[2],
                    "lat": row[3], "lon": row[4],
                }
    except Exception:
        first = last = None

    if not from_name:
        from_name = _place_from_pothole(first) or (
            _reverse_place(first.get("lat"), first.get("lon")) if first else None
        )
    if not to_name:
        to_name = _place_from_pothole(last) or (
            _reverse_place(last.get("lat"), last.get("lon")) if last else None
        )
    return from_name or "Unknown start", to_name or "Unknown end"


@api_bp.route("/admin-dashboard")
@login_required
def admin_dashboard_kpis():
    if not current_user.can_manage_field_ops():
        return jsonify({"error": "forbidden"}), 403

    start, end = _parse_range()

    with db_utils.db_connection() as conn:
        cur = conn.cursor()

        date_clause_td = ""
        params: list = []

        if start and end:
            date_clause_td = "AND (vs.processed_at AT TIME ZONE 'Asia/Kolkata')::date BETWEEN %s AND %s"
            params = [start, end]

        cur.execute(f"""
            SELECT COUNT(*) FROM video_sessions vs
            WHERE 1=1 {date_clause_td}
        """, params if start else [])
        videos_count = cur.fetchone()[0]

        if start and end:
            cur.execute("""
                SELECT COUNT(DISTINCT ts.user_id)
                FROM tracking_sessions ts
                WHERE ts.track_date BETWEEN %s AND %s
                  AND (
                    ts.last_ts IS NOT NULL
                    OR ts.recording = TRUE
                    OR COALESCE(ts.covered_km, 0) > 0
                    OR EXISTS (
                        SELECT 1 FROM tracking_trail_points tp
                        WHERE tp.session_id = ts.id
                        LIMIT 1
                    )
                )
            """, [start, end])
        else:
            cur.execute("""
                SELECT COUNT(DISTINCT ts.user_id)
                FROM tracking_sessions ts
                WHERE ts.last_ts IS NOT NULL
                   OR ts.recording = TRUE
                   OR COALESCE(ts.covered_km, 0) > 0
                   OR EXISTS (
                        SELECT 1 FROM tracking_trail_points tp
                        WHERE tp.session_id = ts.id
                        LIMIT 1
                   )
            """)
        active_members = cur.fetchone()[0]

        if start and end:
            cur.execute("""
                SELECT COALESCE(SUM(covered_km), 0) FROM tracking_sessions ts
                WHERE ts.track_date BETWEEN %s AND %s
            """, [start, end])
        else:
            cur.execute("SELECT COALESCE(SUM(covered_km), 0) FROM tracking_sessions")
        km_covered = round(float(cur.fetchone()[0]), 2)

        cur.execute(f"""
            SELECT COALESCE(SUM(total_potholes), 0) FROM video_sessions vs
            WHERE 1=1 {date_clause_td}
        """, params if start else [])
        potholes_count = cur.fetchone()[0]

    return jsonify({
        "videos_count": videos_count,
        "active_members": active_members,
        "km_covered": km_covered,
        "potholes_count": potholes_count,
        "range": request.args.get("range", "today"),
    })


@api_bp.route("/admin-dashboard/videos")
@login_required
def admin_dashboard_videos():
    """Paginated video list for KPI drill-down (10 / page)."""
    if not current_user.can_manage_field_ops():
        return jsonify({"error": "forbidden"}), 403

    r, err = _drilldown_range_or_error()
    if err:
        return err

    date_clause, date_params = _vs_processed_clause("vs")
    try:
        page = max(1, int(request.args.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    page_size = PAGE_SIZE
    offset = (page - 1) * page_size

    with db_utils.db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT COUNT(*) FROM video_sessions vs
            WHERE 1=1 {date_clause}
            """,
            date_params,
        )
        total = int(cur.fetchone()[0] or 0)

        cur.execute(
            f"""
            SELECT vs.id,
                   vs.display_name,
                   vs.filename,
                   vs.username,
                   vs.user_id,
                   vs.start_label,
                   vs.end_label,
                   COALESCE(vs.total_potholes, 0) AS total_potholes,
                   TO_CHAR(vs.processed_at AT TIME ZONE 'Asia/Kolkata',
                           'YYYY-MM-DD HH24:MI') AS processed_at
            FROM video_sessions vs
            WHERE 1=1 {date_clause}
            ORDER BY vs.processed_at DESC, vs.id DESC
            LIMIT %s OFFSET %s
            """,
            [*date_params, page_size, offset],
        )
        rows = cur.fetchall()

    items = []
    for row in rows:
        sid, display_name, filename, username, user_id, start_label, end_label, pot_count, processed_at = row
        from_label, to_label = _endpoint_labels(int(sid), start_label, end_label)
        items.append({
            "id": int(sid),
            "video_name": _friendly_video_name(int(sid), display_name, filename, username),
            "username": username,
            "user_id": user_id,
            "from_label": from_label,
            "to_label": to_label,
            "route": f"{from_label} → {to_label}",
            "total_potholes": int(pot_count or 0),
            "processed_at": processed_at,
        })

    page_count = max(1, (total + page_size - 1) // page_size) if total else 1
    return jsonify({
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "page_count": page_count,
        "range": r,
    })


@api_bp.route("/admin-dashboard/videos/<int:session_id>")
@login_required
def admin_dashboard_video_detail(session_id: int):
    """Vertical detail payload for one video session."""
    if not current_user.can_manage_field_ops():
        return jsonify({"error": "forbidden"}), 403

    session = db_utils.get_session_by_id(int(session_id))
    if not session:
        return jsonify({"error": "not found"}), 404

    potholes = db_utils.get_potholes_for_session(int(session_id)) or []
    from_label, to_label = _endpoint_labels(
        int(session_id),
        session.get("start_label"),
        session.get("end_label"),
    )

    from routes.report_service import compute_road_stats, severity_distribution

    # Annotated output in processed bucket (runs/{user}/{route}/{run_id}/outputs/)
    output_urls = {}
    try:
        from routes.detection.catalog import resolve_session_output_urls

        output_urls = resolve_session_output_urls(session) or {}
    except Exception:
        output_urls = {}

    # Prefer cumulative GPS distance on detections when available
    gps_log_km = 0.0
    try:
        with db_utils.db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT COALESCE(MAX(cumulative_distance_meters), 0)
                FROM potholes WHERE session_id = %s
                """,
                (session_id,),
            )
            gps_log_km = float(cur.fetchone()[0] or 0) / 1000.0
    except Exception:
        gps_log_km = 0.0

    stats = compute_road_stats(potholes, None, covered_km=None, gps_log_km=gps_log_km)
    sev = severity_distribution(potholes)
    total_km = float(stats.get("total_km") or 0)
    good_pct = float(stats.get("clear_pct") or 0)
    damaged_pct = float(stats.get("pothole_pct") or 0)
    if total_km <= 0 and not potholes:
        good_pct, damaged_pct = 100.0, 0.0
    elif total_km <= 0 and potholes:
        # No length — fall back to severity-weighted share
        high = int(sev.get("High") or 0)
        med = int(sev.get("Medium") or 0)
        low = int(sev.get("Low") or 0)
        n = high + med + low
        if n:
            damaged_pct = round(100.0 * (high * 1.0 + med * 0.6 + low * 0.3) / n, 1)
            good_pct = round(max(0.0, 100.0 - damaged_pct), 1)

    full_name = None
    username = session.get("username")
    uid = session.get("user_id")
    if uid:
        try:
            urow = db_utils.get_user_by_id(int(uid))
            if urow:
                full_name = urow.get("full_name")
                username = username or urow.get("username")
        except Exception:
            pass

    return jsonify(_serialize({
        "id": int(session_id),
        "video_name": _friendly_video_name(
            int(session_id),
            session.get("display_name"),
            session.get("filename"),
            username,
        ),
        "filename": session.get("filename"),
        "username": username,
        "full_name": full_name or username or "Unknown",
        "user_id": uid,
        "from_label": from_label,
        "to_label": to_label,
        "route": f"{from_label} → {to_label}",
        "processed_at": session.get("processed_at"),
        "potholes": {
            "total": len(potholes),
            "high": int(sev.get("High") or 0),
            "medium": int(sev.get("Medium") or 0),
            "low": int(sev.get("Low") or 0),
        },
        "km_covered": round(total_km or float(stats.get("covered_km") or 0) or gps_log_km, 2),
        "good_pct": good_pct,
        "damaged_pct": damaged_pct,
        "proper_km": float(stats.get("proper_km") or 0),
        "affected_km": float(stats.get("affected_km") or 0),
        "output_video_url": output_urls.get("output_video_url"),
        "output_image_url": output_urls.get("output_image_url"),
        "media_kind": output_urls.get("media_kind") or "video",
    }))


@api_bp.route("/admin-dashboard/videos/<int:session_id>/requeue", methods=["POST"])
@login_required
def admin_dashboard_video_requeue(session_id: int):
    """Restore video to input bucket, purge processed objects, delete DB rows."""
    if not current_user.can_manage_field_ops():
        return jsonify({"error": "forbidden"}), 403

    try:
        from routes.requeue_service import requeue_video_session

        result = requeue_video_session(int(session_id))
    except LookupError:
        return jsonify({"error": "not found"}), 404
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e) or "requeue failed"}), 500

    return jsonify(_serialize(result))


@api_bp.route("/admin-dashboard/potholes")
@login_required
def admin_dashboard_potholes_summary():
    """High/medium/low counts for KPI drill-down."""
    if not current_user.can_manage_field_ops():
        return jsonify({"error": "forbidden"}), 403

    r, err = _drilldown_range_or_error()
    if err:
        return err

    date_clause, date_params = _vs_processed_clause("vs")
    with db_utils.db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT
                COUNT(*) FILTER (WHERE LOWER(COALESCE(p.severity, '')) = 'high') AS high,
                COUNT(*) FILTER (WHERE LOWER(COALESCE(p.severity, '')) = 'medium') AS medium,
                COUNT(*) FILTER (WHERE LOWER(COALESCE(p.severity, '')) = 'low') AS low,
                COUNT(*) AS total
            FROM potholes p
            JOIN video_sessions vs ON vs.id = p.session_id
            WHERE 1=1 {date_clause}
            """,
            date_params,
        )
        high, medium, low, total = cur.fetchone()

    return jsonify({
        "range": r,
        "high": int(high or 0),
        "medium": int(medium or 0),
        "low": int(low or 0),
        "total": int(total or 0),
        "severities": [
            {"key": "high", "label": "High", "count": int(high or 0)},
            {"key": "medium", "label": "Medium", "count": int(medium or 0)},
            {"key": "low", "label": "Low", "count": int(low or 0)},
        ],
    })


@api_bp.route("/admin-dashboard/potholes/details")
@login_required
def admin_dashboard_potholes_details():
    """Paginated pothole rows with thumbnail + map link."""
    if not current_user.can_manage_field_ops():
        return jsonify({"error": "forbidden"}), 403

    r, err = _drilldown_range_or_error()
    if err:
        return err

    date_clause, date_params = _vs_processed_clause("vs")
    severity = (request.args.get("severity") or "").strip().lower()
    session_id_raw = (request.args.get("session_id") or "").strip()
    session_id = None
    if session_id_raw:
        try:
            session_id = int(session_id_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "invalid session_id"}), 400
    try:
        page = max(1, int(request.args.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    # 12 rows keeps ~10–15 visible with small thumbs
    page_size = 12
    offset = (page - 1) * page_size

    sev_clause = ""
    session_clause = ""
    params: list = list(date_params)
    if severity in ("high", "medium", "low"):
        sev_clause = "AND LOWER(COALESCE(p.severity, '')) = %s"
        params.append(severity)
    if session_id is not None:
        session_clause = "AND p.session_id = %s"
        params.append(session_id)

    with db_utils.db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT COUNT(*)
            FROM potholes p
            JOIN video_sessions vs ON vs.id = p.session_id
            WHERE 1=1 {date_clause}
              {sev_clause}
              {session_clause}
            """,
            params,
        )
        total = int(cur.fetchone()[0] or 0)

        cur.execute(
            f"""
            SELECT p.id,
                   COALESCE(NULLIF(TRIM(p.severity), ''), 'Unknown') AS severity,
                   ROUND(p.confidence::numeric, 3) AS confidence,
                   p.latitude, p.longitude,
                   p.map_link, p.frame_s3_url,
                   COALESCE(NULLIF(TRIM(p.street_name), ''), NULLIF(TRIM(p.full_address), ''), '') AS place,
                   vs.display_name,
                   vs.filename,
                   vs.username,
                   vs.id AS session_id,
                   TO_CHAR(vs.processed_at AT TIME ZONE 'Asia/Kolkata', 'YYYY-MM-DD HH24:MI') AS processed_at
            FROM potholes p
            JOIN video_sessions vs ON vs.id = p.session_id
            WHERE 1=1 {date_clause}
              {sev_clause}
              {session_clause}
            ORDER BY
              CASE LOWER(COALESCE(p.severity, ''))
                WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2 ELSE 3
              END,
              p.id DESC
            LIMIT %s OFFSET %s
            """,
            [*params, page_size, offset],
        )
        rows = cur.fetchall()

    items = []
    for row in rows:
        (
            pid, sev, conf, lat, lon, map_link, frame_url, place,
            display_name, filename, username, sid, processed_at,
        ) = row
        link = (map_link or "").strip()
        if not link and lat is not None and lon is not None:
            try:
                link = f"https://www.google.com/maps?q={float(lat):.6f},{float(lon):.6f}"
            except (TypeError, ValueError):
                link = ""
        items.append({
            "id": int(pid),
            "severity": sev,
            "confidence": float(conf) if conf is not None else None,
            "lat": float(lat) if lat is not None else None,
            "lon": float(lon) if lon is not None else None,
            "map_link": link or None,
            "frame_url": frame_url or None,
            "place": place or None,
            "video_name": _friendly_video_name(int(sid), display_name, filename, username),
            "username": username,
            "processed_at": processed_at,
        })

    page_count = max(1, (total + page_size - 1) // page_size) if total else 1
    return jsonify({
        "range": r,
        "severity": severity or "all",
        "session_id": session_id,
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "page_count": page_count,
    })


@api_bp.route("/admin-dashboard/potholes/images-zip")
@login_required
def admin_dashboard_potholes_images_zip():
    """Zip of pothole frames: with_overlay/ + without_overlay/ (true clean frames only)."""
    if not current_user.can_manage_field_ops():
        return jsonify({"error": "forbidden"}), 403

    r, err = _drilldown_range_or_error()
    if err:
        return err

    date_clause, date_params = _vs_processed_clause("vs")
    severity = (request.args.get("severity") or "").strip().lower()
    session_id_raw = (request.args.get("session_id") or "").strip()
    session_id = None
    if session_id_raw:
        try:
            session_id = int(session_id_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "invalid session_id"}), 400

    sev_clause = ""
    session_clause = ""
    params: list = list(date_params)
    if severity in ("high", "medium", "low"):
        sev_clause = "AND LOWER(COALESCE(p.severity, '')) = %s"
        params.append(severity)
    if session_id is not None:
        session_clause = "AND p.session_id = %s"
        params.append(session_id)

    with db_utils.db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT p.id,
                   COALESCE(NULLIF(TRIM(p.severity), ''), 'Unknown') AS severity,
                   p.frame_s3_url,
                   vs.id AS session_id,
                   vs.display_name,
                   vs.filename,
                   vs.username,
                   vs.s3_key
            FROM potholes p
            JOIN video_sessions vs ON vs.id = p.session_id
            WHERE 1=1 {date_clause}
              {sev_clause}
              {session_clause}
              AND COALESCE(NULLIF(TRIM(p.frame_s3_url), ''), '') <> ''
            ORDER BY vs.id DESC, p.id ASC
            LIMIT 800
            """,
            params,
        )
        rows = cur.fetchall()

    if not rows:
        return jsonify({"error": "No pothole frames available for this selection."}), 404

    try:
        from s3_utils import download_file, get_processed_bucket, head_exists, is_s3_configured
    except Exception as e:
        return jsonify({"error": f"S3 tooling unavailable: {e}"}), 500

    if not is_s3_configured():
        return jsonify({"error": "S3 not configured"}), 503

    bucket = get_processed_bucket()
    # Group by overlay frame key (one image may cover multiple potholes).
    by_key: dict[str, dict] = {}
    for pid, sev, frame_url, sid, display_name, filename, username, s3_key in rows:
        key = _s3_key_from_frame_url(frame_url)
        if not key:
            continue
        by_key.setdefault(
            key,
            {
                "session_id": int(sid),
                "video_name": _friendly_video_name(int(sid), display_name, filename, username),
                "s3_key": s3_key,
                "severity": sev,
            },
        )

    if not by_key:
        return jsonify({"error": "Could not resolve frame keys from stored URLs."}), 404

    tmp = tempfile.NamedTemporaryFile(prefix="pothole_frames_", suffix=".zip", delete=False)
    tmp_path = tmp.name
    tmp.close()

    written_overlay = 0
    written_clean = 0
    try:
        with tempfile.TemporaryDirectory(prefix="pf_zip_") as work_dir:
            with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(
                    "README.txt",
                    (
                        "with_overlay/     — detection frames with OpenCV boxes/labels\n"
                        "without_overlay/  — same frame saved at detect time with no overlay\n"
                        "                  (sources/<user>/<route>/frames/*.jpg)\n"
                        "If a clean twin was not stored for a frame, it is omitted from\n"
                        "without_overlay/ rather than inventing a scrubbed image.\n"
                    ),
                )
                for idx, (overlay_key, meta) in enumerate(by_key.items(), start=1):
                    overlay_local = os.path.join(
                        work_dir, f"ov_{idx:04d}.jpg"
                    )
                    try:
                        download_file(bucket, overlay_key, overlay_local)
                        with open(overlay_local, "rb") as fh:
                            overlay_bytes = fh.read()
                    except Exception as e:
                        print(f"[admin-zip] overlay download failed {overlay_key}: {e}")
                        continue

                    base = os.path.splitext(os.path.basename(overlay_key))[0] or f"frame_{idx:03d}"
                    safe_video = re.sub(
                        r"[^\w.\-]+",
                        "_",
                        meta["video_name"] or f"session_{meta['session_id']}",
                    )
                    stem = f"{safe_video}__{base}"
                    zf.writestr(f"with_overlay/{stem}.jpg", overlay_bytes)
                    written_overlay += 1

                    clean_bytes = None
                    for clean_key in _sources_clean_frame_keys(
                        overlay_key=overlay_key, s3_key=meta.get("s3_key")
                    ):
                        if not head_exists(bucket, clean_key):
                            continue
                        clean_local = os.path.join(work_dir, f"cl_{idx:04d}.jpg")
                        try:
                            download_file(bucket, clean_key, clean_local)
                            with open(clean_local, "rb") as fh:
                                clean_bytes = fh.read()
                            break
                        except Exception as e:
                            print(f"[admin-zip] clean download failed {clean_key}: {e}")
                    if clean_bytes:
                        zf.writestr(f"without_overlay/{stem}.jpg", clean_bytes)
                        written_clean += 1
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return jsonify({"error": f"Failed to build zip: {e}"}), 500

    if written_overlay <= 0:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return jsonify({"error": "No frames could be downloaded."}), 404

    tag = f"s{session_id}" if session_id else r
    sev_tag = severity if severity in ("high", "medium", "low") else "all"
    download_name = f"pothole_frames_{tag}_{sev_tag}.zip"

    response = send_file(
        tmp_path,
        mimetype="application/zip",
        as_attachment=True,
        download_name=download_name,
        max_age=0,
    )

    @response.call_on_close
    def _cleanup():
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    print(
        f"[admin-zip] wrote overlay={written_overlay} clean={written_clean} "
        f"tag={tag} sev={sev_tag}"
    )
    return response


# ── KM coverage by road class ──────────────────────────────────────────────────

_CLASS_LABELS = {
    "nh": "National highways",
    "sh": "State highways",
    "mdr": "MDR",
    "other": "Local roads",
}


def _normalize_road_ref(ref: str | None) -> str:
    import re
    s = (ref or "").strip().upper()
    if not s:
        return ""
    s = s.replace("—", "-").replace("–", "-").replace("/", "-")
    s = re.sub(r"\s+", "", s)
    m = re.match(r"^(NH|SH|MDR|ODR|VR)[-]?(\d+[A-Z]?)$", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return s


def _sum_covered_by_class(start: date | None, end: date | None) -> dict:
    import json
    totals = {"nh": 0.0, "sh": 0.0, "mdr": 0.0, "other": 0.0}
    with db_utils.db_connection() as conn:
        cur = conn.cursor()
        if start and end:
            cur.execute(
                """
                SELECT covered_by_class, covered_km
                FROM tracking_sessions
                WHERE track_date BETWEEN %s AND %s
                """,
                [start, end],
            )
        else:
            cur.execute(
                """
                SELECT covered_by_class, covered_km
                FROM tracking_sessions
                """
            )
        for cbc, covered_km in cur.fetchall():
            blob = cbc
            if isinstance(blob, (bytes, memoryview)):
                try:
                    blob = json.loads(bytes(blob).decode("utf-8"))
                except Exception:
                    blob = {}
            elif isinstance(blob, str):
                try:
                    blob = json.loads(blob) or {}
                except Exception:
                    blob = {}
            if not isinstance(blob, dict):
                blob = {}
            if not blob and covered_km:
                totals["other"] += float(covered_km or 0)
                continue
            for k in totals:
                try:
                    totals[k] += float((blob or {}).get(k) or 0)
                except (TypeError, ValueError):
                    pass
    return {k: round(v, 2) for k, v in totals.items()}


def _ref_total_km(state_key: str, district_id: str, road_class: str, ref_norm: str) -> float:
    """Sum GIS segment lengths for an official ref within one district."""
    if not ref_norm or not state_key or not district_id:
        return 0.0
    try:
        from routes.survey import state as survey_state
        meta = survey_state._segment_meta(state_key, str(district_id)) or {}
    except Exception:
        return 0.0
    total = 0.0
    for m in meta.values():
        if (m.get("road_class") or "other") != road_class:
            continue
        if _normalize_road_ref(m.get("ref")) != ref_norm:
            continue
        try:
            total += float(m.get("length") or 0)
        except (TypeError, ValueError):
            pass
    return total


def _name_total_km(state_key: str, district_id: str, road_class: str, name: str) -> float:
    if not name or not state_key or not district_id:
        return 0.0
    key = name.strip().lower()
    try:
        from routes.survey import state as survey_state
        meta = survey_state._segment_meta(state_key, str(district_id)) or {}
    except Exception:
        return 0.0
    total = 0.0
    for m in meta.values():
        if (m.get("road_class") or "other") != road_class:
            continue
        if str(m.get("name") or "").strip().lower() != key:
            continue
        try:
            total += float(m.get("length") or 0)
        except (TypeError, ValueError):
            pass
    return total


def _snap_trail_point(lat: float, lon: float, state_id, district_id) -> dict:
    """Resolve official ref/name for a GPS point."""
    from routes import survey_service
    from routes.survey import state as survey_state

    state_key = survey_service.resolve_state_key(state_id=state_id)
    if not state_key and district_id is not None:
        try:
            state_key = survey_state._state_key_for_district_id(district_id)
        except Exception:
            state_key = None

    name = ref = district_name = ""
    if state_key and district_id is not None:
        try:
            hit = survey_state._nearest_snap_in_district(
                float(lat), float(lon), state_key, str(district_id), max_km=0.45,
            )
            if hit:
                _slat, _slon, name, ref, district_name, _d = hit
        except Exception:
            pass

    if not name and not ref:
        try:
            rev = survey_service.reverse_geocode(
                float(lat), float(lon),
                district_ids=[district_id] if district_id is not None else None,
                state_keys=[state_key] if state_key else None,
                allow_nominatim=True,
                prefer_places=False,
            ) or {}
            name = (rev.get("display_name") or rev.get("name") or rev.get("road") or "").strip()
            ref = (rev.get("ref") or "").strip()
        except Exception:
            pass

    return {
        "name": (name or "").strip(),
        "ref": (ref or "").strip(),
        "district_name": (district_name or "").strip(),
        "state_key": state_key,
        "district_id": str(district_id) if district_id is not None else None,
    }


@api_bp.route("/admin-dashboard/km-coverage")
@login_required
def admin_dashboard_km_coverage():
    """Class-level KM covered summary for KPI drill-down."""
    if not current_user.can_manage_field_ops():
        return jsonify({"error": "forbidden"}), 403

    r, err = _drilldown_range_or_error()
    if err:
        return err

    start, end = _parse_range()
    by_class = _sum_covered_by_class(start, end)
    total = round(sum(by_class.values()), 2)
    classes = []
    for key in ("nh", "sh", "mdr", "other"):
        km = float(by_class.get(key) or 0)
        classes.append({
            "key": key,
            "label": _CLASS_LABELS[key],
            "covered_km": km,
            "pct_of_total": round((km / total) * 100, 1) if total > 0 else 0.0,
        })
    return jsonify({
        "range": r,
        "total_km": total,
        "by_class": by_class,
        "classes": classes,
    })


def _parse_cbc(blob) -> dict:
    import json
    if isinstance(blob, (bytes, memoryview)):
        try:
            blob = json.loads(bytes(blob).decode("utf-8"))
        except Exception:
            blob = {}
    elif isinstance(blob, str):
        try:
            blob = json.loads(blob) or {}
        except Exception:
            blob = {}
    return blob if isinstance(blob, dict) else {}


def _session_class_km(cbc, covered_km, rc: str) -> float:
    """Match _sum_covered_by_class: empty CBC dumps covered_km into other."""
    blob = _parse_cbc(cbc)
    typed = False
    for k in ("nh", "sh", "mdr", "other"):
        try:
            if float(blob.get(k) or 0) > 0:
                typed = True
                break
        except (TypeError, ValueError):
            pass
    if not typed:
        return float(covered_km or 0) if rc == "other" else 0.0
    try:
        return float(blob.get(rc) or 0)
    except (TypeError, ValueError):
        return 0.0


def _point_matches_class(road_class: str | None, rc: str) -> bool:
    raw = (road_class or "").strip().lower()
    if rc == "other":
        return raw in ("", "other")
    return raw == rc


def _fmt_share_pct(part: float, whole: float) -> float | None:
    if whole <= 0 or part <= 0:
        return None
    return round((part / whole) * 100.0, 4)


@api_bp.route("/admin-dashboard/km-coverage/<road_class>")
@login_required
def admin_dashboard_km_coverage_class(road_class: str):
    """Per-road breakdown within a class (NH/SH/MDR/local), with videographer shares."""
    if not current_user.can_manage_field_ops():
        return jsonify({"error": "forbidden"}), 403

    rc = (road_class or "").strip().lower()
    if rc not in ("nh", "sh", "mdr", "other"):
        return jsonify({"error": "road_class must be nh|sh|mdr|other"}), 400

    r, err = _drilldown_range_or_error()
    if err:
        return err

    start, end = _parse_range()
    try:
        page = max(1, int(request.args.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    page_size = PAGE_SIZE

    from routes.tracking.store import _haversine_km

    day_by_class = _sum_covered_by_class(start, end)
    class_total = float(day_by_class.get(rc) or 0)
    day_total = round(sum(day_by_class.values()), 2)

    date_clause, date_params = _ts_track_clause("ts")
    with db_utils.db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT ts.id, ts.user_id, ts.username, ts.full_name,
                   ts.state_id, ts.district_id, ts.covered_km, ts.covered_by_class,
                   tp.lat, tp.lon, tp.delta_km, tp.road_class, tp.id
            FROM tracking_sessions ts
            LEFT JOIN tracking_trail_points tp ON tp.session_id = ts.id
            WHERE 1=1 {date_clause}
            ORDER BY ts.id, tp.id
            """,
            date_params,
        )
        rows = cur.fetchall()

    # Group trail rows by session
    sessions: dict[int, dict] = {}
    for (
        sid, user_id, username, full_name, state_id, district_id,
        covered_km, cbc, lat, lon, delta_km, road_class, _tp_id,
    ) in rows:
        s = sessions.get(sid)
        if not s:
            s = {
                "user_id": int(user_id),
                "username": (username or "").strip() or f"user{user_id}",
                "full_name": (full_name or "").strip() or (username or f"user{user_id}"),
                "state_id": state_id,
                "district_id": district_id,
                "class_km": _session_class_km(cbc, covered_km, rc),
                "points": [],
            }
            sessions[sid] = s
        if lat is None or lon is None:
            continue
        try:
            s["points"].append({
                "lat": float(lat),
                "lon": float(lon),
                "delta_km": float(delta_km or 0),
                "road_class": road_class,
            })
        except (TypeError, ValueError):
            continue

    buckets: dict[str, dict] = {}
    snap_cache: dict[str, dict] = {}
    MAX_JUMP_KM = 1.5

    def _snap(lat_f, lon_f, state_id, district_id):
        # ~100 m grid — enough for road naming, fewer GIS/geocode hits
        cache_key = f"{round(lat_f, 3)},{round(lon_f, 3)},{district_id}"
        if cache_key not in snap_cache:
            snap_cache[cache_key] = _snap_trail_point(lat_f, lon_f, state_id, district_id)
        return snap_cache[cache_key]

    def _road_key(snap, lat_f, lon_f):
        ref_norm = _normalize_road_ref(snap.get("ref"))
        name = (snap.get("name") or "").strip()
        if rc in ("nh", "sh", "mdr") and ref_norm:
            return f"ref:{ref_norm}", ref_norm, (name or ref_norm)
        if name:
            return f"name:{name.lower()}", (ref_norm or ""), name
        return f"pt:{round(lat_f, 3)},{round(lon_f, 3)}", "", f"{lat_f:.4f}, {lon_f:.4f}"

    def _ensure_bucket(key, display_ref, display_name, snap, lat_f, lon_f):
        b = buckets.get(key)
        if not b:
            b = {
                "key": key,
                "ref": display_ref,
                "name": display_name,
                "covered_km": 0.0,
                "state_key": snap.get("state_key"),
                "district_id": snap.get("district_id"),
                "district_name": snap.get("district_name") or "",
                "sample_lat": lat_f,
                "sample_lon": lon_f,
                "by_user": {},
            }
            buckets[key] = b
        return b

    def _add_contrib(b, session, dkm):
        if dkm <= 0:
            return
        b["covered_km"] += dkm
        uid = session["user_id"]
        u = b["by_user"].get(uid)
        if not u:
            u = {
                "user_id": uid,
                "username": session["username"],
                "full_name": session["full_name"],
                "covered_km": 0.0,
            }
            b["by_user"][uid] = u
        u["covered_km"] += dkm

    for session in sessions.values():
        target = float(session["class_km"] or 0)
        # Stay aligned with class KPI: only sessions that contributed to this class
        if target <= 0:
            continue

        pts = session["points"]
        has_stored = any(float(p.get("delta_km") or 0) > 0 for p in pts)

        # Effective class segments: (lat, lon, dkm)
        segments: list[tuple[float, float, float]] = []
        if has_stored:
            for p in pts:
                dkm = float(p.get("delta_km") or 0)
                if dkm <= 0 or not _point_matches_class(p.get("road_class"), rc):
                    continue
                segments.append((p["lat"], p["lon"], dkm))
        else:
            # Trail often stores delta_km=0; rebuild path distance between consecutive points.
            prev = None
            for p in pts:
                if not _point_matches_class(p.get("road_class"), rc):
                    prev = (p["lat"], p["lon"])
                    continue
                if prev is not None:
                    try:
                        dkm = float(_haversine_km(prev[0], prev[1], p["lat"], p["lon"]))
                    except Exception:
                        dkm = 0.0
                    if 0 < dkm <= MAX_JUMP_KM:
                        segments.append((p["lat"], p["lon"], dkm))
                prev = (p["lat"], p["lon"])

        if not segments and target > 0 and pts:
            # Still no usable path — put session class km on best snap from trail samples
            sample = pts[len(pts) // 2]
            snap = _snap(sample["lat"], sample["lon"], session["state_id"], session["district_id"])
            key, display_ref, display_name = _road_key(snap, sample["lat"], sample["lon"])
            b = _ensure_bucket(key, display_ref, display_name, snap, sample["lat"], sample["lon"])
            _add_contrib(b, session, target)
            continue

        if not segments:
            continue

        trail_sum = sum(s[2] for s in segments)
        scale = (target / trail_sum) if trail_sum > 0 else 1.0

        for lat_f, lon_f, dkm in segments:
            use_km = dkm * scale
            if use_km <= 0:
                continue
            snap = _snap(lat_f, lon_f, session["state_id"], session["district_id"])
            key, display_ref, display_name = _road_key(snap, lat_f, lon_f)
            b = _ensure_bucket(key, display_ref, display_name, snap, lat_f, lon_f)
            _add_contrib(b, session, use_km)
            if display_name and (_looks_like_coords(b.get("name")) or not b.get("name")):
                b["name"] = display_name
            if display_ref and not b.get("ref"):
                b["ref"] = display_ref

    roads = []
    for b in buckets.values():
        covered = round(float(b["covered_km"]), 3)
        if covered <= 0:
            continue
        total_km = 0.0
        sk, did = b.get("state_key"), b.get("district_id")
        if b.get("ref") and sk and did:
            total_km = _ref_total_km(sk, did, rc, _normalize_road_ref(b["ref"]))
        elif b.get("name") and sk and did and not b.get("ref"):
            total_km = _name_total_km(sk, did, rc, b["name"])

        name = b.get("name") or ""
        if rc == "other" and _looks_like_coords(name):
            try:
                place = _reverse_place(b.get("sample_lat"), b.get("sample_lon"))
                if place:
                    name = place
            except Exception:
                pass

        # Network % is often tiny for local roads — keep high precision when small
        pct_network = None
        if total_km > 0.05:
            raw = (covered / total_km) * 100.0
            pct_network = round(min(100.0, raw), 4 if raw < 0.1 else 2)

        contributors = []
        for u in b["by_user"].values():
            ukm = round(float(u["covered_km"]), 3)
            if ukm <= 0:
                continue
            contributors.append({
                "user_id": u["user_id"],
                "username": u["username"],
                "full_name": u["full_name"],
                "covered_km": round(ukm, 2),
                "share_of_day_pct": _fmt_share_pct(ukm, day_total),
                "share_of_class_pct": _fmt_share_pct(ukm, class_total),
                "share_of_road_pct": _fmt_share_pct(ukm, covered),
            })
        contributors.sort(key=lambda x: (-float(x["covered_km"]), x.get("username") or ""))

        roads.append({
            "ref": b.get("ref") or None,
            "name": name or (b.get("ref") or "Unnamed road"),
            "number": b.get("ref") or None,
            "covered_km": round(covered, 2),
            "total_km": round(total_km, 2) if total_km > 0 else None,
            "pct_covered": pct_network,  # of GIS road length (may be tiny)
            "share_of_day_pct": _fmt_share_pct(covered, day_total),
            "share_of_class_pct": _fmt_share_pct(covered, class_total),
            "district_name": b.get("district_name") or None,
            "contributors": contributors,
        })

    roads.sort(key=lambda x: (-float(x["covered_km"]), x.get("ref") or "", x.get("name") or ""))
    total = len(roads)
    page_count = max(1, (total + page_size - 1) // page_size) if total else 1
    page = min(page, page_count)
    offset = (page - 1) * page_size
    slice_rows = roads[offset: offset + page_size]

    return jsonify({
        "range": r,
        "road_class": rc,
        "label": _CLASS_LABELS[rc],
        "covered_km": class_total,
        "day_total_km": day_total,
        "items": slice_rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "page_count": page_count,
    })
