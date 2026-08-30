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

reexport(_gps, globals())

# ============================================================
# Detection types & drawing (unchanged)
# ============================================================
@dataclass
class DetectionRow:
    cls: str
    conf: float
    x1: int
    y1: int
    x2: int
    y2: int
    severity: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    map_link: str = ""
    captured_at: str = ""
    s3_url: str = ""
    gps_second: int = 0  # video second this detection belongs to (for GPS extras lookup)
    local_frame_path: str = ""  # path to this row's saved crop image on disk, if any (not uploaded yet)


_SEVERITY_COLOR = {
    "Low":    (0, 255,   0),   # Green  (BGR)
    "Medium": (0, 165, 255),   # Orange (BGR)
    "High":   (0,   0, 255),   # Red    (BGR)
}

def _draw_box(img, row: DetectionRow):
    color = _SEVERITY_COLOR.get(row.severity, (0, 255, 0))
    cv2.rectangle(img, (row.x1, row.y1), (row.x2, row.y2), color, 3, lineType=cv2.LINE_AA)
    label = f"{row.cls} {row.conf:.2f} | {row.severity}"
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.75
    thickness = 2
    (tw, th), baseline = cv2.getTextSize(label, font, font_scale, thickness)
    x = row.x1
    y = max(0, row.y1 - 10)
    cv2.rectangle(img, (x, y - th - baseline - 6), (x + tw + 8, y + 4), (0, 0, 0), -1)
    cv2.putText(img, label, (x + 4, y - 4), font, font_scale, (255, 255, 255), thickness, lineType=cv2.LINE_AA)


# Set by pothole_detector when UI rotation was baked — tighter FP filters.
# Parallel workers also read POTHOLE_INPUT_ROTATION from the environment.
_ACTIVE_INPUT_ROTATION = 0


def set_active_input_rotation(deg: int) -> None:
    global _ACTIVE_INPUT_ROTATION
    try:
        d = int(deg) % 360
    except (TypeError, ValueError):
        d = 0
    _ACTIVE_INPUT_ROTATION = d if d in (0, 90, 180, 270) else 0
    os.environ["POTHOLE_INPUT_ROTATION"] = str(_ACTIVE_INPUT_ROTATION)


def _active_input_rotation() -> int:
    if _ACTIVE_INPUT_ROTATION in (90, 180, 270):
        return _ACTIVE_INPUT_ROTATION
    try:
        d = int(os.getenv("POTHOLE_INPUT_ROTATION") or "0") % 360
    except (TypeError, ValueError):
        return 0
    return d if d in (90, 180, 270) else 0


def _reject_background_box(
    *,
    conf: float,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    frame_w: int,
    frame_h: int,
) -> bool:
    """Drop sky / ego-vehicle / huge boxes (mirrors, handlebars, sky)."""
    bw = max(0, x2 - x1)
    bh = max(0, y2 - y1)
    if bw < 2 or bh < 2 or frame_w < 1 or frame_h < 1:
        return True
    area_frac = (bw * bh) / float(frame_w * frame_h)
    y_center = (y1 + y2) / 2.0
    y_frac = y_center / float(frame_h)
    x_center = (x1 + x2) / 2.0
    x_frac = x_center / float(frame_w)

    try:
        min_y = float(os.getenv("POTHOLE_MIN_Y_FRAC") or "0.18")
    except (TypeError, ValueError):
        min_y = 0.18
    try:
        max_box = float(os.getenv("POTHOLE_MAX_BOX_FRAC") or "0.42")
    except (TypeError, ValueError):
        max_box = 0.42

    # Road is usually mid/lower frame for phone/dashcam; upper band is sky/walls.
    if y_frac < min_y and conf < 0.72:
        return True
    # Giant boxes are almost never a single pothole.
    if area_frac > max_box:
        return True

    # --- Ego-vehicle fixtures (scooter/bike mirrors, hands, dash) ---
    # These sit in the lower corners / along the bottom edge of phone mounts.
    ego_on = (os.getenv("POTHOLE_EGO_FILTER") or "1").strip().lower() not in (
        "0", "false", "no", "off",
    )
    if ego_on:
        try:
            ego_y = float(os.getenv("POTHOLE_EGO_Y_FRAC") or "0.52")
        except (TypeError, ValueError):
            ego_y = 0.52
        try:
            ego_x = float(os.getenv("POTHOLE_EGO_X_FRAC") or "0.32")
        except (TypeError, ValueError):
            ego_x = 0.32
        try:
            ego_conf = float(os.getenv("POTHOLE_EGO_CONF") or "0.82")
        except (TypeError, ValueError):
            ego_conf = 0.82

        in_lower = y_frac >= ego_y
        in_left_pocket = x_frac <= ego_x
        in_right_pocket = x_frac >= (1.0 - ego_x)
        # Mirror / hand pockets — require very high conf to keep.
        if in_lower and (in_left_pocket or in_right_pocket) and conf < ego_conf:
            return True

        touches_bottom = y2 >= frame_h - max(2, int(0.01 * frame_h))
        touches_side = x1 <= max(2, int(0.02 * frame_w)) or x2 >= frame_w - max(2, int(0.02 * frame_w))
        # Corner-hugging box on the vehicle body.
        if touches_bottom and touches_side and conf < ego_conf:
            return True
        # Bottom strip near either side (handlebar / mirror stem).
        if touches_bottom and (in_left_pocket or in_right_pocket) and conf < 0.75:
            return True

    # After an explicit extra rotate, be stricter on upper-third + edge hugs.
    if _active_input_rotation():
        if y_frac < 0.32 and conf < 0.55:
            return True
        edge_pad = 0.03
        if (
            x_frac < edge_pad or x_frac > 1.0 - edge_pad
            or y_frac < edge_pad or y_frac > 1.0 - edge_pad
        ) and conf < 0.60:
            return True
        if area_frac > 0.28 and conf < 0.50:
            return True
    return False


def _infer_on_frame(model, frame) -> List[DetectionRow]:
    h, w = frame.shape[:2]
    predict_kw = {"verbose": False}
    device = getattr(model, "_smartroad_device", None)
    if device is not None:
        predict_kw["device"] = device
    imgsz = (os.getenv("POTHOLE_IMGSZ") or "").strip()
    if imgsz:
        try:
            predict_kw["imgsz"] = int(imgsz)
        except ValueError:
            pass
    # FP16 on CUDA roughly doubles throughput on A6000; CPU ignores half.
    half_env = (os.getenv("POTHOLE_HALF") or "").strip().lower()
    if half_env in ("1", "true", "yes"):
        predict_kw["half"] = True
    elif half_env in ("0", "false", "no"):
        predict_kw["half"] = False
    elif device not in (None, "cpu"):
        predict_kw["half"] = True
    # Slightly higher predict conf after rotate — sideways/sky textures spike FPs.
    if _active_input_rotation():
        try:
            predict_kw["conf"] = float(os.getenv("POTHOLE_ROTATION_CONF") or "0.35")
        except (TypeError, ValueError):
            predict_kw["conf"] = 0.35
    res = model.predict(frame, **predict_kw)[0]
    rows: List[DetectionRow] = []
    names = res.names
    if res.boxes is None:
        return rows

    try:
        from dust_guard import dust_guard_enabled, is_dust_like_box, _frame_texture_scale
        _dust_on = dust_guard_enabled()
        _dust_ts = _frame_texture_scale(frame) if _dust_on else 1.0
    except Exception:
        _dust_on = False
        _dust_ts = 1.0

    for b in res.boxes:
        x1, y1, x2, y2 = b.xyxy[0].tolist()
        conf = float(b.conf[0])
        cls_id = int(b.cls[0])
        cls_name = names.get(cls_id, str(cls_id))
        x1i, y1i, x2i, y2i = map(lambda v: int(max(0, v)), [x1, y1, x2, y2])
        x2i, y2i = min(w, x2i), min(h, y2i)
        if _reject_background_box(
            conf=conf, x1=x1i, y1=y1i, x2=x2i, y2=y2i, frame_w=w, frame_h=h,
        ):
            continue
        if _dust_on and x2i > x1i and y2i > y1i:
            try:
                if is_dust_like_box(frame, x1i, y1i, x2i, y2i, texture_scale=_dust_ts):
                    continue
            except Exception:
                pass
        area = max(0, x2i - x1i) * max(0, y2i - y1i)
        y_center = (y1i + y2i) / 2.0
        sev = severity_from_area_and_position(area, y_center, h)
        rows.append(DetectionRow(cls_name, conf, x1i, y1i, x2i, y2i, sev))
    return rows


def _resolve_frame_stride(capture_mode: str | None) -> int:
    """How many frames between YOLO runs.

    Phone→server *upload* chunking does NOT reduce detection work — Finalize
    still builds one full video, then this pipeline runs over every (or every
    Nth) frame. Stride is the main knob for wall-clock speed.

    Env ``POTHOLE_FRAME_STRIDE`` overrides. Defaults: vehicle=5, walking=3.
    """
    raw = (os.getenv("POTHOLE_FRAME_STRIDE") or "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    mode = (capture_mode or "walking").strip().lower()
    return 5 if mode == "vehicle" else 3


def _resolve_write_stride(frame_stride: int) -> int:
    """How often to encode a frame into annotated.mp4 (often the real bottleneck)."""
    raw = (os.getenv("POTHOLE_WRITE_STRIDE") or "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    # Default: write at the same cadence as YOLO (not every source frame).
    return max(1, int(frame_stride))


def _want_annotated_video() -> bool:
    return os.getenv("POTHOLE_ANNOTATED_VIDEO", "true").lower() in ("1", "true", "yes")


def _want_h264_transcode() -> bool:
    return os.getenv("POTHOLE_TRANSCODE_H264", "true").lower() in ("1", "true", "yes")


def _limit_native_threads() -> None:
    """Cap OpenCV/BLAS/torch CPU threads so GPU detect/train does not starve the portal.

    YOLO inference/train run on GPU when YOLO_DEVICE!=cpu, but video decode,
    annotation draw, and dataloader still use CPU. Default used to be
    ``min(12, cpus-2)`` which pegged the box under load.
    """
    cpus = os.cpu_count() or 4
    yolo_dev = (os.getenv("YOLO_DEVICE") or os.getenv("POTHOLE_DEVICE") or "auto").strip().lower()
    on_gpu = yolo_dev not in ("cpu",) and yolo_dev != ""
    if yolo_dev in ("auto", ""):
        try:
            import torch
            on_gpu = bool(torch.cuda.is_available())
        except Exception:
            on_gpu = False
    # GPU path: keep decode/draw light (2–4 cores). CPU-only YOLO: allow more.
    if on_gpu:
        default = str(max(2, min(4, max(1, cpus // 6))))
    else:
        default = str(max(2, min(6, cpus - 2)))
    n = max(1, int(os.getenv("POTHOLE_CPU_THREADS", default)))
    for key in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        # Force even if parent set a high value — detect must not eat the machine.
        os.environ[key] = str(n)
    try:
        import torch
        torch.set_num_threads(n)
        if hasattr(torch, "set_num_interop_threads"):
            torch.set_num_interop_threads(max(1, min(2, n)))
    except Exception:
        pass
    try:
        cv2.setNumThreads(n)
    except Exception:
        pass
    print(f"[threads] POTHOLE_CPU_THREADS={n} (gpu={on_gpu})", flush=True)


def _zip_folder(folder_path: str, zip_path: str) -> str:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(folder_path):
            for f in files:
                fp = os.path.join(root, f)
                rel = os.path.relpath(fp, folder_path)
                zf.write(fp, rel)
    return zip_path


