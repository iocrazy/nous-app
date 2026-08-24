-- 438: resources.summary_follow_up —— "转录完成后补摘要"的意图落库
--
-- 背景（harness 落地计划 W2 第 5 项）：用户把生视频附给 agent 时，前端
-- `ensureResourceProcessed` 触发转录，并在**浏览器内存**里登记"转录完成后
-- 要触发摘要"（transcriptionFollowUp 登记表，由 useResourceProcessingFollowUps
-- 消费）。页面一刷新登记表就没了 —— 转录照常完成，摘要永远不来，用户回来
-- 看到的是一份只有转录、没有摘要的资源。PR #1927（2026-08-19）修 dedup 接管
-- 形态时明确写下"已知边界：监听是内存态，页面刷新丢失"。
--
-- 修法是把意图挪到服务端：本列持久化"谁在什么时候要求了转录后补摘要"，
-- ai_transcription 成功链（chain_summary_for_tags 旁边）读取并消费。
--
-- 为什么放 resources 行而不是 task_tracking.metadata：转录触发端点有三个
-- 分支（音频就绪直发 / 已在跑 dedup / 先抽音频再链转录），task 级存储要在
-- 三处分别写入、且"抽音频→链转录"那一跳还要转发 —— 每个接缝都是意图被
-- 静默丢掉的机会。resource 行是三个分支唯一共享的实体，一次写覆盖全部；
-- 而成功链本来就做 creator 无关的 resource 查询，读取零额外往返。
--
-- 形状：{"requested_by": "<uuid>", "requested_at": "<iso8601>"}
-- 一次性：成功链消费后清空（SET NULL）。NULL = 无人等待。
--
-- 幂等：ADD COLUMN IF NOT EXISTS。纯加列，无回填，无数据迁移。
-- 不写 SET ROLE —— 以连接角色执行（见 CLAUDE.md 对应条目）。

ALTER TABLE public.resources
  ADD COLUMN IF NOT EXISTS summary_follow_up jsonb;

COMMENT ON COLUMN public.resources.summary_follow_up IS
  'One-shot server-side intent: summarize after the in-flight/next '
  'transcription completes. {"requested_by": uuid, "requested_at": iso8601}. '
  'Written by the transcribe trigger endpoint, consumed (read + cleared) by '
  'ai_transcription''s success chain. NULL = nobody is waiting. Replaces the '
  'browser-memory follow-up registry that a page refresh silently wiped '
  '(438, harness W2-5).';
