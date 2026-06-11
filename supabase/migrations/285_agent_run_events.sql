-- 285: agent_run_events — per-run transcript event stream (paperclip port P3).
--
-- Paperclip stores a per-run event log (heartbeat_run_events: run_id + seq +
-- event_type + payload) that powers the Runs detail "Transcript" section
-- (Nice/Raw toggle). MediaHub's equivalent: RunRecorder.record_event appends
-- rows here as AgentRunner executes (user message → tool calls → assistant
-- output), and GET /ai-library/runs/{id}/events serves them to the detail
-- pane. Append-only; payload values truncated at write time (~4k chars per
-- field) so a huge tool result can't bloat the table.

CREATE TABLE IF NOT EXISTS public.agent_run_events (
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

CREATE INDEX IF NOT EXISTS idx_agent_run_events_run_id
    ON public.agent_run_events(run_id);

-- Reads go through the backend endpoint (admin client + explicit ownership
-- check), but enable owner-scoped RLS anyway — the payloads contain LLM
-- conversation content and must never be world-readable via PostgREST.
ALTER TABLE public.agent_run_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS agent_run_events_select_own ON public.agent_run_events;
CREATE POLICY agent_run_events_select_own ON public.agent_run_events
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM public.agent_runs r
            WHERE r.id = agent_run_events.run_id
              AND r.user_id = auth.uid()
        )
    );

NOTIFY pgrst, 'reload schema';
