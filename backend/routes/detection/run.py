"""detection.run — extends catalog (includes private _names)."""
from __future__ import annotations

import routes.detection.catalog as _catalog

globals().update({k: v for k, v in vars(_catalog).items() if not k.startswith('__')})

def run_detection(
    *,
    s3_key: Optional[str] = None,
    local_path: Optional[str] = None,
    gps_path: Optional[str] = None,
    capture_mode: str = "walking",
    rotation: int = 0,
) -> dict:
    use_s3 = bool(s3_key)
    if not use_s3 and not local_path:
        return {"status": _status("error", "Pick a file from S3 or upload one locally.")}

    media_path_for_check = s3_key or local_path
    is_video = _is_video(media_path_for_check)

    if is_video and not gps_path:
        if not use_s3:
            return {
                "status": _status(
                    "error",
                    "Video selected. Please upload a GPS Log (CSV/XLSX) so lat/lon/map_link can be populated.",
                ),
            }
        try:
            sibling = find_sibling_gps_key(get_input_bucket(), s3_key)
            if not sibling:
                stem = os.path.splitext(s3_key)[0]
                return {
                    "status": _status(
                        "error",
                        f"No GPS log uploaded and no sibling .csv/.xlsx found next to {s3_key} in S3. "
                        f"Upload a GPS log, or place one in S3 (e.g. {stem}.csv).",
                    ),
                }
        except Exception as e:
            return {"status": _status("error", f"Could not check S3 for sibling GPS log: {e}")}

    run_id = time.strftime("%Y%m%d_%H%M%S")
    output_dir = str(OUTPUTS_ROOT / "runs" / run_id)
    os.makedirs(output_dir, exist_ok=True)

    lock_fh = _acquire_detection_lock(timeout_s=float(os.getenv("DETECTION_LOCK_WAIT_S", "8")))
    if lock_fh is False:
        return {
            "status": _status(
                "error",
                "Another detection is already running on this server. "
                "Wait for it to finish (or check GPU load), then retry — "
                "this keeps login/uploads responsive.",
            ),
        }

    rot = 0
    try:
        from pothole_detector import pothole_detector, normalize_rotation_deg

        rot = normalize_rotation_deg(rotation)
        in_path, out_path, rows, frame_paths, frames_zip_path = pothole_detector(
            input_path=local_path,
            s3_input_key=s3_key if use_s3 else None,
            gps_log_path=gps_path,
            capture_mode=capture_mode,
            output_dir=output_dir,
            rotation=rot,
            run_id=run_id,
        )
    except Exception as e:
        return {"status": _status("error", f"Processing failed: {e}")}
    finally:
        _release_detection_lock(lock_fh if lock_fh not in (False, None) else None)

    # Pipeline already finished (DB/S3 side effects done). Never mark the job
    # failed for response/UI assembly bugs (e.g. prior UnboundLocalError on
    # get_processed_bucket after a successful detect).
    try:
        is_video_out = _is_video(out_path)
        detections = [_serialize_row(r) for r in rows]
        map_links = [f"{i}: {r['map_link']}" for i, r in enumerate(detections) if r.get("map_link")]

        status_msg = "Processed successfully."
        if _is_video(in_path):
            status_msg += f" (mode: {capture_mode}.)"
        if use_s3:
            status_msg += (
                f" Source moved s3://{get_input_bucket()}/{s3_key} → "
                f"s3://{get_processed_bucket()}/sources/{{user}}/{{route}}/..."
            )
        if is_s3_configured():
            status_msg += (
                f" Outputs in s3://{get_processed_bucket()}/runs/{{user}}/{{route}}/{{run_id}}/..."
            )

        gallery = [_file_api_url(fp) for fp in (frame_paths or [])]
        gallery = [u for u in gallery if u]

        # Prefer S3 annotated object for download (survives after local temp cleanup;
        # Content-Disposition forces a real file download in the browser).
        annotated_name = "annotated.mp4" if is_video_out else "annotated.jpg"
        s3_download_url = None
        if is_s3_configured():
            try:
                from routes.field_upload_service import processed_run_prefix
                # Do not re-import get_processed_bucket/head_exists here — a local
                # import would shadow the module bindings for the whole function.
                from s3_utils import presign_download_url

                prefix = processed_run_prefix(s3_key if use_s3 else None, run_id)
                s3_key_out = f"{prefix}/outputs/{annotated_name}"
                bucket = get_processed_bucket()
                if head_exists(bucket, s3_key_out):
                    s3_download_url = presign_download_url(
                        s3_key_out,
                        bucket=bucket,
                        filename=annotated_name,
                        expires_in=3600,
                    )
            except Exception:
                s3_download_url = None

        # Local fallback: annotated media (+ GPS zip when a small sidecar exists).
        download_path = out_path
        if not s3_download_url:
            try:
                import zipfile

                gps_for_zip = None
                if gps_path and os.path.isfile(gps_path):
                    gps_for_zip = gps_path
                else:
                    stem_local = os.path.splitext(in_path)[0]
                    for cand in (
                        stem_local + ".csv",
                        stem_local + "_log.csv",
                        stem_local + ".xlsx",
                        stem_local + "_log.xlsx",
                    ):
                        if os.path.isfile(cand):
                            gps_for_zip = cand
                            break
                # Skip zipping huge videos — browsers choke; download annotated alone.
                out_size = os.path.getsize(out_path) if out_path and os.path.isfile(out_path) else 0
                if (
                    gps_for_zip
                    and out_path
                    and os.path.isfile(out_path)
                    and out_size < 80 * 1024 * 1024
                ):
                    bundle = os.path.join(output_dir, "annotated_with_gps.zip")
                    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                        zf.write(out_path, arcname=os.path.basename(out_path))
                        zf.write(gps_for_zip, arcname=os.path.basename(gps_for_zip))
                    download_path = bundle
            except Exception:
                download_path = out_path

        verified_complaint = None
        if use_s3 and s3_key:
            try:
                from routes.reporter_service import verify_complaint_by_media_key

                verified_complaint = verify_complaint_by_media_key(s3_key)
                if verified_complaint:
                    status_msg += (
                        f" Citizen complaint {verified_complaint['tracking_number']} marked Verified."
                    )
            except Exception:
                verified_complaint = None

        session_id = None
        try:
            from db_utils import get_session_by_run_id, is_db_configured
            if is_db_configured():
                row = get_session_by_run_id(run_id)
                if row:
                    session_id = row.get("id")
        except Exception:
            session_id = None

        local_file_url = _file_api_url(download_path)
        # Force attachment for local API serve (query flag handled by route).
        if local_file_url and not s3_download_url:
            local_file_url = f"{local_file_url}?download=1"

        return {
            "status": _status("ok", status_msg),
            "run_id": run_id,
            "session_id": session_id,
            "input_preview_url": _file_api_url(in_path),
            "output_image_url": _file_api_url(out_path) if not is_video_out else None,
            "output_video_url": _file_api_url(out_path) if is_video_out else None,
            "output_file_url": s3_download_url or local_file_url,
            "detections": detections,
            "gallery": gallery,
            "frames_zip_url": _file_api_url(frames_zip_path) if frames_zip_path else None,
            "map_links": map_links,
            "verified_complaint": verified_complaint,
        }
    except Exception as e:
        print(f"[detection] response assembly failed after successful run {run_id}: {e}")
        session_id = None
        try:
            from db_utils import get_session_by_run_id, is_db_configured
            if is_db_configured():
                row = get_session_by_run_id(run_id)
                if row:
                    session_id = row.get("id")
        except Exception:
            pass
        return {
            "status": _status(
                "ok",
                f"Processed successfully (UI extras failed: {e}).",
            ),
            "run_id": run_id,
            "session_id": session_id,
            "input_preview_url": None,
            "output_image_url": None,
            "output_video_url": None,
            "output_file_url": None,
            "detections": [],
            "gallery": [],
            "frames_zip_url": None,
            "map_links": [],
            "verified_complaint": None,
        }


def resolve_serve_path(rel_path: str) -> Optional[Path]:
    """Resolve a relative path under outputs/ for safe file serving."""
    clean = Path(rel_path)
    if clean.is_absolute() or ".." in clean.parts:
        return None
    candidate = (OUTPUTS_ROOT / clean).resolve()
    if not str(candidate).startswith(str(OUTPUTS_ROOT.resolve())):
        return None
    if not candidate.is_file():
        return None
    return candidate


