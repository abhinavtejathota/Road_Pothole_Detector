-- Restore reusable mobile OTP for citizen reporters; remove passkey / HTTP-device auth.

DROP TABLE IF EXISTS reporter_passkeys CASCADE;
DROP TABLE IF EXISTS reporter_webauthn_challenges CASCADE;
DROP TABLE IF EXISTS reporter_device_link_codes CASCADE;
DROP TABLE IF EXISTS reporter_http_devices CASCADE;

-- Plaintext OTP for testing (JSON mirror on disk). Clear before production SMS.
CREATE TABLE IF NOT EXISTS reporter_otp_codes (
  mobile            TEXT PRIMARY KEY,
  otp               TEXT NOT NULL,
  created_at        TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  last_verified_at  TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_reporter_otp_codes_created ON reporter_otp_codes(created_at DESC);

-- Keep legacy challenges table available if portal migration created it earlier
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

INSERT INTO schema_migrations (id) VALUES ('20260720_reporter_otp_restore')
ON CONFLICT (id) DO NOTHING;
