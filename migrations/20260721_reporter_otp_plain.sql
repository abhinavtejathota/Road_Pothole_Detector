-- Dev/testing: plaintext OTP alongside hash on reporter_otp_challenges.
-- Drop plain_otp before production SMS (hash-only verification remains).

ALTER TABLE reporter_otp_challenges
  ADD COLUMN IF NOT EXISTS plain_otp TEXT;

INSERT INTO schema_migrations (id) VALUES ('20260721_reporter_otp_plain')
ON CONFLICT (id) DO NOTHING;
