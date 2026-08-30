-- Soft-clear archive: keep abandoned assignments so later upload can seal
-- against the route that was active at record time (not the new active route).
-- Active survey_daily_assignments stays UNIQUE(assignment_date, user_id).

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
