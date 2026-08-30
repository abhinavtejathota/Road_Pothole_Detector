"""SmartRoad upload service — chunk HTTP only (finalize is a separate nohup)."""
import os

import smartroad_path  # noqa: F401

from routes.app_factory import run_service

if __name__ == "__main__":
    os.environ["SMARTROAD_SERVICE"] = "upload"
    os.environ.setdefault("FLASK_PORT", "5006")
    # Prefer NVENC path for any in-process media work; YOLO stays on detect.
    os.environ.setdefault("YOLO_DEVICE", "cpu")
    run_service("upload", default_port=5006)
