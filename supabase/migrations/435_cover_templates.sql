-- 435_cover_templates.sql
--
-- 封面工作室的「样图模板库」。一个模板 = 一张你觉得好看、想让模型照着画的图，
-- 连同一个你给它起的名字。它**不是** prompt，不替代风格（风格是文字，存在 skills 里）。
--
-- ⚠️ 为什么不复用 `style_templates`
-- ================================
-- 名字对得上，东西完全不是。`style_templates` 只有 `prompt_content`（纯文字），
-- 没有任何图片列，线上 0 行、从未被读写过。它的来历是：mig 113 建了它，mig 114
-- 把它 `RENAME TO skills`，但 113 是 `CREATE TABLE IF NOT EXISTS` 且在改名后被
-- 重放过，于是原样复活了一个空壳，从此与 skills 并存（schema_baseline.sql:7476
-- 与 :7331）。后端至今留着与之配套、但**零生产调用方**的 model/repository/schema，
-- router 是个 301 跳转桩。它是 skills 的前身残骸，不是模板库。
--
-- ★ 为什么锚在 generated_media 而不是 resources
-- ============================================
-- 模板存在的**唯一**用途是作为参考图交给出图模型。而出图链路的参考图入口
-- (`canvas_generation.py` 的 `params.source_urls`) 只认这一种 URL：
--
--     /api/v1/generated-media/{id}/(cover|stream|file)
--
-- 不匹配的 URL 会被 `generated_media_local_path()` 返回 None 然后**静默丢弃**
-- —— 除 codex 会记一条 warning 外没有任何信号。锚在 `resources` 上的后果不是
-- 报错，是"模板选了、生成了、图里没有它"，而且查不出为什么。
--
-- 锚在 generated_media 另外白拿三件事：
--   1. `/generated-media/{id}/cover` **无鉴权**，模板缩略图可以裸 <img>，不用带 token；
--   2. 对象存储是内容寻址的，同一张图反复存不额外占空间；
--   3. 「从素材库选」已有现成服务端入口 `POST /generated-media/import-from-resource`，
--      「直接上传」已有 `POST /generated-media/import`，两条来路都不用新写落盘逻辑。
--
-- `source_resource_id` 只做溯源，不参与任何取图路径 —— 素材可以被用户删掉/移走，
-- 而模板不该跟着消失。
--
-- ⚠️ ON DELETE RESTRICT 是刻意的，两个替代方案都更糟
-- ================================================
-- CASCADE：用户在画布里删掉一张图，模板库里对应的模板**无声消失**。模板是用户
--          攒出来的资产，不该被另一个界面的清理动作顺手带走。
-- 不加 FK：模板指向一个不存在的行，界面上是一张永远加载失败的破图。
-- RESTRICT：删除被引用的图会被数据库拒绝 —— 前提是**调用方把它翻译成人话**。
--          `DELETE /generated-media/{id}` 必须捕获 FK 冲突并回一个类型化 409
--          （"这张图被 N 个封面模板引用"），而不是把 IntegrityError 漏成 500。
--          裸 500 等于把"有引用"伪装成"服务坏了"，那就退回到前两个方案的水平了。
--
-- ⚠️ 删除是硬删除，不是软删除（这一条曾经写反过）
-- ==============================================
-- 软删除 + 上面那条 RESTRICT 外键会合成一个具体的 bug：归档行**仍然握着外键**，
-- 于是用户几个月后想删掉那张图时会被拒绝，理由里还点名一个他明明已经删掉、
-- 界面上再也看不见的模板。一个看不见的东西挡住一个看得见的操作，是最难自查的
-- 那类故障。
-- 软删除通常换来的两样东西这里都不成立：① 没有任何表引用 cover_templates.id
-- （生成记录里存的是 generated_media 的 URL，不是模板 id），所以硬删不会让任何
-- 记录指向虚空；② usage_count 也保不住 —— 删掉再重新添加同一张图本来就是新的
-- 一行、从 0 开始计数。既然两样都拿不到，就不该留下一个不可见状态。

-- RLS：service_role only，与兄弟表 generated_media（mig 307）同口径。前端一律走
-- 后端 router，PostgREST 不暴露此表。

CREATE TABLE IF NOT EXISTS public.cover_templates (
    id                 BIGINT      PRIMARY KEY DEFAULT generate_snowflake_id(),
    -- teams.id（个人团队）。与 generated_media.scope_id 同义、同样不加 FK。
    scope_id           BIGINT      NOT NULL,
    creator_id         UUID        NOT NULL,
    -- 用户给的名字，就是设计稿卡片下方那行字（"Bold headline"）。
    name               TEXT        NOT NULL,
    -- 取图的唯一来源，见文件头。
    generated_media_id BIGINT      NOT NULL
        REFERENCES public.generated_media(id) ON DELETE RESTRICT,
    -- 这个模板是怎么进来的。三条路各自的语义不同，合并成一个 bool 会丢信息：
    --   'upload'    用户直接传的文件
    --   'library'   从素材库挑的（此时 source_resource_id 有值）
    --   'generated' 从封面工作室的成品「Save as template」存下来的
    source_kind        TEXT        NOT NULL
        CHECK (source_kind IN ('upload', 'library', 'generated')),
    -- 仅溯源。素材被删/移走不影响模板可用性，所以不加 FK、也允许指向已消失的行。
    source_resource_id BIGINT,
    -- 设计稿卡片上的 "used 12×"。用得多的模板排前面，这是模板库唯一的排序依据
    -- —— 按创建时间排会让第一次攒的模板永远沉底。
    usage_count        INTEGER     NOT NULL DEFAULT 0,
    -- ⚠️ 与 usage_count 分开，不是冗余。计数回答"这个模板好不好用"，
    -- 时间回答"我最近在用哪一批" —— 一个用了 30 次但半年没碰的模板，
    -- 和一个昨天刚用过 2 次的模板，在界面上应该是不同的东西。
    last_used_at       TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 列表查询就这一种形状：某个 scope 下的模板，按用得多的排前面。
CREATE INDEX IF NOT EXISTS idx_cover_templates_scope_usage
    ON public.cover_templates (scope_id, usage_count DESC, created_at DESC);

-- 同一张图在同一个 scope 下只当一个模板。重复添加应该是幂等的（返回已有那行），
-- 而不是攒出两张一模一样、只有名字不同的卡片 —— 参照 `link-existing` 的先例。
CREATE UNIQUE INDEX IF NOT EXISTS ux_cover_templates_scope_media
    ON public.cover_templates (scope_id, generated_media_id);

-- 溯源反查（"我素材库这张图被做成模板了吗"）。
CREATE INDEX IF NOT EXISTS idx_cover_templates_source_resource
    ON public.cover_templates (source_resource_id)
    WHERE source_resource_id IS NOT NULL;

ALTER TABLE public.cover_templates ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS cover_templates_service_role_all ON public.cover_templates;
CREATE POLICY cover_templates_service_role_all ON public.cover_templates
    FOR ALL TO service_role USING (true) WITH CHECK (true);

COMMENT ON TABLE public.cover_templates IS
    '封面工作室的样图模板库：一张参考图 + 一个名字。锚在 generated_media 上，'
    '因为出图链路的 source_urls 只认 /api/v1/generated-media/{id}/... 这一种 URL。'
    '与 style_templates（skills 的空壳前身，纯文字 prompt）无关。';

COMMENT ON COLUMN public.cover_templates.generated_media_id IS
    '唯一取图来源。ON DELETE RESTRICT：删除被引用的图必须由 API 层翻译成类型化 409，'
    '不能漏成 500。';

COMMENT ON COLUMN public.cover_templates.source_resource_id IS
    '仅溯源，不参与取图。素材被删/移走不该让模板消失，所以刻意不加 FK。';
