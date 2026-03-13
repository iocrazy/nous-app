-- Tag Groups (Eagle-style tag categorization)
CREATE TABLE IF NOT EXISTS tag_groups (
  id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  name VARCHAR(50) NOT NULL UNIQUE,
  sort_order INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Add group_id and sort_order to tags
ALTER TABLE tags ADD COLUMN IF NOT EXISTS group_id BIGINT REFERENCES tag_groups(id) ON DELETE SET NULL;
ALTER TABLE tags ADD COLUMN IF NOT EXISTS sort_order INT DEFAULT 0;

-- Index for efficient group filtering
CREATE INDEX IF NOT EXISTS idx_tags_group_id ON tags(group_id);
CREATE INDEX IF NOT EXISTS idx_tag_groups_sort ON tag_groups(sort_order);

-- RLS: tag_groups readable by all authenticated, writable by admin only
ALTER TABLE tag_groups ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Tag groups visible to all authenticated" ON tag_groups
  FOR SELECT TO authenticated USING (true);

-- Admin writes handled via service_role key (bypasses RLS)
