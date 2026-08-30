"""
Frame-level inference for YOLO and RF-DETR backends.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2
import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
import smartroad_path  # noqa: F401 — backend/ on sys.path

from utils import severity_from_area_and_position  # noqa: E402

try:
    from model_loader import resolve_yolo_device
except ImportError:
    def resolve_yolo_device() -> str:  # type: ignore[misc]
        return "cpu"

try:
    from models_registry import ModelSpec, get_model, refresh_registry
except ImportError:
    from model_testing.models_registry import ModelSpec, get_model, refresh_registry

_SEVERITY_BGR = {
    "Low": (0, 255, 0),
    "Medium": (0, 165, 255),
    "High": (0, 0, 255),
}
_GENERAL_BGR = (0, 212, 255)
_RFDETR_BGR = (255, 180, 0)


class ModelCache:
    def __init__(self) -> None:
        self._cache: dict[str, object] = {}
        self._errors: dict[str, str] = {}

    def get_yolo(self, model_id: str):
        if model_id in self._cache:
            return self._cache[model_id]
        if model_id in self._errors:
            raise RuntimeError(self._errors[model_id])
        from ultralytics import YOLO

        try:
            from model_testing.yolo12_aattn_compat import patch_yolo_aattn
        except ImportError:
            from yolo12_aattn_compat import patch_yolo_aattn  # type: ignore

        spec = get_model(model_id)
        try:
            model = YOLO(spec.resolve_path())
            patch_yolo_aattn(model)
            _configure_yolo_model(model)
        except Exception as e:
            self._errors[model_id] = f"Failed to load {spec.label}: {e}"
            raise RuntimeError(self._errors[model_id]) from e
        self._cache[model_id] = model
        return model

    def get_rfdetr(self, model_id: str):
        if model_id in self._cache:
            return self._cache[model_id]
        if model_id in self._errors:
            raise RuntimeError(self._errors[model_id])

        import rfdetr

        spec = get_model(model_id)
        variant = spec.rfdetr_variant or "small"
        cls_map = {
            "small": "RFDETRSmall",
            "medium": "RFDETRMedium",
            "base": "RFDETRBase",
            "large": "RFDETRLarge",
        }
        cls_name = cls_map.get(variant.lower(), "RFDETRSmall")
        RFDETR = getattr(rfdetr, cls_name)
        weights = spec.resolve_path()
        try:
            if Path(weights).is_file():
                model = RFDETR(pretrain_weights=weights)
            else:
                model = RFDETR()
        except Exception as e:
            self._errors[model_id] = f"Failed to load RF-DETR ({spec.label}): {e}"
            raise RuntimeError(self._errors[model_id]) from e
        self._cache[model_id] = model
        return model

    def clear(self) -> None:
        self._cache.clear()
        self._errors.clear()


_CACHE = ModelCache()


def _configure_yolo_model(model):
    """Match production detection: CUDA device + FP16 when configured."""
    device = resolve_yolo_device()
    try:
        model.to("cpu" if device == "cpu" else device)
    except Exception:
        pass
    try:
        model._smartroad_device = device  # type: ignore[attr-defined]
    except Exception:
        pass
    if device != "cpu":
        try:
            import torch

            torch.backends.cudnn.benchmark = True
            if hasattr(torch, "set_float32_matmul_precision"):
                torch.set_float32_matmul_precision("high")
        except Exception:
            pass
    return model


def _yolo_predict_kwargs(model, conf: float) -> dict:
    import os

    kw: dict = {"conf": conf, "verbose": False}
    device = getattr(model, "_smartroad_device", None)
    if device is not None:
        kw["device"] = device
    half_env = (os.getenv("POTHOLE_HALF") or "").strip().lower()
    if half_env in ("1", "true", "yes"):
        kw["half"] = True
    elif half_env in ("0", "false", "no"):
        kw["half"] = False
    elif device not in (None, "cpu"):
        kw["half"] = True
    imgsz = (os.getenv("MODEL_BENCH_IMGSZ") or os.getenv("POTHOLE_IMGSZ") or "").strip()
    if imgsz:
        try:
            kw["imgsz"] = int(imgsz)
        except ValueError:
            pass
    return kw


def _draw_label(img, x1, y1, text: str, color: tuple[int, int, int]) -> None:
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale, thick = 0.55, 2
    (tw, th), base = cv2.getTextSize(text, font, scale, thick)
    y = max(th + 6, y1)
    cv2.rectangle(img, (x1, y - th - base - 4), (x1 + tw + 8, y + 2), (0, 0, 0), -1)
    cv2.putText(img, text, (x1 + 4, y - 2), font, scale, color, thick, cv2.LINE_AA)


def _to_bgr(frame: np.ndarray) -> np.ndarray:
    img = np.asarray(frame)
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
    if img.shape[2] == 3:
        return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    return img.copy()


def _annotate_yolo_pothole(frame_bgr: np.ndarray, model, conf: float) -> tuple[np.ndarray, int, float]:
    h, w = frame_bgr.shape[:2]
    t0 = time.perf_counter()
    res = model.predict(frame_bgr, **_yolo_predict_kwargs(model, conf))[0]
    elapsed = time.perf_counter() - t0
    count = 0
    try:
        from dust_guard import dust_guard_enabled, is_dust_like_box, _frame_texture_scale
        _dust_on = dust_guard_enabled()
        _dust_ts = _frame_texture_scale(frame_bgr) if _dust_on else 1.0
    except Exception:
        _dust_on = False
        _dust_ts = 1.0
    if res.boxes is not None:
        names = res.names or {}
        for b in res.boxes:
            x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w - 1, x2), min(h - 1, y2)
            if _dust_on and x2 > x1 and y2 > y1:
                try:
                    if is_dust_like_box(
                        frame_bgr, x1, y1, x2, y2, texture_scale=_dust_ts,
                    ):
                        continue
                except Exception:
                    pass
            confidence = float(b.conf[0])
            cls_id = int(b.cls[0])
            cls_name = str(names.get(cls_id, cls_id))
            area = max(0, x2 - x1) * max(0, y2 - y1)
            y_center = (y1 + y2) / 2.0
            severity = severity_from_area_and_position(area, y_center, h)
            color = _SEVERITY_BGR.get(severity, _GENERAL_BGR)
            cv2.rectangle(frame_bgr, (x1, y1), (x2, y2), color, 2, cv2.LINE_AA)
            _draw_label(frame_bgr, x1, y1, f"{cls_name} {confidence:.2f} | {severity}", color)
            count += 1
    return frame_bgr, count, elapsed


def _annotate_yolo_general(frame_bgr: np.ndarray, model, conf: float) -> tuple[np.ndarray, int, float]:
    h, w = frame_bgr.shape[:2]
    t0 = time.perf_counter()
    res = model.predict(frame_bgr, **_yolo_predict_kwargs(model, conf))[0]
    elapsed = time.perf_counter() - t0
    count = 0
    if res.boxes is not None:
        names = res.names or {}
        for b in res.boxes:
            x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w - 1, x2), min(h - 1, y2)
            confidence = float(b.conf[0])
            cls_id = int(b.cls[0])
            cls_name = str(names.get(cls_id, cls_id))
            cv2.rectangle(frame_bgr, (x1, y1), (x2, y2), _GENERAL_BGR, 2, cv2.LINE_AA)
            _draw_label(frame_bgr, x1, y1, f"{cls_name} {confidence:.2f}", _GENERAL_BGR)
            count += 1
    return frame_bgr, count, elapsed


def _annotate_rfdetr(frame_bgr: np.ndarray, model, conf: float) -> tuple[np.ndarray, int, float]:
    from PIL import Image

    h, w = frame_bgr.shape[:2]
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)
    t0 = time.perf_counter()
    detections = model.predict(pil, threshold=conf)
    elapsed = time.perf_counter() - t0
    count = 0

    xyxy = getattr(detections, "xyxy", None)
    if xyxy is None and hasattr(detections, "boxes"):
        xyxy = detections.boxes
    confs = getattr(detections, "confidence", None) or getattr(detections, "conf", [])
    class_ids = getattr(detections, "class_id", None) or getattr(detections, "cls", [])

    if xyxy is not None and len(xyxy) > 0:
        for i in range(len(xyxy)):
            x1, y1, x2, y2 = map(int, xyxy[i])
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w - 1, x2), min(h - 1, y2)
            score = float(confs[i]) if len(confs) > i else 0.0
            area = max(0, x2 - x1) * max(0, y2 - y1)
            y_center = (y1 + y2) / 2.0
            severity = severity_from_area_and_position(area, y_center, h)
            color = _SEVERITY_BGR.get(severity, _RFDETR_BGR)
            cv2.rectangle(frame_bgr, (x1, y1), (x2, y2), color, 2, cv2.LINE_AA)
            _draw_label(frame_bgr, x1, y1, f"pothole {score:.2f} | {severity}", color)
            count += 1

    return frame_bgr, count, elapsed


def annotate_frame(
    frame: np.ndarray | None,
    model_id: str,
    conf: float = 0.35,
    *,
    active: bool = True,
) -> tuple[np.ndarray | None, str]:
    if not active:
        return frame, "Camera off — click **Start camera** to begin."
    if frame is None:
        return None, "Waiting for camera frame…"

    refresh_registry()
    spec: ModelSpec = get_model(model_id)
    try:
        if spec.backend == "rfdetr":
            model = _CACHE.get_rfdetr(model_id)
        else:
            model = _CACHE.get_yolo(model_id)
    except RuntimeError as e:
        out = np.asarray(frame).copy()
        cv2.putText(
            out, str(e)[:100], (12, 40),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 80, 80), 1, cv2.LINE_AA,
        )
        return out, str(e)

    img = _to_bgr(frame)

    try:
        if spec.backend == "rfdetr":
            img, count, elapsed = _annotate_rfdetr(img, model, conf)
        elif spec.kind == "pothole":
            img, count, elapsed = _annotate_yolo_pothole(img, model, conf)
        else:
            img, count, elapsed = _annotate_yolo_general(img, model, conf)
    except AttributeError as e:
        msg = str(e)
        if "qk" in msg or "qkv" in msg or "AAttn" in msg:
            # Drop cached model and re-patch once (serves older worker processes).
            _CACHE._cache.pop(model_id, None)
            _CACHE._errors.pop(model_id, None)
            try:
                from model_testing.yolo12_aattn_compat import patch_yolo_aattn
            except ImportError:
                from yolo12_aattn_compat import patch_yolo_aattn  # type: ignore
            model = _CACHE.get_yolo(model_id)
            patch_yolo_aattn(model)
            if spec.kind == "pothole":
                img, count, elapsed = _annotate_yolo_pothole(img, model, conf)
            else:
                img, count, elapsed = _annotate_yolo_general(img, model, conf)
        else:
            raise

    fps = 1.0 / elapsed if elapsed > 0 else 0.0
    status = (
        f"{spec.label} · {count} detection(s) · "
        f"{elapsed * 1000:.0f} ms/frame · ~{fps:.1f} FPS"
    )
    cv2.putText(
        img, status[:95], (8, img.shape[0] - 12),
        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA,
    )
    out_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return out_rgb, status


def annotate_image_file(
    image_path: str | None,
    model_id: str,
    conf: float,
) -> tuple[np.ndarray | None, str]:
    if not image_path:
        return None, "Upload an image to test."
    img = cv2.imread(image_path)
    if img is None:
        return None, f"Could not read image: {image_path}"
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return annotate_frame(rgb, model_id, conf, active=True)
