-- Migration: 008_create_user_settings_table
-- Description: 创建用户设置表，存储每个用户的个人配置

-- 创建用户设置表
CREATE TABLE IF NOT EXISTS public.user_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

    -- 服务器配置
    download_path TEXT DEFAULT '/home/user/downloads/douyin',

    -- 其他设置可以扩展
    settings_json JSONB DEFAULT '{}',

    -- 时间戳
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- 每个用户只能有一条设置记录
    UNIQUE(user_id)
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_user_settings_user_id ON public.user_settings(user_id);

-- 启用 RLS
ALTER TABLE public.user_settings ENABLE ROW LEVEL SECURITY;

-- RLS 策略：用户只能访问自己的设置
CREATE POLICY "Users can view own settings"
    ON public.user_settings FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own settings"
    ON public.user_settings FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own settings"
    ON public.user_settings FOR UPDATE
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can delete own settings"
    ON public.user_settings FOR DELETE
    USING (auth.uid() = user_id);

-- 创建更新时间触发器
CREATE OR REPLACE FUNCTION update_user_settings_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_user_settings_updated_at
    BEFORE UPDATE ON public.user_settings
    FOR EACH ROW
    EXECUTE FUNCTION update_user_settings_updated_at();

-- 添加注释
COMMENT ON TABLE public.user_settings IS '用户个人设置表';
COMMENT ON COLUMN public.user_settings.user_id IS '用户ID，关联 auth.users';
COMMENT ON COLUMN public.user_settings.download_path IS '默认下载路径';
COMMENT ON COLUMN public.user_settings.settings_json IS '其他扩展设置（JSON格式）';
