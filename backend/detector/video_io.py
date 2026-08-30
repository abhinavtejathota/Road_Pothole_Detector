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



# ============================================================
# Public entry point
# ============================================================
def _s3_public_flag() -> bool:
    return os.getenv("S3_PUBLIC", "").lower() in ("1", "true", "yes")


def _move_on_success() -> bool:
    return os.getenv("S3_MOVE_ON_SUCCESS", "true").lower() in ("1", "true", "yes")


def _safe_upload(local_path: str, key: str) -> str:
    if not is_s3_configured() or not local_path or not os.path.exists(local_path):
        return ""
    try:
        _, url = upload_and_link(local_path, key, bucket=get_processed_bucket(), public=_s3_public_flag())
        return url
    except Exception as e:
        print(f"[S3] upload failed for {local_path}: {e}")
        return ""


def _open_video_writer(out_path: str, fps: float, size: Tuple[int, int]) -> Tuple[cv2.VideoWriter, bool]:
    """Open a VideoWriter, preferring a codec browsers can play natively.

    Returns (writer, wrote_h264). Most opencv-python builds can't write H.264
    directly (needs Cisco's OpenH264, which pip can't bundle for licensing
    reasons) and silently fall back to 'mp4v' (MPEG-4 Part 2) — a file that
    processes fine but that Chrome/Firefox/Safari refuse to play in a <video>
    tag. When we land on 'mp4v', the caller re-encodes with _transcode_to_h264.

    On headless cloud GPUs, asking for ``avc1`` often selects FFmpeg's
    ``h264_v4l2m2m`` (V4L2 mem2mem) which then fails with "can't configure
    encoder" / error -22 — no such device on AceCloud/VMs. Prefer software
    ``mp4v`` first; we already transcode to libx264 for browser playback.
    """
    # Env override: comma-separated fourcc list, e.g. "mp4v,avc1"
    raw = (os.getenv("POTHOLE_VIDEO_FOURCC") or "").strip()
    if raw:
        candidates = [c.strip() for c in raw.split(",") if c.strip()]
    else:
        candidates = ["mp4v", "MJPG", "XVID", "avc1"]
    last_err = None
    for fourcc_str in candidates:
        if len(fourcc_str) != 4:
            continue
        try:
            fourcc = cv2.VideoWriter_fourcc(*fourcc_str)
            writer = cv2.VideoWriter(out_path, fourcc, fps, size)
            if writer.isOpened():
                return writer, fourcc_str.lower() in ("avc1", "h264")
            writer.release()
        except Exception as e:
            last_err = e
    raise RuntimeError(
        f"Could not open video writer for {out_path} with any of {candidates}"
        + (f" ({last_err})" if last_err else "")
    )


def _transcode_to_h264(path: str) -> str:
    """Re-encode an mp4v output to H.264 in place so it plays in <video> tags.

    Prefer NVIDIA NVDEC decode + NVENC encode when a system ffmpeg supports it
    (AceCloud A6000); else imageio-ffmpeg / libx264 on CPU. Best-effort.
    """
    try:
        from ffmpeg_accel import (
            accel_label,
            ffmpeg_exe,
            h264_encode_args,
            hwaccel_decode_args,
        )

        ffmpeg_bin = ffmpeg_exe()
        encode = h264_encode_args(audio=False)
        decode = hwaccel_decode_args()
    except Exception as e:
        print(f"[video] ffmpeg unavailable, keeping mp4v output (won't preview in-browser): {e}")
        return path

    h264_path = path[:-4] + "_h264.mp4" if path.lower().endswith(".mp4") else path + ".h264.mp4"
    try:
        print(f"[video] H.264 transcode via {accel_label()}", flush=True)
        subprocess.run(
            [ffmpeg_bin, "-y", *decode, "-i", path, *encode, h264_path],
            check=True, capture_output=True, timeout=900,
        )
        os.replace(h264_path, path)
    except Exception as e:
        try:
            from ffmpeg_accel import h264_encode_args as _enc

            # Retry: software decode + NVENC, then full CPU
            print(f"[video] GPU decode/encode failed ({e}); retrying without NVDEC", flush=True)
            encode2 = _enc(audio=False)
            subprocess.run(
                [ffmpeg_bin, "-y", "-i", path, *encode2, h264_path],
                check=True, capture_output=True, timeout=900,
            )
            os.replace(h264_path, path)
        except Exception as e2:
            try:
                from ffmpeg_accel import h264_encode_args as _enc

                cpu = _enc(audio=False, force_cpu=True)
                print(f"[video] NVENC failed ({e2}); retrying libx264", flush=True)
                subprocess.run(
                    [ffmpeg_bin, "-y", "-i", path, *cpu, h264_path],
                    check=True, capture_output=True, timeout=900,
                )
                os.replace(h264_path, path)
            except Exception as e3:
                print(f"[video] H.264 transcode failed, keeping mp4v output: {e3}")
                if os.path.exists(h264_path):
                    try:
                        os.remove(h264_path)
                    except OSError:
                        pass
    return path


def _open_video_capture(input_path: str) -> cv2.VideoCapture:
    """Open video; request HW decode when OpenCV/FFmpeg build supports it.

    Most pip ``opencv-python`` builds still decode on CPU — NVDEC mainly helps
    the ffmpeg transcode path. This hint is best-effort and never required.
    """
    cap = cv2.VideoCapture(input_path)
    try:
        # OpenCV 4.5+ ; ignored if backend has no HW accel
        if hasattr(cv2, "CAP_PROP_HW_ACCELERATION") and hasattr(cv2, "VIDEO_ACCELERATION_ANY"):
            cap.set(cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_ANY)
    except Exception:
        pass
    return cap


def normalize_rotation_deg(value) -> int:
    """Accept 0/90/180/270 only (clockwise). Anything else → 0."""
    try:
        d = int(value)
    except (TypeError, ValueError):
        return 0
    d %= 360
    return d if d in (0, 90, 180, 270) else 0


def _ffmpeg_transpose_filter(rotation_deg: int) -> str:
    """Map clockwise degrees to ffmpeg transpose chain (raw bitstream, no autorotate)."""
    if rotation_deg == 90:
        return "transpose=1,setsar=1"
    if rotation_deg == 180:
        return "transpose=1,transpose=1,setsar=1"
    if rotation_deg == 270:
        return "transpose=2,setsar=1"
    return ""


def bake_display_orientation(input_path: str) -> str:
    """Bake phone/container display-matrix into pixels so OpenCV matches the browser.

    OpenCV ignores rotate/displaymatrix tags; browsers apply them. Without this,
    YOLO often sees a sideways frame (mirrors/handlebars look like potholes).

    Disable with ``POTHOLE_BAKE_ORIENTATION=0``.
    """
    raw = (os.getenv("POTHOLE_BAKE_ORIENTATION") or "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return input_path
    if not input_path or not os.path.isfile(input_path):
        return input_path
    ext_l = os.path.splitext(input_path)[1].lower()
    if ext_l not in (".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"):
        return input_path

    stem, _ext = os.path.splitext(input_path)
    out_mp4 = f"{stem}_orient.mp4"
    if os.path.isfile(out_mp4) and os.path.getsize(out_mp4) > 32:
        # Reuse prior bake in the same temp download dir.
        return out_mp4

    try:
        from ffmpeg_accel import ffmpeg_exe, h264_encode_args

        ffmpeg_bin = ffmpeg_exe()
        encode = h264_encode_args(audio=False)
    except Exception as e:
        print(f"[detect] skip orientation bake (ffmpeg unavailable): {e}", flush=True)
        return input_path

    # Default ffmpeg decode APPLIES display rotation; write pixels upright, clear tag.
    cmd = [
        ffmpeg_bin, "-y", "-i", input_path,
        "-vf", "setsar=1",
        "-metadata:s:v:0", "rotate=0",
        *encode,
        out_mp4,
    ]
    try:
        print("[detect] baking display orientation into pixels...", flush=True)
        subprocess.run(cmd, check=True, capture_output=True, timeout=900)
    except Exception as e:
        try:
            from ffmpeg_accel import h264_encode_args as _enc

            encode2 = _enc(audio=False, force_cpu=True)
            subprocess.run(
                [
                    ffmpeg_bin, "-y", "-i", input_path,
                    "-vf", "setsar=1",
                    "-metadata:s:v:0", "rotate=0",
                    *encode2,
                    out_mp4,
                ],
                check=True,
                capture_output=True,
                timeout=900,
            )
        except Exception as e2:
            print(f"[detect] orientation bake failed (using original): {e2}", flush=True)
            return input_path
    if not os.path.isfile(out_mp4) or os.path.getsize(out_mp4) < 32:
        return input_path
    print(f"[detect] orientation baked -> {out_mp4}", flush=True)
    return out_mp4


def apply_input_rotation(input_path: str, rotation_deg: int = 0) -> str:
    """Bake clockwise rotation into pixels before OpenCV/YOLO reads the file.

    Returns ``input_path`` unchanged when rotation is 0. Otherwise writes a
    sibling ``*_rot{deg}.*`` file and returns that path.

    Uses ``-noautorotate`` so phone display-matrix metadata is not applied on
    top of the explicit UI angle (call ``bake_display_orientation`` first).
    """
    deg = normalize_rotation_deg(rotation_deg)
    if deg == 0 or not input_path:
        return input_path
    if not os.path.isfile(input_path):
        raise ValueError(f"Cannot rotate missing file: {input_path}")

    stem, ext = os.path.splitext(input_path)
    ext_l = (ext or "").lower()
    is_video = ext_l in (".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v")
    out_path = f"{stem}_rot{deg}{ext or ('.mp4' if is_video else '.jpg')}"

    if not is_video:
        img = cv2.imread(input_path)
        if img is None:
            raise ValueError(f"Could not read image for rotation: {input_path}")
        if deg == 90:
            img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        elif deg == 180:
            img = cv2.rotate(img, cv2.ROTATE_180)
        else:
            img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
        if not cv2.imwrite(out_path, img):
            raise RuntimeError(f"Failed to write rotated image: {out_path}")
        print(f"[detect] input rotated {deg} deg CW -> {out_path}", flush=True)
        return out_path

    vf = _ffmpeg_transpose_filter(deg)
    try:
        from ffmpeg_accel import ffmpeg_exe, h264_encode_args

        ffmpeg_bin = ffmpeg_exe()
        encode = h264_encode_args(audio=False)
    except Exception as e:
        raise RuntimeError(
            f"ffmpeg required to rotate video {deg} deg before detection: {e}"
        ) from e

    out_mp4 = f"{stem}_rot{deg}.mp4"
    cmd = [
        ffmpeg_bin, "-y", "-noautorotate", "-i", input_path,
        "-vf", vf,
        "-metadata:s:v:0", "rotate=0",
        *encode,
        out_mp4,
    ]
    try:
        print(f"[detect] rotating video {deg} deg CW via ffmpeg (noautorotate)...", flush=True)
        subprocess.run(cmd, check=True, capture_output=True, timeout=900)
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"")[-800:].decode("utf-8", errors="replace")
        try:
            from ffmpeg_accel import h264_encode_args as _enc

            encode2 = _enc(audio=False, force_cpu=True)
            subprocess.run(
                [
                    ffmpeg_bin, "-y", "-noautorotate", "-i", input_path,
                    "-vf", vf,
                    "-metadata:s:v:0", "rotate=0",
                    *encode2,
                    out_mp4,
                ],
                check=True,
                capture_output=True,
                timeout=900,
            )
        except Exception as e2:
            raise RuntimeError(
                f"ffmpeg rotate failed ({deg} deg): {e2}; stderr={err[:400]}"
            ) from e2
    except Exception as e:
        raise RuntimeError(f"ffmpeg rotate failed ({deg} deg): {e}") from e

    if not os.path.isfile(out_mp4) or os.path.getsize(out_mp4) < 32:
        raise RuntimeError(f"Rotated video missing or empty: {out_mp4}")
    print(f"[detect] input rotated {deg} deg CW -> {out_mp4}", flush=True)
    return out_mp4


