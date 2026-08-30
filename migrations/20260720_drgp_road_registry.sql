-- DRGP Phase 4 — Digital Road Registry (Postgres / PostGIS)
-- Idempotent: safe on fresh DBs AND partial/older roads tables.
--
-- ID model
--   roads.id              = gis_segment_id  '{district_id}_{osm_id}'  (PK; survey compat)
--   roads.road_code       = human registry ID 'SR-{AP|TG}-{district}-{CLASS}-{osm_id}'
--   roads.osm_id          = OpenStreetMap way id (third-party, not govt registry)
--   roads.osm_ref         = route number when OSM has it (NH44, SH-1 — not unique per segment)
--   roads.district_id     = official LGD district code (see data/ref/district.txt)

CREATE EXTENSION IF NOT EXISTS postgis;

-- ── 1. Digital Road Registry (stub + additive columns) ───────────────────────

CREATE TABLE IF NOT EXISTS roads (
  id                      TEXT PRIMARY KEY
);

ALTER TABLE roads ADD COLUMN IF NOT EXISTS road_code               TEXT;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS gis_segment_id          TEXT;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS road_name               TEXT NOT NULL DEFAULT 'Unnamed';
ALTER TABLE roads ADD COLUMN IF NOT EXISTS osm_ref                 TEXT;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS from_location           TEXT;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS to_location             TEXT;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS road_class              TEXT NOT NULL DEFAULT 'other';
ALTER TABLE roads ADD COLUMN IF NOT EXISTS road_category           TEXT;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS osm_highway             TEXT;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS state_id                INTEGER;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS state_key               TEXT;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS district_id             INTEGER;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS district_name           TEXT;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS constituency            TEXT;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS mandal                  TEXT;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS village_ward            TEXT;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS length_km               DOUBLE PRECISION;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS width_m                 DOUBLE PRECISION;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS lanes                   INTEGER DEFAULT 2;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS surface_type            TEXT DEFAULT 'BT';
ALTER TABLE roads ADD COLUMN IF NOT EXISTS construction_date       DATE;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS construction_agency     TEXT;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS contractor_name         TEXT;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS vendor_id               INTEGER;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS project_cost            NUMERIC(14,2);
ALTER TABLE roads ADD COLUMN IF NOT EXISTS funding_source          TEXT;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS warranty_expiry_date    DATE;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS last_maintenance_date   DATE;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS last_inspection_date    DATE;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS current_rhi             INTEGER DEFAULT 100;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS current_mps             DOUBLE PRECISION;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS traffic_class           CHAR(1) DEFAULT 'L';
ALTER TABLE roads ADD COLUMN IF NOT EXISTS strategic_importance    INTEGER DEFAULT 1;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS survey_status           TEXT DEFAULT 'available';
ALTER TABLE roads ADD COLUMN IF NOT EXISTS osm_id                  BIGINT;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS geom                    GEOMETRY(GEOMETRY, 4326);
ALTER TABLE roads ADD COLUMN IF NOT EXISTS centroid                GEOMETRY(POINT, 4326);
ALTER TABLE roads ADD COLUMN IF NOT EXISTS source                  TEXT DEFAULT 'osm_clip';
ALTER TABLE roads ADD COLUMN IF NOT EXISTS import_batch_id         TEXT;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS is_active               BOOLEAN DEFAULT TRUE;
ALTER TABLE roads ADD COLUMN IF NOT EXISTS created_at              TIMESTAMP WITH TIME ZONE DEFAULT NOW();
ALTER TABLE roads ADD COLUMN IF NOT EXISTS updated_at              TIMESTAMP WITH TIME ZONE DEFAULT NOW();

-- Backfill keys on rows from older partial schemas
UPDATE roads SET gis_segment_id = id WHERE gis_segment_id IS NULL AND id IS NOT NULL;
UPDATE roads SET road_code = 'SR-MIG-' || id
WHERE road_code IS NULL AND id IS NOT NULL;

-- FK + CHECK constraints (skip if already present)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'roads_vendor_id_fkey') THEN
        ALTER TABLE roads
            ADD CONSTRAINT roads_vendor_id_fkey
            FOREIGN KEY (vendor_id) REFERENCES vendors(id) ON DELETE SET NULL;
    END IF;
EXCEPTION WHEN others THEN
    RAISE NOTICE 'roads_vendor_id_fkey not applied: %', SQLERRM;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'roads_length_positive') THEN
        ALTER TABLE roads ADD CONSTRAINT roads_length_positive CHECK (length_km IS NULL OR length_km > 0) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'roads_rhi_range') THEN
        ALTER TABLE roads ADD CONSTRAINT roads_rhi_range CHECK (current_rhi BETWEEN 0 AND 100) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'roads_traffic_class_check') THEN
        ALTER TABLE roads ADD CONSTRAINT roads_traffic_class_check CHECK (traffic_class IN ('H', 'M', 'L')) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'roads_strategic_check') THEN
        ALTER TABLE roads ADD CONSTRAINT roads_strategic_check CHECK (strategic_importance BETWEEN 1 AND 5) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'roads_road_class_check') THEN
        ALTER TABLE roads ADD CONSTRAINT roads_road_class_check CHECK (road_class IN ('nh', 'sh', 'mdr', 'other')) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'roads_survey_status_check') THEN
        ALTER TABLE roads ADD CONSTRAINT roads_survey_status_check CHECK (survey_status IN (
            'available', 'assigned', 'completed', 'verified',
            'approved', 'pending_approval'
        )) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'roads_id_matches_gis') THEN
        ALTER TABLE roads ADD CONSTRAINT roads_id_matches_gis CHECK (gis_segment_id IS NULL OR id = gis_segment_id) NOT VALID;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_roads_road_code_unique ON roads(road_code) WHERE road_code IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_roads_gis_segment_id_unique ON roads(gis_segment_id) WHERE gis_segment_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_roads_geom           ON roads USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_roads_centroid       ON roads USING GIST(centroid);
CREATE INDEX IF NOT EXISTS idx_roads_state_district ON roads(state_id, district_id);
CREATE INDEX IF NOT EXISTS idx_roads_state_key      ON roads(state_key);
CREATE INDEX IF NOT EXISTS idx_roads_road_class     ON roads(road_class);
CREATE INDEX IF NOT EXISTS idx_roads_osm_id         ON roads(osm_id);
CREATE INDEX IF NOT EXISTS idx_roads_osm_ref        ON roads(osm_ref) WHERE osm_ref IS NOT NULL AND osm_ref <> '';
CREATE INDEX IF NOT EXISTS idx_roads_traffic        ON roads(traffic_class);
CREATE INDEX IF NOT EXISTS idx_roads_rhi            ON roads(current_rhi);
CREATE INDEX IF NOT EXISTS idx_roads_mps            ON roads(current_mps);
CREATE INDEX IF NOT EXISTS idx_roads_survey_status  ON roads(survey_status);
CREATE INDEX IF NOT EXISTS idx_roads_vendor         ON roads(vendor_id);
CREATE INDEX IF NOT EXISTS idx_roads_warranty       ON roads(warranty_expiry_date);
CREATE INDEX IF NOT EXISTS idx_roads_active         ON roads(is_active) WHERE is_active = TRUE;

-- ── 2. RHI history ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS road_health_history (
  id                      SERIAL PRIMARY KEY,
  road_id                 TEXT NOT NULL REFERENCES roads(id) ON DELETE CASCADE,
  inspection_date         DATE NOT NULL,
  session_id              INTEGER REFERENCES video_sessions(id) ON DELETE SET NULL,
  score                   INTEGER NOT NULL,
  condition_class         TEXT NOT NULL,
  pothole_count           INTEGER NOT NULL DEFAULT 0,
  high_severity_count     INTEGER NOT NULL DEFAULT 0,
  medium_severity_count   INTEGER NOT NULL DEFAULT 0,
  low_severity_count      INTEGER NOT NULL DEFAULT 0,
  other_defect_count      INTEGER NOT NULL DEFAULT 0,
  damage_index            DOUBLE PRECISION,
  analysed_length_km      DOUBLE PRECISION,
  source                  TEXT NOT NULL DEFAULT 'ai_survey',
  notes                   TEXT,
  created_at              TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

ALTER TABLE road_health_history ADD COLUMN IF NOT EXISTS session_id INTEGER;
ALTER TABLE road_health_history ADD COLUMN IF NOT EXISTS damage_index DOUBLE PRECISION;
ALTER TABLE road_health_history ADD COLUMN IF NOT EXISTS analysed_length_km DOUBLE PRECISION;
ALTER TABLE road_health_history ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'ai_survey';
ALTER TABLE road_health_history ADD COLUMN IF NOT EXISTS notes TEXT;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'road_health_score_range') THEN
        ALTER TABLE road_health_history ADD CONSTRAINT road_health_score_range CHECK (score BETWEEN 0 AND 100) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'road_health_condition_check') THEN
        ALTER TABLE road_health_history ADD CONSTRAINT road_health_condition_check CHECK (condition_class IN (
            'Excellent', 'Good', 'Fair', 'Poor', 'Critical'
        )) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'road_health_road_date_source_key') THEN
        ALTER TABLE road_health_history ADD CONSTRAINT road_health_road_date_source_key UNIQUE (road_id, inspection_date, source);
    END IF;
EXCEPTION WHEN others THEN
    RAISE NOTICE 'road_health_history constraints: %', SQLERRM;
END $$;

CREATE INDEX IF NOT EXISTS idx_road_health_road    ON road_health_history(road_id);
CREATE INDEX IF NOT EXISTS idx_road_health_date    ON road_health_history(inspection_date DESC);
CREATE INDEX IF NOT EXISTS idx_road_health_session ON road_health_history(session_id);

-- ── 3. Dynamic traffic profiles ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS road_traffic_profiles (
  road_id                 TEXT NOT NULL REFERENCES roads(id) ON DELETE CASCADE,
  day_of_week             INTEGER NOT NULL,
  hour_of_day             INTEGER NOT NULL,
  average_speed_kmh       DOUBLE PRECISION,
  traffic_level           CHAR(1) NOT NULL,
  sample_count            INTEGER NOT NULL DEFAULT 0,
  source                  TEXT NOT NULL DEFAULT 'survey_gps',
  updated_at              TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  PRIMARY KEY (road_id, day_of_week, hour_of_day)
);

ALTER TABLE road_traffic_profiles ADD COLUMN IF NOT EXISTS sample_count INTEGER DEFAULT 0;
ALTER TABLE road_traffic_profiles ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'survey_gps';
ALTER TABLE road_traffic_profiles ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW();

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'road_traffic_dow_check') THEN
        ALTER TABLE road_traffic_profiles ADD CONSTRAINT road_traffic_dow_check CHECK (day_of_week BETWEEN 0 AND 6) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'road_traffic_hour_check') THEN
        ALTER TABLE road_traffic_profiles ADD CONSTRAINT road_traffic_hour_check CHECK (hour_of_day BETWEEN 0 AND 23) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'road_traffic_level_check') THEN
        ALTER TABLE road_traffic_profiles ADD CONSTRAINT road_traffic_level_check CHECK (traffic_level IN ('H', 'M', 'L')) NOT VALID;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_road_traffic_level ON road_traffic_profiles(traffic_level);

-- ── 4. Citizen complaints ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS citizen_complaints (
  id                      SERIAL PRIMARY KEY,
  tracking_number         TEXT NOT NULL UNIQUE,
  road_id                 TEXT REFERENCES roads(id) ON DELETE SET NULL,
  defect_type             TEXT NOT NULL,
  latitude                DOUBLE PRECISION NOT NULL,
  longitude               DOUBLE PRECISION NOT NULL,
  location                GEOMETRY(POINT, 4326),
  snap_distance_m         DOUBLE PRECISION,
  photo_s3_url            TEXT,
  description             TEXT,
  reporter_name           TEXT,
  reporter_phone          TEXT,
  reporter_email          TEXT,
  status                  TEXT NOT NULL DEFAULT 'Submitted',
  engineer_id             INTEGER REFERENCES users(id) ON DELETE SET NULL,
  work_order_id           INTEGER REFERENCES work_orders(id) ON DELETE SET NULL,
  contractor_notified     BOOLEAN NOT NULL DEFAULT FALSE,
  verified_at             TIMESTAMP WITH TIME ZONE,
  resolved_at             TIMESTAMP WITH TIME ZONE,
  closed_at               TIMESTAMP WITH TIME ZONE,
  created_at              TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at              TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

ALTER TABLE citizen_complaints ADD COLUMN IF NOT EXISTS snap_distance_m DOUBLE PRECISION;
ALTER TABLE citizen_complaints ADD COLUMN IF NOT EXISTS contractor_notified BOOLEAN DEFAULT FALSE;
ALTER TABLE citizen_complaints ADD COLUMN IF NOT EXISTS verified_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE citizen_complaints ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE citizen_complaints ADD COLUMN IF NOT EXISTS closed_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE citizen_complaints ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW();

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'citizen_complaint_status_check') THEN
        ALTER TABLE citizen_complaints ADD CONSTRAINT citizen_complaint_status_check CHECK (status IN (
            'Submitted', 'Verified', 'Rejected',
            'WorkOrder_Created', 'In_Progress', 'Resolved', 'Closed'
        )) NOT VALID;
    END IF;
EXCEPTION WHEN others THEN
    RAISE NOTICE 'citizen_complaints constraints: %', SQLERRM;
END $$;

CREATE INDEX IF NOT EXISTS idx_citizen_complaints_road      ON citizen_complaints(road_id);
CREATE INDEX IF NOT EXISTS idx_citizen_complaints_status     ON citizen_complaints(status);
CREATE INDEX IF NOT EXISTS idx_citizen_complaints_location    ON citizen_complaints USING GIST(location);
CREATE INDEX IF NOT EXISTS idx_citizen_complaints_created     ON citizen_complaints(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_citizen_complaints_engineer    ON citizen_complaints(engineer_id);

-- ── 5. Maintenance audit per road ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS road_maintenance_events (
  id                      SERIAL PRIMARY KEY,
  road_id                 TEXT NOT NULL REFERENCES roads(id) ON DELETE CASCADE,
  work_order_id           INTEGER REFERENCES work_orders(id) ON DELETE SET NULL,
  warranty_record_id      INTEGER REFERENCES warranty_records(id) ON DELETE SET NULL,
  session_id              INTEGER REFERENCES video_sessions(id) ON DELETE SET NULL,
  complaint_id            INTEGER REFERENCES citizen_complaints(id) ON DELETE SET NULL,
  event_type              TEXT NOT NULL,
  event_date              DATE NOT NULL,
  performed_by            TEXT,
  notes                   TEXT,
  cost_inr                NUMERIC(14,2),
  created_at              TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'road_maint_event_type_check') THEN
        ALTER TABLE road_maintenance_events ADD CONSTRAINT road_maint_event_type_check CHECK (event_type IN (
            'inspection', 'repair', 'resurfacing', 'warranty_claim', 'citizen_verify'
        )) NOT VALID;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_road_maint_road ON road_maintenance_events(road_id);
CREATE INDEX IF NOT EXISTS idx_road_maint_date ON road_maintenance_events(event_date DESC);
CREATE INDEX IF NOT EXISTS idx_road_maint_wo   ON road_maintenance_events(work_order_id);

-- ── 6. Session ↔ roads summary ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS video_session_roads (
  session_id              INTEGER NOT NULL REFERENCES video_sessions(id) ON DELETE CASCADE,
  road_id                 TEXT NOT NULL REFERENCES roads(id) ON DELETE CASCADE,
  pothole_count           INTEGER NOT NULL DEFAULT 0,
  analysed_length_km      DOUBLE PRECISION,
  rhi_snapshot            INTEGER,
  created_at              TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  PRIMARY KEY (session_id, road_id)
);

ALTER TABLE video_session_roads ADD COLUMN IF NOT EXISTS analysed_length_km DOUBLE PRECISION;
ALTER TABLE video_session_roads ADD COLUMN IF NOT EXISTS rhi_snapshot INTEGER;
ALTER TABLE video_session_roads ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_vsr_road ON video_session_roads(road_id);

-- ── 7. Existing table extensions ─────────────────────────────────────────────

ALTER TABLE potholes
  ADD COLUMN IF NOT EXISTS road_id         TEXT,
  ADD COLUMN IF NOT EXISTS snap_distance_m DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS snapped_at      TIMESTAMP WITH TIME ZONE;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'potholes_road_id_fkey') THEN
        ALTER TABLE potholes
            ADD CONSTRAINT potholes_road_id_fkey
            FOREIGN KEY (road_id) REFERENCES roads(id) ON DELETE SET NULL;
    END IF;
EXCEPTION WHEN others THEN
    RAISE NOTICE 'potholes_road_id_fkey not applied: %', SQLERRM;
END $$;

CREATE INDEX IF NOT EXISTS idx_potholes_road_id ON potholes(road_id);

ALTER TABLE work_orders
  ADD COLUMN IF NOT EXISTS primary_road_id TEXT;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'work_orders_primary_road_id_fkey') THEN
        ALTER TABLE work_orders
            ADD CONSTRAINT work_orders_primary_road_id_fkey
            FOREIGN KEY (primary_road_id) REFERENCES roads(id) ON DELETE SET NULL;
    END IF;
EXCEPTION WHEN others THEN
    RAISE NOTICE 'work_orders_primary_road_id_fkey not applied: %', SQLERRM;
END $$;

CREATE INDEX IF NOT EXISTS idx_work_orders_road ON work_orders(primary_road_id);

ALTER TABLE warranty_records
  ADD COLUMN IF NOT EXISTS road_id TEXT;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'warranty_records_road_id_fkey') THEN
        ALTER TABLE warranty_records
            ADD CONSTRAINT warranty_records_road_id_fkey
            FOREIGN KEY (road_id) REFERENCES roads(id) ON DELETE SET NULL;
    END IF;
EXCEPTION WHEN others THEN
    RAISE NOTICE 'warranty_records_road_id_fkey not applied: %', SQLERRM;
END $$;

CREATE INDEX IF NOT EXISTS idx_warranty_road ON warranty_records(road_id);

ALTER TABLE video_sessions
  ADD COLUMN IF NOT EXISTS state_key   TEXT,
  ADD COLUMN IF NOT EXISTS district_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_video_sessions_district ON video_sessions(district_id);

ALTER TABLE tracking_trail_points
  ADD COLUMN IF NOT EXISTS road_id TEXT;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'tracking_trail_points_road_id_fkey') THEN
        ALTER TABLE tracking_trail_points
            ADD CONSTRAINT tracking_trail_points_road_id_fkey
            FOREIGN KEY (road_id) REFERENCES roads(id) ON DELETE SET NULL;
    END IF;
EXCEPTION WHEN others THEN
    RAISE NOTICE 'tracking_trail_points_road_id_fkey not applied: %', SQLERRM;
END $$;

CREATE INDEX IF NOT EXISTS idx_tracking_trail_road ON tracking_trail_points(road_id);

INSERT INTO schema_migrations (id) VALUES ('20260720_drgp_road_registry')
ON CONFLICT (id) DO NOTHING;
