-- Migration: allow videographers to cover multiple districts in their state.
-- district_id remains the primary/default (first of district_ids) for backward compatibility.

BEGIN;

ALTER TABLE users ADD COLUMN IF NOT EXISTS district_ids INTEGER[];

-- Backfill from existing single district_id
UPDATE users
SET district_ids = ARRAY[district_id]
WHERE district_id IS NOT NULL
  AND (district_ids IS NULL OR cardinality(district_ids) = 0);

COMMIT;

-- Example: assign two Anantapur-area districts (replace IDs with real LGD codes)
-- UPDATE users
-- SET district_id = 502,
--     district_ids = ARRAY[502, 503]
-- WHERE username = 'your_vg_username';
