-- Persist multi-district corridor legs on daily assignments.
ALTER TABLE survey_daily_assignments
  ADD COLUMN IF NOT EXISTS legs JSONB NOT NULL DEFAULT '[]'::jsonb;
