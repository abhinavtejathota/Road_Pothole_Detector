-- Rename roles: Admin → DevAdmin, TrackerAdmin → Admin.
-- One-shot (recorded in schema_migrations). Do not re-order relative to
-- 20260727_tracker_admin_role — follow with 20260728_roles_check_devadmin.sql.

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;

UPDATE users SET role = 'DevAdmin' WHERE role = 'Admin';
UPDATE users SET role = 'Admin' WHERE role = 'TrackerAdmin';

ALTER TABLE users ADD CONSTRAINT users_role_check
    CHECK (role IN ('DevAdmin', 'Admin', 'Allocator', 'Supervisor', 'Vendor', 'Videographer')) NOT VALID;
