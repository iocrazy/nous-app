-- 006_collection_videos.sql
-- Many-to-many: collections <-> videos

CREATE TABLE collection_videos (
  collection_id UUID REFERENCES collections(id) ON DELETE CASCADE,
  video_aweme_id VARCHAR(50) REFERENCES douyin_videos(aweme_id) ON DELETE CASCADE,
  added_by UUID REFERENCES auth.users(id),
  added_at TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (collection_id, video_aweme_id)
);

-- RLS
ALTER TABLE collection_videos ENABLE ROW LEVEL SECURITY;

-- View/manage: same as collection access
CREATE POLICY "Collection members can view videos" ON collection_videos
  FOR SELECT USING (
    collection_id IN (
      SELECT id FROM collections WHERE
        owner_id = auth.uid() OR
        team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
    )
  );

CREATE POLICY "Collection members can add videos" ON collection_videos
  FOR INSERT WITH CHECK (
    collection_id IN (
      SELECT id FROM collections WHERE
        owner_id = auth.uid() OR
        team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
    )
  );

CREATE POLICY "Collection members can remove videos" ON collection_videos
  FOR DELETE USING (
    collection_id IN (
      SELECT id FROM collections WHERE
        owner_id = auth.uid() OR
        team_id IN (SELECT team_id FROM team_members WHERE user_id = auth.uid())
    )
  );
