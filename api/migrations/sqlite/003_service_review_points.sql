ALTER TABLE repair_tickets ADD COLUMN check_in_at TEXT;
ALTER TABLE repair_tickets ADD COLUMN check_in_note TEXT;
ALTER TABLE repair_tickets ADD COLUMN completion_note TEXT;
ALTER TABLE repair_tickets ADD COLUMN completed_at TEXT;

CREATE TABLE IF NOT EXISTS repair_work_logs (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES repair_tickets(id) ON DELETE CASCADE,
    actor_id INTEGER NOT NULL REFERENCES users(id),
    worker_name TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('assigned', 'check_in', 'completed')),
    note TEXT NOT NULL,
    latitude TEXT,
    longitude TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repair_reviews (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL UNIQUE REFERENCES repair_tickets(id) ON DELETE CASCADE,
    resident_id INTEGER NOT NULL REFERENCES users(id),
    score INTEGER NOT NULL CHECK(score BETWEEN 1 AND 5),
    body TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_attachments (
    id INTEGER PRIMARY KEY,
    review_id INTEGER NOT NULL REFERENCES repair_reviews(id) ON DELETE CASCADE,
    uploader_id INTEGER NOT NULL REFERENCES users(id),
    storage_key TEXT NOT NULL UNIQUE,
    file_name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resident_points_accounts (
    resident_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    balance INTEGER NOT NULL DEFAULT 0 CHECK(balance >= 0),
    lifetime_earned INTEGER NOT NULL DEFAULT 0 CHECK(lifetime_earned >= 0),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resident_point_ledger (
    id INTEGER PRIMARY KEY,
    resident_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount INTEGER NOT NULL,
    balance_after INTEGER NOT NULL CHECK(balance_after >= 0),
    action TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    note TEXT NOT NULL,
    event_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS point_rewards (
    id INTEGER PRIMARY KEY,
    community_id INTEGER NOT NULL REFERENCES communities(id),
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    points_cost INTEGER NOT NULL CHECK(points_cost > 0),
    stock INTEGER NOT NULL DEFAULT 0 CHECK(stock >= 0),
    status TEXT NOT NULL DEFAULT 'available',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS point_redemptions (
    id INTEGER PRIMARY KEY,
    resident_id INTEGER NOT NULL REFERENCES users(id),
    reward_id INTEGER NOT NULL REFERENCES point_rewards(id),
    points_cost INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS repair_work_logs_ticket_idx ON repair_work_logs(ticket_id, created_at DESC);
CREATE INDEX IF NOT EXISTS review_attachments_review_idx ON review_attachments(review_id, created_at DESC);
CREATE INDEX IF NOT EXISTS point_ledger_resident_idx ON resident_point_ledger(resident_id, created_at DESC);
CREATE INDEX IF NOT EXISTS point_redemptions_resident_idx ON point_redemptions(resident_id, created_at DESC);

INSERT OR IGNORE INTO resident_points_accounts(resident_id, balance, lifetime_earned, updated_at)
SELECT id, 320, 320, datetime('now') FROM users WHERE role = 'resident';

INSERT OR IGNORE INTO resident_point_ledger(resident_id, amount, balance_after, action, resource_type, resource_id, note, event_key, created_at)
SELECT id, 320, 320, 'welcome', 'account', CAST(id AS TEXT), '社区积分体验账户初始积分', 'welcome-' || CAST(id AS TEXT), datetime('now')
FROM users WHERE role = 'resident';

INSERT OR IGNORE INTO point_rewards(community_id, name, description, points_cost, stock, status, created_at)
SELECT id, '环保生活礼包', '可兑换纸巾、垃圾袋等社区实用礼品。', 120, 20, 'available', datetime('now')
FROM communities WHERE slug = 'pengyi';

INSERT OR IGNORE INTO point_rewards(community_id, name, description, points_cost, stock, status, created_at)
SELECT id, '物业服务优先响应券', '下一次普通报修可获得优先回访提醒。', 180, 10, 'available', datetime('now')
FROM communities WHERE slug = 'pengyi';

INSERT OR IGNORE INTO point_rewards(community_id, name, description, points_cost, stock, status, created_at)
SELECT id, '社区活动兑换券', '用于兑换一次社区活动报名名额。', 260, 8, 'available', datetime('now')
FROM communities WHERE slug = 'pengyi';
