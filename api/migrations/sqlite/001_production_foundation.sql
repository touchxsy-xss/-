ALTER TABLE repair_tickets ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal';
ALTER TABLE repair_tickets ADD COLUMN source TEXT NOT NULL DEFAULT 'resident_app';
ALTER TABLE repair_tickets ADD COLUMN expected_at TEXT;
ALTER TABLE repair_tickets ADD COLUMN closed_at TEXT;
ALTER TABLE repair_tickets ADD COLUMN satisfaction_score INTEGER;
ALTER TABLE repair_tickets ADD COLUMN version INTEGER NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS repair_attachments (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES repair_tickets(id) ON DELETE CASCADE,
    uploader_id INTEGER NOT NULL REFERENCES users(id),
    storage_key TEXT NOT NULL UNIQUE,
    file_name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
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

CREATE TABLE IF NOT EXISTS feedback_tickets (
    id INTEGER PRIMARY KEY,
    community_id INTEGER NOT NULL REFERENCES communities(id),
    resident_id INTEGER REFERENCES users(id),
    type TEXT NOT NULL CHECK(type IN ('complaint', 'suggestion', 'praise')),
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback_messages (
    id INTEGER PRIMARY KEY,
    feedback_id INTEGER NOT NULL REFERENCES feedback_tickets(id) ON DELETE CASCADE,
    author_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    read_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payment_orders (
    id INTEGER PRIMARY KEY,
    public_id TEXT NOT NULL UNIQUE,
    community_id INTEGER NOT NULL REFERENCES communities(id),
    payer_id INTEGER NOT NULL REFERENCES users(id),
    order_type TEXT NOT NULL CHECK(order_type IN ('property_fee', 'parking_fee')),
    amount_cents INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'created',
    provider TEXT,
    provider_transaction_id TEXT,
    paid_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS article_targets (
    id INTEGER PRIMARY KEY,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    community_id INTEGER NOT NULL REFERENCES communities(id),
    created_at TEXT NOT NULL,
    UNIQUE(article_id, community_id)
);

CREATE INDEX IF NOT EXISTS repair_attachments_ticket_idx ON repair_attachments(ticket_id, created_at DESC);
CREATE INDEX IF NOT EXISTS feedback_community_status_idx ON feedback_tickets(community_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS notifications_user_unread_idx ON notifications(user_id, read_at, created_at DESC);
CREATE INDEX IF NOT EXISTS payment_orders_payer_idx ON payment_orders(payer_id, status, created_at DESC);
