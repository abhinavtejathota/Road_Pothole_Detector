"""SmartRoad detection service — YOLO / model-bench (GPU)."""
import os

import smartroad_path  # noqa: F401

from routes.app_factory import run_service

if __name__ == "__main__":
    os.environ["SMARTROAD_SERVICE"] = "detect"
    os.environ.setdefault("FLASK_PORT", "5007")
    os.environ.setdefault("YOLO_DEVICE", "0")
    run_service("detect", default_port=5007)
