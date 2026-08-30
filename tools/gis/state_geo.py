"""Shared helpers for Andhra Pradesh / Telangana district-wise road GIS."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATES_DIR = ROOT / "data" / "gis_states"
RAW_DIR = STATES_DIR / "raw"

# Geo2Day daily OSM extracts (state-scoped PBF — fast, complete interior coverage)
PBF_SOURCES = {
    "andhra": {
        "url": "https://geo2day.com/asia/india/andhra_pradesh.pbf",
        "pbf_name": "andhra_pradesh.osm.pbf",
        "label": "Andhra Pradesh",
        "out_dir": "andhra",
        # LGD / Bharatmaps state name variants
        "state_names": {"andhra pradesh", "andhra pradesh (ap)"},
        "state_codes": {"28", "AP", "ap"},
    },
    "telangana": {
        "url": "https://geo2day.com/asia/india/telangana.pbf",
        "pbf_name": "telangana.osm.pbf",
        "label": "Telangana",
        "out_dir": "telangana",
        "state_names": {"telangana", "telangana (tg)", "telengana"},
        "state_codes": {"36", "TS", "TG", "ts", "tg"},
    },
}

# Geofabrik southern zone — larger (~530 MB) but keeps roads that cross state borders
GEOFABRIK_SOUTH_URL = "https://download.geofabrik.de/asia/india/southern-zone-latest.osm.pbf"
GEOFABRIK_SOUTH_NAME = "southern-zone-latest.osm.pbf"

LGD_DISTRICTS_URL = (
    "https://github.com/ramSeraph/indian_admin_boundaries/releases/download/"
    "districts/LGD_Districts.parquet"
)
LGD_DISTRICTS_NAME = "LGD_Districts.parquet"

# Soft buffer (metres) so border roads are not dropped by polygon edge mismatch
STATE_CLIP_BUFFER_M = 150.0

NH_TAGS = {"motorway", "motorway_link", "trunk", "trunk_link"}
SH_TAGS = {"primary", "primary_link"}
MDR_TAGS = {"secondary", "secondary_link", "tertiary", "tertiary_link"}
OTHER_TAGS = {
    "unclassified",
    "residential",
    "living_street",
    "service",
    "road",
    "track",
}


def state_out_dir(state_key: str) -> Path:
    cfg = PBF_SOURCES[state_key]
    return STATES_DIR / cfg["out_dir"]


def classify_row(highway: str | None, ref: str | None, name: str | None) -> str:
    h = (highway or "").lower()
    r = (ref or "").upper()
    n = (name or "").upper()
    if h in NH_TAGS or r.startswith("NH") or "NATIONAL HIGHWAY" in n:
        return "nh"
    if h in SH_TAGS or r.startswith("SH") or "STATE HIGHWAY" in n:
        return "sh"
    if h in MDR_TAGS:
        return "mdr"
    return "other"


def highway_tags(*, all_roads: bool) -> list[str]:
    tags = NH_TAGS | SH_TAGS | MDR_TAGS
    if all_roads:
        tags = tags | OTHER_TAGS
    return sorted(tags)


def parse_ref_from_other_tags(other_tags) -> str:
    if other_tags is None or (isinstance(other_tags, float) and other_tags != other_tags):
        return ""
    text = str(other_tags)
    if not text:
        return ""
    marker = '"ref"=>"'
    if marker in text:
        try:
            return text.split(marker, 1)[1].split('"', 1)[0]
        except IndexError:
            pass
    return ""


def normalize_col(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def find_col(columns, *candidates: str) -> str | None:
    norm = {normalize_col(c): c for c in columns}
    for cand in candidates:
        key = normalize_col(cand)
        if key in norm:
            return norm[key]
    return None


def filter_state_districts(gdf, state_key: str):
    """Keep LGD district rows for one state."""
    cfg = PBF_SOURCES[state_key]
    names = cfg["state_names"]
    codes = {str(c).upper() for c in cfg["state_codes"]}

    state_col = find_col(
        gdf.columns,
        "stname",
        "state_name",
        "ST_NAME",
        "STATE",
        "State_Name",
        "state",
    )
    code_col = find_col(
        gdf.columns,
        "state_lgd",
        "state_lgd_code",
        "statecode",
        "ST_CODE",
        "state_code",
        "st_cd",
        "ST_CD",
        "stcode11",
        "stateid",
    )

    mask = None
    if state_col:
        vals = gdf[state_col].astype(str).str.strip().str.lower()
        mask = vals.isin(names)
        # also allow contains for slight naming variants
        if not mask.any():
            mask = vals.apply(lambda v: any(n in v for n in names))
    if (mask is None or not mask.any()) and code_col:
        codes_series = gdf[code_col].astype(str).str.strip().str.upper()
        mask = codes_series.isin(codes)
    if mask is None or not mask.any():
        raise SystemExit(
            f"Could not filter {cfg['label']} districts. Columns: {list(gdf.columns)}"
        )
    out = gdf[mask].copy()
    if out.empty:
        raise SystemExit(f"No districts matched for {cfg['label']}")
    return out


def district_id_col(gdf) -> str | None:
    return find_col(
        gdf.columns,
        "dist_lgd",
        "district_lgd_code",
        "dtcode11",
        "DISTRICT_C",
        "district_code",
        "dt_code",
        "DIST_CODE",
        "lgd_code",
        "DISTRICT_CODE",
        "OBJECTID",
    )


def district_name_col(gdf) -> str | None:
    return find_col(
        gdf.columns,
        "dtname",
        "district_name",
        "DISTRICT",
        "DIST_NAME",
        "name",
        "NAME",
    )


def filter_roads_to_districts(roads_gdf, districts_gdf):
    """Keep roads that intersect any district polygon (spatial join; no geometry cut)."""
    roads = roads_gdf.to_crs(epsg=4326)
    districts = districts_gdf.to_crs(epsg=4326)[["geometry"]].copy()
    before = len(roads)
    hit = roads.sjoin(districts, how="inner", predicate="intersects")
    kept = roads.loc[hit.index.unique()].copy()
    dropped = before - len(kept)
    if dropped:
        print(f"  Spatial join filter: removed {dropped:,} outside features (kept {len(kept):,}).")
    else:
        print(f"  Spatial join filter: all {len(kept):,} features intersect a district.")
    return kept


def clip_to_state(roads_gdf, districts_gdf, *, buffer_m: float = STATE_CLIP_BUFFER_M):
    """Deprecated alias — prefer filter_roads_to_districts for speed."""
    return filter_roads_to_districts(roads_gdf, districts_gdf)
