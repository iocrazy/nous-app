-- Spec-2 slice 2b: add a 'needs_followup' issue status, distinct from 'blocked'.
--   needs_followup = the agent stalled and needs a human decision/information
--                    (a deliberate hand-off — not a failure).
--   blocked        = execution errored / a real obstacle.
-- Previously the agent's FinishIssue outcome=needs_input mapped to 'blocked',
-- conflating "needs a human" with "errored". This separates them.
--
-- The issues.status CHECK was created inline in mig 166 (anonymous name). Find
-- it by its definition (mentions 'in_review') and replace it — robust against
-- constraint-name drift. Status transitions are enforced at the app layer; this
-- only widens the allowed set, so it is additive and safe.

DO $$
DECLARE cname text;
BEGIN
    SELECT conname INTO cname
    FROM pg_constraint
    WHERE conrelid = 'public.issues'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) LIKE '%in_review%';
    IF cname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE public.issues DROP CONSTRAINT %I', cname);
    END IF;
END $$;

ALTER TABLE public.issues
    ADD CONSTRAINT issues_status_check CHECK (status IN (
        'backlog', 'todo', 'in_progress', 'in_review',
        'blocked', 'needs_followup', 'done', 'cancelled'
    ));
