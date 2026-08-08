-- 412_retire_canvas_stage_node.sql
--
-- B6(用户拍板 2026-08-08):退役流程条上的「Canvas (AI Generation)」独立阶段
-- 节点。画布功能/数据(canvases 表、AI 生成)完全不动——画布自 B5 起是
-- Storyboard 节点的三视图之一(spec §2:Storyboard/Canvas/Shot List 是同一个
-- 分镜节点的三种视图,不是三个节点),独立阶段节点是 B1 前旧模板把视图误当
-- 阶段的遗留,surface NULL 永远按交付物型降级,是流程条上的重复死节点。
--
-- ① node bank 摘除:phase=NULL 而非 DELETE——project_stage_history.stage_id
--   是 NOT NULL 无 ON DELETE 的 FK(mig 295),硬删遇历史行会 RESTRICT;
--   phase=NULL 让 seeder(list_stage_library 只取 phase IS NOT NULL)与
--   picker 立即失效,后续 CI 重放时本迁移编号大于 381,顺序压过其
--   ON CONFLICT DO UPDATE 回填。
-- ② 模板/实例行删除:按 source_stage_id/legacy_stage_id 关联(无 FK,靠
--   381 种子的 slug='canvas' 行取 id),members/deps 双端 CASCADE 自动清;
--   episodes.current_node_id 是 ON DELETE SET NULL(mig 402);
--   projects.current_node_id 无 FK(mig 380 遗留),删除会留悬空 id——生产已
--   验证(2026-08-08)无任何 projects.current_node_id 指向 Canvas 节点(4 个
--   实例 id 逐一比对为 0),此为防御性说明。
-- ③ 镜像 issue 不级联(origin_id 字符串关联,生产已验证 0 行,此为防御):
--   删除前按 origin_id 关闭非终态镜像。
-- 幂等:重跑时子查询空集,各语句 no-op。

BEGIN;

WITH canvas_stage AS (
    SELECT id FROM public.project_stages WHERE slug = 'canvas'
),
gone_nodes AS (
    SELECT n.project_id, n.id
    FROM public.project_stage_nodes n
    WHERE n.legacy_stage_id IN (SELECT id FROM canvas_stage)
)
UPDATE public.issues i
   SET status = 'cancelled'
 WHERE i.origin_kind = 'project_stage'
   AND i.status NOT IN ('done', 'cancelled')
   AND i.origin_id IN (
       SELECT 'project_stage:' || g.project_id || ':' || g.id FROM gone_nodes g
   );

DELETE FROM public.project_stage_nodes
 WHERE legacy_stage_id IN (SELECT id FROM public.project_stages WHERE slug = 'canvas');

DELETE FROM public.workflow_template_nodes
 WHERE source_stage_id IN (SELECT id FROM public.project_stages WHERE slug = 'canvas');

UPDATE public.project_stages SET phase = NULL WHERE slug = 'canvas';

COMMIT;
