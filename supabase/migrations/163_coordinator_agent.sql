-- 163: Add `coordinator` persistent agent — pure-routing M3 dispatcher.
--
-- The coordinator never produces content; it only delegates to other
-- persistent specialists (summarize / analyze / etc.) via the
-- `Delegate` tool. It exists primarily to validate that prompt-driven
-- cross-agent dispatch works end-to-end (G milestone): a real LLM call
-- decides on its own to call Delegate, the tool runs, the target's
-- worker picks up the inbox, the response routes back via outbox
-- dispatcher.
--
-- The seed loader fills `identity_md` / `soul_md` / `agent_md` on
-- startup from `backend/seeds/agents/coordinator/`. This migration only
-- guarantees the row exists with `persistent=true` and the right model
-- on a fresh DB before the seed loader runs.
--
-- ai_agents has no unique constraint on slug, so this uses a manual
-- "exists or insert + conditional update" pattern that's idempotent
-- across re-runs.

DO $$
DECLARE
    existing_id uuid;
BEGIN
    SELECT id INTO existing_id FROM ai_agents WHERE slug = 'coordinator' LIMIT 1;

    IF existing_id IS NULL THEN
        INSERT INTO ai_agents (slug, name, model, persistent, is_system_preset)
        VALUES (
            'coordinator',
            'Coordinator',
            'doubao-seed-2-0-pro-260215',
            true,
            true
        );
    ELSE
        UPDATE ai_agents
        SET persistent = true,
            is_system_preset = true,
            model = COALESCE(model, 'doubao-seed-2-0-pro-260215')
        WHERE id = existing_id;
    END IF;
END $$;
