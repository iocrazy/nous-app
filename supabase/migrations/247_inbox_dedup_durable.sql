-- Migration 247: AI-014 — make agent_inbox dedup durable across all statuses
--
-- idx_inbox_dedup (migration 159) was a partial UNIQUE index scoped to
-- WHERE status IN ('unread','reading'). Once a message moved to 'processed'
-- it fell out of the index, so a replayed outbox dispatch (e.g. a crash
-- between the inbox INSERT and the outbox mark-delivered) could re-insert the
-- same dedup_key as a NEW inbox row — the agent then processes the same
-- message twice. dedup_key = the outbox row id, which is never legitimately
-- reused, so dedup can safely be permanent.
--
-- Recreate the index without the status predicate: (recipient_agent_id,
-- dedup_key) is now unique for the row's whole lifetime, so a replayed
-- dispatch is a guaranteed no-op regardless of the original's status.
--
-- Verified on prod 2026-05-31: agent_inbox has 0 rows and 0 (recipient,
-- dedup_key) duplicates, so the rebuild is instant with no uniqueness
-- violation.

DROP INDEX IF EXISTS idx_inbox_dedup;

CREATE UNIQUE INDEX IF NOT EXISTS idx_inbox_dedup
  ON public.agent_inbox (recipient_agent_id, dedup_key)
  WHERE dedup_key IS NOT NULL;
