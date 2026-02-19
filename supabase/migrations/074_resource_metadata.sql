-- 074: Add metadata columns to resources for Eagle-style info panel
-- notes: free-text notes field
-- url: source URL
-- rating: star rating 0-5

ALTER TABLE resources
  ADD COLUMN IF NOT EXISTS notes TEXT,
  ADD COLUMN IF NOT EXISTS url TEXT,
  ADD COLUMN IF NOT EXISTS rating SMALLINT CHECK (rating >= 0 AND rating <= 5) DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_resources_rating
  ON resources (rating) WHERE rating > 0 AND is_trashed = false;
