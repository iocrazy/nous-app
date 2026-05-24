-- 222_reseed_issue_sequence_global.sql
--
-- Repair: prod public.issue_sequence was EMPTY — the scope='global' counter
-- row that migration 166 seeds (INSERT ... ON CONFLICT DO NOTHING) never
-- persisted on prod (166 partially applied / table created via a different
-- path). The create-issue proc does:
--     UPDATE public.issue_sequence SET counter = counter + 1 WHERE scope='global' RETURNING ...
-- which matched no row -> RAISE 'issue_sequence row missing for scope=global'
-- -> POST /api/v1/issues/ returned 400 (verified on prod 2026-05-24).
--
-- This reseeds the row idempotently so the counter always exists. counter=0
-- means the next allocated identifier is MH-1. Safe to run repeatedly; safe
-- on environments that already have the row (ON CONFLICT DO NOTHING).

INSERT INTO public.issue_sequence (scope, prefix, counter)
VALUES ('global', 'MH', 0)
ON CONFLICT (scope) DO NOTHING;
