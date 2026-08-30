-- Provisional vs committed capture trail metadata
ALTER TABLE tracking_trail_points ADD COLUMN IF NOT EXISTS capture_session_id TEXT;
ALTER TABLE tracking_trail_points ADD COLUMN IF NOT EXISTS committed BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE tracking_trail_points ADD COLUMN IF NOT EXISTS delta_km DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE tracking_trail_points ADD COLUMN IF NOT EXISTS road_class TEXT;

INSERT INTO schema_migrations (id) VALUES ('20260712_capture_session_trail')
ON CONFLICT (id) DO NOTHING;
