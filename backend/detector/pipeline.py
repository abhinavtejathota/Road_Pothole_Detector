import os
import cv2
import time
import math
import zipfile
import tempfile
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict

import exifread
from datetime import datetime
import re

# Optional: video metadata GPS (may be absent in most MP4s)
try:
    from pymediainfo import MediaInfo
except Exception:
    MediaInfo = None

# Optional: GPS log parsing (CSV/XLSX)
try:
    import pandas as pd
except Exception:
    pd = None

from model_loader import load_model, resolve_yolo_device
from utils import severity_from_area_and_position

# --- S3 integration ---
from s3_utils import (
    is_s3_configured,
    upload_and_link,
    upload_file,
    download_file,
    move_object,
    copy_object,
    find_sibling_gps_key,
    get_input_bucket,
    get_processed_bucket,
)



from detector._importutil import reexport
import detector.gps as _gps
import detector.types as _types
import detector.track as _track
import detector.video_io as _video_io
import detector.parallel as _parallel

reexport(_gps, globals())
reexport(_types, globals())
reexport(_track, globals())
reexport(_video_io, globals())
reexport(_parallel, globals())

def pothole_detector(
    input_path: Optional[str] = None,
    output_dir: str = "outputs",
    gps_log_path: Optional[str] = None,
    gps_second_offset: int = 0,
    capture_mode: str = "walking",
    s3_input_key: Optional[str] = None,         # NEW: pull source from S3
    s3_input_bucket: Optional[str] = None,      # NEW: override input bucket
    rotation: int = 0,                          # kept for API compat; ignored (original orientation)
    run_id: Optional[str] = None,               # share id with detection API / DB
) -> Tuple[str, str, List[DetectionRow], List[str], str]:
    """
    Two ways to provide the source media:
      1) Pass a local input_path (legacy behaviour).
      2) Pass s3_input_key (and optionally s3_input_bucket). The file is downloaded
         from S3, processed, then MOVED to the processed bucket on success.

    Outputs are always uploaded to S3_PROCESSED_BUCKET under runs/<run_id>/outputs/...
    when S3 is configured.

    Media is processed in its original orientation (no bake / rotate).
    """
    os.makedirs(output_dir, exist_ok=True)
    model = None  # loaded lazily below - the parallel video path loads its own copy per worker instead

    run_id = (run_id or "").strip() or time.strftime("%Y%m%d_%H%M%S")
    # Clear layout: runs/{user}/{route_folder}/{run_id}/outputs/...
    try:
        from routes.field_upload_service import processed_run_prefix
        s3_run_prefix = processed_run_prefix(s3_input_key, run_id)
    except Exception:
        s3_run_prefix = f"runs/_unscoped/_local/{run_id}"
    src_bucket = s3_input_bucket or get_input_bucket()

    # ---------- Resolve source (S3 download path or local path) ----------
    downloaded_from_s3 = False
    downloaded_gps_from_s3 = False

    if s3_input_key:
        if not is_s3_configured():
            raise RuntimeError(
                "S3 input requested but AWS credentials are not configured in .env"
            )
        local_dir = tempfile.mkdtemp(prefix="smartroad_dl_")
        local_name = os.path.basename(s3_input_key) or "input.bin"
        input_path = os.path.join(local_dir, local_name)
        download_file(src_bucket, s3_input_key, input_path)
        downloaded_from_s3 = True

        # If user didn't pass a GPS log, look for a sibling in S3
        if not gps_log_path:
            sibling_key = find_sibling_gps_key(src_bucket, s3_input_key)
            if sibling_key:
                gps_local = os.path.join(local_dir, os.path.basename(sibling_key))
                try:
                    download_file(src_bucket, sibling_key, gps_local)
                    gps_log_path = gps_local
                    downloaded_gps_from_s3 = True
                except Exception as e:
                    print(f"[S3] Could not download sibling GPS log {sibling_key}: {e}")

    if not input_path:
        raise ValueError("No input provided: pass either input_path or s3_input_key.")

    # 1) Bake phone display-matrix so OpenCV/YOLO see upright frames (like the browser).
    # 2) Apply optional UI rotation on top of that upright bitstream.
    # Keep original pixel orientation — no display-matrix bake / UI rotate.
    set_active_input_rotation(0)

    ext = os.path.splitext(input_path)[1].lower()
    is_video = ext in [".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"]

    # Pre-read GPS (best effort)
    gps = read_gps_from_video(input_path) if is_video else read_gps_from_image(input_path)
    base_lat, base_lon = (gps if gps else (None, None))
    base_captured_at = read_timestamp_from_video(input_path) if is_video else read_timestamp_from_image(input_path)

    # GPS log map for video
    gps_map: Dict[int, Tuple[float, float, str]] = {}
    if is_video and gps_log_path:
        gps_map = load_gps_log(gps_log_path)

    # ============================================================
    #  IMAGE
    # ============================================================
    if not is_video:
        model = load_model()
        img = cv2.imread(input_path)
        if img is None:
            raise ValueError(f"Could not read image: {input_path}")

        rows = _infer_on_frame(model, img)
        for r in rows:
            r.lat = base_lat
            r.lon = base_lon
            r.captured_at = base_captured_at
            r.map_link = maps_link(r.lat, r.lon)
            _draw_box(img, r)

        out_path = os.path.join(output_dir, "annotated.jpg")
        cv2.imwrite(out_path, img)

        annotated_url = _safe_upload(out_path, f"{s3_run_prefix}/outputs/annotated.jpg")
        if annotated_url:
            for r in rows:
                r.s3_url = annotated_url

        # Move source on success
        if s3_input_key and _move_on_success():
            try:
                from routes.field_upload_service import processed_source_key
                dst_key = processed_source_key(s3_input_key)
                move_object(src_bucket, s3_input_key, get_processed_bucket(), dst_key)
            except Exception as e:
                print(f"[S3] move failed: {e}")

        set_active_input_rotation(0)
        return input_path, out_path, rows, [], ""

    # ============================================================
    #  VIDEO
    # ============================================================
    cap = _open_video_capture(input_path)
    if not cap.isOpened():
        raise ValueError(f"Could not open video: {input_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    cap.release()  # each path below opens its own handle(s)

    video_seconds = (total_frames / fps) if fps else 0
    chunk_seconds = float(os.getenv("POTHOLE_CHUNK_SECONDS", "18"))
    overlap_seconds = float(os.getenv("POTHOLE_CHUNK_OVERLAP_SECONDS", "2"))
    yolo_device = resolve_yolo_device()
    # One GPU process is best on a shared A6000; multi-process workers each load
    # a full model and fight for the same card (or fall back to slow CPU).
    _default_workers = "1" if yolo_device != "cpu" else str(max(1, min(8, (os.cpu_count() or 2) - 1)))
    max_workers = int(os.getenv("POTHOLE_MAX_WORKERS", _default_workers))
    MAX_SAVED_FRAMES = int(os.getenv("MAX_SAVED_FRAMES", "500"))
    frame_stride = _resolve_frame_stride(capture_mode)
    write_stride = _resolve_write_stride(frame_stride)
    write_video = _want_annotated_video()
    _limit_native_threads()
    parallel_flag = os.getenv("POTHOLE_PARALLEL", "").strip().lower()
    if yolo_device != "cpu":
        # Never fork after CUDA init — use single-process GPU path.
        want_parallel = False
    elif parallel_flag in ("0", "false", "no"):
        want_parallel = False
    elif parallel_flag in ("1", "true", "yes"):
        want_parallel = True
    else:
        want_parallel = True  # CPU default: parallel helped
    use_parallel = (
        want_parallel
        and max_workers > 1
        and total_frames > 0
        and video_seconds > chunk_seconds * 1.5
    )
    print(
        f"[video] {total_frames} frames @ {fps:.1f}fps (~{video_seconds:.0f}s) | "
        f"device={yolo_device} frame_stride={frame_stride} write_stride={write_stride} "
        f"annotated={write_video} | parallel={use_parallel} workers<={max_workers}"
    )

    frames_dir = os.path.join(output_dir, f"frames_{run_id}")
    os.makedirs(frames_dir, exist_ok=True)
    frames_zip_path: str = ""

    if use_parallel:
        try:
            out_path, unique_rows, saved_frames = _process_video_parallel(
                input_path=input_path, output_dir=output_dir, run_id=run_id, fps=fps,
                total_frames=total_frames, capture_mode=capture_mode, gps_map=gps_map,
                gps_second_offset=gps_second_offset, base_lat=base_lat, base_lon=base_lon,
                base_captured_at=base_captured_at, max_saved_frames=MAX_SAVED_FRAMES,
                chunk_seconds=chunk_seconds, overlap_seconds=overlap_seconds, max_workers=max_workers,
                frame_stride=frame_stride,
            )
            # Chunk crop images live in per-chunk folders (frames_<run_id>_seg000, ...);
            # gather the ones that survived merging/capping into one folder to zip,
            # same as the serial path's single frames_dir.
            # Built incrementally (source path -> dest path) rather than
            # zip(saved_frames, gathered) — if any source file is missing
            # (skipped below), a zip of the two lists would silently misalign
            # every entry after it, attaching the wrong crop to a pothole.
            gathered = []
            path_map = {}
            for fp in saved_frames:
                if not fp or not os.path.exists(fp):
                    continue
                idx = len(gathered) + 1
                dst = os.path.join(frames_dir, f"frame_{idx:03d}.jpg")
                os.replace(fp, dst)
                gathered.append(dst)
                path_map[fp] = dst
                stem, ext = os.path.splitext(fp)
                clean_src = f"{stem}_clean{ext}"
                if os.path.isfile(clean_src):
                    clean_dst = os.path.join(frames_dir, f"frame_{idx:03d}_clean.jpg")
                    try:
                        os.replace(clean_src, clean_dst)
                    except OSError:
                        pass
            for r in unique_rows:
                old = getattr(r, "local_frame_path", "")
                if not old:
                    continue
                # Drop the reference rather than leaving it pointing at a path
                # that either never existed or was already moved above.
                r.local_frame_path = path_map.get(old, "")
            saved_frames = gathered
        except Exception as e:
            print(f"[video] parallel chunked processing failed, falling back to serial: {e}")
            use_parallel = False

    if not use_parallel:
        model = load_model()
        cap = _open_video_capture(input_path)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        out_path = os.path.join(output_dir, "annotated.mp4")
        writer = None
        wrote_h264 = False
        if write_video:
            writer, wrote_h264 = _open_video_writer(out_path, max(1.0, fps / write_stride), (w, h))

        saved_frames: List[str] = []
        saved_track_ids: set = set()   # one frame per unique track
        track_to_frame: Dict[int, str] = {}

        frame_index = 0
        tracks: List[Track] = []
        next_track_id = 1

        capture_mode = (capture_mode or "walking").strip().lower()
        if capture_mode not in ("walking", "vehicle"):
            capture_mode = "walking"

        if capture_mode == "walking":
            MAX_TRACK_GAP_FRAMES = int(fps * 4)
            MATCH_DIST_PX = 220
            MIN_CONF = 0.35
            MIN_HITS = max(1, int(math.ceil(3 / max(1, frame_stride))))
        else:
            MAX_TRACK_GAP_FRAMES = int(fps * 2)
            MATCH_DIST_PX = 260
            MIN_CONF = 0.45
            MIN_HITS = max(1, int(math.ceil(2 / max(1, frame_stride))))

        def _get_frame_gps(frame_second_0: int):
            lat, lon, ts = base_lat, base_lon, base_captured_at
            if gps_map:
                lat2, lon2, ts2 = _gps_for_frame_second(frame_second_0, gps_map, gps_second_offset=gps_second_offset)
                if lat2 is not None and lon2 is not None:
                    lat, lon = lat2, lon2
                if ts2:
                    ts = ts2
            return lat, lon, ts

        while True:
            need_infer = ((frame_index) % frame_stride) == 0
            need_write = bool(writer) and ((frame_index) % write_stride) == 0
            if not need_infer and not need_write:
                # Cheap skip — avoid full decode+BGR convert when unused.
                if not cap.grab():
                    break
                frame_index += 1
                continue

            ok, frame = cap.read()
            if not ok:
                break
            frame_index += 1

            if need_infer:
                rows = _infer_on_frame(model, frame)
            else:
                rows = []

            frame_second_0 = int((frame_index - 1) / fps) if fps else 0
            frame_lat, frame_lon, frame_ts = _get_frame_gps(frame_second_0)

            rows = [r for r in rows if r.conf >= MIN_CONF]

            row_track_ids: List[int] = []
            for r in rows:
                r.lat = frame_lat
                r.lon = frame_lon
                r.captured_at = frame_ts
                r.gps_second = frame_second_0
                rc = _center(r)

                best_t = None
                best_d = 1e9
                for t in tracks:
                    if t.cls != r.cls:
                        continue
                    if (frame_index - t.last_seen_frame) > MAX_TRACK_GAP_FRAMES:
                        continue
                    d = _dist((t.last_cx, t.last_cy), rc)
                    if d < best_d:
                        best_d = d
                        best_t = t

                if best_t is not None and best_d <= MATCH_DIST_PX:
                    best_t.last_cx, best_t.last_cy = rc
                    best_t.last_seen_frame = frame_index
                    best_t.hits += 1
                    if r.lat is not None and r.lon is not None:
                        best_t.gps_count += 1
                        best_t.lat_sum += float(r.lat)
                        best_t.lon_sum += float(r.lon)
                        best_t.captured_at = r.captured_at or best_t.captured_at
                    if r.conf > best_t.best_conf:
                        best_t.best_conf = r.conf
                        best_t.best_row = r
                    row_track_ids.append(best_t.track_id)
                else:
                    t = Track(
                        track_id=next_track_id,
                        cls=r.cls,
                        best_row=r,
                        best_conf=r.conf,
                        last_cx=rc[0],
                        last_cy=rc[1],
                        last_seen_frame=frame_index,
                        hits=1,
                    )
                    next_track_id += 1
                    if r.lat is not None and r.lon is not None:
                        t.gps_count = 1
                        t.lat_sum = float(r.lat)
                        t.lon_sum = float(r.lon)
                        t.captured_at = r.captured_at
                    tracks.append(t)
                    row_track_ids.append(t.track_id)

            # Snapshot before overlays so Admin zip can ship with_overlay + without_overlay.
            will_save_track = any(
                tid not in saved_track_ids for tid in row_track_ids
            ) and len(saved_frames) < MAX_SAVED_FRAMES
            clean_snapshot = frame.copy() if will_save_track else None

            for r in rows:
                r.map_link = maps_link(r.lat, r.lon)
                if need_write:
                    _draw_box(frame, r)
            if need_write and writer is not None:
                writer.write(frame)

            # Save one annotated frame per unique track (one image per pothole)
            for r, tid in zip(rows, row_track_ids):
                if tid not in saved_track_ids and len(saved_frames) < MAX_SAVED_FRAMES:
                    saved_track_ids.add(tid)
                    idx = len(saved_frames) + 1
                    frame_path = os.path.join(frames_dir, f"frame_{idx:03d}.jpg")
                    cv2.imwrite(frame_path, frame)
                    if clean_snapshot is not None:
                        cv2.imwrite(
                            os.path.join(frames_dir, f"frame_{idx:03d}_clean.jpg"),
                            clean_snapshot,
                        )
                    saved_frames.append(frame_path)
                    track_to_frame[tid] = frame_path

        cap.release()
        if writer is not None:
            writer.release()
            if not wrote_h264 and _want_h264_transcode():
                out_path = _transcode_to_h264(out_path)
        else:
            # Placeholder so downstream upload/UI paths don't break.
            out_path = os.path.join(output_dir, "annotated.mp4")
            try:
                open(out_path, "wb").close()
            except OSError:
                pass

        unique_rows: List[DetectionRow] = []
        for t in tracks:
            if t.hits < MIN_HITS:
                continue
            r = t.best_row
            if t.gps_count > 0:
                r.lat = t.lat_sum / t.gps_count
                r.lon = t.lon_sum / t.gps_count
                r.captured_at = t.captured_at or r.captured_at
            r.map_link = maps_link(r.lat, r.lon)
            r.local_frame_path = track_to_frame.get(t.track_id, "")
            unique_rows.append(r)
        unique_rows.sort(key=lambda x: (x.severity, x.conf), reverse=True)
    else:
        # Parallel path already returns an H.264 file from ffmpeg concat.
        pass

    # ----- Upload outputs to processed bucket (shared by both paths) -----
    annotated_video_url = ""
    if out_path and os.path.isfile(out_path) and os.path.getsize(out_path) > 1000:
        annotated_video_url = _safe_upload(out_path, f"{s3_run_prefix}/outputs/annotated.mp4")

    for r in unique_rows:
        if getattr(r, "local_frame_path", "") and os.path.exists(r.local_frame_path):
            base = os.path.basename(r.local_frame_path)
            url = _safe_upload(r.local_frame_path, f"{s3_run_prefix}/outputs/frames/{base}")
            r.s3_url = url or annotated_video_url
            stem, ext = os.path.splitext(base)
            clean_local = os.path.join(os.path.dirname(r.local_frame_path), f"{stem}_clean{ext}")
            if os.path.isfile(clean_local):
                _safe_upload(clean_local, f"{s3_run_prefix}/outputs/frames_clean/{base}")
        else:
            r.s3_url = annotated_video_url

    if saved_frames:
        frames_zip_path = os.path.join(output_dir, f"frames_{run_id}.zip")
        _zip_folder(frames_dir, frames_zip_path)
        _safe_upload(frames_zip_path, f"{s3_run_prefix}/outputs/frames_{run_id}.zip")

    # ----- MOVE source from input bucket -> processed bucket on success -----
    if s3_input_key and _move_on_success():
        try:
            from routes.field_upload_service import processed_source_key
            dst_key = processed_source_key(s3_input_key)
            move_object(src_bucket, s3_input_key, get_processed_bucket(), dst_key)
            # Archive sibling GPS / JSON next to the video under the same route folder.
            sibling_key = None
            try:
                sibling_key = find_sibling_gps_key(src_bucket, s3_input_key)
            except Exception:
                sibling_key = None
            if sibling_key:
                try:
                    copy_object(
                        src_bucket, sibling_key, get_processed_bucket(),
                        processed_source_key(sibling_key),
                    )
                except Exception as e:
                    print(f"[S3] sibling archive-copy failed for {sibling_key}: {e}")
            try:
                from s3_utils import find_sibling_json_key
                json_key = find_sibling_json_key(src_bucket, s3_input_key)
                if json_key and json_key != sibling_key:
                    copy_object(
                        src_bucket, json_key, get_processed_bucket(),
                        processed_source_key(json_key),
                    )
            except Exception as e:
                print(f"[S3] json archive-copy failed: {e}")
        except Exception as e:
            print(f"[S3] move failed: {e}")

    # ── Save to PostgreSQL if configured ──────────────────────────────────────
    try:
        from db_utils import is_db_configured, init_db, save_session, save_potholes
        if is_db_configured():
            init_db()
            gps_extras_map = {}
            if gps_log_path and gps_log_path.lower().endswith(".json"):
                gps_extras_map = _load_gps_extras_json(gps_log_path)
            _uploader_name, _uploader_uid, _uploader_legacy = "Legacy", None, True
            try:
                from routes.report_service import resolve_uploader
                _uploader_name, _uploader_uid, _uploader_legacy = resolve_uploader(s3_input_key or "")
            except Exception as _up_err:
                print(f"[DB] uploader resolve skipped: {_up_err}")
            session_id = save_session(
                filename=os.path.basename(input_path),
                s3_key=s3_input_key or "",
                run_id=run_id,
                total_potholes=len(unique_rows),
                user_id=_uploader_uid,
                username=_uploader_name,
                is_legacy=_uploader_legacy,
            )
            # Set friendly name immediately so Admin never flashes the sha256 key.
            try:
                from routes.report_service import build_display_names
                from db_utils import update_session_report_meta

                dn, _ = build_display_names(
                    username=_uploader_name or "user",
                    session_id=int(session_id),
                )
                update_session_report_meta(
                    int(session_id),
                    filename=dn,
                    display_name=dn,
                    username=_uploader_name,
                    user_id=_uploader_uid,
                    is_legacy=_uploader_legacy,
                )
            except Exception as _dn_err:
                print(f"[DB] early display_name skipped: {_dn_err}")
            # City/state: do NOT Nominatim here (rate-limit stalls detection).
            # Report generation / backfill enrich asynchronously with a small cap.
            save_potholes(session_id, unique_rows, gps_extras_map)
            print(f"[DB] Session {session_id} saved — {len(unique_rows)} potholes")
            try:
                from routes import report_service
                rep = report_service.generate_report_for_session(session_id, force=True)
                print(f"[report] {rep}")
            except Exception as _rep_err:
                print(f"[report] generation failed (non-fatal): {_rep_err}")
    except Exception as _db_err:
        print(f"[DB] Save failed (non-fatal): {_db_err}")

    set_active_input_rotation(0)
    return input_path, out_path, unique_rows, saved_frames, frames_zip_path
