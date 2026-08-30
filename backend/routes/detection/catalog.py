"""detection.catalog — extends status (includes private _names)."""
from __future__ import annotations

import routes.detection.status as _status

globals().update({k: v for k, v in vars(_status).items() if not k.startswith('__')})

def list_s3_catalog(source: str = "videographer") -> dict:
    """Grouped S3 input layout filtered by source.

    Canonical prefixes:
      - videographer → ``videographer/{vg}/…`` (also lists legacy bare ``{vg}/…``)
      - user → ``user/{mobile}/…`` (also lists legacy ``users/…``)
    """
    kind = (source or "videographer").strip().lower()
    if kind not in ("videographer", "user"):
        kind = "videographer"
    if not is_s3_configured():
        return {"users": [], "keys": [], "source": kind, "status": _status("error", "S3 not configured.")}
    try:
        from routes.field_upload_service import S3_USER_ROOT, S3_VG_ROOT, split_field_key

        prefix = os.getenv("S3_INPUT_PREFIX", "").strip()
        if prefix and not prefix.endswith("/"):
            prefix = f"{prefix}/"

        if kind == "user":
            keys = list_media_keys(prefix=f"{prefix}{S3_USER_ROOT}/")
            keys = sorted(set(keys) | set(list_media_keys(prefix=f"{prefix}users/")))
        else:
            keys = list_media_keys(prefix=f"{prefix}{S3_VG_ROOT}/")
            # Legacy VG tops (video5/, video/, …) — exclude citizen/system roots
            for key in list_media_keys(prefix=prefix):
                meta = split_field_key(key)
                if meta.get("source") != "videographer":
                    continue
                if meta.get("root") in (S3_VG_ROOT, S3_USER_ROOT, "users"):
                    continue
                top = (meta.get("username") or "").lower()
                if top in _SKIP_TOP or top in (S3_VG_ROOT, S3_USER_ROOT, "users"):
                    continue
                keys.append(key)
            keys = sorted(set(keys))

        users_map: dict[str, dict[str, list]] = {}
        for key in keys:
            meta = split_field_key(key)
            if meta.get("source") != kind:
                continue
            identity = meta.get("username") or "_unscoped"
            if identity.lower() in _SKIP_TOP or identity.startswith("_"):
                continue
            folder = meta.get("route_folder") or "_unsorted"
            users_map.setdefault(identity, {}).setdefault(folder, []).append(key)

        users = []
        flat_keys = []
        for username in sorted(users_map.keys(), key=lambda u: u.lower()):
            folders = []
            for folder in sorted(users_map[username].keys(), reverse=True):
                vkeys = sorted(users_map[username][folder])
                flat_keys.extend(vkeys)
                folders.append({
                    "folder": folder,
                    "label": _folder_display_label(folder),
                    "keys": vkeys,
                })
            users.append({"username": username, "folders": folders})

        if not flat_keys:
            empty_msg = (
                f"No citizen media in s3://{get_input_bucket()}/{prefix}{S3_USER_ROOT}/ yet."
                if kind == "user"
                else f"No videographer media in s3://{get_input_bucket()}/{prefix}{S3_VG_ROOT}/"
            )
            return {
                "users": [],
                "keys": [],
                "source": kind,
                "status": _status("error", empty_msg),
            }
        label = "citizen upload(s)" if kind == "user" else "video(s)"
        return {
            "users": users,
            "keys": flat_keys,
            "source": kind,
            "status": _status("ok", f"{len(flat_keys)} {label} across {len(users)} account(s)."),
        }
    except Exception as e:
        return {
            "users": [],
            "keys": [],
            "source": kind,
            "status": _status("error", f"S3 catalog failed: {e}"),
        }


def _sibling_candidates(s3_key: str) -> list[str]:
    base, _ = os.path.splitext(s3_key)
    out = []
    for suf in (
        "_log.json", "_frames.json", ".json",
        "_log.csv", ".csv", "_log.xlsx", ".xlsx", "_log.xls", ".xls",
    ):
        out.append(base + suf)
    return out


def _prefix_has_media(bucket: str, prefix: str) -> bool:
    keys = list_media_keys(bucket=bucket, prefix=prefix)
    return bool(keys)


def _delete_prefix_keys(bucket: str, prefix: str) -> list[str]:
    """Delete every object under ``prefix``. Returns deleted keys."""
    from s3_utils import _client, delete_object

    deleted = []
    paginator = _client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents") or []:
            k = obj.get("Key") or ""
            if not k:
                continue
            try:
                delete_object(bucket, k)
                deleted.append(k)
            except Exception:
                pass
    return deleted


def delete_s3_media(s3_key: str, *, source: str | None = None) -> dict:
    """Delete media + siblings; if the VG/mobile folder is empty of media, purge it.

    Never removes the top-level ``videographer/`` or ``user/`` roots.
    """
    _ = source
    if not s3_key:
        return {"keys": [], "selected": None, "status": _status("error", "No file selected to delete.")}
    if not is_s3_configured():
        return {"keys": [], "selected": None, "status": _status("error", "S3 not configured.")}

    from routes.field_upload_service import identity_prefix_from_key, split_field_key

    bucket = get_input_bucket()
    # Citizen media may live on reporter bucket (often same as input)
    try:
        from s3_utils import get_reporter_bucket
        meta = split_field_key(s3_key)
        if meta.get("source") == "user":
            bucket = get_reporter_bucket()
    except Exception:
        pass

    deleted, errors = [], []

    try:
        delete_object(bucket, s3_key)
        deleted.append(s3_key)
    except Exception as e:
        errors.append(f"Media: {e}")

    for candidate in _sibling_candidates(s3_key):
        try:
            if head_exists(bucket, candidate):
                delete_object(bucket, candidate)
                deleted.append(candidate)
        except Exception as e:
            errors.append(f"Sibling {candidate}: {e}")

    # Cascade: if no media remains under this VG/mobile identity, purge that folder.
    identity_pfx = identity_prefix_from_key(s3_key)
    purged_identity = False
    if identity_pfx:
        try:
            if not _prefix_has_media(bucket, identity_pfx):
                extra = _delete_prefix_keys(bucket, identity_pfx)
                for k in extra:
                    if k not in deleted:
                        deleted.append(k)
                purged_identity = True
        except Exception as e:
            errors.append(f"Identity cleanup: {e}")

    kind = (split_field_key(s3_key).get("source") or "videographer")
    listing = list_s3_catalog(source=kind)
    keys = listing.get("keys") or []
    if errors:
        msg = f"Deleted: {', '.join(deleted)}. Errors: {'; '.join(errors)}"
        return {"keys": keys, "selected": keys[0] if keys else None, "status": _status("error", msg), "deleted": deleted}
    extra = ""
    if purged_identity and identity_pfx:
        extra = f" Cleared empty folder {identity_pfx}"
    elif len(deleted) > 1:
        extra = f" Also removed {len(deleted) - 1} sibling file(s)."
    return {
        "keys": keys,
        "selected": keys[0] if keys else None,
        "deleted": deleted,
        "status": _status("ok", f"Deleted: {s3_key}.{extra}"),
    }


def _session_source_kind(s3_key: str | None, username: str | None) -> str:
    from routes.field_upload_service import split_field_key

    if s3_key:
        src = (split_field_key(s3_key).get("source") or "").strip().lower()
        if src in ("user", "videographer"):
            return src
    uname = (username or "").strip()
    if re.fullmatch(r"\d{10,12}", uname):
        return "user"
    return "videographer"


def _presign_processed(key: str) -> str | None:
    try:
        from s3_utils import get_processed_bucket, head_exists, is_s3_configured, presign_url

        if not is_s3_configured():
            return None
        bucket = get_processed_bucket()
        if not head_exists(bucket, key):
            return None
        return presign_url(key, bucket=bucket, expires_in=3600)
    except Exception:
        return None


def resolve_session_output_urls(session: dict) -> dict:
    """Locate annotated output under ``runs/{user}/{route}/{run_id}/outputs/``."""
    from routes.field_upload_service import processed_run_prefix

    run_id = session.get("run_id") or ""
    s3_key = session.get("s3_key") or ""
    filename = session.get("original_filename") or session.get("filename") or ""
    prefix = processed_run_prefix(s3_key or None, run_id) if run_id else ""
    video_url = image_url = download_url = None
    if prefix:
        video_url = _presign_processed(f"{prefix}/outputs/annotated.mp4")
        image_url = _presign_processed(f"{prefix}/outputs/annotated.jpg")
        try:
            from s3_utils import get_processed_bucket, head_exists, presign_download_url

            bucket = get_processed_bucket()
            for name in ("annotated.mp4", "annotated.jpg"):
                key = f"{prefix}/outputs/{name}"
                if head_exists(bucket, key):
                    download_url = presign_download_url(
                        key, bucket=bucket, filename=name, expires_in=3600,
                    )
                    break
        except Exception:
            download_url = video_url or image_url
    is_image = bool(image_url and not video_url) or any(
        str(filename).lower().endswith(ext) for ext in _IMAGE_EXTS
    )
    if video_url:
        is_image = False
    return {
        "output_video_url": video_url,
        "output_image_url": image_url,
        "output_file_url": download_url or video_url or image_url,
        "media_kind": "image" if is_image else "video",
    }


def preview_s3_selection(s3_key: Optional[str]) -> dict:
    if not s3_key:
        return {"video_url": None, "json_text": "", "show_json": False, "status": _status("ok", "")}
    if not is_s3_configured():
        return {
            "video_url": None,
            "json_text": "",
            "show_json": False,
            "status": _status("error", "S3 not configured."),
        }

    try:
        video_url = presign_input_url(s3_key, expires_in=3600)
    except Exception as e:
        return {
            "video_url": None,
            "json_text": "",
            "show_json": False,
            "status": _status("error", f"Could not get video URL: {e}"),
        }

    bucket = get_input_bucket()
    base, _ = os.path.splitext(s3_key)
    json_text = ""
    show_json = False
    json_status = ""
    found_key = None

    try:
        found_key = find_sibling_json_key(bucket, s3_key)
        if found_key:
            raw = read_text_object(bucket, found_key)
            try:
                parsed = json.loads(raw)
                json_text = json.dumps(parsed, indent=2, ensure_ascii=False)
            except Exception:
                json_text = raw
            show_json = True
            json_status = f"JSON: {found_key}"
        else:
            tried = ", ".join([base + "_log.json", base + "_frames.json", base + ".json"])
            json_status = f"No JSON sibling found (tried {tried})"
    except Exception as e:
        json_status = f"Failed to read JSON: {e}"

    parts = [f"Video ready: {s3_key}", json_status]
    try:
        gps_sibling = find_sibling_gps_key(bucket, s3_key)
        if gps_sibling and not str(gps_sibling).lower().endswith(".json"):
            parts.append(f"GPS log sibling: {gps_sibling}")
    except Exception:
        pass

    return {
        "video_url": video_url,
        "json_text": json_text,
        "show_json": show_json,
        "status": _status("ok", " | ".join(parts)),
    }


def _file_api_url(abs_path: str) -> Optional[str]:
    if not abs_path or not os.path.isfile(abs_path):
        return None
    resolved = Path(abs_path).resolve()
    for root in ALLOWED_SERVE_ROOTS:
        try:
            rel = resolved.relative_to(root)
            return f"/api/detection/files/{rel.as_posix()}"
        except ValueError:
            continue
    return None


def _serialize_row(r) -> dict:
    return {
        "class": r.cls,
        "conf": float(r.conf),
        "severity": r.severity,
        "x1": r.x1,
        "y1": r.y1,
        "x2": r.x2,
        "y2": r.y2,
        "lat": r.lat,
        "lon": r.lon,
        "captured_at": r.captured_at,
        "map_link": r.map_link,
        "s3_url": r.s3_url,
    }


