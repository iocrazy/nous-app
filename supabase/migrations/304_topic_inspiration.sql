-- 304_topic_inspiration.sql
-- Topic Inspiration module: signal_sources / hotspots / topic_groups.
-- Phase 1 数据归属: user_id 可空, NULL = 系统级全局 (抓一次, 全员共读).

-- ============ signal_sources ============
CREATE TABLE IF NOT EXISTS signal_sources (
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,  -- NULL = global/system
    kind TEXT NOT NULL CHECK (kind IN ('newsnow', 'rss', 'http_api', 'custom')),
    name TEXT NOT NULL,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    enabled BOOLEAN NOT NULL DEFAULT true,
    category TEXT,
    health TEXT NOT NULL DEFAULT 'ok' CHECK (health IN ('ok', 'degraded', 'dead')),
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_fetched_at TIMESTAMPTZ,
    last_ok_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_signal_sources_enabled ON signal_sources(enabled) WHERE enabled = true;
CREATE INDEX IF NOT EXISTS idx_signal_sources_user ON signal_sources(user_id);

ALTER TABLE signal_sources ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Read global or own sources" ON signal_sources FOR SELECT
    USING (user_id IS NULL OR auth.uid() = user_id);
CREATE POLICY "Manage own sources" ON signal_sources FOR ALL
    USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Service role full access on signal_sources" ON signal_sources FOR ALL
    USING (auth.jwt()->>'role' = 'service_role');

-- ============ topic_groups (created now, populated in Phase 3) ============
CREATE TABLE IF NOT EXISTS topic_groups (
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    source_count INTEGER NOT NULL DEFAULT 1,
    heat NUMERIC NOT NULL DEFAULT 0,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    embedding vector(1536),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE topic_groups ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Read global or own topic_groups" ON topic_groups FOR SELECT
    USING (user_id IS NULL OR auth.uid() = user_id);
CREATE POLICY "Service role full access on topic_groups" ON topic_groups FOR ALL
    USING (auth.jwt()->>'role' = 'service_role');

-- ============ hotspots ============
CREATE TABLE IF NOT EXISTS hotspots (
    id BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,  -- NULL = global
    source_id BIGINT REFERENCES signal_sources(id) ON DELETE SET NULL,
    source_label TEXT,
    title TEXT NOT NULL,
    url TEXT,
    origin_url TEXT,
    content_original TEXT,
    content_translated TEXT,
    summary TEXT,
    ai_summary TEXT,
    reason TEXT,
    score NUMERIC,
    tags TEXT[] NOT NULL DEFAULT '{}',
    category TEXT,
    topic_group_id BIGINT REFERENCES topic_groups(id) ON DELETE SET NULL,
    media_url TEXT,
    cover_url TEXT,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    rank_timeline JSONB NOT NULL DEFAULT '[]'::jsonb,
    dedup_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_hotspots_dedup ON hotspots(dedup_key);
CREATE INDEX IF NOT EXISTS idx_hotspots_captured ON hotspots(captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_hotspots_category ON hotspots(category);

ALTER TABLE hotspots ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Read global or own hotspots" ON hotspots FOR SELECT
    USING (user_id IS NULL OR auth.uid() = user_id);
CREATE POLICY "Service role full access on hotspots" ON hotspots FOR ALL
    USING (auth.jwt()->>'role' = 'service_role');

-- ============ 种子全局信源 (user_id = NULL) ============
INSERT INTO signal_sources (user_id, kind, name, config, category) VALUES
    (NULL, 'newsnow', 'GitHub Trending', '{"platform_id": "github-trending-today"}'::jsonb, 'product'),
    (NULL, 'newsnow', 'Hacker News',     '{"platform_id": "hackernews"}'::jsonb,            'industry'),
    (NULL, 'newsnow', 'V2EX Share',      '{"platform_id": "v2ex-share"}'::jsonb,            'industry'),
    (NULL, 'rss',     'MarkTechPost',    '{"url": "https://www.marktechpost.com/feed/"}'::jsonb, 'model')
ON CONFLICT DO NOTHING;

NOTIFY pgrst, 'reload schema';
