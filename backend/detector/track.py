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
from detector.types import DetectionRow

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
# Tracking
# ============================================================
@dataclass
class Track:
    track_id: int
    cls: str
    best_row: DetectionRow
    best_conf: float
    last_cx: float
    last_cy: float
    last_seen_frame: int
    hits: int = 0
    gps_count: int = 0
    lat_sum: float = 0.0
    lon_sum: float = 0.0
    captured_at: str = ""


def _center(row: DetectionRow) -> Tuple[float, float]:
    return ((row.x1 + row.x2) / 2.0, (row.y1 + row.y2) / 2.0)


def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return (dx * dx + dy * dy) ** 0.5


