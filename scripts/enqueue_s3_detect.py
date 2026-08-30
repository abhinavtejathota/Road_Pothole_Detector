#!/usr/bin/env python3
"""One-shot: enqueue S3 input-bucket media into the detect queue.

  python scripts/enqueue_s3_detect.py
  python scripts/enqueue_s3_detect.py --limit 20
  python scripts/enqueue_s3_detect.py --sources videographer,user

The smartroad_worker also scans periodically; use this for an immediate backfill.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
except Exception:
    pass


def main() -> int:
    p = argparse.ArgumentParser(description="Enqueue pending S3 media for detection")
    p.add_argument("--limit", type=int, default=None, help="Max jobs to enqueue")
    p.add_argument(
        "--sources",
        default="videographer",
        help="Comma list: videographer,user",
    )
    p.add_argument("--mode", default="vehicle", choices=("vehicle", "walking"))
    p.add_argument(
        "--requeue-failed",
        action="store_true",
        help="Also re-enqueue keys that previously failed",
    )
    args = p.parse_args()
    if args.requeue_failed:
        os.environ["DETECT_S3_REQUEUE_FAILED"] = "1"
    os.environ.setdefault("AUTO_DETECT_ON_UPLOAD", "1")
    os.environ.setdefault("AUTO_DETECT_SCAN_S3", "1")

    from routes.detect_queue import scan_and_enqueue_s3_pending

    sources = tuple(s.strip() for s in args.sources.split(",") if s.strip())
    summary = scan_and_enqueue_s3_pending(
        sources=sources or ("videographer",),
        capture_mode=args.mode,
        limit=args.limit,
    )
    print(summary)
    return 0 if not summary.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
