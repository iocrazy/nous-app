-- 413_social_accounts_realtime.sql
--
-- social_accounts 加入 Realtime publication
--
-- ⚠️ 本文件原名 412_social_accounts_realtime.sql(PR #1753),与
-- 412_retire_canvas_stage_node.sql(PR #1752)撞号 —— 两个 PR 各自取号时都只
-- 看到 411 是最大号。改名为 413 消除隐患。相对顺序不变:原来按文件名排序是
-- 412_retire < 412_social(字母序 r<s),现在是 412_retire < 413_social。
--
-- 用户两次反馈同一个现象:扫码绑定成功了,账号卡片却不出现,**手动刷新网页
-- 才看到**(2026-08-06 抖音一次,2026-08-08 B站一次)。
--
-- 根因不在后端:workflow 的顺序是对的 —— 先 upsert_session_account 入库,
-- 成功之后才把 metadata.login 写成 success。前端看到 success 时,账号行确实
-- 已经在库里了。
--
-- 真正的缺口是**账号列表根本没有实时信号**。publication 里只有
-- task_tracking,所以:
--
--   * 登录弹窗订阅 task_tracking → 二维码、扫码进度都能实时更新(工作正常)
--   * 账号列表页只在挂载时 listAccounts() 拉一次,之后完全静止
--
-- 列表刷新全靠"绑定成功那一刻的一次 reload"。上次修复给 onClose 也加了
-- reload 当兜底,但**两条路径是同一个 HTTP 请求** —— 那一刻网络抖动(用户
-- 截图里就有一批 503 与 name resolution failed),两条一起失败,列表便一直
-- 停在旧数据,且没有任何后续补救。
--
-- 加进 publication 后,social_accounts 的插入/更新会直接推到订阅端,不再依赖
-- 那一次请求成功。这是"补一条独立的信号通道",不是"重试得更狠"。
--
-- ⚠️ REPLICA IDENTITY 保持 default(仅主键)。改成 FULL 会让每次 UPDATE 把
-- **整行旧值**写进 WAL —— 而这张表存着 Fernet 加密的 session_state 和
-- access_token,那等于把凭证密文复制进复制流。前端只需要知道"某行变了"然后
-- 重新拉列表,不需要 DELETE 事件里的旧值,所以 default 足够且更安全。

-- 幂等:`ALTER PUBLICATION ... ADD TABLE` 没有 IF NOT EXISTS,重复执行会抛
-- 42710 duplicate_object。而这条迁移的原版(412_social_accounts_realtime.sql)
-- **已经在生产跑过了** —— 生产库 pg_publication_tables 里 social_accounts 确实
-- 在 supabase_realtime 中。改名若被 run-migration.yml 误判成"新增文件"就会重跑
-- 并直接红掉,所以先查再加。
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname = 'supabase_realtime'
      AND schemaname = 'public'
      AND tablename = 'social_accounts'
  ) THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.social_accounts;
    RAISE NOTICE 'social_accounts added to supabase_realtime publication';
  ELSE
    RAISE NOTICE 'social_accounts already in supabase_realtime publication, skipping';
  END IF;
END
$$;
