#!/usr/bin/env python3
"""
Download OSM road networks for Andhra Pradesh and/or Telangana into separate folders.

Default source: Geo2Day state PBF extracts (~70–80 MB each, daily OSM).
Optional: Geofabrik southern-zone (~530 MB) then filter to each state's districts.

Output:
  data/gis_states/andhra/roads.geojson
  data/gis_states/telangana/roads.geojson

Usage:
  python tools/gis/download_state_roads.py --state both --all-roads
  python tools/gis/download_state_roads.py --state andhra --all-roads
  python tools/gis/download_state_roads.py --state both --source geofabrik --all-roads --force
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.request import urlretrieve

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from state_geo import (
    GEOFABRIK_SOUTH_NAME,
    GEOFABRIK_SOUTH_URL,
    PBF_SOURCES,
    RAW_DIR,
    classify_row,
    filter_roads_to_districts,
    highway_tags,
    parse_ref_from_other_tags,
    state_out_dir,
)


def _progress(block: int, block_size: int, total: int) -> None:
    if total <= 0:
        return
    pct = min(100, block * block_size * 100 // total)
    print(
        f"\r  {pct:3d}% ({block * block_size / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB)",
        end="",
        flush=True,
    )


def download_file(url: str, dest: Path, *, force: bool, label: str) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and not force:
        mb = dest.stat().st_size / 1024 / 1024
        print(f"Using cached {label}: {dest} ({mb:.1f} MB)")
        return dest
    print(f"Downloading {label}...")
    print(f"  {url}")
    urlretrieve(url, dest, reporthook=_progress)
    print(f"\nSaved: {dest}")
    return dest


def _extract_with_pyosmium(pbf_path: Path, tags: set[str]):
    """Stream highway ways from PBF via pyosmium (much faster than GDAL OSM)."""
    import osmium
    import shapely.wkb as wkblib
    import geopandas as gpd

    wkbfab = osmium.geom.WKBFactory()

    class RoadHandler(osmium.SimpleHandler):
        def __init__(self) -> None:
            super().__init__()
            self.rows: list[dict] = []

        def way(self, w) -> None:
            hw = w.tags.get("highway")
            if not hw or hw not in tags:
                return
            if len(w.nodes) < 2:
                return
            # Skip ways with unresolved node locations (common near extract borders)
            try:
                if any(not n.location.valid() for n in w.nodes):
                    return
                wkb = wkbfab.create_linestring(w)
                geom = wkblib.loads(wkb, hex=True)
            except Exception:
                return
            self.rows.append({
                "osmid": int(w.id),
                "highway": hw,
                "name": w.tags.get("name", "") or "",
                "ref": w.tags.get("ref", "") or "",
                "geometry": geom,
            })

    print("  Reader: pyosmium")
    handler = RoadHandler()
    handler.apply_file(str(pbf_path), locations=True, idx="flex_mem")
    if not handler.rows:
        return None
    return gpd.GeoDataFrame(handler.rows, crs="EPSG:4326")


def extract_roads_from_pbf(pbf_path: Path, *, all_roads: bool):
    """Extract road lines from a state PBF. Prefers pyosmium; falls back to GDAL."""
    import geopandas as gpd

    tags = set(highway_tags(all_roads=all_roads))
    label = "all road types" if all_roads else "NH + SH + MDR"
    print(f"Extracting roads from PBF ({label})...")
    print(f"  {pbf_path.name}")

    gdf = None
    try:
        gdf = _extract_with_pyosmium(pbf_path, tags)
    except Exception as e:
        print(f"  pyosmium failed ({e}); trying GDAL OSM driver...")

    if gdf is None or gdf.empty:
        where = "highway IN (" + ",".join(f"'{h}'" for h in sorted(tags)) + ")"
        print("  Reader: GDAL/pyogrio (slower)")
        gdf = gpd.read_file(pbf_path, layer="lines", where=where)

    if gdf is None or gdf.empty:
        raise SystemExit(f"No road features in {pbf_path}")

    if "highway" not in gdf.columns:
        raise SystemExit(f"No highway column in extract. Columns: {list(gdf.columns)}")

    gdf = gdf[gdf["highway"].astype(str).str.lower().isin(tags)].copy()
    gdf = gdf[gdf.geometry.type.isin(["LineString", "MultiLineString"])].copy()
    print(f"  Line features: {len(gdf):,}")

    if "ref" in gdf.columns:
        refs = gdf["ref"].fillna("").astype(str)
        if "other_tags" in gdf.columns:
            missing = refs.eq("")
            refs = refs.where(~missing, gdf.loc[missing, "other_tags"].map(parse_ref_from_other_tags))
    elif "other_tags" in gdf.columns:
        refs = gdf["other_tags"].map(parse_ref_from_other_tags)
    else:
        refs = [""] * len(gdf)

    names = gdf["name"].fillna("").astype(str) if "name" in gdf.columns else [""] * len(gdf)
    gdf["road_class"] = [
        classify_row(hw, ref, name)
        for hw, ref, name in zip(gdf["highway"], refs, names)
    ]

    if "osmid" not in gdf.columns:
        if "osm_id" in gdf.columns:
            gdf["osmid"] = gdf["osm_id"]
        elif "id" in gdf.columns:
            gdf["osmid"] = gdf["id"]
        else:
            gdf["osmid"] = gdf.index

    gdf["ref"] = list(refs)
    gdf["name"] = list(names)
    gdf["highway"] = gdf["highway"].astype(str)

    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=4326)
    else:
        gdf = gdf.to_crs(epsg=4326)

    gdf_m = gdf.to_crs(epsg=32644)
    gdf["length_km"] = gdf_m.geometry.length / 1000.0
    return gdf


def load_districts(state_key: str):
    import geopandas as gpd

    path = state_out_dir(state_key) / "districts.geojson"
    if not path.is_file():
        raise SystemExit(
            f"Missing {path}\nRun first: python tools/gis/download_districts.py"
        )
    return gpd.read_file(path)


def save_roads(gdf, state_key: str, *, all_roads: bool, source: str, pbf_path: Path) -> Path:
    out_dir = state_out_dir(state_key)
    out_dir.mkdir(parents=True, exist_ok=True)
    geojson_path = out_dir / "roads.geojson"

    # Keep a lean attribute set for file size
    cols = [c for c in ("osmid", "name", "ref", "highway", "road_class", "length_km", "geometry") if c in gdf.columns]
    out = gdf[cols].copy()

    # Parquet first (fast, compact); GeoJSON for GIS tools that need it
    parquet_path = out_dir / "roads.parquet"
    try:
        out.to_parquet(parquet_path)
        print(f"Saved: {parquet_path}")
    except Exception as e:
        print(f"  parquet skip ({e})")

    geojson_path = out_dir / "roads.geojson"
    print(f"Writing GeoJSON (may take a few minutes for large networks)...")
    out.to_file(geojson_path, driver="GeoJSON")

    summary = out.groupby("road_class")["length_km"].sum().round(1).to_dict()
    meta = {
        "state": PBF_SOURCES[state_key]["label"],
        "state_key": state_key,
        "source": source,
        "pbf_file": str(pbf_path),
        "all_roads": all_roads,
        "feature_count": len(out),
        "total_km": round(float(out["length_km"].sum()), 1),
        "km_by_class": summary,
        "license": "ODbL — © OpenStreetMap contributors",
        "note": "State roads from Geo2Day OSM extract (or Geofabrik+filter). District segments via clip_roads_to_districts.py",
    }
    (out_dir / "roads_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"\nSaved: {geojson_path}")
    print(f"Features: {meta['feature_count']:,}  Total: {meta['total_km']:,} km")
    print("By class:", summary)
    return geojson_path


def process_state(state_key: str, *, all_roads: bool, source: str, force: bool, pbf_cache: dict) -> None:
    cfg = PBF_SOURCES[state_key]
    print(f"\n=== {cfg['label']} ===")

    if source == "geo2day":
        pbf = download_file(cfg["url"], RAW_DIR / cfg["pbf_name"], force=force, label=f"{cfg['label']} PBF")
        source_label = f"OpenStreetMap PBF (Geo2Day {cfg['label']} extract)"
    else:
        if "geofabrik" not in pbf_cache:
            pbf_cache["geofabrik"] = download_file(
                GEOFABRIK_SOUTH_URL,
                RAW_DIR / GEOFABRIK_SOUTH_NAME,
                force=force,
                label="Geofabrik southern-zone PBF (~530 MB)",
            )
        pbf = pbf_cache["geofabrik"]
        source_label = "OpenStreetMap PBF (Geofabrik southern-zone, filtered to state)"

    gdf = extract_roads_from_pbf(pbf, all_roads=all_roads)
    districts = load_districts(state_key)

    # Geo2Day extracts are already state-scoped — skip expensive state filter.
    # Geofabrik southern-zone covers multiple states — keep roads that hit any district.
    if source == "geofabrik":
        print(f"Filtering to {cfg['label']} districts ({len(districts)} polygons) via spatial join...")
        gdf = filter_roads_to_districts(gdf, districts)
    else:
        print(f"Geo2Day extract is state-scoped; skipping state filter ({len(districts)} districts on disk).")

    gdf_m = gdf.to_crs(epsg=32644)
    gdf["length_km"] = gdf_m.geometry.length / 1000.0
    gdf = gdf[gdf["length_km"] >= 0.001].copy()
    save_roads(gdf, state_key, all_roads=all_roads, source=source_label, pbf_path=pbf)


def main() -> None:
    try:
        import geopandas  # noqa: F401
    except ImportError as e:
        raise SystemExit("pip install -r tools/gis/requirements-gis.txt") from e

    parser = argparse.ArgumentParser(description="Download AP/TG roads into separate folders")
    parser.add_argument(
        "--state",
        choices=("andhra", "telangana", "both"),
        default="both",
        help="Which state folder(s) to build (default: both)",
    )
    parser.add_argument(
        "--all-roads",
        action="store_true",
        help="Include residential/service/track/etc (recommended for complete network).",
    )
    parser.add_argument(
        "--source",
        choices=("geo2day", "geofabrik"),
        default="geo2day",
        help="geo2day = per-state PBF (default, fast); geofabrik = southern-zone then clip (best border completeness).",
    )
    parser.add_argument("--force", action="store_true", help="Re-download PBF(s)")
    args = parser.parse_args()

    states = ("andhra", "telangana") if args.state == "both" else (args.state,)
    pbf_cache: dict = {}
    for key in states:
        process_state(
            key,
            all_roads=args.all_roads,
            source=args.source,
            force=args.force,
            pbf_cache=pbf_cache,
        )
    print("\nDone.")


if __name__ == "__main__":
    main()
