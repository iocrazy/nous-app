-- Spec-2 slice 2a: platform toggle for whether an agent's FinishIssue
-- 'completed' declaration auto-closes the issue (status=done) vs. parking it at
-- in_review for a human to confirm. Default OFF — agents do not self-close until
-- an admin explicitly trusts them to.
--
-- Stored as a jsonb STRING via to_jsonb(...::text) like the graph_* keys so
-- asyncpg returns a clean Python string. Settable via the generic admin
-- settings PATCH (the row must pre-exist — repo.update has no upsert).
-- SECURITY: system_settings is admin/service-role-only (no anon surface).

INSERT INTO system_settings (key, value, description)
VALUES
  ('issue_agent_auto_close', to_jsonb('false'::text),
   'When true, an agent FinishIssue outcome=completed closes the issue (done) '
   'instead of parking it at in_review for human review.')
ON CONFLICT (key) DO NOTHING;
