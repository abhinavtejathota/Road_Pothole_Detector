-- Add TrackerAdmin to users.role CHECK constraint.
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check
    CHECK (role IN ('Admin', 'TrackerAdmin', 'Allocator', 'Supervisor', 'Vendor', 'Videographer')) NOT VALID;
