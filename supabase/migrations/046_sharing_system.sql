-- 046_sharing_system.sql
-- Sharing system: shares, share_views, review_comments extensions
-- Supports 4 share types: link, review, presentation, delivery

-- ============================================================================
-- Part 1: shares — Unified sharing table
-- ============================================================================

CREATE TABLE IF NOT EXISTS shares (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  resource_id     UUID REFERENCES resources(id) ON DELETE CASCADE,
  project_file_id UUID REFERENCES project_files(id) ON DELETE CASCADE,
  folder_id       UUID REFERENCES folders(id) ON DELETE CASCADE,
  version_id      UUID REFERENCES file_versions(id) ON DELETE SET NULL,
  share_type      VARCHAR(20) NOT NULL
    CHECK (share_type IN ('link', 'review', 'presentation', 'delivery')),
  shared_by       UUID NOT NULL REFERENCES auth.users(id),
  share_name      VARCHAR(200) NOT NULL,
  share_code      VARCHAR(20) UNIQUE NOT NULL,
  password        TEXT,
  allow_download  BOOLEAN NOT NULL DEFAULT true,
  expires_at      TIMESTAMPTZ,
  max_views       INTEGER,
  view_count      INTEGER NOT NULL DEFAULT 0,
  watermark       BOOLEAN NOT NULL DEFAULT false,
  status          VARCHAR(20) NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'expired', 'cancelled')),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  -- At least one target must be set
  CONSTRAINT shares_has_target CHECK (
    resource_id IS NOT NULL
    OR project_file_id IS NOT NULL
    OR folder_id IS NOT NULL
  )
);

-- ============================================================================
-- Part 2: share_views — View and favorite tracking
-- ============================================================================

CREATE TABLE IF NOT EXISTS share_views (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  share_id        UUID NOT NULL REFERENCES shares(id) ON DELETE CASCADE,
  viewer_id       UUID REFERENCES auth.users(id),  -- NULL for anonymous viewers
  is_favorited    BOOLEAN NOT NULL DEFAULT false,
  last_viewed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  view_count      INTEGER NOT NULL DEFAULT 1,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (share_id, viewer_id)
);

-- ============================================================================
-- Part 3: Extend review_comments for annotations
-- ============================================================================

ALTER TABLE review_comments
  ADD COLUMN IF NOT EXISTS drawing_data JSONB,
  ADD COLUMN IF NOT EXISTS visibility VARCHAR(20) NOT NULL DEFAULT 'all'
    CHECK (visibility IN ('all', 'team', 'private')),
  ADD COLUMN IF NOT EXISTS attachments TEXT[],
  ADD COLUMN IF NOT EXISTS mentions UUID[],
  ADD COLUMN IF NOT EXISTS share_id UUID REFERENCES shares(id) ON DELETE SET NULL;

-- ============================================================================
-- Part 4: Indexes
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_shares_resource
  ON shares (resource_id)
  WHERE resource_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_shares_project_file
  ON shares (project_file_id)
  WHERE project_file_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_shares_folder
  ON shares (folder_id)
  WHERE folder_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_shares_code
  ON shares (share_code);

CREATE INDEX IF NOT EXISTS idx_shares_shared_by
  ON shares (shared_by);

CREATE INDEX IF NOT EXISTS idx_shares_status
  ON shares (status)
  WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_share_views_share
  ON share_views (share_id);

CREATE INDEX IF NOT EXISTS idx_share_views_viewer
  ON share_views (viewer_id)
  WHERE viewer_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_review_comments_share
  ON review_comments (share_id)
  WHERE share_id IS NOT NULL;

-- ============================================================================
-- Part 5: Enable RLS
-- ============================================================================

ALTER TABLE shares ENABLE ROW LEVEL SECURITY;
ALTER TABLE share_views ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- Part 6: RLS Policies — shares
-- ============================================================================

-- SELECT: share creator can read; anyone with share_code can read active shares
DROP POLICY IF EXISTS "Users can read own shares" ON shares;
CREATE POLICY "Users can read own shares"
  ON shares FOR SELECT
  USING (
    shared_by = auth.uid()
    OR status = 'active'  -- active shares are publicly readable (access controlled by share_code in app)
  );

DROP POLICY IF EXISTS "Users can create shares" ON shares;
CREATE POLICY "Users can create shares"
  ON shares FOR INSERT
  WITH CHECK (shared_by = auth.uid());

DROP POLICY IF EXISTS "Users can update own shares" ON shares;
CREATE POLICY "Users can update own shares"
  ON shares FOR UPDATE
  USING (shared_by = auth.uid())
  WITH CHECK (shared_by = auth.uid());

DROP POLICY IF EXISTS "Users can delete own shares" ON shares;
CREATE POLICY "Users can delete own shares"
  ON shares FOR DELETE
  USING (shared_by = auth.uid());

DROP POLICY IF EXISTS "Service role full access on shares" ON shares;
CREATE POLICY "Service role full access on shares"
  ON shares FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- Part 7: RLS Policies — share_views
-- ============================================================================

-- SELECT: anyone can read views for shares they can see
DROP POLICY IF EXISTS "Users can read share views" ON share_views;
CREATE POLICY "Users can read share views"
  ON share_views FOR SELECT
  USING (
    share_id IN (SELECT id FROM shares)
  );

-- INSERT: anyone can create a view record
DROP POLICY IF EXISTS "Anyone can create share views" ON share_views;
CREATE POLICY "Anyone can create share views"
  ON share_views FOR INSERT
  WITH CHECK (true);

-- UPDATE: viewers can update own records (e.g. toggle favorite)
DROP POLICY IF EXISTS "Viewers can update own share views" ON share_views;
CREATE POLICY "Viewers can update own share views"
  ON share_views FOR UPDATE
  USING (viewer_id = auth.uid())
  WITH CHECK (viewer_id = auth.uid());

DROP POLICY IF EXISTS "Service role full access on share_views" ON share_views;
CREATE POLICY "Service role full access on share_views"
  ON share_views FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- Part 8: Realtime
-- ============================================================================

ALTER PUBLICATION supabase_realtime ADD TABLE shares;

-- ============================================================================
-- Done!
-- Verify: SELECT count(*) FROM shares;       -- 0
--         SELECT count(*) FROM share_views;   -- 0
--         \d review_comments                  -- should show drawing_data, visibility, etc.
-- ============================================================================
