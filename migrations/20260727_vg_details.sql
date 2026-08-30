-- Videographer profile details managed by TrackerAdmin.
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

INSERT INTO schema_migrations (id) VALUES ('20260727_vg_details')
ON CONFLICT (id) DO NOTHING;
