"""Prefer NVIDIA NVENC/NVDEC (GPU) for H.264 when available; else libx264 on CPU.

imageio-ffmpeg's static binary usually lacks NVENC. Prefer a system
``ffmpeg`` built with ``--enable-nvenc`` (common on AceCloud GPU images).

Note: OpenCV box-drawing and Ultralytics DataLoader workers cannot run on the
GPU — only encode/decode (NVENC/NVDEC) and YOLO forward/backward can.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
from functools import lru_cache
from typing import Optional


_PROBE_LOCK = threading.Lock()
_NVENC_OK: Optional[bool] = None
_NVDEC_OK: Optional[bool] = None


def ffmpeg_exe() -> str:
    """Resolve ffmpeg: prefer system binary when it has NVENC."""
    prefer_system = os.getenv("FFMPEG_PREFER_SYSTEM", "1").lower() in ("1", "true", "yes")
    which = shutil.which("ffmpeg")
    if prefer_system and which and _ffmpeg_has_nvenc(which):
        return which

    bundled = None
    try:
        import imageio_ffmpeg

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        bundled = None

    if which and prefer_system:
        return which
    if bundled:
        return bundled
    if which:
        return which
    raise RuntimeError(
        "ffmpeg unavailable - install system ffmpeg (NVENC-enabled preferred) "
        "or pip install imageio-ffmpeg"
    )


def _ffmpeg_has_nvenc(exe: str) -> bool:
    try:
        out = subprocess.run(
            [exe, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        blob = (out.stdout or "") + (out.stderr or "")
        return "h264_nvenc" in blob
    except Exception:
        return False


def _ffmpeg_has_nvdec(exe: str) -> bool:
    """True when ffmpeg can use CUDA hwaccel (NVDEC) for decode."""
    try:
        out = subprocess.run(
            [exe, "-hide_banner", "-hwaccels"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        blob = ((out.stdout or "") + (out.stderr or "")).lower()
        return "cuda" in blob
    except Exception:
        return False


def nvenc_available() -> bool:
    """Cached probe — safe to call often."""
    global _NVENC_OK
    if _NVENC_OK is not None:
        return _NVENC_OK
    with _PROBE_LOCK:
        if _NVENC_OK is not None:
            return _NVENC_OK
        forced = (os.getenv("FFMPEG_HW") or os.getenv("FINALIZE_FFMPEG_HW") or "auto").strip().lower()
        if forced in ("0", "false", "no", "cpu", "libx264", "off"):
            _NVENC_OK = False
            return False
        try:
            exe = ffmpeg_exe()
            has = _ffmpeg_has_nvenc(exe)
        except Exception:
            has = False
        if forced in ("1", "true", "yes", "nvenc", "gpu", "cuda"):
            _NVENC_OK = has
            return _NVENC_OK
        # auto
        _NVENC_OK = has
        return _NVENC_OK


def nvdec_available() -> bool:
    """CUDA hwaccel decode (NVDEC) — used before ``-i`` on re-encode paths."""
    global _NVDEC_OK
    if _NVDEC_OK is not None:
        return _NVDEC_OK
    with _PROBE_LOCK:
        if _NVDEC_OK is not None:
            return _NVDEC_OK
        forced = (os.getenv("FFMPEG_HW") or os.getenv("FINALIZE_FFMPEG_HW") or "auto").strip().lower()
        if forced in ("0", "false", "no", "cpu", "libx264", "off"):
            _NVDEC_OK = False
            return False
        try:
            exe = ffmpeg_exe()
            has = _ffmpeg_has_nvdec(exe) and nvenc_available()
        except Exception:
            has = False
        _NVDEC_OK = has
        return _NVDEC_OK


def hwaccel_decode_args(*, force_cpu: bool = False) -> list[str]:
    """Args *before* ``-i`` so decode uses NVDEC when available.

    Keeps pixels on GPU for NVENC when possible (``hwaccel_output_format cuda``).
    Falls back to empty list → software decode.
    """
    if force_cpu or not nvdec_available():
        return []
    return ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]


def h264_encode_args(*, audio: bool = False, force_cpu: bool = False) -> list[str]:
    """Args after ``-i …`` for H.264 output (GPU NVENC or CPU libx264)."""
    threads = max(1, int(os.getenv("FINALIZE_FFMPEG_THREADS") or os.getenv("POTHOLE_FFMPEG_THREADS") or "2"))
    use_nvenc = (not force_cpu) and nvenc_available()
    if use_nvenc:
        preset = os.getenv("FFMPEG_NVENC_PRESET", "p4")
        cq = os.getenv("FFMPEG_NVENC_CQ", os.getenv("POTHOLE_X264_CRF", "28"))
        args = [
            "-c:v", "h264_nvenc",
            "-preset", preset,
            "-rc", "vbr",
            "-cq", str(cq),
            "-b:v", "0",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
        ]
    else:
        preset = os.getenv("POTHOLE_X264_PRESET", "ultrafast")
        crf = os.getenv("POTHOLE_X264_CRF", "28")
        args = [
            "-threads", str(threads),
            "-c:v", "libx264",
            "-preset", preset,
            "-crf", str(crf),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
        ]
    if audio:
        args.extend(["-c:a", "aac"])
    else:
        args.append("-an")
    return args


@lru_cache(maxsize=1)
def accel_label() -> str:
    try:
        enc = "nvenc" if nvenc_available() else "libx264"
        dec = "+nvdec" if nvdec_available() else ""
        return f"{enc}{dec}"
    except Exception:
        return "unknown"
