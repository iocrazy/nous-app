-- 315_user_topic_interests.sql
-- Per-user interest profile for the Topic Inspiration "For You" view. The user
-- writes free-text interests; we embed it (Volcengine doubao-embedding-vision,
-- 2048-dim, same as hotspots) and rank hotspots by cosine similarity to it.
-- One row per user. Embedding unindexed (2048 > pgvector 2000-dim index cap).

CREATE TABLE IF NOT EXISTS user_topic_interests (
    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    interest_text TEXT NOT NULL DEFAULT '',
    embedding vector(2048),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE user_topic_interests ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Manage own topic interest" ON user_topic_interests FOR ALL
    USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Service role full access on user_topic_interests"
    ON user_topic_interests FOR ALL
    USING (auth.jwt()->>'role' = 'service_role');

NOTIFY pgrst, 'reload schema';
