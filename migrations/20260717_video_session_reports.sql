-- Detection reports + session metadata (idempotent)
ALTER TABLE video_sessions
    ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS username TEXT,
    ADD COLUMN IF NOT EXISTS display_name TEXT,
    ADD COLUMN IF NOT EXISTS start_label TEXT,
    ADD COLUMN IF NOT EXISTS end_label TEXT,
    ADD COLUMN IF NOT EXISTS report_s3_key TEXT,
    ADD COLUMN IF NOT EXISTS report_generated_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS is_legacy BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_video_sessions_username ON video_sessions(username);
CREATE INDEX IF NOT EXISTS idx_video_sessions_user_id ON video_sessions(user_id);

INSERT INTO schema_migrations (id) VALUES ('20260717_video_session_reports')
ON CONFLICT (id) DO NOTHING;
