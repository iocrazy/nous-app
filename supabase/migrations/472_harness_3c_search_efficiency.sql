-- 472: harness 三期 3c —— 检索投影表 + 引用镜像表 + 效率计数列 + 三段回填
-- （spec §2.1 / §2.2 / §3.2 / §5）。四段各自幂等，整份可重跑。
--
-- 为什么投影表而不是加 tsvector：唯一的 tsvector 前例（agent_memory.search_tsv）
-- 是 'english' 配置，中文会被切碎；embedding 链当前 503。所以全走 pg_trgm。而
-- 产出正文散在 script_shots 六列 / script_ops 元素数组 / generated_media.prompt
-- 三处，只有投影表能把它们收成一个可索引的面。
-- 为什么 output_citations 是镜像：引用落在 messages.body jsonb，零索引。画布侧
-- 同类反查（canvas_asset_refs）用的就是物化镜像表，这里沿用同一形状。
BEGIN;

-- ── 1. 检索投影表 ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.search_docs (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  entity_kind TEXT NOT NULL CONSTRAINT search_docs_entity_kind_check CHECK (entity_kind IN ('run','output')),
  entity_id TEXT NOT NULL,
  kind TEXT, ref_id TEXT, version INTEGER,
  team_id BIGINT, project_id BIGINT, issue_id BIGINT, run_id BIGINT,
  owner_user_id UUID, agent_id UUID,
  title TEXT NOT NULL, body TEXT, model TEXT, status TEXT, error_code TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT search_docs_entity_key UNIQUE (entity_kind, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_search_docs_title_trgm ON public.search_docs USING gin (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_search_docs_body_trgm ON public.search_docs USING gin (body gin_trgm_ops) WHERE body IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_search_docs_team_updated ON public.search_docs (team_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_search_docs_issue ON public.search_docs (issue_id);
CREATE INDEX IF NOT EXISTS idx_search_docs_project ON public.search_docs (project_id) WHERE project_id IS NOT NULL;

ALTER TABLE public.search_docs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS search_docs_service_role_all ON public.search_docs;
CREATE POLICY search_docs_service_role_all ON public.search_docs
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- ── 2. 引用镜像表 ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.output_citations (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  kind TEXT NOT NULL, ref_id TEXT NOT NULL, version INTEGER NOT NULL,
  issue_id BIGINT, conversation_id BIGINT, message_id BIGINT NOT NULL,
  cited_by_user_id UUID NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT output_citations_message_ref_key UNIQUE (message_id, kind, ref_id, version)
);
CREATE INDEX IF NOT EXISTS idx_output_citations_ref ON public.output_citations (kind, ref_id, version);
CREATE INDEX IF NOT EXISTS idx_output_citations_issue ON public.output_citations (issue_id);

ALTER TABLE public.output_citations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS output_citations_service_role_all ON public.output_citations;
CREATE POLICY output_citations_service_role_all ON public.output_citations
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- ── 3. 效率计数列 ───────────────────────────────────────────────────
-- agent_runs 侧存量行留 NULL（UI 显示 '—'）：0 说「一次工具都没调」，NULL 说
-- 「那时还没在数」—— 两件不同的事，不许合并。
ALTER TABLE public.agent_runs
  ADD COLUMN IF NOT EXISTS steps INTEGER,
  ADD COLUMN IF NOT EXISTS tool_calls INTEGER,
  ADD COLUMN IF NOT EXISTS tool_errors INTEGER,
  ADD COLUMN IF NOT EXISTS deliverables INTEGER,
  ADD COLUMN IF NOT EXISTS turn_end_reason TEXT;
CREATE INDEX IF NOT EXISTS idx_agent_runs_user_created ON public.agent_runs (user_id, created_at DESC);

-- ai_usage_hourly 侧相反：这五列是 upsert 的**加法累加器**（col + EXCLUDED.col），
-- 可空会让一次加法把整行算成 NULL，于是一整个小时桶的计数静默变成未知。
ALTER TABLE public.ai_usage_hourly
  ADD COLUMN IF NOT EXISTS run_count INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS failed_runs INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS tool_calls INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS tool_errors INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS deliverables INTEGER NOT NULL DEFAULT 0;

-- ── 4. 回填 ─────────────────────────────────────────────────────────
-- 顺序是契约的一部分：team_id 必须先补，否则下面投影出来的 run 行带着 NULL
-- team_id 落库，而 ON CONFLICT DO NOTHING 让它们再也修不回来。
UPDATE public.agent_runs a
SET team_id = i.team_id,
    project_id = COALESCE(a.project_id, i.project_id)
FROM public.issues i
WHERE a.issue_id = i.id AND a.team_id IS NULL AND a.ended_at IS NOT NULL;

-- run 行：title = "<issue_key> · <issue_title>"，无议题退到 input_summary 前 80
-- 字，再无则字面量 'run'（title 是 NOT NULL，COALESCE 链必须以常量收尾）。
-- body = output_summary。刻意不用 input_summary：三个写方语义各异，列表投影本来
-- 就把它排除了（agent_runs_repository.py:367-370）。
INSERT INTO public.search_docs (
  entity_kind, entity_id, title, body, team_id, project_id, issue_id, run_id,
  owner_user_id, agent_id, model, status, error_code, created_at, updated_at)
SELECT 'run', r.id::text,
       COALESCE(NULLIF(i.identifier || ' · ' || i.title, ''),
                NULLIF(LEFT(r.input_summary, 80), ''), 'run'),
       r.output_summary, r.team_id, r.project_id, r.issue_id, r.id,
       r.user_id, r.agent_id, r.model, r.status, r.error_code,
       r.created_at, COALESCE(r.ended_at, r.created_at)
FROM public.agent_runs r
LEFT JOIN public.issues i ON i.id = r.issue_id
ON CONFLICT (entity_kind, entity_id) DO NOTHING;

-- output 标题行：存量产出只有 ≤120 字的标签，正文要等 Part C 的三个生产者在登记
-- 时交（向前的，不回补）。body 留 NULL —— NULL 说「没交过正文」，空串会说
-- 「正文是空的」。
INSERT INTO public.search_docs (
  entity_kind, entity_id, kind, ref_id, version, title, team_id, project_id,
  issue_id, run_id, owner_user_id, agent_id, model, created_at, updated_at)
SELECT 'output', d.kind || ':' || d.ref_id || ':' || d.version::text,
       d.kind, d.ref_id, d.version,
       COALESCE(NULLIF(d.title, ''), d.kind || ' ' || d.ref_id),
       r.team_id, r.project_id, r.issue_id, d.run_id,
       COALESCE(r.user_id, d.actor_user_id), r.agent_id, d.model,
       d.created_at, d.created_at
FROM public.run_deliverables d
LEFT JOIN public.agent_runs r ON r.id = d.run_id
ON CONFLICT (entity_kind, entity_id) DO NOTHING;

COMMIT;
NOTIFY pgrst, 'reload schema';
