-- Migration 428: 探针诚实声明能力边界 —— last_test_status 新增 'not_probed'
--
-- 缺陷（生产实证 2026-08-14）
-- ==========================
-- 探针只对 asr / embedding 分派专用协议，其余一切都 POST 到 {base_url}/chat/completions。
-- image / video / tts 因此**永远探不通**，与模型本身能不能用完全无关：
--   文生图模型打 chat 端点 → HTTP 404 InvalidEndpointOrModel
--   CLI 类模型（jimeng-cli-*）base_url 为空 → UnsupportedProtocol: URL missing 'ht...'
-- 当时 4 个红灯里 3 个是这种构造性假红（三个模型实际都是好的），
-- 而且每小时白打 3 次注定失败的请求 + 刷 3 条 WARNING。
--
-- 为什么不给它们写真探针
-- ======================
-- 文生图/视频的真实调用**要花钱、要生成产物**，而这是每模型每小时一次的轮询；
-- CLI 类模型压根没有 HTTP 端点可探。所以正确答案不是"探得更好"，
-- 而是让探针说实话：能验的验，不能验的说"我验不了"。
--
-- 三值语义
-- ========
--   ok         探通了
--   fail       探了，失败了（last_test_code 说明原因）
--   not_probed 没探 —— 本探针不具备验证该类型的能力。**不是故障，也不是"未知"**。
--              last_test_detail 写 'no protocol probe for type=<t>'（不含 host /
--              base_url / 凭据，可安全展示）；last_test_code 保持 NULL ——
--              那一列枚举的是"失败原因"，而这里没有失败。
-- 未探过的行仍然是 NULL（"从没跑过"），与 not_probed（"跑了，但验不了"）不同。
--
-- CHECK 与代码侧的 PROBE_STATUSES / PROBEABLE_TYPES 是一对孪生保证
--   代码侧：backend/app/services/ai/mediahub_model_health.py
--   加值必须配迁移 —— 与 427 的 last_test_code 同一取舍：往一个跨端读取的封闭枚举
--   里加值，本就该是一次显式的、可 review 的改动。
--
-- 为什么要回填而不是等下一轮探针
-- ==============================
-- 不回填的话，admin 页面上那 3 个假红要挂到下一个整点才自愈；而"发完版去看一眼"
-- 恰恰是最可能发生的验收动作，看到的会是还没修好的旧状态。
--
-- Idempotent: 先 DROP 再建 CHECK；UPDATE 带 last_test_status = 'fail' 条件，
-- 第二次执行匹配不到行。重复执行无副作用。

BEGIN;

ALTER TABLE public.mediahub_models
  DROP CONSTRAINT IF EXISTS mediahub_models_last_test_status_check;

ALTER TABLE public.mediahub_models
  ADD CONSTRAINT mediahub_models_last_test_status_check
  CHECK (
    last_test_status IS NULL
    OR last_test_status IN ('ok', 'fail', 'not_probed')
  );

-- 回填。谓词写成 PROBEABLE_TYPES 的补集（而不是列举 image/video/tts），
-- 与探针里 `typ not in PROBEABLE_TYPES` 是同一个判据 —— 将来 type CHECK
-- 再放宽（345 就放宽过一次）也不会漏掉新类型。
UPDATE public.mediahub_models
SET last_test_status = 'not_probed',
    last_test_detail = 'no protocol probe for type=' || type,
    last_test_code = NULL
WHERE last_test_status = 'fail'
  AND type NOT IN ('llm', 'embedding', 'asr');

COMMENT ON COLUMN public.mediahub_models.last_test_status IS
  'Last connectivity-probe outcome: ok = reachable; fail = probed and failed (see last_test_code); not_probed = the probe has no protocol for this model type (image/video/tts) and checked nothing — NOT a fault; NULL = never probed. Code-side twin: PROBE_STATUSES / PROBEABLE_TYPES in backend/app/services/ai/mediahub_model_health.py.';

NOTIFY pgrst, 'reload schema';

COMMIT;
