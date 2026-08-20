-- 002_real_workflows already created repair_assignments on deployed systems.
-- Keep 004 safe for both new and existing databases, then add these worker
-- fields in a dedicated, ordered compatibility migration.
ALTER TABLE repair_assignments ADD COLUMN worker_id INTEGER REFERENCES repair_workers(id);
ALTER TABLE repair_assignments ADD COLUMN accepted_at TEXT;

CREATE INDEX IF NOT EXISTS repair_assignments_worker_idx ON repair_assignments(worker_id, created_at DESC);
