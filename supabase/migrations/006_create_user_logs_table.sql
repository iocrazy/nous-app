-- 创建用户操作日志表
CREATE TABLE IF NOT EXISTS user_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

    -- 日志内容
    action VARCHAR(50) NOT NULL,           -- 操作类型: fetch, download, delete, retry, etc.
    message TEXT NOT NULL,                  -- 日志消息
    status VARCHAR(20) DEFAULT 'info',      -- 状态: success, error, warning, info, pending

    -- 关联数据（可选）
    aweme_id VARCHAR(50),                   -- 关联的视频 ID
    details JSONB,                          -- 额外详情（JSON 格式）

    -- 时间戳
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_user_logs_user_id ON user_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_user_logs_created_at ON user_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_logs_action ON user_logs(action);

-- 启用 RLS
ALTER TABLE user_logs ENABLE ROW LEVEL SECURITY;

-- RLS 策略：用户只能查看自己的日志
CREATE POLICY "Users can view own logs" ON user_logs
    FOR SELECT USING (auth.uid() = user_id);

-- RLS 策略：用户可以插入自己的日志
CREATE POLICY "Users can insert own logs" ON user_logs
    FOR INSERT WITH CHECK (auth.uid() = user_id);

-- 服务端（service_role）可以完全访问
CREATE POLICY "Service role full access" ON user_logs
    FOR ALL USING (auth.role() = 'service_role');

-- 添加注释
COMMENT ON TABLE user_logs IS '用户操作日志表';
COMMENT ON COLUMN user_logs.action IS '操作类型: fetch, download, delete, retry, update, login, logout';
COMMENT ON COLUMN user_logs.status IS '状态: success, error, warning, info, pending';
