# How to obtain REAL road network & constituency data (Andhra Pradesh)

No mock data — this is the pipeline to load **actual** geometry into `data/gis/` for the survey module.

---

## Overview

| Layer | Source | Output file |
|-------|--------|-------------|
| **Road network** | OSM PBF extract (Geo2Day AP, ~70 MB) | `ap_roads.geojson` |
| **175 AC boundaries** | DataMeet shapefile | `ap_constituencies.geojson` |
| **Roads per AC** | Clip roads ∩ constituency polygon | `ap_road_segments.geojson` |

License: OSM = **ODbL** (attribute © OpenStreetMap contributors). AC boundaries = DataMeet / CC-BY 2.5 India.

---

## Step 0 — Install GIS tools (one time)

```powershell
cd d:\Docs\Problem Solving\Forks\smart_road_app
venv\Scripts\activate
pip install -r road_network\requirements-gis.txt
```

---

## Step 1 — Download roads (PBF, no Overpass)

Downloads a pre-cut **Andhra Pradesh** OSM extract once (~70 MB), then filters locally with GDAL/pyogrio (~1–2 min).

```powershell
# NH + SH + MDR (survey priority — recommended first)
python road_network\download_ap_roads.py

# All road types including residential/service/tertiary (larger output)
python road_network\download_ap_roads.py --all-roads

# Re-download PBF and re-export
python road_network\download_ap_roads.py --force
```

Outputs:

- `data/gis/raw/andhra_pradesh.osm.pbf` — cached source file
- `data/gis/ap_roads.geojson`
- `data/gis/ap_roads_meta.json` (km by class: nh / sh / mdr / other)

### Road classification

| Class | OSM tags used |
|-------|----------------|
| **nh** | `motorway`, `trunk`, or `ref` starts with NH |
| **sh** | `primary`, or ref/name suggests state highway |
| **mdr** | `secondary` |
| **other** | tertiary, unclassified, residential, etc. |

---

## Step 2 — Download assembly constituency boundaries (175 AC)

```powershell
python road_network\download_ap_constituencies.py
```

Output: `data/gis/ap_constituencies.geojson` (175 ACs, post-2014 filter applied).

---

## Step 3 — Clip roads to constituencies

```powershell
python road_network\clip_roads_to_constituencies.py
python road_network\build_survey_index.py
```

Output: `data/gis/ap_road_segments.geojson` and `data/gis/index/` (per-AC files for the API).

---

## Recommended order

1. `pip install -r road_network\requirements-gis.txt`
2. `python road_network\download_ap_constituencies.py` — verify 175 ACs
3. `python road_network\download_ap_roads.py` — NH/SH/MDR first
4. `python road_network\clip_roads_to_constituencies.py`
5. `python road_network\build_survey_index.py`
6. Review `ap_road_segments_meta.json` — km per AC / per class
7. Portal: `/survey` and `/survey/admin` (real API — no mocks)

---

## Files in this repo

| Script | Purpose |
|--------|---------|
| `tools/gis/download_ap_roads.py` | PBF download + extract → GeoJSON |
| `tools/gis/download_ap_constituencies.py` | AC boundaries → GeoJSON |
| `tools/gis/clip_roads_to_constituencies.py` | Intersection → segments |
| `tools/gis/requirements-gis.txt` | Python GIS deps |

Generated data lives in `data/gis/` (gitignored).

See also: `docs/AP_CONSTITUENCY_SURVEY.md`, `todo.txt`
