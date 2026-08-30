# Digital Roads Governance Platform (DRGP) - In-Depth Implementation Guide

This guide explains how to design, write, and integrate the missing components of the **Digital Roads Governance Platform (DRGP)**. It addresses your questions regarding:
1. How to map static GIS segments to a SQL **Road Registry**.
2. How to link point-based potholes to line-based roads for **per-road statistics**.
3. The separation between **Citizen Reporting** and **AI Detection**.
4. How to implement **Dynamic Traffic Classification** accounting for times of day.
5. How **Maintenance Priority Score (MPS)** dynamically scales with road damage.

---

## 1. Architectural Separation: Citizen Complaints vs. Scheduled Surveys

It is crucial to separate the data sources for system integrity. The system operates on two parallel tracks:

```
  [ Videographer App ] ───► AI Detection (Video) ───► Official Road Health Index (RHI)
  
  [ Citizen App ]      ───► Single Photo Upload  ───► Live Citizen Alerts (Tickets)
```

1. **Official Survey Detection**: Videographers use motorcycle mounts to capture continuous video. This streams through the chunked upload API and runs heavy parallel YOLOv12 inference. The output directly computes the official, monthly **Road Health Index (RHI)**.
2. **Citizen Reporting**: Citizens report individual issues ad-hoc. We do **not** run the video inference pipeline here. Instead, they capture a single photo and a category (e.g., Pothole, Water Logging). This creates a complaint ticket. 
3. **Data Integrity Guard**: Citizen reports **never** modify the RHI directly. They appear as a separate alert list. If verified by an engineer, they trigger a maintenance work order, which after repair and validation will ultimately improve the next month's RHI.

---

## 2. Implementing the Digital Road Registry & Spatial Pothole Snapping

Currently, your road network exists as static GeoJSON files in the `data/gis_states/` directory. To turn this into a **Digital Road Registry**, you must load these geometries into a Postgres/PostGIS database table and associate individual detected potholes with their corresponding road IDs.

### Step 1: SQL Schema Design for Road-Level Statistics
We define a relational `roads` table that stores PostGIS geometries (`LINESTRING`) and a view to aggregate statistics:

```sql
-- Create the roads registry table
CREATE TABLE IF NOT EXISTS roads (
    id                      TEXT PRIMARY KEY,              -- E.g. 'AP_GNT_10429'
    road_name               TEXT NOT NULL,
    road_class              TEXT NOT NULL,                  -- nh, sh, mdr, other
    state_key               TEXT NOT NULL,                  -- andhra, telangana
    district_id             INTEGER NOT NULL,
    length_km               DOUBLE PRECISION NOT NULL,
    surface_type            TEXT DEFAULT 'BT',              -- BT (Bituminous) / CC (Concrete)
    construction_date       DATE,
    contractor_name         TEXT,
    project_cost            NUMERIC(14,2),
    warranty_expiry_date    DATE,
    current_rhi             INTEGER DEFAULT 100,            -- Latest computed RHI
    geom                    GEOMETRY(LINESTRING, 4326)      -- Spatial road path
);

-- Index for fast spatial joins
CREATE INDEX IF NOT EXISTS idx_roads_geom ON roads USING GIST(geom);
```

### Step 2: Spatial Pothole Snapping (SQL/PostGIS)
Pothole coordinates are stored as Points in the `potholes` table. To calculate per-road statistics, we must determine which road segment each pothole belongs to.

We can run an update query using PostGIS's spatial distance operators to link each pothole to its nearest road within a 15-meter threshold:

```sql
-- Add road_id column to potholes table if not exists
ALTER TABLE potholes ADD COLUMN IF NOT EXISTS road_id TEXT REFERENCES roads(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_potholes_road ON potholes(road_id);

-- Snap orphan potholes to the closest road segment within a 15-meter threshold (0.00015 degrees approx)
UPDATE potholes p
SET road_id = (
    SELECT r.id 
    FROM roads r 
    WHERE ST_DWithin(p.location, r.geom, 0.00015)
    ORDER BY ST_Distance(p.location, r.geom) ASC 
    LIMIT 1
)
WHERE p.road_id IS NULL AND p.location IS NOT NULL;
```

### Step 3: Generating Per-Road Pothole Statistics
With the foreign key `road_id` populated, calculating statistics per road becomes a quick SQL query:

```sql
SELECT 
    r.id AS road_id,
    r.road_name,
    COUNT(p.id) AS total_potholes,
    COUNT(CASE WHEN p.severity = 'High' THEN 1 END) AS high_severity_potholes,
    COUNT(CASE WHEN p.severity = 'Medium' THEN 1 END) AS medium_severity_potholes,
    ROUND((COUNT(p.id) / r.length_km)::numeric, 2) AS pothole_density_per_km
FROM roads r
LEFT JOIN potholes p ON r.id = p.road_id
GROUP BY r.id, r.road_name;
```

---

## 3. Dynamic Traffic Classification (Handling Timings)

Road traffic is dynamic. A road classified as a Minor District Road (MDR) might suffer heavy congestion during morning office hours or crop harvesting seasons. 

To implement this without expensive physical vehicle-counters, we propose a **Traffic Profile** model:

### Step 1: SQL Schema for Traffic Logs
```sql
CREATE TABLE IF NOT EXISTS road_traffic_profiles (
    road_id             TEXT REFERENCES roads(id) ON DELETE CASCADE,
    day_of_week         INTEGER CHECK (day_of_week BETWEEN 0 AND 6), -- 0=Sunday, 6=Saturday
    hour_of_day         INTEGER CHECK (hour_of_day BETWEEN 0 AND 23),
    average_speed_kmh   DOUBLE PRECISION,
    traffic_level       CHAR(1) CHECK (traffic_level IN ('H', 'M', 'L')), -- High, Medium, Low
    PRIMARY KEY (road_id, day_of_week, hour_of_day)
);
```

### Step 2: How to Populate Dynamic Traffic Data
You can obtain this dynamic traffic level through two methods:

1.  **Survey Speed Anomalies (Zero Cost)**: 
    When videographers perform monthly surveys, they record GPS points containing timestamps and speed metrics (`tracking_trail_points` table). By comparing the surveyor's speed on a segment against the road's design speed limit, you can log congestion periods. If average speed drops below 40% of the limit, flag that hour as **High Traffic ('H')**.
2.  **OSRM Route Duration Logs**:
    During daily routing and map previews, query the OSRM backend or Google Maps Traffic API for current trip durations and save the speed index dynamically.

### Step 3: Determining the Active Traffic Weight
When calculating the MPS, determine the current traffic context based on the current system time:

```python
def get_current_traffic_class(road_id: str, db_conn) -> str:
    """
    Returns the active traffic class ('H', 'M', or 'L') based on current day/hour.
    Falls back to the road's baseline traffic class if profile data is missing.
    """
    now = datetime.now()
    day = now.weekday()
    hour = now.hour
    
    with db_conn.cursor() as cur:
        # Check for dynamic profile first
        cur.execute("""
            SELECT traffic_level FROM road_traffic_profiles
            WHERE road_id = %s AND day_of_week = %s AND hour_of_day = %s
        """, (road_id, day, hour))
        row = cur.fetchone()
        if row:
            return row[0]
            
        # Fallback to static registry base class
        cur.execute("SELECT traffic_class FROM roads WHERE id = %s", (road_id,))
        row = cur.fetchone()
        return row[0] if row else "L"
```

---

## 4. Refining the Maintenance Priority Score (MPS)

The MPS determines which roads are repaired first. As you suggested, **the score must be heavily weighted by the amount and severity of damage on the road.**

### Step 1: Defining Road Damage Severity
Instead of a simple pothole count, we calculate a **Damage Score** per kilometer using weights for defect types:

*   **High Severity Pothole**: 10 points
*   **Medium Severity Pothole**: 5 points
*   **Low Severity Pothole**: 2 points

$$\text{Damage Index} = \frac{(10 \times \text{High Count}) + (5 \times \text{Med Count}) + (2 \times \text{Low Count})}{\text{Road Length (km)}}$$

We then translate the **Damage Index** into the **Road Health Index (RHI)**:

$$\text{RHI} = \max(0, 100 - \text{Damage Index})$$

### Step 2: The MPS Formula
The MPS consolidates RHI (Damage), Dynamic Traffic, and Warranty statuses:

```python
def calculate_mps(road_id: str, db_conn) -> float:
    """
    Computes a maintenance priority score from 0 (lowest) to 100 (highest).
    """
    # 1. Fetch road details
    with db_conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT length_km, traffic_class, strategic_importance, warranty_expiry_date
            FROM roads WHERE id = %s
        """, (road_id,))
        road = cur.fetchone()
        
        # 2. Get active citizen complaints count
        cur.execute("""
            SELECT COUNT(*) FROM citizen_complaints 
            WHERE road_id = %s AND status IN ('Submitted', 'Verified')
        """, (road_id,))
        complaints_count = cur.fetchone()[0]
        
        # 3. Get pothole count and severity mapping
        cur.execute("""
            SELECT severity, COUNT(*) 
            FROM potholes 
            WHERE road_id = %s
            GROUP BY severity
        """, (road_id,))
        potholes = {row[0]: row[1] for row in cur.fetchall()}

    # Calculate Damage Index & RHI
    high_count = potholes.get("High", 0)
    med_count = potholes.get("Medium", 0)
    low_count = potholes.get("Low", 0)
    length = float(road["length_km"])
    
    damage_index = ((10 * high_count) + (5 * med_count) + (2 * low_count)) / length if length > 0 else 0
    rhi = max(0, 100 - damage_index)
    
    # 4. Get active traffic classification (dynamic hourly class)
    traffic = get_current_traffic_class(road_id, db_conn)
    traffic_weight = {"H": 100, "M": 60, "L": 20}[traffic]
    
    # 5. Strategic Weight (1 to 5 scale)
    strategic_weight = int(road["strategic_importance"] or 1) * 20
    
    # 6. Warranty Factor (If under warranty, repair belongs to contractor, reducing government priority)
    is_under_warranty = road["warranty_expiry_date"] and road["warranty_expiry_date"] >= datetime.today().date()
    warranty_weight = 0 if is_under_warranty else 100
    
    # 7. Complaint factor
    complaint_weight = min(100, complaints_count * 20)

    # Consolidation Weights
    mps = (
        0.45 * (100 - rhi) +         # 45% weight on actual physical damage
        0.20 * traffic_weight +      # 20% weight on road traffic level
        0.15 * strategic_weight +    # 15% weight on classification hierarchy
        0.10 * complaint_weight +    # 10% weight on citizen dissatisfaction
        0.10 * warranty_weight       # 10% weight on government financial relief
    )
    return round(mps, 2)
```

---

## 5. Detailed Step-by-Step Implementation Guide

To implement this functionality in your project, follow this step-by-step checklist:

### Step 1: Create the SQL Migration
Create a file named `migrations/20260721_road_registry.sql` containing the DDL from Section 2 & 3. Run the bootstrap migration utility or apply it using:
```bash
python -c "import db_utils; db_utils.init_db()"
```

### Step 2: Seed the Road Registry from GIS Files
Write a seeding script `scripts/seed_roads_registry.py` that reads your district GeoJSON files under `data/gis_states/` and inserts the features into the `roads` table:

```python
import json
import psycopg2
from pathlib import Path
import db_utils

def seed_roads():
    conn = db_utils.get_db_connection()
    cur = conn.cursor()
    
    gis_dir = Path("data/gis_states/andhra/index/segments")
    for filepath in gis_dir.glob("*.geojson"):
        district_id = filepath.stem
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        for feature in data.get("features", []):
            props = feature["properties"]
            geom = json.dumps(feature["geometry"])
            
            road_id = f"{district_id}_{props.get('osm_id')}"
            cur.execute("""
                INSERT INTO roads (id, road_name, road_class, state_key, district_id, length_km, geom)
                VALUES (%s, %s, %s, %s, %s, %s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))
                ON CONFLICT (id) DO NOTHING
            """, (
                road_id,
                props.get("name", "Unnamed Segment"),
                props.get("road_class", "other"),
                "andhra",
                int(district_id),
                float(props.get("length_km", 0.1)),
                geom
            ))
    conn.commit()
    print("Roads registry seeded successfully.")

if __name__ == '__main__':
    seed_roads()
```

### Step 3: Hook Spatial Snapping into the Detection Pipeline
Edit your `routes/detection_service.py` to trigger the spatial snapping query right after pothole coordinates are parsed and saved to the database:

```python
# Add this execution call at the end of your detection pipeline function:
def run_pothole_snapping():
    import db_utils
    query = """
        UPDATE potholes p
        SET road_id = (
            SELECT r.id 
            FROM roads r 
            WHERE ST_DWithin(p.location, r.geom, 0.00015)
            ORDER BY ST_Distance(p.location, r.geom) ASC 
            LIMIT 1
        )
        WHERE p.road_id IS NULL AND p.location IS NOT NULL;
    """
    db_utils.execute_write_query(query)
```

### Step 4: Expose Citizen Complaint Endpoints
In `routes/api.py`, write endpoints to handle citizen ticket submissions and snap them to the registered road network:

```python
@api_bp.route("/citizen/report", methods=["POST"])
def citizen_report():
    data = request.json
    lat = float(data["latitude"])
    lon = float(data["longitude"])
    defect = data["defect_type"]
    
    # 1. Query the closest Road ID using PostGIS
    conn = db_utils.get_db_connection()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id FROM roads 
            ORDER BY geom <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326) 
            LIMIT 1
        """, (lon, lat))
        row = cur.fetchone()
        road_id = row[0] if row else None
        
        # 2. Insert complaint ticket
        tracking_number = f"TKT-{random.randint(100000, 999999)}"
        cur.execute("""
            INSERT INTO citizen_complaints (tracking_number, road_id, defect_type, latitude, longitude, location)
            VALUES (%s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
        """, (tracking_number, road_id, defect, lat, lon, lon, lat))
    conn.commit()
    
    return jsonify({"status": "success", "tracking_number": tracking_number, "road_id": road_id})
```
