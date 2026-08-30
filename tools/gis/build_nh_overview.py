#!/usr/bin/env python3
"""Build NH-only overview GeoJSON for dashboard state zoom (from roads.parquet)."""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATES = ROOT / "data" / "gis_states"


def build(state_key: str) -> Path:
    import geopandas as gpd

    pq = STATES / state_key / "roads.parquet"
    out = STATES / state_key / "index" / "nh_overview.geojson"
    if not pq.is_file():
        raise SystemExit(f"Missing {pq}")
    out.parent.mkdir(parents=True, exist_ok=True)
    gdf = gpd.read_parquet(pq)
    nh = gdf[gdf["road_class"] == "nh"].copy()
    nh["geometry"] = nh.geometry.simplify(0.0003, preserve_topology=True)
    keep = [c for c in ("osmid", "name", "ref", "highway", "road_class", "length_km", "geometry") if c in nh.columns]
    nh[keep].to_file(out, driver="GeoJSON")
    print(f"{state_key}: {len(nh):,} NH features -> {out}")
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--state", choices=("andhra", "telangana", "both"), default="both")
    args = p.parse_args()
    keys = ("andhra", "telangana") if args.state == "both" else (args.state,)
    for k in keys:
        build(k)


if __name__ == "__main__":
    main()
