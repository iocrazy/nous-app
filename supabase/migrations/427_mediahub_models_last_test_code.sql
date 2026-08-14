-- Migration 427: 模型自检失败原因分类 —— mediahub_models.last_test_code
--
-- 背景（承接 #1838）
-- ==================
-- #1838 把模型健康状态透到了用户界面，但用户只看得到"失败了"，看不到"为什么"。
-- 2026-08-14 那轮真实探针里，两个红灯对用户意味着**相反的动作**：
--   nous-qwen3-llm            ReadTimeout           → 多半不用管（本机引擎在加载）
--   mediahub-doubao-...-pro   HTTP 429 SetLimit...  → 要去处理（配额/换模型）
-- 而 UI 对两者说的是同一句话。
--
-- 为什么不直接透出 last_test_detail
-- =================================
-- 原文常带上游 host、私有 base_url、上游内部模型 ID（同一轮里
-- `UnsupportedProtocol: URL missing 'ht...'` 就含 URL 片段），而
-- GET /api/v1/ai/mediahub-models 是面向全体用户的接口。所以这里存的是
-- **封闭枚举**：它由异常类型和 HTTP status 推导，不读任何消息文本，
-- 因此天生不可能携带秘密。detail 原文仍然只留给 admin。
--
-- 可空 = 未知
--   旧行、以及本迁移之后第一轮探针跑完之前的行，都是 NULL。NULL 表示"没这个信息"，
--   前端回退到不带原因的通用文案 —— 不是"原因未知"这种伪信息。
--   探针每次都写这一列（成功写 NULL），所以修好的模型不会留着上次失败的 code。
--
-- CHECK 与代码侧的 PROBE_FAILURE_CODES 是一对孪生保证
--   代码侧：backend/app/services/ai/mediahub_model_health.py::PROBE_FAILURE_CODES
--   两边必须同时改。CHECK 的代价是加新 code 必须配一条迁移 —— 这正是想要的：
--   往一个用户可见的封闭枚举里加值，本就该是一次显式的、可 review 的改动。
--   （last_test_status 的 CHECK 是同样的取舍，已经这么活了。）
--
-- Idempotent: ADD COLUMN IF NOT EXISTS + 先 DROP 再建 CHECK；重复执行无副作用。

BEGIN;

ALTER TABLE public.mediahub_models
  ADD COLUMN IF NOT EXISTS last_test_code TEXT;

ALTER TABLE public.mediahub_models
  DROP CONSTRAINT IF EXISTS mediahub_models_last_test_code_check;

ALTER TABLE public.mediahub_models
  ADD CONSTRAINT mediahub_models_last_test_code_check
  CHECK (
    last_test_code IS NULL
    OR last_test_code IN (
      'timeout',
      'unreachable',
      'auth',
      'rate_limit',
      'model_not_found',
      'upstream_error',
      'bad_response',
      'other'
    )
  );

COMMENT ON COLUMN public.mediahub_models.last_test_code IS
  'Closed-enum classification of the last FAILED connectivity probe (NULL when the probe passed or never ran). Derived only from the exception type and HTTP status — never from message text — so it is safe to expose on the public model list, unlike last_test_detail which embeds upstream hosts and private base_urls. Code-side twin: PROBE_FAILURE_CODES in backend/app/services/ai/mediahub_model_health.py.';

NOTIFY pgrst, 'reload schema';

COMMIT;
