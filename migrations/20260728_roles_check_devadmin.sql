-- Ensure role CHECK matches DevAdmin/Admin rename (re-apply after older TrackerAdmin mig).
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check
    CHECK (role IN ('DevAdmin', 'Admin', 'Allocator', 'Supervisor', 'Vendor', 'Videographer')) NOT VALID;
