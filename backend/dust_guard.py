"""
Dust / loose-dirt false-positive guard — classical CV, no trained model.

Filters bright, soft dust/debris boxes. Mitigations for known failure modes:

  • Shadow-margin paradox: ring stats use road-like pixels only (drop V-shadow
    / specular outliers); optional 1.8× fallback ring.
  • Concrete bleed: absolute low_sat / bright / dust_pix are gated by relative
    blend (must look like surround to count as dust).
  • Fixed Canny: adaptive thresholds from local gradient percentiles; if Canny
    blanks but Laplacian still has structure, few_edges is not forced to 1.

Enable: POTHOLE_DUST_GUARD=1 (default). Threshold: POTHOLE_DUST_SCORE=0.68.
"""
from __future__ import annotations

import os
from typing import Sequence

import cv2
import numpy as np


def dust_guard_enabled() -> bool:
    raw = (os.getenv("POTHOLE_DUST_GUARD") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def dust_score_threshold() -> float:
    try:
        return float(os.getenv("POTHOLE_DUST_SCORE") or "0.68")
    except (TypeError, ValueError):
        return 0.68


def _min_box_side() -> int:
    """Smallest box side still scored (distant highway patches). Default 5."""
    try:
        return max(3, int(os.getenv("POTHOLE_DUST_MIN_BOX") or "5"))
    except (TypeError, ValueError):
        return 5


def _dark_cavity_bypass_frac() -> float:
    """dark_frac at/above this → keep (wet hole / shadow core). Default 5%."""
    try:
        return float(os.getenv("POTHOLE_DUST_DARK_FRAC") or "0.05")
    except (TypeError, ValueError):
        return 0.05


def _clamp01(x: float) -> float:
    return float(max(0.0, min(1.0, x)))


def _frame_texture_scale(frame_bgr: np.ndarray) -> float:
    """Scale texture denominators from lower-half sharpness."""
    h = frame_bgr.shape[0]
    band = frame_bgr[h // 2 :, :] if h >= 16 else frame_bgr
    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY) if band.ndim == 3 else band
    gh, gw = gray.shape[:2]
    if max(gh, gw) > 640:
        scale = 640.0 / max(gh, gw)
        gray = cv2.resize(
            gray,
            (max(8, int(gw * scale)), max(8, int(gh * scale))),
            interpolation=cv2.INTER_AREA,
        )
    lap = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return _clamp01(lap / 100.0) * 0.7 + 0.30


def _inset_core(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    if h < _min_box_side() or w < _min_box_side():
        return img
    pad_y = min(2, max(0, h // 25))
    pad_x = min(2, max(0, w // 25))
    if pad_y == 0 and pad_x == 0:
        return img
    core = img[pad_y : h - pad_y or None, pad_x : w - pad_x or None]
    return core if core.size else img


def _punch_ring(
    frame_bgr: np.ndarray,
    x1: int, y1: int, x2: int, y2: int,
    expand: float,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Return (outer_bgr, raw_ring_mask) or None."""
    h, w = frame_bgr.shape[:2]
    bw, bh = max(1, x2 - x1), max(1, y2 - y1)
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    nw, nh = bw * expand, bh * expand
    sx1 = max(0, int(round(cx - nw / 2)))
    sy1 = max(0, int(round(cy - nh / 2)))
    sx2 = min(w, int(round(cx + nw / 2)))
    sy2 = min(h, int(round(cy + nh / 2)))
    if sx2 - sx1 < 8 or sy2 - sy1 < 8:
        return None
    outer = frame_bgr[sy1:sy2, sx1:sx2]
    mask = np.ones(outer.shape[:2], dtype=np.uint8) * 255
    ix1, iy1 = max(0, x1 - sx1), max(0, y1 - sy1)
    ix2, iy2 = min(outer.shape[1], x2 - sx1), min(outer.shape[0], y2 - sy1)
    if ix2 > ix1 and iy2 > iy1:
        mask[iy1:iy2, ix1:ix2] = 0
    if int(np.count_nonzero(mask)) < 64:
        return None
    return outer, mask


def _road_like_mask(outer_bgr: np.ndarray, ring_mask: np.ndarray) -> np.ndarray:
    """
    Keep ring pixels that look like pavement, not cast-shadow margins or speculars.

    Excludes: V < 55 (shadow halo from loose YOLO boxes), V > 245 (glare speckles).
    Keeps mid-tone band around the ring median V so concrete and asphalt both work.
    """
    hsv = cv2.cvtColor(outer_bgr, cv2.COLOR_BGR2HSV)
    val = hsv[:, :, 2]
    base = ring_mask > 0
    if not np.any(base):
        return ring_mask
    v_samples = val[base]
    v_med = float(np.median(v_samples))
    # Band around median road tone (±55), also hard-exclude deep shadow / specular
    lo = max(55, int(v_med - 55))
    hi = min(245, int(v_med + 55))
    road = base & (val >= lo) & (val <= hi) & (val >= 55) & (val <= 245)
    # If shadow ate most of the ring, fall back to non-shadow ring only
    if int(np.count_nonzero(road)) < 48:
        road = base & (val >= 55) & (val <= 245)
    return road.astype(np.uint8) * 255


def _best_surround_ring(
    frame_bgr: np.ndarray,
    x1: int, y1: int, x2: int, y2: int,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Try 1.5× then 1.8×; pick ring with more road-like pixels (stable vs loose boxes)."""
    best = None
    best_n = 0
    for expand in (1.5, 1.8):
        punched = _punch_ring(frame_bgr, x1, y1, x2, y2, expand)
        if not punched:
            continue
        outer, raw = punched
        road = _road_like_mask(outer, raw)
        n = int(np.count_nonzero(road))
        if n > best_n:
            best_n = n
            best = (outer, road)
    return best if best_n >= 48 else None


def _adaptive_canny(gray: np.ndarray) -> tuple[np.ndarray, float]:
    """
    Canny with thresholds from Sobel magnitude percentiles (not fixed 60/140).
    Returns (edge_map, edge_density).
    """
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    p40 = float(np.percentile(mag, 40))
    p80 = float(np.percentile(mag, 80))
    # Floor so completely flat dust still gets a chance; don't lock to 60/140
    lo = max(5, int(round(p40 * 0.8)))
    hi = max(lo + 8, int(round(max(p80, p40 * 1.6))))
    hi = min(255, hi)
    edges = cv2.Canny(gray, lo, hi)
    return edges, float(np.mean(edges > 0))


def _dark_cavity_fraction(core_bgr: np.ndarray) -> float:
    hsv = cv2.cvtColor(core_bgr, cv2.COLOR_BGR2HSV)
    return float(np.mean(hsv[:, :, 2] < 50))


def dust_score_bgr(
    crop_bgr: np.ndarray,
    *,
    surround_bgr: np.ndarray | None = None,
    surround_mask: np.ndarray | None = None,
    texture_scale: float = 1.0,
) -> float:
    """Higher ≈ dust (reject). Prefer ``dust_score_box``."""
    if crop_bgr is None or crop_bgr.size == 0:
        return 0.0
    img = crop_bgr
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    h, w = img.shape[:2]
    if h < _min_box_side() or w < _min_box_side():
        return 0.0

    core = _inset_core(img)

    # --- Cavity / wet-patch override (step 1 dominates fresh wet asphalt) ---
    dark_frac = _dark_cavity_fraction(core)
    bypass = _dark_cavity_bypass_frac()
    hsv_quick = cv2.cvtColor(core, cv2.COLOR_BGR2HSV)
    mean_v_quick = float(np.mean(hsv_quick[:, :, 2])) / 255.0
    # Fresh wet mirror: bright reflective floor + any dark crescent / lip
    wet_patch = mean_v_quick >= 0.55 and dark_frac >= max(0.03, bypass * 0.6)
    if dark_frac >= bypass or wet_patch:
        return 0.0
    dark_discount = 1.0 - min(1.0, dark_frac / max(bypass, 1e-6))

    hsv = cv2.cvtColor(core, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1].astype(np.float32)
    val = hsv[:, :, 2].astype(np.float32)
    gray = cv2.cvtColor(core, cv2.COLOR_BGR2GRAY)

    mean_sat = float(np.mean(sat)) / 255.0
    mean_val = float(np.mean(val)) / 255.0
    dust_pix = float(np.mean((sat < 55) & (val > 120)))

    ts = max(0.30, float(texture_scale))
    lap_den = 120.0 * ts
    edge_den = 0.12 * ts

    lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    soft = 1.0 - min(1.0, lap_var / lap_den)

    edges, edge_density = _adaptive_canny(gray)
    few_edges = 1.0 - min(1.0, edge_density / edge_den)
    # Canny blank on low-contrast pavement but Laplacian still has structure →
    # do not force few_edges to 1.0 (fixed-threshold failure mode).
    if edge_density < 0.004 and lap_var > 15.0 * ts:
        few_edges = min(few_edges, 0.35)
    _ = edges  # computed for density only

    low_sat = 1.0 - min(1.0, mean_sat / 0.35)
    bright = min(1.0, max(0.0, (mean_val - 0.35) / 0.45))

    # --- Relative blend vs road-like ring (not shadow margin) ---
    relative = 0.5  # neutral if no ring
    if surround_bgr is not None and surround_mask is not None:
        m = surround_mask > 0
        if int(np.count_nonzero(m)) >= 48:
            shsv = cv2.cvtColor(surround_bgr, cv2.COLOR_BGR2HSV)
            s_sat = float(np.mean(shsv[:, :, 1][m])) / 255.0
            s_val = float(np.mean(shsv[:, :, 2][m])) / 255.0
            d_sat = abs(mean_sat - s_sat)
            d_val = abs(mean_val - s_val)
            s_gray = cv2.cvtColor(surround_bgr, cv2.COLOR_BGR2GRAY)
            ys, xs = np.where(m)
            if len(ys) > 2000:
                idx = np.linspace(0, len(ys) - 1, 2000).astype(int)
                ys, xs = ys[idx], xs[idx]
            s_lap = float(np.var(s_gray[ys, xs].astype(np.float64)))
            color_sim = 1.0 - min(1.0, (d_sat * 2.0 + d_val) / 0.35)
            tex_sim = 1.0 - min(1.0, abs(lap_var - s_lap) / max(lap_den, 1.0))
            relative = _clamp01(0.6 * color_sim + 0.4 * tex_sim)

    # Concrete / light-asphalt bleed: absolute color only counts if crop *blends*
    # with the road ring. Real potholes (even bright) differ → relative low →
    # color terms collapse.
    color_gate = 0.20 + 0.80 * relative
    # When whole frame is soft (low ts), further damp absolute color spikes
    color_gate *= 0.55 + 0.45 * ts

    color_term = color_gate * (0.35 * low_sat + 0.35 * dust_pix + 0.30 * bright)
    texture_term = 0.55 * soft + 0.45 * few_edges

    score = (
        0.28 * color_term
        + 0.22 * texture_term
        + 0.50 * relative
    )
    score *= dark_discount
    return _clamp01(score)


def dust_score_box(
    frame_bgr: np.ndarray,
    x1: int, y1: int, x2: int, y2: int,
    *,
    texture_scale: float | None = None,
) -> float:
    h, w = frame_bgr.shape[:2]
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(w, int(x2)), min(h, int(y2))
    if x2 - x1 < _min_box_side() or y2 - y1 < _min_box_side():
        return 0.0
    crop = frame_bgr[y1:y2, x1:x2]
    ring = _best_surround_ring(frame_bgr, x1, y1, x2, y2)
    outer, mask = (None, None) if ring is None else ring
    ts = _frame_texture_scale(frame_bgr) if texture_scale is None else float(texture_scale)
    return dust_score_bgr(
        crop, surround_bgr=outer, surround_mask=mask, texture_scale=ts,
    )


def is_dust_like_bgr(
    crop_bgr: np.ndarray,
    *,
    threshold: float | None = None,
    surround_bgr: np.ndarray | None = None,
    surround_mask: np.ndarray | None = None,
    texture_scale: float = 1.0,
) -> bool:
    thr = dust_score_threshold() if threshold is None else float(threshold)
    return dust_score_bgr(
        crop_bgr,
        surround_bgr=surround_bgr,
        surround_mask=surround_mask,
        texture_scale=texture_scale,
    ) >= thr


def is_dust_like_box(
    frame_bgr: np.ndarray,
    x1: int, y1: int, x2: int, y2: int,
    *,
    threshold: float | None = None,
    texture_scale: float | None = None,
) -> bool:
    thr = dust_score_threshold() if threshold is None else float(threshold)
    return dust_score_box(
        frame_bgr, x1, y1, x2, y2, texture_scale=texture_scale,
    ) >= thr


def filter_boxes_dust(
    frame_bgr: np.ndarray,
    boxes_xyxy: Sequence[Sequence[float]],
    *,
    threshold: float | None = None,
) -> list[bool]:
    if not dust_guard_enabled():
        return [True] * len(boxes_xyxy)
    thr = dust_score_threshold() if threshold is None else float(threshold)
    ts = _frame_texture_scale(frame_bgr)
    keep: list[bool] = []
    for box in boxes_xyxy:
        try:
            x1, y1, x2, y2 = [int(round(float(v))) for v in box[:4]]
        except (TypeError, ValueError, IndexError):
            keep.append(True)
            continue
        keep.append(
            not is_dust_like_box(
                frame_bgr, x1, y1, x2, y2, threshold=thr, texture_scale=ts,
            )
        )
    return keep
