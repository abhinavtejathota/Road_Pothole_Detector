-- Citizen / reporter portal (separate from staff users table)
-- Mobile OTP login + complaint uploads to S3 users/{reporter_id}/

CREATE TABLE IF NOT EXISTS reporter_users (
  id              SERIAL PRIMARY KEY,
  mobile          TEXT NOT NULL UNIQUE,
  is_active       BOOLEAN NOT NULL DEFAULT TRUE,
  created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  last_login_at   TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_reporter_users_mobile ON reporter_users(mobile);

CREATE TABLE IF NOT EXISTS reporter_otp_challenges (
  id              SERIAL PRIMARY KEY,
  mobile          TEXT NOT NULL,
  otp_hash        TEXT NOT NULL,
  expires_at      TIMESTAMP WITH TIME ZONE NOT NULL,
  consumed_at     TIMESTAMP WITH TIME ZONE,
  attempt_count   INTEGER NOT NULL DEFAULT 0,
  ip_address      TEXT,
  created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reporter_otp_mobile ON reporter_otp_challenges(mobile, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reporter_otp_expires ON reporter_otp_challenges(expires_at);

CREATE TABLE IF NOT EXISTS reporter_complaints (
  id                  SERIAL PRIMARY KEY,
  tracking_number     TEXT NOT NULL UNIQUE,
  reporter_id         INTEGER NOT NULL REFERENCES reporter_users(id) ON DELETE CASCADE,
  defect_type         TEXT NOT NULL,
  description         TEXT,
  latitude            DOUBLE PRECISION NOT NULL,
  longitude           DOUBLE PRECISION NOT NULL,
  location            GEOMETRY(POINT, 4326),
  gps_accuracy_m      DOUBLE PRECISION,
  photo_s3_key        TEXT,
  video_s3_key        TEXT,
  media_content_type  TEXT,
  road_id             TEXT,
  snap_distance_m     DOUBLE PRECISION,
  status              TEXT NOT NULL DEFAULT 'Submitted',
  created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  CONSTRAINT reporter_complaint_status_check CHECK (status IN (
    'Submitted', 'Verified', 'Rejected',
    'WorkOrder_Created', 'In_Progress', 'Resolved', 'Closed'
  ))
);

CREATE INDEX IF NOT EXISTS idx_reporter_complaints_reporter ON reporter_complaints(reporter_id);
CREATE INDEX IF NOT EXISTS idx_reporter_complaints_status ON reporter_complaints(status);
CREATE INDEX IF NOT EXISTS idx_reporter_complaints_created ON reporter_complaints(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reporter_complaints_location ON reporter_complaints USING GIST(location);

INSERT INTO schema_migrations (id) VALUES ('20260720_reporter_portal')
ON CONFLICT (id) DO NOTHING;
