-- Supabase 初始数据库结构
-- 抖音视频分析和下载系统

-- ============================================
-- 1. 启用必要的扩展
-- ============================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================
-- 2. 创建枚举类型
-- ============================================

-- 下载状态枚举
CREATE TYPE download_status AS ENUM (
    'pending',
    'downloading',
    'completed',
    'failed',
    'skipped'
);

-- 用户角色枚举
CREATE TYPE user_role AS ENUM (
    'admin',
    'user',
    'test'
);

-- ============================================
-- 3. 创建用户配置表（扩展 Supabase Auth）
-- ============================================

CREATE TABLE IF NOT EXISTS user_profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    username VARCHAR(255) UNIQUE,
    avatar_url TEXT,
    role user_role DEFAULT 'user',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 创建更新时间触发器
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_user_profiles_updated_at
    BEFORE UPDATE ON user_profiles
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- 4. 创建抖音视频数据表
-- ============================================

CREATE TABLE IF NOT EXISTS douyin_videos (
    id BIGSERIAL PRIMARY KEY,

    -- 视频唯一标识
    aweme_id VARCHAR(255) NOT NULL UNIQUE,

    -- 视频互动数据
    video_digg_count INTEGER DEFAULT 0,
    video_comment_count INTEGER DEFAULT 0,
    video_share_count INTEGER DEFAULT 0,
    video_collect_count INTEGER DEFAULT 0,

    -- 视频元数据
    video_original_url VARCHAR(512) NOT NULL,
    video_duration VARCHAR(50),
    video_resolution VARCHAR(50),
    video_datasize VARCHAR(50),
    video_hashtag_name TEXT,
    video_created_time TIMESTAMPTZ,
    author VARCHAR(255),
    video_title TEXT,
    aweme_type VARCHAR(50),
    video_desc TEXT,
    video_categories VARCHAR(255),

    -- 视频下载信息
    need_download_video BOOLEAN DEFAULT TRUE,
    video_download_urls JSONB DEFAULT '[]'::JSONB,
    image_download_urls JSONB DEFAULT '[]'::JSONB,

    -- 音频信息
    music_download_urls JSONB DEFAULT '[]'::JSONB,
    music_name VARCHAR(255),
    need_download_music BOOLEAN DEFAULT FALSE,

    -- 下载状态跟踪
    video_download_status download_status DEFAULT 'pending',
    music_download_status download_status DEFAULT 'pending',
    download_duration FLOAT,
    download_path TEXT,
    error_message TEXT,
    download_time TIMESTAMPTZ,

    -- 用户关联（可选）
    user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,

    -- 时间戳
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_douyin_videos_aweme_id ON douyin_videos(aweme_id);
CREATE INDEX IF NOT EXISTS idx_douyin_videos_author ON douyin_videos(author);
CREATE INDEX IF NOT EXISTS idx_douyin_videos_download_status ON douyin_videos(video_download_status);
CREATE INDEX IF NOT EXISTS idx_douyin_videos_created_at ON douyin_videos(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_douyin_videos_user_id ON douyin_videos(user_id);
CREATE INDEX IF NOT EXISTS idx_douyin_videos_aweme_type ON douyin_videos(aweme_type);

-- 更新时间触发器
CREATE TRIGGER update_douyin_videos_updated_at
    BEFORE UPDATE ON douyin_videos
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- 5. 创建下载任务队列表
-- ============================================

CREATE TABLE IF NOT EXISTS download_tasks (
    id BIGSERIAL PRIMARY KEY,

    -- 关联视频
    aweme_id VARCHAR(255) NOT NULL REFERENCES douyin_videos(aweme_id) ON DELETE CASCADE,

    -- 任务类型
    task_type VARCHAR(50) NOT NULL, -- 'video', 'music', 'images'

    -- 任务状态
    status download_status DEFAULT 'pending',
    priority INTEGER DEFAULT 0, -- 优先级，数字越大优先级越高

    -- 执行信息
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    error_message TEXT,

    -- 时间戳
    scheduled_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_download_tasks_status ON download_tasks(status);
CREATE INDEX IF NOT EXISTS idx_download_tasks_priority ON download_tasks(priority DESC);
CREATE INDEX IF NOT EXISTS idx_download_tasks_scheduled ON download_tasks(scheduled_at);

-- 更新时间触发器
CREATE TRIGGER update_download_tasks_updated_at
    BEFORE UPDATE ON download_tasks
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- 6. 创建统计表
-- ============================================

CREATE TABLE IF NOT EXISTS statistics (
    id BIGSERIAL PRIMARY KEY,

    -- 统计日期
    stat_date DATE NOT NULL UNIQUE,

    -- 视频统计
    total_videos INTEGER DEFAULT 0,
    videos_downloaded INTEGER DEFAULT 0,
    videos_failed INTEGER DEFAULT 0,

    -- 存储统计
    total_storage_bytes BIGINT DEFAULT 0,

    -- 时间戳
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 更新时间触发器
CREATE TRIGGER update_statistics_updated_at
    BEFORE UPDATE ON statistics
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- 7. 行级安全策略 (RLS)
-- ============================================

-- 启用 RLS
ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE douyin_videos ENABLE ROW LEVEL SECURITY;
ALTER TABLE download_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE statistics ENABLE ROW LEVEL SECURITY;

-- user_profiles 策略
CREATE POLICY "用户可以查看自己的配置" ON user_profiles
    FOR SELECT USING (auth.uid() = id);

CREATE POLICY "用户可以更新自己的配置" ON user_profiles
    FOR UPDATE USING (auth.uid() = id);

CREATE POLICY "用户可以插入自己的配置" ON user_profiles
    FOR INSERT WITH CHECK (auth.uid() = id);

-- douyin_videos 策略
-- 所有认证用户可以查看视频
CREATE POLICY "认证用户可以查看所有视频" ON douyin_videos
    FOR SELECT USING (auth.role() = 'authenticated');

-- 用户只能插入自己的视频
CREATE POLICY "认证用户可以插入视频" ON douyin_videos
    FOR INSERT WITH CHECK (auth.role() = 'authenticated');

-- 用户可以更新自己创建的视频，管理员可以更新所有
CREATE POLICY "用户可以更新自己的视频" ON douyin_videos
    FOR UPDATE USING (
        auth.uid() = user_id OR
        EXISTS (
            SELECT 1 FROM user_profiles
            WHERE id = auth.uid() AND role = 'admin'
        )
    );

-- 只有管理员可以删除视频
CREATE POLICY "管理员可以删除视频" ON douyin_videos
    FOR DELETE USING (
        EXISTS (
            SELECT 1 FROM user_profiles
            WHERE id = auth.uid() AND role = 'admin'
        )
    );

-- download_tasks 策略
CREATE POLICY "认证用户可以查看下载任务" ON download_tasks
    FOR SELECT USING (auth.role() = 'authenticated');

CREATE POLICY "认证用户可以创建下载任务" ON download_tasks
    FOR INSERT WITH CHECK (auth.role() = 'authenticated');

CREATE POLICY "认证用户可以更新下载任务" ON download_tasks
    FOR UPDATE USING (auth.role() = 'authenticated');

-- statistics 策略
CREATE POLICY "所有用户可以查看统计" ON statistics
    FOR SELECT USING (true);

-- ============================================
-- 8. 创建有用的视图
-- ============================================

-- 视频统计视图
CREATE OR REPLACE VIEW video_statistics AS
SELECT
    COUNT(*) as total_videos,
    COUNT(*) FILTER (WHERE video_download_status = 'completed') as downloaded,
    COUNT(*) FILTER (WHERE video_download_status = 'pending') as pending,
    COUNT(*) FILTER (WHERE video_download_status = 'failed') as failed,
    COUNT(*) FILTER (WHERE video_download_status = 'skipped') as skipped,
    COUNT(*) FILTER (WHERE aweme_type = '0') as standard_videos,
    COUNT(*) FILTER (WHERE aweme_type = '2') as image_collections,
    COUNT(*) FILTER (WHERE aweme_type = '68') as image_texts,
    COUNT(*) FILTER (WHERE aweme_type IN ('4', '61')) as special_videos
FROM douyin_videos;

-- 每日统计视图
CREATE OR REPLACE VIEW daily_statistics AS
SELECT
    DATE(created_at) as date,
    COUNT(*) as videos_added,
    COUNT(*) FILTER (WHERE video_download_status = 'completed') as videos_downloaded
FROM douyin_videos
GROUP BY DATE(created_at)
ORDER BY date DESC;

-- ============================================
-- 9. 创建触发器：自动创建用户配置
-- ============================================

CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO user_profiles (id, username, role)
    VALUES (
        NEW.id,
        COALESCE(NEW.raw_user_meta_data->>'username', NEW.email),
        'user'
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE OR REPLACE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION handle_new_user();

-- ============================================
-- 10. 创建函数：更新统计
-- ============================================

CREATE OR REPLACE FUNCTION update_daily_statistics()
RETURNS VOID AS $$
BEGIN
    INSERT INTO statistics (stat_date, total_videos, videos_downloaded, videos_failed)
    SELECT
        CURRENT_DATE,
        COUNT(*),
        COUNT(*) FILTER (WHERE video_download_status = 'completed'),
        COUNT(*) FILTER (WHERE video_download_status = 'failed')
    FROM douyin_videos
    ON CONFLICT (stat_date)
    DO UPDATE SET
        total_videos = EXCLUDED.total_videos,
        videos_downloaded = EXCLUDED.videos_downloaded,
        videos_failed = EXCLUDED.videos_failed,
        updated_at = NOW();
END;
$$ LANGUAGE plpgsql;
