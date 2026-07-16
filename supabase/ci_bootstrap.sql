-- ============================================================================
-- ci_bootstrap.sql — runtime-owned objects that no business migration creates
-- ============================================================================
--
-- WHAT THIS IS
-- ------------
-- supabase/migrations/** assumes a set of objects already exists, because in a
-- real deployment they are created by things that are NOT our migrations:
--
--   * Supabase Auth (gotrue)      → the `auth` schema, auth.users, auth.uid()/role()/jwt()
--   * Supabase Storage (storage-api) → the `storage` schema, storage.buckets/objects
--   * The DBOS python engine       → the `dbos` schema, dbos.workflow_status
--   * The Postgres image / platform → roles (anon/authenticated/service_role) + extensions
--
-- None of those are business schema, so none of them live in a migration. This
-- file provides the minimum stub of each so that schema_baseline.sql and any
-- post-watermark migration can apply against a stock Postgres in CI.
--
-- THESE ARE STUBS, NOT THE REAL THING. They exist only to satisfy DDL
-- dependencies (FK targets, function references, `TO <role>` in RLS policies).
-- The schema-drift test only reflects the `public` schema, so the stubs are
-- never compared against anything.
--
-- WHY EACH ENTRY IS HERE (measured against the committed SQL, 2026-07-15):
--   auth.users            — 70 FK references from public tables
--   auth.uid/role/jwt     — 348 / 42 / 6 references inside RLS policies
--   anon/authenticated/service_role — named in `CREATE POLICY ... TO <role>`
--   pgcrypto              — gen_random_uuid() (34 uses), digest() (2 uses)
--   pg_trgm               — gin_trgm_ops index opclass (5 uses)
--   vector                — pgvector columns (6 uses)
--   storage.* / dbos.*    — not needed by the current baseline, but migrations
--                           have historically referenced them (e.g. 359 creates
--                           a storage bucket), so future post-watermark
--                           migrations can rely on them being present.
--
-- Applied with ON_ERROR_STOP: if any statement here fails, CI goes RED.
-- ============================================================================

-- ── 1. Extensions ───────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

-- ── 2. Platform roles ───────────────────────────────────────────────────────
-- Supplied natively by supabase/postgres; created here so the gate also runs on
-- a stock Postgres image. Idempotent (mirrors the guard style of migration 165).
DO $$
DECLARE
    r text;
BEGIN
    FOREACH r IN ARRAY ARRAY[
        'anon', 'authenticated', 'service_role', 'authenticator',
        'mediahub_app', 'mediahub_dbos'
    ] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN NOINHERIT', r);
        END IF;
    END LOOP;
END
$$;

-- ── 3. auth schema (owned by Supabase Auth / gotrue at runtime) ─────────────
CREATE SCHEMA IF NOT EXISTS auth;

-- Only `id` matters: it is the FK target for 70 public-schema columns. The
-- remaining columns mirror the real gotrue shape closely enough that a
-- post-watermark migration touching auth.users would not silently no-op.
CREATE TABLE IF NOT EXISTS auth.users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email character varying(255),
    raw_user_meta_data jsonb,
    created_at timestamptz DEFAULT now()
);

-- RLS helper functions. The bodies mirror gotrue's real implementations (read
-- the JWT claims out of the request GUCs); in CI no JWT is ever set, so they
-- return NULL. Only their existence and signature matter for DDL to apply.
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$ SELECT nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;

CREATE OR REPLACE FUNCTION auth.role() RETURNS text
    LANGUAGE sql STABLE
    AS $$ SELECT nullif(current_setting('request.jwt.claim.role', true), '')::text $$;

CREATE OR REPLACE FUNCTION auth.jwt() RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$ SELECT coalesce(nullif(current_setting('request.jwt.claim', true), ''), '{}')::jsonb $$;

-- ── 4. Realtime publication (created by the supabase-realtime container) ────
-- 73 migrations run `ALTER PUBLICATION supabase_realtime ADD TABLE ...`.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
        CREATE PUBLICATION supabase_realtime;
    END IF;
END
$$;

-- ── 5. storage schema (owned by the supabase storage-api service) ───────────
CREATE SCHEMA IF NOT EXISTS storage;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'storage' AND t.typname = 'buckettype'
    ) THEN
        CREATE TYPE storage.buckettype AS ENUM ('STANDARD', 'ANALYTICS', 'VECTOR');
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS storage.buckets (
    id text PRIMARY KEY,
    name text NOT NULL,
    owner uuid,
    public boolean DEFAULT false,
    file_size_limit bigint,
    allowed_mime_types text[],
    type storage.buckettype DEFAULT 'STANDARD'::storage.buckettype NOT NULL,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS storage.objects (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bucket_id text,
    name text,
    owner uuid,
    metadata jsonb,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

-- ── 6. dbos schema (created at runtime by the DBOS python engine) ───────────
-- The engine owns this table; migrations only attach triggers / read from it.
-- Column set mirrors the live engine table so a post-watermark migration that
-- references a column finds it.
CREATE SCHEMA IF NOT EXISTS dbos;

CREATE TABLE IF NOT EXISTS dbos.workflow_status (
    workflow_uuid text PRIMARY KEY,
    status text,
    name text,
    authenticated_user text,
    assumed_role text,
    authenticated_roles text,
    request text,
    output text,
    error text,
    executor_id text,
    created_at bigint DEFAULT ((EXTRACT(epoch FROM now()) * 1000.0))::bigint NOT NULL,
    updated_at bigint DEFAULT ((EXTRACT(epoch FROM now()) * 1000.0))::bigint NOT NULL,
    application_version text,
    application_id text,
    class_name character varying(255),
    config_name character varying(255),
    recovery_attempts bigint DEFAULT 0,
    queue_name text,
    workflow_timeout_ms bigint,
    workflow_deadline_epoch_ms bigint,
    inputs text,
    started_at_epoch_ms bigint,
    deduplication_id text,
    priority integer DEFAULT 0 NOT NULL,
    queue_partition_key text,
    forked_from text,
    owner_xid text,
    parent_workflow_id text,
    serialization text,
    delay_until_epoch_ms bigint,
    was_forked_from boolean
);
