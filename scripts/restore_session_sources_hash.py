"""One-off: undo report display-name renames on processed sources, restore to input.

Renames sources/{user}/{route}/videoX_sN_....mp4 -> {sha256}.mp4 in the
processed bucket, then copies that hash key into the input videographer/ tree
so auto-detect can re-queue. Does not change app code.

Run:
  python scripts/restore_session_sources_hash.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from s3_utils import (  # noqa: E402
    copy_object,
    get_input_bucket,
    get_processed_bucket,
    object_exists,
    rename_object_safe,
)

# (session_id, processed display key, original hash basename)
JOBS = [
    (
        48,
        "sources/video5/my-location_my-location-20260727-1053/video5_s48_20260727-125854.mp4",
        "2392932e4d4e55b1b29df2fab34674d53ba5ed4637d5c9a62388ad5c4883c455.mp4",
    ),
    (
        49,
        "sources/video6/chintalapudi_settivarigudem-20260727-1137/video6_s49_20260727-133021.mp4",
        "f905f5e8b031535109a1013670abb3763cd3593e11d73e9a32df2fc0414285f6.mp4",
    ),
]


def _folder(key: str) -> str:
    return key.rsplit("/", 1)[0]


def main() -> int:
    pb = get_processed_bucket()
    ib = get_input_bucket()
    print(f"processed={pb}")
    print(f"input={ib}")

    for sid, display_key, hash_name in JOBS:
        folder = _folder(display_key)
        hash_proc = f"{folder}/{hash_name}"
        # sources/video5/... -> videographer/video5/...
        input_key = "videographer/" + hash_proc.removeprefix("sources/")

        print(f"\n=== session {sid} ===")
        print(f"  display: {display_key}")
        print(f"  hash:    {hash_proc}")
        print(f"  input:   {input_key}")

        if object_exists(pb, hash_proc):
            print("  processed already has hash name — skip rename")
        elif object_exists(pb, display_key):
            print("  renaming processed display -> hash (server-side copy)…")
            uri = rename_object_safe(pb, display_key, hash_proc, delete_source=True)
            print(f"  renamed: {uri}")
        else:
            print("  ERROR: neither display nor hash key found in processed")
            continue

        if object_exists(ib, input_key):
            print("  input already has hash mp4 — skip copy")
        else:
            print("  copying processed hash -> input…")
            uri = copy_object(pb, hash_proc, ib, input_key)
            print(f"  copied: {uri}")

        print(f"  done session {sid}")

    # Session 50: already restored as hash in input; drop leftover display name if present
    s50_display = (
        "videographer/video5/my-location_my-location-20260726-1352/"
        "video5_s50_20260727-135303.mp4"
    )
    s50_hash = (
        "videographer/video5/my-location_my-location-20260726-1352/"
        "a8d7f0123f4e7836f9376e0ae8dc23ab1407423456098598ec6a76ecac28ed45.mp4"
    )
    print("\n=== session 50 cleanup ===")
    if object_exists(ib, s50_hash) and object_exists(ib, s50_display):
        from s3_utils import delete_object

        print(f"  deleting duplicate display name in input: {s50_display}")
        delete_object(ib, s50_display)
        print("  deleted")
    elif object_exists(ib, s50_hash):
        print("  hash present in input; no display duplicate")
    else:
        print("  WARN: session 50 hash missing in input — not restoring here (no processed source)")

    print("\nAll jobs finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
