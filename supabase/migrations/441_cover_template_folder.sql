-- 441_cover_template_folder.sql
--
-- 封面模板库改为「素材库里的一个系统文件夹」，不再是独立的表。
--
-- 为什么推翻 435
-- ==============
-- 435 把模板做成 `cover_templates` 表（锚在 generated_media）。上线后用户第一反应是
-- "模板库在哪里？"——他已经在「我的上传」里自己建了一个「封面」文件夹放样图，而
-- 工作室里上传的图落在 generated_media，素材库里看不见。两套账，用户找不到、管不了。
-- 用户的心智模型是对的：模板就是素材库里能管理的文件，一处管理、两边可见。
-- 435 的表线上 0 行，直接替换，不做数据搬迁。
--
-- ★ 系统文件夹靠 system_key 标识，不靠名字
-- ====================================
-- `folders.is_system` 字段早就有，但此前**零处读写**（生产 0 行 true）——它只是个
-- 声明，没有任何保护被执行。名字不能当身份：用户可能叫它「封面」「Covers」「封面
-- 模板」，将来还可能改名/本地化。`system_key` 是稳定身份（'cover_templates'），
-- `is_system` 保留为"受保护"的开关，两者一起置位。
-- 每个 scope 只能有一个活着的同 key 文件夹（回收站里的不占坑）。
--
-- ★ 保护是 API 层执行的，不在 DB
-- =============================
-- 改名 / 移动 / 回收站 / 删除 四个端点对 is_system=true 的文件夹回类型化 409
-- （code=system_folder）。DB 层不加 trigger：文件夹内容（往里放图、从里面移走）
-- 必须自由，只有文件夹本身的存在与身份受保护。
--
-- ★ 用量记录按 resource 记
-- =======================
-- 「用过 N 次」用于排序。以前锚在 generated_media；现在模板 = resources 行，
-- 参考 URL 在选中时按需 import（对象存储内容寻址，同一张图反复 import 不占空间，
-- 但会产生新的 generated_media 行——所以 gen id 不能当稳定键，resource id 才能）。
-- 素材被删/移出文件夹后这行就成孤儿，无害：列表是按文件夹内容 JOIN 出来的，
-- 孤儿行不会出现在界面上。

ALTER TABLE public.folders ADD COLUMN IF NOT EXISTS system_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS ux_folders_scope_system_key
    ON public.folders (scope_id, system_key)
    WHERE system_key IS NOT NULL AND is_trashed = false;

COMMENT ON COLUMN public.folders.system_key IS
    '系统文件夹的稳定身份（如 cover_templates），与名字无关；is_system 是受保护开关。'
    '保护由 API 层执行（改名/移动/回收站/删除 → 409 system_folder）。';

DROP TABLE IF EXISTS public.cover_templates;

CREATE TABLE IF NOT EXISTS public.cover_template_usage (
    scope_id     BIGINT      NOT NULL,
    resource_id  BIGINT      NOT NULL,
    usage_count  INTEGER     NOT NULL DEFAULT 0,
    last_used_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (scope_id, resource_id)
);

ALTER TABLE public.cover_template_usage ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS cover_template_usage_service_role_all ON public.cover_template_usage;
CREATE POLICY cover_template_usage_service_role_all ON public.cover_template_usage
    FOR ALL TO service_role USING (true) WITH CHECK (true);

COMMENT ON TABLE public.cover_template_usage IS
    '封面模板「用过 N 次」，按 (scope, resource) 记，仅用于排序。模板的归属是系统文件夹'
    '（folders.system_key = cover_templates），不是这张表。';
