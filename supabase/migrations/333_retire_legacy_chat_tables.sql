-- 333_retire_legacy_chat_tables.sql — Phase 3 (Wave 2): retire legacy
-- team-chat + 1:1 ai-chat tables now superseded by conversations/messages
-- (mig 320-332). All live traffic was repointed onto conversations/
-- conversation_members/messages in Wave 0 + Wave 1 of this epic.
--
-- USER-CONFIRMED DATA DROP (2026-07-04): legacy team-chat + 1:1 ai chat
-- history is disposable per user decision.
--
-- SCOPE — 6 tables dropped (the plan's original skeleton named 7; see the
-- ai_session_memory carve-out below, discovered by checking the LIVE schema
-- + code before writing this file, per this task's own instructions):
--   channels, channel_members, channel_messages, agent_channels,
--   ai_sessions, ai_messages
--
-- ai_session_memory IS DELIBERATELY EXCLUDED. Migration 332
-- (332_direct_agent_sidecars.sql) already repurposed it: it dropped
-- ai_session_memory_session_id_fkey specifically so the same BIGINT
-- session_id column can hold either a legacy ai_sessions.id (old rows) or a
-- conversations.id (new-store rows going forward) — see 332's own header
-- comment. backend/app/repositories/session_memory_repository.py +
-- app/agent_framework/session_memory.py + session_memory_runner.py +
-- ai_library_chat_service.py actively read/write this table TODAY as the
-- agent working-memory store. Dropping it here would break that live
-- feature — it is not part of the legacy chat/history being retired.

-- ── FK unhooks BEFORE drops ──────────────────────────────────────────────
-- Both target ai_sessions (which we ARE dropping). Both surviving columns
-- (agent_runs.session_id, issues.ai_session_id) are kept as plain BIGINT —
-- historical linkage data, just no longer FK-enforced. Names confirmed
-- against mig 231 (231_ai_sessions_snowflake.sql:233/242) and
-- backend/app/models/agents.py::AgentRuns / models/reviews.py::Issues.
-- Table-existence guarded (to_regclass) — mirrors mig 231's own defensive
-- style around `issues`; some environments' local schemas lag prod.
DO $$
BEGIN
  IF to_regclass('public.agent_runs') IS NOT NULL THEN
    ALTER TABLE public.agent_runs DROP CONSTRAINT IF EXISTS agent_runs_session_id_fkey;
  END IF;
  IF to_regclass('public.issues') IS NOT NULL THEN
    ALTER TABLE public.issues DROP CONSTRAINT IF EXISTS issues_ai_session_id_fkey;
  END IF;
END $$;

-- Belt-and-braces: enumerate + drop ANY remaining FK from a SURVIVING table
-- pointing at one of the 6 doomed tables that the two explicit drops above
-- didn't already name. Defensive only — expected to be a no-op given the
-- explicit drops above plus the FKs already retired by earlier migrations
-- (231 renamed/recreated ai_sessions FKs; 332 dropped ai_session_memory's).
DO $$
DECLARE
  doomed text[] := ARRAY['channels', 'channel_members', 'channel_messages',
                          'agent_channels', 'ai_sessions', 'ai_messages'];
  rec RECORD;
BEGIN
  FOR rec IN
    SELECT DISTINCT con.conname, src.relname AS source_table
      FROM pg_constraint con
      JOIN pg_class src ON src.oid = con.conrelid
      JOIN pg_namespace src_ns ON src_ns.oid = src.relnamespace
      JOIN pg_class tgt ON tgt.oid = con.confrelid
      JOIN pg_namespace tgt_ns ON tgt_ns.oid = tgt.relnamespace
     WHERE con.contype = 'f'
       AND tgt_ns.nspname = 'public'
       AND tgt.relname = ANY (doomed)
       AND src_ns.nspname = 'public'
       AND src.relname <> ALL (doomed)
  LOOP
    EXECUTE format(
      'ALTER TABLE public.%I DROP CONSTRAINT IF EXISTS %I',
      rec.source_table, rec.conname
    );
    RAISE NOTICE 'mig333: dropped FK % on % (belt-and-braces sweep)',
      rec.conname, rec.source_table;
  END LOOP;
END $$;

-- ── Publication trap (bug #733) ─────────────────────────────────────────
-- "ALTER PUBLICATION ... DROP TABLE IF EXISTS" is INVALID SQL — DROP TABLE
-- has no IF EXISTS clause in this context. Guard with pg_publication_tables
-- instead (same pattern mig 329 used for ADD TABLE).
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_publication_tables
     WHERE pubname = 'supabase_realtime' AND schemaname = 'public'
       AND tablename = 'channel_messages'
  ) THEN
    ALTER PUBLICATION supabase_realtime DROP TABLE public.channel_messages;
    RAISE NOTICE 'mig333: channel_messages removed from supabase_realtime.';
  END IF;

  IF EXISTS (
    SELECT 1 FROM pg_publication_tables
     WHERE pubname = 'supabase_realtime' AND schemaname = 'public'
       AND tablename = 'channel_members'
  ) THEN
    ALTER PUBLICATION supabase_realtime DROP TABLE public.channel_members;
    RAISE NOTICE 'mig333: channel_members removed from supabase_realtime.';
  END IF;
END $$;

-- ── Orphaned cross-table RLS policy on a SURVIVING table ────────────────
-- ai_session_memory.own_session_memory_readable (mig 187) subqueries
-- ai_sessions in its USING clause — a real pg_depend edge even though it's
-- not an FK, which blocks dropping ai_sessions. This policy is already dead
-- in practice: nothing in frontend/ queries ai_session_memory directly
-- (grep-verified), and backend access goes through
-- session_memory_repository.py on the service_role connection, which
-- bypasses RLS (see the FOR-ALL session_memory_write_service_only policy
-- alongside it). It was also already semantically stale post-mig-332 —
-- session_id can hold a conversations.id for new-store rows, which this
-- policy's ai_sessions join can never match. Dropped, not replaced (out of
-- scope for a table-retirement migration; service_role is the only live
-- access path).
DROP POLICY IF EXISTS own_session_memory_readable ON public.ai_session_memory;

-- ── Drop the 6 tables (children before parents) ─────────────────────────
-- Dropping channels/channel_members/channel_messages/agent_channels also
-- drops their RLS SELECT policies (channels_select, channel_members_select,
-- channel_messages_select, agent_channels_select), which is what frees the
-- two RLS helper functions below — DROP FUNCTION must come AFTER these.
DROP TABLE IF EXISTS public.agent_channels;
DROP TABLE IF EXISTS public.channel_messages;
DROP TABLE IF EXISTS public.channel_members;
DROP TABLE IF EXISTS public.channels;
DROP TABLE IF EXISTS public.ai_messages;
DROP TABLE IF EXISTS public.ai_sessions;

-- ── Orphaned RLS helper functions (mig 321) ─────────────────────────────
-- Only ever referenced by the RLS policies just dropped above (channels /
-- channel_members / channel_messages / agent_channels select policies).
-- Drop the now-dead helper functions too so nothing lingers pointing at a
-- retired table shape.
DROP FUNCTION IF EXISTS public.is_channel_member(uuid, bigint);
DROP FUNCTION IF EXISTS public.channel_member_joined_at(uuid, bigint);

-- ── Stale broadcast watermarks ───────────────────────────────────────────
-- Key format is `broadcast_watermark_channel_{id}` both before AND after
-- the Wave-0/Task-3 repoint (chat_broadcast_repository.py) — {id} now holds
-- a conversations.id instead of a channels.id, but the key STRING is
-- indistinguishable between the two eras. We must NOT blanket-delete by
-- LIKE pattern (that would also nuke live watermarks for real conversations
-- created since the repoint). Instead: anti-join on conversations.id.
-- Snowflake ids are minted from one global sequence (globally unique, never
-- per-table), so a key's numeric suffix can only match a live conversations
-- row if it was written post-repoint for a real conversation; any key whose
-- suffix does NOT resolve to a conversations row is unambiguously a stale
-- pre-repoint (legacy channels.id) watermark.
DELETE FROM public.system_settings ss
 WHERE ss.key LIKE 'broadcast_watermark_channel_%'
   AND NOT EXISTS (
     SELECT 1 FROM public.conversations c
      WHERE c.id::text = substring(ss.key FROM 'broadcast_watermark_channel_(\d+)$')
   );

NOTIFY pgrst, 'reload schema';
