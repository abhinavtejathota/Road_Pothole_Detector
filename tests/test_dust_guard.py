"""Dust guard — shadow-margin, concrete bleed, adaptive Canny."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestDustGuard(unittest.TestCase):
    def setUp(self):
        os.environ["POTHOLE_DUST_GUARD"] = "1"
        os.environ["POTHOLE_DUST_SCORE"] = "0.68"

    def test_flat_dust_on_matching_road_rejected(self):
        from dust_guard import is_dust_like_box

        frame = np.full((200, 200, 3), (175, 178, 182), dtype=np.uint8)
        self.assertTrue(is_dust_like_box(frame, 70, 70, 130, 130))

    def test_dark_cavity_bypasses(self):
        from dust_guard import dust_score_box, is_dust_like_box

        frame = np.full((200, 200, 3), (160, 160, 160), dtype=np.uint8)
        frame[80:120, 80:120] = (180, 180, 180)
        frame[90:110, 90:110] = (15, 15, 15)
        self.assertEqual(dust_score_box(frame, 80, 80, 120, 120), 0.0)
        self.assertFalse(is_dust_like_box(frame, 80, 80, 120, 120))

    def test_loose_box_with_shadow_margin_still_keeps_cavity(self):
        """Shadow-margin paradox: loose YOLO box includes dark rim; road-like ring ignores it."""
        from dust_guard import is_dust_like_box

        frame = np.full((240, 240, 3), (110, 110, 110), dtype=np.uint8)  # asphalt
        # Loose box region with bright floor + dark margin (cast shadow)
        frame[70:170, 70:170] = (200, 200, 205)
        frame[70:85, 70:170] = (30, 30, 30)   # top shadow margin inside loose box
        frame[70:170, 70:85] = (30, 30, 30)
        # Real dark cavity core
        frame[110:140, 110:140] = (20, 20, 20)
        self.assertFalse(is_dust_like_box(frame, 70, 70, 170, 170))

    def test_concrete_pothole_differs_from_surround_kept(self):
        """Concrete bleed: bright low-sat crop but darker hole vs light surround → keep."""
        from dust_guard import is_dust_like_box

        frame = np.full((220, 220, 3), (200, 200, 205), dtype=np.uint8)  # light cement
        # Soft-edged darker patch (pothole on concrete) — still differs from surround
        frame[90:140, 90:140] = (150, 150, 155)
        frame[105:125, 105:125] = (40, 40, 40)
        self.assertFalse(is_dust_like_box(frame, 90, 90, 140, 140))

    def test_adaptive_canny_does_not_max_few_edges_on_structure(self):
        from dust_guard import dust_score_bgr

        # Low-contrast gray with mild structure (would blank fixed Canny 60/140)
        rng = np.random.default_rng(0)
        crop = np.full((80, 80, 3), 140, dtype=np.uint8)
        crop = np.clip(crop.astype(np.int16) + rng.integers(-8, 9, crop.shape), 0, 255).astype(np.uint8)
        for i in range(0, 80, 4):
            crop[i, :] = np.clip(crop[i, :].astype(int) - 12, 0, 255)
        # Surround-like same tone → relative mid; score should not explode solely from few_edges
        score = dust_score_bgr(crop, texture_scale=0.5)
        self.assertLess(score, 0.95)

    def test_can_disable(self):
        from dust_guard import dust_guard_enabled, filter_boxes_dust

        os.environ["POTHOLE_DUST_GUARD"] = "0"
        self.assertFalse(dust_guard_enabled())
        frame = np.full((100, 100, 3), 200, dtype=np.uint8)
        self.assertEqual(filter_boxes_dust(frame, [[10, 10, 50, 50]]), [True])


if __name__ == "__main__":
    unittest.main()
