-- ============================================
-- API 密钥管理系统迁移
-- 创建时间: 2026-01-13
-- 描述: 添加 API 密钥表和相关 RLS 策略
-- ============================================

-- ============================================
-- 1. 创建 API 密钥状态枚举
-- ============================================
DO $$ BEGIN
    CREATE TYPE api_key_status AS ENUM (
        'active',
        'revoked',
        'expired'
    );
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- ============================================
-- 2. 创建 API 密钥表
-- ============================================
CREATE TABLE IF NOT EXISTS api_keys (
    id BIGSERIAL PRIMARY KEY,

    -- 密钥标识（公开部分，用于显示和查找）
    key_id VARCHAR(32) NOT NULL UNIQUE,

    -- 密钥哈希（SHA-256，存储完整密钥的哈希）
    key_hash VARCHAR(64) NOT NULL,

    -- 密钥前缀（用于用户识别，如 "dk_xxxx..."）
    key_prefix VARCHAR(20) NOT NULL,

    -- 密钥名称（用户自定义）
    name VARCHAR(255) NOT NULL,

    -- 描述
    description TEXT,

    -- 所属用户
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

    -- 权限范围（JSON 数组）
    scopes JSONB NOT NULL DEFAULT '[]'::JSONB,

    -- 状态
    status api_key_status NOT NULL DEFAULT 'active',

    -- 过期时间（NULL 表示永不过期）
    expires_at TIMESTAMPTZ,

    -- 最后使用时间
    last_used_at TIMESTAMPTZ,

    -- 使用次数
    usage_count INTEGER DEFAULT 0,

    -- 速率限制（每分钟请求数，NULL 表示使用默认）
    rate_limit INTEGER,

    -- 时间戳
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 添加注释
COMMENT ON TABLE api_keys IS 'API 密钥管理表';
COMMENT ON COLUMN api_keys.key_id IS '密钥公开标识符';
COMMENT ON COLUMN api_keys.key_hash IS '密钥 SHA-256 哈希值';
COMMENT ON COLUMN api_keys.key_prefix IS '密钥前缀，用于用户识别';
COMMENT ON COLUMN api_keys.scopes IS '权限范围 JSON 数组';

-- ============================================
-- 3. 创建索引
-- ============================================
CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_key_id ON api_keys(key_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_key_hash ON api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_api_keys_status ON api_keys(status);
CREATE INDEX IF NOT EXISTS idx_api_keys_expires_at ON api_keys(expires_at) WHERE expires_at IS NOT NULL;

-- ============================================
-- 4. 更新时间触发器
-- ============================================
CREATE TRIGGER update_api_keys_updated_at
    BEFORE UPDATE ON api_keys
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- 5. 启用 RLS 并创建策略
-- ============================================
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;

-- 用户只能查看自己的 API 密钥
CREATE POLICY "用户可以查看自己的API密钥" ON api_keys
    FOR SELECT USING (auth.uid() = user_id);

-- 用户可以创建自己的 API 密钥
CREATE POLICY "用户可以创建自己的API密钥" ON api_keys
    FOR INSERT WITH CHECK (auth.uid() = user_id);

-- 用户可以更新自己的 API 密钥
CREATE POLICY "用户可以更新自己的API密钥" ON api_keys
    FOR UPDATE USING (auth.uid() = user_id);

-- 用户可以删除自己的 API 密钥
CREATE POLICY "用户可以删除自己的API密钥" ON api_keys
    FOR DELETE USING (auth.uid() = user_id);

-- ============================================
-- 6. 创建使用计数更新函数
-- ============================================
CREATE OR REPLACE FUNCTION increment_api_key_usage(p_key_id VARCHAR)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    UPDATE api_keys
    SET
        usage_count = usage_count + 1,
        last_used_at = NOW()
    WHERE key_id = p_key_id;
END;
$$;

-- ============================================
-- 7. 创建 API 密钥使用日志表（可选，用于审计）
-- ============================================
CREATE TABLE IF NOT EXISTS api_key_logs (
    id BIGSERIAL PRIMARY KEY,

    -- 关联的 API 密钥
    api_key_id BIGINT NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,

    -- 请求信息
    endpoint VARCHAR(255) NOT NULL,
    method VARCHAR(10) NOT NULL,
    ip_address INET,
    user_agent TEXT,

    -- 响应状态
    status_code INTEGER,

    -- 时间戳
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 日志表索引
CREATE INDEX IF NOT EXISTS idx_api_key_logs_api_key_id ON api_key_logs(api_key_id);
CREATE INDEX IF NOT EXISTS idx_api_key_logs_created_at ON api_key_logs(created_at DESC);

-- 日志表 RLS
ALTER TABLE api_key_logs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "用户可以查看自己密钥的日志" ON api_key_logs
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM api_keys
            WHERE api_keys.id = api_key_logs.api_key_id
            AND api_keys.user_id = auth.uid()
        )
    );

-- ============================================
-- 8. 添加服务角色访问策略（允许后端 service_role 操作）
-- ============================================
-- 注意：service_role 默认绑过 RLS，无需额外策略

COMMENT ON TABLE api_key_logs IS 'API 密钥使用日志表';
