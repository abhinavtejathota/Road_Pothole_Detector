"""API routes: vg_details CRUD + active-members drill-down."""
from __future__ import annotations

from datetime import timedelta

from flask import jsonify, request
from flask_login import login_required, current_user

import db_utils
from routes.api import api_bp
from routes.api._helpers import _serialize
from routes.api.admin_dashboard import PAGE_SIZE, _parse_range


def _compute_rating(*, day_videos: int, days_active_14: int, videos_14: int) -> float:
    """1.0–5.0 rating from same-day activity + 14-day regularity."""
    day_videos = max(0, int(day_videos or 0))
    days_active_14 = max(0, min(14, int(days_active_14 or 0)))
    videos_14 = max(0, int(videos_14 or 0))
    day_component = min(1.0, day_videos / 4.0)           # 4+ videos that day = full
    reg_component = days_active_14 / 14.0                 # distinct active days / 14
    vol_component = min(1.0, videos_14 / 28.0)            # ~2 videos/day over 14 days
    score = 0.50 * day_component + 0.35 * reg_component + 0.15 * vol_component
    return round(1.0 + 4.0 * score, 1)


@api_bp.route("/vg-details", methods=["GET"])
@login_required
def vg_details_list():
    """All videographers with optional vg_details join (field Admin / DevAdmin)."""
    if not current_user.can_manage_field_ops():
        return jsonify({"error": "forbidden"}), 403

    with db_utils.db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT u.id, u.username, u.full_name, u.email, u.state_id, u.district_id,
                   u.is_active,
                   d.display_name, d.mobile, d.village, d.notes,
                   TO_CHAR(d.updated_at AT TIME ZONE 'Asia/Kolkata',
                           'YYYY-MM-DD HH24:MI') AS details_updated_at,
                   CASE WHEN d.user_id IS NOT NULL THEN TRUE ELSE FALSE END AS has_details
            FROM users u
            LEFT JOIN vg_details d ON d.user_id = u.id
            WHERE u.role = 'Videographer'
            ORDER BY COALESCE(NULLIF(TRIM(d.display_name), ''), NULLIF(TRIM(u.full_name), ''), u.username)
            """
        )
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    return jsonify(_serialize({"videographers": rows}))


@api_bp.route("/vg-details", methods=["POST"])
@login_required
def vg_details_create():
    """Create a new Videographer user + vg_details (field Admin / DevAdmin)."""
    if not current_user.can_manage_field_ops():
        return jsonify({"error": "forbidden"}), 403

    data = request.get_json(silent=True) or {}
    display_name = (data.get("display_name") or data.get("full_name") or "").strip()
    mobile = (data.get("mobile") or "").strip()
    village = (data.get("village") or "").strip()
    notes = (data.get("notes") or "").strip() or None
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    try:
        state_id = int(data["state_id"]) if data.get("state_id") not in (None, "") else 1
    except (TypeError, ValueError):
        state_id = 1

    if not display_name:
        return jsonify({"error": "videographer name is required"}), 400
    if not mobile:
        return jsonify({"error": "mobile is required"}), 400
    if not village:
        return jsonify({"error": "village is required"}), 400

    import re
    import secrets
    from werkzeug.security import generate_password_hash

    if not username:
        base = re.sub(r"[^a-z0-9]+", "", display_name.lower())[:12] or "vg"
        digits = re.sub(r"\D", "", mobile)[-4:] or secrets.token_hex(2)
        username = f"{base}{digits}"
    if len(password) < 8:
        password = secrets.token_urlsafe(10)

    # Ensure unique username
    candidate = username
    for i in range(20):
        with db_utils.db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM users WHERE username = %s LIMIT 1", (candidate,))
            exists = cur.fetchone()
        if not exists:
            username = candidate
            break
        candidate = f"{username}{i + 1}"
    else:
        return jsonify({"error": "Could not allocate a unique username"}), 400

    try:
        uid = db_utils.create_user(
            username=username,
            password_hash=generate_password_hash(password),
            full_name=display_name,
            email=(data.get("email") or "").strip() or f"{username}@smartroad.local",
            role="Videographer",
            vendor_id=None,
            state_id=state_id,
            district_id=None,
            district_ids=None,
        )
    except Exception as e:
        return jsonify({"error": f"Could not create user: {e}"}), 400

    with db_utils.db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO vg_details (user_id, display_name, mobile, village, notes, updated_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                mobile = EXCLUDED.mobile,
                village = EXCLUDED.village,
                notes = EXCLUDED.notes,
                updated_at = NOW()
            """,
            (int(uid), display_name, mobile, village, notes),
        )
        conn.commit()

    return jsonify({
        "ok": True,
        "user_id": int(uid),
        "username": username,
        "password": password,
        "display_name": display_name,
        "mobile": mobile,
        "village": village,
        "role": "Videographer",
    }), 201


@api_bp.route("/vg-details/<int:user_id>", methods=["PUT", "POST"])
@login_required
def vg_details_upsert(user_id: int):
    """Create/update vg_details for an existing Videographer user."""
    if not current_user.can_manage_field_ops():
        return jsonify({"error": "forbidden"}), 403

    data = request.get_json(silent=True) or {}
    mobile = (data.get("mobile") or "").strip()
    village = (data.get("village") or "").strip()
    display_name = (data.get("display_name") or "").strip() or None
    notes = (data.get("notes") or "").strip() or None

    if not mobile:
        return jsonify({"error": "mobile is required"}), 400
    if not village:
        return jsonify({"error": "village is required"}), 400

    with db_utils.db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, role, full_name FROM users WHERE id = %s",
            (int(user_id),),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "user not found"}), 404
        if row[1] != "Videographer":
            return jsonify({"error": "user is not a Videographer"}), 400

        if not display_name:
            display_name = (row[2] or "").strip() or None

        cur.execute(
            """
            INSERT INTO vg_details (user_id, display_name, mobile, village, notes, updated_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                mobile = EXCLUDED.mobile,
                village = EXCLUDED.village,
                notes = EXCLUDED.notes,
                updated_at = NOW()
            RETURNING user_id, display_name, mobile, village, notes
            """,
            (int(user_id), display_name, mobile, village, notes),
        )
        out = cur.fetchone()
        conn.commit()

    return jsonify({
        "ok": True,
        "user_id": out[0],
        "display_name": out[1],
        "mobile": out[2],
        "village": out[3],
        "notes": out[4],
    })


@api_bp.route("/admin-dashboard/members")
@login_required
def admin_dashboard_members():
    """Members who started capture in the selected range — 10 / page.

    Status ``Active`` = uploaded ≥1 video in range; otherwise ``Capturing``.
    """
    if not current_user.can_manage_field_ops():
        return jsonify({"error": "forbidden"}), 403

    from routes.api.admin_dashboard import _drilldown_range_or_error, _ts_track_clause, _vs_processed_clause

    r, err = _drilldown_range_or_error()
    if err:
        return err

    start, end = _parse_range()
    try:
        page = max(1, int(request.args.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    page_size = PAGE_SIZE
    offset = (page - 1) * page_size

    today = end or start
    if today is None:
        from routes.api.admin_dashboard import _today_ist
        today = _today_ist()
    window_start = (start or today) - timedelta(days=13)
    window_end = end or today

    ts_clause, ts_params = _ts_track_clause("ts")
    vs_clause, vs_params = _vs_processed_clause("vs")

    with db_utils.db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT COUNT(DISTINCT ts.user_id)
            FROM tracking_sessions ts
            WHERE (
                ts.last_ts IS NOT NULL
                OR ts.recording = TRUE
                OR COALESCE(ts.covered_km, 0) > 0
                OR EXISTS (
                    SELECT 1 FROM tracking_trail_points tp
                    WHERE tp.session_id = ts.id
                    LIMIT 1
                )
              )
              {ts_clause}
            """,
            ts_params,
        )
        total = int(cur.fetchone()[0] or 0)

        cur.execute(
            f"""
            WITH capturing AS (
                SELECT ts.user_id,
                       BOOL_OR(ts.recording) AS is_recording,
                       COALESCE(SUM(ts.covered_km), 0) AS covered_km,
                       MAX(ts.last_ts) AS last_ts
                FROM tracking_sessions ts
                WHERE (
                    ts.last_ts IS NOT NULL
                    OR ts.recording = TRUE
                    OR COALESCE(ts.covered_km, 0) > 0
                    OR EXISTS (
                        SELECT 1 FROM tracking_trail_points tp
                        WHERE tp.session_id = ts.id
                        LIMIT 1
                    )
                  )
                  {ts_clause}
                GROUP BY ts.user_id
            ),
            day_uploads AS (
                SELECT vs.user_id,
                       COUNT(*) AS day_videos
                FROM video_sessions vs
                WHERE vs.user_id IS NOT NULL
                  {vs_clause}
                GROUP BY vs.user_id
            ),
            hist_capture AS (
                SELECT ts.user_id,
                       COUNT(DISTINCT ts.track_date) AS days_active_14
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
                GROUP BY ts.user_id
            ),
            hist_uploads AS (
                SELECT vs.user_id,
                       COUNT(*) AS videos_14
                FROM video_sessions vs
                WHERE vs.user_id IS NOT NULL
                  AND (vs.processed_at AT TIME ZONE 'Asia/Kolkata')::date BETWEEN %s AND %s
                GROUP BY vs.user_id
            )
            SELECT c.user_id,
                   u.username,
                   u.full_name,
                   d.display_name,
                   d.mobile,
                   d.village,
                   COALESCE(du.day_videos, 0) AS day_videos,
                   COALESCE(hu.videos_14, 0) AS videos_14,
                   COALESCE(hc.days_active_14, 1) AS days_active_14,
                   c.is_recording,
                   c.covered_km
            FROM capturing c
            JOIN users u ON u.id = c.user_id
            LEFT JOIN vg_details d ON d.user_id = c.user_id
            LEFT JOIN day_uploads du ON du.user_id = c.user_id
            LEFT JOIN hist_capture hc ON hc.user_id = c.user_id
            LEFT JOIN hist_uploads hu ON hu.user_id = c.user_id
            ORDER BY COALESCE(du.day_videos, 0) DESC,
                     c.covered_km DESC,
                     COALESCE(d.display_name, u.full_name, u.username)
            LIMIT %s OFFSET %s
            """,
            [
                *ts_params,          # capturing
                *vs_params,          # day_uploads
                window_start, window_end,   # hist_capture
                window_start, window_end,   # hist_uploads
                page_size, offset,
            ],
        )
        rows = cur.fetchall()

    items = []
    for row in rows:
        (
            uid, username, full_name, display_name, mobile, village,
            day_videos, videos_14, days_active_14, is_recording, covered_km,
        ) = row
        name = (display_name or full_name or username or f"User {uid}").strip()
        day_videos = int(day_videos or 0)
        # Status: Active only after upload; otherwise still capturing in the field
        status = "Active" if day_videos > 0 else "Capturing"
        rating = _compute_rating(
            day_videos=day_videos,
            days_active_14=int(days_active_14 or 0),
            videos_14=int(videos_14 or 0),
        )
        items.append({
            "user_id": int(uid),
            "name": name,
            "username": username,
            "mobile": mobile or "—",
            "village": village or "—",
            "status": status,
            "rating": rating,
            "day_videos": day_videos,
            "days_active_14": int(days_active_14 or 0),
            "videos_14": int(videos_14 or 0),
            "is_recording": bool(is_recording),
            "covered_km": round(float(covered_km or 0), 2),
            "has_details": bool(mobile and village),
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
