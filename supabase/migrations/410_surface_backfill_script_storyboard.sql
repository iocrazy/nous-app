-- 410_surface_backfill_script_storyboard.sql
--
-- B4 前置(spec §5 / B3 遗留):模板早于 B1 建立,11 节点 surface 全 NULL,
-- 导致 44 个已实例化节点全按交付物型保守降级,B4 自动完成与 B5 surface 导航
-- 均不激活。本迁移按节点名回填 script/storyboard 两档。
--
-- renders 本期不映射(用户拍板 2026-08-07):判据 renders_count 数的是
-- script_shots 的 image_url/video_url(镜头出图),与成片表 generated_media
-- 口径分裂;且 Canvas (AI Generation) 节点 B6 退役。等成片链路理清再定。
--
-- 幂等:只填 NULL,不覆盖已有值;按 lower(name) 精确匹配,对全部模板生效
-- (当前生产只有一个模板,名字是模板 seeder 的固定英文名)。
-- surface 冻结语义(spec §5 ③)不受影响:这是一次性数据修复,不是运行时 join。

UPDATE public.workflow_template_nodes
   SET surface = 'script'
 WHERE surface IS NULL AND lower(name) = 'script';

UPDATE public.workflow_template_nodes
   SET surface = 'storyboard'
 WHERE surface IS NULL AND lower(name) = 'storyboard';

-- 实例节点:仅剧集绑定的(episode_id IS NOT NULL)。遗留项目级节点(NULL)
-- 保持 surface NULL = 交付物型降级(spec §5 ③),UI 本就不展示它们。
UPDATE public.project_stage_nodes
   SET surface = 'script', updated_at = now()
 WHERE surface IS NULL AND episode_id IS NOT NULL AND lower(name) = 'script';

UPDATE public.project_stage_nodes
   SET surface = 'storyboard', updated_at = now()
 WHERE surface IS NULL AND episode_id IS NOT NULL AND lower(name) = 'storyboard';
