-- 378_inspiration_notes_timeline_anchor.sql — Beats redesign M4 (memo pins).
-- Anchors an inspiration note to a point on a script's Beats arrangement
-- timeline. Both columns NULLable: a note with a NULL anchor_script_id is a
-- plain library note (the classic mig 349 row), unchanged; a note carrying both
-- an anchor_script_id and an anchor_sec renders as a memo pin on that script's
-- timeline at that whole-second offset. No FK constraint (matching the beats
-- arrangement columns in mig 372) — a deleted script simply leaves orphan
-- anchors that the timeline query never surfaces; the note itself survives in
-- the library. The composite index serves the by-script memo-rail list query.
ALTER TABLE inspiration_notes
  ADD COLUMN IF NOT EXISTS anchor_script_id BIGINT,   -- NULL=非时间轴锚定的普通笔记
  ADD COLUMN IF NOT EXISTS anchor_sec       INTEGER;  -- 时间轴锚定的整秒偏移

CREATE INDEX IF NOT EXISTS idx_inspiration_notes_anchor
  ON inspiration_notes (anchor_script_id, anchor_sec)
  WHERE anchor_script_id IS NOT NULL;

NOTIFY pgrst, 'reload schema';
