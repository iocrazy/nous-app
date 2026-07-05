-- 340_backfill_episodes.sql — 存量项目回填 Ep 1 并挂接 script_projects（spec v3 §2.1 / audit #23）
INSERT INTO episodes (project_id, title, sort_order)
SELECT DISTINCT sp.project_id, 'Ep 1', 0
FROM script_projects sp
WHERE sp.project_id IS NOT NULL AND sp.episode_id IS NULL
  AND NOT EXISTS (SELECT 1 FROM episodes e WHERE e.project_id = sp.project_id);

UPDATE script_projects sp
SET episode_id = e.id
FROM episodes e
WHERE sp.episode_id IS NULL AND sp.project_id = e.project_id;
NOTIFY pgrst, 'reload schema';
