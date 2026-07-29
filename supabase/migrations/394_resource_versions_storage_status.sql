-- resource_versions.storage_status: 迁移探测不到源文件时标记,不删行。
-- 95 个旧日期桶失效行(PR #1601 已对齐 87 个,剩 5 个 B站无 parsed_media
-- 记录)用这个标记,让迁移跳过、前端显示"文件已丢失"而非无限加载。
ALTER TABLE resource_versions
  ADD COLUMN IF NOT EXISTS storage_status text NOT NULL DEFAULT 'ok';
-- 取值: 'ok' | 'source_missing'
COMMENT ON COLUMN resource_versions.storage_status IS
  '迁移/读取时的源文件存在性: ok=正常, source_missing=文件已丢失(记录保留)';
