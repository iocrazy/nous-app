-- 376_script_projects_target_duration.sql — Beats redesign M3 (target length).
-- A script can carry an OPTIONAL target total runtime in seconds. The Beats
-- Arrangement view uses it to size the timeline ruler (max of target / furthest
-- beat / 60s) and as the default total when applying a methodology template
-- (save-the-cat / five-beats / kishotenketsu percentage anchors resolve against
-- it). NULL = "not set" (the topbar shows "Set length"); the ruler then falls
-- back to the furthest arranged beat. INTEGER matches the PG range the schema
-- boundary already guards (0 .. 2^31-1).
ALTER TABLE script_projects
  ADD COLUMN IF NOT EXISTS target_duration_sec INTEGER;  -- NULL=未设目标总时长

NOTIFY pgrst, 'reload schema';
