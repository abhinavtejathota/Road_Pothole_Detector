"""One-off: delete today's requeued videos from the processed bucket."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from s3_utils import _client, get_processed_bucket

# From today's sessions (sources + runs) — refresh before each purge.
PREFIXES = [
    # sources (video + archived GPS/json + any frames/)
    "sources/video5/my-location-my-location-20260721-1636/",
    "sources/video5/my-location-y-s-20260721-1211/",
    "sources/video5/my-location-y-s-20260721-1221/",
    "sources/video5/my-location-y-s-20260721-1225/",
    "sources/video5/my-location_my-location-20260726-1352/",
    "sources/video5/my-location_my-location-20260727-1053/",
    "sources/video6/chintalapudi_settivarigudem-20260727-1137/",
    # runs for those sessions
    "runs/video5/my-location-my-location-20260721-1636/20260727_190927/",
    "runs/video5/my-location-y-s-20260721-1211/20260727_191808/",
    "runs/video5/my-location-y-s-20260721-1221/20260727_191844/",
    "runs/video5/my-location-y-s-20260721-1225/20260727_191925/",
    "runs/video5/my-location_my-location-20260726-1352/20260727_191943/",
    "runs/video5/my-location_my-location-20260727-1053/20260727_192629/",
    "runs/video6/chintalapudi_settivarigudem-20260727-1137/20260727_193539/",
]


def delete_prefix(bucket: str, prefix: str) -> int:
    cli = _client()
    deleted = 0
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        resp = cli.list_objects_v2(**kwargs)
        objs = resp.get("Contents") or []
        if not objs:
            break
        # delete in batches of 1000
        for i in range(0, len(objs), 1000):
            chunk = objs[i : i + 1000]
            cli.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": o["Key"]} for o in chunk], "Quiet": True},
            )
            deleted += len(chunk)
            for o in chunk[:5]:
                print("  del", o["Key"])
            if len(chunk) > 5:
                print(f"  ... +{len(chunk) - 5} more in batch")
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    return deleted


def main() -> int:
    bucket = get_processed_bucket()
    print("processed bucket:", bucket)
    total = 0
    for prefix in PREFIXES:
        n = delete_prefix(bucket, prefix)
        print(f"{prefix} -> {n} objects")
        total += n
    print(f"TOTAL deleted: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
