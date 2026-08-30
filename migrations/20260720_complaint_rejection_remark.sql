-- Citizen complaint rejection remark (admin review)

ALTER TABLE reporter_complaints
  ADD COLUMN IF NOT EXISTS rejection_remark TEXT;

INSERT INTO schema_migrations (id) VALUES ('20260720_complaint_rejection_remark')
ON CONFLICT (id) DO NOTHING;
