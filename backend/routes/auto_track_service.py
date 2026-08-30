"""Custom auto-track: free-drive GPS trail vs video GPS verification.

Tables (see schema.sql):
  - auto_track_gps          — user + GPS covered track (start/end + points)
  - auto_track_video_coords — user + coords extracted from uploaded video GPS log
  - auto_track_uploads      — user + video title + upload day (+ match result)
"""
from __future__ import annotations

import csv
import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# Mean point-to-track distance (km) under this → matched (~40 m, was 80 m)
MATCH_THRESHOLD_KM = 0.04
# Anti-tamper gates
MIN_MATCH_POINTS = 25
MIN_MATCH_KM = 0.15
MAX_POINT_SPEED_KMH = 160.0  # reject impossible jumps in either track
MAX_MEAN_ACCURACY_M = 80.0  # if accuracy present and terrible, soft-fail
# Cap stored GPS JSON for long days / high-rate pings
MAX_STORED_GPS_POINTS = 40000


def today_ist() -> str:
    return datetime.now(IST).date().isoformat()


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _path_km(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    return sum(
        _haversine_km(points[i][0], points[i][1], points[i + 1][0], points[i + 1][1])
        for i in range(len(points) - 1)
    )


def _track_tamper_flags(points: list[dict]) -> list[str]:
    """Heuristic anti-spoof checks on a GPS point list."""
    flags: list[str] = []
    if len(points) < MIN_MATCH_POINTS:
        flags.append(f"too_few_points:{len(points)}<{MIN_MATCH_POINTS}")
    coords = [(float(p["lat"]), float(p["lon"])) for p in points]
    km = _path_km(coords)
    if km < MIN_MATCH_KM:
        flags.append(f"too_short_km:{km:.3f}<{MIN_MATCH_KM}")
    # Impossible speeds between consecutive samples
    bad_jumps = 0
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        d = _haversine_km(float(a["lat"]), float(a["lon"]), float(b["lat"]), float(b["lon"]))
        dt = None
        try:
            ta = a.get("ts")
            tb = b.get("ts")
            if ta and tb:
                # ISO or numeric video-second
                if isinstance(ta, (int, float)) or (isinstance(ta, str) and ta.replace(".", "", 1).isdigit()):
                    dt = abs(float(tb) - float(ta))
                else:
                    from datetime import datetime as _dt
                    da = _dt.fromisoformat(str(ta).replace("Z", "+00:00"))
                    db = _dt.fromisoformat(str(tb).replace("Z", "+00:00"))
                    dt = abs((db - da).total_seconds())
        except Exception:
            dt = None
        if dt is not None and dt > 0.2:
            speed = (d / dt) * 3600.0  # km/h
            if speed > MAX_POINT_SPEED_KMH:
                bad_jumps += 1
        elif d > 2.0:
            # No usable timestamps but huge jump
            bad_jumps += 1
    if bad_jumps >= 3:
        flags.append(f"impossible_jumps:{bad_jumps}")
    accs = []
    for p in points:
        try:
            if p.get("accuracy") is not None or p.get("accuracy_m") is not None:
                accs.append(float(p.get("accuracy") if p.get("accuracy") is not None else p["accuracy_m"]))
        except (TypeError, ValueError):
            continue
    if accs and (sum(accs) / len(accs)) > MAX_MEAN_ACCURACY_M:
        flags.append(f"poor_accuracy_m:{sum(accs)/len(accs):.0f}")
    return flags


def _server_trail_points(user_id: int, day: str) -> list[dict]:
    """Prefer committed Capture trail from tracking (harder to spoof than only the uploaded CSV)."""
    try:
        from routes import tracking_service
        detail = tracking_service.get_videographer_track(int(user_id), day)
        trail = detail.get("trail") or []
        out = []
        for p in trail:
            if not isinstance(p, dict):
                continue
            if p.get("committed") is False:
                continue
            if p.get("lat") is None or p.get("lon") is None:
                continue
            out.append({
                "lat": float(p["lat"]),
                "lon": float(p["lon"]),
                "ts": p.get("ts"),
                "accuracy": p.get("accuracy"),
            })
        return out
    except Exception:
        return []


def _normalize_points(raw) -> list[dict]:
    out = []
    if not isinstance(raw, list):
        return out
    for p in raw:
        if not isinstance(p, (list, tuple, dict)):
            continue
        try:
            if isinstance(p, dict):
                lat = float(p.get("lat") if p.get("lat") is not None else p.get("latitude"))
                lon = float(p.get("lon") if p.get("lon") is not None else p.get("longitude"))
                ts = p.get("ts") or p.get("timestamp")
                acc = p.get("accuracy") if p.get("accuracy") is not None else p.get("accuracy_m")
            else:
                lat, lon = float(p[0]), float(p[1])
                ts = p[2] if len(p) > 2 else None
                acc = None
            if abs(lat) > 90 or abs(lon) > 180:
                continue
            item = {"lat": lat, "lon": lon}
            if ts is not None:
                item["ts"] = str(ts)
            if acc is not None:
                try:
                    item["accuracy"] = float(acc)
                except (TypeError, ValueError):
                    pass
            out.append(item)
        except (TypeError, ValueError):
            continue
    return _cap_points(out)


def _cap_points(points: list[dict], max_n: int = MAX_STORED_GPS_POINTS) -> list[dict]:
    """Downsample evenly when trails get huge (all-day Capture)."""
    n = len(points)
    if n <= max_n:
        return points
    out = [points[0]]
    step = (n - 1) / (max_n - 1)
    for i in range(1, max_n - 1):
        out.append(points[int(round(i * step))])
    out.append(points[-1])
    return out


def _parse_gps_log_file(path: str | Path) -> list[dict]:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except Exception:
            return []
        if isinstance(data, dict):
            data = data.get("points") or data.get("gps") or data.get("trail") or []
        return _normalize_points(data)

    # CSV — flexible headers
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    reader = csv.DictReader(lines)
    rows = list(reader)
    if rows:
        pts = []
        for row in rows:
            keys = {k.lower().strip(): k for k in row}
            lat_k = keys.get("lat") or keys.get("latitude") or keys.get("y")
            lon_k = keys.get("lon") or keys.get("lng") or keys.get("longitude") or keys.get("x")
            if not lat_k or not lon_k:
                continue
            try:
                lat = float(row[lat_k])
                lon = float(row[lon_k])
            except (TypeError, ValueError):
                continue
            ts_k = keys.get("ts") or keys.get("timestamp") or keys.get("time") or keys.get("videosecond")
            item = {"lat": lat, "lon": lon}
            if ts_k and row.get(ts_k) not in (None, ""):
                item["ts"] = str(row[ts_k])
            pts.append(item)
        if pts:
            return _normalize_points(pts)

    # Fallback: bare lat,lon columns without header
    pts = []
    for ln in lines[1:] if "," in lines[0].lower() and "lat" in lines[0].lower() else lines:
        parts = [p.strip() for p in ln.split(",")]
        if len(parts) < 2:
            continue
        try:
            a, b = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        # Heuristic: India lon ~70-90, lat ~8-35
        if abs(a) <= 90 and abs(b) <= 180:
            lat, lon = a, b
            if abs(a) > 40 and abs(b) < 40:
                lon, lat = a, b
            pts.append({"lat": lat, "lon": lon})
    return _normalize_points(pts)


def _mean_dist_to_track_km(probe: list[dict], track: list[dict]) -> float:
    """Average nearest-neighbour distance from probe points onto track (km)."""
    if not probe or not track:
        return 999.0
    t_coords = [(float(p["lat"]), float(p["lon"])) for p in track]
    # Sample probe if huge
    step = max(1, len(probe) // 400)
    samples = probe[::step]
    total = 0.0
    for p in samples:
        plat, plon = float(p["lat"]), float(p["lon"])
        best = min(_haversine_km(plat, plon, t[0], t[1]) for t in t_coords)
        total += best
    return total / max(len(samples), 1)


def upsert_gps_track(
    user_id: int,
    *,
    gps_points: list | None = None,
    start_lat: float | None = None,
    start_lon: float | None = None,
    end_lat: float | None = None,
    end_lon: float | None = None,
    start_label: str | None = None,
    end_label: str | None = None,
    track_date: str | None = None,
    append: bool = True,
) -> dict:
    """Create/update today's GPS free-drive track for a videographer."""
    import db_utils
    import psycopg2.extras

    if not db_utils.is_db_configured():
        raise RuntimeError("Database is not configured.")

    date = track_date or today_ist()
    points = _normalize_points(gps_points or [])
    conn = db_utils._get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, gps_points FROM auto_track_gps
                WHERE user_id = %s AND track_date = %s::date
                ORDER BY id DESC LIMIT 1
                """,
                (int(user_id), date),
            )
            row = cur.fetchone()
            if row and append and points:
                existing = row.get("gps_points") or []
                if isinstance(existing, str):
                    existing = json.loads(existing)
                merged = _normalize_points(list(existing) + points)
            elif points:
                merged = points
            elif row:
                existing = row.get("gps_points") or []
                if isinstance(existing, str):
                    existing = json.loads(existing)
                merged = _normalize_points(existing)
            else:
                merged = []

            coords = [(p["lat"], p["lon"]) for p in merged]
            km = round(_path_km(coords), 3)
            if start_lat is None and coords:
                start_lat, start_lon = coords[0]
            if end_lat is None and coords:
                end_lat, end_lon = coords[-1]

            geom_wkt = None
            if len(coords) >= 2:
                geom_wkt = "LINESTRING(" + ", ".join(f"{lon} {lat}" for lat, lon in coords) + ")"

            payload = psycopg2.extras.Json(merged)
            if row:
                cur.execute(
                    """
                    UPDATE auto_track_gps SET
                        start_lat = COALESCE(%s, start_lat),
                        start_lon = COALESCE(%s, start_lon),
                        end_lat = COALESCE(%s, end_lat),
                        end_lon = COALESCE(%s, end_lon),
                        start_label = COALESCE(%s, start_label),
                        end_label = COALESCE(%s, end_label),
                        gps_points = %s,
                        covered_km = %s,
                        gps_geom = CASE WHEN %s IS NOT NULL
                            THEN ST_SetSRID(ST_GeomFromText(%s), 4326) ELSE gps_geom END,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id
                    """,
                    (
                        start_lat, start_lon, end_lat, end_lon,
                        start_label, end_label, payload, km,
                        geom_wkt, geom_wkt, row["id"],
                    ),
                )
                tid = cur.fetchone()["id"]
            else:
                cur.execute(
                    """
                    INSERT INTO auto_track_gps (
                        user_id, track_date, start_lat, start_lon, end_lat, end_lon,
                        start_label, end_label, gps_points, covered_km, gps_geom
                    ) VALUES (
                        %s, %s::date, %s, %s, %s, %s, %s, %s, %s, %s,
                        CASE WHEN %s IS NOT NULL THEN ST_SetSRID(ST_GeomFromText(%s), 4326) ELSE NULL END
                    )
                    RETURNING id
                    """,
                    (
                        int(user_id), date, start_lat, start_lon, end_lat, end_lon,
                        start_label, end_label, payload, km, geom_wkt, geom_wkt,
                    ),
                )
                tid = cur.fetchone()["id"]
        conn.commit()
        return {
            "id": tid,
            "user_id": int(user_id),
            "track_date": date,
            "point_count": len(merged),
            "covered_km": km,
            "start": {"lat": start_lat, "lon": start_lon, "label": start_label},
            "end": {"lat": end_lat, "lon": end_lon, "label": end_label},
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_upload_and_verify(
    user_id: int,
    *,
    video_title: str,
    gps_log_path: str | Path | None = None,
    s3_key: str | None = None,
    upload_day: str | None = None,
) -> dict:
    """Store upload metadata + video coords; compare to today's GPS track when present."""
    import db_utils
    import psycopg2.extras

    if not db_utils.is_db_configured():
        return {"ok": False, "error": "Database is not configured."}

    day = upload_day or today_ist()
    title = (video_title or "upload").strip() or "upload"
    video_pts = _parse_gps_log_file(gps_log_path) if gps_log_path else []
    video_km = round(_path_km([(p["lat"], p["lon"]) for p in video_pts]), 3) if video_pts else 0.0

    conn = db_utils._get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, gps_points, covered_km FROM auto_track_gps
                WHERE user_id = %s AND track_date = %s::date
                ORDER BY id DESC LIMIT 1
                """,
                (int(user_id), day),
            )
            gps_row = cur.fetchone()
            gps_track_id = int(gps_row["id"]) if gps_row else None
            gps_pts = []
            if gps_row:
                raw = gps_row.get("gps_points") or []
                if isinstance(raw, str):
                    raw = json.loads(raw)
                gps_pts = _normalize_points(raw)

            # Prefer live Capture trail from tracking (server-side) over client-only CSV seed
            server_trail = _server_trail_points(int(user_id), day)
            if len(server_trail) >= MIN_MATCH_POINTS:
                gps_pts = server_trail
            elif server_trail and len(server_trail) > len(gps_pts):
                gps_pts = server_trail

            tamper_flags = _track_tamper_flags(video_pts) + [
                f"gps_{f}" for f in _track_tamper_flags(gps_pts)
            ]

            if not gps_pts and not gps_track_id:
                match_status = "no_gps_track"
                match_score = None
            elif not video_pts:
                match_status = "mismatch"
                match_score = None
            elif tamper_flags:
                match_status = "mismatch"
                match_score = None
            else:
                # Bidirectional mean distance (video→gps and gps→video)
                d1 = _mean_dist_to_track_km(video_pts, gps_pts)
                d2 = _mean_dist_to_track_km(gps_pts, video_pts)
                match_score = round((d1 + d2) / 2.0, 4)
                match_status = "matched" if match_score <= MATCH_THRESHOLD_KM else "mismatch"

            cur.execute(
                """
                INSERT INTO auto_track_uploads (
                    user_id, video_title, upload_day, s3_key, gps_track_id, match_score, match_status
                ) VALUES (%s, %s, %s::date, %s, %s, %s, %s)
                RETURNING id
                """,
                (int(user_id), title, day, s3_key, gps_track_id, match_score, match_status),
            )
            upload_id = cur.fetchone()["id"]

            geom_wkt = None
            if len(video_pts) >= 2:
                geom_wkt = "LINESTRING(" + ", ".join(
                    f"{p['lon']} {p['lat']}" for p in video_pts
                ) + ")"
            cur.execute(
                """
                INSERT INTO auto_track_video_coords (
                    user_id, upload_id, coords, covered_km, coord_geom
                ) VALUES (
                    %s, %s, %s, %s,
                    CASE WHEN %s IS NOT NULL THEN ST_SetSRID(ST_GeomFromText(%s), 4326) ELSE NULL END
                )
                RETURNING id
                """,
                (
                    int(user_id),
                    upload_id,
                    psycopg2.extras.Json(video_pts),
                    video_km,
                    geom_wkt,
                    geom_wkt,
                ),
            )
            video_id = cur.fetchone()["id"]
        conn.commit()

        # Unlock next assign when custom track matches
        if match_status == "matched":
            try:
                from routes import survey_service
                state = survey_service._load_state()
                entry = (
                    state.get("daily_assignments", {}).get(day, {}) or {}
                ).get(survey_service._user_day_key(int(user_id)))
                if isinstance(entry, dict) and str(entry.get("mode") or "").lower() == "auto_track":
                    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
                    meta = dict(meta)
                    meta["completed"] = True
                    meta["completed_at"] = datetime.now(IST).isoformat()
                    meta["match_upload_id"] = upload_id
                    entry["meta"] = meta
                    survey_service._save_state(state)
            except Exception:
                pass

        return {
            "ok": True,
            "upload_id": upload_id,
            "video_coords_id": video_id,
            "gps_track_id": gps_track_id,
            "video_title": title,
            "upload_day": day,
            "video_point_count": len(video_pts),
            "video_covered_km": video_km,
            "match_status": match_status,
            "match_score_km": match_score,
            "threshold_km": MATCH_THRESHOLD_KM,
            "tamper_flags": tamper_flags,
            "gps_source": "tracking_trail" if len(server_trail) >= MIN_MATCH_POINTS else "auto_track_gps",
        }
    except Exception as e:
        conn.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()


def list_uploads_for_user(user_id: int, day: str | None = None) -> list[dict]:
    import db_utils
    import psycopg2.extras

    if not db_utils.is_db_configured():
        return []
    conn = db_utils._get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if day:
                cur.execute(
                    """
                    SELECT id, user_id, video_title, upload_day, s3_key, gps_track_id,
                           match_score, match_status, created_at
                    FROM auto_track_uploads
                    WHERE user_id = %s AND upload_day = %s::date
                    ORDER BY id DESC
                    """,
                    (int(user_id), day),
                )
            else:
                cur.execute(
                    """
                    SELECT id, user_id, video_title, upload_day, s3_key, gps_track_id,
                           match_score, match_status, created_at
                    FROM auto_track_uploads
                    WHERE user_id = %s
                    ORDER BY upload_day DESC, id DESC
                    LIMIT 50
                    """,
                    (int(user_id),),
                )
            rows = cur.fetchall()
            out = []
            for r in rows:
                out.append({
                    "id": r["id"],
                    "user_id": r["user_id"],
                    "video_title": r["video_title"],
                    "upload_day": r["upload_day"].isoformat() if hasattr(r["upload_day"], "isoformat") else str(r["upload_day"]),
                    "s3_key": r["s3_key"],
                    "gps_track_id": r["gps_track_id"],
                    "match_score_km": float(r["match_score"]) if r["match_score"] is not None else None,
                    "match_status": r["match_status"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                })
            return out
    finally:
        conn.close()
