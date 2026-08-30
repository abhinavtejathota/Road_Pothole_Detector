-- Migration: survey + tracking state → Postgres/PostGIS
-- Dual-write: app still mirrors data/gis/survey_state.json + tracking_state.json
-- Safe to re-run (IF NOT EXISTS / ON CONFLICT).

CREATE EXTENSION IF NOT EXISTS postgis;

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
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (assignment_date, user_id)
);

CREATE INDEX IF NOT EXISTS idx_survey_assign_date ON survey_daily_assignments(assignment_date);
CREATE INDEX IF NOT EXISTS idx_survey_assign_user ON survey_daily_assignments(user_id);
CREATE INDEX IF NOT EXISTS idx_survey_assign_start ON survey_daily_assignments USING GIST(start_geom);
CREATE INDEX IF NOT EXISTS idx_survey_assign_end   ON survey_daily_assignments USING GIST(end_geom);

CREATE TABLE IF NOT EXISTS survey_segment_status (
    segment_id          TEXT PRIMARY KEY,
    status              TEXT NOT NULL DEFAULT 'available',
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

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

CREATE TABLE IF NOT EXISTS tracking_trail_points (
    id                  BIGSERIAL PRIMARY KEY,
    session_id          INTEGER NOT NULL REFERENCES tracking_sessions(id) ON DELETE CASCADE,
    lat                 DOUBLE PRECISION NOT NULL,
    lon                 DOUBLE PRECISION NOT NULL,
    ts                  TIMESTAMP WITH TIME ZONE,
    geom                GEOMETRY(POINT, 4326),
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tracking_trail_session ON tracking_trail_points(session_id);
CREATE INDEX IF NOT EXISTS idx_tracking_trail_geom ON tracking_trail_points USING GIST(geom);
