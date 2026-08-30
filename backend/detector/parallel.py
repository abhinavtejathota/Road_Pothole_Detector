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
import detector.types as _types
import detector.track as _track
import detector.video_io as _video_io
import detector.gps as _gps

reexport(_types, globals())
reexport(_track, globals())
reexport(_video_io, globals())
reexport(_gps, globals())

# ============================================================
# Parallel chunked video processing (CPU multiprocessing)
# ============================================================
#
# Why chunking isn't a plain "split the file N ways" job: the code above
# tracks each pothole across consecutive frames (Track objects) so one
# pothole seen in 40 frames is reported once, not 40 times. If you split
# the video into independent pieces, a pothole straddling a cut point
# would get counted twice - once as each neighboring chunk's tracker
# "discovers" it. The scheme below avoids that:
#
#   - Each worker reads its chunk PLUS a little padding on both sides
#     (POTHOLE_CHUNK_OVERLAP_SECONDS) purely to give its own tracker
#     context near the boundary.
#   - Each worker only WRITES output frames / saves crop images for its
#     own core (non-overlapping) region, so concatenating chunk videos in
#     order reproduces the original video exactly - no gaps, no repeats.
#   - Each worker still applies the full track/MIN_HITS logic over its
#     padded read window, so a pothole that only exists near a boundary
#     can independently reach both neighbors.
#   - After merging, `_dedupe_boundary_rows` collapses same-object
#     duplicates that can result from two neighboring chunks
#     independently detecting the same real-world pothole in the padding
#     they share. Only rows tagged near a boundary are even considered,
#     so this can't accidentally merge two distinct potholes elsewhere
#     in the video.

def _segment_ranges(total_frames: int, fps: float, chunk_seconds: float, overlap_seconds: float) -> List[Dict]:
    """
    Frame numbers are 0-based, ranges are half-open [start, end).
    core_start/core_end: frames THIS chunk owns for output video + saved crops
                          (chunks never overlap here - concatenation is exact).
    read_start/read_end: frames THIS chunk actually reads (core + padding on
                          both sides) so its tracker has boundary context.
    """
    chunk_frames = max(1, int(round(chunk_seconds * fps)))
    overlap_frames = max(0, int(round(overlap_seconds * fps)))
    segments = []
    start = 0
    idx = 0
    while start < total_frames:
        core_end = min(start + chunk_frames, total_frames)
        segments.append({
            "chunk_id": idx,
            "core_start": start,
            "core_end": core_end,
            "read_start": max(0, start - overlap_frames),
            "read_end": min(total_frames, core_end + overlap_frames),
        })
        start = core_end
        idx += 1
    return segments


def _process_segment_worker(args: Dict) -> Dict:
    """
    Runs entirely inside a worker process (own model instance - safe here
    since inference is CPU-only, so there's no GPU contention between
    workers). Returns only plain, picklable data (paths, dataclass rows,
    lists) - never the model or any cv2 object.
    """
    input_path = args["input_path"]
    output_dir = args["output_dir"]
    run_id = args["run_id"]
    seg = args["seg"]
    fps = args["fps"]
    capture_mode = args["capture_mode"]
    gps_map = args["gps_map"]
    gps_second_offset = args["gps_second_offset"]
    base_lat = args["base_lat"]
    base_lon = args["base_lon"]
    base_captured_at = args["base_captured_at"]
    max_saved_frames = args["max_saved_frames"]
    frame_stride = max(1, int(args.get("frame_stride") or 1))

    model = load_model()

    cap = _open_video_capture(input_path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, seg["read_start"])
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    seg_out_path = os.path.join(output_dir, f"seg_{seg['chunk_id']:03d}_{run_id}.mp4")
    writer, _ = _open_video_writer(seg_out_path, fps, (w, h))

    frames_dir = os.path.join(output_dir, f"frames_{run_id}_seg{seg['chunk_id']:03d}")
    os.makedirs(frames_dir, exist_ok=True)

    capture_mode = (capture_mode or "walking").strip().lower()
    if capture_mode not in ("walking", "vehicle"):
        capture_mode = "walking"
    if capture_mode == "walking":
        MAX_TRACK_GAP_FRAMES = int(fps * 4); MATCH_DIST_PX = 220; MIN_CONF = 0.35; MIN_HITS = 3
    else:
        MAX_TRACK_GAP_FRAMES = int(fps * 2); MATCH_DIST_PX = 260; MIN_CONF = 0.45; MIN_HITS = 2
    try:
        _rot = int(os.getenv("POTHOLE_INPUT_ROTATION") or "0") % 360
    except (TypeError, ValueError):
        _rot = 0
    if _rot in (90, 180, 270):
        MIN_CONF = min(0.85, MIN_CONF + 0.08)

    def _get_frame_gps(frame_second_0):
        lat, lon, ts = base_lat, base_lon, base_captured_at
        if gps_map:
            lat2, lon2, ts2 = _gps_for_frame_second(frame_second_0, gps_map, gps_second_offset=gps_second_offset)
            if lat2 is not None and lon2 is not None:
                lat, lon = lat2, lon2
            if ts2:
                ts = ts2
        return lat, lon, ts

    tracks: List[Track] = []
    next_track_id = 1
    saved_frames: List[str] = []
    saved_track_ids: set = set()
    chunk_track_to_frame: Dict[int, str] = {}

    frame_index = seg["read_start"]  # count of frames already consumed globally
    p = seg["read_start"]
    while p < seg["read_end"]:
        ok, frame = cap.read()
        if not ok:
            break
        frame_index += 1  # 1-based global frame number (matches serial-path semantics)
        p += 1

        # Skip YOLO on non-stride frames (still decode + write for smooth annotated video).
        if ((frame_index - 1) % frame_stride) != 0:
            rows = []
        else:
            rows = _infer_on_frame(model, frame)
        frame_second_0 = int((frame_index - 1) / fps) if fps else 0
        frame_lat, frame_lon, frame_ts = _get_frame_gps(frame_second_0)
        rows = [r for r in rows if r.conf >= MIN_CONF]

        row_track_ids: List[int] = []
        for r in rows:
            r.lat, r.lon, r.captured_at, r.gps_second = frame_lat, frame_lon, frame_ts, frame_second_0
            rc = _center(r)
            best_t, best_d = None, 1e9
            for t in tracks:
                if t.cls != r.cls:
                    continue
                if (frame_index - t.last_seen_frame) > MAX_TRACK_GAP_FRAMES:
                    continue
                d = _dist((t.last_cx, t.last_cy), rc)
                if d < best_d:
                    best_d, best_t = d, t
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
                    best_t.best_conf, best_t.best_row = r.conf, r
                row_track_ids.append(best_t.track_id)
            else:
                t = Track(track_id=next_track_id, cls=r.cls, best_row=r, best_conf=r.conf,
                          last_cx=rc[0], last_cy=rc[1], last_seen_frame=frame_index, hits=1)
                next_track_id += 1
                if r.lat is not None and r.lon is not None:
                    t.gps_count, t.lat_sum, t.lon_sum, t.captured_at = 1, float(r.lat), float(r.lon), r.captured_at
                tracks.append(t)
                row_track_ids.append(t.track_id)

        # Only this chunk's OWN (non-overlapping) region gets written out /
        # saved as crops - the padding is tracker-context only.
        in_core = seg["core_start"] <= (frame_index - 1) < seg["core_end"]
        if in_core:
            will_save = any(tid not in saved_track_ids for tid in row_track_ids) and len(saved_frames) < max_saved_frames
            clean_snapshot = frame.copy() if will_save else None
            for r in rows:
                r.map_link = maps_link(r.lat, r.lon)
                _draw_box(frame, r)
            writer.write(frame)
            for r, tid in zip(rows, row_track_ids):
                if tid not in saved_track_ids and len(saved_frames) < max_saved_frames:
                    saved_track_ids.add(tid)
                    idx = len(saved_frames) + 1
                    fp = os.path.join(frames_dir, f"frame_{idx:03d}.jpg")
                    cv2.imwrite(fp, frame)
                    if clean_snapshot is not None:
                        cv2.imwrite(os.path.join(frames_dir, f"frame_{idx:03d}_clean.jpg"), clean_snapshot)
                    saved_frames.append(fp)
                    chunk_track_to_frame[tid] = fp

    cap.release()
    writer.release()

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
        # Tag each row with the crop image (if any) captured for its track,
        # so the caller can upload/attach it without needing global track IDs.
        r.local_frame_path = chunk_track_to_frame.get(t.track_id, "")
        unique_rows.append(r)

    return {
        "chunk_id": seg["chunk_id"],
        "seg_video_path": seg_out_path if os.path.exists(seg_out_path) else "",
        "rows": unique_rows,
        "boundary_seconds": [seg["core_start"] / fps if fps else 0, seg["core_end"] / fps if fps else 0],
    }


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _dedupe_boundary_rows(all_rows: List[DetectionRow], boundary_seconds: List[float],
                           overlap_seconds: float, gps_merge_radius_m: float = 12.0) -> List[DetectionRow]:
    """
    Collapses duplicate detections of the same real-world pothole that can
    arise when two neighboring chunks both see it in their shared padding.
    Only rows within `overlap_seconds` of an actual chunk boundary are
    considered at all - everything else is interior to exactly one chunk's
    read window and is already unique.
    """
    if not boundary_seconds:
        return all_rows

    near, interior = [], []
    for r in all_rows:
        is_near = any(abs(r.gps_second - b) <= overlap_seconds for b in boundary_seconds)
        (near if is_near else interior).append(r)

    used = [False] * len(near)
    merged: List[DetectionRow] = []
    for i, r1 in enumerate(near):
        if used[i]:
            continue
        used[i] = True
        match_j = None
        for j in range(i + 1, len(near)):
            if used[j] or near[j].cls != r1.cls:
                continue
            r2 = near[j]
            same_boundary = any(
                abs(r1.gps_second - b) <= overlap_seconds and abs(r2.gps_second - b) <= overlap_seconds
                for b in boundary_seconds
            )
            if not same_boundary:
                continue
            if None not in (r1.lat, r1.lon, r2.lat, r2.lon):
                if _haversine_m(r1.lat, r1.lon, r2.lat, r2.lon) <= gps_merge_radius_m:
                    match_j = j
                    break
            elif abs(r1.gps_second - r2.gps_second) <= overlap_seconds:
                # No GPS to confirm with - fall back to time-proximity only.
                match_j = j
                break
        if match_j is not None:
            used[match_j] = True
            r2 = near[match_j]
            merged.append(r1 if r1.conf >= r2.conf else r2)
        else:
            merged.append(r1)

    return interior + merged


def _concat_segment_videos(seg_paths: List[str], out_path: str) -> bool:
    """Concatenate chunk mp4s (already in chunk order) into one file via
    ffmpeg's concat demuxer, re-encoding to H.264 for browser playback."""
    seg_paths = [p for p in seg_paths if p and os.path.exists(p)]
    if not seg_paths:
        return False
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:
        print(f"[video] imageio-ffmpeg unavailable, cannot concatenate chunks: {e}")
        return False

    list_path = out_path + ".concat.txt"
    with open(list_path, "w") as f:
        for p in seg_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
    try:
        subprocess.run(
            [ffmpeg_exe, "-y", "-f", "concat", "-safe", "0", "-i", list_path,
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", "-an", out_path],
            check=True, capture_output=True, timeout=1800,
        )
        return True
    except Exception as e:
        print(f"[video] chunk concat failed: {e}")
        return False
    finally:
        try:
            os.remove(list_path)
        except OSError:
            pass


def _process_video_parallel(
    input_path: str, output_dir: str, run_id: str, fps: float, total_frames: int,
    capture_mode: str, gps_map: Dict, gps_second_offset: int,
    base_lat: Optional[float], base_lon: Optional[float], base_captured_at: str,
    max_saved_frames: int, chunk_seconds: float, overlap_seconds: float, max_workers: int,
    frame_stride: int = 1,
) -> Tuple[str, List["DetectionRow"], List[str]]:
    """
    Orchestrates the chunked multiprocess pipeline. Returns
    (annotated_video_path, unique_rows, saved_frame_paths) - the same
    shape the serial path produces, so the caller doesn't need to care
    which path ran.
    """
    segments = _segment_ranges(total_frames, fps, chunk_seconds, overlap_seconds)
    worker_args = [
        {
            "input_path": input_path, "output_dir": output_dir, "run_id": run_id, "seg": seg,
            "fps": fps, "capture_mode": capture_mode, "gps_map": gps_map,
            "gps_second_offset": gps_second_offset, "base_lat": base_lat, "base_lon": base_lon,
            "base_captured_at": base_captured_at, "max_saved_frames": max_saved_frames,
            "frame_stride": max(1, int(frame_stride or 1)),
        }
        for seg in segments
    ]

    results_by_chunk: Dict[int, Dict] = {}
    # Linux default start method is "fork", which breaks CUDA after the parent
    # has touched the GPU. Always spawn for parallel video workers.
    import multiprocessing as mp
    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=min(max_workers, len(segments)),
        mp_context=ctx,
    ) as ex:
        futures = {ex.submit(_process_segment_worker, a): a["seg"]["chunk_id"] for a in worker_args}
        for fut in as_completed(futures):
            chunk_id = futures[fut]
            results_by_chunk[chunk_id] = fut.result()

    ordered = [results_by_chunk[i] for i in sorted(results_by_chunk)]

    seg_video_paths = [r["seg_video_path"] for r in ordered]
    out_path = os.path.join(output_dir, "annotated.mp4")
    if not _concat_segment_videos(seg_video_paths, out_path):
        # Fall back to whichever chunk video exists so we still return something playable.
        out_path = next((p for p in seg_video_paths if p), "")

    all_rows: List[DetectionRow] = []
    boundary_seconds: List[float] = []
    for r in ordered:
        all_rows.extend(r["rows"])
        boundary_seconds.extend(r["boundary_seconds"])
    # Boundaries are shared between neighbors (chunk i's core_end == chunk i+1's core_start),
    # so de-duplicate the list itself before using it for row matching.
    boundary_seconds = sorted(set(round(b, 3) for b in boundary_seconds))

    unique_rows = _dedupe_boundary_rows(all_rows, boundary_seconds, overlap_seconds)
    unique_rows.sort(key=lambda x: (x.severity, x.conf), reverse=True)

    saved_frames = [r.local_frame_path for r in unique_rows if getattr(r, "local_frame_path", "")]
    # Respect the global cap after merging (each chunk already capped itself locally).
    if len(saved_frames) > max_saved_frames:
        keep = set(saved_frames[:max_saved_frames])
        saved_frames = [p for p in saved_frames if p in keep]
        for r in unique_rows:
            if getattr(r, "local_frame_path", "") and r.local_frame_path not in keep:
                r.local_frame_path = ""

    for seg_path in seg_video_paths:
        if seg_path and seg_path != out_path and os.path.exists(seg_path):
            try:
                os.remove(seg_path)
            except OSError:
                pass

    return out_path, unique_rows, saved_frames


