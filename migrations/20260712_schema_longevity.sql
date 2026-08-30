-- Longevity / consistency upgrade for SmartRoad AP survey + core schema.
-- Idempotent. Prefer: python -c "import db_utils; db_utils.init_db()" (runs schema.sql).
-- Or: psql -d smartroad_ap -f migrations/20260712_schema_longevity.sql

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS schema_migrations (
    id              TEXT PRIMARY KEY,
    applied_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

ALTER TABLE users ADD COLUMN IF NOT EXISTS district_ids INTEGER[];
ALTER TABLE users ADD COLUMN IF NOT EXISTS state_id INTEGER;
ALTER TABLE users ADD COLUMN IF NOT EXISTS district_id INTEGER;

ALTER TABLE survey_daily_assignments ADD COLUMN IF NOT EXISTS legs JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE survey_daily_assignments ADD COLUMN IF NOT EXISTS polyline JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE survey_daily_assignments ADD COLUMN IF NOT EXISTS route_km DOUBLE PRECISION;
ALTER TABLE survey_daily_assignments ADD COLUMN IF NOT EXISTS route_geom GEOMETRY(LINESTRING, 4326);
ALTER TABLE survey_daily_assignments ADD COLUMN IF NOT EXISTS continuous BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE survey_daily_assignments ADD COLUMN IF NOT EXISTS preferred_km DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE survey_daily_assignments ADD COLUMN IF NOT EXISTS connector_km DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE survey_daily_assignments ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'corridor';
ALTER TABLE survey_daily_assignments ADD COLUMN IF NOT EXISTS meta JSONB NOT NULL DEFAULT '{}'::jsonb;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_vendor_id_fkey') THEN
        ALTER TABLE users
            ADD CONSTRAINT users_vendor_id_fkey
            FOREIGN KEY (vendor_id) REFERENCES vendors(id) ON DELETE SET NULL;
    END IF;
EXCEPTION WHEN others THEN
    RAISE NOTICE 'users_vendor_id_fkey not applied: %', SQLERRM;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_role_check') THEN
        ALTER TABLE users ADD CONSTRAINT users_role_check
            CHECK (role IN ('Admin', 'Allocator', 'Supervisor', 'Vendor', 'Videographer')) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'vendors_status_check') THEN
        ALTER TABLE vendors ADD CONSTRAINT vendors_status_check
            CHECK (status IS NULL OR status IN ('Active', 'Suspended', 'Blacklisted')) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'work_orders_status_check') THEN
        ALTER TABLE work_orders ADD CONSTRAINT work_orders_status_check
            CHECK (status IN ('Created', 'Allocated', 'WIP', 'Completed', 'Verified', 'Failed')) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'survey_seg_status_check') THEN
        ALTER TABLE survey_segment_status ADD CONSTRAINT survey_seg_status_check
            CHECK (status IN (
                'available', 'assigned', 'completed', 'verified',
                'approved', 'pending_approval'
            )) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'survey_assign_mode_check') THEN
        ALTER TABLE survey_daily_assignments ADD CONSTRAINT survey_assign_mode_check
            CHECK (mode IN ('corridor', 'nearest', 'manual')) NOT VALID;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_survey_assign_route ON survey_daily_assignments USING GIST(route_geom);
CREATE INDEX IF NOT EXISTS idx_survey_assign_district ON survey_daily_assignments(district_id);
CREATE INDEX IF NOT EXISTS idx_survey_assign_state ON survey_daily_assignments(state_key);
CREATE INDEX IF NOT EXISTS idx_survey_seg_status ON survey_segment_status(status);
CREATE INDEX IF NOT EXISTS idx_users_vendor ON users(vendor_id);
CREATE INDEX IF NOT EXISTS idx_users_state ON users(state_id);
CREATE INDEX IF NOT EXISTS idx_users_district ON users(district_id);
CREATE INDEX IF NOT EXISTS idx_tracking_user ON tracking_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_tracking_trail_ts ON tracking_trail_points(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_potholes_created ON potholes(created_at);
CREATE INDEX IF NOT EXISTS idx_video_sessions_run ON video_sessions(run_id);
CREATE INDEX IF NOT EXISTS idx_video_sessions_processed ON video_sessions(processed_at);
CREATE INDEX IF NOT EXISTS idx_vendors_status ON vendors(status);

-- Backfill route_geom from stored polyline [[lat,lon], ...] when missing
UPDATE survey_daily_assignments s
SET route_geom = sub.geom
FROM (
    SELECT id,
           ST_SetSRID(
               ST_MakeLine(
                   ARRAY(
                       SELECT ST_MakePoint(
                           (pt->>1)::float8,  -- lon
                           (pt->>0)::float8   -- lat
                       )
                       FROM jsonb_array_elements(polyline) AS pt
                       WHERE jsonb_typeof(pt) = 'array'
                         AND jsonb_array_length(pt) >= 2
                   )
               ),
               4326
           ) AS geom
    FROM survey_daily_assignments
    WHERE route_geom IS NULL
      AND polyline IS NOT NULL
      AND jsonb_typeof(polyline) = 'array'
      AND jsonb_array_length(polyline) >= 2
) sub
WHERE s.id = sub.id
  AND sub.geom IS NOT NULL
  AND ST_NPoints(sub.geom) >= 2;

INSERT INTO schema_migrations (id) VALUES ('20260712_schema_longevity')
ON CONFLICT (id) DO NOTHING;
