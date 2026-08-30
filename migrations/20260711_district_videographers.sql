-- Migration: constituency_id → state_id + district_id (videographer assignment)
-- Run on your Postgres server after deploying this change.
--
-- state_id: 1 = Andhra Pradesh, 2 = Telangana
-- district_id: LGD district code (see district.txt / data/gis_states/*/index/districts.json)

BEGIN;

ALTER TABLE users ADD COLUMN IF NOT EXISTS state_id INTEGER;
ALTER TABLE users ADD COLUMN IF NOT EXISTS district_id INTEGER;

-- After re-linking all videographers, drop the legacy column:
ALTER TABLE users DROP COLUMN IF EXISTS constituency_id;

COMMIT;

-- Example: link a videographer to Hyderabad (Telangana LGD 507)
-- UPDATE users SET state_id = 2, district_id = 507 WHERE username = 'your_vg_username';
