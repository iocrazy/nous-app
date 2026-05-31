-- Migration 246: AI-011 — FK agent_memories.user_id → auth.users ON DELETE CASCADE
--
-- agent_memories.user_id was a plain UUID with no FK (migration 156). When a
-- user is deleted, their memories were left orphaned (user_id pointing at a
-- now-gone auth.users row) instead of being cleaned up. Add the missing FK with
-- ON DELETE CASCADE so memories die with their owner.
--
-- user_id is nullable (system/shared memories have NULL) — the FK permits NULL,
-- so those rows are unaffected.
--
-- Verified on prod 2026-05-31: agent_memories has 0 rows and 0 orphans, so the
-- ADD CONSTRAINT validates instantly with no table scan / lock concern.

ALTER TABLE public.agent_memories
  ADD CONSTRAINT agent_memories_user_id_fkey
  FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;
