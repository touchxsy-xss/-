ALTER TABLE announcements ADD COLUMN IF NOT EXISTS image_url TEXT;
ALTER TABLE announcements ADD COLUMN IF NOT EXISTS link_url TEXT;
ALTER TABLE announcements ADD COLUMN IF NOT EXISTS link_type VARCHAR(40) NOT NULL DEFAULT 'announcement';
ALTER TABLE announcements ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS announcements_carousel_idx
    ON announcements(community_id, status, sort_order, published_at DESC);
