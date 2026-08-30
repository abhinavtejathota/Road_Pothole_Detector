"""detection.sessions — extends run (includes private _names)."""
from __future__ import annotations

import routes.detection.run as _run

globals().update({k: v for k, v in vars(_run).items() if not k.startswith('__')})

def load_sessions(source: str | None = None) -> dict:
    try:
        from db_utils import get_all_sessions, is_db_configured

        if not is_db_configured():
            return {"sessions": [], "status": _status("error", "DB not configured.")}
        rows = get_all_sessions()
        if not rows:
            return {"sessions": [], "status": _status("ok", "No videos processed yet.")}
        from routes.report_service import short_route_caption

        want = (source or "").strip().lower() or None
        sessions = []
        for r in rows:
            if bool(r.get("is_legacy")):
                continue
            uname = (r.get("username") or "").strip()
            if uname.lower() in ("", "legacy"):
                continue
            kind = _session_source_kind(r.get("s3_key"), uname)
            if want in ("videographer", "user") and kind != want:
                continue
            fname = r.get("display_name") or r["filename"]
            is_image = any(str(r.get("original_filename") or fname).lower().endswith(ext) for ext in _IMAGE_EXTS)
            sessions.append({
                "id": r["id"],
                "filename": fname,
                "original_filename": r.get("original_filename") or r["filename"],
                "processed_at": r["processed_at"],
                "total_potholes": r["total_potholes"],
                "username": r.get("username"),
                "is_legacy": bool(r.get("is_legacy")),
                "report_s3_key": r.get("report_s3_key"),
                "s3_key": r.get("s3_key"),
                "run_id": r.get("run_id"),
                "start_label": r.get("start_label"),
                "end_label": r.get("end_label"),
                "route_short": short_route_caption(
                    r.get("start_label") or "", r.get("end_label") or "",
                ),
                "has_report": bool(r.get("report_s3_key")),
                "source_kind": kind,
                "media_kind": "image" if is_image else "video",
            })
        label = "citizen session(s)" if want == "user" else "video(s)"
        return {
            "sessions": sessions,
            "source": want or "all",
            "status": _status("ok", f"{len(sessions)} {label} processed."),
        }
    except Exception as e:
        return {"sessions": [], "status": _status("error", f"DB error: {e}")}


def load_session_detail(session_id: Optional[int]) -> dict:
    """Full detail for the dashboard View-details modal."""
    try:
        from db_utils import get_potholes_for_session, get_session_by_id
        from routes.report_service import short_route_caption

        if not session_id:
            return {"session": None, "potholes": [], "status": _status("error", "Session ID required.")}
        session = get_session_by_id(int(session_id))
        if not session:
            return {"session": None, "potholes": [], "status": _status("error", f"Session {session_id} not found.")}

        rows = get_potholes_for_session(int(session_id))
        potholes = [
            {
                "class": r["class"],
                "conf": r["conf"],
                "severity": r["severity"],
                "x1": r["x1"],
                "y1": r["y1"],
                "x2": r["x2"],
                "y2": r["y2"],
                "lat": r["lat"],
                "lon": r["lon"],
                "city": r["city"],
                "state": r["state"],
                "street_name": r.get("street_name"),
                "captured_at": r["captured_at"],
                "map_link": r["map_link"],
                "s3_url": r["frame_s3_url"],
            }
            for r in rows
        ]
        outputs = resolve_session_output_urls(session)
        kind = _session_source_kind(session.get("s3_key"), session.get("username"))
        route_short = short_route_caption(
            session.get("start_label") or "", session.get("end_label") or "",
        )
        return {
            "session": {
                "id": session["id"],
                "filename": session.get("display_name") or session.get("filename"),
                "original_filename": session.get("filename"),
                "username": session.get("username"),
                "s3_key": session.get("s3_key"),
                "run_id": session.get("run_id"),
                "total_potholes": session.get("total_potholes"),
                "start_label": session.get("start_label"),
                "end_label": session.get("end_label"),
                "route_short": route_short,
                "processed_at": session.get("processed_at"),
                "source_kind": kind,
                **outputs,
            },
            "potholes": potholes,
            "map_links": [
                {"index": i, "url": p["map_link"], "lat": p.get("lat"), "lon": p.get("lon")}
                for i, p in enumerate(potholes)
                if p.get("map_link")
            ],
            "status": _status("ok", f"Session {session_id}: {len(potholes)} pothole(s)."),
        }
    except Exception as e:
        return {"session": None, "potholes": [], "status": _status("error", f"DB error: {e}")}


def load_session_potholes(session_id: Optional[int]) -> dict:
    detail = load_session_detail(session_id)
    return {
        "potholes": detail.get("potholes") or [],
        "map_links": [
            f"{m['index']}: {m['url']}" for m in (detail.get("map_links") or [])
        ],
        "session": detail.get("session"),
        "status": detail.get("status"),
    }
