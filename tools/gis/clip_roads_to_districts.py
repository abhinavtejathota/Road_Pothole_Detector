#!/usr/bin/env python3
"""
Clip state roads to each district polygon → per-district road segments.

Inputs (run download scripts first):
  data/gis_states/<state>/roads.geojson
  data/gis_states/<state>/districts.geojson

Output:
  data/gis_states/<state>/road_segments.geojson
  data/gis_states/<state>/index/districts.json
  data/gis_states/<state>/index/segments/<district_id>.geojson

Usage:
  python tools/gis/clip_roads_to_districts.py --state both
  python tools/gis/clip_roads_to_districts.py --state andhra
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from state_geo import PBF_SOURCES, state_out_dir


def process_state(state_key: str) -> None:
    import geopandas as gpd

    out_dir = state_out_dir(state_key)
    roads_path = out_dir / "roads.geojson"
    dist_path = out_dir / "districts.geojson"
    if not roads_path.is_file():
        raise SystemExit(
            f"Missing {roads_path}\nRun: python tools/gis/download_state_roads.py --state {state_key} --all-roads"
        )
    if not dist_path.is_file():
        raise SystemExit(
            f"Missing {dist_path}\nRun: python tools/gis/download_districts.py"
        )

    label = PBF_SOURCES[state_key]["label"]
    print(f"\n=== {label}: clipping roads to districts ===")
    print("Loading roads and districts...")
    roads_parquet = out_dir / "roads.parquet"
    if roads_parquet.is_file():
        roads = gpd.read_parquet(roads_parquet).to_crs(epsg=32644)
    else:
        roads = gpd.read_file(roads_path).to_crs(epsg=32644)
    districts = gpd.read_file(dist_path).to_crs(epsg=32644)

    if "district_id" not in districts.columns or "district_name" not in districts.columns:
        raise SystemExit(f"districts.geojson missing district_id/district_name: {list(districts.columns)}")

    # Spatial overlay can be very heavy on 600k+ lines; use sindex + per-district clip
    print(f"  Roads: {len(roads):,}  Districts: {len(districts)}")
    print("  Clipping per district (spatial index)...")
    left = roads[["osmid", "name", "ref", "road_class", "geometry"]].copy()
    right = districts[["district_id", "district_name", "geometry"]].copy()
    right["district_id"] = right["district_id"].astype(str)

    parts = []
    sindex = left.sindex
    for i, (_, dist) in enumerate(right.iterrows(), start=1):
        hits = list(sindex.intersection(dist.geometry.bounds))
        if not hits:
            continue
        subset = left.iloc[hits]
        try:
            piece = gpd.clip(subset, dist.geometry)
        except Exception:
            continue
        if piece.empty:
            continue
        piece = piece.copy()
        piece["district_id"] = dist["district_id"]
        piece["district_name"] = dist["district_name"]
        parts.append(piece)
        if i % 5 == 0 or i == len(right):
            print(f"    {i}/{len(right)} districts...")

    if not parts:
        raise SystemExit(f"No segments after clip for {label}")
    import pandas as pd

    clipped = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=left.crs)

    clipped = clipped[~clipped.geometry.is_empty & clipped.geometry.notna()].copy()
    clipped["length_km"] = clipped.geometry.length / 1000.0
    clipped = clipped[clipped["length_km"] >= 0.001].copy()
    clipped["length_km"] = clipped["length_km"].round(3)
    clipped["osm_id"] = clipped.get("osmid", clipped.index)

    if clipped.empty:
        raise SystemExit(f"No segments after clip for {label}")

    out_gdf = clipped.to_crs(epsg=4326)
    seg_path = out_dir / "road_segments.geojson"
    out_gdf.to_file(seg_path, driver="GeoJSON")

    meta = {
        "state": label,
        "state_key": state_key,
        "segment_count": len(out_gdf),
        "total_km": round(float(out_gdf["length_km"].sum()), 1),
        "by_class": out_gdf.groupby("road_class")["length_km"].sum().round(1).to_dict(),
        "by_district": out_gdf.groupby("district_name")["length_km"].sum().round(1).to_dict(),
    }
    (out_dir / "road_segments_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Saved: {seg_path}")
    print(f"Segments: {meta['segment_count']:,}  Total: {meta['total_km']:,} km")

    # Per-district index for fast serving
    index_dir = out_dir / "index"
    seg_dir = index_dir / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)

    district_list = []
    for _, row in districts.to_crs(epsg=4326).iterrows():
        geom = row.geometry
        c = geom.centroid if geom is not None else None
        did = str(row["district_id"])
        district_list.append({
            "id": did,
            "district_id": did,
            "name": row["district_name"],
            "center": [round(c.y, 5), round(c.x, 5)] if c else None,
        })
    district_list.sort(key=lambda d: d["name"])
    (index_dir / "districts.json").write_text(json.dumps(district_list, indent=2), encoding="utf-8")

    keep_cols = [c for c in (
        "osm_id", "osmid", "name", "ref", "road_class", "length_km",
        "district_id", "district_name", "geometry",
    ) if c in out_gdf.columns]

    written = 0
    for did, group in out_gdf.groupby(out_gdf["district_id"].astype(str)):
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in did)
        group[keep_cols].to_file(seg_dir / f"{safe}.geojson", driver="GeoJSON")
        written += 1
    print(f"Index: {len(district_list)} districts, {written} segment files -> {index_dir}")


def main() -> None:
    try:
        import geopandas  # noqa: F401
    except ImportError as e:
        raise SystemExit("pip install -r tools/gis/requirements-gis.txt") from e

    parser = argparse.ArgumentParser(description="Clip AP/TG roads to districts")
    parser.add_argument(
        "--state",
        choices=("andhra", "telangana", "both"),
        default="both",
    )
    args = parser.parse_args()
    states = ("andhra", "telangana") if args.state == "both" else (args.state,)
    for key in states:
        process_state(key)
    print("\nDone.")


if __name__ == "__main__":
    main()
