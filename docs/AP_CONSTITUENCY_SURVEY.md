# AP Constituency Road Survey — Product spec (draft)

Scope: **Andhra Pradesh only** · 175 assembly constituencies · videographer field survey for pothole detection coverage.

---

## Agreed requirements (from planning)

| Topic | Decision |
|-------|----------|
| Users per AC | 1 primary + optional backup videographers per constituency |
| Daily target | **100 km per videographer per day** (admin-configurable later in UI) |
| Timezone | Assignments fixed for calendar day **IST** |
| Road priority | **NH → SH → MDR → other** within AC |
| Repeat policy | **Supervisor-approved** segments are not re-assigned; supervisor may **force re-survey** |
| Completion | Upload + supervisor approval before segment shows complete on map |
| Capture (v1) | **Gradio** for video/GPS upload; **portal** for map + assignments |
| Upload ↔ segment | **Deferred** (see `todo.txt`) |
| AC boundaries | **Not implemented yet** — sources recommended below |

---

## Frontend map approach (options evaluated)

### Option A — Leaflet + GeoJSON from API (chosen for prototype)

- **Pros:** Already in stack (`PotholeMap`), light tiles, easy polylines, zoom events for layer declutter.
- **Cons:** Large AC road networks need tiling/vector simplification at scale.

**Admin declutter:** At zoom &lt; 12 show **NH only**; zoom ≥ 12 show NH + SH + MDR + other (prototype in `RoadSurveyMap`).

### Option B — MapLibre GL JS + vector tiles

- **Pros:** Best performance for 175 ACs × full road network, smooth zoom styling.
- **Cons:** New dependency; needs tile server or PMTiles hosting.

**Use when:** Full-state road import exceeds ~50k segments in browser GeoJSON.

### Option C — deck.gl / Mapbox overlay

- **Pros:** GPU layers, good for status coloring at scale.
- **Cons:** Heavier; licensing for Mapbox.

### Option D — External GIS (QGIS / ArcGIS) embed

- **Pros:** Government teams already use GIS.
- **Cons:** Poor fit for videographer mobile workflow; not integrated with auth.

**Recommendation:** Start **Option A** (current prototype). Move to **Option B (PMTiles)** when real OSM import is loaded.

---

## Where to get 175 AC boundaries (when ready)

| Source | Notes |
|--------|--------|
| **Election Commission of India** | Authoritative AC boundaries; often shared as shapefile during elections. Check AP CEO / datagov.in. |
| **data.gov.in / AP Open Government** | Search "assembly constituency" + Andhra Pradesh. |
| **OpenStreetMap** | Relations tagged `boundary=political`, `admin_level` — coverage varies; needs validation against ECI. |
| **GADM / geoBoundaries** | Global admin boundaries; may not match latest delimitation. |
| **Bhuvan (ISRO)** | Indian admin layers; may need registration. |

**Pilot suggestion:** Start with **1–3 ACs** (e.g. Vijayawada East/West, Guntur) from ECI shapefile or verified OSM relation, then scale import script.

---

## Road network data (AP)

| Source | Road types | Notes |
|--------|------------|--------|
| **OpenStreetMap** (Overpass / Geofabrik India extract) | NH, SH, MDR, local | Good default; clip to AC polygon; tag `highway=*`, ref=NH/SH. |
| **NHAI / NH network lists** | National highways | Cross-check OSM `ref=NH*` for AP; official list on nhai.gov.in. |
| **AP Roads & Buildings Dept** | SH, MDR | May need RTI / government MOU for linework. |
| **Google / MapmyIndia** | All | Licensing restrictions for government deployment. |

**Recommendation:** **OSM clipped per AC** for v1; merge NHAI CSV of NH segments for validation layer.

---

## Roles (planned)

| Role | Access |
|------|--------|
| **Admin** | All ACs, all road layers, km/day config, user↔AC assignment, force re-survey |
| **Supervisor** | Approve completions, re-assign repeats |
| **Videographer** | Own AC only, today's assigned segments, mark complete (pending approval) |
| Existing roles | Unchanged (Allocator, Vendor, …) |

---

## Prototype routes (frontend)

| Path | Audience | Purpose |
|------|----------|---------|
| `/survey` | Videographer (Admin preview) | Today's assignments + map |
| `/survey/admin` | Admin | AC picker, full network, km/day setting |

Data is served from **`/api/survey/*`** reading `data/gis/index/` (built by `build_survey_index.py`).
