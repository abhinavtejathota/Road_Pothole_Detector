-- SmartRoad AP — PostgreSQL + PostGIS schema
-- Idempotent: safe on every app startup via db_utils.init_db()
-- Also apply dated files under migrations/ for documented upgrades.
--
-- Run once: psql -d smartroad_ap -f schema.sql
--           or paste into pgAdmin Query Tool

CREATE EXTENSION IF NOT EXISTS postgis;

-- Track applied migrations (longevity / ops)
CREATE TABLE IF NOT EXISTS schema_migrations (
    id              TEXT PRIMARY KEY,
    applied_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ── Video processing sessions ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS video_sessions (
    id                  SERIAL PRIMARY KEY,
    filename            TEXT NOT NULL,
    s3_key              TEXT,
    run_id              TEXT,
    processed_at        TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    total_potholes      INTEGER DEFAULT 0,
    video_duration_sec  DOUBLE PRECISION,
    -- user_id FK added after users table exists (see ALTER below / init_db)
    user_id             INTEGER,
    username            TEXT,
    display_name        TEXT,
    start_label         TEXT,
    end_label           TEXT,
    report_s3_key       TEXT,
    report_generated_at TIMESTAMP WITH TIME ZONE,
    is_legacy           BOOLEAN NOT NULL DEFAULT FALSE
);

-- ── Individual pothole detections ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS potholes (
    id              SERIAL PRIMARY KEY,
    session_id      INTEGER NOT NULL REFERENCES video_sessions(id) ON DELETE CASCADE,
    class_name      TEXT,
    confidence      DOUBLE PRECISION,
    severity        TEXT,                       -- Low / Medium / High
    x1 INTEGER, y1 INTEGER, x2 INTEGER, y2 INTEGER,
    latitude        DOUBLE PRECISION,
    longitude       DOUBLE PRECISION,
    location        GEOMETRY(POINT, 4326),      -- PostGIS spatial column
    captured_at     TEXT,
    map_link        TEXT,
    frame_s3_url    TEXT,                       -- S3 URL of the marked frame
    -- GPS extras from the mobile JSON log
    altitude                    DOUBLE PRECISION,
    accuracy                    DOUBLE PRECISION,
    speed                       DOUBLE PRECISION,
    cumulative_distance_meters  DOUBLE PRECISION,
    street_name     TEXT,
    city            TEXT,
    state           TEXT,
    country         TEXT,
    zip_code        TEXT,
    full_address    TEXT,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_potholes_session_id ON potholes(session_id);
CREATE INDEX IF NOT EXISTS idx_potholes_location   ON potholes USING GIST(location);
CREATE INDEX IF NOT EXISTS idx_potholes_severity   ON potholes(severity);
CREATE INDEX IF NOT EXISTS idx_potholes_created    ON potholes(created_at);
CREATE INDEX IF NOT EXISTS idx_video_sessions_run  ON video_sessions(run_id);
CREATE INDEX IF NOT EXISTS idx_video_sessions_processed ON video_sessions(processed_at);
CREATE INDEX IF NOT EXISTS idx_video_sessions_username ON video_sessions(username);
CREATE INDEX IF NOT EXISTS idx_video_sessions_user_id ON video_sessions(user_id);

-- ═══════════════════════════════════════════════════════════════════════════════
-- PHASE 3 — Vendor Management, Task Allocation, Validation, Dashboard
-- ═══════════════════════════════════════════════════════════════════════════════

-- Vendors before users FK (users.vendor_id → vendors.id)
CREATE TABLE IF NOT EXISTS vendors (
    id                      SERIAL PRIMARY KEY,
    company_name            TEXT NOT NULL,
    registration_number     TEXT UNIQUE,               -- CIN / UCIN
    gst_number              TEXT,
    pan_number              TEXT,
    contact_person_name     TEXT,
    contact_phone           TEXT,
    contact_email           TEXT,
    address                 TEXT,
    city                    TEXT,
    district                TEXT,
    state                   TEXT,
    pin_code                TEXT,
    description             TEXT,                      -- experience summary / portfolio text
    specializations         TEXT[],                    -- e.g. ARRAY['pothole-patching','resurfacing']
    max_active_tasks        INTEGER DEFAULT 10,
    performance_score       DOUBLE PRECISION DEFAULT 100.0,  -- 0–100, auto-computed
    status                  TEXT DEFAULT 'Active',     -- Active / Suspended / Blacklisted
    status_reason           TEXT,
    created_at              TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at              TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ── Users / Auth ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    username        TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    full_name       TEXT,
    email           TEXT,
    role            TEXT NOT NULL DEFAULT 'Allocator', -- DevAdmin/Admin/Allocator/Supervisor/Vendor/Videographer
    vendor_id       INTEGER,                           -- set when role=Vendor
    state_id        INTEGER,                           -- Videographer: 1=Andhra Pradesh, 2=Telangana
    district_id     INTEGER,                           -- Videographer: primary LGD district (first of district_ids)
    district_ids    INTEGER[],                         -- Videographer: one or more LGD districts in that state
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_login      TIMESTAMP WITH TIME ZONE,
    token_version   INTEGER NOT NULL DEFAULT 0   -- bump on logout to revoke mobile JWTs
);

-- FK deferred until users exists (video_sessions is created earlier)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'video_sessions_user_id_fkey'
    ) THEN
        ALTER TABLE video_sessions
            ADD CONSTRAINT video_sessions_user_id_fkey
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL;
    END IF;
END $$;

-- ── Vendor geographic service zones (PostGIS polygon) ─────────────────────────
CREATE TABLE IF NOT EXISTS vendor_zones (
    id          SERIAL PRIMARY KEY,
    vendor_id   INTEGER NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
    zone_name   TEXT,
    boundary    GEOMETRY(POLYGON, 4326),
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ── Work orders (video session assigned to vendor) ────────────────────────────
CREATE TABLE IF NOT EXISTS work_orders (
    id                  SERIAL PRIMARY KEY,
    session_id          INTEGER NOT NULL REFERENCES video_sessions(id),
    vendor_id           INTEGER REFERENCES vendors(id) ON DELETE SET NULL,
    status              TEXT NOT NULL DEFAULT 'Created', -- Created/Allocated/WIP/Completed/Verified/Failed
    sla_tier            TEXT,                            -- Critical/Standard/Routine
    sla_due_date        TIMESTAMP WITH TIME ZONE,        -- computed on allocation
    allocated_at        TIMESTAMP WITH TIME ZONE,
    started_at          TIMESTAMP WITH TIME ZONE,
    completed_at        TIMESTAMP WITH TIME ZONE,
    verified_at         TIMESTAMP WITH TIME ZONE,
    estimated_budget    NUMERIC(14,2),
    actual_spend        NUMERIC(14,2),
    remarks             TEXT,
    created_by          TEXT,
    allocated_by        TEXT,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ── Per-pothole tracking within a work order ──────────────────────────────────
CREATE TABLE IF NOT EXISTS work_order_potholes (
    id              SERIAL PRIMARY KEY,
    work_order_id   INTEGER NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    pothole_id      INTEGER NOT NULL REFERENCES potholes(id),
    status          TEXT NOT NULL DEFAULT 'Pending',  -- Pending/InProgress/Repaired/Verified/Failed
    repair_sequence INTEGER,                           -- route-optimised order
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(work_order_id, pothole_id)
);

-- ── Full audit trail (every status change) ───────────────────────────────────
CREATE TABLE IF NOT EXISTS status_history (
    id              SERIAL PRIMARY KEY,
    entity_type     TEXT NOT NULL,       -- 'work_order' or 'pothole'
    entity_id       INTEGER NOT NULL,
    from_status     TEXT,
    to_status       TEXT NOT NULL,
    comment         TEXT,
    changed_by      TEXT,
    changed_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ── Completion validation (GPS + timestamp + YOLO re-detection) ───────────────
CREATE TABLE IF NOT EXISTS completion_validations (
    id                      SERIAL PRIMARY KEY,
    work_order_id           INTEGER NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    pothole_id              INTEGER REFERENCES potholes(id) ON DELETE SET NULL,  -- NULL = session-level check
    after_photo_s3_url      TEXT,
    after_photo_lat         DOUBLE PRECISION,
    after_photo_lon         DOUBLE PRECISION,
    after_photo_timestamp   TIMESTAMP WITH TIME ZONE,
    after_photo_hash        TEXT,                             -- SHA-256 for duplicate detection
    gps_distance_meters     DOUBLE PRECISION,                 -- PostGIS computed
    gps_check               TEXT,                             -- PASS / FAIL
    timestamp_check         TEXT,                             -- PASS / FAIL
    yolo_pothole_detected   BOOLEAN,
    yolo_confidence         DOUBLE PRECISION,
    yolo_severity           TEXT,
    ai_result               TEXT,                             -- PASS / PARTIAL / FAIL
    resolution_score        DOUBLE PRECISION,                 -- 0–100
    final_result            TEXT,                             -- PASS / FAIL (post human review)
    reviewed_by             TEXT,
    review_comment          TEXT,
    validated_at            TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ── Warranty & maintenance schedule ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS warranty_records (
    id                          SERIAL PRIMARY KEY,
    work_order_id               INTEGER NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE UNIQUE,
    warranty_period_months      INTEGER DEFAULT 12,
    warranty_start_date         DATE,
    warranty_expiry_date        DATE,
    next_maintenance_date       DATE,
    maintenance_interval_months INTEGER DEFAULT 6,
    warranty_status             TEXT DEFAULT 'Valid',  -- Valid / Expiring / Expired / Claimed
    claim_count                 INTEGER DEFAULT 0,
    notes                       TEXT,
    created_at                  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ── Escalation log ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS escalations (
    id                  SERIAL PRIMARY KEY,
    work_order_id       INTEGER NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    escalation_type     TEXT,   -- SLA_BREACH / HIGH_SEVERITY / MONSOON
    escalated_at        TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    notified_to         TEXT,
    notification_method TEXT,   -- email / sms
    resolved            BOOLEAN DEFAULT FALSE,
    resolved_at         TIMESTAMP WITH TIME ZONE
);

-- ── Phase 3 indexes ───────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_work_orders_session   ON work_orders(session_id);
CREATE INDEX IF NOT EXISTS idx_work_orders_vendor    ON work_orders(vendor_id);
CREATE INDEX IF NOT EXISTS idx_work_orders_status    ON work_orders(status);
CREATE INDEX IF NOT EXISTS idx_work_orders_sla       ON work_orders(sla_due_date);
CREATE INDEX IF NOT EXISTS idx_vendor_zones_boundary ON vendor_zones USING GIST(boundary);
CREATE INDEX IF NOT EXISTS idx_status_history_entity ON status_history(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_warranty_expiry       ON warranty_records(warranty_expiry_date);
CREATE INDEX IF NOT EXISTS idx_validations_wo        ON completion_validations(work_order_id);
CREATE INDEX IF NOT EXISTS idx_users_role            ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_vendor          ON users(vendor_id);
CREATE INDEX IF NOT EXISTS idx_users_state           ON users(state_id);
CREATE INDEX IF NOT EXISTS idx_users_district        ON users(district_id);
CREATE INDEX IF NOT EXISTS idx_vendors_status        ON vendors(status);

-- ═══════════════════════════════════════════════════════════════════════════════
-- SURVEY + FIELD TRACKING (dual-written with data/gis/*.json)
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS survey_settings (
    id                  INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    daily_km            DOUBLE PRECISION NOT NULL DEFAULT 100,
    focus_road_class    TEXT NOT NULL DEFAULT 'all',
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

INSERT INTO survey_settings (id, daily_km, focus_road_class)
VALUES (1, 100, 'all')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS survey_daily_assignments (
    id                  SERIAL PRIMARY KEY,
    assignment_date     DATE NOT NULL,
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    state_key           TEXT,
    state_id            INTEGER,
    district_id         INTEGER,
    district_name       TEXT,
    segment_ids         TEXT[] NOT NULL DEFAULT '{}',
    start_lat           DOUBLE PRECISION,
    start_lon           DOUBLE PRECISION,
    start_label         TEXT,
    end_lat             DOUBLE PRECISION,
    end_lon             DOUBLE PRECISION,
    end_label           TEXT,
    start_geom          GEOMETRY(POINT, 4326),
    end_geom            GEOMETRY(POINT, 4326),
    corridor_km         DOUBLE PRECISION,
    covered_km          DOUBLE PRECISION NOT NULL DEFAULT 0,
    covered_by_class    JSONB NOT NULL DEFAULT '{}'::jsonb,
    legs                JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- Driven corridor (OSRM) — source of truth for map + assigned km
    polyline            JSONB NOT NULL DEFAULT '[]'::jsonb,
    route_km            DOUBLE PRECISION,
    route_geom          GEOMETRY(LINESTRING, 4326),
    continuous          BOOLEAN NOT NULL DEFAULT TRUE,
    preferred_km        DOUBLE PRECISION NOT NULL DEFAULT 0,
    connector_km        DOUBLE PRECISION NOT NULL DEFAULT 0,
    mode                TEXT NOT NULL DEFAULT 'corridor',  -- corridor / nearest / manual
    meta                JSONB NOT NULL DEFAULT '{}'::jsonb, -- extensible (gap_bridges, warnings, …)
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (assignment_date, user_id)
);

CREATE INDEX IF NOT EXISTS idx_survey_assign_date ON survey_daily_assignments(assignment_date);
CREATE INDEX IF NOT EXISTS idx_survey_assign_user ON survey_daily_assignments(user_id);
CREATE INDEX IF NOT EXISTS idx_survey_assign_start ON survey_daily_assignments USING GIST(start_geom);
CREATE INDEX IF NOT EXISTS idx_survey_assign_end   ON survey_daily_assignments USING GIST(end_geom);
-- route_geom / district / state indexes created after ALTER ADD COLUMN below

CREATE TABLE IF NOT EXISTS survey_segment_status (
    segment_id          TEXT PRIMARY KEY,
    status              TEXT NOT NULL DEFAULT 'available',
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- status index after possible upgrades

CREATE TABLE IF NOT EXISTS tracking_sessions (
    id                  SERIAL PRIMARY KEY,
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    track_date          DATE NOT NULL,
    username            TEXT,
    full_name           TEXT,
    state_id            INTEGER,
    district_id         INTEGER,
    recording           BOOLEAN NOT NULL DEFAULT FALSE,
    covered_km          DOUBLE PRECISION NOT NULL DEFAULT 0,
    covered_by_class    JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_lat            DOUBLE PRECISION,
    last_lon            DOUBLE PRECISION,
    last_accuracy       DOUBLE PRECISION,
    last_ts             TIMESTAMP WITH TIME ZONE,
    last_geom           GEOMETRY(POINT, 4326),
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (user_id, track_date)
);

CREATE INDEX IF NOT EXISTS idx_tracking_date ON tracking_sessions(track_date);
CREATE INDEX IF NOT EXISTS idx_tracking_last ON tracking_sessions USING GIST(last_geom);
-- user index after additive section (safe re-create)

CREATE TABLE IF NOT EXISTS tracking_trail_points (
    id                  BIGSERIAL PRIMARY KEY,
    session_id          INTEGER NOT NULL REFERENCES tracking_sessions(id) ON DELETE CASCADE,
    lat                 DOUBLE PRECISION NOT NULL,
    lon                 DOUBLE PRECISION NOT NULL,
    ts                  TIMESTAMP WITH TIME ZONE,
    geom                GEOMETRY(POINT, 4326),
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    capture_session_id  TEXT,
    committed           BOOLEAN NOT NULL DEFAULT TRUE,
    delta_km            DOUBLE PRECISION NOT NULL DEFAULT 0,
    road_class          TEXT
);

CREATE INDEX IF NOT EXISTS idx_tracking_trail_session ON tracking_trail_points(session_id);
CREATE INDEX IF NOT EXISTS idx_tracking_trail_geom ON tracking_trail_points USING GIST(geom);
-- (session_id, ts) composite index in additive section

-- ═══════════════════════════════════════════════════════════════════════════════
-- Additive upgrades (existing DBs: CREATE TABLE IF NOT EXISTS won't alter)
-- ═══════════════════════════════════════════════════════════════════════════════

ALTER TABLE users ADD COLUMN IF NOT EXISTS district_ids INTEGER[];
ALTER TABLE users ADD COLUMN IF NOT EXISTS state_id INTEGER;
ALTER TABLE users ADD COLUMN IF NOT EXISTS district_id INTEGER;

ALTER TABLE survey_daily_assignments ADD COLUMN IF NOT EXISTS legs JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE survey_daily_assignments ADD COLUMN IF NOT EXISTS polyline JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE survey_daily_assignments ADD COLUMN IF NOT EXISTS route_km DOUBLE PRECISION;
ALTER TABLE survey_daily_assignments ADD COLUMN IF NOT EXISTS route_geom GEOMETRY(LINESTRING, 4326);
ALTER TABLE tracking_trail_points ADD COLUMN IF NOT EXISTS capture_session_id TEXT;
ALTER TABLE tracking_trail_points ADD COLUMN IF NOT EXISTS committed BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE tracking_trail_points ADD COLUMN IF NOT EXISTS delta_km DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE tracking_trail_points ADD COLUMN IF NOT EXISTS road_class TEXT;
ALTER TABLE survey_daily_assignments ADD COLUMN IF NOT EXISTS continuous BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE survey_daily_assignments ADD COLUMN IF NOT EXISTS preferred_km DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE survey_daily_assignments ADD COLUMN IF NOT EXISTS connector_km DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE survey_daily_assignments ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'corridor';
ALTER TABLE survey_daily_assignments ADD COLUMN IF NOT EXISTS meta JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Soft FK: vendor users → vendors (nullable; ON DELETE clears link)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'users_vendor_id_fkey'
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT users_vendor_id_fkey
            FOREIGN KEY (vendor_id) REFERENCES vendors(id) ON DELETE SET NULL;
    END IF;
EXCEPTION WHEN others THEN
    -- Skip if orphan vendor_id rows exist; fix data then re-run
    RAISE NOTICE 'users_vendor_id_fkey not applied: %', SQLERRM;
END $$;

-- Domain CHECKs (NOT VALID so existing odd rows don't block upgrade; validate later)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_role_check') THEN
        ALTER TABLE users ADD CONSTRAINT users_role_check
            CHECK (role IN ('DevAdmin', 'Admin', 'Allocator', 'Supervisor', 'Vendor', 'Videographer')) NOT VALID;
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

-- Custom auto-track: free-drive GPS vs video GPS verification
CREATE TABLE IF NOT EXISTS auto_track_gps (
    id                  SERIAL PRIMARY KEY,
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    track_date          DATE NOT NULL,
    start_lat           DOUBLE PRECISION,
    start_lon           DOUBLE PRECISION,
    end_lat             DOUBLE PRECISION,
    end_lon             DOUBLE PRECISION,
    start_label         TEXT,
    end_label           TEXT,
    gps_points          JSONB NOT NULL DEFAULT '[]'::jsonb,
    gps_geom            GEOMETRY(LINESTRING, 4326),
    covered_km          DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_auto_track_gps_user ON auto_track_gps(user_id);
CREATE INDEX IF NOT EXISTS idx_auto_track_gps_date ON auto_track_gps(track_date);
CREATE INDEX IF NOT EXISTS idx_auto_track_gps_geom ON auto_track_gps USING GIST(gps_geom);

CREATE TABLE IF NOT EXISTS auto_track_uploads (
    id                  SERIAL PRIMARY KEY,
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    video_title         TEXT NOT NULL,
    upload_day          DATE NOT NULL,
    s3_key              TEXT,
    gps_track_id        INTEGER REFERENCES auto_track_gps(id) ON DELETE SET NULL,
    match_score         DOUBLE PRECISION,
    match_status        TEXT NOT NULL DEFAULT 'pending',
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_auto_track_uploads_user ON auto_track_uploads(user_id);
CREATE INDEX IF NOT EXISTS idx_auto_track_uploads_day ON auto_track_uploads(upload_day);

CREATE TABLE IF NOT EXISTS auto_track_video_coords (
    id                  SERIAL PRIMARY KEY,
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    upload_id           INTEGER REFERENCES auto_track_uploads(id) ON DELETE CASCADE,
    coords              JSONB NOT NULL DEFAULT '[]'::jsonb,
    coord_geom          GEOMETRY(LINESTRING, 4326),
    covered_km          DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_auto_track_video_user ON auto_track_video_coords(user_id);
CREATE INDEX IF NOT EXISTS idx_auto_track_video_upload ON auto_track_video_coords(upload_id);
CREATE INDEX IF NOT EXISTS idx_auto_track_video_geom ON auto_track_video_coords USING GIST(coord_geom);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'auto_track_uploads_match_check') THEN
        ALTER TABLE auto_track_uploads ADD CONSTRAINT auto_track_uploads_match_check
            CHECK (match_status IN ('pending', 'matched', 'mismatch', 'no_gps_track')) NOT VALID;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'survey_assign_mode_check') THEN
        ALTER TABLE survey_daily_assignments DROP CONSTRAINT survey_assign_mode_check;
    END IF;
    ALTER TABLE survey_daily_assignments ADD CONSTRAINT survey_assign_mode_check
        CHECK (mode IN ('corridor', 'nearest', 'manual', 'auto_track')) NOT VALID;
EXCEPTION WHEN others THEN
    RAISE NOTICE 'auto_track / mode check: %', SQLERRM;
END $$;

INSERT INTO schema_migrations (id) VALUES ('20260712_schema_longevity')
ON CONFLICT (id) DO NOTHING;
INSERT INTO schema_migrations (id) VALUES ('20260712_capture_session_trail')
ON CONFLICT (id) DO NOTHING;
INSERT INTO schema_migrations (id) VALUES ('20260713_auto_track_tables')
ON CONFLICT (id) DO NOTHING;

-- Abandoned assignments kept for later upload seal (clear survey → new route → upload old video)
CREATE TABLE IF NOT EXISTS survey_cleared_assignments (
    id                  SERIAL PRIMARY KEY,
    assignment_date     DATE NOT NULL,
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    cleared_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    start_lat           DOUBLE PRECISION,
    start_lon           DOUBLE PRECISION,
    end_lat             DOUBLE PRECISION,
    end_lon             DOUBLE PRECISION,
    start_label         TEXT,
    end_label           TEXT,
    segment_ids         TEXT[] NOT NULL DEFAULT '{}',
    polyline            JSONB NOT NULL DEFAULT '[]'::jsonb,
    route_km            DOUBLE PRECISION,
    corridor_km         DOUBLE PRECISION,
    mode                TEXT,
    district_id         INTEGER,
    district_name       TEXT,
    state_key           TEXT,
    state_id            INTEGER,
    entry_snapshot      JSONB NOT NULL DEFAULT '{}'::jsonb,
    consumed_at         TIMESTAMPTZ,
    consumed_note       TEXT
);

CREATE INDEX IF NOT EXISTS idx_survey_cleared_user_date
    ON survey_cleared_assignments (user_id, assignment_date);

CREATE INDEX IF NOT EXISTS idx_survey_cleared_open
    ON survey_cleared_assignments (user_id, cleared_at DESC)
    WHERE consumed_at IS NULL;

-- ── Videographer profile details (Admin / DevAdmin) ───────────────────────────
CREATE TABLE IF NOT EXISTS vg_details (
    user_id         INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    display_name    TEXT,
    mobile          TEXT,
    village         TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vg_details_mobile ON vg_details (mobile);

INSERT INTO schema_migrations (id) VALUES ('20260725_survey_cleared_assignments')
ON CONFLICT (id) DO NOTHING;

INSERT INTO schema_migrations (id) VALUES ('20260727_vg_details')
ON CONFLICT (id) DO NOTHING;
