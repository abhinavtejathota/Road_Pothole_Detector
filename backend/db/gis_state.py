from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
import json
import math
import os
import time

from db.connection import _get_conn, is_db_configured
try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None

# ── Survey + tracking state (Postgres dual-write with JSON files) ─────────────

def _polyline_to_linestring_ewkt(polyline) -> str | None:
    """Convert [[lat, lon], ...] to EWKT LINESTRING. None if < 2 valid points."""
    if not isinstance(polyline, list) or len(polyline) < 2:
        return None
    parts = []
    for p in polyline:
        try:
            if isinstance(p, dict):
                lat, lon = float(p["lat"]), float(p["lon"])
            else:
                lat, lon = float(p[0]), float(p[1])
        except (TypeError, ValueError, KeyError, IndexError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        parts.append(f"{lon} {lat}")
    if len(parts) < 2:
        return None
    return f"SRID=4326;LINESTRING({','.join(parts)})"


def _json_field(val, default):
    if val is None:
        return default
    if isinstance(val, dict):
        return val
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        s = val.strip()
        if not s or s in ("null", "None"):
            return default
        import json as _json
        try:
            return _json.loads(s)
        except Exception:
            return default
    return val


def survey_db_load_state() -> dict | None:
    """Load survey state in the same shape as survey_state.json. None if DB unavailable."""
    if not is_db_configured():
        return None
    try:
        conn = _get_conn()
    except Exception:
        return None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT daily_km, focus_road_class FROM survey_settings WHERE id = 1")
            settings_row = cur.fetchone()
            settings = {
                "daily_km": float(settings_row["daily_km"]) if settings_row else 100,
                "focus_road_class": (settings_row["focus_road_class"] if settings_row else "all") or "all",
            }

            cur.execute("SELECT segment_id, status FROM survey_segment_status")
            segment_status = {r["segment_id"]: r["status"] for r in cur.fetchall()}

            # Prefer full column set; fall back for partially migrated DBs
            cols_full = """
                assignment_date, user_id, state_key, state_id, district_id, district_name,
                segment_ids, start_lat, start_lon, start_label, end_lat, end_lon, end_label,
                corridor_km, covered_km, covered_by_class, legs, created_at,
                polyline, route_km, continuous, preferred_km, connector_km, mode, meta
            """
            cols_mid = """
                assignment_date, user_id, state_key, state_id, district_id, district_name,
                segment_ids, start_lat, start_lon, start_label, end_lat, end_lon, end_label,
                corridor_km, covered_km, covered_by_class, legs, created_at,
                polyline, route_km, continuous
            """
            cols_base = """
                assignment_date, user_id, state_key, state_id, district_id, district_name,
                segment_ids, start_lat, start_lon, start_label, end_lat, end_lon, end_label,
                corridor_km, covered_km, covered_by_class, legs, created_at
            """
            level = "full"
            try:
                cur.execute(f"SELECT {cols_full} FROM survey_daily_assignments ORDER BY assignment_date, user_id")
            except Exception:
                conn.rollback()
                level = "mid"
                try:
                    cur.execute(f"SELECT {cols_mid} FROM survey_daily_assignments ORDER BY assignment_date, user_id")
                except Exception:
                    conn.rollback()
                    level = "base"
                    cur.execute(f"SELECT {cols_base} FROM survey_daily_assignments ORDER BY assignment_date, user_id")

            daily = {}
            for r in cur.fetchall():
                date = r["assignment_date"].isoformat() if hasattr(r["assignment_date"], "isoformat") else str(r["assignment_date"])
                day = daily.setdefault(date, {})
                start = None
                if r["start_lat"] is not None and r["start_lon"] is not None:
                    start = {"lat": float(r["start_lat"]), "lon": float(r["start_lon"]), "label": r["start_label"] or ""}
                end = None
                if r["end_lat"] is not None and r["end_lon"] is not None:
                    end = {"lat": float(r["end_lat"]), "lon": float(r["end_lon"]), "label": r["end_label"] or ""}
                entry = {
                    "user_id": int(r["user_id"]),
                    "district_id": str(r["district_id"]) if r["district_id"] is not None else None,
                    "district_name": r["district_name"],
                    "state_key": r["state_key"],
                    "state_id": r["state_id"],
                    "segment_ids": list(r["segment_ids"] or []),
                    "legs": list(_json_field(r.get("legs"), []) or []),
                    "start": start,
                    "end": end,
                    "corridor_km": float(r["corridor_km"]) if r["corridor_km"] is not None else None,
                    "covered_km": float(r["covered_km"] or 0),
                    "covered_by_class": _json_field(r.get("covered_by_class"), {}) or {},
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                    "continuous": True,
                    "preferred_km": 0.0,
                    "connector_km": 0.0,
                    "mode": "corridor",
                    "meta": {},
                }
                if level in ("mid", "full"):
                    poly = _json_field(r.get("polyline"), []) or []
                    if isinstance(poly, list) and len(poly) >= 2:
                        entry["polyline"] = poly
                    if r.get("route_km") is not None:
                        entry["route_km"] = float(r["route_km"])
                    if r.get("continuous") is not None:
                        entry["continuous"] = bool(r["continuous"])
                if level == "full":
                    if r.get("preferred_km") is not None:
                        entry["preferred_km"] = float(r["preferred_km"] or 0)
                    if r.get("connector_km") is not None:
                        entry["connector_km"] = float(r["connector_km"] or 0)
                    if r.get("mode"):
                        entry["mode"] = str(r["mode"])
                    entry["meta"] = _json_field(r.get("meta"), {}) or {}
                day[f"u{int(r['user_id'])}"] = entry

            return {
                "settings": settings,
                "segment_status": segment_status,
                "daily_assignments": daily,
            }
    except Exception:
        return None
    finally:
        conn.close()


def survey_db_save_state(state: dict) -> bool:
    """Persist full survey state dict to Postgres. Returns False if DB unavailable."""
    if not is_db_configured():
        return False
    try:
        conn = _get_conn()
    except Exception:
        return False
    try:
        settings = state.get("settings") or {}
        daily_km = float(settings.get("daily_km", 100))
        focus = str(settings.get("focus_road_class") or "all")
        segment_status = state.get("segment_status") or {}
        daily = state.get("daily_assignments") or {}

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO survey_settings (id, daily_km, focus_road_class, updated_at)
                VALUES (1, %s, %s, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    daily_km = EXCLUDED.daily_km,
                    focus_road_class = EXCLUDED.focus_road_class,
                    updated_at = NOW()
            """, (daily_km, focus))

            cur.execute("DELETE FROM survey_segment_status")
            if segment_status:
                psycopg2.extras.execute_batch(
                    cur,
                    """
                    INSERT INTO survey_segment_status (segment_id, status, updated_at)
                    VALUES (%s, %s, NOW())
                    """,
                    [(str(sid), str(status)) for sid, status in segment_status.items()],
                    page_size=500,
                )

            keep_set = set()
            for date, day in daily.items():
                if not isinstance(day, dict):
                    continue
                for ukey, entry in day.items():
                    if not isinstance(entry, dict):
                        continue
                    # Corridor needs segments; custom auto_track is start/end only (empty segment_ids)
                    mode_raw = str(entry.get("mode") or "corridor").lower()
                    segs = entry.get("segment_ids") or []
                    start = entry.get("start") or {}
                    end = entry.get("end") or {}
                    has_endpoints = (
                        start.get("lat") is not None and start.get("lon") is not None
                        and end.get("lat") is not None and end.get("lon") is not None
                    )
                    if not segs and not (mode_raw == "auto_track" and has_endpoints):
                        continue
                    try:
                        user_id = int(entry.get("user_id") or str(ukey).lstrip("u"))
                    except (TypeError, ValueError):
                        continue
                    date_s = str(date)
                    keep_set.add((date_s, user_id))
                    slat, slon = start.get("lat"), start.get("lon")
                    elat, elon = end.get("lat"), end.get("lon")
                    did = entry.get("district_id")
                    try:
                        did_int = int(did) if did is not None and str(did).strip() != "" else None
                    except (TypeError, ValueError):
                        did_int = None
                    cbc = psycopg2.extras.Json(entry.get("covered_by_class") or {})
                    legs_json = psycopg2.extras.Json(entry.get("legs") or [])
                    poly = entry.get("polyline") or []
                    # Ensure auto_track always has at least start→end for the map
                    if (not isinstance(poly, list) or len(poly) < 2) and has_endpoints:
                        poly = [
                            [float(slat), float(slon)],
                            [float(elat), float(elon)],
                        ]
                    poly_json = psycopg2.extras.Json(poly if isinstance(poly, list) else [])
                    meta_json = psycopg2.extras.Json(entry.get("meta") or {})
                    route_km = entry.get("route_km")
                    try:
                        route_km_f = float(route_km) if route_km is not None else None
                    except (TypeError, ValueError):
                        route_km_f = None
                    continuous = bool(entry.get("continuous", True))
                    if mode_raw == "auto_track":
                        continuous = False
                    try:
                        preferred_km = float(entry.get("preferred_km") or 0)
                    except (TypeError, ValueError):
                        preferred_km = 0.0
                    try:
                        connector_km = float(entry.get("connector_km") or 0)
                    except (TypeError, ValueError):
                        connector_km = 0.0
                    mode = mode_raw if mode_raw in ("corridor", "nearest", "manual", "auto_track") else "corridor"
                    route_ewkt = _polyline_to_linestring_ewkt(poly if isinstance(poly, list) else [])

                    cur.execute("""
                        INSERT INTO survey_daily_assignments (
                            assignment_date, user_id, state_key, state_id, district_id, district_name,
                            segment_ids, start_lat, start_lon, start_label, end_lat, end_lon, end_label,
                            start_geom, end_geom, corridor_km, covered_km, covered_by_class, legs,
                            polyline, route_km, route_geom, continuous,
                            preferred_km, connector_km, mode, meta, updated_at
                        ) VALUES (
                            %s::date, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s,
                            CASE WHEN %s IS NOT NULL AND %s IS NOT NULL
                                 THEN ST_SetSRID(ST_MakePoint(%s, %s), 4326) END,
                            CASE WHEN %s IS NOT NULL AND %s IS NOT NULL
                                 THEN ST_SetSRID(ST_MakePoint(%s, %s), 4326) END,
                            %s, %s, %s, %s,
                            %s, %s,
                            CASE WHEN %s IS NOT NULL THEN ST_GeomFromEWKT(%s) END,
                            %s, %s, %s, %s, %s, NOW()
                        )
                        ON CONFLICT (assignment_date, user_id) DO UPDATE SET
                            state_key = EXCLUDED.state_key,
                            state_id = EXCLUDED.state_id,
                            district_id = EXCLUDED.district_id,
                            district_name = EXCLUDED.district_name,
                            segment_ids = EXCLUDED.segment_ids,
                            start_lat = EXCLUDED.start_lat,
                            start_lon = EXCLUDED.start_lon,
                            start_label = EXCLUDED.start_label,
                            end_lat = EXCLUDED.end_lat,
                            end_lon = EXCLUDED.end_lon,
                            end_label = EXCLUDED.end_label,
                            start_geom = EXCLUDED.start_geom,
                            end_geom = EXCLUDED.end_geom,
                            corridor_km = EXCLUDED.corridor_km,
                            covered_km = EXCLUDED.covered_km,
                            covered_by_class = EXCLUDED.covered_by_class,
                            legs = EXCLUDED.legs,
                            polyline = EXCLUDED.polyline,
                            route_km = EXCLUDED.route_km,
                            route_geom = EXCLUDED.route_geom,
                            continuous = EXCLUDED.continuous,
                            preferred_km = EXCLUDED.preferred_km,
                            connector_km = EXCLUDED.connector_km,
                            mode = EXCLUDED.mode,
                            meta = EXCLUDED.meta,
                            updated_at = NOW()
                    """, (
                        date_s, user_id,
                        entry.get("state_key"), entry.get("state_id"), did_int, entry.get("district_name"),
                        list(entry.get("segment_ids") or []),
                        slat, slon, start.get("label") or "",
                        elat, elon, end.get("label") or "",
                        slon, slat, slon, slat,
                        elon, elat, elon, elat,
                        entry.get("corridor_km"),
                        float(entry.get("covered_km") or 0),
                        cbc,
                        legs_json,
                        poly_json,
                        route_km_f,
                        route_ewkt, route_ewkt,
                        continuous,
                        preferred_km,
                        connector_km,
                        mode,
                        meta_json,
                    ))

            cur.execute("SELECT assignment_date, user_id FROM survey_daily_assignments")
            for row in cur.fetchall():
                d = row[0].isoformat() if hasattr(row[0], "isoformat") else str(row[0])
                u = int(row[1])
                if (d, u) not in keep_set:
                    cur.execute(
                        "DELETE FROM survey_daily_assignments WHERE assignment_date = %s::date AND user_id = %s",
                        (d, u),
                    )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def tracking_db_list_user_dates() -> list[tuple[int, str]]:
    """All (user_id, track_date) pairs in tracking_sessions."""
    if not is_db_configured():
        return []
    try:
        conn = _get_conn()
    except Exception:
        return []
    try:
        with conn.cursor() as cur:
            # Column is track_date (schema.sql) — session_date does not exist and
            # previously made this always fail → dashboard fell back to "today only"
            # and showed 0 km while Tracking (date-filtered) still showed coverage.
            cur.execute("""
                SELECT user_id, track_date::text
                FROM tracking_sessions
                ORDER BY track_date, user_id
            """)
            out = []
            for uid, d in cur.fetchall():
                out.append((int(uid), str(d)[:10]))
            return out
    except Exception:
        return []
    finally:
        conn.close()


def tracking_db_aggregate_overall_coverage() -> dict | None:
    """
    Sum covered_km / covered_by_class across ALL tracking_sessions (all dates).
    Returns {by_user: {uid: {covered_km, by_class, state_id}}, totals...} or None.
    """
    if not is_db_configured():
        return None
    try:
        conn = _get_conn()
    except Exception:
        return None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT user_id, state_id,
                       COALESCE(SUM(covered_km), 0) AS covered_km,
                       COALESCE(
                         jsonb_agg(covered_by_class) FILTER (WHERE covered_by_class IS NOT NULL),
                         '[]'::jsonb
                       ) AS class_parts
                FROM tracking_sessions
                GROUP BY user_id, state_id
            """)
            by_user = {}
            total = 0.0
            by_class = {"nh": 0.0, "sh": 0.0, "mdr": 0.0, "other": 0.0}
            for row in cur.fetchall():
                uid = str(int(row["user_id"]))
                cbc = {"nh": 0.0, "sh": 0.0, "mdr": 0.0, "other": 0.0}
                parts = row.get("class_parts") or []
                if isinstance(parts, str):
                    s = parts.strip()
                    if not s:
                        parts = []
                    else:
                        try:
                            import json as _json
                            parts = _json.loads(s)
                        except Exception:
                            parts = []
                if not isinstance(parts, list):
                    parts = []
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    for k in cbc:
                        cbc[k] += float(part.get(k) or 0)
                km = float(row["covered_km"] or 0)
                # Same user can appear with different state_id historically — merge
                prev = by_user.get(uid)
                if prev:
                    prev["covered_km"] += km
                    for k in cbc:
                        prev["by_class"][k] += cbc[k]
                    if row.get("state_id") is not None:
                        prev["state_id"] = row["state_id"]
                else:
                    by_user[uid] = {
                        "covered_km": km,
                        "by_class": cbc,
                        "state_id": row.get("state_id"),
                    }
                total += km
                for k in by_class:
                    by_class[k] += cbc[k]
            return {
                "by_user": by_user,
                "covered_km": round(total, 3),
                "by_class": {k: round(v, 3) for k, v in by_class.items()},
                "n_users_with_gps": sum(1 for v in by_user.values() if v["covered_km"] > 0.01),
            }
    except Exception:
        return None
    finally:
        conn.close()


def _safe_json_dict(val, default=None):
    """Parse JSONB/text that may be empty or already a dict — never raise."""
    if default is None:
        default = {}
    if val is None:
        return dict(default)
    if isinstance(val, dict):
        return val
    if isinstance(val, (bytes, bytearray)):
        try:
            val = val.decode("utf-8")
        except Exception:
            return dict(default)
    if isinstance(val, str):
        s = val.strip()
        if not s or s in ("null", "None"):
            return dict(default)
        try:
            import json as _json
            out = _json.loads(s)
            return out if isinstance(out, dict) else dict(default)
        except Exception:
            return dict(default)
    return dict(default)


def coverage_db_persist_both(
    user_id: int,
    date: str,
    covered_km: float,
    covered_by_class: dict | None = None,
) -> bool:
    """One connection: update tracking_sessions + survey_daily_assignments together."""
    if not is_db_configured() or not date:
        return False
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return False
    try:
        conn = _get_conn()
    except Exception:
        return False
    try:
        cbc = psycopg2.extras.Json(_safe_json_dict(covered_by_class))
        km = float(covered_km or 0)
        day = str(date)[:10]
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE tracking_sessions
                SET covered_km = %s,
                    covered_by_class = %s,
                    updated_at = NOW()
                WHERE user_id = %s AND track_date = %s::date
                """,
                (km, cbc, uid, day),
            )
            if cur.rowcount == 0:
                cur.execute(
                    """
                    INSERT INTO tracking_sessions (
                        user_id, track_date, covered_km, covered_by_class, updated_at
                    ) VALUES (%s, %s::date, %s, %s, NOW())
                    ON CONFLICT (user_id, track_date) DO UPDATE SET
                        covered_km = EXCLUDED.covered_km,
                        covered_by_class = EXCLUDED.covered_by_class,
                        updated_at = NOW()
                    """,
                    (uid, day, km, cbc),
                )
            cur.execute(
                """
                UPDATE survey_daily_assignments
                SET covered_km = %s,
                    covered_by_class = %s,
                    updated_at = NOW()
                WHERE user_id = %s AND assignment_date = %s::date
                """,
                (km, cbc, uid, day),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def survey_db_list_user_dates() -> list[tuple[int, str]]:
    """All (user_id, assignment_date) pairs in survey_daily_assignments."""
    if not is_db_configured():
        return []
    try:
        conn = _get_conn()
    except Exception:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT user_id, assignment_date::text
                FROM survey_daily_assignments
                ORDER BY assignment_date, user_id
            """)
            return [(int(uid), str(d)[:10]) for uid, d in cur.fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


def _survey_assignment_row_to_entry(r: dict, *, level: str = "full") -> dict:
    """Normalize one survey_daily_assignments row into the in-memory entry shape."""
    start = None
    if r.get("start_lat") is not None and r.get("start_lon") is not None:
        start = {
            "lat": float(r["start_lat"]),
            "lon": float(r["start_lon"]),
            "label": r.get("start_label") or "",
        }
    end = None
    if r.get("end_lat") is not None and r.get("end_lon") is not None:
        end = {
            "lat": float(r["end_lat"]),
            "lon": float(r["end_lon"]),
            "label": r.get("end_label") or "",
        }
    entry = {
        "user_id": int(r["user_id"]),
        "district_id": str(r["district_id"]) if r.get("district_id") is not None else None,
        "district_name": r.get("district_name"),
        "state_key": r.get("state_key"),
        "state_id": r.get("state_id"),
        "segment_ids": list(r.get("segment_ids") or []),
        "legs": list(_json_field(r.get("legs"), []) or []),
        "start": start,
        "end": end,
        "corridor_km": float(r["corridor_km"]) if r.get("corridor_km") is not None else None,
        "covered_km": float(r.get("covered_km") or 0),
        "covered_by_class": _json_field(r.get("covered_by_class"), {}) or {},
        "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        "continuous": True,
        "preferred_km": 0.0,
        "connector_km": 0.0,
        "mode": "corridor",
        "meta": {},
    }
    if level in ("mid", "full"):
        poly = _json_field(r.get("polyline"), []) or []
        if isinstance(poly, list) and len(poly) >= 2:
            entry["polyline"] = poly
        if r.get("route_km") is not None:
            entry["route_km"] = float(r["route_km"])
        if r.get("continuous") is not None:
            entry["continuous"] = bool(r["continuous"])
    if level == "full":
        if r.get("preferred_km") is not None:
            entry["preferred_km"] = float(r["preferred_km"] or 0)
        if r.get("connector_km") is not None:
            entry["connector_km"] = float(r["connector_km"] or 0)
        if r.get("mode"):
            entry["mode"] = str(r["mode"])
        entry["meta"] = _json_field(r.get("meta"), {}) or {}
    return entry


def survey_db_get_assignment(user_id: int, date: str) -> dict | None:
    """Load one day's assignment for a user from Postgres (source of truth)."""
    if not is_db_configured() or not date:
        return None
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return None
    try:
        conn = _get_conn()
    except Exception:
        return None
    cols_full = """
        assignment_date, user_id, state_key, state_id, district_id, district_name,
        segment_ids, start_lat, start_lon, start_label, end_lat, end_lon, end_label,
        corridor_km, covered_km, covered_by_class, legs, created_at,
        polyline, route_km, continuous, preferred_km, connector_km, mode, meta
    """
    cols_mid = """
        assignment_date, user_id, state_key, state_id, district_id, district_name,
        segment_ids, start_lat, start_lon, start_label, end_lat, end_lon, end_label,
        corridor_km, covered_km, covered_by_class, legs, created_at,
        polyline, route_km, continuous
    """
    cols_base = """
        assignment_date, user_id, state_key, state_id, district_id, district_name,
        segment_ids, start_lat, start_lon, start_label, end_lat, end_lon, end_label,
        corridor_km, covered_km, covered_by_class, legs, created_at
    """
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for cols, level in (
                (cols_full, "full"),
                (cols_mid, "mid"),
                (cols_base, "base"),
            ):
                try:
                    cur.execute(
                        f"""
                        SELECT {cols}
                        FROM survey_daily_assignments
                        WHERE user_id = %s AND assignment_date = %s::date
                        LIMIT 1
                        """,
                        (uid, str(date)[:10]),
                    )
                    row = cur.fetchone()
                    if row:
                        return _survey_assignment_row_to_entry(dict(row), level=level)
                    return None
                except Exception:
                    conn.rollback()
                    continue
        return None
    except Exception:
        return None
    finally:
        conn.close()


def survey_db_find_assignment_near_date(
    user_id: int,
    date: str,
    *,
    days_before: int = 3,
    days_after: int = 1,
) -> tuple[str, dict] | None:
    """Nearest assignment on/before/after date (detection often runs next day)."""
    if not is_db_configured() or not date:
        return None
    try:
        uid = int(user_id)
        from datetime import date as date_cls, timedelta

        center = date_cls.fromisoformat(str(date)[:10])
    except Exception:
        return None
    try:
        conn = _get_conn()
    except Exception:
        return None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT assignment_date::text AS d
                FROM survey_daily_assignments
                WHERE user_id = %s
                  AND assignment_date BETWEEN %s::date AND %s::date
                ORDER BY ABS(assignment_date - %s::date), assignment_date DESC
                LIMIT 1
                """,
                (
                    uid,
                    (center - timedelta(days=max(0, int(days_before)))).isoformat(),
                    (center + timedelta(days=max(0, int(days_after)))).isoformat(),
                    center.isoformat(),
                ),
            )
            row = cur.fetchone()
            if not row:
                return None
            d = str(row["d"])[:10]
            entry = survey_db_get_assignment(uid, d)
            if entry:
                return d, entry
        return None
    except Exception:
        return None
    finally:
        conn.close()


def tracking_db_get_covered(user_id: int, date: str) -> tuple[float, dict]:
    """covered_km + by_class from tracking_sessions for one user/day."""
    if not is_db_configured() or not date:
        return 0.0, {}
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return 0.0, {}
    try:
        conn = _get_conn()
    except Exception:
        return 0.0, {}
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT covered_km, covered_by_class
                FROM tracking_sessions
                WHERE user_id = %s AND track_date = %s::date
                LIMIT 1
                """,
                (uid, str(date)[:10]),
            )
            row = cur.fetchone()
            if not row:
                return 0.0, {}
            return float(row["covered_km"] or 0), _safe_json_dict(row.get("covered_by_class"))
    except Exception:
        return 0.0, {}
    finally:
        conn.close()


def survey_db_update_covered(
    user_id: int,
    date: str,
    covered_km: float,
    covered_by_class: dict | None = None,
) -> bool:
    """Light UPDATE of covered_km / covered_by_class on one assignment row."""
    if not is_db_configured() or not date:
        return False
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return False
    try:
        conn = _get_conn()
    except Exception:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE survey_daily_assignments
                SET covered_km = %s,
                    covered_by_class = %s,
                    updated_at = NOW()
                WHERE user_id = %s AND assignment_date = %s::date
                """,
                (
                    float(covered_km or 0),
                    psycopg2.extras.Json(covered_by_class or {}),
                    uid,
                    str(date)[:10],
                ),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def survey_db_aggregate_covered_km() -> dict | None:
    """Sum covered_km / covered_by_class from survey_daily_assignments (canonical KPIs).

    Matches Tracking OVERALL / VG day panels — does NOT include orphan tracking
    days whose assignments were deleted (those inflated dashboard cards).
    """
    if not is_db_configured():
        return None
    try:
        conn = _get_conn()
    except Exception:
        return None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT user_id, state_id, state_key,
                       COALESCE(SUM(COALESCE(covered_km, 0)), 0) AS covered_km,
                       COALESCE(
                         jsonb_agg(covered_by_class) FILTER (WHERE covered_by_class IS NOT NULL),
                         '[]'::jsonb
                       ) AS class_parts
                FROM survey_daily_assignments
                GROUP BY user_id, state_id, state_key
            """)
            by_user = {}
            total = 0.0
            by_class = {"nh": 0.0, "sh": 0.0, "mdr": 0.0, "other": 0.0}
            for row in cur.fetchall():
                uid = str(int(row["user_id"]))
                cbc = {"nh": 0.0, "sh": 0.0, "mdr": 0.0, "other": 0.0}
                parts = row.get("class_parts") or []
                if isinstance(parts, str):
                    s = parts.strip()
                    if not s:
                        parts = []
                    else:
                        try:
                            import json as _json
                            parts = _json.loads(s)
                        except Exception:
                            parts = []
                if not isinstance(parts, list):
                    parts = []
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    for k in cbc:
                        cbc[k] += float(part.get(k) or 0)
                km = float(row["covered_km"] or 0)
                # Keep class mix aligned to this user's covered total
                class_sum = sum(cbc.values())
                if km > 0.01 and abs(class_sum - km) > 0.08:
                    if class_sum > 0.01:
                        scale = km / class_sum
                        for k in cbc:
                            cbc[k] = round(cbc[k] * scale, 3)
                    else:
                        cbc = {"nh": 0.0, "sh": 0.0, "mdr": 0.0, "other": round(km, 3)}
                prev = by_user.get(uid)
                if prev:
                    prev["covered_km"] += km
                    for k in cbc:
                        prev["by_class"][k] += cbc[k]
                    if row.get("state_id") is not None:
                        prev["state_id"] = row["state_id"]
                    if row.get("state_key"):
                        prev["state_key"] = row["state_key"]
                else:
                    by_user[uid] = {
                        "covered_km": km,
                        "by_class": cbc,
                        "state_id": row.get("state_id"),
                        "state_key": row.get("state_key"),
                    }
                total += km
                for k in by_class:
                    by_class[k] += cbc[k]
            return {
                "by_user": by_user,
                "covered_km": round(total, 3),
                "by_class": {k: round(v, 3) for k, v in by_class.items()},
                "n_users_with_gps": sum(1 for v in by_user.values() if v["covered_km"] > 0.01),
            }
    except Exception:
        return None
    finally:
        conn.close()


def survey_db_aggregate_assigned_km() -> dict | None:
    """Sum route_km across all survey_daily_assignments (overall)."""
    if not is_db_configured():
        return None
    try:
        conn = _get_conn()
    except Exception:
        return None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT user_id, state_id, state_key,
                       COALESCE(SUM(COALESCE(route_km, corridor_km, 0)), 0) AS assigned_km,
                       COUNT(*)::int AS n_assignments
                FROM survey_daily_assignments
                GROUP BY user_id, state_id, state_key
            """)
            by_user = {}
            total = 0.0
            n_assign = 0
            for row in cur.fetchall():
                uid = str(int(row["user_id"]))
                km = float(row["assigned_km"] or 0)
                n = int(row["n_assignments"] or 0)
                prev = by_user.get(uid)
                if prev:
                    prev["assigned_km"] += km
                    prev["n_assignments"] += n
                else:
                    by_user[uid] = {
                        "assigned_km": km,
                        "n_assignments": n,
                        "state_id": row.get("state_id"),
                        "state_key": row.get("state_key"),
                    }
                total += km
                n_assign += n
            return {
                "by_user": by_user,
                "assigned_km": round(total, 3),
                "n_assignments": n_assign,
                "n_users_with_assignment": len(by_user),
            }
    except Exception:
        return None
    finally:
        conn.close()


def tracking_db_load_for_date(date: str) -> dict | None:
    """Load tracking sessions for a single calendar day (IST date string)."""
    if not is_db_configured() or not date:
        return None
    try:
        conn = _get_conn()
    except Exception:
        return None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, user_id, track_date, username, full_name, state_id, district_id,
                       recording, covered_km, covered_by_class,
                       last_lat, last_lon, last_accuracy, last_ts
                FROM tracking_sessions
                WHERE track_date = %s::date
                ORDER BY user_id
            """, (date,))
            sessions = cur.fetchall()
            vids = {}
            for s in sessions:
                uid = str(int(s["user_id"]))
                day = s["track_date"].isoformat() if hasattr(s["track_date"], "isoformat") else str(s["track_date"])
                cur.execute("""
                    SELECT lat, lon, ts, capture_session_id, committed, delta_km, road_class
                    FROM tracking_trail_points
                    WHERE session_id = %s ORDER BY id ASC
                """, (s["id"],))
                trail = []
                for p in cur.fetchall():
                    ts = p["ts"].isoformat() if p["ts"] else None
                    trail.append({
                        "lat": float(p["lat"]),
                        "lon": float(p["lon"]),
                        "ts": ts,
                        "capture_session_id": p.get("capture_session_id"),
                        "committed": True if p.get("committed") is None else bool(p["committed"]),
                        "delta_km": float(p["delta_km"] or 0),
                        "road_class": p.get("road_class"),
                    })
                last = None
                if s["last_lat"] is not None and s["last_lon"] is not None:
                    last = {
                        "lat": float(s["last_lat"]),
                        "lon": float(s["last_lon"]),
                        "accuracy": float(s["last_accuracy"]) if s["last_accuracy"] is not None else None,
                        "ts": s["last_ts"].isoformat() if s["last_ts"] else None,
                    }
                cbc = _safe_json_dict(s["covered_by_class"])
                vids[uid] = {
                    "user_id": int(s["user_id"]),
                    "username": s["username"],
                    "full_name": s["full_name"],
                    "state_id": s["state_id"],
                    "district_id": s["district_id"],
                    "date": day,
                    "trail": trail,
                    "recording": bool(s["recording"]),
                    "last": last,
                    "covered_km": float(s["covered_km"] or 0),
                    "covered_by_class": cbc,
                }
            return {"videographers": vids, "date": date}
    except Exception:
        return None
    finally:
        conn.close()


def tracking_db_load_state() -> dict | None:
    """Load tracking state in the same shape as tracking_state.json."""
    if not is_db_configured():
        return None
    try:
        conn = _get_conn()
    except Exception:
        return None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, user_id, track_date, username, full_name, state_id, district_id,
                       recording, covered_km, covered_by_class,
                       last_lat, last_lon, last_accuracy, last_ts
                FROM tracking_sessions
                ORDER BY track_date DESC, user_id
            """)
            sessions = cur.fetchall()
            vids = {}
            for s in sessions:
                # Prefer today's sessions when multiple dates exist — keep latest date per user
                uid = str(int(s["user_id"]))
                date = s["track_date"].isoformat() if hasattr(s["track_date"], "isoformat") else str(s["track_date"])
                if uid in vids and vids[uid].get("date") > date:
                    continue
                cur.execute("""
                    SELECT lat, lon, ts, capture_session_id, committed, delta_km, road_class
                    FROM tracking_trail_points
                    WHERE session_id = %s ORDER BY id ASC
                """, (s["id"],))
                trail = []
                for p in cur.fetchall():
                    ts = p["ts"].isoformat() if p["ts"] else None
                    trail.append({
                        "lat": float(p["lat"]),
                        "lon": float(p["lon"]),
                        "ts": ts,
                        "capture_session_id": p.get("capture_session_id"),
                        "committed": True if p.get("committed") is None else bool(p["committed"]),
                        "delta_km": float(p["delta_km"] or 0),
                        "road_class": p.get("road_class"),
                    })
                last = None
                if s["last_lat"] is not None and s["last_lon"] is not None:
                    last = {
                        "lat": float(s["last_lat"]),
                        "lon": float(s["last_lon"]),
                        "accuracy": float(s["last_accuracy"]) if s["last_accuracy"] is not None else None,
                        "ts": s["last_ts"].isoformat() if s["last_ts"] else None,
                    }
                cbc = _safe_json_dict(s["covered_by_class"])
                vids[uid] = {
                    "user_id": int(s["user_id"]),
                    "username": s["username"],
                    "full_name": s["full_name"],
                    "state_id": s["state_id"],
                    "district_id": s["district_id"],
                    "date": date,
                    "trail": trail,
                    "recording": bool(s["recording"]),
                    "last": last,
                    "covered_km": float(s["covered_km"] or 0),
                    "covered_by_class": cbc,
                }
            return {"videographers": vids}
    except Exception:
        return None
    finally:
        conn.close()


def tracking_db_update_covered(
    user_id: int,
    date: str,
    covered_km: float,
    covered_by_class: dict | None = None,
) -> bool:
    """Update covered_km / covered_by_class only — does not touch trail points."""
    if not is_db_configured() or not date:
        return False
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return False
    try:
        conn = _get_conn()
    except Exception:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE tracking_sessions
                SET covered_km = %s,
                    covered_by_class = %s,
                    updated_at = NOW()
                WHERE user_id = %s AND track_date = %s::date
                """,
                (
                    float(covered_km or 0),
                    psycopg2.extras.Json(covered_by_class or {}),
                    uid,
                    str(date)[:10],
                ),
            )
            if cur.rowcount == 0:
                # Session row missing — create a minimal one so KPIs stay consistent
                cur.execute(
                    """
                    INSERT INTO tracking_sessions (
                        user_id, track_date, covered_km, covered_by_class, updated_at
                    ) VALUES (%s, %s::date, %s, %s, NOW())
                    ON CONFLICT (user_id, track_date) DO UPDATE SET
                        covered_km = EXCLUDED.covered_km,
                        covered_by_class = EXCLUDED.covered_by_class,
                        updated_at = NOW()
                    """,
                    (
                        uid,
                        str(date)[:10],
                        float(covered_km or 0),
                        psycopg2.extras.Json(covered_by_class or {}),
                    ),
                )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def _tracking_upsert_session(cur, entry: dict) -> int | None:
    """INSERT/UPDATE tracking_sessions for one user/day; returns session id."""
    try:
        user_id = int(entry.get("user_id"))
    except (TypeError, ValueError):
        return None
    date = entry.get("date")
    if not date:
        return None
    last = entry.get("last") or {}
    cbc = psycopg2.extras.Json(entry.get("covered_by_class") or {})
    lat, lon = last.get("lat"), last.get("lon")
    cur.execute("""
        INSERT INTO tracking_sessions (
            user_id, track_date, username, full_name, state_id, district_id,
            recording, covered_km, covered_by_class,
            last_lat, last_lon, last_accuracy, last_ts, last_geom, updated_at
        ) VALUES (
            %s, %s::date, %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s,
            CASE WHEN %s IS NOT NULL AND %s IS NOT NULL
                 THEN ST_SetSRID(ST_MakePoint(%s, %s), 4326) END,
            NOW()
        )
        ON CONFLICT (user_id, track_date) DO UPDATE SET
            username = EXCLUDED.username,
            full_name = EXCLUDED.full_name,
            state_id = EXCLUDED.state_id,
            district_id = EXCLUDED.district_id,
            recording = EXCLUDED.recording,
            covered_km = EXCLUDED.covered_km,
            covered_by_class = EXCLUDED.covered_by_class,
            last_lat = EXCLUDED.last_lat,
            last_lon = EXCLUDED.last_lon,
            last_accuracy = EXCLUDED.last_accuracy,
            last_ts = EXCLUDED.last_ts,
            last_geom = EXCLUDED.last_geom,
            updated_at = NOW()
        RETURNING id
    """, (
        user_id, date,
        entry.get("username"), entry.get("full_name"),
        entry.get("state_id"), entry.get("district_id"),
        bool(entry.get("recording")),
        float(entry.get("covered_km") or 0),
        cbc,
        lat, lon, last.get("accuracy"), last.get("ts"),
        lon, lat, lon, lat,
    ))
    row = cur.fetchone()
    return int(row[0]) if row else None


def _tracking_insert_trail_points(cur, session_id: int, points: list) -> None:
    batch = []
    for p in points or []:
        if not isinstance(p, dict) or p.get("lat") is None or p.get("lon") is None:
            continue
        plat, plon = float(p["lat"]), float(p["lon"])
        batch.append((
            session_id, plat, plon, p.get("ts"), plon, plat,
            p.get("capture_session_id"),
            True if p.get("committed") is None else bool(p.get("committed")),
            float(p.get("delta_km") or 0),
            p.get("road_class"),
        ))
    if not batch:
        return
    psycopg2.extras.execute_batch(
        cur,
        """
        INSERT INTO tracking_trail_points (
            session_id, lat, lon, ts, geom,
            capture_session_id, committed, delta_km, road_class
        )
        VALUES (
            %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326),
            %s, %s, %s, %s
        )
        """,
        batch,
        page_size=200,
    )


def _tracking_trim_trail(cur, session_id: int, max_pts: int) -> None:
    if max_pts <= 0:
        return
    cur.execute(
        """
        DELETE FROM tracking_trail_points
        WHERE session_id = %s
          AND id < (
            SELECT MIN(keep_id) FROM (
                SELECT id AS keep_id
                FROM tracking_trail_points
                WHERE session_id = %s
                ORDER BY id DESC
                LIMIT %s
            ) t
          )
        """,
        (session_id, session_id, max_pts),
    )


def tracking_db_append_ping(entry: dict, new_points: list | None = None) -> bool:
    """Hot path: upsert session meta + INSERT only new trail points (no full rewrite)."""
    if not is_db_configured() or not isinstance(entry, dict):
        return False
    try:
        conn = _get_conn()
    except Exception:
        return False
    try:
        with conn.cursor() as cur:
            session_id = _tracking_upsert_session(cur, entry)
            if session_id is None:
                return False
            _tracking_insert_trail_points(cur, session_id, new_points or [])
            max_pts = int(os.getenv("TRACKING_DB_MAX_TRAIL_POINTS", "400"))
            # Trim occasionally (not every ping) when we appended points
            if new_points and max_pts > 0:
                cur.execute(
                    "SELECT COUNT(*) FROM tracking_trail_points WHERE session_id = %s",
                    (session_id,),
                )
                count = int(cur.fetchone()[0] or 0)
                if count > max_pts + 50:
                    _tracking_trim_trail(cur, session_id, max_pts)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def tracking_db_save_user_entry(entry: dict) -> bool:
    """Upsert one videographer day + replace only that session's trail points.

    Use for discard/commit (trail edit). Prefer ``tracking_db_append_ping`` on GPS pings.
    """
    if not is_db_configured() or not isinstance(entry, dict):
        return False
    try:
        conn = _get_conn()
    except Exception:
        return False
    try:
        with conn.cursor() as cur:
            session_id = _tracking_upsert_session(cur, entry)
            if session_id is None:
                return False
            trail = list(entry.get("trail") or [])
            max_pts = int(os.getenv("TRACKING_DB_MAX_TRAIL_POINTS", "400"))
            if len(trail) > max_pts:
                trail = trail[-max_pts:]
            cur.execute("DELETE FROM tracking_trail_points WHERE session_id = %s", (session_id,))
            _tracking_insert_trail_points(cur, session_id, trail)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def tracking_db_save_state(state: dict) -> bool:
    """Persist tracking_state-shaped dict to Postgres."""
    if not is_db_configured():
        return False
    try:
        conn = _get_conn()
    except Exception:
        return False
    try:
        vids = state.get("videographers") or {}
        with conn.cursor() as cur:
            for key, entry in vids.items():
                if not isinstance(entry, dict):
                    continue
                try:
                    user_id = int(entry.get("user_id") or key)
                except (TypeError, ValueError):
                    continue
                date = entry.get("date")
                if not date:
                    continue
                last = entry.get("last") or {}
                cbc = psycopg2.extras.Json(entry.get("covered_by_class") or {})
                lat, lon = last.get("lat"), last.get("lon")
                cur.execute("""
                    INSERT INTO tracking_sessions (
                        user_id, track_date, username, full_name, state_id, district_id,
                        recording, covered_km, covered_by_class,
                        last_lat, last_lon, last_accuracy, last_ts, last_geom, updated_at
                    ) VALUES (
                        %s, %s::date, %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s,
                        CASE WHEN %s IS NOT NULL AND %s IS NOT NULL
                             THEN ST_SetSRID(ST_MakePoint(%s, %s), 4326) END,
                        NOW()
                    )
                    ON CONFLICT (user_id, track_date) DO UPDATE SET
                        username = EXCLUDED.username,
                        full_name = EXCLUDED.full_name,
                        state_id = EXCLUDED.state_id,
                        district_id = EXCLUDED.district_id,
                        recording = EXCLUDED.recording,
                        covered_km = EXCLUDED.covered_km,
                        covered_by_class = EXCLUDED.covered_by_class,
                        last_lat = EXCLUDED.last_lat,
                        last_lon = EXCLUDED.last_lon,
                        last_accuracy = EXCLUDED.last_accuracy,
                        last_ts = EXCLUDED.last_ts,
                        last_geom = EXCLUDED.last_geom,
                        updated_at = NOW()
                    RETURNING id
                """, (
                    user_id, date,
                    entry.get("username"), entry.get("full_name"),
                    entry.get("state_id"), entry.get("district_id"),
                    bool(entry.get("recording")),
                    float(entry.get("covered_km") or 0),
                    cbc,
                    lat, lon, last.get("accuracy"), last.get("ts"),
                    lon, lat, lon, lat,
                ))
                session_id = cur.fetchone()[0]

                # Replace trail for this session (keeps JSON and DB aligned)
                cur.execute("DELETE FROM tracking_trail_points WHERE session_id = %s", (session_id,))
                for p in entry.get("trail") or []:
                    if p.get("lat") is None or p.get("lon") is None:
                        continue
                    plat, plon = float(p["lat"]), float(p["lon"])
                    cur.execute("""
                        INSERT INTO tracking_trail_points (
                            session_id, lat, lon, ts, geom,
                            capture_session_id, committed, delta_km, road_class
                        )
                        VALUES (
                            %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                            %s, %s, %s, %s
                        )
                    """, (
                        session_id, plat, plon, p.get("ts"), plon, plat,
                        p.get("capture_session_id"),
                        True if p.get("committed") is None else bool(p.get("committed")),
                        float(p.get("delta_km") or 0),
                        p.get("road_class"),
                    ))

        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def survey_db_archive_cleared_assignment(
    user_id: int,
    date: str,
    entry: dict,
) -> int | None:
    """Persist a soft-cleared assignment snapshot for later upload sealing."""
    if not is_db_configured() or not isinstance(entry, dict):
        return None
    try:
        uid = int(user_id)
        day = str(date)[:10]
    except (TypeError, ValueError):
        return None
    start = entry.get("start") or {}
    end = entry.get("end") or {}
    try:
        slat = float(start["lat"]) if start.get("lat") is not None else None
        slon = float(start["lon"]) if start.get("lon") is not None else None
    except (TypeError, ValueError, KeyError):
        slat = slon = None
    try:
        elat = float(end["lat"]) if end.get("lat") is not None else None
        elon = float(end["lon"]) if end.get("lon") is not None else None
    except (TypeError, ValueError, KeyError):
        elat = elon = None
    try:
        did = int(entry["district_id"]) if entry.get("district_id") is not None else None
    except (TypeError, ValueError):
        did = None
    try:
        conn = _get_conn()
    except Exception:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO survey_cleared_assignments (
                    assignment_date, user_id,
                    start_lat, start_lon, end_lat, end_lon,
                    start_label, end_label, segment_ids, polyline,
                    route_km, corridor_km, mode,
                    district_id, district_name, state_key, state_id,
                    entry_snapshot
                ) VALUES (
                    %s::date, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s
                )
                RETURNING id
                """,
                (
                    day, uid,
                    slat, slon, elat, elon,
                    start.get("label") or "", end.get("label") or "",
                    list(entry.get("segment_ids") or []),
                    json.dumps(entry.get("polyline") or []),
                    entry.get("route_km"), entry.get("corridor_km"),
                    entry.get("mode") or "corridor",
                    did, entry.get("district_name"),
                    entry.get("state_key"), entry.get("state_id"),
                    json.dumps(entry, default=str),
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return int(row[0]) if row else None
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        conn.close()


def survey_db_list_open_cleared_assignments(
    user_id: int,
    *,
    days: int = 14,
    limit: int = 20,
) -> list[dict]:
    """Uncleared (not yet sealed-by-upload) soft-deleted assignments for a VG."""
    if not is_db_configured():
        return []
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return []
    try:
        conn = _get_conn()
    except Exception:
        return []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, assignment_date::text AS assignment_date,
                       cleared_at, entry_snapshot,
                       start_lat, start_lon, end_lat, end_lon,
                       segment_ids, polyline, route_km, corridor_km, mode,
                       district_id, district_name, state_key, state_id
                FROM survey_cleared_assignments
                WHERE user_id = %s
                  AND consumed_at IS NULL
                  AND cleared_at >= NOW() - (%s || ' days')::interval
                ORDER BY cleared_at DESC
                LIMIT %s
                """,
                (uid, int(max(1, days)), int(max(1, limit))),
            )
            rows = cur.fetchall() or []
        out = []
        for r in rows:
            snap = _json_field(r.get("entry_snapshot"), {}) or {}
            if not isinstance(snap, dict) or not snap:
                snap = {
                    "user_id": uid,
                    "segment_ids": list(r.get("segment_ids") or []),
                    "polyline": _json_field(r.get("polyline"), []) or [],
                    "route_km": r.get("route_km"),
                    "corridor_km": r.get("corridor_km"),
                    "mode": r.get("mode") or "corridor",
                    "district_id": str(r["district_id"]) if r.get("district_id") is not None else None,
                    "district_name": r.get("district_name"),
                    "state_key": r.get("state_key"),
                    "state_id": r.get("state_id"),
                    "start": (
                        {"lat": float(r["start_lat"]), "lon": float(r["start_lon"]), "label": ""}
                        if r.get("start_lat") is not None and r.get("start_lon") is not None
                        else None
                    ),
                    "end": (
                        {"lat": float(r["end_lat"]), "lon": float(r["end_lon"]), "label": ""}
                        if r.get("end_lat") is not None and r.get("end_lon") is not None
                        else None
                    ),
                }
            out.append({
                "id": int(r["id"]),
                "date": str(r.get("assignment_date") or "")[:10],
                "cleared_at": r["cleared_at"].isoformat() if r.get("cleared_at") else None,
                "entry": snap,
            })
        return out
    except Exception:
        return []
    finally:
        conn.close()


def survey_db_consume_cleared_assignment(archive_id: int, note: str | None = None) -> bool:
    """Mark a soft-cleared assignment as used after upload seal (or drop it)."""
    if not is_db_configured():
        return False
    try:
        aid = int(archive_id)
    except (TypeError, ValueError):
        return False
    try:
        conn = _get_conn()
    except Exception:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE survey_cleared_assignments
                SET consumed_at = NOW(),
                    consumed_note = %s
                WHERE id = %s AND consumed_at IS NULL
                """,
                ((note or "")[:240] or None, aid),
            )
            n = cur.rowcount
        conn.commit()
        return n > 0
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        conn.close()


def migrate_survey_json_to_db(json_path=None) -> dict:
    """One-shot import of survey_state.json into Postgres."""
    from pathlib import Path
    import json as _json
    path = Path(json_path) if json_path else Path(__file__).resolve().parents[1] / "data" / "gis" / "survey_state.json"
    if not path.is_file():
        return {"ok": False, "error": f"Missing {path}"}
    if not is_db_configured():
        return {"ok": False, "error": "Database not configured"}
    state = _json.loads(path.read_text(encoding="utf-8"))
    ok = survey_db_save_state(state)
    return {"ok": ok, "path": str(path), "assignments_days": len(state.get("daily_assignments") or {})}


def migrate_tracking_json_to_db(json_path=None) -> dict:
    """One-shot import of tracking_state.json into Postgres."""
    from pathlib import Path
    import json as _json
    path = Path(json_path) if json_path else Path(__file__).resolve().parents[1] / "data" / "gis" / "tracking_state.json"
    if not path.is_file():
        return {"ok": False, "error": f"Missing {path}"}
    if not is_db_configured():
        return {"ok": False, "error": "Database not configured"}
    state = _json.loads(path.read_text(encoding="utf-8"))
    ok = tracking_db_save_state(state)
    return {"ok": ok, "path": str(path), "videographers": len((state.get("videographers") or {}))}
