-- 233_issue_messages_snowflake.sql
-- Convert issue_messages.id from UUID to BIGINT Snowflake.
-- Simplest of the 3 PR-B ID migrations: zero FK dependents (confirmed on prod 2026-05-28).
--
-- Defensive IF EXISTS guard allows this migration to run safely even when
-- the local dev DB is behind on migrations (issue_messages may not exist yet).
-- In production the table must exist.
--
-- Read-only window estimate: ~10-20 s depending on row count.
--
-- See docs/superpowers/specs/2026-05-28-id-unification-design.md § 4.

BEGIN;

DO $main$
BEGIN

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'issue_messages'
    ) THEN
        RAISE NOTICE 'issue_messages table not found — skipping mig 233 (local dev DB is behind)';
        -- Still emit NOTIFY so PostgREST doesn't stall on a cache miss.
        PERFORM pg_notify('pgrst', 'reload schema');
        RETURN;
    END IF;

    -- ========================================================================
    -- SECTION 1: Add new BIGINT id column
    -- ========================================================================

    ALTER TABLE public.issue_messages ADD COLUMN new_id BIGINT;
    UPDATE public.issue_messages SET new_id = generate_snowflake_id() WHERE new_id IS NULL;
    ALTER TABLE public.issue_messages ALTER COLUMN new_id SET NOT NULL;

    -- ========================================================================
    -- SECTION 2: Drop old PK and swap columns
    -- ========================================================================

    ALTER TABLE public.issue_messages DROP CONSTRAINT IF EXISTS issue_messages_pkey;
    ALTER TABLE public.issue_messages DROP COLUMN id;
    ALTER TABLE public.issue_messages RENAME COLUMN new_id TO id;
    ALTER TABLE public.issue_messages ADD PRIMARY KEY (id);
    ALTER TABLE public.issue_messages ALTER COLUMN id SET DEFAULT generate_snowflake_id();

    -- ========================================================================
    -- SECTION 3: PostgREST reload
    -- ========================================================================

    PERFORM pg_notify('pgrst', 'reload schema');

END
$main$;

COMMIT;
