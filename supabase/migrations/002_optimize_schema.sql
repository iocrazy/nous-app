-- 数据库优化迁移
-- v2.0.0: 优化表结构，添加作者表和收藏夹功能

-- ============================================
-- 1. 创建作者表
-- ============================================
CREATE TABLE IF NOT EXISTS authors (
    id BIGSERIAL PRIMARY KEY,

    -- 抖音作者ID
    author_id VARCHAR(64) UNIQUE,

    -- 作者信息
    nickname VARCHAR(255) NOT NULL,
    signature TEXT,
    avatar_url TEXT,

    -- 统计数据（可选，定期更新）
    follower_count INTEGER DEFAULT 0,
    following_count INTEGER DEFAULT 0,
    total_favorited INTEGER DEFAULT 0,

    -- 时间戳
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_authors_nickname ON authors(nickname);
CREATE INDEX IF NOT EXISTS idx_authors_author_id ON authors(author_id);

-- 更新时间触发器
CREATE TRIGGER update_authors_updated_at
    BEFORE UPDATE ON authors
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- 2. 为 douyin_videos 添加新字段
-- ============================================
ALTER TABLE douyin_videos
ADD COLUMN IF NOT EXISTS author_id BIGINT REFERENCES authors(id) ON DELETE SET NULL;

ALTER TABLE douyin_videos
ADD COLUMN IF NOT EXISTS cover_url TEXT;

CREATE INDEX IF NOT EXISTS idx_douyin_videos_author_id ON douyin_videos(author_id);

-- ============================================
-- 3. 创建视频类型枚举
-- ============================================
DO $$ BEGIN
    CREATE TYPE aweme_type_enum AS ENUM (
        '0',   -- 标准视频
        '2',   -- 图片轮播/图集
        '4',   -- 特殊视频
        '61',  -- 特殊视频变体
        '68',  -- 图文
        '150', -- 其他类型
        '157'  -- 其他类型
    );
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- ============================================
-- 4. 创建收藏夹表
-- ============================================
CREATE TABLE IF NOT EXISTS collections (
    id BIGSERIAL PRIMARY KEY,

    -- 收藏夹信息
    name VARCHAR(255) NOT NULL,
    description TEXT,

    -- 用户关联
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,

    -- 时间戳
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 视频-收藏夹关联表
CREATE TABLE IF NOT EXISTS video_collections (
    video_id BIGINT REFERENCES douyin_videos(id) ON DELETE CASCADE,
    collection_id BIGINT REFERENCES collections(id) ON DELETE CASCADE,
    added_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (video_id, collection_id)
);

-- ============================================
-- 5. 启用 RLS
-- ============================================
ALTER TABLE authors ENABLE ROW LEVEL SECURITY;
ALTER TABLE collections ENABLE ROW LEVEL SECURITY;
ALTER TABLE video_collections ENABLE ROW LEVEL SECURITY;

-- authors 策略
CREATE POLICY "认证用户可以查看作者" ON authors
    FOR SELECT USING (auth.role() = 'authenticated');

CREATE POLICY "认证用户可以插入作者" ON authors
    FOR INSERT WITH CHECK (auth.role() = 'authenticated');

-- collections 策略
CREATE POLICY "用户可以查看自己的收藏夹" ON collections
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "用户可以创建自己的收藏夹" ON collections
    FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "用户可以更新自己的收藏夹" ON collections
    FOR UPDATE USING (auth.uid() = user_id);

CREATE POLICY "用户可以删除自己的收藏夹" ON collections
    FOR DELETE USING (auth.uid() = user_id);

-- video_collections 策略
CREATE POLICY "用户可以查看自己收藏夹中的视频" ON video_collections
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM collections
            WHERE id = collection_id AND user_id = auth.uid()
        )
    );

CREATE POLICY "用户可以添加视频到自己的收藏夹" ON video_collections
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1 FROM collections
            WHERE id = collection_id AND user_id = auth.uid()
        )
    );

CREATE POLICY "用户可以从自己的收藏夹移除视频" ON video_collections
    FOR DELETE USING (
        EXISTS (
            SELECT 1 FROM collections
            WHERE id = collection_id AND user_id = auth.uid()
        )
    );

-- ============================================
-- 6. 更新统计视图
-- ============================================
DROP VIEW IF EXISTS video_statistics;
CREATE VIEW video_statistics WITH (security_invoker = true) AS
SELECT
    COUNT(*) as total_videos,
    COUNT(*) FILTER (WHERE video_download_status = 'completed') as downloaded,
    COUNT(*) FILTER (WHERE video_download_status = 'pending') as pending,
    COUNT(*) FILTER (WHERE video_download_status = 'downloading') as downloading,
    COUNT(*) FILTER (WHERE video_download_status = 'failed') as failed,
    COUNT(*) FILTER (WHERE video_download_status = 'skipped') as skipped,
    COUNT(*) FILTER (WHERE aweme_type = '0') as standard_videos,
    COUNT(*) FILTER (WHERE aweme_type = '2') as image_collections,
    COUNT(*) FILTER (WHERE aweme_type = '68') as image_texts,
    COUNT(*) FILTER (WHERE aweme_type IN ('4', '61')) as special_videos,
    COUNT(DISTINCT author) as unique_authors,
    SUM(video_digg_count) as total_likes,
    SUM(video_comment_count) as total_comments,
    SUM(video_share_count) as total_shares
FROM douyin_videos;

-- 作者统计视图
CREATE OR REPLACE VIEW author_statistics WITH (security_invoker = true) AS
SELECT
    author,
    COUNT(*) as video_count,
    SUM(video_digg_count) as total_likes,
    SUM(video_comment_count) as total_comments,
    AVG(video_digg_count) as avg_likes
FROM douyin_videos
WHERE author IS NOT NULL
GROUP BY author
ORDER BY video_count DESC;

-- ============================================
-- 7. 删除不需要的表
-- ============================================
-- download_tasks 功能已合并到 douyin_videos
DROP TABLE IF EXISTS download_tasks CASCADE;

-- statistics 表使用视图替代
DROP TABLE IF EXISTS statistics CASCADE;
