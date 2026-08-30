#!/usr/bin/env python3
"""
Download current LGD district boundaries and split into Andhra / Telangana folders.

Source: ramSeraph indian_admin_boundaries (LGD / Bharatmaps, CC0)
Output:
  data/gis_states/andhra/districts.geojson
  data/gis_states/telangana/districts.geojson

Usage:
  python tools/gis/download_districts.py
  python tools/gis/download_districts.py --force
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.request import urlretrieve

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from state_geo import (
    LGD_DISTRICTS_NAME,
    LGD_DISTRICTS_URL,
    PBF_SOURCES,
    RAW_DIR,
    district_id_col,
    district_name_col,
    filter_state_districts,
    find_col,
    state_out_dir,
)


def download_lgd(*, force: bool) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / LGD_DISTRICTS_NAME
    if path.is_file() and not force:
        mb = path.stat().st_size / 1024 / 1024
        print(f"Using cached districts: {path} ({mb:.1f} MB)")
        return path

    print("Downloading LGD district boundaries (parquet, ~33 MB)...")
    print(f"  {LGD_DISTRICTS_URL}")

    def _progress(block: int, block_size: int, total: int) -> None:
        if total <= 0:
            return
        pct = min(100, block * block_size * 100 // total)
        print(
            f"\r  {pct:3d}% ({block * block_size / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB)",
            end="",
            flush=True,
        )

    urlretrieve(LGD_DISTRICTS_URL, path, reporthook=_progress)
    print(f"\nSaved: {path}")
    return path


def normalize_districts(gdf, state_key: str):
    """Standardize id/name columns for downstream clip scripts."""
    id_col = district_id_col(gdf)
    name_col = district_name_col(gdf)
    if not name_col:
        raise SystemExit(f"No district name column in {list(gdf.columns)}")

    out = gdf.copy()
    if id_col:
        out["district_id"] = out[id_col].astype(str).str.strip()
    else:
        # Stable fallback from name order within state
        out = out.sort_values(name_col).reset_index(drop=True)
        out["district_id"] = [f"{state_key[:2].upper()}{i+1:02d}" for i in range(len(out))]

    out["district_name"] = out[name_col].astype(str).str.strip()
    state_name_col = find_col(out.columns, "stname", "state_name", "ST_NAME", "STATE", "State_Name")
    if state_name_col:
        out["state_name"] = out[state_name_col].astype(str).str.strip()
    else:
        out["state_name"] = PBF_SOURCES[state_key]["label"]

    keep = ["district_id", "district_name", "state_name", "geometry"]
    # Preserve useful LGD codes if present
    for extra in ("dist_lgd", "state_lgd", "dtcode11", "stcode11"):
        col = find_col(out.columns, extra)
        if col and col not in keep:
            out[extra] = out[col]
            keep.insert(-1, extra)
    return out[keep]


def save_state(gdf, state_key: str) -> Path:
    out_dir = state_out_dir(state_key)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "districts.geojson"
    gdf.to_crs(epsg=4326).to_file(path, driver="GeoJSON")

    meta = {
        "state": PBF_SOURCES[state_key]["label"],
        "state_key": state_key,
        "count": len(gdf),
        "source": LGD_DISTRICTS_URL,
        "license": "CC0 1.0 — attribute LGD/Bharatmaps / ramSeraph indian_admin_boundaries where possible",
        "districts": sorted(gdf["district_name"].tolist()),
    }
    (out_dir / "districts_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"  {PBF_SOURCES[state_key]['label']}: {len(gdf)} districts -> {path}")
    return path


def main() -> None:
    try:
        import geopandas as gpd
    except ImportError as e:
        raise SystemExit("pip install -r tools/gis/requirements-gis.txt") from e

    try:
        import pyarrow  # noqa: F401
    except ImportError as e:
        raise SystemExit(
            "Missing pyarrow (needed to read LGD parquet).\n"
            "  pip install pyarrow\n"
            "  or: pip install -r tools/gis/requirements-gis.txt"
        ) from e

    import argparse

    parser = argparse.ArgumentParser(description="Download LGD districts for AP and Telangana")
    parser.add_argument("--force", action="store_true", help="Re-download parquet")
    args = parser.parse_args()

    parquet_path = download_lgd(force=args.force)
    print(f"Reading: {parquet_path}")
    gdf = gpd.read_parquet(parquet_path)
    print(f"  All-India districts: {len(gdf):,}  columns: {list(gdf.columns)}")

    for state_key in ("andhra", "telangana"):
        state_gdf = filter_state_districts(gdf, state_key)
        state_gdf = normalize_districts(state_gdf, state_key)
        save_state(state_gdf, state_key)

    print("\nDone.")


if __name__ == "__main__":
    main()
