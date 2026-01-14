-- 添加封面相关字段
-- 用于存储和下载视频封面图片

-- ============================================
-- 1. 添加封面字段到 douyin_videos 表
-- ============================================

-- 封面 URL（存储原始封面链接列表）
ALTER TABLE douyin_videos
ADD COLUMN IF NOT EXISTS cover_urls JSONB DEFAULT '[]'::JSONB;

-- 动态封面 URL（GIF 格式）
ALTER TABLE douyin_videos
ADD COLUMN IF NOT EXISTS dynamic_cover_url TEXT;

-- 是否需要下载封面
ALTER TABLE douyin_videos
ADD COLUMN IF NOT EXISTS need_download_cover BOOLEAN DEFAULT TRUE;

-- 封面下载状态
ALTER TABLE douyin_videos
ADD COLUMN IF NOT EXISTS cover_download_status download_status DEFAULT 'pending';

-- 封面本地保存路径
ALTER TABLE douyin_videos
ADD COLUMN IF NOT EXISTS cover_download_path TEXT;

-- ============================================
-- 2. 添加索引
-- ============================================

CREATE INDEX IF NOT EXISTS idx_douyin_videos_cover_status
ON douyin_videos(cover_download_status);

-- ============================================
-- 3. 更新统计视图
-- ============================================

CREATE OR REPLACE VIEW video_statistics AS
SELECT
    COUNT(*) as total_videos,
    COUNT(*) FILTER (WHERE video_download_status = 'completed') as downloaded,
    COUNT(*) FILTER (WHERE video_download_status = 'pending') as pending,
    COUNT(*) FILTER (WHERE video_download_status = 'failed') as failed,
    COUNT(*) FILTER (WHERE video_download_status = 'skipped') as skipped,
    COUNT(*) FILTER (WHERE cover_download_status = 'completed') as covers_downloaded,
    COUNT(*) FILTER (WHERE aweme_type = '0') as standard_videos,
    COUNT(*) FILTER (WHERE aweme_type = '2') as image_collections,
    COUNT(*) FILTER (WHERE aweme_type = '68') as image_texts,
    COUNT(*) FILTER (WHERE aweme_type IN ('4', '61')) as special_videos
FROM douyin_videos;

-- ============================================
-- 4. 注释说明
-- ============================================

COMMENT ON COLUMN douyin_videos.cover_urls IS '封面图片 URL 列表（原始、高清等多个版本）';
COMMENT ON COLUMN douyin_videos.dynamic_cover_url IS '动态封面 URL（GIF 格式，用于预览）';
COMMENT ON COLUMN douyin_videos.need_download_cover IS '是否需要下载封面';
COMMENT ON COLUMN douyin_videos.cover_download_status IS '封面下载状态';
COMMENT ON COLUMN douyin_videos.cover_download_path IS '封面本地保存路径';
