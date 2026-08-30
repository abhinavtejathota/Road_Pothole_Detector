-- Custom auto-track tables (also in schema.sql for init_db)
-- Run via: python -c "import db_utils; db_utils.init_db()"

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

CREATE TABLE IF NOT EXISTS auto_track_video_coords (
    id                  SERIAL PRIMARY KEY,
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    upload_id           INTEGER REFERENCES auto_track_uploads(id) ON DELETE CASCADE,
    coords              JSONB NOT NULL DEFAULT '[]'::jsonb,
    coord_geom          GEOMETRY(LINESTRING, 4326),
    covered_km          DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
