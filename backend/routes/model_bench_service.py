"""Model testing bench — API layer over model_testing.inference."""
from __future__ import annotations

import base64
import os
import tempfile
import threading
import time
import uuid
from typing import Optional

import cv2
import numpy as np

from model_testing.inference import annotate_frame, annotate_image_file
from model_testing.models_registry import (
    choices,
    default_model_id,
    get_model,
    refresh_registry,
)


def _rgb_to_data_url(rgb: np.ndarray) -> str:
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    if not ok:
        raise RuntimeError("Failed to encode output image")
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def list_models() -> dict:
    refresh_registry()
    models = []
    for label, model_id in choices():
        spec = get_model(model_id)
        models.append({
            "id": model_id,
            "label": label,
            "kind": spec.kind,
            "backend": spec.backend,
            "description": spec.description,
            "params_m": spec.params_m,
            "notes": spec.notes,
        })
    return {"models": models, "default_id": default_model_id()}


def get_default_model_id() -> str:
    return default_model_id()


def run_image_file(local_path: Optional[str], model_id: str, conf: float) -> dict:
    if not local_path:
        return {"status": {"kind": "error", "message": "Upload an image to test."}}
    try:
        out_rgb, status = annotate_image_file(local_path, model_id, conf)
        if out_rgb is None:
            return {"status": {"kind": "error", "message": status}}
        return {
            "status": {"kind": "ok", "message": status},
            "image_data_url": _rgb_to_data_url(out_rgb),
        }
    except Exception as e:
        return {"status": {"kind": "error", "message": str(e)}}


def run_frame_bytes(image_bytes: bytes, model_id: str, conf: float, active: bool = True) -> dict:
    if not active:
        return {
            "status": {"kind": "ok", "message": "Camera off — click Start camera to begin."},
            "image_data_url": None,
        }
    if not image_bytes:
        return {
            "status": {"kind": "ok", "message": "Waiting for camera frame…"},
            "image_data_url": None,
        }
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        return {"status": {"kind": "error", "message": "Could not decode frame."}, "image_data_url": None}
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    try:
        out_rgb, status = annotate_frame(rgb, model_id, conf, active=True)
        if out_rgb is None:
            return {"status": {"kind": "error", "message": status}, "image_data_url": None}
        return {
            "status": {"kind": "ok", "message": status.replace("**", "")},
            "image_data_url": _rgb_to_data_url(out_rgb),
        }
    except Exception as e:
        return {"status": {"kind": "error", "message": str(e)}, "image_data_url": None}


# Ephemeral annotated videos — never persisted beyond this process / TTL.
_VIDEO_LOCK = threading.Lock()
_VIDEO_JOBS: dict[str, dict] = {}
_VIDEO_TTL_S = 30 * 60  # 30 minutes max


def _purge_expired_videos(except_token: str | None = None) -> None:
    now = time.time()
    dead = []
    for tok, job in _VIDEO_JOBS.items():
        if except_token and tok == except_token:
            continue
        if now - float(job.get("created_at") or 0) > _VIDEO_TTL_S:
            dead.append(tok)
    for tok in dead:
        discard_video(tok)


def discard_video(token: str | None) -> None:
    if not token:
        return
    with _VIDEO_LOCK:
        job = _VIDEO_JOBS.pop(str(token), None)
    if not job:
        return
    for key in ("out_path", "in_path"):
        path = job.get(key)
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass


def discard_user_videos(user_id: int) -> None:
    with _VIDEO_LOCK:
        tokens = [t for t, j in _VIDEO_JOBS.items() if int(j.get("user_id") or 0) == int(user_id)]
    for t in tokens:
        discard_video(t)


def get_video_path(token: str) -> str | None:
    with _VIDEO_LOCK:
        job = _VIDEO_JOBS.get(str(token))
        if not job:
            return None
        if time.time() - float(job.get("created_at") or 0) > _VIDEO_TTL_S:
            pass
        else:
            path = job.get("out_path")
            if path and os.path.isfile(path):
                return path
    discard_video(token)
    return None


def run_video_file(
    local_path: Optional[str],
    model_id: str,
    conf: float,
    *,
    user_id: int,
    max_frames: int = 900,
    stride: int = 1,
) -> dict:
    """Annotate a video to a temp file; return ephemeral token (not stored permanently)."""
    if not local_path or not os.path.isfile(local_path):
        return {"status": {"kind": "error", "message": "Upload a video to test."}}

    discard_user_videos(int(user_id))
    _purge_expired_videos()

    cap = cv2.VideoCapture(local_path)
    if not cap.isOpened():
        return {"status": {"kind": "error", "message": "Could not open video."}}

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 20.0)
    if fps <= 1e-3:
        fps = 20.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if width <= 0 or height <= 0:
        cap.release()
        return {"status": {"kind": "error", "message": "Invalid video dimensions."}}

    fd, out_path = tempfile.mkstemp(prefix="sr_bench_vid_", suffix=".mp4")
    os.close(fd)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(out_path, fourcc, fps, (width, height))
    if not writer.isOpened():
        cap.release()
        try:
            os.remove(out_path)
        except OSError:
            pass
        return {"status": {"kind": "error", "message": "Could not create annotated video writer."}}

    frame_i = 0
    written = 0
    last_status = ""
    try:
        while written < max_frames:
            ok, bgr = cap.read()
            if not ok:
                break
            if stride > 1 and (frame_i % stride) != 0:
                frame_i += 1
                continue
            frame_i += 1
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            out_rgb, status = annotate_frame(rgb, model_id, conf, active=True)
            last_status = status or last_status
            if out_rgb is None:
                out_bgr = bgr
            else:
                out_bgr = cv2.cvtColor(out_rgb, cv2.COLOR_RGB2BGR)
            if out_bgr.shape[1] != width or out_bgr.shape[0] != height:
                out_bgr = cv2.resize(out_bgr, (width, height))
            writer.write(out_bgr)
            written += 1
    finally:
        writer.release()
        cap.release()

    if written <= 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) < 100:
        try:
            os.remove(out_path)
        except OSError:
            pass
        return {"status": {"kind": "error", "message": "No frames could be annotated."}}

    # OpenCV mp4v often won't play / "download" cleanly in browsers — remux to H.264.
    try:
        from detector.video_io import _transcode_to_h264

        out_path = _transcode_to_h264(out_path)
    except Exception as e:
        print(f"[bench] H.264 transcode skipped: {e}", flush=True)

    token = uuid.uuid4().hex
    with _VIDEO_LOCK:
        _VIDEO_JOBS[token] = {
            "user_id": int(user_id),
            "out_path": out_path,
            "in_path": None,
            "created_at": time.time(),
            "frames": written,
            "filename": f"annotated_{token[:8]}.mp4",
        }

    return {
        "status": {
            "kind": "ok",
            "message": f"Annotated {written} frame(s). Download now — video is not stored.",
        },
        "token": token,
        "frames": written,
        "preview_url": f"/api/model-bench/video/{token}",
        "download_url": f"/api/model-bench/video/{token}?download=1",
        "detail": last_status,
    }
