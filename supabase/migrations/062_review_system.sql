-- 062_review_system.sql
-- Review system: comments, annotations, and approval status
-- NOTE: resources.id is BIGINT (Snowflake), resource_versions.id is UUID, auth.users.id is UUID

-- Drop old review_comments table (different schema from previous design iteration)
DROP TABLE IF EXISTS review_comments CASCADE;

-- Review comments (with optional timecode for video)
CREATE TABLE review_comments (
  id              BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  resource_id     BIGINT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  version_id      UUID REFERENCES resource_versions(id) ON DELETE SET NULL,
  author_id       UUID NOT NULL REFERENCES auth.users(id),
  timecode        FLOAT,                -- video seconds (null = general comment)
  frame_number    INTEGER,              -- corresponding frame number
  content         TEXT NOT NULL,         -- comment text (supports @mention)
  status          VARCHAR(20) NOT NULL DEFAULT 'open',  -- 'open' / 'resolved' / 'wontfix'
  parent_id       BIGINT REFERENCES review_comments(id) ON DELETE CASCADE,  -- reply thread
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Canvas annotations (bound to comment)
CREATE TABLE review_annotations (
  id              BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  comment_id      BIGINT NOT NULL REFERENCES review_comments(id) ON DELETE CASCADE,
  tool_type       VARCHAR(20) NOT NULL,  -- 'arrow' / 'rect' / 'freehand' / 'text'
  data            JSONB NOT NULL,        -- coordinates, color, width, etc.
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Resource approval status
CREATE TABLE review_status (
  id              BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  resource_id     BIGINT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  version_id      UUID REFERENCES resource_versions(id) ON DELETE SET NULL,
  reviewer_id     UUID NOT NULL REFERENCES auth.users(id),
  status          VARCHAR(20) NOT NULL DEFAULT 'pending',  -- 'pending' / 'approved' / 'needs_changes' / 'rejected'
  comment         TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_review_comments_resource ON review_comments(resource_id);
CREATE INDEX idx_review_comments_version ON review_comments(version_id);
CREATE INDEX idx_review_comments_author ON review_comments(author_id);
CREATE INDEX idx_review_comments_parent ON review_comments(parent_id);
CREATE INDEX idx_review_comments_status ON review_comments(status);

CREATE INDEX idx_review_annotations_comment ON review_annotations(comment_id);

CREATE INDEX idx_review_status_resource ON review_status(resource_id);
CREATE INDEX idx_review_status_version ON review_status(version_id);
CREATE INDEX idx_review_status_reviewer ON review_status(reviewer_id);

-- RLS policies
ALTER TABLE review_comments ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_annotations ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_status ENABLE ROW LEVEL SECURITY;

-- Allow authenticated users to read all comments on resources they can access
CREATE POLICY "Users can read review comments" ON review_comments
  FOR SELECT USING (auth.uid() IS NOT NULL);

CREATE POLICY "Users can insert review comments" ON review_comments
  FOR INSERT WITH CHECK (auth.uid() = author_id);

CREATE POLICY "Authors can update own comments" ON review_comments
  FOR UPDATE USING (auth.uid() = author_id);

CREATE POLICY "Authors can delete own comments" ON review_comments
  FOR DELETE USING (auth.uid() = author_id);

-- Annotations follow comment access
CREATE POLICY "Users can read annotations" ON review_annotations
  FOR SELECT USING (auth.uid() IS NOT NULL);

CREATE POLICY "Users can insert annotations" ON review_annotations
  FOR INSERT WITH CHECK (auth.uid() IS NOT NULL);

CREATE POLICY "Users can delete own annotations" ON review_annotations
  FOR DELETE USING (
    EXISTS (
      SELECT 1 FROM review_comments rc
      WHERE rc.id = review_annotations.comment_id
      AND rc.author_id = auth.uid()
    )
  );

-- Review status access
CREATE POLICY "Users can read review status" ON review_status
  FOR SELECT USING (auth.uid() IS NOT NULL);

CREATE POLICY "Users can insert review status" ON review_status
  FOR INSERT WITH CHECK (auth.uid() = reviewer_id);

CREATE POLICY "Reviewers can update own status" ON review_status
  FOR UPDATE USING (auth.uid() = reviewer_id);
