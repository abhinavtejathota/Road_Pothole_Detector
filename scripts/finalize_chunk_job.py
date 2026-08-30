#!/usr/bin/env python3
"""Run chunk finalize (ffmpeg + S3) in an isolated process.

Keeps the main Flask workers free so /detection and login stay responsive
while AceCloud finishes background assemble/upload after phone capture.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Cap native libs in this child so the parent Flask process keeps CPU.
for _k in (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
):
    os.environ.setdefault(_k, "2")
os.environ.setdefault("S3_UPLOAD_CONCURRENCY", os.getenv("FINALIZE_S3_CONCURRENCY", "8"))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
except Exception:
    pass


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: finalize_chunk_job.py <payload.json> <result.json>", file=sys.stderr)
        return 2
    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    payload = json.loads(in_path.read_text(encoding="utf-8"))
    from routes.field_upload_service import finalize_chunk_session

    result = finalize_chunk_session(**payload)
    out_path.write_text(json.dumps(result, default=str), encoding="utf-8")
    kind = (result.get("status") or {}).get("kind")
    return 0 if kind == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
