ALTER TABLE repair_tickets ADD COLUMN IF NOT EXISTS priority VARCHAR(20) NOT NULL DEFAULT 'normal';
ALTER TABLE repair_tickets ADD COLUMN IF NOT EXISTS source VARCHAR(40) NOT NULL DEFAULT 'resident_app';
ALTER TABLE repair_tickets ADD COLUMN IF NOT EXISTS expected_at TIMESTAMPTZ;
ALTER TABLE repair_tickets ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ;
ALTER TABLE repair_tickets ADD COLUMN IF NOT EXISTS satisfaction_score INTEGER;
ALTER TABLE repair_tickets ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS repair_attachments (
    id BIGSERIAL PRIMARY KEY,
    ticket_id BIGINT NOT NULL REFERENCES repair_tickets(id) ON DELETE CASCADE,
    uploader_id BIGINT NOT NULL REFERENCES users(id),
    storage_key VARCHAR(500) NOT NULL UNIQUE,
    file_name VARCHAR(255) NOT NULL,
    mime_type VARCHAR(100) NOT NULL,
    byte_size BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS repair_assignments (
    id BIGSERIAL PRIMARY KEY,
    ticket_id BIGINT NOT NULL REFERENCES repair_tickets(id) ON DELETE CASCADE,
    assignee_name VARCHAR(100) NOT NULL,
    vendor_name VARCHAR(160),
    assigned_by BIGINT NOT NULL REFERENCES users(id),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS feedback_tickets (
    id BIGSERIAL PRIMARY KEY,
    community_id BIGINT NOT NULL REFERENCES communities(id),
    resident_id BIGINT REFERENCES users(id),
    type VARCHAR(20) NOT NULL CHECK(type IN ('complaint', 'suggestion', 'praise')),
    subject VARCHAR(240) NOT NULL,
    body TEXT NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS feedback_messages (
    id BIGSERIAL PRIMARY KEY,
    feedback_id BIGINT NOT NULL REFERENCES feedback_tickets(id) ON DELETE CASCADE,
    author_id BIGINT NOT NULL REFERENCES users(id),
    body TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS notifications (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type VARCHAR(60) NOT NULL,
    title VARCHAR(240) NOT NULL,
    body TEXT NOT NULL,
    resource_type VARCHAR(40),
    resource_id VARCHAR(80),
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS payment_orders (
    id BIGSERIAL PRIMARY KEY,
    public_id VARCHAR(60) NOT NULL UNIQUE,
    community_id BIGINT NOT NULL REFERENCES communities(id),
    payer_id BIGINT NOT NULL REFERENCES users(id),
    order_type VARCHAR(30) NOT NULL CHECK(order_type IN ('property_fee', 'parking_fee')),
    amount_cents INTEGER NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'created',
    provider VARCHAR(40),
    provider_transaction_id VARCHAR(160),
    paid_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS article_targets (
    id BIGSERIAL PRIMARY KEY,
    article_id BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    community_id BIGINT NOT NULL REFERENCES communities(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(article_id, community_id)
);

CREATE INDEX IF NOT EXISTS repair_attachments_ticket_idx ON repair_attachments(ticket_id, created_at DESC);
CREATE INDEX IF NOT EXISTS feedback_community_status_idx ON feedback_tickets(community_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS notifications_user_unread_idx ON notifications(user_id, read_at, created_at DESC);
CREATE INDEX IF NOT EXISTS payment_orders_payer_idx ON payment_orders(payer_id, status, created_at DESC);
