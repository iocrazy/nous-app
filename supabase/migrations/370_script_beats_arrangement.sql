-- 370_script_beats_arrangement.sql — Beats redesign M1 (data layer).
-- Adds the timeline-arrangement columns to script_beats so a beat can carry a
-- start offset, a duration, a methodology-template role key, and a card color
-- strip. All NULLable: a NULL start_sec means "not yet arranged" (the classic
-- list-mode beat, backwards-compatible with mig 350 rows), NULL duration means
-- "no duration set", NULL beat_role means a free-form (non-template) beat, and
-- NULL color renders with the neutral card chrome. Same shape as 350; the
-- Arrangement timeline view + template library land in M2/M3.
ALTER TABLE script_beats
  ADD COLUMN IF NOT EXISTS start_sec    INTEGER,      -- NULL=未编排（旧数据兼容）
  ADD COLUMN IF NOT EXISTS duration_sec INTEGER,      -- NULL=未设时长
  ADD COLUMN IF NOT EXISTS beat_role    VARCHAR(40),  -- 模板角色 key（save_the_cat.catalyst 等，自由节拍 NULL）
  ADD COLUMN IF NOT EXISTS color        VARCHAR(20);  -- 卡色条

NOTIFY pgrst, 'reload schema';
