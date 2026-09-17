"""Field upload helpers including S3 multipart (presigned) for large videos."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import smartroad_path
from s3_utils import (
    DEFAULT_MULTIPART_PART_SIZE,
    MIN_MULTIPART_PART_SIZE,
    abort_multipart_upload,
    complete_multipart_upload,
    create_multipart_upload,
    get_input_bucket,
    is_s3_configured,
    list_media_keys,
    presign_multipart_part,
    upload_file,
    upload_part_file,
)

ROOT = smartroad_path.ROOT
LOCAL_UPLOAD_DIR = ROOT / "data" / "field_uploads"

# Classic phone→Flask is used for all field uploads now; this threshold is only
# kept for any remaining multipart helpers / docs.
MULTIPART_THRESHOLD = 1 * 1024 * 1024  # 1 MiB
AUTO_TRACK_MAX_POINTS = 40000
# Place-name slug length inside <start>-<end>-<date>-<time>
_ROUTE_PLACE_MAX = 28


def _status(kind: str, message: str) -> dict:
    return {"kind": kind, "message": message}


def _safe_name(filename: str) -> str:
    name = os.path.basename(filename or "upload.bin")
    name = re.sub(r"[^\w.\-]+", "_", name)
    return name or "upload.bin"


def _safe_user(username: Optional[str]) -> str:
    if not username:
        return "_anonymous"
    return re.sub(r"[^\w.\-]+", "_", str(username)).strip("._") or "_anonymous"


def _slug_place(label: str | None, *, fallback: str = "place") -> str:
    raw = (label or "").strip().lower()
    # Prefer the leading place token: "JNTU College, Kukatpally" → "jntu"
    raw = re.split(r"[,|/]| - ", raw, maxsplit=1)[0]
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    if not slug:
        return fallback
    # Keep first 1–2 tokens so keys stay short (jntu / cyber-towers)
    parts = [p for p in slug.split("-") if p]
    if len(parts) >= 2 and len(parts[0]) <= 4:
        slug = f"{parts[0]}-{parts[1]}"
    else:
        slug = parts[0]
    return slug[:_ROUTE_PLACE_MAX].strip("-") or fallback


def build_route_folder_name(
    start_label: str | None = None,
    end_label: str | None = None,
    *,
    when: float | None = None,
    explicit: str | None = None,
) -> str:
    """``<start>-<end>-YYYYMMDD-HHMM`` (IST), places truncated for readable S3 keys."""
    if explicit and str(explicit).strip():
        slug = re.sub(r"[^\w.\-]+", "-", str(explicit).strip().lower()).strip("-")
        return (slug or "route")[:120]

    from datetime import datetime
    try:
        from routes.survey_service import IST
        dt = datetime.now(IST) if when is None else datetime.fromtimestamp(when, tz=IST)
    except Exception:
        from datetime import timezone, timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        dt = datetime.now(ist) if when is None else datetime.fromtimestamp(when, tz=ist)

    stamp = dt.strftime("%Y%m%d-%H%M")
    start = _slug_place(start_label, fallback="")
    end = _slug_place(end_label, fallback="")
    if start and end:
        return f"{start}_{end}-{stamp}"
    if start or end:
        return f"{start or end}-{stamp}"
    return stamp


def resolve_route_folder_for_user(
    user_id: int | None,
    *,
    route_label: str | None = None,
    start_label: str | None = None,
    end_label: str | None = None,
) -> str:
    """Prefer client labels; else today's assignment start→end; else start-end-now."""
    if route_label and str(route_label).strip():
        return build_route_folder_name(explicit=route_label)
    start = (start_label or "").strip() or None
    end = (end_label or "").strip() or None
    if user_id and (not start or not end):
        try:
            from routes import survey_service as _ss
            summary = _ss.assignment_summary_for_user(int(user_id))
            if not start:
                start = ((summary.get("start") or {}) or {}).get("label")
            if not end:
                end = ((summary.get("end") or {}) or {}).get("label")
            if (not start or not end) and (summary.get("legs") or []):
                legs = summary["legs"]
                if not start:
                    start = ((legs[0] or {}).get("start") or {}).get("label")
                if not end:
                    end = ((legs[-1] or {}).get("end") or {}).get("label")
        except Exception:
            pass
    return build_route_folder_name(start, end)


def _input_prefix() -> str:
    prefix = os.getenv("S3_INPUT_PREFIX", "").strip()
    if prefix and not prefix.endswith("/"):
        prefix = f"{prefix}/"
    return prefix


# Top-level roots in the input bucket (never deleted as empty folders).
S3_VG_ROOT = "videographer"
S3_USER_ROOT = "user"
_PROTECTED_ROOTS = frozenset({S3_VG_ROOT, S3_USER_ROOT, "users"})  # users = legacy citizen root


def _user_prefix(username: Optional[str]) -> str:
    """``{prefix}videographer/{username}/`` for field / VG uploads."""
    return f"{_input_prefix()}{S3_VG_ROOT}/{_safe_user(username)}/"


def _hash_media_filename(media_path: str) -> str:
    """Content hash for S3 video object name (``<sha256>.mp4``)."""
    h = hashlib.sha256()
    with open(media_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return f"{h.hexdigest()}.mp4"


def build_media_key(
    username: Optional[str],
    media_filename: str,
    *,
    route_folder: str | None = None,
    session_id: str | None = None,
) -> str:
    """``videographer/{user}/{route_folder}/{file}`` under optional ``S3_INPUT_PREFIX``."""
    media_name = _safe_name(media_filename)
    folder = route_folder or session_id or build_route_folder_name("start", "end")
    folder = re.sub(r"[^\w.\-]+", "-", str(folder)).strip("-.")[:120] or "route"
    return f"{_user_prefix(username)}{folder}/{media_name}"


def identity_prefix_from_key(key: str) -> str | None:
    """Return ``videographer/{vg}/`` or ``user/{mobile}/`` (with input prefix), or None.

    Used when cascading deletes — never returns a protected root alone.
    """
    meta = split_field_key(key)
    root = meta.get("root")
    username = meta.get("username")
    if not root or not username or username.startswith("_"):
        return None
    if root not in (S3_VG_ROOT, S3_USER_ROOT, "users"):
        # Legacy bare ``{vg}/{folder}/file`` → treat identity as ``{vg}/``
        if root is None and username and not username.startswith("_"):
            return f"{_input_prefix()}{username}/"
        return None
    # Map legacy ``users`` root to new ``user`` for identity cleanup target
    canon = S3_USER_ROOT if root == "users" else root
    return f"{_input_prefix()}{canon}/{username}/"


def split_field_key(key: str) -> dict:
    """Parse input-bucket keys into identity + route folder + filename.

    Canonical:
      ``videographer/{vg}/{route_folder}/{file}``
      ``user/{mobileno}/{YYYYMMDD_HHMM}/{file}``

    Legacy (still parsed):
      ``{vg}/{route_folder}/{file}``
      ``users/{mobileno_date_time}/{file}`` or ``users/{id}/{file}``
    """
    raw = (key or "").replace("\\", "/").lstrip("/")
    prefix = _input_prefix()
    if prefix and raw.startswith(prefix):
        raw = raw[len(prefix):]
    parts = [p for p in raw.split("/") if p]

    def _pack(*, root, username, route_folder, filename, source, scoped=True):
        return {
            "root": root,
            "username": username,
            "route_folder": route_folder,
            "filename": filename,
            "source": source,
            "scoped": scoped,
        }

    if not parts:
        return _pack(
            root=None, username="_unscoped", route_folder="_unsorted",
            filename="file.bin", source="videographer", scoped=False,
        )

    top = parts[0].lower()

    # Canonical citizen: user/{mobile}/{folder}/{file}
    if top == S3_USER_ROOT and len(parts) >= 4:
        return _pack(
            root=S3_USER_ROOT,
            username=parts[1],
            route_folder=parts[2],
            filename=parts[-1],
            source="user",
        )
    if top == S3_USER_ROOT and len(parts) == 3:
        return _pack(
            root=S3_USER_ROOT,
            username=parts[1],
            route_folder="_unsorted",
            filename=parts[2],
            source="user",
        )
    if top == S3_USER_ROOT and len(parts) == 2:
        return _pack(
            root=S3_USER_ROOT, username=parts[1], route_folder="_unsorted",
            filename=parts[1], source="user",
        )

    # Legacy citizen: users/{folder_or_id}/{file}
    if top == "users" and len(parts) >= 3:
        folder = parts[1]
        mobile_m = re.match(r"^(\d{10,12})_\d{8}_\d{4,6}$", folder)
        identity = mobile_m.group(1) if mobile_m else folder
        return _pack(
            root="users", username=identity, route_folder=folder,
            filename=parts[-1], source="user",
        )
    if top == "users" and len(parts) == 2:
        return _pack(
            root="users", username=parts[1], route_folder="_unsorted",
            filename=parts[1], source="user",
        )

    # Canonical VG: videographer/{vg}/{folder}/{file}
    if top == S3_VG_ROOT and len(parts) >= 4:
        return _pack(
            root=S3_VG_ROOT,
            username=parts[1],
            route_folder=parts[2],
            filename=parts[-1],
            source="videographer",
        )
    if top == S3_VG_ROOT and len(parts) == 3:
        return _pack(
            root=S3_VG_ROOT,
            username=parts[1],
            route_folder="_unsorted",
            filename=parts[2],
            source="videographer",
        )
    if top == S3_VG_ROOT and len(parts) == 2:
        return _pack(
            root=S3_VG_ROOT, username=parts[1], route_folder="_unsorted",
            filename=parts[1], source="videographer",
        )

    # Legacy VG: {vg}/{folder}/{file}
    if len(parts) >= 3:
        return _pack(
            root=None, username=parts[0], route_folder=parts[1],
            filename=parts[-1], source="videographer",
        )
    if len(parts) == 2:
        return _pack(
            root=None, username=parts[0], route_folder="_unsorted",
            filename=parts[1], source="videographer",
        )
    return _pack(
        root=None, username="_unscoped", route_folder="_unsorted",
        filename=parts[-1], source="videographer", scoped=False,
    )


def processed_source_key(input_key: str) -> str:
    """``sources/{user}/{route_folder}/{filename}`` under the processed bucket."""
    meta = split_field_key(input_key)
    return f"sources/{meta['username']}/{meta['route_folder']}/{meta['filename']}"


def processed_source_frames_prefix(input_key: str | None) -> str | None:
    """``sources/{user}/{route_folder}/frames`` — clean (no-overlay) JPG snapshots."""
    if not input_key:
        return None
    meta = split_field_key(input_key)
    return f"sources/{meta['username']}/{meta['route_folder']}/frames"


def processed_run_prefix(input_key: str | None, run_id: str) -> str:
    """``runs/{user}/{route_folder}/{run_id}`` (outputs live under this)."""
    if not input_key:
        return f"runs/_unscoped/_local/{run_id}"
    meta = split_field_key(input_key)
    return f"runs/{meta['username']}/{meta['route_folder']}/{run_id}"


def _local_dest(username: Optional[str], filename: str, *, route_folder: str | None = None) -> Path:
    LOCAL_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    parts = [_safe_user(username)]
    if route_folder:
        parts.append(re.sub(r"[^\w.\-]+", "-", route_folder).strip("-.")[:120] or "route")
    dest = LOCAL_UPLOAD_DIR.joinpath(*parts, _safe_name(filename))
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def init_multipart_video(
    *,
    username: str,
    media_filename: str,
    size_bytes: int | None = None,
    content_type: str = "video/mp4",
    capture_session_id: str | None = None,
    route_folder: str | None = None,
    user_id: int | None = None,
    start_label: str | None = None,
    end_label: str | None = None,
    route_label: str | None = None,
) -> dict:
    """Create multipart upload + choose part size for 2–10 GB field videos."""
    if not is_s3_configured():
        return {
            "ok": False,
            "error": "S3 is not configured — use classic /api/upload for local storage.",
            "multipart": False,
        }
    size = int(size_bytes or 0)
    part_size = 8 * 1024 * 1024
    if size > 0:
        target_parts = max(40, min(250, size // (16 * 1024 * 1024) or 40))
        part_size = max(MIN_MULTIPART_PART_SIZE, (size + target_parts - 1) // target_parts)
        part_size = max(MIN_MULTIPART_PART_SIZE, (part_size // (1024 * 1024)) * (1024 * 1024))
        if size < 512 * 1024 * 1024:
            part_size = min(part_size, 16 * 1024 * 1024)
    folder = route_folder or resolve_route_folder_for_user(
        user_id,
        route_label=route_label,
        start_label=start_label,
        end_label=end_label,
    )
    # capture_session_id alone is NOT a route name — only use as folder if it looks like one
    if not route_folder and capture_session_id and "-" in str(capture_session_id) and not str(capture_session_id).startswith("cap_"):
        folder = re.sub(r"[^\w.\-]+", "-", capture_session_id)[:120]
    key = build_media_key(username, media_filename, route_folder=folder)
    meta = create_multipart_upload(key, content_type=content_type or "video/mp4")
    return {
        "ok": True,
        "multipart": True,
        "bucket": meta["bucket"],
        "key": meta["key"],
        "upload_id": meta["upload_id"],
        "part_size": part_size,
        "session_id": folder,
        "route_folder": folder,
        "content_type": content_type or "video/mp4",
        "expires_hint_sec": 6 * 3600,
        "message": (
            "Upload each part with PUT to the presigned URL, then call complete. "
            "S3 merges parts into one object for Detection."
        ),
    }


def presign_parts(
    key: str,
    upload_id: str,
    part_numbers: list[int],
    *,
    username: str | None = None,
    bucket: str | None = None,
) -> dict:
    """Presign multipart part URLs — key must belong to username; bucket is server-chosen."""
    if not username:
        raise ValueError("username required for multipart presign.")
    expected_prefix = _user_prefix(username)
    if not expected_prefix or not str(key).startswith(expected_prefix):
        raise ValueError("Upload key does not belong to this user.")
    # Ignore client-supplied bucket — always use the configured input bucket.
    _ = bucket
    from s3_utils import get_input_bucket

    resolved_bucket = get_input_bucket()
    urls = []
    for n in part_numbers:
        n = int(n)
        urls.append({
            "part_number": n,
            "url": presign_multipart_part(key, upload_id, n, bucket=resolved_bucket),
        })
    return {"ok": True, "parts": urls, "bucket": resolved_bucket}


def relay_part(
    *,
    username: str,
    key: str,
    upload_id: str,
    part_number: int,
    part_path: str,
    bucket: str | None = None,
) -> dict:
    """Accept one part body from the phone and forward it to S3 (LAN-only phones)."""
    expected_prefix = _user_prefix(username)
    if expected_prefix and not str(key).startswith(expected_prefix):
        return {"ok": False, "error": "Upload key does not belong to this user."}
    try:
        out = upload_part_file(
            part_path,
            key,
            upload_id,
            int(part_number),
            bucket=bucket,
        )
        return {"ok": True, **out}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def finish_multipart_video(
    *,
    username: str,
    key: str,
    upload_id: str,
    parts: list[dict],
    gps_path: str | None = None,
    gps_filename: str | None = None,
    frame_meta_path: str | None = None,
    frame_meta_filename: str | None = None,
    bucket: str | None = None,
) -> dict:
    """Complete multipart video, then upload small GPS / frame sidecars next to it."""
    expected_prefix = _user_prefix(username)
    if expected_prefix and not str(key).startswith(expected_prefix):
        return {
            "status": _status("error", "Upload key does not belong to this user."),
            "keys": [],
        }
    try:
        complete_multipart_upload(key, upload_id, parts, bucket=bucket)
    except Exception as e:
        try:
            abort_multipart_upload(key, upload_id, bucket=bucket)
        except Exception:
            pass
        return {"status": _status("error", f"Multipart complete failed: {e}"), "keys": []}

    uploaded = [key]
    stem, _ = os.path.splitext(key)
    bucket = bucket or get_input_bucket()

    if gps_path and gps_filename:
        gps_ext = os.path.splitext(_safe_name(gps_filename))[1].lower() or ".csv"
        if gps_ext not in (".csv", ".xlsx", ".xls", ".json"):
            gps_ext = ".csv"
        gps_key = f"{stem}{gps_ext}"
        try:
            upload_file(gps_path, gps_key, bucket=bucket)
            uploaded.append(gps_key)
        except Exception as e:
            return {
                "status": _status("warn", f"Video uploaded as {key}, but GPS log failed: {e}"),
                "keys": uploaded,
                "storage": "s3",
                "multipart": True,
            }

    if frame_meta_path and frame_meta_filename:
        frame_meta_path = enrich_frame_meta_file(frame_meta_path, username=username)
        meta_key = f"{stem}_log.json"
        try:
            upload_file(frame_meta_path, meta_key, bucket=bucket)
            uploaded.append(meta_key)
        except Exception as e:
            return {
                "status": _status("warn", f"Video uploaded as {key}, but frame metadata failed: {e}"),
                "keys": uploaded,
                "storage": "s3",
                "multipart": True,
            }

    return {
        "status": _status(
            "ok",
            f"Uploaded to s3://{bucket}/{key}"
            + (f" (+ {len(uploaded) - 1} sidecar(s))" if len(uploaded) > 1 else "")
            + ". Available in Detection → S3 bucket dropdown.",
        ),
        "keys": uploaded,
        "storage": "s3",
        "multipart": True,
    }


def abort_multipart(
    key: str,
    upload_id: str,
    *,
    username: str | None = None,
    bucket: str | None = None,
) -> dict:
    if not username:
        return {"ok": False, "error": "username required."}
    expected_prefix = _user_prefix(username)
    if not expected_prefix or not str(key).startswith(expected_prefix):
        return {"ok": False, "error": "Upload key does not belong to this user."}
    _ = bucket
    try:
        from s3_utils import get_input_bucket

        abort_multipart_upload(key, upload_id, bucket=get_input_bucket())
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _geo_context_for_user(user_id: int | None, username: str | None = None) -> dict:
    """Country / state / district for GPS frame-meta JSON (India field ops)."""
    out = {"country": "India", "country_code": "IN"}
    try:
        import db_utils
        if not db_utils.is_db_configured():
            return out
        row = None
        if user_id:
            for u in db_utils.get_all_users():
                if int(u["id"]) == int(user_id):
                    row = u
                    break
        if not row and username:
            for u in db_utils.get_all_users():
                if str(u.get("username") or "").lower() == str(username).lower():
                    row = u
                    break
        if not row:
            return out
        sid = row.get("state_id")
        out["state_id"] = sid
        out["state"] = (
            "Telangana" if int(sid or 0) == 2
            else "Andhra Pradesh" if int(sid or 0) == 1
            else None
        )
        did = row.get("district_id")
        ids = row.get("district_ids") or ([] if did is None else [did])
        out["district_id"] = ids[0] if ids else did
        out["district_ids"] = list(ids)
        try:
            from routes import survey_service as _ss
            sk = _ss.resolve_state_key(state_id=sid) if sid else None
            if out.get("district_id") and sk:
                d = _ss.get_district(out["district_id"], sk)
                if d:
                    out["district"] = d.get("name")
                    out["state"] = out.get("state") or d.get("state_name")
            # Prefer today's assignment district when present
            if user_id:
                summary = _ss.assignment_summary_for_user(int(user_id))
                ad = summary.get("district_id") or summary.get("district")
                if ad:
                    out["district_id"] = ad
                    dist = _ss.get_district(ad, sk) if sk else None
                    if dist:
                        out["district"] = dist.get("name")
                if summary.get("district_name"):
                    out["district"] = summary.get("district_name")
        except Exception:
            pass
    except Exception:
        pass
    return {k: v for k, v in out.items() if v is not None}


def enrich_frame_meta_file(
    frame_meta_path: str | None,
    *,
    user_id: int | None = None,
    username: str | None = None,
) -> str | None:
    """Ensure sidecar JSON includes country / state / district (keeps lat/lon frames)."""
    if not frame_meta_path or not os.path.isfile(frame_meta_path):
        return frame_meta_path
    try:
        import json
        raw = Path(frame_meta_path).read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            data = {"frames": data if isinstance(data, list) else []}
        geo = _geo_context_for_user(user_id, username)
        for k, v in geo.items():
            data.setdefault(k, v)
        Path(frame_meta_path).write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass
    return frame_meta_path


def upload_to_input(
    media_path: str,
    media_filename: str,
    gps_path: Optional[str] = None,
    gps_filename: Optional[str] = None,
    *,
    username: Optional[str] = None,
    frame_meta_path: Optional[str] = None,
    frame_meta_filename: Optional[str] = None,
    route_folder: Optional[str] = None,
    user_id: int | None = None,
    start_label: str | None = None,
    end_label: str | None = None,
    route_label: str | None = None,
) -> dict:
    media_name = _safe_name(media_filename)
    base, _ = os.path.splitext(media_name)
    folder = route_folder or resolve_route_folder_for_user(
        user_id,
        route_label=route_label,
        start_label=start_label,
        end_label=end_label,
    )

    if frame_meta_path:
        frame_meta_path = enrich_frame_meta_file(
            frame_meta_path, user_id=user_id, username=username,
        )

    if not is_s3_configured():
        dest_media = _local_dest(username, media_name, route_folder=folder)
        shutil.copy2(media_path, dest_media)
        uploaded = [str(dest_media.relative_to(ROOT)).replace("\\", "/")]
        if gps_path and gps_filename:
            gps_ext = os.path.splitext(_safe_name(gps_filename))[1].lower() or ".csv"
            if gps_ext not in (".csv", ".xlsx", ".xls", ".json"):
                gps_ext = ".csv"
            gps_name = f"{base}{gps_ext}"
            dest_gps = _local_dest(username, gps_name, route_folder=folder)
            shutil.copy2(gps_path, dest_gps)
            uploaded.append(str(dest_gps.relative_to(ROOT)).replace("\\", "/"))
        if frame_meta_path and frame_meta_filename:
            dest_meta = _local_dest(username, f"{base}_log.json", route_folder=folder)
            shutil.copy2(frame_meta_path, dest_meta)
            uploaded.append(str(dest_meta.relative_to(ROOT)).replace("\\", "/"))
        return {
            "status": _status(
                "ok",
                f"S3 not configured — saved locally under data/field_uploads/ "
                f"({len(uploaded)} file(s)). Set AWS credentials for S3 Detection queue.",
            ),
            "keys": uploaded,
            "storage": "local",
            "route_folder": folder,
        }

    media_key = build_media_key(username, media_filename, route_folder=folder)

    try:
        upload_file(media_path, media_key, bucket=get_input_bucket())
        uploaded = [media_key]
    except Exception as e:
        return {"status": _status("error", f"Upload failed: {e}")}

    sidecars: list[tuple[str, str]] = []
    stem, _ = os.path.splitext(media_key)
    if gps_path and gps_filename:
        gps_ext = os.path.splitext(_safe_name(gps_filename))[1].lower()
        if gps_ext not in (".csv", ".xlsx", ".xls", ".json"):
            gps_ext = ".csv"
        sidecars.append((gps_path, f"{stem}{gps_ext}"))
    if frame_meta_path and frame_meta_filename:
        sidecars.append((frame_meta_path, f"{stem}_log.json"))

    if sidecars:
        warn_msgs: list[str] = []
        with ThreadPoolExecutor(max_workers=min(2, len(sidecars))) as pool:
            futs = {
                pool.submit(upload_file, path, key, get_input_bucket()): key
                for path, key in sidecars
            }
            for fut in as_completed(futs):
                key = futs[fut]
                try:
                    fut.result()
                    uploaded.append(key)
                except Exception as e:
                    warn_msgs.append(f"{key}: {e}")
        if warn_msgs:
            return {
                "status": _status(
                    "warn",
                    f"Video uploaded as {media_key}, but sidecar(s) failed: " + "; ".join(warn_msgs),
                ),
                "keys": uploaded,
                "storage": "s3",
                "route_folder": folder,
            }

    return {
        "status": _status(
            "ok",
            f"Uploaded to s3://{get_input_bucket()}/{media_key}"
            + (f" (+ {len(uploaded) - 1} sidecar file(s))" if len(uploaded) > 1 else "")
            + ". Available in Detection → S3 bucket dropdown.",
        ),
        "keys": uploaded,
        "storage": "s3",
        "route_folder": folder,
    }


def list_recent_keys(limit: int = 20) -> dict:
    if not is_s3_configured():
        LOCAL_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        keys = []
        for p in sorted(LOCAL_UPLOAD_DIR.rglob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.is_file() and p.suffix.lower() in (".mp4", ".webm", ".mov", ".jpg", ".jpeg", ".png"):
                keys.append(str(p.relative_to(ROOT)).replace("\\", "/"))
            if len(keys) >= limit:
                break
        return {
            "keys": keys,
            "status": _status("warn", f"S3 not configured — {len(keys)} local file(s) in data/field_uploads/."),
            "storage": "local",
        }
    try:
        prefix = os.getenv("S3_INPUT_PREFIX", "")
        keys = list_media_keys(prefix=prefix)
        return {
            "keys": keys[:limit],
            "status": _status("ok", f"{len(keys)} file(s) in input bucket."),
            "storage": "s3",
        }
    except Exception as e:
        return {"keys": [], "status": _status("error", str(e))}


# ── Chunked capture sessions (1‑min phone clips → ffmpeg concat → S3) ─────────

CHUNK_ROOT = LOCAL_UPLOAD_DIR / "_chunks"
CHUNK_ROTATE_SECONDS = 60
_CHUNK_LOCKS: dict[str, threading.Lock] = {}
_CHUNK_LOCKS_GUARD = threading.Lock()


def _chunk_lock(session_dir: Path) -> threading.Lock:
    key = str(session_dir)
    with _CHUNK_LOCKS_GUARD:
        lock = _CHUNK_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _CHUNK_LOCKS[key] = lock
        return lock


def _place_chunk_file(src: str, dest: Path) -> None:
    """Prefer atomic rename over copy (half the disk I/O on the upload thread)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(src, dest)
    except OSError:
        shutil.copy2(src, dest)
        try:
            os.remove(src)
        except OSError:
            pass


def _chunk_session_dir(username: Optional[str], session_id: str) -> Path:
    sid = re.sub(r"[^\w.\-]+", "_", str(session_id or "").strip())[:80]
    if not sid:
        raise ValueError("capture_session_id is required")
    return CHUNK_ROOT / _safe_user(username) / sid


def _chunk_meta_path(session_dir: Path) -> Path:
    return session_dir / "meta.json"


def _read_chunk_meta(session_dir: Path) -> dict:
    import json
    path = _chunk_meta_path(session_dir)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_chunk_meta(session_dir: Path, meta: dict) -> None:
    import json
    session_dir.mkdir(parents=True, exist_ok=True)
    _chunk_meta_path(session_dir).write_text(
        json.dumps(meta, indent=2),
        encoding="utf-8",
    )


# Terminal / in-flight finalize — late GPS must not mutate coverage after this.
_FINALIZE_CLOSED_STATUSES = frozenset({"queued", "running", "done"})


def is_chunk_session_finalizing(*, username: str, session_id: str) -> bool:
    """True when capture chunk session has begun or finished finalize.

    Used by ``tracking_service.record_ping`` to drop late keepalive / offline
    retries that would otherwise rewrite covered_km after shift end.
    """
    sid = (session_id or "").strip()
    if not sid or not (username or "").strip():
        return False
    try:
        session_dir = _chunk_session_dir(username, sid)
        if not session_dir.is_dir():
            return False
        status = str((_read_chunk_meta(session_dir).get("finalize_status") or "")).strip().lower()
        return status in _FINALIZE_CLOSED_STATUSES
    except Exception:
        return False


def _ffmpeg_exe() -> str:
    from ffmpeg_accel import ffmpeg_exe

    return ffmpeg_exe()


def _concat_chunks_to_master(session_dir: Path, chunk_paths: list[Path]) -> Path:
    """Remux ordered MP4 chunks into master.mp4 (copy when possible, else re-encode)."""
    import subprocess

    from ffmpeg_accel import accel_label, h264_encode_args

    if not chunk_paths:
        raise ValueError("No video chunks to concatenate")
    if len(chunk_paths) == 1:
        master = session_dir / "master.mp4"
        shutil.copy2(chunk_paths[0], master)
        return master

    ffmpeg = _ffmpeg_exe()
    master = session_dir / "master.mp4"
    list_file = session_dir / "concat_list.txt"
    # ffmpeg concat demuxer needs forward slashes / escaped paths
    lines = []
    for p in chunk_paths:
        path_str = str(p.resolve()).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{path_str}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _run(args: list[str]) -> None:
        # Cap well under gunicorn timeout so one finalize can't wedge a worker forever.
        # Demux/copy still benefits from a few CPU threads; encode uses NVENC when present.
        ff_threads = max(1, int(os.getenv("FINALIZE_FFMPEG_THREADS", "4")))
        full = list(args)
        # Only inject -threads for non-NVENC command lines (NVENC ignores CPU thread count).
        if "-c:v" not in full or "h264_nvenc" not in full:
            if "-threads" not in full and len(full) >= 1:
                full[1:1] = ["-threads", str(ff_threads)]
        env = os.environ.copy()
        try:
            if hasattr(os, "nice"):
                os.nice(5)
        except Exception:
            pass
        subprocess.run(
            full,
            check=True,
            capture_output=True,
            timeout=int(os.getenv("CHUNK_CONCAT_TIMEOUT_S", "600")),
            env=env,
        )

    try:
        _run([
            ffmpeg, "-y", "-fflags", "+genpts",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            "-movflags", "+faststart",
            str(master),
        ])
    except Exception:
        # Codec/timestamp mismatch across phone segments — re-encode once (GPU if NVENC).
        encode = h264_encode_args(audio=True)
        print(f"[chunk] concat remux failed — re-encoding with {accel_label()}", flush=True)
        try:
            _run([
                ffmpeg, "-y", "-fflags", "+genpts",
                "-f", "concat", "-safe", "0",
                "-i", str(list_file),
                *encode,
                str(master),
            ])
        except Exception:
            # NVENC can fail mid-session (driver/session limit) — CPU fallback.
            cpu = h264_encode_args(audio=True, force_cpu=True)
            print("[chunk] GPU encode failed — falling back to libx264", flush=True)
            _run([
                ffmpeg, "-y", "-fflags", "+genpts",
                "-f", "concat", "-safe", "0",
                "-i", str(list_file),
                *cpu,
                str(master),
            ])
    return master


def init_chunk_session(
    *,
    username: str,
    session_id: str,
    user_id: int | None = None,
) -> dict:
    """Create (or reset) a chunked capture session directory on the Flask host."""
    session_dir = _chunk_session_dir(username, session_id)
    if session_dir.exists():
        shutil.rmtree(session_dir, ignore_errors=True)
    session_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "session_id": session_id,
        "username": _safe_user(username),
        "user_id": int(user_id) if user_id is not None else None,
        "chunk_count": 0,
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    _write_chunk_meta(session_dir, meta)
    return {
        "ok": True,
        "session_id": session_id,
        "chunk_count": 0,
        "rotate_seconds": CHUNK_ROTATE_SECONDS,
        "status": _status("ok", "Chunk session ready — phone can stream 1‑min clips."),
    }


def _missing_indices(received: list[int]) -> list[int]:
    """Gaps below the highest received index (e.g. [0, 2, 3] -> [1])."""
    if not received:
        return []
    have = set(received)
    return [i for i in range(received[-1]) if i not in have]


def append_chunk(
    *,
    username: str,
    session_id: str,
    chunk_path: str,
    chunk_index: int | None = None,
) -> dict:
    """Save one MP4 segment to disk only (no ffmpeg on the request path).

    Previously every chunk remixed *all* prior minutes with ffmpeg on the same
    gunicorn thread — under concurrent videographers that saturated CPU and
    filled the worker pool until the process had to be restarted.

    Concatenation happens once in ``finalize_chunk_session``.
    """
    session_dir = _chunk_session_dir(username, session_id)
    if not session_dir.is_dir():
        return {
            "ok": False,
            "status": _status("error", "Unknown chunk session — call /upload/session/init first."),
        }
    with _chunk_lock(session_dir):
        meta = _read_chunk_meta(session_dir)
        received: list[int] = sorted(set(int(i) for i in (meta.get("received_indices") or [])))
        idx = int(chunk_index) if chunk_index is not None else (received[-1] + 1 if received else 0)

        dest = session_dir / f"chunk_{idx:04d}.mp4"
        _place_chunk_file(chunk_path, dest)
        if idx not in received:
            received.append(idx)
            received.sort()

        missing = _missing_indices(received)
        chunk_bytes = dest.stat().st_size if dest.is_file() else 0
        meta["received_indices"] = received
        meta["chunk_count"] = received[-1] + 1 if received else 0
        meta["missing_indices"] = missing
        meta["updated_at"] = time.time()
        meta["last_chunk_bytes"] = chunk_bytes
        meta.pop("last_concat_error", None)
        _write_chunk_meta(session_dir, meta)
    gap_note = (
        f" — minute(s) {', '.join(str(m + 1) for m in missing)} still missing"
        if missing else ""
    )
    return {
        "ok": True,
        "session_id": session_id,
        "chunk_index": idx,
        "chunk_count": meta["chunk_count"],
        "missing_indices": missing,
        "chunk_bytes": chunk_bytes,
        "status": _status(
            "ok",
            f"Chunk {idx + 1} saved on server"
            + (f" ({chunk_bytes // (1024 * 1024)} MB)" if chunk_bytes >= 1024 * 1024 else "")
            + gap_note
            + ". Full video assembles at Upload/finalize.",
        ),
    }


# One finalize at a time by default — stacked encodes starve /detection UI.
_FINALIZE_POOL = ThreadPoolExecutor(
    max_workers=max(1, int(os.getenv("FINALIZE_CONCURRENCY", "1"))),
    thread_name_prefix="finalize",
)


def _run_finalize_subprocess(payload: dict) -> dict:
    """ffmpeg + S3 in a child process so Flask stays responsive for the portal."""
    import json
    import subprocess
    import sys
    import tempfile

    root = ROOT
    script = root / "scripts" / "finalize_chunk_job.py"
    timeout = int(os.getenv("FINALIZE_JOB_TIMEOUT_S", "1800"))
    fd_in, in_path = tempfile.mkstemp(prefix="sr_fin_in_", suffix=".json")
    os.close(fd_in)
    out_path = in_path + ".out.json"
    try:
        Path(in_path).write_text(json.dumps(payload), encoding="utf-8")
        cmd = [sys.executable, str(script), in_path, out_path]
        # Linux: low priority so /detection + login keep CPU.
        if os.name == "posix" and shutil.which("nice"):
            cmd = ["nice", "-n", "15", *cmd]
        env = os.environ.copy()
        env.setdefault("OMP_NUM_THREADS", "2")
        env.setdefault("S3_UPLOAD_CONCURRENCY", os.getenv("FINALIZE_S3_CONCURRENCY", "8"))
        print(f"[chunk] finalize subprocess start session={payload.get('session_id')}", flush=True)
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if Path(out_path).is_file():
            result = json.loads(Path(out_path).read_text(encoding="utf-8"))
        else:
            err = (proc.stderr or proc.stdout or f"exit {proc.returncode}")[:500]
            result = {"status": _status("error", f"Finalize worker failed: {err}")}
        if proc.returncode != 0 and (result.get("status") or {}).get("kind") == "ok":
            # Prefer structured error if worker wrote one.
            pass
        print(
            f"[chunk] finalize subprocess done session={payload.get('session_id')} "
            f"rc={proc.returncode} kind={(result.get('status') or {}).get('kind')}",
            flush=True,
        )
        return result
    finally:
        for p in (in_path, out_path):
            try:
                os.remove(p)
            except OSError:
                pass


def queue_chunk_finalize(
    *,
    username: str,
    session_id: str,
    gps_path: str,
    gps_filename: str,
    frame_meta_path: str | None = None,
    frame_meta_filename: str | None = None,
    user_id: int | None = None,
    route_label: str | None = None,
    start_label: str | None = None,
    end_label: str | None = None,
    on_complete=None,
    media_title: str | None = None,
    capture_session_id: str | None = None,
) -> dict:
    """Accept phone upload immediately; enqueue assemble/S3 for the worker process.

    Default: write ``data/finalize_queue/pending/*.json`` only — Flask does **not**
    run ffmpeg/S3. ``smartroad-worker`` picks jobs up. Set
    ``FINALIZE_EXTERNAL_WORKER=0`` to fall back to in-process (legacy).
    """
    from routes.finalize_queue import enqueue_finalize_job, external_worker_enabled

    session_dir = _chunk_session_dir(username, session_id)
    if not session_dir.is_dir():
        return {"ok": False, "status": _status("error", "No chunk session to finalize.")}
    with _chunk_lock(session_dir):
        meta = _read_chunk_meta(session_dir)
        if meta.get("finalize_status") in ("queued", "running", "done"):
            return {
                "ok": True,
                "accepted": True,
                "async": True,
                "session_id": session_id,
                "chunk_count": int(meta.get("chunk_count") or 0),
                "missing_indices": meta.get("missing_indices") or [],
                "status": _status("ok", "Upload already received — processing continues on the server."),
            }
        chunks = sorted(session_dir.glob("chunk_*.mp4"))
        if not chunks:
            return {"ok": False, "status": _status("error", "No video chunks uploaded yet.")}

        gps_ext = os.path.splitext(_safe_name(gps_filename))[1].lower() or ".csv"
        if gps_ext not in (".csv", ".xlsx", ".xls", ".json"):
            gps_ext = ".csv"
        staged_gps = session_dir / f"finalize_gps{gps_ext}"
        shutil.copy2(gps_path, staged_gps)
        staged_frame = None
        staged_frame_name = None
        if frame_meta_path and frame_meta_filename:
            staged_frame = session_dir / "finalize_frames.json"
            shutil.copy2(frame_meta_path, staged_frame)
            staged_frame_name = "finalize_frames.json"

        meta["finalize_status"] = "queued"
        meta["finalize_queued_at"] = time.time()
        meta["finalize_error"] = None
        _write_chunk_meta(session_dir, meta)
        missing = list(meta.get("missing_indices") or [])
        chunk_count = int(meta.get("chunk_count") or len(chunks))

    payload = {
        "username": username,
        "session_id": session_id,
        "gps_path": str(staged_gps),
        "gps_filename": f"capture{gps_ext}",
        "frame_meta_path": str(staged_frame) if staged_frame else None,
        "frame_meta_filename": staged_frame_name,
        "user_id": user_id,
        "route_label": route_label,
        "start_label": start_label,
        "end_label": end_label,
        "wipe_session": False,
    }
    side_effects = {
        "user_id": user_id,
        "media_title": media_title or f"chunked:{session_id}",
        "gps_path": str(staged_gps),
        "capture_session_id": capture_session_id or session_id,
    }

    gap = ""
    if missing:
        mins = ", ".join(str(m + 1) for m in missing)
        gap = f" Warning: minute(s) {mins} never arrived — video may have gaps."

    # Preferred path: external worker owns all heavy IO/CPU.
    if external_worker_enabled():
        job_meta = enqueue_finalize_job(
            finalize_payload=payload,
            side_effects=side_effects,
            session_id=session_id,
            username=username,
        )
        with _chunk_lock(session_dir):
            m = _read_chunk_meta(session_dir)
            m["finalize_job_id"] = job_meta.get("job_id")
            _write_chunk_meta(session_dir, m)
        return {
            "ok": True,
            "accepted": True,
            "async": True,
            "queued": True,
            "job_id": job_meta.get("job_id"),
            "session_id": session_id,
            "chunk_count": chunk_count,
            "missing_indices": missing,
            "chunked": True,
            "status": _status(
                "ok",
                f"Upload complete — {chunk_count} clip(s) queued for background assemble.{gap}",
            ),
        }

    # Legacy: in-process thread (+ optional subprocess) — not recommended on AceCloud.
    def _job():
        with _chunk_lock(session_dir):
            m = _read_chunk_meta(session_dir)
            m["finalize_status"] = "running"
            _write_chunk_meta(session_dir, m)
        try:
            use_sub = os.getenv("FINALIZE_SUBPROCESS", "1").lower() in ("1", "true", "yes")
            if use_sub:
                result = _run_finalize_subprocess(payload)
            else:
                result = finalize_chunk_session(**payload)
            kind = (result.get("status") or {}).get("kind")
            gps_for_seal = str(staged_gps) if staged_gps.is_file() else None
            if kind == "ok" and callable(on_complete):
                try:
                    on_complete(result, gps_for_seal)
                except Exception:
                    pass
            if kind == "ok":
                shutil.rmtree(session_dir, ignore_errors=True)
            elif session_dir.is_dir():
                with _chunk_lock(session_dir):
                    m = _read_chunk_meta(session_dir)
                    m["finalize_status"] = "error"
                    m["finalize_error"] = (result.get("status") or {}).get("message")
                    _write_chunk_meta(session_dir, m)
        except Exception as e:
            if session_dir.is_dir():
                with _chunk_lock(session_dir):
                    m = _read_chunk_meta(session_dir)
                    m["finalize_status"] = "error"
                    m["finalize_error"] = str(e)
                    _write_chunk_meta(session_dir, m)

    _FINALIZE_POOL.submit(_job)
    return {
        "ok": True,
        "accepted": True,
        "async": True,
        "session_id": session_id,
        "chunk_count": chunk_count,
        "missing_indices": missing,
        "chunked": True,
        "status": _status(
            "ok",
            f"Upload complete — {chunk_count} clip(s) received on server.{gap}",
        ),
    }


def finalize_chunk_session(
    *,
    username: str,
    session_id: str,
    gps_path: str | None = None,
    gps_filename: str | None = None,
    frame_meta_path: str | None = None,
    frame_meta_filename: str | None = None,
    user_id: int | None = None,
    route_label: str | None = None,
    start_label: str | None = None,
    end_label: str | None = None,
    wipe_session: bool = True,
) -> dict:
    """Concat chunks (if needed) → upload full video to S3/local → optionally delete session dir."""
    session_dir = _chunk_session_dir(username, session_id)
    if not session_dir.is_dir():
        return {"status": _status("error", "No chunk session to finalize.")}
    meta = _read_chunk_meta(session_dir)
    chunks = sorted(session_dir.glob("chunk_*.mp4"))
    if not chunks:
        return {"status": _status("error", "No video chunks uploaded yet.")}

    try:
        # Always rebuild from remaining chunks at finalize (do not trust a stale master).
        with _chunk_lock(session_dir):
            master = _concat_chunks_to_master(session_dir, chunks)
    except Exception as e:
        return {"status": _status("error", f"Could not assemble video chunks: {e}")}

    media_name = _hash_media_filename(str(master))
    result = upload_to_input(
        str(master),
        media_name,
        gps_path=gps_path,
        gps_filename=gps_filename,
        username=username,
        frame_meta_path=frame_meta_path,
        frame_meta_filename=frame_meta_filename,
        user_id=user_id,
        route_label=route_label,
        start_label=start_label,
        end_label=end_label,
    )
    if (result.get("status") or {}).get("kind") == "ok":
        result["chunk_count"] = int(meta.get("chunk_count") or len(chunks))
        result["chunked"] = True
        missing = meta.get("missing_indices") or []
        if missing:
            # Surface the gap instead of silently returning ok — kind stays "ok" so
            # the caller still runs GPS-sealing/detection side effects, but the
            # message + missing_indices field flag which minute(s) never arrived.
            result["missing_indices"] = missing
            mins = ", ".join(str(m + 1) for m in missing)
            base_msg = (result.get("status") or {}).get("message") or ""
            result["status"] = _status(
                "ok",
                f"{base_msg} Warning: minute(s) {mins} never uploaded — this video has gaps.".strip(),
            )
        if wipe_session:
            # Wipe session so Discard after success is a no-op
            shutil.rmtree(session_dir, ignore_errors=True)
    return result


def discard_chunk_session(*, username: str, session_id: str) -> dict:
    """Delete all uploaded chunks / master for this capture session."""
    try:
        session_dir = _chunk_session_dir(username, session_id)
    except ValueError as e:
        return {"ok": False, "cleared": False, "status": _status("error", str(e))}
    if not session_dir.exists():
        return {
            "ok": True,
            "cleared": False,
            "session_id": session_id,
            "status": _status("ok", "No chunk session on server."),
        }
    with _chunk_lock(session_dir):
        meta = _read_chunk_meta(session_dir)
        fin = (meta.get("finalize_status") or "").strip().lower()
        if fin in ("queued", "running"):
            return {
                "ok": False,
                "cleared": False,
                "session_id": session_id,
                "status": _status(
                    "error",
                    "Upload is still assembling on the server — wait a moment before discarding.",
                ),
            }
        shutil.rmtree(session_dir, ignore_errors=True)
    return {
        "ok": True,
        "cleared": True,
        "session_id": session_id,
        "status": _status("ok", "Chunk session discarded."),
    }


def chunk_session_status(*, username: str, session_id: str) -> dict:
    try:
        session_dir = _chunk_session_dir(username, session_id)
    except ValueError as e:
        return {"ok": False, "status": _status("error", str(e))}
    if not session_dir.is_dir():
        return {"ok": False, "exists": False, "chunk_count": 0}
    meta = _read_chunk_meta(session_dir)
    return {
        "ok": True,
        "exists": True,
        "session_id": session_id,
        "chunk_count": int(meta.get("chunk_count") or 0),
        "missing_indices": meta.get("missing_indices") or [],
        "master_bytes": int(meta.get("master_bytes") or 0),
        "rotate_seconds": CHUNK_ROTATE_SECONDS,
    }
