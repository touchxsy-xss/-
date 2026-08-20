ALTER TABLE announcements ADD COLUMN image_url TEXT;
ALTER TABLE announcements ADD COLUMN link_url TEXT;
ALTER TABLE announcements ADD COLUMN link_type TEXT NOT NULL DEFAULT 'announcement';
ALTER TABLE announcements ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS announcements_carousel_idx
    ON announcements(community_id, status, sort_order, published_at DESC);
