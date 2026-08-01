-- 397: agent_run_transcript_events — transcript stream gets its OWN table.
--
-- Why: mig 285 tried to create the transcript table as `agent_run_events`
-- with CREATE TABLE IF NOT EXISTS — but that name was already taken by the
-- mig-155 cost-audit event log (iteration / tool_name / token deltas, still
-- actively written by cost_auditor via the AgentRunEvents ORM model). The
-- IF NOT EXISTS silently no-oped, so in EVERY environment (prod and the
-- CI ephemeral schema alike) the 285 shape never materialized:
--   - RunRecorder.record_event INSERTs (run_id/seq/event_type/payload)
--     failed best-effort since the feature shipped → no transcript data,
--   - GET /ai-library/runs/{id}/events 500'd (column "seq" does not exist).
--
-- Fix: the transcript stream moves to its own table; agent_run_events stays
-- untouched as the cost-audit log. Mig 285 remains in history as a no-op.
-- DDL below mirrors 285's intent verbatim, only the name differs.

CREATE TABLE IF NOT EXISTS public.agent_run_transcript_events (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id      bigint NOT NULL REFERENCES public.agent_runs(id) ON DELETE CASCADE,
    seq         integer NOT NULL,
    event_type  text NOT NULL CHECK (
        event_type IN ('user', 'assistant', 'tool_call', 'error', 'system')
    ),
    payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_agent_run_transcript_events_run_id
    ON public.agent_run_transcript_events(run_id);

-- Payloads contain LLM conversation content — owner-scoped RLS, same
-- policy shape as 285 intended.
ALTER TABLE public.agent_run_transcript_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS agent_run_transcript_events_select_own
    ON public.agent_run_transcript_events;
CREATE POLICY agent_run_transcript_events_select_own
    ON public.agent_run_transcript_events
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM public.agent_runs r
            WHERE r.id = agent_run_transcript_events.run_id
              AND r.user_id = auth.uid()
        )
    );
