-- Work-material submissions are intentionally separate from published articles.
-- A submission keeps the original operational facts and media together with the
-- generation run that produced the article sent into the existing review flow.
CREATE TABLE IF NOT EXISTS work_material_submissions (
    id INTEGER PRIMARY KEY,
    community_id INTEGER NOT NULL REFERENCES communities(id),
    uploader_id INTEGER NOT NULL REFERENCES users(id),
    weekly_summary TEXT NOT NULL,
    incomplete_repair_reasons TEXT NOT NULL,
    next_week_plan TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('uploaded', 'generating', 'submitted_for_review', 'generation_failed')),
    article_id INTEGER REFERENCES articles(id),
    analysis_json TEXT NOT NULL DEFAULT '{}',
    generated_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_material_attachments (
    id INTEGER PRIMARY KEY,
    material_id INTEGER NOT NULL REFERENCES work_material_submissions(id) ON DELETE CASCADE,
    uploader_id INTEGER NOT NULL REFERENCES users(id),
    storage_key TEXT NOT NULL UNIQUE,
    file_name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_material_status_logs (
    id INTEGER PRIMARY KEY,
    material_id INTEGER NOT NULL REFERENCES work_material_submissions(id) ON DELETE CASCADE,
    actor_id INTEGER NOT NULL REFERENCES users(id),
    from_status TEXT,
    to_status TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_material_generation_runs (
    id INTEGER PRIMARY KEY,
    material_id INTEGER NOT NULL REFERENCES work_material_submissions(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'failed')),
    result_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT
);

-- Worker identities are deliberately separate from property-console accounts.
-- This avoids granting a phone-side repair worker access to property functions.
CREATE TABLE IF NOT EXISTS repair_workers (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    demo_key TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    community_id INTEGER NOT NULL REFERENCES communities(id),
    specialty TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repair_assignments (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES repair_tickets(id) ON DELETE CASCADE,
    assignee_name TEXT NOT NULL,
    vendor_name TEXT,
    assigned_by INTEGER NOT NULL REFERENCES users(id),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repair_worker_events (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES repair_tickets(id) ON DELETE CASCADE,
    worker_id INTEGER NOT NULL REFERENCES repair_workers(id),
    action TEXT NOT NULL CHECK(action IN ('accepted', 'expected_visit', 'arrived', 'problem_media', 'completed')),
    note TEXT NOT NULL,
    latitude TEXT,
    longitude TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repair_attachment_stages (
    attachment_id INTEGER PRIMARY KEY REFERENCES repair_attachments(id) ON DELETE CASCADE,
    stage TEXT NOT NULL CHECK(stage IN ('resident_report', 'problem', 'completion')),
    recorded_at TEXT NOT NULL
);

ALTER TABLE repair_tickets ADD COLUMN worker_id INTEGER REFERENCES repair_workers(id);

CREATE INDEX IF NOT EXISTS work_material_submissions_community_idx ON work_material_submissions(community_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS work_material_attachments_material_idx ON work_material_attachments(material_id, created_at DESC);
CREATE INDEX IF NOT EXISTS work_material_status_logs_material_idx ON work_material_status_logs(material_id, created_at DESC);
CREATE INDEX IF NOT EXISTS work_material_generation_runs_material_idx ON work_material_generation_runs(material_id, started_at DESC);
CREATE INDEX IF NOT EXISTS repair_workers_community_idx ON repair_workers(community_id, active, display_name);
CREATE INDEX IF NOT EXISTS repair_assignments_ticket_idx ON repair_assignments(ticket_id, created_at DESC);
CREATE INDEX IF NOT EXISTS repair_worker_events_ticket_idx ON repair_worker_events(ticket_id, created_at DESC);
