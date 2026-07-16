-- ============================================================================
-- schema_baseline.sql — PROD's ACTUAL applied `public` schema, as a snapshot
-- ============================================================================
--
--   BASELINE WATERMARK: 364
--   Generated:          2026-07-15
--   Source:             prod (mediahub-sb-prod-db), PostgreSQL 17.6
--   Contents:           public schema only, 155 tables
--                       (--schema-only --no-owner --no-privileges;
--                        _scratch_dbos / _scratch_test excluded)
--
-- WHAT THE WATERMARK MEANS
-- ------------------------
-- Every migration file numbered <= 364 is ALREADY REFLECTED in this snapshot.
-- CI applies this baseline and then ONLY migrations numbered > 364.
--
-- Read that precisely: the watermark does NOT claim each file <= 364 applied
-- cleanly. It claims this is prod's real state after whatever actually ran.
-- Those are different, and the difference is the whole reason this file exists
-- (see "why a baseline" below). Example: `176_drop_project_tasks.sql` never
-- succeeded in prod — `project_tasks` is still there, so it is still in here.
--
-- WHY A BASELINE INSTEAD OF REPLAYING THE MIGRATIONS
-- --------------------------------------------------
-- Because replaying them does not reconstruct prod, and cannot. Measured
-- 2026-07-15 by replaying all 371 forward migrations in file-number order onto
-- a scratch database:
--
--   bare postgres                      →  86/371 applied
--   + a supabase-shaped bootstrap      → 342/371 applied
--   + storage/dbos schema stubs        → ~346/371 applied
--
-- The residue is not fixable with a better bootstrap — the file numbering is
-- not the order in which these migrations actually reached prod:
--
--   * 069_project_folders declares `project_id uuid REFERENCES projects(id)`,
--     but by file order mig 051 has already turned projects.id into bigint. The
--     FK is impossible. Prod nonetheless has project_folders.project_id = int8,
--     and no later migration converts it → 069 reached prod BEFORE 051.
--   * 095 uses `CREATE POLICY IF NOT EXISTS` and 210 uses
--     `ALTER PUBLICATION ... DROP TABLE IF EXISTS`. Neither is valid Postgres
--     syntax in any version — they have never applied anywhere, as committed.
--   * 180_task_tracking_rename is wrapped in BEGIN/COMMIT and fails on
--     publication membership, so a replay ends with `unified_tasks` and NO
--     `task_tracking` — the exact opposite of prod, where task_tracking is the
--     single source of truth for the whole task system.
--
-- A gate built on a replay would therefore compare the ORM models against a
-- schema that is materially NOT prod. That is worse than no gate: it would be
-- confidently wrong. Hence: snapshot what prod really is, and replay only
-- what comes after.
--
-- HOW TO REGENERATE
-- -----------------
-- See docs/runbook/schema-baseline-refresh.md for the full procedure and for
-- when a refresh is warranted. The dump itself is read-only against prod:
--
--   ssh -i ~/.ssh/nas_deploy_key -p 1122 heygo@192.168.50.9 \
--     "sudo /usr/local/bin/docker exec mediahub-sb-prod-db \
--        pg_dump -U supabase_admin -d postgres --schema-only --no-owner \
--        --no-privileges -n public -T 'public._scratch_dbos' -T 'public._scratch_test'"
--
-- Then update the WATERMARK + Generated date above, and re-scan the output for
-- secrets before committing (the runbook lists the exact greps).
--
-- DO NOT hand-edit the SQL below — it is generated. Edit prod via a migration,
-- then regenerate.
--
-- TWO MECHANICAL NORMALIZATIONS are applied to the raw pg_dump output (the
-- runbook's regenerate script does both; nothing else is touched):
--   1. `\restrict` / `\unrestrict` psql meta-commands are stripped — they are
--      client-side guards, not schema, and they break older psql clients.
--   2. `CREATE SCHEMA public;` → `CREATE SCHEMA IF NOT EXISTS public;` — every
--      fresh database already has a public schema, so the raw line aborts the
--      apply.
-- ============================================================================

--
-- PostgreSQL database dump
--


-- Dumped from database version 17.6
-- Dumped by pg_dump version 17.6

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS public;


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'standard public schema';


--
-- Name: ai_task_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.ai_task_status AS ENUM (
    'none',
    'pending',
    'processing',
    'completed',
    'failed',
    'skipped'
);


--
-- Name: api_key_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.api_key_status AS ENUM (
    'active',
    'revoked',
    'expired'
);


--
-- Name: aweme_type_enum; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.aweme_type_enum AS ENUM (
    '0',
    '2',
    '4',
    '61',
    '68',
    '150',
    '157'
);


--
-- Name: download_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.download_status AS ENUM (
    'pending',
    'downloading',
    'completed',
    'failed',
    'skipped'
);


--
-- Name: user_role; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.user_role AS ENUM (
    'admin',
    'user',
    'test'
);


--
-- Name: add_owner_as_member(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.add_owner_as_member() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_catalog'
    AS $$
BEGIN
  INSERT INTO team_members (team_id, user_id, role)
  VALUES (NEW.id, NEW.owner_id, 'owner');
  RETURN NEW;
END;
$$;


--
-- Name: advisory_unlock(bigint); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.advisory_unlock(lock_key bigint) RETURNS boolean
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $$
  SELECT pg_advisory_unlock(lock_key);
$$;


--
-- Name: agent_runs_default_last_useful_action(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.agent_runs_default_last_useful_action() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  IF NEW.last_useful_action_at IS NULL THEN
    NEW.last_useful_action_at := COALESCE(NEW.started_at, now());
  END IF;
  RETURN NEW;
END;
$$;


--
-- Name: agent_runs_track_liveness_change(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.agent_runs_track_liveness_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  IF NEW.liveness_state IS DISTINCT FROM OLD.liveness_state THEN
    NEW.liveness_changed_at := now();
  END IF;
  RETURN NEW;
END;
$$;


--
-- Name: bump_agent_tasks_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.bump_agent_tasks_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;


--
-- Name: can_read_script_op(bigint); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.can_read_script_op(p_scene_id bigint) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
  SELECT EXISTS (
    SELECT 1
    FROM script_scenes s
    JOIN script_projects sp ON sp.id = s.script_id
    JOIN team_members tm ON tm.team_id = sp.team_id
    WHERE s.id = p_scene_id
      AND tm.user_id = auth.uid()
  );
$$;


--
-- Name: canvases_touch_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.canvases_touch_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;


--
-- Name: cascade_cancel_run(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cascade_cancel_run() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  -- Recursion guard: only the outermost call should fan out.
  IF pg_trigger_depth() > 1 THEN
    RETURN NEW;
  END IF;

  IF NEW.cancel_requested = true AND OLD.cancel_requested = false THEN
    UPDATE agent_runs
       SET cancel_requested = true
     WHERE root_run_id = COALESCE(NEW.root_run_id, NEW.id)
       AND status = 'running'
       AND id != NEW.id;
  END IF;
  RETURN NEW;
END;
$$;


--
-- Name: FUNCTION cascade_cancel_run(); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.cascade_cancel_run() IS 'Cancels child runs in a delegation tree when the root cancel_requested flips. Guarded by pg_trigger_depth() to prevent N-deep recursive fan-out (TODO-AI-013).';


--
-- Name: check_orphan_resource(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.check_orphan_resource() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM resource_items WHERE resource_id = OLD.resource_id
  ) THEN
    UPDATE resources
    SET is_trashed = true,
        trashed_at = NOW()
    WHERE id = OLD.resource_id
      AND is_trashed = false;

    RAISE NOTICE 'Resource % marked as trashed (no remaining items)', OLD.resource_id;
  END IF;

  RETURN OLD;
END;
$$;


--
-- Name: classify_workflow_health(text, timestamp with time zone, timestamp with time zone, integer, timestamp with time zone, text, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.classify_workflow_health(p_phase text, p_started_at timestamp with time zone, p_heartbeat_at timestamp with time zone, p_progress integer, p_progress_changed_at timestamp with time zone, p_task_type text, p_user_max_minutes integer) RETURNS text
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE
    policy RECORD;
    elapsed_seconds INT;
    heartbeat_age_seconds INT;
    progress_age_seconds INT;
    user_hard_seconds INT;
BEGIN
    -- PENDING (i.e., not yet running) — only ORPHAN matters.
    IF p_phase = 'queued' THEN
        SELECT * INTO policy FROM public.workflow_timeout_policy WHERE task_type = p_task_type;
        IF NOT FOUND THEN
            RETURN 'HEALTHY';  -- unknown type, give it benefit of doubt
        END IF;
        elapsed_seconds := EXTRACT(EPOCH FROM (now() - COALESCE(p_started_at, now())))::INT;
        IF elapsed_seconds > policy.hard_ceiling_seconds * 3 THEN
            RETURN 'ORPHAN_PENDING';
        END IF;
        RETURN 'HEALTHY';
    END IF;

    -- RUNNING checks: need a started_at to reason about elapsed time.
    IF p_started_at IS NULL THEN
        RETURN 'HEALTHY';
    END IF;

    SELECT * INTO policy FROM public.workflow_timeout_policy WHERE task_type = p_task_type;
    IF NOT FOUND THEN
        RETURN 'HEALTHY';
    END IF;

    elapsed_seconds := EXTRACT(EPOCH FROM (now() - p_started_at))::INT;
    heartbeat_age_seconds := CASE
        WHEN p_heartbeat_at IS NULL THEN elapsed_seconds  -- never wrote one
        ELSE EXTRACT(EPOCH FROM (now() - p_heartbeat_at))::INT
    END;
    progress_age_seconds := CASE
        WHEN p_progress_changed_at IS NULL THEN elapsed_seconds
        ELSE EXTRACT(EPOCH FROM (now() - p_progress_changed_at))::INT
    END;

    -- LOST: heartbeat is stale beyond policy → worker died.
    -- This is the ONLY auto-action state (besides ORPHAN_PENDING) so the
    -- threshold needs to be conservative.
    IF heartbeat_age_seconds > policy.heartbeat_stale_seconds THEN
        RETURN 'LOST';
    END IF;

    -- User-defined hard cap — if set and exceeded, mark as overage so
    -- caller can flip to timed_out (user opted into auto-cancel by
    -- setting max_duration_minutes).
    user_hard_seconds := CASE
        WHEN p_user_max_minutes IS NOT NULL THEN p_user_max_minutes * 60
        ELSE NULL
    END;
    IF user_hard_seconds IS NOT NULL AND elapsed_seconds > user_hard_seconds THEN
        RETURN 'USER_TIMEOUT';
    END IF;

    -- Below user cap: classify between healthy / slow / stalled / stuck.
    IF elapsed_seconds <= policy.expected_duration_seconds THEN
        IF progress_age_seconds > policy.expected_duration_seconds / 2 THEN
            RETURN 'STUCK_IN_STEP';
        END IF;
        RETURN 'HEALTHY';
    END IF;

    -- Past expected but heartbeat is fresh.
    IF progress_age_seconds > (policy.expected_duration_seconds / 2) THEN
        RETURN 'STALLED';
    END IF;
    RETURN 'SLOW';
END;
$$;


--
-- Name: FUNCTION classify_workflow_health(p_phase text, p_started_at timestamp with time zone, p_heartbeat_at timestamp with time zone, p_progress integer, p_progress_changed_at timestamp with time zone, p_task_type text, p_user_max_minutes integer); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.classify_workflow_health(p_phase text, p_started_at timestamp with time zone, p_heartbeat_at timestamp with time zone, p_progress integer, p_progress_changed_at timestamp with time zone, p_task_type text, p_user_max_minutes integer) IS 'Pure-SQL classifier used by the workflow health sweeper. Returns
     HEALTHY / SLOW / STUCK_IN_STEP / STALLED / LOST / ORPHAN_PENDING /
     USER_TIMEOUT given the row plus policy. No side effects — caller
     decides whether to act on the classification.';


--
-- Name: cleanup_old_alert_history(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cleanup_old_alert_history(retention_days integer DEFAULT 30) RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER
    AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM alert_history
    WHERE created_at < NOW() - (retention_days || ' days')::INTERVAL;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$;


--
-- Name: conversation_member_joined_at(uuid, bigint); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.conversation_member_joined_at(p_user uuid, p_conversation bigint) RETURNS timestamp with time zone
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
  SELECT joined_at FROM public.conversation_members
  WHERE conversation_id = p_conversation AND member_type='user' AND user_id = p_user;
$$;


--
-- Name: count_scope_resources(text, boolean); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.count_scope_resources(p_scope_id text, p_web boolean) RETURNS bigint
    LANGUAGE sql STABLE
    SET search_path TO 'public'
    AS $$
  SELECT count(*) FROM (
    SELECT DISTINCT ri.resource_id
    FROM public.resource_items ri
    JOIN public.resources r ON r.id = ri.resource_id
    WHERE ri.scope_id = p_scope_id::bigint
      AND r.is_trashed = false
      AND (CASE WHEN p_web THEN r.source_type = 'web'
                ELSE r.source_type IS DISTINCT FROM 'web' END)
    LIMIT 100001
  ) capped;
$$;


--
-- Name: dbos_error_to_text(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.dbos_error_to_text(err text) RETURNS text
    LANGUAGE plpgsql IMMUTABLE
    AS $_$
DECLARE
  is_pickle BOOLEAN;
  chunks TEXT[];
  c TEXT;
  classname TEXT := NULL;
  message TEXT := NULL;
BEGIN
  IF err IS NULL OR length(err) = 0 THEN
    RETURN NULL;
  END IF;

  is_pickle := length(err) >= 24
               AND substring(err, 1, 3) = ANY (ARRAY['gAS', 'gAU', 'gAQ', 'gAV']);

  IF NOT is_pickle THEN
    RETURN substring(err, 1, 500);
  END IF;

  BEGIN
    chunks := string_to_array(
      regexp_replace(
        regexp_replace(
          encode(decode(err, 'base64'), 'escape'),
          '\\(?:[0-9]{3}|.)', chr(1), 'g'
        ),
        '[^[:print:]' || chr(1) || ']', chr(1), 'g'
      ),
      chr(1)
    );

    -- Pass 1: classname = first CamelCase ending in Error/Exception (len >= 5).
    FOREACH c IN ARRAY chunks LOOP
      c := trim(c);
      IF c ~ '^[A-Z][A-Za-z0-9_]*(Error|Exception)$' AND length(c) >= 5 THEN
        classname := c;
        EXIT;
      END IF;
    END LOOP;

    -- Pass 2: message = longest chunk that looks like human text.
    -- mig 298: reject a dotted module path (word.word.word…) even with a
    -- leading SHORT_BINUNICODE length byte ('2app.services…'), so it can't
    -- masquerade as the message and beat the real reason on length.
    FOREACH c IN ARRAY chunks LOOP
      c := trim(c);
      IF length(c) >= 8
         AND c <> classname
         AND (position(' ' IN c) > 0 OR length(c) >= 20)
         AND c !~ '^[a-z_][a-z0-9_]*$'
         AND c !~ '^.?[a-z_][a-z0-9_]*([.][a-z_][a-z0-9_]*)+$'
         AND c NOT IN ('builtins', 'dbos._error') THEN
        IF message IS NULL OR length(c) > length(message) THEN
          message := c;
        END IF;
      END IF;
    END LOOP;

    -- Strip a surviving pickle SHORT_BINUNICODE length-byte prefix.
    IF message IS NOT NULL AND length(message) >= 2
       AND length(message) - 1 = ascii(message) THEN
      message := substring(message, 2);
    END IF;
  EXCEPTION WHEN OTHERS THEN
    classname := NULL;
    message := NULL;
  END;

  IF classname IS NOT NULL AND message IS NOT NULL THEN
    RETURN substring(classname || ': ' || message, 1, 500);
  ELSIF classname IS NOT NULL THEN
    RETURN classname || ' (open detail for context)';
  END IF;

  RETURN 'Workflow failed — open detail to see the exception.';
END;
$_$;


--
-- Name: FUNCTION dbos_error_to_text(err text); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.dbos_error_to_text(err text) IS 'Friendly-stringify a DBOS workflow_status.error value for the
   task_tracking.error_msg cache. Attempts to lift exception class
   name + message out of the pickled Python exception via printable-
   substring extraction (see migration 219 for details). Falls back
   to a hint pointing the user at the detail endpoint if extraction
   yields nothing recognisable. Plain-text errors pass through
   truncated to 500 chars.';


--
-- Name: delete_team_with_cleanup(bigint); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.delete_team_with_cleanup(target_team_id bigint) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_catalog'
    AS $$
DECLARE
  team_owner_id UUID;
  caller_id UUID;
  tid TEXT;
BEGIN
  caller_id := auth.uid();
  IF caller_id IS NULL THEN
    RAISE EXCEPTION 'Not authenticated';
  END IF;

  SELECT owner_id INTO team_owner_id
    FROM teams WHERE id = target_team_id;

  IF team_owner_id IS NULL THEN
    RAISE EXCEPTION 'Team not found';
  END IF;

  IF team_owner_id != caller_id THEN
    RAISE EXCEPTION 'Only the team owner can delete a team';
  END IF;

  tid := target_team_id::TEXT;

  DELETE FROM resources
    WHERE id IN (
      SELECT resource_id FROM resource_items
      WHERE scope_type = 'team' AND scope_id = tid
    );

  DELETE FROM resource_tags
    WHERE tag_id IN (
      SELECT id FROM tags WHERE scope_type = 'team' AND scope_id = tid
    );

  DELETE FROM tags              WHERE scope_type = 'team' AND scope_id = tid;
  DELETE FROM smart_collections WHERE scope_type = 'team' AND scope_id = tid;
  DELETE FROM resource_items    WHERE scope_type = 'team' AND scope_id = tid;
  DELETE FROM folders           WHERE scope_type = 'team' AND scope_id = tid;
  DELETE FROM libraries         WHERE scope_type = 'team' AND scope_id = tid;

  DELETE FROM projects WHERE team_id = target_team_id;
  DELETE FROM collections WHERE team_id = target_team_id;

  DELETE FROM teams WHERE id = target_team_id;
END;
$$;


--
-- Name: emit_agent_run_dispatch_message(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.emit_agent_run_dispatch_message() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
BEGIN
  IF NEW.issue_id IS NULL THEN
    RETURN NEW;
  END IF;

  -- Idempotent: skip if a row for this run already exists (e.g. some
  -- code path inserted the chat row directly).
  IF EXISTS (
    SELECT 1 FROM public.issue_messages
    WHERE agent_run_id = NEW.id AND kind = 'agent_run'
  ) THEN
    RETURN NEW;
  END IF;

  INSERT INTO public.issue_messages (
    issue_id, kind, author_agent_id, agent_run_id,
    body, meta
  ) VALUES (
    NEW.issue_id, 'agent_run', NEW.agent_id, NEW.id,
    'Agent picking up…',
    jsonb_build_object(
      'status', NEW.status,
      'liveness_state', NEW.liveness_state,
      'model', NEW.model,
      'trigger', NEW.trigger
    )
  );

  RETURN NEW;
END;
$$;


--
-- Name: FUNCTION emit_agent_run_dispatch_message(); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.emit_agent_run_dispatch_message() IS 'AFTER INSERT trigger on agent_runs. When issue_id is set, emit a
   "Agent picking up…" placeholder chat row immediately so the user
   sees feedback before the agent runtime finishes. The terminal-state
   trigger (emit_agent_run_message_on_resolve) updates this same row
   in place when the run resolves.';


--
-- Name: emit_agent_run_message_on_resolve(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.emit_agent_run_message_on_resolve() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
DECLARE
  duration_s   INT;
  body_text    TEXT;
  existing_id  UUID;
  meta_obj     JSONB;
BEGIN
  IF NEW.status NOT IN ('completed', 'failed', 'cancelled') THEN
    RETURN NEW;
  END IF;
  IF OLD.status = NEW.status THEN
    RETURN NEW;
  END IF;
  IF NEW.issue_id IS NULL THEN
    RETURN NEW;
  END IF;

  duration_s := CASE
    WHEN NEW.ended_at IS NOT NULL AND NEW.started_at IS NOT NULL
    THEN GREATEST(0, EXTRACT(EPOCH FROM (NEW.ended_at - NEW.started_at))::INT)
    ELSE NULL
  END;

  body_text := COALESCE(
    NEW.output_summary,
    CASE NEW.status
      WHEN 'completed' THEN 'Run completed.'
      WHEN 'failed'    THEN COALESCE(NEW.error_message, 'Run failed.')
      WHEN 'cancelled' THEN 'Run cancelled.'
      ELSE NULL
    END
  );

  meta_obj := jsonb_build_object(
    'status', NEW.status,
    'liveness_state', NEW.liveness_state,
    'cost_cents', NEW.cost_cents,
    'model', NEW.model,
    'prompt_tokens', NEW.prompt_tokens,
    'completion_tokens', NEW.completion_tokens,
    'error_code', NEW.error_code,
    'continuation_attempt', NEW.continuation_attempt
  );

  -- Try UPDATE first (dispatch trigger placed a placeholder row).
  SELECT id INTO existing_id
  FROM public.issue_messages
  WHERE agent_run_id = NEW.id AND kind = 'agent_run'
  LIMIT 1;

  IF existing_id IS NOT NULL THEN
    UPDATE public.issue_messages
       SET body = body_text,
           duration_seconds = duration_s,
           meta = meta_obj
     WHERE id = existing_id;
  ELSE
    INSERT INTO public.issue_messages (
      issue_id, kind, author_agent_id, agent_run_id,
      body, duration_seconds, meta
    ) VALUES (
      NEW.issue_id, 'agent_run', NEW.agent_id, NEW.id,
      body_text, duration_s, meta_obj
    );
  END IF;

  RETURN NEW;
END;
$$;


--
-- Name: FUNCTION emit_agent_run_message_on_resolve(); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.emit_agent_run_message_on_resolve() IS 'Bridge from agent_runs lifecycle to issue_messages chat thread.
   Updated by mig 208: now UPDATEs the placeholder row inserted by
   trg_agent_runs_emit_dispatch when the run resolves, falling back to
   INSERT only when no placeholder exists. One run = one chat row that
   evolves through the run''s lifecycle, instead of two separate rows.';


--
-- Name: emit_issue_status_change_message(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.emit_issue_status_change_message() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
BEGIN
  IF NEW.status IS DISTINCT FROM OLD.status THEN
    INSERT INTO public.issue_messages
      (issue_id, kind, author_user_id, from_status, to_status, meta)
    VALUES
      (NEW.id, 'system_status',
       -- Best-effort author: prefer the user who triggered the update.
       -- auth.uid() returns NULL when the update came via service_role
       -- (e.g., agent worker); leave author_user_id NULL in that case.
       auth.uid(),
       OLD.status, NEW.status,
       jsonb_build_object('source', 'trigger'));
  END IF;
  RETURN NEW;
END;
$$;


--
-- Name: FUNCTION emit_issue_status_change_message(); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.emit_issue_status_change_message() IS 'AFTER UPDATE OF status trigger on issues. Inserts a system_status
   row into issue_messages so the chat thread shows the status
   transition inline. SECURITY DEFINER because the trigger may fire
   under a role that lacks INSERT on issue_messages directly.';


--
-- Name: enforce_personal_team_singleton(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enforce_personal_team_singleton() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.teams t
         WHERE t.id = NEW.team_id AND t.kind = 'personal'
    ) THEN
        IF (SELECT COUNT(*) FROM public.team_members
              WHERE team_id = NEW.team_id) >= 1 THEN
            RAISE EXCEPTION
              'personal team % cannot have more than one member', NEW.team_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: find_duplicate_videos(uuid, double precision, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.find_duplicate_videos(p_user_id uuid, similarity_threshold double precision DEFAULT 0.85, max_results integer DEFAULT 20) RETURNS TABLE(media_id bigint, title text, cover_urls jsonb, author character varying, storage_size bigint, created_at timestamp with time zone, last_viewed_at timestamp with time zone, view_count integer, similar_to bigint, similarity_score double precision)
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'public', 'pg_catalog'
    AS $$
BEGIN
    RETURN QUERY
    WITH user_media AS (
        SELECT pm.id, ra.content_embedding
        FROM resources r
        JOIN parsed_media pm ON pm.id = r.media_id
        JOIN resource_analysis ra ON ra.resource_id = r.id
        WHERE r.creator_id = p_user_id
        AND r.is_trashed = false
        AND ra.content_embedding IS NOT NULL
        AND pm.keep_forever = false
    ),
    similarity_pairs AS (
        SELECT
            um1.id as mid1,
            um2.id as mid2,
            (1 - (um1.content_embedding <=> um2.content_embedding))::float as sim_score
        FROM user_media um1
        JOIN user_media um2 ON um2.id > um1.id
        WHERE (1 - (um1.content_embedding <=> um2.content_embedding)) >= similarity_threshold
    )
    SELECT DISTINCT ON (sp.mid2)
        sp.mid2 as media_id,
        pm.title,
        pm.cover_urls,
        pm.author,
        pm.storage_size,
        pm.created_at,
        pm.last_viewed_at,
        pm.view_count,
        sp.mid1 as similar_to,
        sp.sim_score as similarity_score
    FROM similarity_pairs sp
    JOIN parsed_media pm ON pm.id = sp.mid2
    ORDER BY sp.mid2, sp.sim_score DESC
    LIMIT max_results;
END;
$$;


--
-- Name: generate_invite_code(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.generate_invite_code() RETURNS text
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $$
DECLARE
  chars TEXT := 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  result TEXT := '';
  i INT;
BEGIN
  FOR i IN 1..8 LOOP
    result := result || substr(chars, floor(random() * length(chars) + 1)::int, 1);
  END LOOP;
  RETURN result;
END;
$$;


--
-- Name: generate_snowflake_id(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.generate_snowflake_id() RETURNS bigint
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $$
DECLARE
  epoch BIGINT := 1704067200000;
  now_ms BIGINT;
  seq INT;
  result BIGINT;
BEGIN
  now_ms := (EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT - epoch;
  seq := nextval('snowflake_seq') % 4096;
  result := (now_ms << 12) | seq;
  RETURN result;
END;
$$;


--
-- Name: generate_team_invite_code(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.generate_team_invite_code() RETURNS text
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $$
DECLARE
  chars TEXT := 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789';
  result TEXT := '';
  i INT;
BEGIN
  FOR i IN 1..12 LOOP
    result := result || substr(chars, floor(random() * length(chars) + 1)::int, 1);
  END LOOP;
  RETURN result;
END;
$$;


--
-- Name: get_cleanup_data(uuid, integer, integer, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.get_cleanup_data(p_user_id uuid, p_never_viewed_days integer DEFAULT 7, p_old_unused_days integer DEFAULT 30, p_limit integer DEFAULT 50) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'public', 'pg_catalog'
    AS $$
DECLARE
    result jsonb;
    cutoff_30 timestamptz := now() - interval '30 days';
BEGIN
    WITH
    owned AS (
        SELECT DISTINCT pm.id,
               pm.title,
               pm.cover_urls,
               pm.author,
               pm.storage_size,
               pm.created_at,
               pm.last_viewed_at,
               pm.view_count,
               pm.keep_forever
        FROM resources r
        JOIN parsed_media pm ON pm.id = r.media_id
        WHERE r.creator_id = p_user_id
        AND r.is_trashed = false
    ),
    suggestions AS (
        SELECT
            o.id as media_id,
            o.title,
            o.cover_urls,
            o.author,
            o.storage_size,
            o.created_at,
            o.last_viewed_at,
            o.view_count,
            CASE
                WHEN o.view_count = 0 AND o.created_at < now() - (p_never_viewed_days || ' days')::interval THEN 'never_viewed'
                WHEN o.view_count > 0 AND o.last_viewed_at < now() - (p_old_unused_days || ' days')::interval THEN 'old_unused'
                ELSE 'large_file'
            END as reason,
            CASE
                WHEN o.view_count = 0 AND o.created_at < now() - (p_never_viewed_days || ' days')::interval
                    THEN 'Downloaded over ' || p_never_viewed_days || ' days ago but never viewed'
                WHEN o.view_count > 0 AND o.last_viewed_at < now() - (p_old_unused_days || ' days')::interval
                    THEN 'Not viewed in over ' || p_old_unused_days || ' days'
                ELSE 'Large file: ' || pg_size_pretty(o.storage_size)
            END as reason_detail,
            CASE
                WHEN o.view_count = 0 THEN 1
                WHEN o.last_viewed_at < now() - (p_old_unused_days || ' days')::interval THEN 2
                ELSE 3
            END as priority
        FROM owned o
        WHERE o.keep_forever = false
        AND (
            (o.view_count = 0 AND o.created_at < now() - (p_never_viewed_days || ' days')::interval)
            OR (o.view_count > 0 AND o.last_viewed_at < now() - (p_old_unused_days || ' days')::interval)
            OR (o.storage_size IS NOT NULL AND o.storage_size > (
                SELECT COALESCE(percentile_cont(0.9) WITHIN GROUP (ORDER BY sub.storage_size), 0)
                FROM owned sub
                WHERE sub.storage_size IS NOT NULL
            ))
        )
        ORDER BY priority, storage_size DESC NULLS LAST
        LIMIT p_limit
    ),
    stats AS (
        SELECT
            COUNT(*)::bigint as total_videos,
            COALESCE(SUM(o.storage_size), 0)::bigint as total_storage_bytes,
            COUNT(*) FILTER (WHERE o.view_count = 0)::bigint as videos_never_viewed,
            COUNT(*) FILTER (WHERE o.last_viewed_at < cutoff_30)::bigint as videos_not_viewed_30_days,
            COUNT(*) FILTER (WHERE o.keep_forever = true)::bigint as videos_marked_keep,
            COALESCE(SUM(o.storage_size) FILTER (
                WHERE o.keep_forever = false
                AND (o.view_count = 0 OR o.last_viewed_at < cutoff_30)
            ), 0)::bigint as reclaimable_bytes
        FROM owned o
    ),
    categories AS (
        SELECT
            COUNT(*) FILTER (WHERE reason = 'never_viewed')::int as never_viewed,
            COUNT(*) FILTER (WHERE reason = 'old_unused')::int as old_unused,
            COUNT(*) FILTER (WHERE reason = 'large_file')::int as large_file
        FROM suggestions
    )
    SELECT jsonb_build_object(
        'suggestions', COALESCE((SELECT jsonb_agg(row_to_json(s.*)) FROM suggestions s), '[]'::jsonb),
        'stats', (SELECT row_to_json(st.*) FROM stats st),
        'categories', (SELECT row_to_json(c.*) FROM categories c)
    ) INTO result;

    RETURN result;
END;
$$;


--
-- Name: get_cleanup_stats(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.get_cleanup_stats(p_user_id uuid) RETURNS TABLE(total_videos bigint, total_storage_bytes bigint, videos_never_viewed bigint, videos_not_viewed_30_days bigint, videos_marked_keep bigint, reclaimable_bytes bigint)
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'public', 'pg_catalog'
    AS $$
DECLARE
    cutoff_30 timestamptz := now() - interval '30 days';
BEGIN
    RETURN QUERY
    WITH owned AS (
        SELECT DISTINCT pm.id,
               pm.storage_size,
               pm.view_count,
               pm.last_viewed_at,
               pm.keep_forever
        FROM resources r
        JOIN parsed_media pm ON pm.id = r.media_id
        WHERE r.creator_id = p_user_id
        AND r.is_trashed = false
    )
    SELECT
        COUNT(*)::bigint as total_videos,
        COALESCE(SUM(o.storage_size), 0)::bigint as total_storage_bytes,
        COUNT(*) FILTER (WHERE o.view_count = 0)::bigint as videos_never_viewed,
        COUNT(*) FILTER (WHERE o.last_viewed_at < cutoff_30)::bigint as videos_not_viewed_30_days,
        COUNT(*) FILTER (WHERE o.keep_forever = true)::bigint as videos_marked_keep,
        COALESCE(SUM(o.storage_size) FILTER (
            WHERE o.keep_forever = false
            AND (o.view_count = 0 OR o.last_viewed_at < cutoff_30)
        ), 0)::bigint as reclaimable_bytes
    FROM owned o;
END;
$$;


--
-- Name: get_cleanup_suggestions(uuid, integer, integer, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.get_cleanup_suggestions(p_user_id uuid, p_never_viewed_days integer DEFAULT 7, p_old_unused_days integer DEFAULT 30, p_limit integer DEFAULT 50) RETURNS TABLE(media_id bigint, title text, cover_urls jsonb, author character varying, storage_size bigint, created_at timestamp with time zone, last_viewed_at timestamp with time zone, view_count integer, reason text, reason_detail text)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public', 'pg_catalog'
    AS $$
    -- The user's owned, non-trashed media (distinct media_ids).
    WITH owned AS (
        SELECT DISTINCT pm.id,
               pm.title,
               pm.cover_urls,
               pm.author,
               pm.storage_size,
               pm.created_at,
               pm.last_viewed_at,
               pm.view_count,
               pm.keep_forever
        FROM resources r
        JOIN parsed_media pm ON pm.id = r.media_id
        WHERE r.creator_id = p_user_id
        AND r.is_trashed = false
    ),
    suggestions AS (
        SELECT
            o.id,
            o.title,
            o.cover_urls,
            o.author,
            o.storage_size,
            o.created_at,
            o.last_viewed_at,
            o.view_count,
            'never_viewed'::text as reason,
            'Downloaded over ' || p_never_viewed_days || ' days ago but never viewed' as reason_detail,
            1 as priority
        FROM owned o
        WHERE o.keep_forever = false
        AND o.view_count = 0
        AND o.created_at < now() - (p_never_viewed_days || ' days')::interval

        UNION ALL

        SELECT
            o.id,
            o.title,
            o.cover_urls,
            o.author,
            o.storage_size,
            o.created_at,
            o.last_viewed_at,
            o.view_count,
            'old_unused'::text as reason,
            'Not viewed in over ' || p_old_unused_days || ' days' as reason_detail,
            2 as priority
        FROM owned o
        WHERE o.keep_forever = false
        AND o.view_count > 0
        AND o.last_viewed_at < now() - (p_old_unused_days || ' days')::interval

        UNION ALL

        SELECT
            o.id,
            o.title,
            o.cover_urls,
            o.author,
            o.storage_size,
            o.created_at,
            o.last_viewed_at,
            o.view_count,
            'large_file'::text as reason,
            'Large file: ' || pg_size_pretty(o.storage_size) as reason_detail,
            3 as priority
        FROM owned o
        WHERE o.keep_forever = false
        AND o.storage_size IS NOT NULL
        AND o.storage_size > (
            SELECT COALESCE(percentile_cont(0.9) WITHIN GROUP (ORDER BY sub.storage_size), 0)
            FROM owned sub
            WHERE sub.storage_size IS NOT NULL
        )
    )
    SELECT DISTINCT ON (s.id)
        s.id as media_id,
        s.title,
        s.cover_urls,
        s.author,
        s.storage_size,
        s.created_at,
        s.last_viewed_at,
        s.view_count,
        s.reason,
        s.reason_detail
    FROM suggestions s
    ORDER BY s.id, s.priority
    LIMIT p_limit;
$$;


--
-- Name: get_popular_searches(integer, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.get_popular_searches(days_back integer DEFAULT 7, max_results integer DEFAULT 10) RETURNS TABLE(query text, search_count bigint, avg_results numeric)
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $$
BEGIN
    RETURN QUERY
    SELECT
        sl.query,
        COUNT(*) as search_count,
        AVG(sl.result_count)::NUMERIC as avg_results
    FROM search_logs sl
    WHERE sl.created_at > now() - (days_back || ' days')::INTERVAL
    AND sl.query IS NOT NULL
    AND LENGTH(sl.query) > 2
    GROUP BY sl.query
    ORDER BY search_count DESC
    LIMIT max_results;
END;
$$;


--
-- Name: FUNCTION get_popular_searches(days_back integer, max_results integer); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.get_popular_searches(days_back integer, max_results integer) IS 'Returns most popular search queries in the specified time period';


--
-- Name: get_search_analytics(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.get_search_analytics(days_back integer DEFAULT 30) RETURNS TABLE(date date, total_searches bigint, unique_users bigint, semantic_searches bigint, hybrid_searches bigint, avg_result_count numeric)
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $$
BEGIN
    RETURN QUERY
    SELECT
        DATE(sl.created_at) as date,
        COUNT(*) as total_searches,
        COUNT(DISTINCT sl.user_id) as unique_users,
        COUNT(*) FILTER (WHERE sl.search_type = 'semantic') as semantic_searches,
        COUNT(*) FILTER (WHERE sl.search_type = 'hybrid') as hybrid_searches,
        AVG(sl.result_count)::NUMERIC as avg_result_count
    FROM search_logs sl
    WHERE sl.created_at > now() - (days_back || ' days')::INTERVAL
    GROUP BY DATE(sl.created_at)
    ORDER BY date DESC;
END;
$$;


--
-- Name: FUNCTION get_search_analytics(days_back integer); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.get_search_analytics(days_back integer) IS 'Returns daily search analytics for dashboard';


--
-- Name: get_tag_counts_by_ids(text[]); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.get_tag_counts_by_ids(p_tag_ids text[]) RETURNS TABLE(tag_id text, count bigint)
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_catalog'
    AS $$
  SELECT rt.tag_id::text AS tag_id, COUNT(*)::bigint AS count
  FROM resource_tags rt
  WHERE rt.tag_id = ANY(
    SELECT unnest(p_tag_ids)::bigint
  )
  GROUP BY rt.tag_id;
$$;


--
-- Name: get_user_tag_counts(uuid, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.get_user_tag_counts(p_user_id uuid, p_limit integer DEFAULT 10) RETURNS TABLE(id bigint, name character varying, color character varying, icon character varying, type character varying, count bigint)
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_catalog'
    AS $$
BEGIN
    RETURN QUERY
    SELECT
        t.id,
        t.name,
        t.color,
        t.icon,
        t.type,
        COUNT(rt.resource_id) AS count
    FROM tags t
    JOIN resource_tags rt ON t.id = rt.tag_id
    JOIN resources r ON r.id = rt.resource_id
    WHERE r.creator_id = p_user_id
    GROUP BY t.id, t.name, t.color, t.icon, t.type
    ORDER BY count DESC
    LIMIT p_limit;
END;
$$;


--
-- Name: get_user_team_ids(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.get_user_team_ids(p_user_id uuid) RETURNS SETOF bigint
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public', 'pg_catalog'
    AS $$
  SELECT DISTINCT team_id FROM team_members WHERE user_id = p_user_id;
$$;


--
-- Name: get_user_team_ids_text(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.get_user_team_ids_text(p_user_id uuid) RETURNS SETOF text
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public', 'pg_catalog'
    AS $$
  SELECT DISTINCT team_id::text FROM team_members WHERE user_id = p_user_id;
$$;


--
-- Name: grant_daily_free_points_batch(integer, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.grant_daily_free_points_batch(p_amount integer, p_today date) RETURNS TABLE(granted integer, skipped integer)
    LANGUAGE plpgsql
    SET search_path TO 'public'
    AS $$
DECLARE
    v_granted integer := 0;
    v_total   integer := 0;
BEGIN
    IF p_amount IS NULL OR p_amount <= 0 THEN
        RETURN QUERY SELECT 0, 0;
        RETURN;
    END IF;

    SELECT COUNT(*) INTO v_total FROM teams WHERE kind = 'personal';

    -- Quota rows must pre-exist for the credit UPDATE (CTEs cannot see
    -- each other's inserts, so this is a separate statement).
    INSERT INTO team_quotas (team_id)
    SELECT t.id FROM teams t
    WHERE t.kind = 'personal'
      AND NOT EXISTS (SELECT 1 FROM team_quotas q WHERE q.team_id = t.id);

    WITH eligible AS (
        SELECT t.id AS team_id, t.owner_id AS user_id
        FROM teams t
        WHERE t.kind = 'personal'
    ),
    claimed AS (
        -- Claim the idempotency row FIRST: on conflict the team drops out
        -- of every downstream CTE, so a re-run cannot double-credit.
        INSERT INTO daily_point_gifts
            (user_id, team_id, gift_date, amount_granted, status)
        SELECT e.user_id, e.team_id, p_today, p_amount, 'granted'
        FROM eligible e
        ON CONFLICT (user_id, gift_date) DO NOTHING
        RETURNING team_id, user_id
    ),
    credited AS (
        UPDATE team_quotas q
        SET points_balance = q.points_balance + p_amount,
            updated_at = now()
        FROM claimed c
        WHERE q.team_id = c.team_id
        RETURNING q.team_id, q.points_balance AS balance_after
    ),
    txns AS (
        INSERT INTO point_transactions
            (team_id, user_id, amount, balance_after,
             type, reference_type, description)
        SELECT cr.team_id, c.user_id, p_amount, cr.balance_after,
               'daily_gift', 'daily_gift',
               'Daily free points (' || p_today::text || ')'
        FROM credited cr
        JOIN claimed c ON c.team_id = cr.team_id
        RETURNING 1
    )
    SELECT COUNT(*) INTO v_granted FROM claimed;

    RETURN QUERY SELECT v_granted, v_total - v_granted;
END;
$$;


--
-- Name: handle_new_user(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.handle_new_user() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
DECLARE
    uname TEXT;
BEGIN
    uname := COALESCE(
        NEW.raw_user_meta_data->>'username',
        split_part(NEW.email, '@', 1)
    );

    -- Create user profile (idempotent)
    INSERT INTO public.user_profiles (id, username, role)
    VALUES (NEW.id, uname, 'user')
    ON CONFLICT (id) DO NOTHING;

    -- Auto-create personal team.
    -- kind='personal' is explicit so the column DEFAULT ('collaborative')
    -- does not take effect.
    -- Idempotent: uq_teams_owner_personal prevents a second personal team.
    INSERT INTO public.teams (name, owner_id, kind)
    VALUES (
        uname || '''s Workspace',
        NEW.id,
        'personal'
    )
    ON CONFLICT DO NOTHING;

    RETURN NEW;
END;
$$;


--
-- Name: increment_api_key_usage(character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.increment_api_key_usage(p_key_id character varying) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_catalog'
    AS $$
BEGIN
    UPDATE api_keys
    SET
        usage_count = usage_count + 1,
        last_used_at = NOW()
    WHERE key_id = p_key_id;
END;
$$;


--
-- Name: initialize_user_credits(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.initialize_user_credits() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_catalog'
    AS $$
DECLARE
    initial_credits INTEGER;
BEGIN
    -- Resolve initial credit amount with two fallback layers:
    --   1. system_settings row missing entirely → COALESCE the scalar
    --      subquery to the literal 100
    --   2. negative configured value → clamp to 0 (defensive)
    initial_credits := GREATEST(
        COALESCE(
            (SELECT (value)::integer FROM system_settings WHERE key = 'new_user_credits'),
            100
        ),
        0
    );

    -- Create credit account with initial balance
    INSERT INTO user_credits (user_id, balance, total_earned)
    VALUES (NEW.id, initial_credits, initial_credits)
    ON CONFLICT (user_id) DO NOTHING;

    -- Log the initial credit gift if credits were given
    IF initial_credits > 0 THEN
        INSERT INTO credit_transactions (user_id, amount, type, description)
        VALUES (NEW.id, initial_credits, 'gift', 'Welcome bonus for new user');
    END IF;

    RETURN NEW;
END;
$$;


--
-- Name: inspiration_activity(uuid, date, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.inspiration_activity(p_user_id uuid, p_from date, p_to date) RETURNS TABLE(day date, cnt bigint)
    LANGUAGE sql STABLE
    AS $$
  SELECT note_date AS day, COUNT(*) AS cnt
  FROM inspiration_notes
  WHERE user_id = p_user_id AND deleted_at IS NULL
    AND note_date BETWEEN p_from AND p_to
  GROUP BY note_date
  ORDER BY note_date;
$$;


--
-- Name: inspiration_tag_counts(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.inspiration_tag_counts(p_user_id uuid) RETURNS TABLE(tag text, cnt bigint)
    LANGUAGE sql STABLE
    AS $$
  SELECT t.tag, COUNT(*) AS cnt
  FROM inspiration_notes n, unnest(n.tags) AS t(tag)
  WHERE n.user_id = p_user_id AND n.deleted_at IS NULL
  GROUP BY t.tag
  ORDER BY cnt DESC, tag;
$$;


--
-- Name: is_conversation_member(uuid, bigint); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.is_conversation_member(p_user uuid, p_conversation bigint) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.conversation_members
    WHERE conversation_id = p_conversation AND member_type='user' AND user_id = p_user
  );
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: issues; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.issues (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    issue_number integer NOT NULL,
    identifier text NOT NULL,
    team_id bigint,
    project_id bigint,
    parent_id bigint,
    goal_id bigint,
    title text NOT NULL,
    description text,
    status text DEFAULT 'backlog'::text NOT NULL,
    priority text DEFAULT 'medium'::text NOT NULL,
    assignee_agent_id uuid,
    assignee_user_id uuid,
    created_by_agent_id uuid,
    created_by_user_id uuid,
    dbos_workflow_id text,
    execution_locked_at timestamp with time zone,
    execution_state jsonb,
    origin_kind text DEFAULT 'manual'::text NOT NULL,
    origin_id text,
    origin_fingerprint text DEFAULT 'default'::text NOT NULL,
    request_depth integer DEFAULT 0 NOT NULL,
    billing_code text,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    cancelled_at timestamp with time zone,
    hidden_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    ai_session_id bigint,
    CONSTRAINT issues_assignee_xor CHECK ((NOT ((assignee_agent_id IS NOT NULL) AND (assignee_user_id IS NOT NULL)))),
    CONSTRAINT issues_creator_required CHECK (((created_by_agent_id IS NOT NULL) OR (created_by_user_id IS NOT NULL))),
    CONSTRAINT issues_description_check CHECK (((description IS NULL) OR (length(description) <= 50000))),
    CONSTRAINT issues_origin_kind_check CHECK ((origin_kind = ANY (ARRAY['manual'::text, 'chat_delegate'::text, 'celery_pipeline'::text, 'agent_dispatch'::text, 'routine'::text, 'escalation'::text]))),
    CONSTRAINT issues_priority_check CHECK ((priority = ANY (ARRAY['critical'::text, 'high'::text, 'medium'::text, 'low'::text]))),
    CONSTRAINT issues_request_depth_check CHECK (((request_depth >= 0) AND (request_depth < 100))),
    CONSTRAINT issues_status_check CHECK ((status = ANY (ARRAY['backlog'::text, 'todo'::text, 'in_progress'::text, 'in_review'::text, 'blocked'::text, 'needs_followup'::text, 'done'::text, 'cancelled'::text]))),
    CONSTRAINT issues_title_check CHECK (((length(title) >= 1) AND (length(title) <= 500)))
);


--
-- Name: TABLE issues; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.issues IS 'Top-level user-visible "thing". DBOS workflows reference issues.id via dbos_workflow_id and issues.dbos_workflow_id reciprocally. Schema ported from Paperclip (MIT) with mediahub adaptations. Realtime publication excludes execution_state / execution_locked_at / dbos_workflow_id (internal plumbing or sensitive); see migration 172.';


--
-- Name: COLUMN issues.dbos_workflow_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.issues.dbos_workflow_id IS 'DBOS workflow handle (string id from DBOS.start_workflow). NULL until execution starts.';


--
-- Name: COLUMN issues.request_depth; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.issues.request_depth IS 'Delegation chain depth — incremented when an agent dispatches a child issue. Capped at 100 to break cycles.';


--
-- Name: issue_create_atomic(jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.issue_create_atomic(payload jsonb) RETURNS public.issues
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
DECLARE
  next_n  INTEGER;
  pfx     TEXT;
  ident   TEXT;
  new_row public.issues;
BEGIN
  -- Step 1: allocate identifier (atomic counter UPDATE)
  UPDATE public.issue_sequence
  SET counter = counter + 1
  WHERE scope = 'global'
  RETURNING counter, prefix INTO next_n, pfx;

  IF next_n IS NULL THEN
    RAISE EXCEPTION 'issue_sequence row missing for scope=global';
  END IF;

  ident := pfx || '-' || next_n::text;

  -- Step 2: INSERT using payload + allocated identifier. If this fails
  -- (CHECK violation, FK violation, RLS policy), the entire txn including
  -- the counter UPDATE rolls back — no gap.
  INSERT INTO public.issues (
    issue_number, identifier,
    team_id, project_id, parent_id,
    title, description,
    status, priority,
    assignee_agent_id, assignee_user_id,
    created_by_agent_id, created_by_user_id,
    dbos_workflow_id, execution_state,
    origin_kind, origin_id, origin_fingerprint,
    request_depth, billing_code
  ) VALUES (
    next_n, ident,
    (payload->>'team_id')::BIGINT,
    (payload->>'project_id')::BIGINT,
    (payload->>'parent_id')::BIGINT,
    payload->>'title',
    payload->>'description',
    COALESCE(payload->>'status', 'backlog'),
    COALESCE(payload->>'priority', 'medium'),
    (payload->>'assignee_agent_id')::UUID,
    (payload->>'assignee_user_id')::UUID,
    (payload->>'created_by_agent_id')::UUID,
    (payload->>'created_by_user_id')::UUID,
    payload->>'dbos_workflow_id',
    payload->'execution_state',
    COALESCE(payload->>'origin_kind', 'manual'),
    payload->>'origin_id',
    COALESCE(payload->>'origin_fingerprint', 'default'),
    COALESCE((payload->>'request_depth')::INTEGER, 0),
    payload->>'billing_code'
  )
  RETURNING * INTO new_row;

  RETURN new_row;
END;
$$;


--
-- Name: FUNCTION issue_create_atomic(payload jsonb); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.issue_create_atomic(payload jsonb) IS 'Atomic issue creation: allocates MH-N identifier and inserts in a single PG transaction. Replaces the 2-round-trip pattern in Python IssueRepository.atomic_create. Closes the counter-gap race for INSERT-failure cases. PR-D2.1.';


--
-- Name: issue_next_identifier(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.issue_next_identifier() RETURNS TABLE(issue_number integer, identifier text)
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
DECLARE
  next_n INTEGER;
  pfx    TEXT;
BEGIN
  UPDATE public.issue_sequence
  SET counter = counter + 1
  WHERE scope = 'global'
  RETURNING counter, prefix INTO next_n, pfx;

  IF next_n IS NULL THEN
    RAISE EXCEPTION 'issue_sequence row missing for scope=global';
  END IF;

  RETURN QUERY SELECT next_n, pfx || '-' || next_n::text;
END;
$$;


--
-- Name: issues_enforce_update_allowlist(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.issues_enforce_update_allowlist() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public'
    AS $$
DECLARE
  -- Columns mutable by creator/assignee. Everything else is service_role-only.
  -- These are the "user-facing" fields a creator or assignee should be able
  -- to edit through the UI: title, description, priority, the assignee
  -- itself, project/team scope changes (if they're entitled), billing_code,
  -- and the soft-delete signal hidden_at. Status is technically here too
  -- but in practice the router validates allowed transitions.
  immutable_changed BOOLEAN := false;
BEGIN
  -- Bypass for the EFFECTIVE role (after SET ROLE). We deliberately do NOT
  -- check session_user — the supabase pooler (Supavisor) connects as the
  -- `postgres` login role and then PostgREST does `SET ROLE authenticated`
  -- per JWT. If we trusted session_user, every authenticated request would
  -- bypass the trigger (session_user='postgres' = bypass). current_user is
  -- the effective role we actually want to authorize against:
  --   - DBOS step pattern: SET ROLE service_role → bypass (legitimate)
  --   - Direct supabase_admin connection (migrations) → bypass
  --   - Authenticated user via PostgREST: SET ROLE authenticated → enforce
  IF current_user IN ('service_role', 'supabase_admin') THEN
    RETURN NEW;
  END IF;

  -- Field-by-field check. Comparing NULLs requires IS DISTINCT FROM.
  IF NEW.id                  IS DISTINCT FROM OLD.id                  THEN immutable_changed := true; END IF;
  IF NEW.issue_number        IS DISTINCT FROM OLD.issue_number        THEN immutable_changed := true; END IF;
  IF NEW.identifier          IS DISTINCT FROM OLD.identifier          THEN immutable_changed := true; END IF;
  IF NEW.created_by_user_id  IS DISTINCT FROM OLD.created_by_user_id  THEN immutable_changed := true; END IF;
  IF NEW.created_by_agent_id IS DISTINCT FROM OLD.created_by_agent_id THEN immutable_changed := true; END IF;
  IF NEW.dbos_workflow_id    IS DISTINCT FROM OLD.dbos_workflow_id    THEN immutable_changed := true; END IF;
  IF NEW.execution_locked_at IS DISTINCT FROM OLD.execution_locked_at THEN immutable_changed := true; END IF;
  IF NEW.execution_state     IS DISTINCT FROM OLD.execution_state     THEN immutable_changed := true; END IF;
  IF NEW.request_depth       IS DISTINCT FROM OLD.request_depth       THEN immutable_changed := true; END IF;
  IF NEW.origin_kind         IS DISTINCT FROM OLD.origin_kind         THEN immutable_changed := true; END IF;
  IF NEW.origin_id           IS DISTINCT FROM OLD.origin_id           THEN immutable_changed := true; END IF;
  IF NEW.origin_fingerprint  IS DISTINCT FROM OLD.origin_fingerprint  THEN immutable_changed := true; END IF;
  IF NEW.created_at          IS DISTINCT FROM OLD.created_at          THEN immutable_changed := true; END IF;

  IF immutable_changed THEN
    RAISE EXCEPTION 'issues: attempted to modify column not in user-allowlist (only service_role can change identity / execution / origin / created_at fields)'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  RETURN NEW;
END;
$$;


--
-- Name: FUNCTION issues_enforce_update_allowlist(); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.issues_enforce_update_allowlist() IS 'Column-allowlist guard for issues UPDATE. Closes /review finding #1 — RLS WHO check is necessary but not sufficient; trigger enforces WHAT. Bypassed by service_role / supabase_admin / postgres roles.';


--
-- Name: match_videos_by_embedding(public.vector, double precision, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.match_videos_by_embedding(query_embedding public.vector, match_threshold double precision DEFAULT 0.7, match_count integer DEFAULT 10) RETURNS TABLE(media_id bigint, platform_id text, title text, description text, cover_urls jsonb, author text, view_count bigint, created_at timestamp with time zone, similarity double precision)
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $$
BEGIN
    RETURN QUERY
    SELECT
        pm.id,
        -- platform_id / author are varchar(255) on parsed_media; cast to text
        -- so the projection matches the RETURNS TABLE declared types (plpgsql
        -- RETURN QUERY type-checks strictly: varchar != text would 42804).
        pm.platform_id::text,
        pm.title,
        pm.description,
        pm.cover_urls,
        pm.author::text,
        COALESCE(pm.view_count, 0)::bigint,
        pm.created_at,
        (1 - (ra.content_embedding <=> query_embedding))::float
    FROM resource_analysis ra
    JOIN resources r     ON r.id = ra.resource_id
    JOIN parsed_media pm ON pm.id = r.media_id
    WHERE ra.content_embedding IS NOT NULL
      AND r.media_id IS NOT NULL
      AND (1 - (ra.content_embedding <=> query_embedding)) > match_threshold
    ORDER BY ra.content_embedding <=> query_embedding
    LIMIT match_count;
END;
$$;


--
-- Name: merge_tags(text, text[], uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.merge_tags(p_target text, p_sources text[], p_user uuid) RETURNS bigint
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
DECLARE
  v_target  bigint := p_target::bigint;
  v_sources bigint[];
  v_count   bigint;
  v_bad     int;
BEGIN
  -- Normalize sources: cast to bigint, drop the target if present, dedupe.
  SELECT array_agg(DISTINCT s::bigint)
    INTO v_sources
    FROM unnest(p_sources) AS s
   WHERE s::bigint <> v_target;

  IF v_sources IS NULL OR array_length(v_sources, 1) IS NULL THEN
    RAISE EXCEPTION 'merge_tags: no source tags to merge';
  END IF;

  -- Target must exist and be the caller's own user tag.
  IF NOT EXISTS (
    SELECT 1 FROM tags
     WHERE id = v_target AND type = 'user' AND user_id = p_user
  ) THEN
    RAISE EXCEPTION 'merge_tags: target % is not an owned user tag', v_target;
  END IF;

  -- Every source must be the caller's own user tag.
  SELECT count(*) INTO v_bad FROM tags
   WHERE id = ANY(v_sources) AND (type <> 'user' OR user_id <> p_user);
  IF v_bad > 0 THEN
    RAISE EXCEPTION 'merge_tags: one or more source tags are not owned user tags';
  END IF;

  -- All sources must exist.
  IF (SELECT count(*) FROM tags WHERE id = ANY(v_sources)) <> array_length(v_sources, 1) THEN
    RAISE EXCEPTION 'merge_tags: one or more source tags do not exist';
  END IF;

  -- Re-point with dedup against PK (resource_id, tag_id).
  INSERT INTO resource_tags (resource_id, tag_id, source, confidence, created_at)
  SELECT resource_id, v_target, source, confidence, created_at
    FROM resource_tags
   WHERE tag_id = ANY(v_sources)
  ON CONFLICT (resource_id, tag_id) DO NOTHING;

  -- Delete the source tags (FK ON DELETE CASCADE clears their resource_tags).
  DELETE FROM tags WHERE id = ANY(v_sources);

  SELECT count(*) INTO v_count FROM resource_tags WHERE tag_id = v_target;
  RETURN v_count;
END;
$$;


--
-- Name: mirror_dbos_lifecycle_to_tracking(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.mirror_dbos_lifecycle_to_tracking() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  mapped_status TEXT;
  mapped_phase  TEXT;
BEGIN
  mapped_status := CASE NEW.status
    WHEN 'PENDING'                       THEN 'pending'
    WHEN 'ENQUEUED'                      THEN 'pending'
    WHEN 'RUNNING'                       THEN 'processing'
    WHEN 'SUCCESS'                       THEN 'completed'
    WHEN 'CANCELLED'                     THEN 'cancelled'
    WHEN 'ERROR'                         THEN 'failed'
    WHEN 'RETRIES_EXCEEDED'              THEN 'failed'
    WHEN 'MAX_RECOVERY_ATTEMPTS_EXCEEDED' THEN 'failed'
    ELSE NULL
  END;

  mapped_phase := CASE NEW.status
    WHEN 'PENDING'                       THEN 'queued'
    WHEN 'ENQUEUED'                      THEN 'queued'
    WHEN 'RUNNING'                       THEN 'in_progress'
    WHEN 'SUCCESS'                       THEN 'completed'
    WHEN 'CANCELLED'                     THEN 'cancelled'
    WHEN 'ERROR'                         THEN 'failed'
    WHEN 'RETRIES_EXCEEDED'              THEN 'failed'
    WHEN 'MAX_RECOVERY_ATTEMPTS_EXCEEDED' THEN 'failed'
    ELSE NULL
  END;

  UPDATE public.task_tracking SET
    -- Preserve manual terminal failure: if the row is already 'failed' or
    -- 'cancelled' (set by record_workflow_failure or user cancel), don't
    -- let an incoming SUCCESS regress it to 'completed'.
    status = CASE
      WHEN status IN ('failed', 'cancelled') AND mapped_status = 'completed'
        THEN status
      ELSE COALESCE(mapped_status, status)
    END,

    -- Same anti-regression rule for phase. UI reads this; without it the
    -- card stays "Initializing..." forever even after DBOS reports SUCCESS.
    phase = CASE
      WHEN phase IN ('failed', 'cancelled') AND mapped_phase = 'completed'
        THEN phase
      ELSE COALESCE(mapped_phase, phase)
    END,

    -- Bump progress to 100 on terminal SUCCESS so the UI bar fills.
    -- On terminal failure, leave progress as-is so operators can see how
    -- far the run got before it died (often a useful diagnostic).
    progress = CASE
      WHEN NEW.status = 'SUCCESS' THEN 100
      ELSE progress
    END,

    started_at = COALESCE(
      CASE WHEN NEW.started_at_epoch_ms IS NOT NULL
           THEN to_timestamp(NEW.started_at_epoch_ms / 1000.0)
      END,
      started_at
    ),

    completed_at = CASE
      WHEN NEW.status IN (
        'SUCCESS', 'ERROR', 'CANCELLED',
        'RETRIES_EXCEEDED', 'MAX_RECOVERY_ATTEMPTS_EXCEEDED'
      )
      THEN to_timestamp(NEW.updated_at / 1000.0)
      ELSE completed_at
    END,

    error_msg = CASE
      WHEN NEW.error IS NOT NULL THEN public.dbos_error_to_text(NEW.error)
      ELSE error_msg
    END,

    updated_at = now()
  WHERE dbos_workflow_id = NEW.workflow_uuid;

  RETURN NEW;
END;
$$;


--
-- Name: FUNCTION mirror_dbos_lifecycle_to_tracking(); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.mirror_dbos_lifecycle_to_tracking() IS 'D8-A trigger function. Mirrors lifecycle from dbos.workflow_status to
   public.task_tracking on UPDATE. Mirrors: status, phase, progress,
   started_at, completed_at, error_msg. Frontend Task Center reads phase,
   so omitting it (mig 180) caused all parse/download/transcode rows to
   stay "Initializing" forever even after DBOS reported SUCCESS. Mig 200
   fixed that.';


--
-- Name: notify_on_release_notes(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.notify_on_release_notes() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
BEGIN
  IF COALESCE(NEW.status, 'success') = 'success'
     AND NEW.release_notes IS NOT NULL
     AND NEW.release_notes <> ''
     AND (TG_OP = 'INSERT' OR OLD.release_notes IS NULL OR OLD.release_notes = '')
  THEN
    INSERT INTO public.notifications (type, title, content)
    VALUES (
      'system',
      left(
        'New in ' || COALESCE(NEW.service, 'app') ||
        COALESCE(' ' || NEW.version, ''),
        200
      ),
      NEW.release_notes
    );
  END IF;
  RETURN NEW;
END;
$$;


--
-- Name: FUNCTION notify_on_release_notes(); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.notify_on_release_notes() IS 'Derive a single system notification when a deployment_logs row publishes
   release_notes (empty→non-empty). Idempotent via the transition guard.';


--
-- Name: notify_team_members(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.notify_team_members() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_catalog'
    AS $$
BEGIN
  IF NEW.type = 'team' AND NEW.team_id IS NOT NULL THEN
    INSERT INTO user_notifications (user_id, notification_id)
    SELECT user_id, NEW.id FROM team_members WHERE team_id = NEW.team_id;
  END IF;
  RETURN NEW;
END;
$$;


--
-- Name: reclaim_daily_free_points_batch(date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reclaim_daily_free_points_batch(p_yesterday date) RETURNS TABLE(reclaimed_count integer, total_reclaimed bigint)
    LANGUAGE plpgsql
    SET search_path TO 'public'
    AS $$
DECLARE
    v_count integer := 0;
    v_total bigint  := 0;
BEGIN
    WITH g AS (
        SELECT id, user_id, team_id, amount_granted, granted_at
        FROM daily_point_gifts
        WHERE gift_date = p_yesterday AND status = 'granted'
    ),
    usage AS (
        SELECT g.id AS gift_id, g.user_id, g.team_id, g.amount_granted,
               LEAST(
                   g.amount_granted,
                   COALESCE((
                       SELECT SUM(ABS(t.amount))
                       FROM point_transactions t
                       WHERE t.user_id = g.user_id
                         AND t.team_id = g.team_id
                         AND t.type = 'consume'
                         AND t.created_at >= g.granted_at
                   ), 0)
               )::integer AS used
        FROM g
    ),
    calc AS (
        SELECT u.gift_id, u.user_id, u.team_id, u.used,
               LEAST(u.amount_granted - u.used,
                     COALESCE(q.points_balance, 0))::integer AS reclaim_actual
        FROM usage u
        LEFT JOIN team_quotas q ON q.team_id = u.team_id
    ),
    debited AS (
        UPDATE team_quotas q
        SET points_balance = q.points_balance - c.reclaim_actual,
            updated_at = now()
        FROM calc c
        WHERE q.team_id = c.team_id AND c.reclaim_actual > 0
        RETURNING q.team_id, q.points_balance AS balance_after
    ),
    txns AS (
        INSERT INTO point_transactions
            (team_id, user_id, amount, balance_after,
             type, reference_type, description)
        SELECT d.team_id, c.user_id, -c.reclaim_actual, d.balance_after,
               'daily_gift_reclaim', 'daily_gift_reclaim',
               'Reclaim unused daily gift (' || p_yesterday::text || ')'
        FROM debited d
        JOIN calc c ON c.team_id = d.team_id
        RETURNING 1
    ),
    marked AS (
        UPDATE daily_point_gifts dg
        SET status = 'reclaimed',
            amount_consumed = c.used,
            amount_reclaimed = c.reclaim_actual,
            reclaimed_at = now()
        FROM calc c
        WHERE dg.id = c.gift_id
        RETURNING c.reclaim_actual
    )
    SELECT COUNT(*), COALESCE(SUM(m.reclaim_actual), 0)
    INTO v_count, v_total
    FROM marked m;

    RETURN QUERY SELECT v_count, v_total;
END;
$$;


--
-- Name: reinforce_agent_memories(uuid[], timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reinforce_agent_memories(p_memory_ids uuid[], p_now timestamp with time zone) RETURNS void
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
  UPDATE agent_memories
     SET reinforcement_count = reinforcement_count + 1,
         last_recalled_at = p_now
   WHERE id = ANY(p_memory_ids)
     AND (auth.uid() IS NULL OR user_id = auth.uid());
$$;


--
-- Name: resource_aspect_bucket(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.resource_aspect_bucket(res text) RETURNS text
    LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
    AS $_$
DECLARE
  m        TEXT[];
  w        NUMERIC;
  h        NUMERIC;
  ratio    NUMERIC;
BEGIN
  IF res IS NULL THEN
    RETURN 'other';
  END IF;
  m := regexp_match(res, '^\s*(\d+)\s*[xX×:]\s*(\d+)\s*$');
  IF m IS NULL THEN
    RETURN 'other';
  END IF;
  w := m[1]::NUMERIC;
  h := m[2]::NUMERIC;
  IF w <= 0 OR h <= 0 THEN
    RETURN 'other';
  END IF;
  ratio := w / h;
  IF ratio > 0.5 AND ratio < 0.6 THEN
    RETURN '9:16';
  ELSIF ratio > 1.7 AND ratio < 1.85 THEN
    RETURN '16:9';
  ELSIF ratio > 0.95 AND ratio < 1.05 THEN
    RETURN '1:1';
  ELSIF ratio > 1.28 AND ratio < 1.4 THEN
    RETURN '4:3';
  ELSE
    RETURN 'other';
  END IF;
END;
$_$;


--
-- Name: rls_auto_enable(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rls_auto_enable() RETURNS event_trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE
  cmd record;
BEGIN
  FOR cmd IN
    SELECT *
    FROM pg_event_trigger_ddl_commands()
    WHERE command_tag IN ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO')
      AND object_type IN ('table','partitioned table')
  LOOP
     IF cmd.schema_name IS NOT NULL AND cmd.schema_name IN ('public') AND cmd.schema_name NOT IN ('pg_catalog','information_schema') AND cmd.schema_name NOT LIKE 'pg_toast%' AND cmd.schema_name NOT LIKE 'pg_temp%' THEN
      BEGIN
        EXECUTE format('alter table if exists %s enable row level security', cmd.object_identity);
        RAISE LOG 'rls_auto_enable: enabled RLS on %', cmd.object_identity;
      EXCEPTION
        WHEN OTHERS THEN
          RAISE LOG 'rls_auto_enable: failed to enable RLS on %', cmd.object_identity;
      END;
     ELSE
        RAISE LOG 'rls_auto_enable: skip % (either system schema or not in enforced list: %.)', cmd.object_identity, cmd.schema_name;
     END IF;
  END LOOP;
END;
$$;


--
-- Name: rpc_confirm_order_and_credit(bigint); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rpc_confirm_order_and_credit(p_order_id bigint) RETURNS TABLE(success boolean, already_credited boolean, points_added integer, new_balance integer, reason text)
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
DECLARE
    v_order       orders%ROWTYPE;
    v_ref_id      TEXT := p_order_id::text;
    v_new_balance INTEGER;
    v_existing    BIGINT;
BEGIN
    SELECT * INTO v_order FROM orders WHERE id = p_order_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT FALSE, FALSE, 0, NULL::INTEGER, 'Order not found'::TEXT; RETURN;
    END IF;
    IF v_order.payment_status = 'paid' THEN
        SELECT id INTO v_existing FROM point_transactions
            WHERE reference_type = 'order' AND reference_id = v_ref_id LIMIT 1;
        IF FOUND THEN
            SELECT points_balance INTO v_new_balance FROM team_quotas WHERE team_id = v_order.team_id;
            RETURN QUERY SELECT TRUE, TRUE, 0, v_new_balance, NULL::TEXT; RETURN;
        END IF;
        -- paid but no ledger row = the crash gap -> fall through and credit.
    ELSIF v_order.payment_status <> 'pending' THEN
        RETURN QUERY SELECT FALSE, FALSE, 0, NULL::INTEGER,
            format('Order status is ''%s'', cannot confirm', v_order.payment_status)::TEXT; RETURN;
    END IF;
    UPDATE orders SET payment_status='paid', paid_at=COALESCE(paid_at, now()), updated_at=now()
        WHERE id = p_order_id;
    INSERT INTO team_quotas (team_id, points_balance) VALUES (v_order.team_id, 0)
        ON CONFLICT (team_id) DO NOTHING;
    UPDATE team_quotas SET points_balance = points_balance + v_order.points_amount
        WHERE team_id = v_order.team_id RETURNING points_balance INTO v_new_balance;
    BEGIN
        INSERT INTO point_transactions (team_id, user_id, amount, balance_after, type, reference_type, reference_id, description)
        VALUES (v_order.team_id, v_order.user_id, v_order.points_amount, v_new_balance,
                'purchase', 'order', v_ref_id,
                format('Purchased %s points (order %s)', v_order.points_amount, p_order_id));
    EXCEPTION WHEN unique_violation THEN
        UPDATE team_quotas SET points_balance = points_balance - v_order.points_amount
            WHERE team_id = v_order.team_id RETURNING points_balance INTO v_new_balance;
        RETURN QUERY SELECT TRUE, TRUE, 0, v_new_balance, NULL::TEXT; RETURN;
    END;
    RETURN QUERY SELECT TRUE, FALSE, v_order.points_amount, v_new_balance, NULL::TEXT;
END;
$$;


--
-- Name: rpc_consume_team_points(bigint, uuid, integer, boolean); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rpc_consume_team_points(p_team_id bigint, p_user_id uuid, p_points_cost integer, p_monthly_limit_check boolean DEFAULT true) RETURNS TABLE(success boolean, points_cost integer, balance_after integer, reason text)
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
DECLARE
    v_balance INTEGER;
    v_monthly_limit INTEGER;
    v_used_this_month INTEGER;
BEGIN
    IF p_points_cost <= 0 THEN
        RETURN QUERY SELECT TRUE, 0, NULL::INTEGER, NULL::TEXT;
        RETURN;
    END IF;

    UPDATE team_quotas
       SET points_balance = points_balance - p_points_cost
     WHERE team_id = p_team_id
       AND points_balance >= p_points_cost
    RETURNING points_balance INTO v_balance;

    IF NOT FOUND THEN
        SELECT points_balance INTO v_balance
          FROM team_quotas
         WHERE team_id = p_team_id;

        IF v_balance IS NULL THEN
            RETURN QUERY SELECT FALSE, p_points_cost, 0,
                'Team quota not found. Please contact support.'::TEXT;
        ELSE
            RETURN QUERY SELECT FALSE, p_points_cost, v_balance,
                format('Insufficient points balance. Required: %s, available: %s.',
                       p_points_cost, v_balance)::TEXT;
        END IF;
        RETURN;
    END IF;

    IF p_monthly_limit_check THEN
        SELECT monthly_points_limit, points_used_this_month
          INTO v_monthly_limit, v_used_this_month
          FROM member_quotas
         WHERE team_id = p_team_id
           AND user_id = p_user_id;

        IF v_monthly_limit IS NOT NULL
           AND (COALESCE(v_used_this_month, 0) + p_points_cost) > v_monthly_limit
        THEN
            UPDATE team_quotas
               SET points_balance = points_balance + p_points_cost
             WHERE team_id = p_team_id;

            RETURN QUERY SELECT FALSE, p_points_cost, v_balance + p_points_cost,
                format('Monthly points limit exceeded. Limit: %s, used: %s, required: %s.',
                       v_monthly_limit, COALESCE(v_used_this_month, 0), p_points_cost)::TEXT;
            RETURN;
        END IF;
    END IF;

    UPDATE member_quotas
       SET points_used_this_month = COALESCE(points_used_this_month, 0) + p_points_cost
     WHERE team_id = p_team_id
       AND user_id = p_user_id;

    RETURN QUERY SELECT TRUE, p_points_cost, v_balance, NULL::TEXT;
END;
$$;


--
-- Name: rpc_downloads_library_search(uuid, text[], integer, boolean, boolean, boolean, text, text, integer, integer, text[], text[], text[], boolean, integer, integer, integer, integer, text, timestamp with time zone, bigint, integer, boolean); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rpc_downloads_library_search(p_user_id uuid, p_tag_ids text[] DEFAULT NULL::text[], p_min_rating integer DEFAULT NULL::integer, p_ai_transcribed boolean DEFAULT NULL::boolean, p_ai_summarized boolean DEFAULT NULL::boolean, p_ai_analyzed boolean DEFAULT NULL::boolean, p_created_after text DEFAULT NULL::text, p_created_before text DEFAULT NULL::text, p_duration_min integer DEFAULT NULL::integer, p_duration_max integer DEFAULT NULL::integer, p_aspect_ratios text[] DEFAULT NULL::text[], p_platforms text[] DEFAULT NULL::text[], p_media_types text[] DEFAULT NULL::text[], p_has_comments boolean DEFAULT NULL::boolean, p_min_likes integer DEFAULT NULL::integer, p_min_comments integer DEFAULT NULL::integer, p_min_favorites integer DEFAULT NULL::integer, p_min_shares integer DEFAULT NULL::integer, p_social_combine text DEFAULT 'and'::text, p_cursor_ts timestamp with time zone DEFAULT NULL::timestamp with time zone, p_cursor_id bigint DEFAULT NULL::bigint, p_limit integer DEFAULT 40, p_with_count boolean DEFAULT false) RETURNS jsonb
    LANGUAGE sql STABLE
    SET search_path TO 'public'
    AS $$
  WITH filtered AS (
    SELECT
      r.id         AS res_id,
      r.created_at AS created_at,
      jsonb_build_object(
        'id',         r.id,
        'created_at', r.created_at,
        'parsed_media', to_jsonb(pm.*)
      ) AS row
    FROM public.resources r
    JOIN public.parsed_media pm
      ON pm.id = r.media_id
    WHERE
      -- Base filters (always applied) — mirror dataService's resources query.
      r.creator_id = p_user_id
      AND r.source_type = 'web'
      AND r.is_trashed = false

      -- Tag AND: resource must carry EVERY requested tag.
      AND (
        p_tag_ids IS NULL
        OR cardinality(p_tag_ids) = 0
        OR r.id IN (
          SELECT rt.resource_id
          FROM public.resource_tags rt
          WHERE rt.tag_id = ANY(p_tag_ids::bigint[])
          GROUP BY rt.resource_id
          HAVING count(DISTINCT rt.tag_id) = cardinality(p_tag_ids)
        )
      )

      -- Resource-column filters (rating / AI status / dates / duration / aspect)
      AND (p_min_rating IS NULL OR p_min_rating = 0 OR r.rating >= p_min_rating)
      AND (p_ai_transcribed IS NULL OR p_ai_transcribed = false OR r.transcript_status = 'completed')
      AND (p_ai_summarized  IS NULL OR p_ai_summarized  = false OR r.summary_status = 'completed')
      AND (p_ai_analyzed    IS NULL OR p_ai_analyzed    = false OR r.visual_analysis_status = 'completed')
      AND (p_created_after  IS NULL OR r.created_at >= (p_created_after || 'T00:00:00')::timestamptz)
      AND (p_created_before IS NULL OR r.created_at <= (p_created_before || 'T23:59:59.999')::timestamptz)
      AND (p_duration_min IS NULL OR r.duration_seconds >= p_duration_min)
      AND (p_duration_max IS NULL OR r.duration_seconds <= p_duration_max)
      AND (p_aspect_ratios IS NULL OR cardinality(p_aspect_ratios) = 0 OR r.aspect_bucket = ANY(p_aspect_ratios))

      -- parsed_media-column filters (platform / media_type / comments).
      AND (p_platforms IS NULL OR cardinality(p_platforms) = 0 OR pm.source_platform = ANY(p_platforms))
      AND (p_media_types IS NULL OR cardinality(p_media_types) = 0 OR pm.media_type = ANY(p_media_types))
      AND (p_has_comments IS NULL OR p_has_comments = false OR pm.comment_count > 0)

      -- Social thresholds: 'or' combine vs (default) 'and' combine — matches
      -- applyLibraryFilters' AND-chain / .or() fragment behaviour. Thresholds
      -- of 0/NULL are inactive.
      AND (
        CASE
          WHEN p_social_combine = 'or' THEN (
            (p_min_likes IS NOT NULL AND p_min_likes > 0 AND pm.like_count >= p_min_likes)
            OR (p_min_comments IS NOT NULL AND p_min_comments > 0 AND pm.comment_count >= p_min_comments)
            OR (p_min_favorites IS NOT NULL AND p_min_favorites > 0 AND pm.favorite_count >= p_min_favorites)
            OR (p_min_shares IS NOT NULL AND p_min_shares > 0 AND pm.share_count >= p_min_shares)
            -- no social threshold active → OR group must not exclude rows
            OR NOT (
              (p_min_likes IS NOT NULL AND p_min_likes > 0)
              OR (p_min_comments IS NOT NULL AND p_min_comments > 0)
              OR (p_min_favorites IS NOT NULL AND p_min_favorites > 0)
              OR (p_min_shares IS NOT NULL AND p_min_shares > 0)
            )
          )
          ELSE (
            (p_min_likes IS NULL OR p_min_likes = 0 OR pm.like_count >= p_min_likes)
            AND (p_min_comments IS NULL OR p_min_comments = 0 OR pm.comment_count >= p_min_comments)
            AND (p_min_favorites IS NULL OR p_min_favorites = 0 OR pm.favorite_count >= p_min_favorites)
            AND (p_min_shares IS NULL OR p_min_shares = 0 OR pm.share_count >= p_min_shares)
          )
        END
      )
  )
  SELECT jsonb_build_object(
    'rows',
    COALESCE(
      (
        SELECT jsonb_agg(page.row ORDER BY page.created_at DESC, page.res_id DESC)
        FROM (
          SELECT f.row, f.created_at, f.res_id
          FROM filtered f
          WHERE (
            p_cursor_ts IS NULL
            OR f.created_at < p_cursor_ts
            OR (f.created_at = p_cursor_ts AND f.res_id < p_cursor_id)
          )
          ORDER BY f.created_at DESC, f.res_id DESC
          LIMIT p_limit
        ) AS page
      ),
      '[]'::jsonb
    ),
    'total_count',
    CASE WHEN p_with_count THEN (SELECT count(*) FROM filtered) ELSE NULL END
  );
$$;


--
-- Name: rpc_monitoring_stats(timestamp with time zone, timestamp with time zone, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rpc_monitoring_stats(p_start timestamp with time zone, p_end timestamp with time zone, p_bucket_minutes integer) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
  WITH req AS (
    SELECT path, status_code, response_time_ms, "timestamp" AS ts
    FROM public.api_request_logs
    WHERE "timestamp" >= p_start AND "timestamp" <= p_end
  ),
  app AS (
    SELECT level, module, message, logged_at
    FROM public.application_logs
    WHERE logged_at >= p_start AND logged_at <= p_end
  ),
  req_agg AS (
    SELECT
      count(*)                                                      AS total_requests,
      count(*) FILTER (WHERE COALESCE(status_code, 0) >= 400)       AS error_requests,
      COALESCE(sum(COALESCE(response_time_ms, 0)), 0)               AS sum_ms
    FROM req
  ),
  trend AS (
    SELECT
      CASE
        WHEN p_bucket_minutes >= 1440 THEN
          to_char(date_trunc('day', ts AT TIME ZONE 'UTC'), 'YYYY-MM-DD"T"00:00')
        WHEN p_bucket_minutes >= 60 THEN
          to_char(
            date_bin(make_interval(mins => p_bucket_minutes),
                     ts AT TIME ZONE 'UTC',
                     date_trunc('day', ts AT TIME ZONE 'UTC')),
            'YYYY-MM-DD"T"HH24:00')
        ELSE
          to_char(
            date_bin(make_interval(mins => p_bucket_minutes),
                     ts AT TIME ZONE 'UTC',
                     date_trunc('day', ts AT TIME ZONE 'UTC')),
            'YYYY-MM-DD"T"HH24:MI')
      END AS k,
      count(*)                                                AS requests,
      count(*) FILTER (WHERE COALESCE(status_code, 0) >= 400) AS errors
    FROM req
    GROUP BY 1
  ),
  slow AS (
    SELECT
      COALESCE(path, '')                                          AS p,
      round(avg(response_time_ms), 1)                             AS avg_ms,
      percentile_disc(0.95) WITHIN GROUP (ORDER BY response_time_ms) AS p95_ms,
      count(*)                                                    AS c
    FROM req
    WHERE response_time_ms IS NOT NULL AND path IS NOT NULL AND path <> ''
    GROUP BY 1
    ORDER BY avg(response_time_ms) DESC
    LIMIT 10
  ),
  err_ep AS (
    SELECT
      COALESCE(path, '')                          AS p,
      count(*)                                     AS c,
      (array_agg(status_code ORDER BY ts DESC))[1] AS last_status
    FROM req
    WHERE COALESCE(status_code, 0) >= 400
    GROUP BY 1
    ORDER BY count(*) DESC
    LIMIT 10
  ),
  level_dist AS (
    SELECT COALESCE(level, 'UNKNOWN') AS lvl, count(*) AS c
    FROM app GROUP BY 1
  ),
  mod_err AS (
    SELECT
      CASE
        WHEN COALESCE(module, 'unknown') LIKE '%.%'
          THEN reverse(split_part(reverse(module), '.', 1))
        ELSE COALESCE(module, 'unknown')
      END                AS m,
      count(*)           AS c
    FROM app
    WHERE level IN ('ERROR', 'CRITICAL', 'WARNING')
    GROUP BY 1
    ORDER BY count(*) DESC
    LIMIT 10
  ),
  recent AS (
    SELECT
      level,
      NULLIF(
        CASE
          WHEN module LIKE '%.%' THEN reverse(split_part(reverse(module), '.', 1))
          ELSE COALESCE(module, '')
        END, ''
      )                       AS module,
      COALESCE(message, '')   AS message,
      logged_at
    FROM app
    WHERE level IN ('ERROR', 'CRITICAL')
    ORDER BY logged_at DESC
    LIMIT 5
  )
  SELECT jsonb_build_object(
    'overview', jsonb_build_object(
      'total_requests', (SELECT total_requests FROM req_agg),
      'error_rate', (
        SELECT CASE WHEN total_requests > 0
                    THEN round(error_requests::numeric / total_requests * 100, 2)
                    ELSE 0 END
        FROM req_agg
      ),
      'avg_response_ms', (
        SELECT CASE WHEN total_requests > 0
                    THEN round(sum_ms::numeric / total_requests, 1)
                    ELSE 0 END
        FROM req_agg
      ),
      'app_error_count', (
        SELECT count(*) FROM app WHERE level IN ('ERROR', 'CRITICAL')
      ),
      'frontend_error_count', (
        SELECT count(*) FROM public.frontend_error_logs
        WHERE created_at >= p_start AND created_at <= p_end
      )
    ),
    'request_trend', COALESCE((
      SELECT jsonb_agg(
        jsonb_build_object('time', k, 'requests', requests, 'errors', errors)
        ORDER BY k
      ) FROM trend
    ), '[]'::jsonb),
    'top_slow_apis', COALESCE((
      SELECT jsonb_agg(
        jsonb_build_object('path', p, 'avg_ms', avg_ms, 'p95_ms', p95_ms, 'count', c)
        ORDER BY avg_ms DESC, p
      ) FROM slow
    ), '[]'::jsonb),
    'top_error_endpoints', COALESCE((
      SELECT jsonb_agg(
        jsonb_build_object('path', p, 'error_count', c, 'last_status', last_status)
        ORDER BY c DESC, p
      ) FROM err_ep
    ), '[]'::jsonb),
    'log_level_distribution', COALESCE(
      (SELECT jsonb_object_agg(lvl, c) FROM level_dist), '{}'::jsonb
    ),
    'top_error_modules', COALESCE((
      SELECT jsonb_agg(
        jsonb_build_object('module', m, 'count', c)
        ORDER BY c DESC, m
      ) FROM mod_err
    ), '[]'::jsonb),
    'recent_errors', COALESCE((
      SELECT jsonb_agg(
        jsonb_build_object(
          'level', level, 'module', module,
          'message', message, 'logged_at', logged_at
        ) ORDER BY logged_at DESC
      ) FROM recent
    ), '[]'::jsonb)
  );
$$;


--
-- Name: rpc_refund_order_and_debit(bigint); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rpc_refund_order_and_debit(p_order_id bigint) RETURNS TABLE(success boolean, already_refunded boolean, points_debited integer, new_balance integer, reason text)
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
DECLARE
    v_order       orders%ROWTYPE;
    v_ref_id      TEXT := p_order_id::text;
    v_balance     INTEGER;
    v_deduct      INTEGER;
    v_new_balance INTEGER;
    v_has_quota   BOOLEAN;
    v_existing    BIGINT;
BEGIN
    SELECT * INTO v_order FROM orders WHERE id = p_order_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT FALSE, FALSE, 0, NULL::INTEGER, 'Order not found'::TEXT; RETURN;
    END IF;

    IF v_order.payment_status = 'refunded' THEN
        SELECT id INTO v_existing FROM point_transactions
            WHERE reference_type = 'order_refund' AND reference_id = v_ref_id LIMIT 1;
        IF FOUND THEN
            SELECT points_balance INTO v_new_balance FROM team_quotas WHERE team_id = v_order.team_id;
            RETURN QUERY SELECT TRUE, TRUE, 0, v_new_balance, NULL::TEXT; RETURN;
        END IF;
        -- refunded but no ledger row = the crash gap -> fall through and debit.
    ELSIF v_order.payment_status <> 'paid' THEN
        RETURN QUERY SELECT FALSE, FALSE, 0, NULL::INTEGER,
            format('Order status is ''%s'', cannot refund', v_order.payment_status)::TEXT; RETURN;
    END IF;

    -- Current balance (0 if no team_quotas row).
    SELECT points_balance INTO v_balance FROM team_quotas WHERE team_id = v_order.team_id;
    v_has_quota := FOUND;

    -- CLAMP AT 0: never claw back more than the team currently has.
    v_deduct := LEAST(v_order.points_amount, COALESCE(v_balance, 0));

    UPDATE orders SET payment_status = 'refunded', updated_at = now()
        WHERE id = p_order_id;

    IF v_has_quota THEN
        UPDATE team_quotas SET points_balance = points_balance - v_deduct
            WHERE team_id = v_order.team_id RETURNING points_balance INTO v_new_balance;
    ELSE
        v_new_balance := 0;  -- no quota row -> nothing to debit
    END IF;

    BEGIN
        INSERT INTO point_transactions (team_id, user_id, amount, balance_after, type, reference_type, reference_id, description)
        VALUES (v_order.team_id, v_order.user_id, -v_deduct, v_new_balance,
                'refund', 'order_refund', v_ref_id,
                format('Refunded order %s (-%s points)', p_order_id, v_deduct));
    EXCEPTION WHEN unique_violation THEN
        -- Race: another worker inserted the refund between our check and insert.
        -- Roll back the balance debit to keep the ledger honest.
        IF v_has_quota THEN
            UPDATE team_quotas SET points_balance = points_balance + v_deduct
                WHERE team_id = v_order.team_id RETURNING points_balance INTO v_new_balance;
        END IF;
        RETURN QUERY SELECT TRUE, TRUE, 0, v_new_balance, NULL::TEXT; RETURN;
    END;

    RETURN QUERY SELECT TRUE, FALSE, v_deduct, v_new_balance, NULL::TEXT;
END;
$$;


--
-- Name: rpc_refund_team_points_idempotent(bigint, uuid, integer, text, text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rpc_refund_team_points_idempotent(p_team_id bigint, p_user_id uuid, p_amount integer, p_reference_type text, p_reference_id text, p_description text) RETURNS TABLE(success boolean, already_refunded boolean, new_balance integer)
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
DECLARE
    v_existing BIGINT;
    v_new_balance INTEGER;
BEGIN
    IF p_amount <= 0 THEN
        RETURN QUERY SELECT FALSE, FALSE, NULL::INTEGER;
        RETURN;
    END IF;

    IF p_reference_id IS NOT NULL THEN
        SELECT id INTO v_existing
          FROM point_transactions
         WHERE team_id = p_team_id
           AND reference_type = p_reference_type
           AND reference_id = p_reference_id
           AND type = 'refund'
         LIMIT 1;

        IF FOUND THEN
            SELECT points_balance INTO v_new_balance
              FROM team_quotas
             WHERE team_id = p_team_id;
            RETURN QUERY SELECT TRUE, TRUE, v_new_balance;
            RETURN;
        END IF;
    END IF;

    UPDATE team_quotas
       SET points_balance = points_balance + p_amount
     WHERE team_id = p_team_id
    RETURNING points_balance INTO v_new_balance;

    IF NOT FOUND THEN
        RETURN QUERY SELECT FALSE, FALSE, NULL::INTEGER;
        RETURN;
    END IF;

    BEGIN
        INSERT INTO point_transactions (
            team_id, user_id, amount, balance_after,
            type, reference_type, reference_id, description
        )
        VALUES (
            p_team_id, p_user_id, p_amount, v_new_balance,
            'refund', p_reference_type, p_reference_id, p_description
        );
    EXCEPTION WHEN unique_violation THEN
        UPDATE team_quotas
           SET points_balance = points_balance - p_amount
         WHERE team_id = p_team_id
        RETURNING points_balance INTO v_new_balance;

        RETURN QUERY SELECT TRUE, TRUE, v_new_balance;
        RETURN;
    END;

    RETURN QUERY SELECT TRUE, FALSE, v_new_balance;
END;
$$;


--
-- Name: rpc_reorder_storyboard_frames(uuid[]); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rpc_reorder_storyboard_frames(p_frame_ids uuid[]) RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
DECLARE
    v_updated INTEGER;
BEGIN
    UPDATE storyboard_frames AS sf
       SET sort_order = x.idx
      FROM unnest(p_frame_ids) WITH ORDINALITY AS x(frame_id, idx)
     WHERE sf.id = x.frame_id;

    GET DIAGNOSTICS v_updated = ROW_COUNT;
    RETURN v_updated;
END;
$$;


--
-- Name: rpc_request_log_stats(timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rpc_request_log_stats(p_since timestamp with time zone) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
  WITH base AS (
    SELECT method, status_code, path, response_time_ms, "timestamp" AS ts
    FROM public.api_request_logs
    WHERE "timestamp" >= p_since
  )
  SELECT jsonb_build_object(
    'total', (SELECT count(*) FROM base),
    'by_method', COALESCE((
      SELECT jsonb_agg(jsonb_build_object('method', m, 'count', c) ORDER BY c DESC, m)
      FROM (
        SELECT COALESCE(method, 'UNKNOWN') AS m, count(*) AS c
        FROM base GROUP BY 1
      ) q
    ), '[]'::jsonb),
    'by_status', COALESCE((
      SELECT jsonb_agg(jsonb_build_object('status_group', g, 'count', c) ORDER BY g)
      FROM (
        SELECT (status_code / 100)::text || 'xx' AS g, count(*) AS c
        FROM base WHERE status_code IS NOT NULL GROUP BY 1
      ) q
    ), '[]'::jsonb),
    'top_paths', COALESCE((
      SELECT jsonb_agg(
        jsonb_build_object('path', p, 'count', c, 'avg_response_time_ms', a)
        ORDER BY c DESC, p
      )
      FROM (
        SELECT
          COALESCE(path, '') AS p,
          count(*) AS c,
          COALESCE(
            floor(
              avg(response_time_ms) FILTER (
                WHERE response_time_ms IS NOT NULL AND response_time_ms <> 0
              )
            )::int,
            0
          ) AS a
        FROM base
        GROUP BY 1
        ORDER BY count(*) DESC
        LIMIT 20
      ) q
    ), '[]'::jsonb),
    'by_hour', COALESCE((
      SELECT jsonb_agg(jsonb_build_object('hour', h, 'count', c) ORDER BY h)
      FROM (
        SELECT to_char(ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24') AS h, count(*) AS c
        FROM base GROUP BY 1
      ) q
    ), '[]'::jsonb)
  );
$$;


--
-- Name: rpc_user_media_text_search(uuid, text, text[], text, text, text, text[], integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rpc_user_media_text_search(p_user_id uuid, p_pattern text, p_fields text[] DEFAULT '{}'::text[], p_author text DEFAULT NULL::text, p_date_from text DEFAULT NULL::text, p_date_to text DEFAULT NULL::text, p_tag_ids text[] DEFAULT NULL::text[], p_limit integer DEFAULT 1000) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
  WITH matched AS (
    SELECT DISTINCT ON (pm.id)
      pm.id          AS pm_id,
      pm.created_at  AS pm_created_at,
      jsonb_build_object(
        'id',                     pm.id,
        'platform_id',            pm.platform_id,
        'source_platform',        pm.source_platform,
        'title',                  pm.title,
        'author',                 pm.author,
        'description',            pm.description,
        'original_url',           pm.original_url,
        'cover_urls',             pm.cover_urls,
        'dynamic_cover_url',      pm.dynamic_cover_url,
        'cover_download_status',  pm.cover_download_status,
        'cover_download_path',    pm.cover_download_path,
        'like_count',             pm.like_count,
        'comment_count',          pm.comment_count,
        'share_count',            pm.share_count,
        'favorite_count',         pm.favorite_count,
        'view_count',             pm.view_count,
        'extract_audio_path',     pm.extract_audio_path,
        'music_download_path',    pm.music_download_path,
        'music_download_status',  pm.music_download_status,
        'music_name',             pm.music_name,
        'music_play_urls',        pm.music_play_urls,
        'video_download_status',  pm.video_download_status,
        'video_download_urls',    pm.video_download_urls,
        'image_download_status',  pm.image_download_status,
        'image_download_urls',    pm.image_download_urls,
        'image_download_path',    pm.image_download_path,
        'hashtags',               pm.hashtags,
        'error_message',          pm.error_message,
        'created_at',             pm.created_at,
        'updated_at',             pm.updated_at,
        'published_at',           pm.published_at,
        'last_viewed_at',         pm.last_viewed_at,
        'media_type',             pm.media_type,
        'media_format',           pm.media_format,
        'duration',               pm.duration,
        'resolution',             pm.resolution,
        'datasize',               pm.datasize,
        'datasize_bytes',         pm.datasize_bytes,
        'storage_size',           pm.storage_size,
        'keep_forever',           pm.keep_forever,
        'hls_path',               pm.hls_path,
        'download_path',          pm.download_path,
        'download_time',          pm.download_time,
        'download_duration',      pm.download_duration,
        -- Per-user decoration (so the card click navigates to the right
        -- /resources/file/<id> URL and the AI dot icons reflect real state).
        'resource_id',            r.id::text,
        'transcript_status',      r.transcript_status,
        'summary_status',         r.summary_status,
        'visual_analysis_status', r.visual_analysis_status
      ) AS row
    FROM public.parsed_media pm
    JOIN public.resources r
      ON  r.media_id    = pm.id
      AND r.creator_id  = p_user_id
      AND r.source_type = 'web'
      AND r.is_trashed  = false
    WHERE
      -- Field OR-match. NULL pattern = match-all (filter-only path).
      (
        p_pattern IS NULL
        OR ('title'       = ANY(p_fields) AND pm.title           ILIKE p_pattern)
        OR ('description' = ANY(p_fields) AND pm.description      ILIKE p_pattern)
        OR ('author'      = ANY(p_fields) AND pm.author           ILIKE p_pattern)
        OR ('hashtags'    = ANY(p_fields) AND pm.hashtags         ILIKE p_pattern)
        OR ('transcript'  = ANY(p_fields) AND pm.ai_extract_text  ILIKE p_pattern)
        OR ('notes'       = ANY(p_fields) AND r.notes             ILIKE p_pattern)
        OR ('analysis'    = ANY(p_fields) AND EXISTS (
              SELECT 1 FROM public.resource_analysis ra
              WHERE ra.resource_id = r.id
                AND (ra.visual_description ILIKE p_pattern
                     OR ra.detected_text ILIKE p_pattern)
           ))
        OR ('tags'        = ANY(p_fields) AND EXISTS (
              SELECT 1
              FROM public.resource_tags rt
              JOIN public.tags t ON t.id = rt.tag_id
              WHERE rt.resource_id = r.id
                AND t.name ILIKE p_pattern
           ))
      )
      -- AND filters (hybrid): author / date range.
      AND (p_author    IS NULL OR pm.author ILIKE ('%' || p_author || '%'))
      AND (p_date_from IS NULL OR pm.created_at >= p_date_from::timestamptz)
      AND (p_date_to   IS NULL OR pm.created_at <= p_date_to::timestamptz)
      -- AND filter (hybrid): resource must carry at least one requested tag id.
      AND (
        p_tag_ids IS NULL
        OR cardinality(p_tag_ids) = 0
        OR EXISTS (
          SELECT 1 FROM public.resource_tags rt2
          WHERE rt2.resource_id = r.id
            AND rt2.tag_id = ANY(p_tag_ids::bigint[])
        )
      )
    -- DISTINCT ON requires the dedup key to lead the ORDER BY; pick the newest
    -- owning resource per media for deterministic decoration.
    ORDER BY pm.id, r.created_at DESC
  )
  SELECT jsonb_build_object(
    'rows',
    COALESCE(
      (
        SELECT jsonb_agg(page.row ORDER BY page.pm_created_at DESC, page.pm_id DESC)
        FROM (
          SELECT row, pm_created_at, pm_id
          FROM matched
          ORDER BY pm_created_at DESC, pm_id DESC
          LIMIT p_limit
        ) AS page
      ),
      '[]'::jsonb
    )
  );
$$;


--
-- Name: rpc_user_owned_platform_ids(uuid, text[]); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.rpc_user_owned_platform_ids(p_user_id uuid, p_platform_ids text[]) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
  SELECT COALESCE(jsonb_agg(DISTINCT pm.platform_id), '[]'::jsonb)
  FROM public.parsed_media pm
  JOIN public.resources r
    ON  r.media_id    = pm.id
    AND r.creator_id  = p_user_id
    AND r.source_type = 'web'
    AND r.is_trashed  = false
  WHERE pm.platform_id = ANY(p_platform_ids);
$$;


--
-- Name: search_scope_resources(text, boolean, text, text, text[], integer, boolean, boolean, boolean, text, text, integer, integer, text[], text[], text[], boolean, integer, integer, integer, integer, text, timestamp with time zone, bigint, integer, boolean); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.search_scope_resources(p_scope_id text, p_is_personal boolean, p_folder_id text DEFAULT NULL::text, p_library_id text DEFAULT NULL::text, p_tag_ids text[] DEFAULT NULL::text[], p_min_rating integer DEFAULT NULL::integer, p_ai_transcribed boolean DEFAULT NULL::boolean, p_ai_summarized boolean DEFAULT NULL::boolean, p_ai_analyzed boolean DEFAULT NULL::boolean, p_created_after text DEFAULT NULL::text, p_created_before text DEFAULT NULL::text, p_duration_min integer DEFAULT NULL::integer, p_duration_max integer DEFAULT NULL::integer, p_aspect_ratios text[] DEFAULT NULL::text[], p_types text[] DEFAULT NULL::text[], p_platforms text[] DEFAULT NULL::text[], p_has_comments boolean DEFAULT NULL::boolean, p_min_likes integer DEFAULT NULL::integer, p_min_comments integer DEFAULT NULL::integer, p_min_favorites integer DEFAULT NULL::integer, p_min_shares integer DEFAULT NULL::integer, p_social_combine text DEFAULT 'and'::text, p_cursor_ts timestamp with time zone DEFAULT NULL::timestamp with time zone, p_cursor_id bigint DEFAULT NULL::bigint, p_limit integer DEFAULT 40, p_with_count boolean DEFAULT false) RETURNS jsonb
    LANGUAGE sql STABLE
    SET search_path TO 'public'
    AS $$
  WITH filtered AS (
    SELECT
      ri.id         AS item_id,
      ri.created_at AS created_at,
      to_jsonb(ri.*) || jsonb_build_object(
        'resource',
        to_jsonb(r.*) || jsonb_build_object(
          'media',
          CASE WHEN pm.id IS NOT NULL THEN jsonb_build_object(
            'id',             pm.id,
            'source_platform', pm.source_platform,
            'like_count',     pm.like_count,
            'comment_count',  pm.comment_count,
            'favorite_count', pm.favorite_count,
            'share_count',    pm.share_count
          ) ELSE NULL END
        )
      ) AS row
    FROM public.resource_items ri
    JOIN public.resources r
      ON r.id = ri.resource_id
    LEFT JOIN public.parsed_media pm
      ON pm.id = r.media_id
    WHERE
      -- Base filters (always applied)
      ri.scope_id = p_scope_id::bigint
      AND r.is_trashed = false
      AND r.source_type IS DISTINCT FROM 'web'

      -- Folder: explicit folder, else root (NULL)
      AND (
        (p_folder_id IS NOT NULL AND ri.folder_id = p_folder_id::bigint)
        OR (p_folder_id IS NULL AND ri.folder_id IS NULL)
      )

      -- Library: explicit library; if not personal + no library -> NULL only;
      -- personal with no library -> no constraint.
      AND (
        p_library_id IS NOT NULL AND ri.library_id = p_library_id::bigint
        OR (p_library_id IS NULL AND p_is_personal = false AND ri.library_id IS NULL)
        OR (p_library_id IS NULL AND p_is_personal IS DISTINCT FROM false)
      )

      -- Tag AND: resource must carry EVERY requested tag
      AND (
        p_tag_ids IS NULL
        OR cardinality(p_tag_ids) = 0
        OR r.id IN (
          SELECT rt.resource_id
          FROM public.resource_tags rt
          WHERE rt.tag_id = ANY(p_tag_ids::bigint[])
          GROUP BY rt.resource_id
          HAVING count(DISTINCT rt.tag_id) = cardinality(p_tag_ids)
        )
      )

      -- Resource-column filters
      AND (p_min_rating IS NULL OR r.rating >= p_min_rating)
      AND (p_ai_transcribed IS NULL OR p_ai_transcribed = false OR r.transcript_status = 'completed')
      AND (p_ai_summarized  IS NULL OR p_ai_summarized  = false OR r.summary_status = 'completed')
      AND (p_ai_analyzed    IS NULL OR p_ai_analyzed    = false OR r.visual_analysis_status = 'completed')
      AND (p_created_after  IS NULL OR r.created_at >= (p_created_after || 'T00:00:00')::timestamptz)
      AND (p_created_before IS NULL OR r.created_at <= (p_created_before || 'T23:59:59.999')::timestamptz)
      AND (p_duration_min IS NULL OR r.duration_seconds >= p_duration_min)
      AND (p_duration_max IS NULL OR r.duration_seconds <= p_duration_max)
      AND (p_aspect_ratios IS NULL OR r.aspect_bucket = ANY(p_aspect_ratios))

      -- Type filter: OR across the selected mime-type prefix groups
      AND (
        p_types IS NULL
        OR (
          ('video' = ANY(p_types) AND r.mime_type LIKE 'video/%')
          OR ('image' = ANY(p_types) AND r.mime_type LIKE 'image/%')
          OR ('audio' = ANY(p_types) AND r.mime_type LIKE 'audio/%')
          OR ('document' = ANY(p_types) AND (
                r.mime_type LIKE 'application/pdf%'
                OR r.mime_type LIKE 'application/msword%'
                OR r.mime_type LIKE 'application/vnd.%'
                OR r.mime_type LIKE 'text/%'
          ))
          OR ('other' = ANY(p_types) AND (
                r.mime_type NOT LIKE 'video/%'
                AND r.mime_type NOT LIKE 'image/%'
                AND r.mime_type NOT LIKE 'audio/%'
                AND r.mime_type NOT LIKE 'application/pdf%'
                AND r.mime_type NOT LIKE 'application/msword%'
                AND r.mime_type NOT LIKE 'application/vnd.%'
                AND r.mime_type NOT LIKE 'text/%'
          ))
        )
      )

      -- parsed_media-column filters
      AND (p_platforms IS NULL OR pm.source_platform = ANY(p_platforms))
      AND (p_has_comments IS NULL OR p_has_comments = false OR pm.comment_count > 0)

      -- Social thresholds: 'or' combine vs (default) 'and' combine
      AND (
        CASE
          WHEN p_social_combine = 'or' THEN (
            (p_min_likes IS NOT NULL AND p_min_likes > 0 AND pm.like_count >= p_min_likes)
            OR (p_min_comments IS NOT NULL AND p_min_comments > 0 AND pm.comment_count >= p_min_comments)
            OR (p_min_favorites IS NOT NULL AND p_min_favorites > 0 AND pm.favorite_count >= p_min_favorites)
            OR (p_min_shares IS NOT NULL AND p_min_shares > 0 AND pm.share_count >= p_min_shares)
            -- if no social threshold is set, the OR group must not exclude rows
            OR NOT (
              (p_min_likes IS NOT NULL AND p_min_likes > 0)
              OR (p_min_comments IS NOT NULL AND p_min_comments > 0)
              OR (p_min_favorites IS NOT NULL AND p_min_favorites > 0)
              OR (p_min_shares IS NOT NULL AND p_min_shares > 0)
            )
          )
          ELSE (
            (p_min_likes IS NULL OR p_min_likes = 0 OR pm.like_count >= p_min_likes)
            AND (p_min_comments IS NULL OR p_min_comments = 0 OR pm.comment_count >= p_min_comments)
            AND (p_min_favorites IS NULL OR p_min_favorites = 0 OR pm.favorite_count >= p_min_favorites)
            AND (p_min_shares IS NULL OR p_min_shares = 0 OR pm.share_count >= p_min_shares)
          )
        END
      )
  )
  SELECT jsonb_build_object(
    'rows',
    COALESCE(
      (
        SELECT jsonb_agg(page.row ORDER BY page.created_at DESC, page.item_id DESC)
        FROM (
          SELECT f.row, f.created_at, f.item_id
          FROM filtered f
          WHERE (
            p_cursor_ts IS NULL
            OR f.created_at < p_cursor_ts
            OR (f.created_at = p_cursor_ts AND f.item_id < p_cursor_id)
          )
          ORDER BY f.created_at DESC, f.item_id DESC
          LIMIT p_limit
        ) AS page
      ),
      '[]'::jsonb
    ),
    'total_count',
    CASE WHEN p_with_count THEN (SELECT count(*) FROM filtered) ELSE NULL END
  );
$$;


--
-- Name: search_smart_folder(text, jsonb, text, timestamp with time zone, bigint, integer, boolean); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.search_smart_folder(p_scope_id text, p_rules jsonb, p_search text DEFAULT NULL::text, p_cursor_ts timestamp with time zone DEFAULT NULL::timestamp with time zone, p_cursor_id bigint DEFAULT NULL::bigint, p_limit integer DEFAULT 40, p_with_count boolean DEFAULT false) RETURNS jsonb
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public'
    AS $$
DECLARE
  v_operator  text  := upper(coalesce(p_rules->>'operator', 'AND'));
  v_match     bool  := coalesce((p_rules->>'match')::bool, true);
  v_combiner  text  := CASE WHEN v_operator = 'OR' THEN ' OR ' ELSE ' AND ' END;
  v_cond      jsonb;
  v_field     text;
  v_op        text;
  v_value     text;
  v_col       text;
  v_type      text;
  v_pred      text;
  v_res_preds text[] := '{}';   -- resource-field conditions
  v_tag_preds text[] := '{}';   -- tag membership conditions
  v_groups    text[] := '{}';
  v_rule      text;
  v_where     text;   -- rule + search (no cursor) — used by the count
  v_sql       text;
  v_rows      jsonb;
  v_total     bigint := NULL;
BEGIN
  FOR v_cond IN SELECT * FROM jsonb_array_elements(coalesce(p_rules->'conditions', '[]'::jsonb))
  LOOP
    v_field := v_cond->>'field';
    v_op    := v_cond->>'op';
    v_value := v_cond->>'value';
    v_pred  := NULL;

    IF v_field = 'tags' THEN
      -- Case-insensitive tag membership (mirrors _filter_by_tags).
      IF v_op = 'contains' THEN
        v_pred := format(
          'EXISTS (SELECT 1 FROM resource_tags rt JOIN tags t ON t.id = rt.tag_id'
          || ' WHERE rt.resource_id = r.id AND lower(t.name) = lower(%L))', v_value);
      ELSIF v_op = 'not_contains' THEN
        v_pred := format(
          'NOT EXISTS (SELECT 1 FROM resource_tags rt JOIN tags t ON t.id = rt.tag_id'
          || ' WHERE rt.resource_id = r.id AND lower(t.name) = lower(%L))', v_value);
      END IF;
      IF v_pred IS NOT NULL THEN
        v_tag_preds := array_append(v_tag_preds, '(' || v_pred || ')');
      END IF;
    ELSE
      -- Allowlist: field → (column, cast-type). Unknown field → skip (parity).
      v_col := CASE v_field
        WHEN 'filename'         THEN 'r.filename'
        WHEN 'file_type'        THEN 'r.file_type'
        WHEN 'source_type'      THEN 'r.source_type'
        WHEN 'mime_type'        THEN 'r.mime_type'
        WHEN 'resolution'       THEN 'r.resolution'
        WHEN 'file_size_bytes'  THEN 'r.file_size_bytes'
        WHEN 'duration_seconds' THEN 'r.duration_seconds'
        WHEN 'created_at'       THEN 'r.created_at'
        ELSE NULL END;
      v_type := CASE v_field
        WHEN 'file_size_bytes'  THEN 'bigint'
        WHEN 'duration_seconds' THEN 'integer'
        WHEN 'created_at'       THEN 'timestamptz'
        ELSE 'text' END;

      IF v_col IS NOT NULL THEN
        v_pred := CASE v_op
          WHEN 'eq'          THEN format('%s = %L::%s', v_col, v_value, v_type)
          WHEN 'contains'    THEN format('%s ILIKE %L', v_col, '%' || v_value || '%')
          WHEN 'starts_with' THEN format('%s ILIKE %L', v_col, v_value || '%')
          WHEN 'gt'          THEN format('%s > %L::%s',  v_col, v_value, v_type)
          WHEN 'lt'          THEN format('%s < %L::%s',  v_col, v_value, v_type)
          WHEN 'gte'         THEN format('%s >= %L::%s', v_col, v_value, v_type)
          WHEN 'lte'         THEN format('%s <= %L::%s', v_col, v_value, v_type)
          WHEN 'in'          THEN format('%s::text = ANY(string_to_array(%L, %L))', v_col, v_value, ',')
          ELSE NULL END;
      END IF;
      IF v_pred IS NOT NULL THEN
        v_res_preds := array_append(v_res_preds, '(' || v_pred || ')');
      END IF;
    END IF;
  END LOOP;

  -- PARITY with execute_smart_rules: the legacy path combines resource
  -- conditions among themselves by `operator`, then *post-filters* the result
  -- by tag conditions (also combined by `operator`). So the resource-group and
  -- tag-group are ALWAYS intersected (AND), even when operator='OR'. Replicate
  -- that exactly: each group is OR/AND internally, the two groups are AND-ed.
  IF array_length(v_res_preds, 1) IS NOT NULL THEN
    v_groups := array_append(v_groups, '(' || array_to_string(v_res_preds, v_combiner) || ')');
  END IF;
  IF array_length(v_tag_preds, 1) IS NOT NULL THEN
    v_groups := array_append(v_groups, '(' || array_to_string(v_tag_preds, v_combiner) || ')');
  END IF;

  -- No valid conditions → match nothing (the endpoint already guards the
  -- empty-conditions case, so this is only hit by all-unknown rules).
  IF array_length(v_groups, 1) IS NULL THEN
    v_rule := 'false';
  ELSE
    v_rule := array_to_string(v_groups, ' AND ');
  END IF;

  -- Exclude mode: negate the whole matched predicate (replaces fetch-all-subtract).
  IF NOT v_match THEN
    v_rule := 'NOT (' || v_rule || ')';
  END IF;

  v_where := format('ri.scope_id = %L::bigint AND r.is_trashed = false AND (%s)',
                    p_scope_id, v_rule);

  IF p_search IS NOT NULL AND length(btrim(p_search)) > 0 THEN
    v_where := v_where || format(
      ' AND (r.filename ILIKE %L OR r.url ILIKE %L OR r.notes ILIKE %L)',
      '%' || p_search || '%', '%' || p_search || '%', '%' || p_search || '%');
  END IF;

  IF p_with_count THEN
    v_sql := format(
      'SELECT count(*) FROM resource_items ri JOIN resources r ON r.id = ri.resource_id WHERE %s',
      v_where);
    EXECUTE v_sql INTO v_total;
  END IF;

  -- Keyset cursor on (ri.created_at, ri.id) for the ROW page only.
  IF p_cursor_ts IS NOT NULL AND p_cursor_id IS NOT NULL THEN
    v_where := v_where || format(
      ' AND (ri.created_at < %L::timestamptz OR (ri.created_at = %L::timestamptz AND ri.id < %L::bigint))',
      p_cursor_ts, p_cursor_ts, p_cursor_id);
  END IF;

  v_sql := format(
    'SELECT coalesce(jsonb_agg(row ORDER BY ord_ts DESC, ord_id DESC), ''[]''::jsonb)'
    || ' FROM ('
    || '   SELECT ri.created_at AS ord_ts, ri.id AS ord_id,'
    || '          to_jsonb(ri.*) || jsonb_build_object(''resource'', to_jsonb(r.*)) AS row'
    || '   FROM resource_items ri JOIN resources r ON r.id = ri.resource_id'
    || '   WHERE %s'
    || '   ORDER BY ri.created_at DESC, ri.id DESC'
    || '   LIMIT %s'
    || ' ) s', v_where, p_limit);
  EXECUTE v_sql INTO v_rows;

  RETURN jsonb_build_object('rows', coalesce(v_rows, '[]'::jsonb), 'total_count', v_total);
END;
$$;


--
-- Name: set_invite_code(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.set_invite_code() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $$
BEGIN
  IF NEW.invite_code IS NULL THEN
    NEW.invite_code := generate_invite_code();
  END IF;
  RETURN NEW;
END;
$$;


--
-- Name: set_team_invite_code(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.set_team_invite_code() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $$
BEGIN
  IF NEW.code IS NULL THEN
    NEW.code := generate_team_invite_code();
  END IF;
  RETURN NEW;
END;
$$;


--
-- Name: skill_files_bump_parent_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.skill_files_bump_parent_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  UPDATE skills
     SET updated_at = now()
   WHERE id = COALESCE(NEW.skill_id, OLD.skill_id);
  RETURN COALESCE(NEW, OLD);
END;
$$;


--
-- Name: touch_dbos_workflow_routing_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.touch_dbos_workflow_routing_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;


--
-- Name: touch_issue_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.touch_issue_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;


--
-- Name: try_advisory_lock(bigint); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.try_advisory_lock(lock_key bigint) RETURNS boolean
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $$
  SELECT pg_try_advisory_lock(lock_key);
$$;


--
-- Name: update_agent_run_message_on_liveness(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_agent_run_message_on_liveness() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
BEGIN
  IF NEW.liveness_state IS NOT DISTINCT FROM OLD.liveness_state THEN
    RETURN NEW;
  END IF;
  IF NEW.issue_id IS NULL THEN
    RETURN NEW;
  END IF;

  UPDATE public.issue_messages
     SET meta = meta || jsonb_build_object(
           'liveness_state', NEW.liveness_state,
           'continuation_attempt', NEW.continuation_attempt
         )
   WHERE agent_run_id = NEW.id AND kind = 'agent_run';

  RETURN NEW;
END;
$$;


--
-- Name: FUNCTION update_agent_run_message_on_liveness(); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.update_agent_run_message_on_liveness() IS 'AFTER UPDATE OF liveness_state on agent_runs. Patches meta on the
   matching issue_messages row so the UI pill colour updates live as
   the scanner promotes running → silent → stuck without the run
   needing to terminate first.';


--
-- Name: update_daily_statistics(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_daily_statistics() RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $$
BEGIN
    NULL;
END;
$$;


--
-- Name: update_flow_aggregate(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_flow_aggregate() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_flow_id UUID;
    v_total INT;
    v_done  INT;
    v_failed INT;
    v_cancelled INT;
    v_state TEXT;
BEGIN
    v_flow_id := COALESCE(NEW.flow_id, OLD.flow_id);
    IF v_flow_id IS NULL THEN
        RETURN COALESCE(NEW, OLD);
    END IF;

    -- Recompute child status counts atomically. Doing it via a single
    -- SELECT FROM task_tracking is simpler than maintaining per-row
    -- delta arithmetic (which fails under race conditions / phase
    -- skips like queued -> failed without going through running).
    SELECT
        count(*),
        count(*) FILTER (WHERE phase = 'completed'),
        count(*) FILTER (WHERE phase IN ('failed', 'lost', 'timed_out')),
        count(*) FILTER (WHERE phase = 'cancelled')
    INTO v_total, v_done, v_failed, v_cancelled
    FROM public.task_tracking
    WHERE flow_id = v_flow_id;

    -- Derive the flow state from child terminal counts.
    -- Terminal: completed + failed + cancelled = total
    IF v_done + v_failed + v_cancelled < v_total THEN
        v_state := 'running';
    ELSIF v_failed > 0 AND v_done = 0 AND v_cancelled = 0 THEN
        v_state := 'failed';
    ELSIF v_cancelled > 0 AND v_done = 0 AND v_failed = 0 THEN
        v_state := 'cancelled';
    ELSIF v_done = v_total THEN
        v_state := 'completed';
    ELSE
        -- Mixed terminal — partial success.
        v_state := 'partial';
    END IF;

    UPDATE public.task_flows SET
        total_tasks     = v_total,
        completed_tasks = v_done,
        failed_tasks    = v_failed,
        cancelled_tasks = v_cancelled,
        state           = CASE
            -- Don't regress a manually-cancelled flow back to running.
            WHEN state = 'cancelled' AND v_state = 'running' THEN 'cancelled'
            ELSE v_state
        END,
        updated_at      = now(),
        completed_at    = CASE
            WHEN v_state IN ('completed', 'failed', 'cancelled', 'partial')
                AND completed_at IS NULL
            THEN now()
            ELSE completed_at
        END
    WHERE id = v_flow_id;

    RETURN COALESCE(NEW, OLD);
END;
$$;


--
-- Name: FUNCTION update_flow_aggregate(); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.update_flow_aggregate() IS 'Trigger function. Recomputes task_flows counters + derived state on
     any task_tracking row INSERT/UPDATE/DELETE that touches flow_id or
     phase. Atomic — uses a single SELECT to avoid delta race conditions.';


--
-- Name: update_flow_aggregate_stmt(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_flow_aggregate_stmt() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_flow_ids UUID[];
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT array_agg(DISTINCT flow_id)
          INTO v_flow_ids
          FROM new_rows
         WHERE flow_id IS NOT NULL;
    ELSIF TG_OP = 'DELETE' THEN
        SELECT array_agg(DISTINCT flow_id)
          INTO v_flow_ids
          FROM old_rows
         WHERE flow_id IS NOT NULL;
    ELSE  -- UPDATE: join old<->new on the PK (dbos_workflow_id), keep only
          -- rows whose phase or flow_id genuinely changed; a row may move
          -- between flows, so collect BOTH its old and new flow_id.
        SELECT array_agg(DISTINCT fid)
          INTO v_flow_ids
          FROM (
              SELECT n.flow_id AS fid
                FROM new_rows n
                JOIN old_rows o ON o.dbos_workflow_id = n.dbos_workflow_id
               WHERE n.phase   IS DISTINCT FROM o.phase
                  OR n.flow_id IS DISTINCT FROM o.flow_id
              UNION
              SELECT o.flow_id AS fid
                FROM new_rows n
                JOIN old_rows o ON o.dbos_workflow_id = n.dbos_workflow_id
               WHERE n.phase   IS DISTINCT FROM o.phase
                  OR n.flow_id IS DISTINCT FROM o.flow_id
          ) s
         WHERE fid IS NOT NULL;
    END IF;

    IF v_flow_ids IS NULL OR array_length(v_flow_ids, 1) IS NULL THEN
        RETURN NULL;
    END IF;

    UPDATE public.task_flows f SET
        total_tasks     = a.total,
        completed_tasks = a.done,
        failed_tasks    = a.failed,
        cancelled_tasks = a.cancelled,
        state = CASE
            WHEN f.state = 'cancelled' AND a.derived_state = 'running'
                THEN 'cancelled'
            ELSE a.derived_state
        END,
        updated_at = now(),
        completed_at = CASE
            WHEN a.derived_state IN ('completed', 'failed', 'cancelled', 'partial')
                 AND f.completed_at IS NULL
            THEN now()
            ELSE f.completed_at
        END
    FROM (
        SELECT
            t.flow_id,
            count(*)                                                    AS total,
            count(*) FILTER (WHERE t.phase = 'completed')               AS done,
            count(*) FILTER (WHERE t.phase IN ('failed', 'lost', 'timed_out')) AS failed,
            count(*) FILTER (WHERE t.phase = 'cancelled')               AS cancelled,
            CASE
                WHEN count(*) FILTER (WHERE t.phase = 'completed')
                   + count(*) FILTER (WHERE t.phase IN ('failed', 'lost', 'timed_out'))
                   + count(*) FILTER (WHERE t.phase = 'cancelled') < count(*)
                    THEN 'running'
                WHEN count(*) FILTER (WHERE t.phase IN ('failed', 'lost', 'timed_out')) > 0
                   AND count(*) FILTER (WHERE t.phase = 'completed') = 0
                   AND count(*) FILTER (WHERE t.phase = 'cancelled') = 0
                    THEN 'failed'
                WHEN count(*) FILTER (WHERE t.phase = 'cancelled') > 0
                   AND count(*) FILTER (WHERE t.phase = 'completed') = 0
                   AND count(*) FILTER (WHERE t.phase IN ('failed', 'lost', 'timed_out')) = 0
                    THEN 'cancelled'
                WHEN count(*) FILTER (WHERE t.phase = 'completed') = count(*)
                    THEN 'completed'
                ELSE 'partial'
            END AS derived_state
        FROM public.task_tracking t
        WHERE t.flow_id = ANY(v_flow_ids)
        GROUP BY t.flow_id
    ) a
    WHERE f.id = a.flow_id;

    RETURN NULL;
END;
$$;


--
-- Name: FUNCTION update_flow_aggregate_stmt(); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.update_flow_aggregate_stmt() IS 'Statement-level flow aggregate (mig 310, join fixed in mig 312:
     old<->new joined on dbos_workflow_id, the real task_tracking PK).';


--
-- Name: update_sb_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_sb_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


--
-- Name: update_script_chapters_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_script_chapters_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;


--
-- Name: update_script_projects_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_script_projects_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;


--
-- Name: update_system_status_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_system_status_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;


--
-- Name: update_unified_tasks_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_unified_tasks_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;


--
-- Name: update_updated_at_column(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_updated_at_column() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


--
-- Name: update_user_settings_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_user_settings_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


--
-- Name: update_video_analysis_timestamp(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_video_analysis_timestamp() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;


--
-- Name: update_video_view_stats(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_video_view_stats() RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $$
BEGIN
    UPDATE parsed_media pm
    SET
        view_count = stats.cnt,
        last_viewed_at = stats.last_view
    FROM (
        SELECT
            media_id,
            COUNT(*) as cnt,
            MAX(created_at) as last_view
        FROM media_access_logs
        WHERE action IN ('view', 'play')
        GROUP BY media_id
    ) stats
    WHERE pm.id = stats.media_id;
END;
$$;


--
-- Name: _scratch_dbos_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public._scratch_dbos_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: _scratch_test_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public._scratch_test_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: access_overrides; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.access_overrides (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    object_type character varying(20) NOT NULL,
    object_id text NOT NULL,
    user_id uuid NOT NULL,
    role character varying(20) NOT NULL,
    granted_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT access_overrides_object_type_check CHECK (((object_type)::text = ANY (ARRAY[('library'::character varying)::text, ('folder'::character varying)::text, ('project'::character varying)::text]))),
    CONSTRAINT access_overrides_role_check CHECK (((role)::text = ANY (ARRAY[('admin'::character varying)::text, ('editor'::character varying)::text, ('viewer'::character varying)::text, ('none'::character varying)::text])))
);


--
-- Name: admin_table_preferences; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_table_preferences (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    table_key text NOT NULL,
    filters jsonb DEFAULT '[]'::jsonb,
    sorts jsonb DEFAULT '[]'::jsonb,
    visible_columns text[],
    column_order text[],
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: agent_approval_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_approval_requests (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    agent_id uuid NOT NULL,
    session_id bigint,
    run_id bigint,
    hook_name text NOT NULL,
    reason text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    decided_at timestamp with time zone,
    decided_by uuid,
    decision_note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone DEFAULT (now() + '24:00:00'::interval) NOT NULL,
    CONSTRAINT agent_approval_requests_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'approved'::text, 'rejected'::text, 'expired'::text, 'cancelled'::text])))
);


--
-- Name: TABLE agent_approval_requests; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.agent_approval_requests IS 'G1: gating rows for hook decisions of type await_approval. The agent run pauses; the frontend renders pending rows; the user approves/rejects; a wake-up path resumes the workflow with the decision. Sweeper marks rows expired after expires_at.';


--
-- Name: agent_commitments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_commitments (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    agent_id uuid NOT NULL,
    user_id uuid,
    session_id uuid,
    description text NOT NULL,
    payload_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    trigger_type text NOT NULL,
    trigger_at timestamp with time zone,
    trigger_event text,
    status text DEFAULT 'pending'::text NOT NULL,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    fulfilled_at timestamp with time zone,
    fulfillment_notes text,
    fulfillment_run_id bigint,
    CONSTRAINT commitments_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'fulfilled'::text, 'cancelled'::text, 'expired'::text, 'failed'::text]))),
    CONSTRAINT commitments_trigger_data_check CHECK ((((trigger_type = 'time'::text) AND (trigger_at IS NOT NULL)) OR ((trigger_type = 'event'::text) AND (trigger_event IS NOT NULL)) OR (trigger_type = 'next_session'::text))),
    CONSTRAINT commitments_trigger_type_check CHECK ((trigger_type = ANY (ARRAY['time'::text, 'event'::text, 'next_session'::text])))
);


--
-- Name: TABLE agent_commitments; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.agent_commitments IS 'Cross-session followups (Sprint 4). Distinct from agent_memories (facts) — these are promises owed.';


--
-- Name: COLUMN agent_commitments.trigger_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.agent_commitments.trigger_type IS 'time = fire at trigger_at; event = fire on trigger_event publish; next_session = fire on next session open.';


--
-- Name: COLUMN agent_commitments.expires_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.agent_commitments.expires_at IS 'Auto-cancel after this. NULL = no auto-expire.';


--
-- Name: agent_inbox; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_inbox (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    recipient_agent_id uuid NOT NULL,
    sender_kind text NOT NULL,
    sender_user_id uuid,
    sender_agent_id uuid,
    message_type text NOT NULL,
    payload jsonb NOT NULL,
    status text DEFAULT 'unread'::text NOT NULL,
    priority smallint DEFAULT 5 NOT NULL,
    reading_claimed_at timestamp with time zone,
    reading_claimed_by text,
    task_id uuid,
    reply_to_message_id uuid,
    dedup_key text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    processed_at timestamp with time zone,
    expires_at timestamp with time zone,
    CONSTRAINT agent_inbox_message_type_check CHECK ((message_type = ANY (ARRAY['task'::text, 'question'::text, 'notification'::text, 'approval_request'::text, 'cancel'::text, 'status_query'::text]))),
    CONSTRAINT agent_inbox_sender_kind_check CHECK ((sender_kind = ANY (ARRAY['user'::text, 'agent'::text, 'system'::text, 'schedule'::text]))),
    CONSTRAINT agent_inbox_status_check CHECK ((status = ANY (ARRAY['unread'::text, 'reading'::text, 'processed'::text, 'dismissed'::text, 'expired'::text])))
);


--
-- Name: TABLE agent_inbox; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.agent_inbox IS 'M2 Persistent Workforce: incoming messages (user/agent/system/schedule).';


--
-- Name: COLUMN agent_inbox.task_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.agent_inbox.task_id IS 'UUID 形式的 task id，对应 task_tracking.dbos_workflow_id (TEXT) — 应用层 cast 后查询。FK 在 A4 (migration 200) 移除。';


--
-- Name: agent_memory; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_memory (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    scope text NOT NULL,
    owner_user_id uuid NOT NULL,
    team_id bigint,
    project_id bigint,
    agent_id uuid,
    session_id bigint,
    visibility text DEFAULT 'private'::text NOT NULL,
    kind text DEFAULT 'fact'::text NOT NULL,
    title text DEFAULT ''::text NOT NULL,
    body_md text DEFAULT ''::text NOT NULL,
    when_to_use text DEFAULT ''::text NOT NULL,
    fingerprint text DEFAULT ''::text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    reinforcement_count integer DEFAULT 0 NOT NULL,
    last_recalled_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    search_tsv tsvector GENERATED ALWAYS AS ((setweight(to_tsvector('english'::regconfig, ((COALESCE(title, ''::text) || ' '::text) || COALESCE(when_to_use, ''::text))), 'A'::"char") || setweight(to_tsvector('english'::regconfig, COALESCE(body_md, ''::text)), 'B'::"char"))) STORED,
    CONSTRAINT agent_memory_kind_check CHECK ((kind = ANY (ARRAY['fact'::text, 'decision'::text, 'preference'::text, 'procedure'::text]))),
    CONSTRAINT agent_memory_scope_check CHECK ((scope = ANY (ARRAY['session'::text, 'user'::text, 'agent_user'::text, 'project'::text, 'team'::text]))),
    CONSTRAINT agent_memory_shared_requires_team_id CHECK (((visibility = 'private'::text) OR (team_id IS NOT NULL))),
    CONSTRAINT agent_memory_status_check CHECK ((status = ANY (ARRAY['active'::text, 'archived'::text, 'superseded'::text]))),
    CONSTRAINT agent_memory_visibility_check CHECK ((visibility = ANY (ARRAY['private'::text, 'shared'::text])))
);


--
-- Name: agent_memory_promotions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_memory_promotions (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    memory_id bigint NOT NULL,
    proposed_scope text NOT NULL,
    target_team_id bigint NOT NULL,
    target_project_id bigint,
    classification_kind text DEFAULT 'fact'::text NOT NULL,
    confidence real DEFAULT 0 NOT NULL,
    justification text DEFAULT ''::text NOT NULL,
    scrubbed_body_md text DEFAULT ''::text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    reviewed_by uuid,
    reviewed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT agent_memory_promotions_proposed_scope_check CHECK ((proposed_scope = ANY (ARRAY['team'::text, 'project'::text]))),
    CONSTRAINT agent_memory_promotions_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'approved'::text, 'rejected'::text])))
);


--
-- Name: agent_outbox; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_outbox (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sender_agent_id uuid NOT NULL,
    recipient_kind text NOT NULL,
    recipient_user_id uuid,
    recipient_agent_id uuid,
    message_type text NOT NULL,
    payload jsonb NOT NULL,
    task_id uuid,
    delivered boolean DEFAULT false NOT NULL,
    delivered_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT agent_outbox_recipient_kind_check CHECK ((recipient_kind = ANY (ARRAY['user'::text, 'agent'::text, 'broadcast'::text])))
);


--
-- Name: TABLE agent_outbox; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.agent_outbox IS 'M2 Persistent Workforce: outgoing messages, Realtime delivery channel.';


--
-- Name: COLUMN agent_outbox.task_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.agent_outbox.task_id IS 'UUID 形式的 task id，对应 task_tracking.dbos_workflow_id (TEXT) — 应用层 cast 后查询。FK 在 A4 (migration 200) 移除。';


--
-- Name: agent_overrides; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_overrides (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    agent_id uuid NOT NULL,
    user_id uuid,
    team_id bigint,
    identity_md text,
    soul_md text,
    agent_md text,
    model text,
    temperature numeric,
    max_tokens integer,
    fallback_models text[],
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT agent_overrides_one_scope CHECK (((((user_id IS NOT NULL))::integer + ((team_id IS NOT NULL))::integer) = 1))
);


--
-- Name: agent_run_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_run_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    iteration smallint NOT NULL,
    tool_name text,
    tool_args_summary text,
    prompt_tokens_delta integer DEFAULT 0 NOT NULL,
    completion_tokens_delta integer DEFAULT 0 NOT NULL,
    cost_cents_delta numeric(10,6) DEFAULT 0 NOT NULL,
    duration_ms integer,
    hook_decisions jsonb DEFAULT '{}'::jsonb NOT NULL,
    model text,
    provider text,
    error_code text,
    error_message text,
    metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    run_id bigint NOT NULL,
    cost_snapshot jsonb,
    byok_key_id bigint,
    parent_run_id bigint
);


--
-- Name: COLUMN agent_run_events.cost_snapshot; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.agent_run_events.cost_snapshot IS 'Full per-call cost breakdown. Schema in 166 migration header.
   Written by adapter wrapper after each LLM call. NULL for legacy rows.';


--
-- Name: COLUMN agent_run_events.byok_key_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.agent_run_events.byok_key_id IS 'FK provider_byok_keys.id when caller used their own provider key.
   NULL = platform-billed call.';


--
-- Name: COLUMN agent_run_events.parent_run_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.agent_run_events.parent_run_id IS 'Snowflake ID of the parent agent_run when this iteration was triggered
   via Delegate / Tasklet from another run. NULL = top-level call.
   M2 will populate; M1.A leaves null.';


--
-- Name: agent_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_runs (
    agent_id uuid NOT NULL,
    user_id uuid NOT NULL,
    team_id bigint,
    project_id bigint,
    status text NOT NULL,
    cancel_requested boolean DEFAULT false NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    ended_at timestamp with time zone,
    heartbeat_at timestamp with time zone DEFAULT now() NOT NULL,
    model text,
    provider text,
    prompt_tokens integer DEFAULT 0 NOT NULL,
    completion_tokens integer DEFAULT 0 NOT NULL,
    total_tokens integer GENERATED ALWAYS AS ((prompt_tokens + completion_tokens)) STORED,
    prompt_cents_per_1k_snapshot numeric(12,6),
    completion_cents_per_1k_snapshot numeric(12,6),
    cost_cents numeric(12,6),
    trigger text NOT NULL,
    skill_slugs_used text[] DEFAULT '{}'::text[] NOT NULL,
    input_summary text,
    output_summary text,
    error_code text,
    error_message text,
    metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    agent_depth smallint DEFAULT 0 NOT NULL,
    delegation_reason text,
    issue_id bigint,
    liveness_state text DEFAULT 'running'::text NOT NULL,
    last_useful_action_at timestamp with time zone,
    continuation_attempt integer DEFAULT 0 NOT NULL,
    output_silence_bytes bigint DEFAULT 0 NOT NULL,
    liveness_changed_at timestamp with time zone,
    session_id bigint,
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    parent_run_id bigint,
    root_run_id bigint,
    cached_input_tokens integer DEFAULT 0 NOT NULL,
    outcome text,
    task_id text,
    conversation_id bigint,
    CONSTRAINT agent_runs_liveness_state_check CHECK ((liveness_state = ANY (ARRAY['running'::text, 'silent'::text, 'stuck'::text, 'dead'::text, 'cancelled'::text]))),
    CONSTRAINT agent_runs_outcome_check CHECK (((outcome IS NULL) OR (outcome = ANY (ARRAY['pending'::text, 'user_accepted'::text, 'user_rejected'::text, 'timeout'::text])))),
    CONSTRAINT agent_runs_status_check CHECK ((status = ANY (ARRAY['running'::text, 'completed'::text, 'failed'::text, 'cancelled'::text, 'heartbeat_lost'::text])))
);


--
-- Name: COLUMN agent_runs.issue_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.agent_runs.issue_id IS 'Optional FK back to issues. Set when an agent run was dispatched
   from an issue (paperclip-style "reply triggers agent"). Surfaced
   in issue_messages.kind=agent_run for the chat thread.';


--
-- Name: COLUMN agent_runs.liveness_state; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.agent_runs.liveness_state IS 'paperclip-style 5-state liveness orthogonal to status.
   running→silent→stuck→dead transitions are driven by the scanner
   in app/workflows/liveness_scanner.py based on heartbeat_at +
   last_useful_action_at + output_silence_bytes thresholds.';


--
-- Name: COLUMN agent_runs.last_useful_action_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.agent_runs.last_useful_action_at IS 'Last time the agent made observable progress (new output bytes,
   completed step, written file, etc). Distinct from heartbeat_at —
   heartbeat says "process alive," last_useful_action_at says
   "actually doing work." Set by the runtime, not the scanner.';


--
-- Name: COLUMN agent_runs.continuation_attempt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.agent_runs.continuation_attempt IS 'Number of times the scanner has tried to revive this run from
   stuck. Bumped by the scanner; capped to prevent infinite loops.';


--
-- Name: COLUMN agent_runs.output_silence_bytes; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.agent_runs.output_silence_bytes IS 'Snapshot of how much output the run had at the last scanner pass
   it was found stuck. If the next pass finds the same byte count,
   stuck → dead.';


--
-- Name: COLUMN agent_runs.outcome; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.agent_runs.outcome IS 'User-visible verdict on this agent run. NULL = legacy / never marked.
   Front-end writes user_accepted / user_rejected. timeout written by
   sweeper. Drives Cost-per-Outcome admin dashboard view.';


--
-- Name: agent_skills; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_skills (
    agent_id uuid NOT NULL,
    skill_id bigint NOT NULL,
    sort_order integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now(),
    enabled boolean DEFAULT true
);


--
-- Name: TABLE agent_skills; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.agent_skills IS 'Binds agents to their available skills (lazy-readable skill injection)';


--
-- Name: COLUMN agent_skills.enabled; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.agent_skills.enabled IS 'Per-binding toggle to disable a skill on an agent without removing the row';


--
-- Name: agent_state_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_state_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    agent_id uuid NOT NULL,
    from_state text,
    to_state text NOT NULL,
    trigger text NOT NULL,
    task_id uuid,
    metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    changed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE agent_state_history; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.agent_state_history IS 'M2 Persistent Workforce: audit trail for state machine transitions.';


--
-- Name: COLUMN agent_state_history.task_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.agent_state_history.task_id IS 'UUID 形式的 task id，对应 task_tracking.dbos_workflow_id (TEXT) — 应用层 cast 后查询。FK 在 A4 (migration 200) 移除。';


--
-- Name: agent_tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_tasks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    agent_id uuid NOT NULL,
    parent_task_id uuid,
    root_task_id uuid,
    inbox_message_id uuid,
    user_id uuid NOT NULL,
    title text,
    payload jsonb NOT NULL,
    lifecycle_status text DEFAULT 'queued'::text NOT NULL,
    result jsonb,
    error_code text,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    assigned_at timestamp with time zone,
    started_at timestamp with time zone,
    ended_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    issue_id bigint,
    current_run_id bigint,
    CONSTRAINT agent_tasks_lifecycle_status_check CHECK ((lifecycle_status = ANY (ARRAY['queued'::text, 'assigned'::text, 'in_progress'::text, 'waiting_for_other'::text, 'blocked'::text, 'done'::text, 'failed'::text, 'cancelled'::text])))
);


--
-- Name: TABLE agent_tasks; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.agent_tasks IS 'M2 Persistent Workforce: per-agent task queue with lifecycle state machine.';


--
-- Name: COLUMN agent_tasks.issue_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.agent_tasks.issue_id IS 'Back-reference to issues.id. agent_tasks rows that originate from a chat_delegate or agent_dispatch issue link back here.';


--
-- Name: agent_workers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_workers (
    agent_id uuid NOT NULL,
    state text NOT NULL,
    current_task_id uuid,
    worker_pid integer,
    worker_hostname text,
    heartbeat_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    state_changed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT agent_workers_state_check CHECK ((state = ANY (ARRAY['idle'::text, 'working'::text, 'waiting_for_other'::text, 'blocked'::text, 'paused'::text, 'terminated'::text])))
);


--
-- Name: TABLE agent_workers; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.agent_workers IS 'M2 Persistent Workforce: one row per agent runtime state.';


--
-- Name: ai_agent_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_agent_versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    agent_id uuid NOT NULL,
    version_number integer NOT NULL,
    identity_md text,
    soul_md text,
    agent_md text,
    model text,
    temperature double precision,
    max_tokens integer,
    notes text,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ai_agents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_agents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    description text,
    model text DEFAULT 'qwen-max'::text,
    temperature numeric DEFAULT 0.7,
    max_tokens integer DEFAULT 4096,
    team_id bigint,
    project_id bigint,
    created_by uuid,
    enabled boolean DEFAULT true,
    sort_order integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    slug character varying(64),
    identity_md text,
    soul_md text,
    agent_md text,
    is_system_preset boolean DEFAULT false,
    user_id uuid,
    current_version integer DEFAULT 1 NOT NULL,
    icon text,
    monthly_token_budget integer,
    monthly_cost_cents_budget numeric(12,6),
    paused_reason text,
    budget_per_run_cents numeric(8,2) DEFAULT 50.0,
    fallback_models text[] DEFAULT '{}'::text[] NOT NULL,
    persistent boolean DEFAULT false NOT NULL,
    memory_injection_top_n integer,
    seed_hash text,
    timeout_sec integer,
    max_concurrent_runs integer,
    capability_profile jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT ai_agents_max_concurrent_runs_check CHECK (((max_concurrent_runs IS NULL) OR (max_concurrent_runs >= 1))),
    CONSTRAINT ai_agents_memory_injection_top_n_check CHECK (((memory_injection_top_n IS NULL) OR ((memory_injection_top_n >= 0) AND (memory_injection_top_n <= 50)))),
    CONSTRAINT ai_agents_paused_reason_check CHECK (((paused_reason IS NULL) OR (paused_reason = ANY (ARRAY['budget'::text, 'manual'::text])))),
    CONSTRAINT ai_agents_timeout_sec_check CHECK (((timeout_sec IS NULL) OR (timeout_sec >= 0)))
);


--
-- Name: TABLE ai_agents; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.ai_agents IS 'AI Agent definitions with personas and configurations';


--
-- Name: COLUMN ai_agents.slug; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ai_agents.slug IS 'Stable identifier (e.g. script_ai, summarize); unique per scope';


--
-- Name: COLUMN ai_agents.identity_md; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ai_agents.identity_md IS 'Markdown: who the agent is (static persona)';


--
-- Name: COLUMN ai_agents.soul_md; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ai_agents.soul_md IS 'Markdown: core values / tone / voice';


--
-- Name: COLUMN ai_agents.agent_md; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ai_agents.agent_md IS 'Markdown: operating instructions / capabilities';


--
-- Name: COLUMN ai_agents.is_system_preset; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ai_agents.is_system_preset IS 'true = platform-managed preset, false = user-created';


--
-- Name: COLUMN ai_agents.budget_per_run_cents; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ai_agents.budget_per_run_cents IS 'Per-run BudgetGuard threshold in cents. NULL = unlimited.';


--
-- Name: COLUMN ai_agents.fallback_models; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ai_agents.fallback_models IS 'Ordered fallback model chain. Adapter walks this after primary exhausts retries.';


--
-- Name: COLUMN ai_agents.memory_injection_top_n; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ai_agents.memory_injection_top_n IS 'Phase M M3.D: per-agent override for retriever top_n. NULL = use code default (5).';


--
-- Name: COLUMN ai_agents.seed_hash; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ai_agents.seed_hash IS 'sha256 of IDENTITY.md + SOUL.md + AGENT.md + frontmatter; null = always re-upsert';


--
-- Name: COLUMN ai_agents.capability_profile; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ai_agents.capability_profile IS 'Phase 4.5 capability gating + Team Chat PHASE-0. Keys: tool_blacklist, allowed_skills, max_parallel_delegates, context_budget_tokens, rate_limit_tool_calls_per_min, chat{enabled,read_team_resources,auto_broadcast,allowed_team_ids}. Empty/absent = ungated EXCEPT chat, which is fail-closed (absent = cannot chat).';


--
-- Name: ai_model_prices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_model_prices (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    model text NOT NULL,
    provider text NOT NULL,
    prompt_cents_per_1k numeric(12,6) NOT NULL,
    completion_cents_per_1k numeric(12,6) NOT NULL,
    effective_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    supports_vision boolean DEFAULT false NOT NULL,
    cached_input_cents_per_1k numeric
);


--
-- Name: COLUMN ai_model_prices.supports_vision; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ai_model_prices.supports_vision IS 'TRUE if the model accepts image_url multipart parts (OpenAI multimodal shape). Read by app.services.ai.model_capabilities.model_supports_vision.';


--
-- Name: ai_session_memory; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_session_memory (
    body_md text DEFAULT ''::text NOT NULL,
    sections_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    last_updated_at timestamp with time zone DEFAULT now() NOT NULL,
    tokens_at_last_update integer DEFAULT 0 NOT NULL,
    tool_calls_at_last_update integer DEFAULT 0 NOT NULL,
    turns_at_last_update integer DEFAULT 0 NOT NULL,
    session_id bigint NOT NULL
);


--
-- Name: TABLE ai_session_memory; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.ai_session_memory IS 'Wave 5b: continuously-maintained session notes with fixed schema. Replaces compactor''s one-shot summary at compaction time.';


--
-- Name: COLUMN ai_session_memory.body_md; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ai_session_memory.body_md IS 'Markdown source of truth. Updated by background updater on dual-threshold trigger.';


--
-- Name: COLUMN ai_session_memory.sections_json; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ai_session_memory.sections_json IS 'Parsed-out sections for query/admin: {title, current_state, task_spec, key_files, workflow_steps, errors_and_fixes}.';


--
-- Name: COLUMN ai_session_memory.version; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ai_session_memory.version IS 'Optimistic concurrency: bumped on each successful update.';


--
-- Name: ai_usage_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_usage_logs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    team_id bigint,
    project_id bigint,
    session_id uuid,
    agent_id uuid,
    action text,
    model text NOT NULL,
    prompt_tokens integer NOT NULL,
    completion_tokens integer NOT NULL,
    total_tokens integer GENERATED ALWAYS AS ((prompt_tokens + completion_tokens)) STORED,
    cost_points numeric,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: TABLE ai_usage_logs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.ai_usage_logs IS 'Token consumption tracking for billing';


--
-- Name: alert_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alert_history (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    rule_id bigint NOT NULL,
    rule_name character varying(255) NOT NULL,
    metric_type character varying(50) NOT NULL,
    metric_value double precision NOT NULL,
    threshold double precision NOT NULL,
    condition character varying(10) NOT NULL,
    message text NOT NULL,
    notified boolean DEFAULT false NOT NULL,
    resolved boolean DEFAULT false NOT NULL,
    resolved_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: alert_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alert_rules (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    name character varying(255) NOT NULL,
    metric_type character varying(50) NOT NULL,
    condition character varying(10) NOT NULL,
    threshold double precision NOT NULL,
    window_minutes integer DEFAULT 5 NOT NULL,
    notification_channel character varying(50) DEFAULT 'discord'::character varying NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    is_muted boolean DEFAULT false NOT NULL,
    mute_until timestamp with time zone,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: api_key_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.api_key_logs (
    id bigint NOT NULL,
    api_key_id bigint NOT NULL,
    endpoint character varying(255) NOT NULL,
    method character varying(10) NOT NULL,
    ip_address inet,
    user_agent text,
    status_code integer,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: TABLE api_key_logs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.api_key_logs IS 'API 密钥使用日志表';


--
-- Name: api_key_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.api_key_logs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: api_key_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.api_key_logs_id_seq OWNED BY public.api_key_logs.id;


--
-- Name: api_keys; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.api_keys (
    id bigint NOT NULL,
    key_id character varying(32) NOT NULL,
    key_hash character varying(64) NOT NULL,
    key_prefix character varying(20) NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    user_id uuid NOT NULL,
    scopes jsonb DEFAULT '[]'::jsonb NOT NULL,
    status public.api_key_status DEFAULT 'active'::public.api_key_status NOT NULL,
    expires_at timestamp with time zone,
    last_used_at timestamp with time zone,
    usage_count integer DEFAULT 0,
    rate_limit integer,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    key_value character varying
);


--
-- Name: TABLE api_keys; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.api_keys IS 'API 密钥管理表';


--
-- Name: COLUMN api_keys.key_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.api_keys.key_id IS '密钥公开标识符';


--
-- Name: COLUMN api_keys.key_hash; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.api_keys.key_hash IS '密钥 SHA-256 哈希值';


--
-- Name: COLUMN api_keys.key_prefix; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.api_keys.key_prefix IS '密钥前缀，用于用户识别';


--
-- Name: COLUMN api_keys.scopes; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.api_keys.scopes IS '权限范围 JSON 数组';


--
-- Name: api_keys_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.api_keys_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: api_keys_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.api_keys_id_seq OWNED BY public.api_keys.id;


--
-- Name: api_request_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.api_request_logs (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    request_id character varying(36) NOT NULL,
    user_id uuid,
    auth_type character varying(20) DEFAULT 'anonymous'::character varying,
    method character varying(10) NOT NULL,
    path character varying(500) NOT NULL,
    query_params jsonb,
    request_body jsonb,
    status_code integer,
    response_time_ms integer,
    ip_address character varying(45),
    user_agent text,
    error_detail text,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: application_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.application_logs (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    level character varying(10) NOT NULL,
    message text NOT NULL,
    module character varying(255),
    function character varying(255),
    line integer,
    file_path character varying(500),
    exception text,
    extra jsonb DEFAULT '{}'::jsonb,
    logged_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: audit_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_logs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    admin_id uuid NOT NULL,
    action character varying(100) NOT NULL,
    target_type character varying(50) NOT NULL,
    target_id character varying(255) NOT NULL,
    details jsonb,
    ip_address character varying(45),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE audit_logs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.audit_logs IS 'Audit trail for admin actions';


--
-- Name: COLUMN audit_logs.admin_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.audit_logs.admin_id IS 'Admin who performed the action';


--
-- Name: COLUMN audit_logs.action; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.audit_logs.action IS 'Action performed (e.g., ban_user, adjust_credits)';


--
-- Name: COLUMN audit_logs.target_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.audit_logs.target_type IS 'Type of target entity (e.g., user, video, setting)';


--
-- Name: COLUMN audit_logs.target_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.audit_logs.target_id IS 'ID of the target entity';


--
-- Name: COLUMN audit_logs.details; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.audit_logs.details IS 'Additional details about the action';


--
-- Name: COLUMN audit_logs.ip_address; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.audit_logs.ip_address IS 'IP address of the admin';


--
-- Name: parsed_media; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.parsed_media (
    platform_id character varying(255) NOT NULL,
    like_count integer DEFAULT 0,
    comment_count integer DEFAULT 0,
    share_count integer DEFAULT 0,
    favorite_count integer DEFAULT 0,
    original_url character varying(512) NOT NULL,
    duration character varying(50),
    resolution character varying(50),
    datasize character varying(50),
    hashtags text,
    published_at timestamp with time zone,
    author character varying(255),
    title text,
    media_type character varying(50),
    description text,
    video_download_urls jsonb DEFAULT '[]'::jsonb,
    image_download_urls jsonb DEFAULT '[]'::jsonb,
    music_name character varying(255),
    video_download_status public.download_status DEFAULT 'pending'::public.download_status,
    music_download_status public.download_status DEFAULT 'pending'::public.download_status,
    download_duration double precision,
    download_path text,
    error_message text,
    download_time timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    cover_urls jsonb DEFAULT '[]'::jsonb,
    dynamic_cover_url text,
    cover_download_status public.download_status DEFAULT 'pending'::public.download_status,
    cover_download_path text,
    ai_extract_text text,
    ai_rewrite_text text,
    ai_analyze_text text,
    ai_generated_at timestamp with time zone,
    view_count integer DEFAULT 0,
    last_viewed_at timestamp with time zone,
    storage_size bigint,
    keep_forever boolean DEFAULT false,
    datasize_bytes bigint DEFAULT 0,
    source_platform character varying(50) DEFAULT 'douyin'::character varying NOT NULL,
    hls_path text,
    media_format character varying(10) DEFAULT 'mp4'::character varying,
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    image_download_status public.download_status DEFAULT 'skipped'::public.download_status,
    image_download_path text,
    music_download_path text,
    music_play_urls jsonb DEFAULT '[]'::jsonb,
    extract_audio_path text,
    extract_audio_status text DEFAULT 'pending'::text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb,
    download_retry_count integer DEFAULT 0 NOT NULL,
    CONSTRAINT parsed_media_extract_audio_status_check CHECK ((extract_audio_status = ANY (ARRAY['pending'::text, 'processing'::text, 'completed'::text, 'failed'::text, 'skipped'::text])))
);

ALTER TABLE ONLY public.parsed_media REPLICA IDENTITY FULL;


--
-- Name: TABLE parsed_media; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.parsed_media IS 'Main media table (renamed from videos). PK is BIGINT Snowflake ID.';


--
-- Name: COLUMN parsed_media.platform_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.parsed_media.platform_id IS 'Unique identifier on the source platform (was aweme_id)';


--
-- Name: COLUMN parsed_media.media_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.parsed_media.media_type IS 'Content type: video, carousel, image_text, special, short, live_clip';


--
-- Name: COLUMN parsed_media.ai_extract_text; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.parsed_media.ai_extract_text IS 'AI extracted summary from video content';


--
-- Name: COLUMN parsed_media.ai_rewrite_text; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.parsed_media.ai_rewrite_text IS 'AI rewritten video description';


--
-- Name: COLUMN parsed_media.ai_analyze_text; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.parsed_media.ai_analyze_text IS 'AI content analysis result';


--
-- Name: COLUMN parsed_media.ai_generated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.parsed_media.ai_generated_at IS 'Timestamp when AI content was last generated';


--
-- Name: COLUMN parsed_media.datasize_bytes; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.parsed_media.datasize_bytes IS 'Raw video file size in bytes for aggregation queries';


--
-- Name: COLUMN parsed_media.source_platform; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.parsed_media.source_platform IS 'Source platform: douyin, youtube, bilibili, twitter, other';


--
-- Name: COLUMN parsed_media.hls_path; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.parsed_media.hls_path IS 'HLS playlist path relative to downloads directory';


--
-- Name: COLUMN parsed_media.media_format; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.parsed_media.media_format IS 'Storage format: mp4 (legacy) or hls (new)';


--
-- Name: COLUMN parsed_media.id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.parsed_media.id IS 'Snowflake BIGINT primary key (was display_id, originally UUID)';


--
-- Name: COLUMN parsed_media.music_download_path; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.parsed_media.music_download_path IS 'Relative path to downloaded music file';


--
-- Name: COLUMN parsed_media.music_play_urls; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.parsed_media.music_play_urls IS 'Standalone music play URLs for carousel/image-text content (type 2/68). List of fallback URLs from music.play_url.url_list.';


--
-- Name: COLUMN parsed_media.extract_audio_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.parsed_media.extract_audio_status IS 'Status of the extract_audio_workflow for this media: pending (not run
   yet) / processing / completed / failed / skipped (no video to extract
   audio from, e.g. image carousels). Distinct from music_download_status
   (which tracks downloaded music URLs). Drives the MediaCard audio icon.';


--
-- Name: author_statistics; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.author_statistics WITH (security_invoker='true') AS
 SELECT author,
    count(*) AS video_count,
    sum(like_count) AS total_likes,
    sum(comment_count) AS total_comments,
    avg(like_count) AS avg_likes
   FROM public.parsed_media
  WHERE (author IS NOT NULL)
  GROUP BY author
  ORDER BY (count(*)) DESC;


--
-- Name: authors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.authors (
    id bigint NOT NULL,
    author_id character varying(64),
    nickname character varying(255) NOT NULL,
    signature text,
    avatar_url text,
    follower_count integer DEFAULT 0,
    following_count integer DEFAULT 0,
    total_favorited integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: authors_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.authors_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: authors_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.authors_id_seq OWNED BY public.authors.id;


--
-- Name: boundary_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.boundary_audit (
    id bigint NOT NULL,
    blocked_at timestamp with time zone DEFAULT now() NOT NULL,
    layer text NOT NULL,
    reason text NOT NULL,
    raw_url text,
    resolved_ip text,
    user_id bigint,
    request_id text,
    metadata_json jsonb DEFAULT '{}'::jsonb
);


--
-- Name: TABLE boundary_audit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.boundary_audit IS 'Boundary layer block audit log. Server-side only; raw_url never echoed to client. Sweeper auto-prunes >90d (B9-G follow-up).';


--
-- Name: boundary_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.boundary_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: boundary_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.boundary_audit_id_seq OWNED BY public.boundary_audit.id;


--
-- Name: canvas_resource_refs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.canvas_resource_refs (
    canvas_id bigint NOT NULL,
    resource_id bigint NOT NULL,
    role text NOT NULL,
    node_id text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT canvas_resource_refs_role_check CHECK ((role = ANY (ARRAY['reference'::text, 'output'::text])))
);


--
-- Name: TABLE canvas_resource_refs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.canvas_resource_refs IS 'IC-port P4. One row per (canvas, resource, node) reference, extracted
   from canvases.nodes_json on save. role=reference (shot node ref image)
   or output (prompt-run rendered artifact). Derived/rebuildable — backfill
   script can reconstruct from nodes_json at any time.';


--
-- Name: canvases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.canvases (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    project_id bigint NOT NULL,
    name character varying(200) DEFAULT 'Untitled'::character varying NOT NULL,
    kind text DEFAULT 'smart'::text NOT NULL,
    viewport_json jsonb DEFAULT '{"x": 0, "y": 0, "zoom": 1}'::jsonb NOT NULL,
    nodes_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    connections_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    node_ops_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    connection_ops_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    base_updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid,
    deleted_at timestamp with time zone,
    CONSTRAINT canvases_kind_check CHECK ((kind = ANY (ARRAY['smart'::text, 'lite'::text, 'classic'::text, 'character'::text, 'location'::text, 'prop'::text])))
);

ALTER TABLE ONLY public.canvases REPLICA IDENTITY FULL;


--
-- Name: TABLE canvases; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.canvases IS 'Phase 1 Day 1. One canvas = one editing surface inside a project.
   Smart kind = Infinite-Canvas; classic kind = legacy block-editor.
   nodes_json/connections_json hold derived state for fast cold reads;
   node_ops_json/connection_ops_json are append-only ops, kept as the
   CRDT escape hatch (future Yjs swap-in without schema change).';


--
-- Name: COLUMN canvases.node_ops_json; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.canvases.node_ops_json IS 'Append-only ops log. Today derived state is the source of truth at
   render time; this is kept so a future Yjs/CRDT swap-in does not need
   a schema migration. See plan v1.2 §3 CRDT escape hatch.';


--
-- Name: COLUMN canvases.base_updated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.canvases.base_updated_at IS 'Optimistic-lock token. PUT requests must echo the value they read;
   mismatch → 409 conflict (handled by Phase 1 Week 3 frontend).';


--
-- Name: COLUMN canvases.deleted_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.canvases.deleted_at IS 'Soft-delete stamp (G9 trash). NULL = live. Set by DELETE /canvases/{id}, cleared by restore, row removed for real by purge.';


--
-- Name: collections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.collections (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    owner_id uuid,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    team_id bigint
);

ALTER TABLE ONLY public.collections REPLICA IDENTITY FULL;


--
-- Name: TABLE collections; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.collections IS 'User/team video collections. PK migrated to Snowflake BIGINT in 060.';


--
-- Name: conversation_ai_meta; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversation_ai_meta (
    conversation_id bigint NOT NULL,
    agent_slug text NOT NULL,
    agent_id uuid,
    total_tokens bigint DEFAULT 0 NOT NULL,
    message_count integer DEFAULT 0 NOT NULL,
    context_type text,
    context_id text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE conversation_ai_meta; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.conversation_ai_meta IS 'AI-session decoration for direct_agent conversations (agent binding, token/message counters, client grouping hints). Phase 2 sidecar; one row per direct_agent conversation. Backend-only (service-role RLS).';


--
-- Name: conversation_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversation_members (
    conversation_id bigint NOT NULL,
    member_type text NOT NULL,
    user_id uuid,
    agent_id uuid,
    role text DEFAULT 'member'::text NOT NULL,
    last_read_seq bigint DEFAULT 0 NOT NULL,
    mention_count integer DEFAULT 0 NOT NULL,
    notify_level text DEFAULT 'all'::text NOT NULL,
    open boolean DEFAULT true NOT NULL,
    added_by uuid,
    joined_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT conversation_members_member_type_check CHECK ((member_type = ANY (ARRAY['user'::text, 'agent'::text]))),
    CONSTRAINT conversation_members_notify_level_check CHECK ((notify_level = ANY (ARRAY['all'::text, 'mentions'::text, 'none'::text]))),
    CONSTRAINT conversation_members_one_id CHECK ((((member_type = 'user'::text) AND (user_id IS NOT NULL) AND (agent_id IS NULL)) OR ((member_type = 'agent'::text) AND (agent_id IS NOT NULL) AND (user_id IS NULL))))
);

ALTER TABLE ONLY public.conversation_members REPLICA IDENTITY FULL;


--
-- Name: conversation_memory; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversation_memory (
    conversation_id bigint NOT NULL,
    summary_md text DEFAULT ''::text NOT NULL,
    last_seq_summarized bigint DEFAULT 0 NOT NULL,
    model text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE conversation_memory; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.conversation_memory IS 'Rolling compressed summary of a conversation''s older messages (head). Agents read summary + recent tail; compaction advances last_seq_summarized. Backend-only (service-role RLS).';


--
-- Name: conversations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversations (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    type text NOT NULL,
    scope_id bigint NOT NULL,
    project_id bigint,
    title text,
    topic text,
    history_mode text DEFAULT 'shared'::text NOT NULL,
    last_seq bigint DEFAULT 0 NOT NULL,
    created_by uuid NOT NULL,
    archived_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT conversations_history_mode_check CHECK ((history_mode = ANY (ARRAY['shared'::text, 'joined'::text])))
);


--
-- Name: cost_audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cost_audit_log (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    entity_type text NOT NULL,
    entity_id text NOT NULL,
    action text NOT NULL,
    changed_by uuid,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    before jsonb,
    after jsonb,
    reason text,
    CONSTRAINT cost_audit_log_action_check CHECK ((action = ANY (ARRAY['create'::text, 'update'::text, 'invalidate'::text, 'delete'::text])))
);


--
-- Name: TABLE cost_audit_log; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.cost_audit_log IS 'Compliance audit trail for price/contract/credit changes. Append-only.
   Future: tighten with trigger to capture all writes automatically.';


--
-- Name: credit_pricing; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.credit_pricing (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    action character varying(100) NOT NULL,
    cost integer DEFAULT 0 NOT NULL,
    description text,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE credit_pricing; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.credit_pricing IS 'Configurable pricing for different actions';


--
-- Name: COLUMN credit_pricing.action; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.credit_pricing.action IS 'Action identifier (e.g., parse, download)';


--
-- Name: COLUMN credit_pricing.cost; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.credit_pricing.cost IS 'Credit cost for this action';


--
-- Name: COLUMN credit_pricing.is_active; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.credit_pricing.is_active IS 'Whether this pricing rule is active';


--
-- Name: credit_transactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.credit_transactions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    amount integer NOT NULL,
    type character varying(50) NOT NULL,
    description text,
    related_id character varying(255),
    admin_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT credit_transactions_type_check CHECK (((type)::text = ANY (ARRAY[('recharge'::character varying)::text, ('consume'::character varying)::text, ('refund'::character varying)::text, ('gift'::character varying)::text, ('adjustment'::character varying)::text])))
);


--
-- Name: TABLE credit_transactions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.credit_transactions IS 'Log of all credit transactions';


--
-- Name: COLUMN credit_transactions.type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.credit_transactions.type IS 'Transaction type: recharge, consume, refund, gift, adjustment';


--
-- Name: COLUMN credit_transactions.related_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.credit_transactions.related_id IS 'Related entity ID (e.g., video aweme_id for consumption)';


--
-- Name: COLUMN credit_transactions.admin_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.credit_transactions.admin_id IS 'Admin who performed the action (for adjustments/gifts)';


--
-- Name: daily_point_gifts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.daily_point_gifts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    team_id bigint NOT NULL,
    gift_date date NOT NULL,
    amount_granted integer DEFAULT 0 NOT NULL,
    amount_consumed integer DEFAULT 0 NOT NULL,
    amount_reclaimed integer DEFAULT 0 NOT NULL,
    status text DEFAULT 'granted'::text NOT NULL,
    granted_at timestamp with time zone DEFAULT now() NOT NULL,
    reclaimed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT daily_point_gifts_status_check CHECK ((status = ANY (ARRAY['granted'::text, 'reclaimed'::text])))
);


--
-- Name: daily_statistics; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.daily_statistics WITH (security_invoker='true') AS
 SELECT date(created_at) AS date,
    count(*) AS videos_added,
    count(*) FILTER (WHERE (video_download_status = 'completed'::public.download_status)) AS videos_downloaded
   FROM public.parsed_media
  GROUP BY (date(created_at))
  ORDER BY (date(created_at)) DESC;


--
-- Name: dbos_workflow_routing; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dbos_workflow_routing (
    task_type text NOT NULL,
    mode text DEFAULT 'celery'::text NOT NULL,
    notes text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by text DEFAULT 'migration'::text NOT NULL,
    CONSTRAINT dbos_workflow_routing_mode_check CHECK ((mode = ANY (ARRAY['celery'::text, 'shadow'::text, 'dbos'::text])))
);


--
-- Name: TABLE dbos_workflow_routing; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.dbos_workflow_routing IS 'Per-task_type DBOS routing. mode controls which executor handles the task: celery (legacy), shadow (DBOS parallel-run, output discarded), dbos (DBOS canonical). FastAPI lifespan loads this table at boot + refreshes on tick. Schema PR-D2.1, design doc P11.';


--
-- Name: COLUMN dbos_workflow_routing.mode; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dbos_workflow_routing.mode IS 'celery = use Celery only (default for un-ported tasks); shadow = DBOS runs in parallel for comparison, Celery output is canonical; dbos = DBOS is canonical, Celery skipped.';


--
-- Name: deployment_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deployment_logs (
    id bigint DEFAULT (((EXTRACT(epoch FROM now()) * (1000000)::numeric))::bigint + ((random() * (1000)::double precision))::bigint) NOT NULL,
    service text NOT NULL,
    version text,
    commit_sha text,
    commit_count integer DEFAULT 0,
    commits jsonb DEFAULT '[]'::jsonb,
    summary text,
    deployed_at timestamp with time zone DEFAULT now(),
    deployed_by text,
    status text DEFAULT 'success'::text,
    metadata jsonb DEFAULT '{}'::jsonb,
    release_notes text,
    published_by text DEFAULT 'auto'::text
);


--
-- Name: distribution_oauth_states; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.distribution_oauth_states (
    state text NOT NULL,
    user_id uuid NOT NULL,
    platform character varying(50) NOT NULL,
    scope_type character varying(10) NOT NULL,
    scope_id text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: episodes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.episodes (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    project_id bigint NOT NULL,
    title character varying(200) DEFAULT 'Ep 1'::character varying NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: file_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.file_versions (
    version_number integer NOT NULL,
    filename character varying(500),
    file_path text,
    file_size_bytes bigint,
    mime_type character varying(100),
    duration_seconds integer,
    resolution character varying(50),
    fps numeric(6,2),
    video_codec character varying(50),
    audio_codec character varying(50),
    video_bitrate_kbps integer,
    audio_bitrate_kbps integer,
    audio_channels integer,
    audio_sample_rate integer,
    thumbnail_path text,
    cover_image_path text,
    uploaded_by uuid,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    file_id bigint NOT NULL
);

ALTER TABLE ONLY public.file_versions REPLICA IDENTITY FULL;


--
-- Name: folders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.folders (
    name character varying(200) NOT NULL,
    scope_id bigint NOT NULL,
    created_by uuid NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    is_system boolean DEFAULT false NOT NULL,
    icon character varying(50),
    color character varying(20),
    visibility character varying(20) DEFAULT 'inherited'::character varying NOT NULL,
    is_trashed boolean DEFAULT false NOT NULL,
    trashed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    parent_id bigint,
    library_id bigint,
    is_smart boolean DEFAULT false,
    smart_rules jsonb,
    CONSTRAINT folders_visibility_check CHECK (((visibility)::text = ANY (ARRAY[('inherited'::character varying)::text, ('restricted'::character varying)::text])))
);

ALTER TABLE ONLY public.folders REPLICA IDENTITY FULL;


--
-- Name: frontend_error_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.frontend_error_logs (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    user_id uuid,
    session_id character varying(100),
    error_type character varying(100) NOT NULL,
    message text,
    stack text,
    url character varying(1000),
    component character varying(255),
    user_agent text,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: fx_rates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fx_rates (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    from_currency text NOT NULL,
    to_currency text NOT NULL,
    rate numeric(15,8) NOT NULL,
    effective_at timestamp with time zone DEFAULT now() NOT NULL,
    source text
);


--
-- Name: TABLE fx_rates; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.fx_rates IS 'FX rate history. compute_cost() picks the rate effective at call time
   and persists the fx_rate_id in cost_snapshot so old calls replay correctly.';


--
-- Name: generated_media; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.generated_media (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    scope_id bigint NOT NULL,
    creator_id uuid NOT NULL,
    media_kind text NOT NULL,
    mime text,
    file_path text NOT NULL,
    file_size_bytes bigint,
    origin_kind text NOT NULL,
    origin_run_id text,
    agent_id uuid,
    canvas_id bigint,
    node_id text,
    prompt text,
    model text,
    provider text,
    params jsonb DEFAULT '{}'::jsonb NOT NULL,
    cost_cents numeric,
    parent_resource_id bigint,
    derivation_kind text,
    promoted_resource_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    conversation_id bigint,
    content_sha256 text
);


--
-- Name: TABLE generated_media; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.generated_media IS 'Unified "staged media" tier: AI generations (origin_kind agent_run/canvas_run) and chat uploads (origin_kind chat_upload, channel_id set). Cheap/high-churn; promote to resources on keep/use. Backend-only (service-role RLS).';


--
-- Name: hotspot_user_state; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hotspot_user_state (
    user_id uuid NOT NULL,
    hotspot_id bigint NOT NULL,
    is_read boolean DEFAULT false NOT NULL,
    is_saved boolean DEFAULT false NOT NULL,
    is_hidden boolean DEFAULT false NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: hotspots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hotspots (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    user_id uuid,
    source_id bigint,
    source_label text,
    title text NOT NULL,
    url text,
    origin_url text,
    content_original text,
    content_translated text,
    summary text,
    ai_summary text,
    reason text,
    score numeric,
    tags text[] DEFAULT '{}'::text[] NOT NULL,
    category text,
    topic_group_id bigint,
    media_url text,
    cover_url text,
    captured_at timestamp with time zone DEFAULT now() NOT NULL,
    rank_timeline jsonb DEFAULT '[]'::jsonb NOT NULL,
    dedup_key text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    heat numeric,
    embedding public.vector(2048),
    score_dims jsonb
);


--
-- Name: COLUMN hotspots.embedding; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.hotspots.embedding IS 'pgvector(2048) — Volcengine doubao-embedding-vision. Unindexed (2048 >
     pgvector ivfflat/hnsw 2000-dim cap); seq cosine scan at current scale.';


--
-- Name: inspiration_api_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.inspiration_api_tokens (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    user_id uuid NOT NULL,
    name character varying(128) NOT NULL,
    token_hash character varying(64) NOT NULL,
    last_used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_at timestamp with time zone
);


--
-- Name: inspiration_attachments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.inspiration_attachments (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    note_id bigint NOT NULL,
    user_id uuid NOT NULL,
    storage_backend character varying(32) DEFAULT 'supabase'::character varying NOT NULL,
    bucket character varying(64) DEFAULT 'inspiration'::character varying NOT NULL,
    path text NOT NULL,
    mime character varying(255) NOT NULL,
    size_bytes bigint NOT NULL,
    original_name text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: inspiration_notes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.inspiration_notes (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    user_id uuid NOT NULL,
    content_md text DEFAULT ''::text NOT NULL,
    tags text[] DEFAULT '{}'::text[] NOT NULL,
    ref_hotspot jsonb,
    pinned boolean DEFAULT false NOT NULL,
    note_date date NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone
);


--
-- Name: issue_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.issue_messages (
    issue_id bigint NOT NULL,
    kind text NOT NULL,
    author_user_id uuid,
    author_agent_id uuid,
    body text,
    meta jsonb DEFAULT '{}'::jsonb NOT NULL,
    duration_seconds integer,
    from_status text,
    to_status text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    agent_run_id bigint,
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    CONSTRAINT issue_messages_author_chk CHECK (
CASE kind
    WHEN 'comment'::text THEN ((author_user_id IS NOT NULL) OR (author_agent_id IS NOT NULL))
    WHEN 'agent_run'::text THEN (author_agent_id IS NOT NULL)
    WHEN 'system_status'::text THEN (author_agent_id IS NULL)
    ELSE false
END),
    CONSTRAINT issue_messages_kind_check CHECK ((kind = ANY (ARRAY['comment'::text, 'agent_run'::text, 'system_status'::text]))),
    CONSTRAINT issue_messages_status_chk CHECK (((kind = 'system_status'::text) = ((from_status IS NOT NULL) OR (to_status IS NOT NULL))))
);

ALTER TABLE ONLY public.issue_messages REPLICA IDENTITY FULL;


--
-- Name: TABLE issue_messages; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.issue_messages IS 'Paperclip-style chat thread per issue (A8). Three kinds in one timeline:
   comment / agent_run / system_status. RLS cascades through issues:
   anyone who can SELECT the issue can SELECT its messages. Trigger
   trg_issue_status_change_message auto-emits a system_status row on
   any issues.status update.';


--
-- Name: issue_sequence; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.issue_sequence (
    scope text DEFAULT 'global'::text NOT NULL,
    prefix text DEFAULT 'MH'::text NOT NULL,
    counter integer DEFAULT 0 NOT NULL
);


--
-- Name: TABLE issue_sequence; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.issue_sequence IS 'Atomic counter for issues.identifier (MH-N). Single-row table; UPDATE...RETURNING is the contract.';


--
-- Name: libraries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.libraries (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    name text NOT NULL,
    scope_type text NOT NULL,
    scope_id text NOT NULL,
    created_by uuid NOT NULL,
    icon text,
    color text,
    sort_order integer DEFAULT 0,
    visibility text DEFAULT 'inherited'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT libraries_scope_type_check CHECK ((scope_type = 'team'::text)),
    CONSTRAINT libraries_visibility_check CHECK ((visibility = ANY (ARRAY['inherited'::text, 'restricted'::text])))
);


--
-- Name: mediahub_models; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mediahub_models (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    name text NOT NULL,
    display_name text NOT NULL,
    type text NOT NULL,
    actual_provider text NOT NULL,
    actual_model text NOT NULL,
    api_key text NOT NULL,
    app_id text,
    base_url text,
    pricing_type text DEFAULT 'per_hour'::text NOT NULL,
    pricing_value numeric DEFAULT 8 NOT NULL,
    is_enabled boolean DEFAULT true NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    description text,
    last_test_status text,
    last_test_detail text,
    last_tested_at timestamp with time zone,
    CONSTRAINT mediahub_models_last_test_status_check CHECK (((last_test_status IS NULL) OR (last_test_status = ANY (ARRAY['ok'::text, 'fail'::text])))),
    CONSTRAINT mediahub_models_pricing_type_check CHECK ((pricing_type = ANY (ARRAY['per_hour'::text, 'per_request'::text, 'per_token'::text]))),
    CONSTRAINT mediahub_models_type_check CHECK ((type = ANY (ARRAY['llm'::text, 'embedding'::text, 'tts'::text, 'asr'::text, 'image'::text, 'video'::text])))
);


--
-- Name: member_quotas; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.member_quotas (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    monthly_points_limit integer,
    points_used_this_month integer DEFAULT 0 NOT NULL,
    reset_at timestamp with time zone DEFAULT (date_trunc('month'::text, now()) + '1 mon'::interval) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    team_id bigint NOT NULL
);


--
-- Name: message_attachments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.message_attachments (
    message_id bigint NOT NULL,
    generated_media_id bigint NOT NULL,
    ord integer DEFAULT 0 NOT NULL
);


--
-- Name: message_refs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.message_refs (
    message_id bigint NOT NULL,
    ref_type text NOT NULL,
    ref_id text NOT NULL
);


--
-- Name: messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.messages (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    conversation_id bigint NOT NULL,
    seq bigint NOT NULL,
    parent_id bigint,
    sender_type text DEFAULT 'user'::text NOT NULL,
    sender_id uuid,
    from_agent_id uuid,
    type text DEFAULT 'text'::text NOT NULL,
    body jsonb DEFAULT '{}'::jsonb NOT NULL,
    edited_at timestamp with time zone,
    deleted_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT messages_sender_type_check CHECK ((sender_type = ANY (ARRAY['user'::text, 'agent'::text, 'system'::text])))
);

ALTER TABLE ONLY public.messages REPLICA IDENTITY FULL;


--
-- Name: notifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notifications (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    type character varying(20) NOT NULL,
    title character varying(200) NOT NULL,
    content text,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now(),
    team_id bigint,
    CONSTRAINT notifications_type_check CHECK (((type)::text = ANY (ARRAY[('system'::character varying)::text, ('team'::character varying)::text])))
);

ALTER TABLE ONLY public.notifications REPLICA IDENTITY FULL;


--
-- Name: orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.orders (
    user_id uuid NOT NULL,
    package_id uuid,
    points_amount integer NOT NULL,
    amount_cents integer NOT NULL,
    currency character varying(10) DEFAULT 'CNY'::character varying NOT NULL,
    payment_method character varying(20) NOT NULL,
    payment_status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    trade_no character varying(200),
    payment_url text,
    paid_at timestamp with time zone,
    expired_at timestamp with time zone DEFAULT (now() + '00:30:00'::interval) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    team_id bigint NOT NULL,
    CONSTRAINT orders_amount_cents_check CHECK ((amount_cents > 0)),
    CONSTRAINT orders_payment_method_check CHECK (((payment_method)::text = ANY (ARRAY[('wechat'::character varying)::text, ('alipay'::character varying)::text]))),
    CONSTRAINT orders_payment_status_check CHECK (((payment_status)::text = ANY (ARRAY[('pending'::character varying)::text, ('paid'::character varying)::text, ('failed'::character varying)::text, ('expired'::character varying)::text, ('refunded'::character varying)::text]))),
    CONSTRAINT orders_points_amount_check CHECK ((points_amount > 0))
);


--
-- Name: point_packages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.point_packages (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100) NOT NULL,
    description text,
    points_amount integer NOT NULL,
    price_cents integer NOT NULL,
    currency character varying(10) DEFAULT 'CNY'::character varying NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT point_packages_points_amount_check CHECK ((points_amount > 0)),
    CONSTRAINT point_packages_price_cents_check CHECK ((price_cents > 0))
);


--
-- Name: point_pricing; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.point_pricing (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    action_type character varying(50) NOT NULL,
    points_cost integer NOT NULL,
    description text,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT point_pricing_points_cost_check CHECK ((points_cost >= 0))
);


--
-- Name: point_transactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.point_transactions (
    user_id uuid,
    amount integer NOT NULL,
    balance_after integer NOT NULL,
    type character varying(20) NOT NULL,
    reference_type character varying(50),
    reference_id character varying(200),
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    team_id bigint NOT NULL,
    provider text,
    model text,
    duration_seconds numeric,
    is_nous boolean DEFAULT false,
    CONSTRAINT point_transactions_type_check CHECK (((type)::text = ANY ((ARRAY['purchase'::character varying, 'consume'::character varying, 'refund'::character varying, 'gift'::character varying, 'admin_adjust'::character varying, 'daily_gift'::character varying, 'daily_gift_reclaim'::character varying])::text[])))
);


--
-- Name: project_characters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.project_characters (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    project_id bigint NOT NULL,
    name character varying(100) NOT NULL,
    role_tag text DEFAULT ''::text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    tags jsonb DEFAULT '{}'::jsonb NOT NULL,
    portrait_url text,
    source text DEFAULT 'manual'::text NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT project_characters_role_tag_check CHECK ((role_tag = ANY (ARRAY[''::text, 'lead'::text, 'support'::text, 'antagonist'::text]))),
    CONSTRAINT project_characters_source_check CHECK ((source = ANY (ARRAY['manual'::text, 'script'::text])))
);


--
-- Name: project_collections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.project_collections (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    project_id bigint NOT NULL,
    collection_code character varying(20) NOT NULL,
    collection_name character varying(200) NOT NULL,
    allowed_types text[],
    max_file_size_mb integer DEFAULT 500,
    deadline timestamp with time zone,
    is_active boolean DEFAULT true,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: project_file_comments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.project_file_comments (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    file_id bigint NOT NULL,
    version_id bigint,
    author_id uuid NOT NULL,
    content text NOT NULL,
    timestamp_seconds double precision,
    drawing_data jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: project_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.project_files (
    filename character varying(500) NOT NULL,
    file_type character varying(50),
    mime_type character varying(100),
    file_path text,
    file_size_bytes bigint,
    duration_seconds integer,
    resolution character varying(20),
    fps numeric(6,2),
    video_codec character varying(50),
    audio_codec character varying(50),
    video_bitrate_kbps integer,
    audio_bitrate_kbps integer,
    audio_channels integer,
    audio_sample_rate integer,
    thumbnail_path text,
    cover_image_path text,
    uploaded_by uuid,
    notes text,
    is_trashed boolean DEFAULT false NOT NULL,
    trashed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    review_status character varying(30),
    current_version integer DEFAULT 1 NOT NULL,
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    project_id bigint NOT NULL,
    media_id bigint,
    folder_id bigint,
    CONSTRAINT project_files_review_status_check CHECK (((review_status)::text = ANY (ARRAY[('pending_review'::character varying)::text, ('in_review'::character varying)::text, ('feedback_collected'::character varying)::text, ('approved'::character varying)::text])))
);

ALTER TABLE ONLY public.project_files REPLICA IDENTITY FULL;


--
-- Name: project_folders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.project_folders (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    project_id bigint NOT NULL,
    parent_id bigint,
    name character varying(200) DEFAULT 'New Folder'::character varying NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: project_lib_entities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.project_lib_entities (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    project_id bigint NOT NULL,
    entity_type text NOT NULL,
    name character varying(100) NOT NULL,
    badge_tag text DEFAULT ''::text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    tags jsonb DEFAULT '{}'::jsonb NOT NULL,
    cover_url text,
    source text DEFAULT 'manual'::text NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT project_lib_entities_entity_type_check CHECK ((entity_type = ANY (ARRAY['location'::text, 'prop'::text]))),
    CONSTRAINT project_lib_entities_source_check CHECK ((source = ANY (ARRAY['manual'::text, 'script'::text])))
);


--
-- Name: project_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.project_members (
    user_id uuid NOT NULL,
    role character varying(20) DEFAULT 'viewer'::character varying NOT NULL,
    invited_by uuid,
    joined_at timestamp with time zone DEFAULT now() NOT NULL,
    project_id bigint NOT NULL,
    CONSTRAINT project_members_role_check CHECK (((role)::text = ANY (ARRAY[('manager'::character varying)::text, ('editor'::character varying)::text, ('viewer'::character varying)::text, ('external'::character varying)::text])))
);

ALTER TABLE ONLY public.project_members REPLICA IDENTITY FULL;


--
-- Name: project_stage_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.project_stage_history (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    project_id bigint NOT NULL,
    stage_id bigint NOT NULL,
    entered_at timestamp with time zone DEFAULT now() NOT NULL,
    exited_at timestamp with time zone,
    transitioned_by uuid
);


--
-- Name: project_stages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.project_stages (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    slug text NOT NULL,
    name text NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    tools_recommended jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: project_style_profile; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.project_style_profile (
    project_id bigint NOT NULL,
    style_md text DEFAULT ''::text NOT NULL,
    visual_style jsonb DEFAULT '{}'::jsonb NOT NULL,
    reference_links jsonb DEFAULT '[]'::jsonb NOT NULL,
    updated_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: project_tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.project_tasks (
    workflow_node_id uuid,
    title character varying(500) NOT NULL,
    description text,
    task_type character varying(30) DEFAULT 'general'::character varying NOT NULL,
    assignee_id uuid,
    due_date date,
    sort_order integer DEFAULT 0 NOT NULL,
    status character varying(20) DEFAULT 'todo'::character varying NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    project_id bigint NOT NULL,
    issue_id bigint,
    CONSTRAINT project_tasks_status_check CHECK (((status)::text = ANY (ARRAY[('todo'::character varying)::text, ('in_progress'::character varying)::text, ('done'::character varying)::text, ('cancelled'::character varying)::text, ('on_hold'::character varying)::text]))),
    CONSTRAINT project_tasks_task_type_check CHECK (((task_type)::text = ANY (ARRAY[('general'::character varying)::text, ('storyboard'::character varying)::text, ('script'::character varying)::text, ('filming'::character varying)::text, ('editing'::character varying)::text, ('review'::character varying)::text])))
);

ALTER TABLE ONLY public.project_tasks REPLICA IDENTITY FULL;


--
-- Name: COLUMN project_tasks.issue_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.project_tasks.issue_id IS 'Back-reference to issues.id. Set during PR-D6 dual-write window; NULL for tasks that pre-date the migration. Frontend may read either side.';


--
-- Name: project_workflows; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.project_workflows (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100) NOT NULL,
    is_default boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    team_id bigint NOT NULL
);

ALTER TABLE ONLY public.project_workflows REPLICA IDENTITY FULL;


--
-- Name: projects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.projects (
    name character varying(200) NOT NULL,
    description text,
    owner_id uuid NOT NULL,
    project_type character varying(20) DEFAULT 'personal'::character varying NOT NULL,
    project_group character varying(100),
    is_starred boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    visibility character varying(20) DEFAULT 'inherited'::character varying NOT NULL,
    workflow_id uuid,
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    team_id bigint,
    announcement text,
    color_label character varying(20),
    current_canvas_id bigint,
    current_stage_id bigint,
    archived_at timestamp with time zone,
    CONSTRAINT projects_project_type_check CHECK (((project_type)::text = ANY (ARRAY[('internal'::character varying)::text, ('external'::character varying)::text, ('personal'::character varying)::text]))),
    CONSTRAINT projects_visibility_check CHECK (((visibility)::text = ANY (ARRAY[('inherited'::character varying)::text, ('restricted'::character varying)::text])))
);

ALTER TABLE ONLY public.projects REPLICA IDENTITY FULL;


--
-- Name: COLUMN projects.announcement; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.projects.announcement IS 'Project announcement (max 100 chars)';


--
-- Name: COLUMN projects.current_canvas_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.projects.current_canvas_id IS 'Pointer to canvases.id of the project''s active canvas. No FK because
   the column is mutated frequently and a stale pointer (canvas deleted)
   is treated as "no current canvas" in app code, not an error.';


--
-- Name: provider_byok_keys; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.provider_byok_keys (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    user_id bigint,
    team_id bigint,
    provider_slug text NOT NULL,
    key_hash text NOT NULL,
    key_label text,
    added_at timestamp with time zone DEFAULT now() NOT NULL,
    last_used_at timestamp with time zone,
    active boolean DEFAULT true NOT NULL
);


--
-- Name: TABLE provider_byok_keys; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.provider_byok_keys IS 'BYOK identification. Calls flagged byok_key_id NOT NULL are zero-billed
   on cost ledger (user owes vendor directly). Latency/quality still tracked.';


--
-- Name: provider_contracts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.provider_contracts (
    contract_id text NOT NULL,
    provider_slug text NOT NULL,
    tenant_id bigint,
    enterprise_discount_pct numeric(5,2) DEFAULT 0 NOT NULL,
    volume_tier_json jsonb,
    monthly_min_commit_usd numeric(12,2),
    monthly_min_commit_local numeric(12,2),
    monthly_min_commit_currency text,
    overage_rate_multiplier numeric(5,2) DEFAULT 1.0 NOT NULL,
    prepay_total_usd numeric(12,2),
    prepay_remaining_usd numeric(12,2),
    prepay_expires_at timestamp with time zone,
    start_date date,
    end_date date,
    auto_renew boolean DEFAULT false NOT NULL,
    contact_email text,
    contact_phone text,
    account_manager_name text,
    contract_pdf_url text,
    status text DEFAULT 'active'::text NOT NULL,
    signed_at timestamp with time zone,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT provider_contracts_status_check CHECK ((status = ANY (ARRAY['active'::text, 'expired'::text, 'pending'::text, 'terminated'::text])))
);


--
-- Name: TABLE provider_contracts; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.provider_contracts IS 'Contract terms (enterprise discount / volume tier / prepay / overage).
   tenant_id NULL = platform-default; non-null = team-specific (BYOK-enterprise).';


--
-- Name: COLUMN provider_contracts.volume_tier_json; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.provider_contracts.volume_tier_json IS 'JSONB array of {threshold_monthly_usd, discount_pct} tiers. Applied at compute_cost() time.';


--
-- Name: provider_credits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.provider_credits (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    provider_slug text NOT NULL,
    contract_id text,
    credit_type text NOT NULL,
    amount_usd numeric(12,2) NOT NULL,
    remaining_usd numeric(12,2) NOT NULL,
    earned_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone,
    consumed_at timestamp with time zone,
    reason text,
    evidence_url text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT provider_credits_type_check CHECK ((credit_type = ANY (ARRAY['free_trial'::text, 'promotion'::text, 'sla_refund'::text, 'goodwill'::text, 'prepay'::text])))
);


--
-- Name: TABLE provider_credits; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.provider_credits IS 'Vendor credits: free trial, promotion, SLA refund, goodwill, prepay top-up.
   compute_cost() consumes from this table when remaining_usd > 0.';


--
-- Name: provider_pricing; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.provider_pricing (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    provider_slug text NOT NULL,
    model_slug text NOT NULL,
    region text,
    modality text NOT NULL,
    list_input_per_1m_usd numeric(12,6),
    list_output_per_1m_usd numeric(12,6),
    list_cache_read_per_1m_usd numeric(12,6),
    list_cache_write_per_1m_usd numeric(12,6),
    list_reasoning_per_1m_usd numeric(12,6),
    list_tool_use_per_1m_usd numeric(12,6),
    list_vision_per_1m_usd numeric(12,6),
    list_audio_per_1m_usd numeric(12,6),
    list_image_per_unit_usd numeric(12,6),
    list_image_per_megapixel_usd numeric(12,6),
    list_video_per_second_usd numeric(12,6),
    list_audio_per_second_usd numeric(12,6),
    list_audio_per_char_usd numeric(12,6),
    batch_discount_pct numeric(5,2) DEFAULT 0 NOT NULL,
    cache_ttl_seconds integer DEFAULT 300 NOT NULL,
    fail_billing_policy text DEFAULT 'no_charge'::text NOT NULL,
    contract_id text,
    effective_from timestamp with time zone DEFAULT now() NOT NULL,
    effective_to timestamp with time zone,
    extra jsonb DEFAULT '{}'::jsonb NOT NULL,
    source text,
    source_url text,
    source_pdf_url text,
    added_by uuid,
    added_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT provider_pricing_fail_policy_check CHECK ((fail_billing_policy = ANY (ARRAY['no_charge'::text, 'partial'::text, 'full'::text]))),
    CONSTRAINT provider_pricing_modality_check CHECK ((modality = ANY (ARRAY['text_chat'::text, 'text_embedding'::text, 'image_generation'::text, 'image_editing'::text, 'image_input'::text, 'video_generation'::text, 'video_input'::text, 'audio_tts'::text, 'audio_stt'::text, 'audio_input'::text, 'reasoning'::text])))
);


--
-- Name: TABLE provider_pricing; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.provider_pricing IS 'Historical rate table per (provider, model, modality, region, contract).
   effective_to NULL = currently in effect. Old rows kept immutably for audit.';


--
-- Name: COLUMN provider_pricing.batch_discount_pct; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.provider_pricing.batch_discount_pct IS 'Async-batch discount %. 0 = no batch tier available.';


--
-- Name: COLUMN provider_pricing.fail_billing_policy; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.provider_pricing.fail_billing_policy IS 'no_charge / partial / full — vendor policy on failed calls.';


--
-- Name: provider_effective_rate; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.provider_effective_rate AS
 SELECT p.id,
    p.provider_slug,
    p.model_slug,
    p.region,
    p.modality,
    p.contract_id,
    p.effective_from,
    p.effective_to,
    c.tenant_id,
    c.enterprise_discount_pct,
    c.volume_tier_json,
    ((p.list_input_per_1m_usd * ((1)::numeric - (COALESCE(c.enterprise_discount_pct, (0)::numeric) / 100.0))))::numeric(12,6) AS effective_input_usd,
    ((p.list_output_per_1m_usd * ((1)::numeric - (COALESCE(c.enterprise_discount_pct, (0)::numeric) / 100.0))))::numeric(12,6) AS effective_output_usd,
    ((p.list_cache_read_per_1m_usd * ((1)::numeric - (COALESCE(c.enterprise_discount_pct, (0)::numeric) / 100.0))))::numeric(12,6) AS effective_cache_read_usd,
    ((p.list_cache_write_per_1m_usd * ((1)::numeric - (COALESCE(c.enterprise_discount_pct, (0)::numeric) / 100.0))))::numeric(12,6) AS effective_cache_write_usd,
    ((p.list_reasoning_per_1m_usd * ((1)::numeric - (COALESCE(c.enterprise_discount_pct, (0)::numeric) / 100.0))))::numeric(12,6) AS effective_reasoning_usd,
    ((p.list_tool_use_per_1m_usd * ((1)::numeric - (COALESCE(c.enterprise_discount_pct, (0)::numeric) / 100.0))))::numeric(12,6) AS effective_tool_use_usd,
    ((p.list_vision_per_1m_usd * ((1)::numeric - (COALESCE(c.enterprise_discount_pct, (0)::numeric) / 100.0))))::numeric(12,6) AS effective_vision_usd,
    ((p.list_audio_per_1m_usd * ((1)::numeric - (COALESCE(c.enterprise_discount_pct, (0)::numeric) / 100.0))))::numeric(12,6) AS effective_audio_usd,
    ((p.list_image_per_unit_usd * ((1)::numeric - (COALESCE(c.enterprise_discount_pct, (0)::numeric) / 100.0))))::numeric(12,6) AS effective_image_per_unit_usd,
    ((p.list_video_per_second_usd * ((1)::numeric - (COALESCE(c.enterprise_discount_pct, (0)::numeric) / 100.0))))::numeric(12,6) AS effective_video_per_second_usd,
    p.batch_discount_pct,
    p.cache_ttl_seconds,
    p.fail_billing_policy,
    p.extra
   FROM (public.provider_pricing p
     LEFT JOIN public.provider_contracts c ON ((p.contract_id = c.contract_id)))
  WHERE (p.effective_to IS NULL);


--
-- Name: VIEW provider_effective_rate; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.provider_effective_rate IS 'Currently-effective rates with enterprise discount applied.
   Volume tier + BYOK + credit consumption + FX happen at compute_cost() time.';


--
-- Name: provider_monthly_spend; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.provider_monthly_spend (
    provider_slug text NOT NULL,
    contract_id text NOT NULL,
    year_month character(7) NOT NULL,
    total_usd numeric(12,4) DEFAULT 0 NOT NULL,
    total_local_cents bigint DEFAULT 0 NOT NULL,
    call_count bigint DEFAULT 0 NOT NULL,
    last_updated timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE provider_monthly_spend; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.provider_monthly_spend IS 'Rollup cache. Background job refreshes from agent_run_events every 5 min.
   compute_cost() reads this to apply volume_tier_json from contracts.';


--
-- Name: publish_task_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.publish_task_accounts (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    task_id bigint NOT NULL,
    account_id bigint NOT NULL,
    resource_id bigint,
    channel character varying(10) DEFAULT 'h5'::character varying NOT NULL,
    title text,
    description text,
    topics jsonb,
    share_id text,
    status character varying(16) DEFAULT 'pending'::character varying NOT NULL,
    error_message text,
    published_url text,
    platform_item_id text,
    published_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT publish_task_accounts_channel_check CHECK (((channel)::text = ANY ((ARRAY['official'::character varying, 'h5'::character varying])::text[]))),
    CONSTRAINT publish_task_accounts_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'pending_share'::character varying, 'publishing'::character varying, 'success'::character varying, 'failed'::character varying, 'cancelled'::character varying])::text[])))
);


--
-- Name: publish_tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.publish_tasks (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    user_id uuid NOT NULL,
    team_id bigint,
    content_type character varying(10) DEFAULT 'video'::character varying NOT NULL,
    resource_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    title text NOT NULL,
    description text,
    topics jsonb DEFAULT '[]'::jsonb NOT NULL,
    cover_vertical_resource_id bigint,
    cover_horizontal_resource_id bigint,
    visibility character varying(10) DEFAULT 'public'::character varying NOT NULL,
    ai_content boolean DEFAULT false NOT NULL,
    allow_download boolean DEFAULT true NOT NULL,
    distribution_mode character varying(12) DEFAULT 'broadcast'::character varying NOT NULL,
    scheduled_at timestamp with time zone,
    dbos_workflow_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT publish_tasks_content_type_check CHECK (((content_type)::text = ANY ((ARRAY['video'::character varying, 'images'::character varying, 'article'::character varying])::text[]))),
    CONSTRAINT publish_tasks_distribution_mode_check CHECK (((distribution_mode)::text = ANY ((ARRAY['broadcast'::character varying, 'one_to_one'::character varying])::text[]))),
    CONSTRAINT publish_tasks_visibility_check CHECK (((visibility)::text = ANY ((ARRAY['public'::character varying, 'friends'::character varying, 'private'::character varying])::text[])))
);


--
-- Name: resource_access_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.resource_access_logs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    action character varying(20) NOT NULL,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now(),
    resource_id bigint NOT NULL,
    CONSTRAINT video_access_logs_action_check CHECK (((action)::text = ANY (ARRAY[('view'::character varying)::text, ('play'::character varying)::text, ('share'::character varying)::text, ('download_again'::character varying)::text, ('export'::character varying)::text])))
);


--
-- Name: TABLE resource_access_logs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.resource_access_logs IS 'User access logs for parsed media.';


--
-- Name: resource_analysis; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.resource_analysis (
    resource_id bigint NOT NULL,
    analysis_level character varying(10) DEFAULT 'none'::character varying NOT NULL,
    visual_description text,
    detected_objects jsonb DEFAULT '[]'::jsonb,
    detected_scenes jsonb DEFAULT '[]'::jsonb,
    detected_people jsonb DEFAULT '[]'::jsonb,
    detected_text text,
    full_text_for_embedding text,
    content_embedding public.vector(2048),
    analysis_model character varying(50),
    analysis_cost numeric(10,6) DEFAULT 0,
    analyzed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT resource_analysis_analysis_level_check CHECK (((analysis_level)::text = ANY ((ARRAY['none'::character varying, 'L1'::character varying, 'L2'::character varying, 'L3'::character varying])::text[])))
);


--
-- Name: COLUMN resource_analysis.content_embedding; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.resource_analysis.content_embedding IS 'pgvector(2048) — doubao-embedding-vision (platform embedder). Unindexed
     (2048 > pgvector ivfflat/hnsw 2000-dim cap); seq cosine scan at this scale.';


--
-- Name: resource_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.resource_items (
    scope_id bigint NOT NULL,
    added_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    resource_id bigint NOT NULL,
    folder_id bigint,
    library_id bigint,
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL
);

ALTER TABLE ONLY public.resource_items REPLICA IDENTITY FULL;


--
-- Name: resources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.resources (
    creator_id uuid NOT NULL,
    source_type character varying(20) NOT NULL,
    filename character varying(500) NOT NULL,
    file_type character varying(50),
    mime_type character varying(100),
    file_path text,
    file_size_bytes bigint,
    duration_seconds integer,
    resolution character varying(50),
    thumbnail_path text,
    cover_image_path text,
    current_version integer DEFAULT 1 NOT NULL,
    is_trashed boolean DEFAULT false NOT NULL,
    trashed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    media_id bigint,
    file_hash character varying(64),
    transcript_status public.ai_task_status DEFAULT 'none'::public.ai_task_status NOT NULL,
    summary_status public.ai_task_status DEFAULT 'none'::public.ai_task_status NOT NULL,
    visual_analysis_status public.ai_task_status DEFAULT 'none'::public.ai_task_status NOT NULL,
    notes text,
    url text,
    rating smallint DEFAULT 0,
    last_folder_id bigint,
    last_library_id bigint,
    last_scope_type character varying(20),
    last_scope_id text,
    aspect_bucket text GENERATED ALWAYS AS (public.resource_aspect_bucket((resolution)::text)) STORED,
    audio_bitrate_kbps integer,
    lyrics_json jsonb,
    chorus_start_ms integer,
    gen_prompt text,
    gen_prompt_zh text,
    CONSTRAINT resources_rating_check CHECK (((rating >= 0) AND (rating <= 5))),
    CONSTRAINT resources_source_type_check CHECK (((source_type)::text = ANY ((ARRAY['web'::character varying, 'upload'::character varying, 'generated'::character varying, 'derived'::character varying])::text[])))
);

ALTER TABLE ONLY public.resources REPLICA IDENTITY FULL;


--
-- Name: COLUMN resources.file_hash; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.resources.file_hash IS 'SHA-256 hex digest of the latest version file content';


--
-- Name: COLUMN resources.transcript_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.resources.transcript_status IS 'Per-user AI transcript status: none|pending|processing|completed|failed';


--
-- Name: COLUMN resources.summary_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.resources.summary_status IS 'Per-user AI summary status: none|pending|processing|completed|failed';


--
-- Name: COLUMN resources.visual_analysis_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.resources.visual_analysis_status IS 'Per-user AI visual analysis status: none|pending|processing|completed|failed';


--
-- Name: COLUMN resources.audio_bitrate_kbps; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.resources.audio_bitrate_kbps IS 'Audio bitrate in kbps, ffprobed on upload for audio/* resources';


--
-- Name: COLUMN resources.lyrics_json; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.resources.lyrics_json IS 'User lyrics for uploaded audio: {lrc: text, lines: [{text, line_start_ms}]}';


--
-- Name: COLUMN resources.chorus_start_ms; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.resources.chorus_start_ms IS 'Chorus/highlight start in ms for uploaded audio; NULL = unset';


--
-- Name: COLUMN resources.gen_prompt; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.resources.gen_prompt IS 'AI generation prompt for this asset (user-entered or auto-extracted)';


--
-- Name: COLUMN resources.gen_prompt_zh; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.resources.gen_prompt_zh IS 'Chinese-language AI generation prompt (user-entered or provider-translated)';


--
-- Name: resource_statistics; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.resource_statistics WITH (security_invoker='true') AS
 SELECT creator_id AS user_id,
    count(*) AS total_resources,
    count(*) FILTER (WHERE ((file_type)::text = 'video'::text)) AS video_count,
    count(*) FILTER (WHERE ((file_type)::text = 'image'::text)) AS image_count,
    count(*) FILTER (WHERE ((file_type)::text = 'audio'::text)) AS audio_count,
    count(*) FILTER (WHERE ((file_type)::text = 'document'::text)) AS document_count,
    COALESCE(sum(file_size_bytes), (0)::numeric) AS total_size_bytes,
    count(*) FILTER (WHERE (is_trashed = true)) AS trashed_count
   FROM public.resources r
  GROUP BY creator_id;


--
-- Name: resource_summaries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.resource_summaries (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    summary_type character varying(20),
    summary_text text,
    key_points jsonb,
    topics jsonb,
    llm_model character varying(50),
    llm_provider character varying(50),
    created_at timestamp with time zone DEFAULT now(),
    resource_id bigint NOT NULL
);

ALTER TABLE ONLY public.resource_summaries REPLICA IDENTITY FULL;


--
-- Name: TABLE resource_summaries; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.resource_summaries IS 'AI-generated summaries for parsed media.';


--
-- Name: resource_tags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.resource_tags (
    tagged_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    resource_id bigint NOT NULL,
    source character varying(50) DEFAULT 'user'::character varying,
    confidence double precision,
    tag_id bigint NOT NULL
);


--
-- Name: resource_transcripts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.resource_transcripts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    language character varying(10),
    full_text text,
    segments jsonb,
    whisper_model character varying(50),
    duration_seconds double precision,
    created_at timestamp with time zone DEFAULT now(),
    resource_id bigint NOT NULL
);

ALTER TABLE ONLY public.resource_transcripts REPLICA IDENTITY FULL;


--
-- Name: TABLE resource_transcripts; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.resource_transcripts IS 'AI-generated transcripts for parsed media.';


--
-- Name: resource_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.resource_versions (
    version_number integer NOT NULL,
    filename character varying(500),
    file_path text,
    file_size_bytes bigint,
    mime_type character varying(100),
    duration_seconds integer,
    resolution character varying(50),
    thumbnail_path text,
    uploaded_by uuid,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    resource_id bigint NOT NULL,
    hls_path text,
    transcode_status character varying(20) DEFAULT NULL::character varying,
    transcode_at timestamp with time zone,
    file_hash character varying(64),
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    audio_bitrate_kbps integer
);


--
-- Name: COLUMN resource_versions.hls_path; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.resource_versions.hls_path IS 'Relative path to master.m3u8 (null = not transcoded)';


--
-- Name: COLUMN resource_versions.transcode_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.resource_versions.transcode_status IS 'pending / processing / completed / failed';


--
-- Name: COLUMN resource_versions.transcode_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.resource_versions.transcode_at IS 'When transcoding completed';


--
-- Name: COLUMN resource_versions.file_hash; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.resource_versions.file_hash IS 'SHA-256 hex digest of this version file content';


--
-- Name: review_annotations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.review_annotations (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    comment_id bigint NOT NULL,
    tool_type character varying(20) NOT NULL,
    data jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: review_comments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.review_comments (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    resource_id bigint NOT NULL,
    author_id uuid NOT NULL,
    timecode double precision,
    frame_number integer,
    content text NOT NULL,
    status character varying(20) DEFAULT 'open'::character varying NOT NULL,
    parent_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    version_id bigint
);


--
-- Name: review_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.review_status (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    resource_id bigint NOT NULL,
    reviewer_id uuid NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    comment text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    version_id bigint
);


--
-- Name: script_assets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.script_assets (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    script_id bigint NOT NULL,
    asset_type text NOT NULL,
    name text NOT NULL,
    content text,
    data_json jsonb DEFAULT '{}'::jsonb,
    sort_order integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: script_beats; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.script_beats (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    script_id bigint NOT NULL,
    title character varying(200) NOT NULL,
    summary text,
    scene_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: script_chapters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.script_chapters (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    script_id bigint NOT NULL,
    parent_chapter_id bigint,
    chapter_number integer,
    title text,
    summary text,
    content text,
    branch_label text,
    branch_type text,
    position_x double precision DEFAULT 0,
    position_y double precision DEFAULT 0,
    width double precision,
    height double precision,
    data_json jsonb DEFAULT '{}'::jsonb,
    sort_order integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    content_json jsonb
);


--
-- Name: COLUMN script_chapters.content; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.script_chapters.content IS 'Plain text derived from content_json (for search and AI)';


--
-- Name: COLUMN script_chapters.content_json; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.script_chapters.content_json IS 'TipTap ProseMirror JSON document (source of truth)';


--
-- Name: script_commits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.script_commits (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    script_id bigint NOT NULL,
    message character varying(200) NOT NULL,
    watermarks jsonb NOT NULL,
    scene_ids jsonb NOT NULL,
    created_by character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: script_ops; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.script_ops (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    scene_id bigint NOT NULL,
    op_seq integer NOT NULL,
    op_json jsonb NOT NULL,
    actor character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.script_ops REPLICA IDENTITY FULL;


--
-- Name: script_projects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.script_projects (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    project_id bigint NOT NULL,
    team_id bigint NOT NULL,
    display_code text,
    name character varying(200) NOT NULL,
    description text,
    settings_json jsonb DEFAULT '{}'::jsonb,
    viewport_json jsonb,
    status text DEFAULT 'active'::text,
    created_by uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    genre character varying(50),
    episode_id bigint
);


--
-- Name: COLUMN script_projects.genre; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.script_projects.genre IS 'Story genre/style';


--
-- Name: script_scenes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.script_scenes (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    script_id bigint NOT NULL,
    chapter_id bigint,
    heading_int_ext character varying(10),
    location_text text,
    location_id bigint,
    time_of_day character varying(20),
    content_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    content text DEFAULT ''::text NOT NULL,
    content_version integer DEFAULT 0 NOT NULL,
    position_x double precision,
    position_y double precision,
    width double precision,
    height double precision,
    sort_order integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: script_shots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.script_shots (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    scene_id bigint NOT NULL,
    shot_number integer,
    shot_type character varying(20),
    camera_angle character varying(20),
    camera_movement character varying(20),
    focal_length character varying(20),
    lighting text,
    description text,
    image_url text,
    thumbnail_url text,
    video_url text,
    status character varying(20) DEFAULT 'empty'::character varying NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: script_storyboard_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.script_storyboard_links (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    chapter_id bigint NOT NULL,
    storyboard_project_id bigint NOT NULL,
    storyboard_node_id bigint,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: search_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.search_logs (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    user_id uuid,
    query text NOT NULL,
    search_type character varying(20) NOT NULL,
    result_count integer DEFAULT 0,
    top_result_similarity double precision,
    filters_used jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    top_result_video_id bigint,
    CONSTRAINT search_logs_search_type_check CHECK (((search_type)::text = ANY (ARRAY[('semantic'::character varying)::text, ('hybrid'::character varying)::text, ('similar'::character varying)::text, ('quick'::character varying)::text])))
);


--
-- Name: TABLE search_logs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.search_logs IS 'Tracks search queries for analytics and suggestions';


--
-- Name: share_views; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.share_views (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    viewer_id uuid,
    is_favorited boolean DEFAULT false NOT NULL,
    last_viewed_at timestamp with time zone DEFAULT now() NOT NULL,
    view_count integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    share_id bigint NOT NULL
);


--
-- Name: shares; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.shares (
    share_type character varying(20) NOT NULL,
    shared_by uuid NOT NULL,
    share_name character varying(200) NOT NULL,
    share_code character varying(20) NOT NULL,
    password text,
    allow_download boolean DEFAULT true NOT NULL,
    expires_at timestamp with time zone,
    max_views integer,
    view_count integer DEFAULT 0 NOT NULL,
    watermark boolean DEFAULT false NOT NULL,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    project_file_id bigint,
    version_id bigint,
    resource_id bigint,
    folder_id bigint,
    library_id bigint,
    team_id bigint,
    CONSTRAINT shares_share_type_check CHECK (((share_type)::text = ANY (ARRAY[('link'::character varying)::text, ('review'::character varying)::text, ('presentation'::character varying)::text, ('delivery'::character varying)::text]))),
    CONSTRAINT shares_status_check CHECK (((status)::text = ANY (ARRAY[('active'::character varying)::text, ('inactive'::character varying)::text, ('expired'::character varying)::text, ('cancelled'::character varying)::text])))
);

ALTER TABLE ONLY public.shares REPLICA IDENTITY FULL;


--
-- Name: signal_sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.signal_sources (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    user_id uuid,
    kind text NOT NULL,
    name text NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    category text,
    health text DEFAULT 'ok'::text NOT NULL,
    consecutive_failures integer DEFAULT 0 NOT NULL,
    last_error text,
    last_fetched_at timestamp with time zone,
    last_ok_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    tier smallint DEFAULT 2 NOT NULL,
    CONSTRAINT signal_sources_health_check CHECK ((health = ANY (ARRAY['ok'::text, 'degraded'::text, 'dead'::text]))),
    CONSTRAINT signal_sources_kind_check CHECK ((kind = ANY (ARRAY['newsnow'::text, 'rss'::text, 'http_api'::text, 'custom'::text]))),
    CONSTRAINT signal_sources_tier_check CHECK ((tier = ANY (ARRAY[1, 2, 3])))
);


--
-- Name: skill_file_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.skill_file_versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    skill_file_id uuid NOT NULL,
    version_number integer NOT NULL,
    path text NOT NULL,
    content text,
    file_type text,
    binary_url text,
    notes text,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: skill_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.skill_files (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    skill_id bigint NOT NULL,
    path text NOT NULL,
    content text,
    sort_order integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    file_type text DEFAULT 'markdown'::text NOT NULL,
    binary_url text,
    current_version integer DEFAULT 1 NOT NULL,
    seed_hash text,
    CONSTRAINT skill_files_file_type_check CHECK ((file_type = ANY (ARRAY['markdown'::text, 'script'::text, 'text-asset'::text, 'binary-ref'::text])))
);


--
-- Name: TABLE skill_files; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.skill_files IS 'Supplementary files attached to a skill (references, templates, examples)';


--
-- Name: COLUMN skill_files.path; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.skill_files.path IS 'Relative path within the skill bundle (e.g. SKILL.md, refs/example.md, assets/logo.png)';


--
-- Name: COLUMN skill_files.file_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.skill_files.file_type IS 'markdown | script | text-asset | binary-ref';


--
-- Name: COLUMN skill_files.binary_url; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.skill_files.binary_url IS 'URL for binary-ref assets (content is NULL in that case)';


--
-- Name: COLUMN skill_files.seed_hash; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.skill_files.seed_hash IS 'sha256 of file content; null = always re-upsert';


--
-- Name: skill_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.skill_versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    skill_id bigint NOT NULL,
    version_number integer NOT NULL,
    body_md text,
    frontmatter_json jsonb,
    notes text,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: skills; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.skills (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    team_id bigint,
    project_id bigint,
    created_by uuid,
    name text NOT NULL,
    description text,
    content_md text,
    category text,
    icon character varying(20) DEFAULT '✨'::character varying,
    output_format text,
    trigger_keywords text[] DEFAULT '{}'::text[],
    is_public boolean DEFAULT false,
    status character varying(20) DEFAULT 'active'::character varying,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    prompt_template text,
    input_schema jsonb,
    default_agent_id uuid,
    slug character varying(64),
    body_md text,
    frontmatter_json jsonb DEFAULT '{}'::jsonb,
    current_version integer DEFAULT 1 NOT NULL,
    seed_hash text
);


--
-- Name: COLUMN skills.slug; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.skills.slug IS 'Stable identifier (e.g. translate, compress)';


--
-- Name: COLUMN skills.body_md; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.skills.body_md IS 'Markdown body (skill prompt/instructions)';


--
-- Name: COLUMN skills.frontmatter_json; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.skills.frontmatter_json IS 'YAML frontmatter parsed as JSON (metadata, I/O schema hints)';


--
-- Name: COLUMN skills.seed_hash; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.skills.seed_hash IS 'sha256 of body_md + frontmatter_json; null = always re-upsert';


--
-- Name: smart_collections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.smart_collections (
    user_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    icon character varying(50) DEFAULT '📁'::character varying,
    description text,
    rules jsonb DEFAULT '{"match": "all", "conditions": []}'::jsonb NOT NULL,
    cached_count integer DEFAULT 0,
    cached_at timestamp with time zone,
    is_preset boolean DEFAULT false,
    sort_by character varying(50) DEFAULT 'created_at'::character varying,
    sort_order character varying(10) DEFAULT 'desc'::character varying,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    is_active boolean DEFAULT true,
    color character varying(20) DEFAULT '#3b82f6'::character varying,
    scope_id bigint,
    cached_video_ids bigint[] DEFAULT '{}'::bigint[],
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    CONSTRAINT smart_collections_sort_order_check CHECK (((sort_order)::text = ANY (ARRAY[('asc'::character varying)::text, ('desc'::character varying)::text])))
);

ALTER TABLE ONLY public.smart_collections REPLICA IDENTITY FULL;


--
-- Name: TABLE smart_collections; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.smart_collections IS 'Smart folders with dynamic filter rules. PK migrated from UUID to Snowflake BIGINT in 060.';


--
-- Name: COLUMN smart_collections.is_active; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.smart_collections.is_active IS 'Whether the collection is currently active';


--
-- Name: COLUMN smart_collections.color; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.smart_collections.color IS 'Optional color for collection display (e.g., #6366f1)';


--
-- Name: snowflake_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.snowflake_seq
    START WITH 0
    INCREMENT BY 1
    MINVALUE 0
    MAXVALUE 4095
    CACHE 1
    CYCLE;


--
-- Name: social_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.social_accounts (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    scope_type character varying(10) NOT NULL,
    scope_id text NOT NULL,
    platform character varying(50) NOT NULL,
    platform_user_id text NOT NULL,
    username text NOT NULL,
    avatar_url text,
    access_token text,
    refresh_token text,
    token_expires_at timestamp with time zone,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_by uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT social_accounts_scope_type_check CHECK (((scope_type)::text = ANY ((ARRAY['user'::character varying, 'team'::character varying])::text[]))),
    CONSTRAINT social_accounts_status_check CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'expired'::character varying])::text[])))
);


--
-- Name: style_templates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.style_templates (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    team_id bigint,
    name text NOT NULL,
    description text,
    prompt_content text NOT NULL,
    category text,
    is_public boolean DEFAULT false,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: system_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.system_settings (
    key character varying(100) NOT NULL,
    value jsonb NOT NULL,
    description text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid,
    category character varying(50) DEFAULT 'general'::character varying,
    input_type character varying(30) DEFAULT 'text'::character varying,
    options jsonb
);


--
-- Name: TABLE system_settings; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.system_settings IS 'Global system configuration settings';


--
-- Name: COLUMN system_settings.key; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.system_settings.key IS 'Setting key identifier';


--
-- Name: COLUMN system_settings.value; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.system_settings.value IS 'Setting value in JSON format';


--
-- Name: COLUMN system_settings.updated_by; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.system_settings.updated_by IS 'Admin who last updated this setting';


--
-- Name: system_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.system_status (
    id uuid DEFAULT '00000000-0000-0000-0000-000000000001'::uuid NOT NULL,
    queue jsonb DEFAULT '{}'::jsonb NOT NULL,
    workers jsonb DEFAULT '[]'::jsonb NOT NULL,
    storage jsonb DEFAULT '{}'::jsonb NOT NULL,
    network jsonb DEFAULT '{}'::jsonb NOT NULL,
    active_tasks jsonb DEFAULT '[]'::jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT system_status_id_check CHECK ((id = '00000000-0000-0000-0000-000000000001'::uuid))
);

ALTER TABLE ONLY public.system_status REPLICA IDENTITY FULL;


--
-- Name: TABLE system_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.system_status IS 'DEPRECATED 2026-05-04 (A-route A2). Snapshot moved to Redis HASH
     mediahub:system:status (see app/services/system_status_redis.py).
     Table no longer receives writes. Will be dropped in a follow-up
     migration after the 30-day deprecation window. Read from /api/v1/
     system/status (Redis-backed) instead.';


--
-- Name: tag_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tag_groups (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    name character varying(50) NOT NULL,
    sort_order integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: tags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tags (
    name character varying(50) NOT NULL,
    type character varying(20) NOT NULL,
    color character varying(20) DEFAULT '#6366f1'::character varying,
    icon character varying(50),
    user_id uuid,
    created_at timestamp with time zone DEFAULT now(),
    name_zh character varying(50),
    scope_id bigint,
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    group_id bigint,
    sort_order integer DEFAULT 0,
    enabled boolean DEFAULT true NOT NULL,
    CONSTRAINT tags_type_check CHECK (((type)::text = ANY (ARRAY[('system'::character varying)::text, ('user'::character varying)::text, ('time'::character varying)::text])))
);

ALTER TABLE ONLY public.tags REPLICA IDENTITY FULL;


--
-- Name: COLUMN tags.enabled; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tags.enabled IS 'Whether this tag is visible in the frontend API.';


--
-- Name: task_flows; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.task_flows (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    name text NOT NULL,
    state text DEFAULT 'running'::text NOT NULL,
    cascade_cancel boolean DEFAULT true NOT NULL,
    total_tasks integer DEFAULT 0 NOT NULL,
    completed_tasks integer DEFAULT 0 NOT NULL,
    failed_tasks integer DEFAULT 0 NOT NULL,
    cancelled_tasks integer DEFAULT 0 NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT task_flows_state_check CHECK ((state = ANY (ARRAY['running'::text, 'completed'::text, 'failed'::text, 'cancelled'::text, 'partial'::text])))
);


--
-- Name: TABLE task_flows; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.task_flows IS 'Parent grouping for related tasks. One flow = one user submission
     (e.g., "Process URL X"). Child tasks reference via task_tracking.flow_id.
     Aggregate counters maintained by trigger trg_task_tracking_flow_aggregate.';


--
-- Name: COLUMN task_flows.state; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.task_flows.state IS 'running / completed (all children done OK) / failed (any child failed
     and cascade_cancel=true) / cancelled (user cancelled) / partial
     (mixed: some completed, some cancelled — terminal but not pure success)';


--
-- Name: COLUMN task_flows.cascade_cancel; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.task_flows.cascade_cancel IS 'When true, cancelling the flow cancels every non-terminal child task
     via the lifecycle bus. When false, cancel marks the flow alone.';


--
-- Name: task_tracking; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.task_tracking (
    user_id uuid NOT NULL,
    task_type character varying(20) NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    resource_id text,
    media_id text,
    dbos_workflow_id text NOT NULL,
    progress smallint DEFAULT 0,
    speed bigint,
    total_bytes bigint,
    title text NOT NULL,
    subtitle text,
    error_msg text,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now(),
    group_id uuid,
    phase text DEFAULT 'queued'::text,
    dedup_key text,
    subscribers jsonb DEFAULT '[]'::jsonb,
    error_code text,
    issue_id bigint,
    cost_cents integer DEFAULT 0 NOT NULL,
    heartbeat_at timestamp with time zone,
    health_status text,
    health_notified_at timestamp with time zone,
    max_duration_minutes integer,
    expected_duration_minutes integer,
    do_not_auto_cancel boolean DEFAULT false NOT NULL,
    flow_id uuid,
    task_kind text DEFAULT 'workflow'::text NOT NULL,
    agent_id uuid,
    parent_task_id text,
    root_task_id text,
    inbox_message_id uuid,
    CONSTRAINT task_tracking_task_kind_check CHECK ((task_kind = ANY (ARRAY['workflow'::text, 'agent_task'::text]))),
    CONSTRAINT unified_tasks_progress_check CHECK (((progress >= 0) AND (progress <= 100)))
);

ALTER TABLE ONLY public.task_tracking REPLICA IDENTITY FULL;


--
-- Name: TABLE task_tracking; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.task_tracking IS '通用任务追踪表（multi-task_kind）。
   task_kind=''workflow''   → DBOS workflow，1:1 mirror dbos.workflow_status，
                              status 由 mirror_dbos_lifecycle_to_tracking trigger 写
   task_kind=''agent_task'' → 应用层（agent_workforce）维护 lifecycle，
                              不依赖 dbos.workflow_status；
                              phase 列保留 8 状态精度 (queued / assigned /
                              in_progress / waiting_for_other / blocked /
                              done / failed / cancelled)
   原 1:1 sidecar FK (task_tracking_dbos_fk) 在 A4 (migration 200) 移除，
   cascade GC 失效 — 由应用层负责清理过期任务。';


--
-- Name: COLUMN task_tracking.status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.task_tracking.status IS 'Mirror of DBOS lifecycle (5-state). Updated by trigger ONLY — never
   write directly. Source of truth: dbos.workflow_status.status.';


--
-- Name: COLUMN task_tracking.dbos_workflow_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.task_tracking.dbos_workflow_id IS 'PK; equals dbos.workflow_status.workflow_uuid (FK CASCADE).';


--
-- Name: COLUMN task_tracking.group_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.task_tracking.group_id IS 'Groups related sub-tasks (e.g. 3 AI steps for same media)';


--
-- Name: COLUMN task_tracking.phase; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.task_tracking.phase IS 'Free-form business phase (e.g. parsing/downloading/waiting_for_human_review).
   Application code is the truth source for this column. DBOS lifecycle stays
   in `status` column; phase is finer-grained business detail.';


--
-- Name: COLUMN task_tracking.issue_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.task_tracking.issue_id IS 'Back-reference to issues.id. NULL for legacy rows (pre-PR-D1) and system-only tasks (scheduled cleanup, etc.). User-facing tasks set this on creation.';


--
-- Name: COLUMN task_tracking.cost_cents; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.task_tracking.cost_cents IS 'Aggregated AI cost in cents for tasks that triggered AI workflows. Updated
   by application code reading agent_runs at task completion time.';


--
-- Name: COLUMN task_tracking.heartbeat_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.task_tracking.heartbeat_at IS 'Set by the workflow body itself every ~30s. NULL = workflow has not
     started writing heartbeats yet. Stale = worker died (LOST).';


--
-- Name: COLUMN task_tracking.health_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.task_tracking.health_status IS 'Last classification by workflow_health_sweeper.
     One of: HEALTHY / SLOW / STUCK_IN_STEP / STALLED / LOST / ORPHAN_PENDING / NULL.';


--
-- Name: COLUMN task_tracking.health_notified_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.task_tracking.health_notified_at IS 'Last time we surfaced a "running long" notification to the user.
     Used to dedupe — we notify once per classification flip.';


--
-- Name: COLUMN task_tracking.max_duration_minutes; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.task_tracking.max_duration_minutes IS 'User-set hard cap. NULL = use workflow_timeout_policy.hard_ceiling.
     The sweeper RESPECTS this even when do_not_auto_cancel=false:
     surpassing max_duration_minutes flips status=timed_out (which IS
     auto-action; user opted into it by setting the field).';


--
-- Name: COLUMN task_tracking.expected_duration_minutes; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.task_tracking.expected_duration_minutes IS 'User-declared "this is supposed to be slow". Above this we DO NOT
     emit "running long" notifications. Below max_duration_minutes still
     auto-times-out.';


--
-- Name: COLUMN task_tracking.do_not_auto_cancel; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.task_tracking.do_not_auto_cancel IS 'Opt out of any sweeper-driven action. Even ORPHAN_PENDING and LOST
     classifications only log; never flip status. User must cancel manually.';


--
-- Name: COLUMN task_tracking.flow_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.task_tracking.flow_id IS 'Parent flow this task belongs to. NULL for unflow''d tasks (legacy
     ad-hoc dispatches still allowed). FK ON DELETE SET NULL so deleting
     a flow doesn''t cascade-delete child tasks (keep them for history).';


--
-- Name: COLUMN task_tracking.task_kind; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.task_tracking.task_kind IS 'workflow | agent_task — 决定 status 列的写入权（trigger vs 应用层）';


--
-- Name: COLUMN task_tracking.agent_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.task_tracking.agent_id IS 'task_kind=agent_task 时填，引用 ai_agents.id；workflow 时为 NULL';


--
-- Name: COLUMN task_tracking.parent_task_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.task_tracking.parent_task_id IS '父任务的 dbos_workflow_id (TEXT)，跨 task_kind 通用；DBOS 自身 parent_workflow_id 仍存在 dbos.workflow_status，本列是应用层快查路径';


--
-- Name: COLUMN task_tracking.root_task_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.task_tracking.root_task_id IS '根任务的 dbos_workflow_id (TEXT)，跨 task_kind 通用';


--
-- Name: COLUMN task_tracking.inbox_message_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.task_tracking.inbox_message_id IS 'task_kind=agent_task 时填，引用 agent_inbox.id（触发该 task 的入站消息）';


--
-- Name: team_invites; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.team_invites (
    code character varying(20) NOT NULL,
    created_by uuid,
    expires_at timestamp with time zone,
    max_uses integer,
    use_count integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now(),
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    team_id bigint NOT NULL
);

ALTER TABLE ONLY public.team_invites REPLICA IDENTITY FULL;


--
-- Name: team_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.team_members (
    user_id uuid NOT NULL,
    role character varying(20) DEFAULT 'member'::character varying,
    joined_at timestamp with time zone DEFAULT now(),
    team_id bigint NOT NULL,
    CONSTRAINT team_members_role_check CHECK (((role)::text = ANY (ARRAY[('owner'::character varying)::text, ('admin'::character varying)::text, ('member'::character varying)::text])))
);

ALTER TABLE ONLY public.team_members REPLICA IDENTITY FULL;


--
-- Name: team_plans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.team_plans (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    plan_tier character varying(30) DEFAULT 'free'::character varying NOT NULL,
    max_seats integer DEFAULT 1 NOT NULL,
    max_storage_bytes bigint DEFAULT '5368709120'::bigint NOT NULL,
    max_project_members integer DEFAULT 5 NOT NULL,
    custom_permissions boolean DEFAULT false NOT NULL,
    custom_workflows boolean DEFAULT false NOT NULL,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    team_id bigint NOT NULL,
    CONSTRAINT team_plans_plan_tier_check CHECK (((plan_tier)::text = ANY (ARRAY[('free'::character varying)::text, ('studio_standard'::character varying)::text, ('studio_pro'::character varying)::text, ('enterprise_standard'::character varying)::text, ('enterprise_pro'::character varying)::text, ('enterprise_flagship'::character varying)::text])))
);


--
-- Name: team_quotas; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.team_quotas (
    points_balance integer DEFAULT 0 NOT NULL,
    storage_limit_bytes bigint DEFAULT '5368709120'::bigint NOT NULL,
    storage_used_bytes bigint DEFAULT 0 NOT NULL,
    free_points_granted boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    team_id bigint NOT NULL
);


--
-- Name: teams; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.teams (
    name character varying(100) NOT NULL,
    owner_id uuid NOT NULL,
    invite_code character varying(20) NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    enabled_modules jsonb DEFAULT '["parser", "resources", "library", "projects", "ai_analysis", "dashboard", "cleanup"]'::jsonb,
    settings_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    kind text DEFAULT 'collaborative'::text NOT NULL,
    CONSTRAINT teams_kind_check CHECK ((kind = ANY (ARRAY['personal'::text, 'collaborative'::text])))
);

ALTER TABLE ONLY public.teams REPLICA IDENTITY FULL;


--
-- Name: COLUMN teams.settings_json; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.teams.settings_json IS 'Per-team settings as JSON. Known keys: chat_temp_ttl_days (int days, -1 = never expire).';


--
-- Name: COLUMN teams.kind; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.teams.kind IS 'personal = auto-created single-member team; collaborative = user-created multi-member team. See ID-unification design 2026-05-28.';


--
-- Name: temp_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.temp_tokens (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    token character varying(64) NOT NULL,
    user_id uuid NOT NULL,
    scopes text[] DEFAULT '{}'::text[] NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    selection text[] DEFAULT '{}'::text[]
);


--
-- Name: TABLE temp_tokens; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.temp_tokens IS 'Short-lived tokens for secure web page access (e.g. Shortcuts tag picker)';


--
-- Name: topic_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.topic_groups (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    user_id uuid,
    label text NOT NULL,
    source_count integer DEFAULT 1 NOT NULL,
    heat numeric DEFAULT 0 NOT NULL,
    first_seen timestamp with time zone DEFAULT now() NOT NULL,
    last_seen timestamp with time zone DEFAULT now() NOT NULL,
    embedding public.vector(2048),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    source_labels text[] DEFAULT '{}'::text[] NOT NULL
);


--
-- Name: COLUMN topic_groups.embedding; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.topic_groups.embedding IS 'pgvector(2048) cluster centroid (seed = first member). Unindexed; seq cosine
     scan over the active-window groups. Matches hotspots.embedding dimension.';


--
-- Name: user_cookies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_cookies (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    user_id uuid NOT NULL,
    platform character varying(50) NOT NULL,
    cookie_text text,
    cookie_file text,
    is_valid boolean DEFAULT true NOT NULL,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    custom_headers text
);


--
-- Name: user_credits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_credits (
    user_id uuid NOT NULL,
    balance integer DEFAULT 0 NOT NULL,
    total_earned integer DEFAULT 0 NOT NULL,
    total_spent integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE user_credits; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.user_credits IS 'User credit accounts for tracking balance and usage';


--
-- Name: COLUMN user_credits.balance; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_credits.balance IS 'Current available credits';


--
-- Name: COLUMN user_credits.total_earned; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_credits.total_earned IS 'Total credits earned (recharges, gifts, etc.)';


--
-- Name: COLUMN user_credits.total_spent; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_credits.total_spent IS 'Total credits spent on actions';


--
-- Name: user_hidden_sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_hidden_sources (
    user_id uuid NOT NULL,
    source_id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: user_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_logs (
    user_id uuid NOT NULL,
    action character varying(50) NOT NULL,
    message text NOT NULL,
    status character varying(20) DEFAULT 'info'::character varying,
    aweme_id character varying(50),
    details jsonb,
    created_at timestamp with time zone DEFAULT now(),
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL
);

ALTER TABLE ONLY public.user_logs REPLICA IDENTITY FULL;


--
-- Name: TABLE user_logs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.user_logs IS '用户操作日志表';


--
-- Name: COLUMN user_logs.action; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_logs.action IS '操作类型: fetch, download, delete, retry, update, login, logout';


--
-- Name: COLUMN user_logs.status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_logs.status IS '状态: success, error, warning, info, pending';


--
-- Name: user_mcp_servers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_mcp_servers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    name text NOT NULL,
    url text NOT NULL,
    bearer_token text,
    description text,
    enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT user_mcp_servers_name_check CHECK ((name ~ '^[A-Za-z0-9_]+$'::text)),
    CONSTRAINT user_mcp_servers_url_check CHECK ((url ~ '^https?://'::text))
);


--
-- Name: TABLE user_mcp_servers; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.user_mcp_servers IS 'G1+G5: per-user outbound MCP server registrations. Used by ai_library_chat_wiring to construct an MCPOutboundRegistry for each chat turn so the AgentRunner can dispatch to those servers.';


--
-- Name: user_notifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_notifications (
    user_id uuid NOT NULL,
    notification_id bigint NOT NULL,
    read_at timestamp with time zone
);

ALTER TABLE ONLY public.user_notifications REPLICA IDENTITY FULL;


--
-- Name: user_profiles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_profiles (
    id uuid NOT NULL,
    username character varying(255),
    avatar_url text,
    role public.user_role DEFAULT 'user'::public.user_role,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    is_banned boolean DEFAULT false NOT NULL,
    display_id bigint DEFAULT public.generate_snowflake_id() NOT NULL
);

ALTER TABLE ONLY public.user_profiles REPLICA IDENTITY FULL;


--
-- Name: COLUMN user_profiles.is_banned; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_profiles.is_banned IS 'Whether the user is banned from the platform';


--
-- Name: user_schedules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_schedules (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid,
    name text NOT NULL,
    cron_expr text NOT NULL,
    task_type text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    lane text DEFAULT 'scheduled'::text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    last_fired_at timestamp with time zone,
    next_fire_at timestamp with time zone NOT NULL,
    fire_count integer DEFAULT 0 NOT NULL,
    fail_count integer DEFAULT 0 NOT NULL,
    last_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE user_schedules; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.user_schedules IS 'User- or system-configured cron schedules. Master scheduler workflow
     (app/workflows/scheduled_master.py) scans this table every minute and
     dispatches due rows. Replaces hard-coded @DBOS.scheduled decorators
     for user-facing recurring tasks; internal DBOS sweepers still use
     the decorator pattern because they''re system primitives.';


--
-- Name: COLUMN user_schedules.user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_schedules.user_id IS 'Owner of the schedule. NULL means system-owned (operator-managed
     via DB / admin tools, not user-facing UI).';


--
-- Name: COLUMN user_schedules.cron_expr; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_schedules.cron_expr IS '5-field cron expression in UTC (m h dom mon dow). Validated by
     croniter on insert/update at the service layer (DB doesn''t parse
     cron). Examples: "0 9 * * *" daily 9am UTC, "*/15 * * * *" every
     15 min, "0 0 * * 0" weekly Sunday midnight.';


--
-- Name: COLUMN user_schedules.next_fire_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_schedules.next_fire_at IS 'Pre-computed next fire time, kept in sync by the master scheduler
     after each fire (or by service layer on insert/update). The
     idx_user_schedules_due index makes finding due rows O(log n).';


--
-- Name: user_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_settings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    download_path text DEFAULT '/home/user/downloads/douyin'::text,
    settings_json jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    canvas_mode_preference text,
    CONSTRAINT user_settings_canvas_mode_preference_check CHECK (((canvas_mode_preference IS NULL) OR (canvas_mode_preference = ANY (ARRAY['smart'::text, 'classic'::text]))))
);


--
-- Name: TABLE user_settings; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.user_settings IS '用户个人设置表';


--
-- Name: COLUMN user_settings.user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_settings.user_id IS '用户ID，关联 auth.users';


--
-- Name: COLUMN user_settings.download_path; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_settings.download_path IS '默认下载路径';


--
-- Name: COLUMN user_settings.settings_json; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_settings.settings_json IS 'Per-user settings as JSON. Known keys: chat_temp_ttl_days (int days, -1 = never expire).';


--
-- Name: COLUMN user_settings.canvas_mode_preference; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_settings.canvas_mode_preference IS 'Per-user default mode for new canvases. NULL = follow canvas.kind
   (no user-level override). smart/classic = force that mode when the
   user opens a new canvas.';


--
-- Name: user_tag_preferences; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_tag_preferences (
    user_id uuid NOT NULL,
    starred_tag_ids text[] DEFAULT '{}'::text[],
    picker_settings jsonb DEFAULT '{"layout": "list", "showCount": true, "columnWidth": "medium", "showStarred": true, "showRecently": true, "showRecommended": false}'::jsonb,
    panel_size jsonb DEFAULT '{"width": 480, "height": 400}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: user_topic_interests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_topic_interests (
    user_id uuid NOT NULL,
    interest_text text DEFAULT ''::text NOT NULL,
    embedding public.vector(2048),
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: v_admin_cache_hit_rate_30d; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_admin_cache_hit_rate_30d AS
 WITH per_event AS (
         SELECT date_trunc('day'::text, agent_run_events.created_at) AS day,
            (((agent_run_events.cost_snapshot -> 'tokens'::text) ->> 'input'::text))::bigint AS input_tokens,
            (((agent_run_events.cost_snapshot -> 'tokens'::text) ->> 'cache_read'::text))::bigint AS cache_read_tokens,
            (((agent_run_events.cost_snapshot -> 'cost'::text) ->> 'saved_by_cache_usd'::text))::numeric AS saved_usd
           FROM public.agent_run_events
          WHERE ((agent_run_events.cost_snapshot IS NOT NULL) AND (agent_run_events.created_at >= (now() - '30 days'::interval)))
        )
 SELECT day,
    sum(input_tokens) AS input_tokens,
    sum(cache_read_tokens) AS cache_read_tokens,
    round(((100.0 * sum(cache_read_tokens)) / NULLIF(sum((input_tokens + cache_read_tokens)), (0)::numeric)), 2) AS hit_rate_pct,
    sum(saved_usd) AS saved_usd
   FROM per_event
  GROUP BY GROUPING SETS ((day), ())
  ORDER BY day;


--
-- Name: VIEW v_admin_cache_hit_rate_30d; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_admin_cache_hit_rate_30d IS 'Phase 0.5-D Appendix D.1. Daily cache hit rate + 30-day rollup (day=NULL).
   hit_rate_pct = cache_read / (input + cache_read).';


--
-- Name: v_admin_cost_anomalies_24h; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_admin_cost_anomalies_24h AS
 WITH baseline AS (
         SELECT r.agent_id,
            avg((((e.cost_snapshot -> 'cost'::text) ->> 'discounted_usd'::text))::numeric) AS mean_usd,
            stddev_pop((((e.cost_snapshot -> 'cost'::text) ->> 'discounted_usd'::text))::numeric) AS stddev_usd,
            count(*) AS sample_size
           FROM (public.agent_run_events e
             JOIN public.agent_runs r ON ((r.id = e.run_id)))
          WHERE ((e.cost_snapshot IS NOT NULL) AND (e.created_at >= (now() - '30 days'::interval)) AND (e.created_at < (now() - '24:00:00'::interval)))
          GROUP BY r.agent_id
         HAVING (count(*) >= 30)
        ), recent AS (
         SELECT e.id,
            e.run_id,
            e.iteration,
            r.agent_id,
            r.user_id,
            a.slug AS agent_slug,
            (((e.cost_snapshot -> 'cost'::text) ->> 'discounted_usd'::text))::numeric AS event_usd,
            (e.cost_snapshot ->> 'model_slug'::text) AS model_slug,
            e.created_at
           FROM ((public.agent_run_events e
             JOIN public.agent_runs r ON ((r.id = e.run_id)))
             LEFT JOIN public.ai_agents a ON ((a.id = r.agent_id)))
          WHERE ((e.cost_snapshot IS NOT NULL) AND (e.created_at >= (now() - '24:00:00'::interval)))
        )
 SELECT recent.id AS event_id,
    recent.run_id,
    recent.iteration,
    recent.agent_slug,
    recent.model_slug,
    recent.user_id,
    recent.event_usd,
    baseline.mean_usd AS baseline_mean_usd,
    baseline.stddev_usd AS baseline_stddev_usd,
    round(((recent.event_usd - baseline.mean_usd) / NULLIF(baseline.stddev_usd, (0)::numeric)), 2) AS sigmas_above,
    recent.created_at
   FROM (recent
     JOIN baseline ON ((baseline.agent_id = recent.agent_id)))
  WHERE ((baseline.stddev_usd > (0)::numeric) AND (recent.event_usd > (baseline.mean_usd + ((3)::numeric * baseline.stddev_usd))))
  ORDER BY (round(((recent.event_usd - baseline.mean_usd) / NULLIF(baseline.stddev_usd, (0)::numeric)), 2)) DESC NULLS LAST;


--
-- Name: VIEW v_admin_cost_anomalies_24h; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_admin_cost_anomalies_24h IS 'Phase 0.5-D Appendix D.3. Events from last 24h that are >3σ above the
   agent''s 30-day baseline (baseline excludes the last 24h, needs ≥30
   samples). Catches runaway loops / broken tool calls.';


--
-- Name: v_admin_cost_by_agent_slug_30d; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_admin_cost_by_agent_slug_30d AS
 SELECT COALESCE(a.slug, '(unknown)'::character varying) AS agent_slug,
    a.name AS agent_name,
    count(e.*) AS call_count,
    count(DISTINCT e.run_id) AS run_count,
    sum((((e.cost_snapshot -> 'cost'::text) ->> 'discounted_usd'::text))::numeric) AS total_usd,
    avg((((e.cost_snapshot -> 'cost'::text) ->> 'discounted_usd'::text))::numeric) AS avg_per_call_usd,
    sum(((((((e.cost_snapshot -> 'tokens'::text) ->> 'input'::text))::bigint + (((e.cost_snapshot -> 'tokens'::text) ->> 'output'::text))::bigint) + (((e.cost_snapshot -> 'tokens'::text) ->> 'cache_read'::text))::bigint) + (((e.cost_snapshot -> 'tokens'::text) ->> 'cache_write'::text))::bigint)) AS total_tokens
   FROM ((public.agent_run_events e
     JOIN public.agent_runs r ON ((r.id = e.run_id)))
     LEFT JOIN public.ai_agents a ON ((a.id = r.agent_id)))
  WHERE ((e.cost_snapshot IS NOT NULL) AND (e.created_at >= (now() - '30 days'::interval)))
  GROUP BY a.slug, a.name
  ORDER BY (sum((((e.cost_snapshot -> 'cost'::text) ->> 'discounted_usd'::text))::numeric)) DESC NULLS LAST;


--
-- Name: VIEW v_admin_cost_by_agent_slug_30d; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_admin_cost_by_agent_slug_30d IS 'Phase 0.5-D Appendix B.4. Last 30 days cost by ai_agents.slug.
   Includes per-call avg so the highest-impact prompts surface.';


--
-- Name: v_admin_cost_by_provider_30d; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_admin_cost_by_provider_30d AS
 SELECT (cost_snapshot ->> 'provider_slug'::text) AS provider_slug,
    count(*) AS call_count,
    sum((((cost_snapshot -> 'cost'::text) ->> 'discounted_usd'::text))::numeric) AS total_usd,
    sum((((cost_snapshot -> 'cost'::text) ->> 'saved_by_cache_usd'::text))::numeric) AS saved_by_cache_usd,
    sum(((((((cost_snapshot -> 'tokens'::text) ->> 'input'::text))::bigint + (((cost_snapshot -> 'tokens'::text) ->> 'output'::text))::bigint) + (((cost_snapshot -> 'tokens'::text) ->> 'cache_read'::text))::bigint) + (((cost_snapshot -> 'tokens'::text) ->> 'cache_write'::text))::bigint)) AS total_tokens,
    round(((100.0 * sum((((cost_snapshot -> 'cost'::text) ->> 'discounted_usd'::text))::numeric)) / NULLIF(sum(sum((((cost_snapshot -> 'cost'::text) ->> 'discounted_usd'::text))::numeric)) OVER (), (0)::numeric)), 2) AS share_pct
   FROM public.agent_run_events
  WHERE ((cost_snapshot IS NOT NULL) AND (created_at >= (now() - '30 days'::interval)))
  GROUP BY (cost_snapshot ->> 'provider_slug'::text)
  ORDER BY (sum((((cost_snapshot -> 'cost'::text) ->> 'discounted_usd'::text))::numeric)) DESC NULLS LAST;


--
-- Name: VIEW v_admin_cost_by_provider_30d; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_admin_cost_by_provider_30d IS 'Phase 0.5-D Appendix B.1. Last 30 days cost share by provider_slug.
   share_pct uses window function to compute % of total.';


--
-- Name: v_admin_outcome_distribution_30d; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_admin_outcome_distribution_30d AS
 WITH run_cost AS (
         SELECT r.id AS run_id,
            r.outcome,
            r.created_at AS run_created_at,
            sum((((e.cost_snapshot -> 'cost'::text) ->> 'discounted_usd'::text))::numeric) AS run_usd
           FROM (public.agent_runs r
             LEFT JOIN public.agent_run_events e ON (((e.run_id = r.id) AND (e.cost_snapshot IS NOT NULL))))
          WHERE (r.created_at >= (now() - '30 days'::interval))
          GROUP BY r.id, r.outcome, r.created_at
        )
 SELECT COALESCE(outcome, '(unmarked)'::text) AS outcome,
    count(*) AS run_count,
    round(((100.0 * (count(*))::numeric) / sum(count(*)) OVER ()), 2) AS share_pct,
    sum(run_usd) AS total_usd,
    avg(run_usd) AS avg_per_run_usd
   FROM run_cost
  GROUP BY outcome
  ORDER BY
        CASE COALESCE(outcome, '(unmarked)'::text)
            WHEN 'user_accepted'::text THEN 1
            WHEN 'pending'::text THEN 2
            WHEN 'user_rejected'::text THEN 3
            WHEN 'timeout'::text THEN 4
            ELSE 5
        END;


--
-- Name: VIEW v_admin_outcome_distribution_30d; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_admin_outcome_distribution_30d IS 'Phase 0.5-D Appendix E.1. Last 30 days outcome distribution.
   Captures runs where the user verdict is recorded; (unmarked) bucket
   shows runs with no outcome (legacy or in-flight).';


--
-- Name: v_admin_today_total_cost; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_admin_today_total_cost AS
 WITH today AS (
         SELECT (((agent_run_events.cost_snapshot -> 'cost'::text) ->> 'discounted_usd'::text))::numeric AS discounted_usd,
            (((agent_run_events.cost_snapshot -> 'cost'::text) ->> 'raw_usd'::text))::numeric AS raw_usd,
            (((agent_run_events.cost_snapshot -> 'cost'::text) ->> 'saved_by_cache_usd'::text))::numeric AS saved_by_cache_usd,
            (((agent_run_events.cost_snapshot -> 'cost'::text) ->> 'local_cents'::text))::numeric AS local_cents,
            ((agent_run_events.cost_snapshot -> 'cost'::text) ->> 'local_currency'::text) AS local_currency
           FROM public.agent_run_events
          WHERE ((agent_run_events.cost_snapshot IS NOT NULL) AND (agent_run_events.created_at >= date_trunc('day'::text, (now() AT TIME ZONE 'UTC'::text))) AND (agent_run_events.created_at < (date_trunc('day'::text, (now() AT TIME ZONE 'UTC'::text)) + '1 day'::interval)))
        )
 SELECT COALESCE(local_currency, 'USD'::text) AS currency,
    count(*) AS call_count,
    sum(discounted_usd) AS total_usd,
    sum(raw_usd) AS raw_usd,
    sum(saved_by_cache_usd) AS saved_by_cache_usd,
    (sum(local_cents) / 100.0) AS total_local,
    round(((100.0 * sum(saved_by_cache_usd)) / NULLIF(sum(raw_usd), (0)::numeric)), 2) AS cache_savings_pct
   FROM today
  GROUP BY GROUPING SETS ((local_currency), ());


--
-- Name: VIEW v_admin_today_total_cost; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_admin_today_total_cost IS 'Phase 0.5-D Appendix A.1. Today total cost, USD + per local currency.
   GROUPING SETS gives one row per currency + one rollup row (currency=USD).';


--
-- Name: v_admin_top_users_cost_30d; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_admin_top_users_cost_30d AS
 SELECT r.user_id,
    r.team_id,
    count(e.*) AS call_count,
    count(DISTINCT e.run_id) AS run_count,
    sum((((e.cost_snapshot -> 'cost'::text) ->> 'discounted_usd'::text))::numeric) AS total_usd,
    max(e.created_at) AS last_call_at
   FROM (public.agent_run_events e
     JOIN public.agent_runs r ON ((r.id = e.run_id)))
  WHERE ((e.cost_snapshot IS NOT NULL) AND (e.created_at >= (now() - '30 days'::interval)))
  GROUP BY r.user_id, r.team_id
  ORDER BY (sum((((e.cost_snapshot -> 'cost'::text) ->> 'discounted_usd'::text))::numeric)) DESC NULLS LAST;


--
-- Name: VIEW v_admin_top_users_cost_30d; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_admin_top_users_cost_30d IS 'Phase 0.5-D Appendix C.1. Last 30 days cost per user (with team).
   Admin app applies LIMIT 10 / 50. Already ordered DESC by total_usd.';


--
-- Name: worker_registry; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.worker_registry (
    executor_id text NOT NULL,
    boot_generation uuid NOT NULL,
    app_version text,
    pid integer,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    heartbeat_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE worker_registry; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.worker_registry IS 'Per-process worker liveness + boot-generation fencing token. Written by the health sweeper on the worker; not user data. See Worker Foundation plan.';


--
-- Name: COLUMN worker_registry.boot_generation; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.worker_registry.boot_generation IS 'uuid minted once per process boot — fencing token for orphan detection (P3).';


--
-- Name: COLUMN worker_registry.heartbeat_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.worker_registry.heartbeat_at IS 'Last refresh; stale ⇒ that worker process is presumed gone (observe-only in P1).';


--
-- Name: workflow_nodes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workflow_nodes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workflow_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    status_type character varying(20) NOT NULL,
    node_type character varying(30) DEFAULT 'status'::character varying NOT NULL,
    sort_order integer NOT NULL,
    color character varying(20),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT workflow_nodes_node_type_check CHECK (((node_type)::text = ANY (ARRAY[('status'::character varying)::text, ('milestone'::character varying)::text, ('gate'::character varying)::text]))),
    CONSTRAINT workflow_nodes_status_type_check CHECK (((status_type)::text = ANY (ARRAY[('not_started'::character varying)::text, ('in_progress'::character varying)::text, ('completed'::character varying)::text])))
);


--
-- Name: workflow_timeout_policy; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workflow_timeout_policy (
    task_type text NOT NULL,
    expected_duration_seconds integer NOT NULL,
    hard_ceiling_seconds integer NOT NULL,
    heartbeat_stale_seconds integer DEFAULT 300 NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT workflow_timeout_policy_check CHECK ((hard_ceiling_seconds >= expected_duration_seconds))
);


--
-- Name: TABLE workflow_timeout_policy; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.workflow_timeout_policy IS 'Per-task-type duration policy used by the workflow health classifier.
     expected_duration_seconds = when we tell the user "running long".
     hard_ceiling_seconds = upper bound for ORPHAN_PENDING detection.
     heartbeat_stale_seconds = how long without heartbeat = LOST.';


--
-- Name: COLUMN workflow_timeout_policy.expected_duration_seconds; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.workflow_timeout_policy.expected_duration_seconds IS 'Beyond this, user gets a "running long" notification (no auto-action).';


--
-- Name: COLUMN workflow_timeout_policy.hard_ceiling_seconds; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.workflow_timeout_policy.hard_ceiling_seconds IS 'A workflow stuck PENDING (never picked up by executor) for hard_ceiling × 3 = ORPHAN_PENDING. Safe to auto-cancel because the body never ran.';


--
-- Name: COLUMN workflow_timeout_policy.heartbeat_stale_seconds; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.workflow_timeout_policy.heartbeat_stale_seconds IS 'Heartbeat older than this with status=RUNNING ⇒ LOST (worker died).';


--
-- Name: zzz_deprecated_storyboard_assets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.zzz_deprecated_storyboard_assets (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    project_id bigint NOT NULL,
    file_path text NOT NULL,
    file_hash character varying(64),
    file_size bigint,
    mime_type character varying(50),
    width integer,
    height integer,
    preview_path text,
    metadata_json jsonb,
    source_type character varying(20) DEFAULT 'generated'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT storyboard_assets_source_type_check CHECK (((source_type)::text = ANY (ARRAY[('uploaded'::character varying)::text, ('generated'::character varying)::text, ('split'::character varying)::text, ('imported'::character varying)::text])))
);


--
-- Name: TABLE zzz_deprecated_storyboard_assets; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.zzz_deprecated_storyboard_assets IS 'Retired 2026-07-07 (Phase B P4 cutover, PR-V2 #1109 tombstoned the routes with 410). Restore: ALTER TABLE RENAME back. See docs/superpowers/plans/2026-07-07-phase-b-p4-versioning.md Task 5.';


--
-- Name: zzz_deprecated_storyboard_characters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.zzz_deprecated_storyboard_characters (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    project_id bigint NOT NULL,
    name character varying(100) NOT NULL,
    description text,
    reference_image_url text,
    thumbnail_url text,
    visual_traits jsonb,
    sort_order integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE zzz_deprecated_storyboard_characters; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.zzz_deprecated_storyboard_characters IS 'Retired 2026-07-07 (Phase B P4 cutover, PR-V2 #1109 tombstoned the routes with 410). Restore: ALTER TABLE RENAME back. See docs/superpowers/plans/2026-07-07-phase-b-p4-versioning.md Task 5.';


--
-- Name: zzz_deprecated_storyboard_edges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.zzz_deprecated_storyboard_edges (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    project_id bigint NOT NULL,
    source_node_id bigint NOT NULL,
    target_node_id bigint NOT NULL,
    source_handle character varying(50),
    target_handle character varying(50),
    edge_type character varying(20) DEFAULT 'default'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE zzz_deprecated_storyboard_edges; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.zzz_deprecated_storyboard_edges IS 'Retired 2026-07-07 (Phase B P4 cutover, PR-V2 #1109 tombstoned the routes with 410). Restore: ALTER TABLE RENAME back. See docs/superpowers/plans/2026-07-07-phase-b-p4-versioning.md Task 5.';


--
-- Name: zzz_deprecated_storyboard_frame_characters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.zzz_deprecated_storyboard_frame_characters (
    frame_id bigint NOT NULL,
    character_id bigint NOT NULL
);


--
-- Name: TABLE zzz_deprecated_storyboard_frame_characters; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.zzz_deprecated_storyboard_frame_characters IS 'Retired 2026-07-07 (Phase B P4 cutover, PR-V2 #1109 tombstoned the routes with 410). Restore: ALTER TABLE RENAME back. See docs/superpowers/plans/2026-07-07-phase-b-p4-versioning.md Task 5.';


--
-- Name: zzz_deprecated_storyboard_frames; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.zzz_deprecated_storyboard_frames (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    node_id bigint NOT NULL,
    project_id bigint NOT NULL,
    frame_index integer NOT NULL,
    image_url text,
    thumbnail_url text,
    note text,
    shot_type character varying(30),
    camera_angle character varying(30),
    camera_movement character varying(30),
    focal_length character varying(20),
    lighting text,
    duration_seconds double precision DEFAULT 3.0 NOT NULL,
    transition_type character varying(20) DEFAULT 'cut'::character varying NOT NULL,
    annotations_json jsonb,
    sort_order integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT storyboard_frames_transition_type_check CHECK (((transition_type)::text = ANY (ARRAY[('cut'::character varying)::text, ('fade'::character varying)::text, ('dissolve'::character varying)::text])))
);


--
-- Name: TABLE zzz_deprecated_storyboard_frames; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.zzz_deprecated_storyboard_frames IS 'Retired 2026-07-07 (Phase B P4 cutover, PR-V2 #1109 tombstoned the routes with 410). Restore: ALTER TABLE RENAME back. See docs/superpowers/plans/2026-07-07-phase-b-p4-versioning.md Task 5.';


--
-- Name: zzz_deprecated_storyboard_nodes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.zzz_deprecated_storyboard_nodes (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    project_id bigint NOT NULL,
    node_type character varying(30) NOT NULL,
    position_x double precision DEFAULT 0 NOT NULL,
    position_y double precision DEFAULT 0 NOT NULL,
    width double precision,
    height double precision,
    data_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    sort_order integer,
    locked boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT storyboard_nodes_node_type_check CHECK (((node_type)::text = ANY (ARRAY[('upload'::character varying)::text, ('image_edit'::character varying)::text, ('storyboard_split'::character varying)::text, ('storyboard_gen'::character varying)::text, ('text_annotation'::character varying)::text, ('group'::character varying)::text, ('export'::character varying)::text, ('image_to_video'::character varying)::text])))
);


--
-- Name: TABLE zzz_deprecated_storyboard_nodes; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.zzz_deprecated_storyboard_nodes IS 'Retired 2026-07-07 (Phase B P4 cutover, PR-V2 #1109 tombstoned the routes with 410). Restore: ALTER TABLE RENAME back. See docs/superpowers/plans/2026-07-07-phase-b-p4-versioning.md Task 5.';


--
-- Name: zzz_deprecated_storyboard_projects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.zzz_deprecated_storyboard_projects (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    team_id bigint NOT NULL,
    created_by uuid NOT NULL,
    name character varying(200) NOT NULL,
    description text,
    cover_image_url text,
    viewport_json jsonb,
    settings_json jsonb,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    project_id bigint,
    display_code character varying(20),
    CONSTRAINT storyboard_projects_status_check CHECK (((status)::text = ANY (ARRAY[('active'::character varying)::text, ('archived'::character varying)::text, ('deleted'::character varying)::text])))
);


--
-- Name: TABLE zzz_deprecated_storyboard_projects; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.zzz_deprecated_storyboard_projects IS 'Retired 2026-07-07 (Phase B P4 cutover, PR-V2 #1109 tombstoned the routes with 410). Restore: ALTER TABLE RENAME back. See docs/superpowers/plans/2026-07-07-phase-b-p4-versioning.md Task 5.';


--
-- Name: zzz_deprecated_storyboard_video_assets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.zzz_deprecated_storyboard_video_assets (
    id bigint DEFAULT public.generate_snowflake_id() NOT NULL,
    project_id bigint NOT NULL,
    source_frame_id bigint,
    source_node_id bigint,
    file_path text NOT NULL,
    thumbnail_path text,
    duration_seconds double precision,
    width integer,
    height integer,
    file_size bigint,
    provider character varying(30),
    generation_params jsonb,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT storyboard_video_assets_status_check CHECK (((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('processing'::character varying)::text, ('completed'::character varying)::text, ('failed'::character varying)::text])))
);


--
-- Name: TABLE zzz_deprecated_storyboard_video_assets; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.zzz_deprecated_storyboard_video_assets IS 'Retired 2026-07-07 (Phase B P4 cutover, PR-V2 #1109 tombstoned the routes with 410). Restore: ALTER TABLE RENAME back. See docs/superpowers/plans/2026-07-07-phase-b-p4-versioning.md Task 5.';


--
-- Name: api_key_logs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_key_logs ALTER COLUMN id SET DEFAULT nextval('public.api_key_logs_id_seq'::regclass);


--
-- Name: api_keys id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys ALTER COLUMN id SET DEFAULT nextval('public.api_keys_id_seq'::regclass);


--
-- Name: authors id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.authors ALTER COLUMN id SET DEFAULT nextval('public.authors_id_seq'::regclass);


--
-- Name: boundary_audit id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.boundary_audit ALTER COLUMN id SET DEFAULT nextval('public.boundary_audit_id_seq'::regclass);


--
-- Name: access_overrides access_overrides_object_type_object_id_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.access_overrides
    ADD CONSTRAINT access_overrides_object_type_object_id_user_id_key UNIQUE (object_type, object_id, user_id);


--
-- Name: access_overrides access_overrides_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.access_overrides
    ADD CONSTRAINT access_overrides_pkey PRIMARY KEY (id);


--
-- Name: admin_table_preferences admin_table_preferences_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_table_preferences
    ADD CONSTRAINT admin_table_preferences_pkey PRIMARY KEY (id);


--
-- Name: admin_table_preferences admin_table_preferences_user_id_table_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_table_preferences
    ADD CONSTRAINT admin_table_preferences_user_id_table_key_key UNIQUE (user_id, table_key);


--
-- Name: agent_approval_requests agent_approval_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_approval_requests
    ADD CONSTRAINT agent_approval_requests_pkey PRIMARY KEY (id);


--
-- Name: agent_commitments agent_commitments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_commitments
    ADD CONSTRAINT agent_commitments_pkey PRIMARY KEY (id);


--
-- Name: agent_inbox agent_inbox_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_inbox
    ADD CONSTRAINT agent_inbox_pkey PRIMARY KEY (id);


--
-- Name: agent_memory agent_memory_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_memory
    ADD CONSTRAINT agent_memory_pkey PRIMARY KEY (id);


--
-- Name: agent_memory_promotions agent_memory_promotions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_memory_promotions
    ADD CONSTRAINT agent_memory_promotions_pkey PRIMARY KEY (id);


--
-- Name: agent_outbox agent_outbox_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_outbox
    ADD CONSTRAINT agent_outbox_pkey PRIMARY KEY (id);


--
-- Name: agent_overrides agent_overrides_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_overrides
    ADD CONSTRAINT agent_overrides_pkey PRIMARY KEY (id);


--
-- Name: agent_run_events agent_run_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_run_events
    ADD CONSTRAINT agent_run_events_pkey PRIMARY KEY (id);


--
-- Name: agent_runs agent_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT agent_runs_pkey PRIMARY KEY (id);


--
-- Name: agent_skills agent_skills_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_skills
    ADD CONSTRAINT agent_skills_pkey PRIMARY KEY (agent_id, skill_id);


--
-- Name: agent_state_history agent_state_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_state_history
    ADD CONSTRAINT agent_state_history_pkey PRIMARY KEY (id);


--
-- Name: agent_tasks agent_tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_tasks
    ADD CONSTRAINT agent_tasks_pkey PRIMARY KEY (id);


--
-- Name: agent_workers agent_workers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_workers
    ADD CONSTRAINT agent_workers_pkey PRIMARY KEY (agent_id);


--
-- Name: ai_agent_versions ai_agent_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_agent_versions
    ADD CONSTRAINT ai_agent_versions_pkey PRIMARY KEY (id);


--
-- Name: ai_agents ai_agents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_agents
    ADD CONSTRAINT ai_agents_pkey PRIMARY KEY (id);


--
-- Name: ai_model_prices ai_model_prices_model_provider_effective_at_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_model_prices
    ADD CONSTRAINT ai_model_prices_model_provider_effective_at_key UNIQUE (model, provider, effective_at);


--
-- Name: ai_model_prices ai_model_prices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_model_prices
    ADD CONSTRAINT ai_model_prices_pkey PRIMARY KEY (id);


--
-- Name: ai_session_memory ai_session_memory_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_session_memory
    ADD CONSTRAINT ai_session_memory_pkey PRIMARY KEY (session_id);


--
-- Name: ai_usage_logs ai_usage_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_usage_logs
    ADD CONSTRAINT ai_usage_logs_pkey PRIMARY KEY (id);


--
-- Name: alert_history alert_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_history
    ADD CONSTRAINT alert_history_pkey PRIMARY KEY (id);


--
-- Name: alert_rules alert_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_rules
    ADD CONSTRAINT alert_rules_pkey PRIMARY KEY (id);


--
-- Name: api_key_logs api_key_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_key_logs
    ADD CONSTRAINT api_key_logs_pkey PRIMARY KEY (id);


--
-- Name: api_keys api_keys_key_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_key_id_key UNIQUE (key_id);


--
-- Name: api_keys api_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_pkey PRIMARY KEY (id);


--
-- Name: api_request_logs api_request_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_request_logs
    ADD CONSTRAINT api_request_logs_pkey PRIMARY KEY (id);


--
-- Name: application_logs application_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.application_logs
    ADD CONSTRAINT application_logs_pkey PRIMARY KEY (id);


--
-- Name: audit_logs audit_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_pkey PRIMARY KEY (id);


--
-- Name: authors authors_author_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.authors
    ADD CONSTRAINT authors_author_id_key UNIQUE (author_id);


--
-- Name: authors authors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.authors
    ADD CONSTRAINT authors_pkey PRIMARY KEY (id);


--
-- Name: boundary_audit boundary_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.boundary_audit
    ADD CONSTRAINT boundary_audit_pkey PRIMARY KEY (id);


--
-- Name: canvas_resource_refs canvas_resource_refs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.canvas_resource_refs
    ADD CONSTRAINT canvas_resource_refs_pkey PRIMARY KEY (canvas_id, resource_id, node_id);


--
-- Name: canvases canvases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.canvases
    ADD CONSTRAINT canvases_pkey PRIMARY KEY (id);


--
-- Name: collections collections_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collections
    ADD CONSTRAINT collections_pkey PRIMARY KEY (id);


--
-- Name: conversation_ai_meta conversation_ai_meta_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_ai_meta
    ADD CONSTRAINT conversation_ai_meta_pkey PRIMARY KEY (conversation_id);


--
-- Name: conversation_memory conversation_memory_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_memory
    ADD CONSTRAINT conversation_memory_pkey PRIMARY KEY (conversation_id);


--
-- Name: conversations conversations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_pkey PRIMARY KEY (id);


--
-- Name: cost_audit_log cost_audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cost_audit_log
    ADD CONSTRAINT cost_audit_log_pkey PRIMARY KEY (id);


--
-- Name: credit_pricing credit_pricing_action_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_pricing
    ADD CONSTRAINT credit_pricing_action_key UNIQUE (action);


--
-- Name: credit_pricing credit_pricing_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_pricing
    ADD CONSTRAINT credit_pricing_pkey PRIMARY KEY (id);


--
-- Name: credit_transactions credit_transactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_transactions
    ADD CONSTRAINT credit_transactions_pkey PRIMARY KEY (id);


--
-- Name: daily_point_gifts daily_point_gifts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.daily_point_gifts
    ADD CONSTRAINT daily_point_gifts_pkey PRIMARY KEY (id);


--
-- Name: daily_point_gifts daily_point_gifts_user_id_gift_date_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.daily_point_gifts
    ADD CONSTRAINT daily_point_gifts_user_id_gift_date_key UNIQUE (user_id, gift_date);


--
-- Name: dbos_workflow_routing dbos_workflow_routing_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dbos_workflow_routing
    ADD CONSTRAINT dbos_workflow_routing_pkey PRIMARY KEY (task_type);


--
-- Name: deployment_logs deployment_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deployment_logs
    ADD CONSTRAINT deployment_logs_pkey PRIMARY KEY (id);


--
-- Name: distribution_oauth_states distribution_oauth_states_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.distribution_oauth_states
    ADD CONSTRAINT distribution_oauth_states_pkey PRIMARY KEY (state);


--
-- Name: episodes episodes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.episodes
    ADD CONSTRAINT episodes_pkey PRIMARY KEY (id);


--
-- Name: file_versions file_versions_file_id_version_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_versions
    ADD CONSTRAINT file_versions_file_id_version_number_key UNIQUE (file_id, version_number);


--
-- Name: file_versions file_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_versions
    ADD CONSTRAINT file_versions_pkey PRIMARY KEY (id);


--
-- Name: folders folders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.folders
    ADD CONSTRAINT folders_pkey PRIMARY KEY (id);


--
-- Name: frontend_error_logs frontend_error_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.frontend_error_logs
    ADD CONSTRAINT frontend_error_logs_pkey PRIMARY KEY (id);


--
-- Name: fx_rates fx_rates_from_currency_to_currency_effective_at_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fx_rates
    ADD CONSTRAINT fx_rates_from_currency_to_currency_effective_at_key UNIQUE (from_currency, to_currency, effective_at);


--
-- Name: fx_rates fx_rates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fx_rates
    ADD CONSTRAINT fx_rates_pkey PRIMARY KEY (id);


--
-- Name: generated_media generated_media_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.generated_media
    ADD CONSTRAINT generated_media_pkey PRIMARY KEY (id);


--
-- Name: hotspot_user_state hotspot_user_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hotspot_user_state
    ADD CONSTRAINT hotspot_user_state_pkey PRIMARY KEY (user_id, hotspot_id);


--
-- Name: hotspots hotspots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hotspots
    ADD CONSTRAINT hotspots_pkey PRIMARY KEY (id);


--
-- Name: inspiration_api_tokens inspiration_api_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inspiration_api_tokens
    ADD CONSTRAINT inspiration_api_tokens_pkey PRIMARY KEY (id);


--
-- Name: inspiration_attachments inspiration_attachments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inspiration_attachments
    ADD CONSTRAINT inspiration_attachments_pkey PRIMARY KEY (id);


--
-- Name: inspiration_notes inspiration_notes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inspiration_notes
    ADD CONSTRAINT inspiration_notes_pkey PRIMARY KEY (id);


--
-- Name: issue_messages issue_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issue_messages
    ADD CONSTRAINT issue_messages_pkey PRIMARY KEY (id);


--
-- Name: issue_sequence issue_sequence_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issue_sequence
    ADD CONSTRAINT issue_sequence_pkey PRIMARY KEY (scope);


--
-- Name: issues issues_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issues
    ADD CONSTRAINT issues_pkey PRIMARY KEY (id);


--
-- Name: libraries libraries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.libraries
    ADD CONSTRAINT libraries_pkey PRIMARY KEY (id);


--
-- Name: mediahub_models mediahub_models_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mediahub_models
    ADD CONSTRAINT mediahub_models_name_key UNIQUE (name);


--
-- Name: mediahub_models mediahub_models_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mediahub_models
    ADD CONSTRAINT mediahub_models_pkey PRIMARY KEY (id);


--
-- Name: member_quotas member_quotas_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.member_quotas
    ADD CONSTRAINT member_quotas_pkey PRIMARY KEY (id);


--
-- Name: member_quotas member_quotas_team_id_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.member_quotas
    ADD CONSTRAINT member_quotas_team_id_user_id_key UNIQUE (team_id, user_id);


--
-- Name: message_attachments message_attachments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.message_attachments
    ADD CONSTRAINT message_attachments_pkey PRIMARY KEY (message_id, generated_media_id);


--
-- Name: message_refs message_refs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.message_refs
    ADD CONSTRAINT message_refs_pkey PRIMARY KEY (message_id, ref_type, ref_id);


--
-- Name: messages messages_conversation_id_seq_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_conversation_id_seq_key UNIQUE (conversation_id, seq);


--
-- Name: messages messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);


--
-- Name: notifications notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (id);


--
-- Name: agent_approval_requests one_open_per_run; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_approval_requests
    ADD CONSTRAINT one_open_per_run UNIQUE (run_id, status) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: orders orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_pkey PRIMARY KEY (id);


--
-- Name: point_packages point_packages_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.point_packages
    ADD CONSTRAINT point_packages_name_key UNIQUE (name);


--
-- Name: point_packages point_packages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.point_packages
    ADD CONSTRAINT point_packages_pkey PRIMARY KEY (id);


--
-- Name: point_pricing point_pricing_action_type_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.point_pricing
    ADD CONSTRAINT point_pricing_action_type_key UNIQUE (action_type);


--
-- Name: point_pricing point_pricing_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.point_pricing
    ADD CONSTRAINT point_pricing_pkey PRIMARY KEY (id);


--
-- Name: point_transactions point_transactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.point_transactions
    ADD CONSTRAINT point_transactions_pkey PRIMARY KEY (id);


--
-- Name: project_characters project_characters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_characters
    ADD CONSTRAINT project_characters_pkey PRIMARY KEY (id);


--
-- Name: project_collections project_collections_collection_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_collections
    ADD CONSTRAINT project_collections_collection_code_key UNIQUE (collection_code);


--
-- Name: project_collections project_collections_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_collections
    ADD CONSTRAINT project_collections_pkey PRIMARY KEY (id);


--
-- Name: project_file_comments project_file_comments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_file_comments
    ADD CONSTRAINT project_file_comments_pkey PRIMARY KEY (id);


--
-- Name: project_files project_files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_files
    ADD CONSTRAINT project_files_pkey PRIMARY KEY (id);


--
-- Name: project_folders project_folders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_folders
    ADD CONSTRAINT project_folders_pkey PRIMARY KEY (id);


--
-- Name: project_lib_entities project_lib_entities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_lib_entities
    ADD CONSTRAINT project_lib_entities_pkey PRIMARY KEY (id);


--
-- Name: project_members project_members_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_members
    ADD CONSTRAINT project_members_pkey PRIMARY KEY (project_id, user_id);


--
-- Name: project_stage_history project_stage_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_stage_history
    ADD CONSTRAINT project_stage_history_pkey PRIMARY KEY (id);


--
-- Name: project_stages project_stages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_stages
    ADD CONSTRAINT project_stages_pkey PRIMARY KEY (id);


--
-- Name: project_stages project_stages_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_stages
    ADD CONSTRAINT project_stages_slug_key UNIQUE (slug);


--
-- Name: project_style_profile project_style_profile_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_style_profile
    ADD CONSTRAINT project_style_profile_pkey PRIMARY KEY (project_id);


--
-- Name: project_tasks project_tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_tasks
    ADD CONSTRAINT project_tasks_pkey PRIMARY KEY (id);


--
-- Name: project_workflows project_workflows_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_workflows
    ADD CONSTRAINT project_workflows_pkey PRIMARY KEY (id);


--
-- Name: projects projects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_pkey PRIMARY KEY (id);


--
-- Name: provider_byok_keys provider_byok_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.provider_byok_keys
    ADD CONSTRAINT provider_byok_keys_pkey PRIMARY KEY (id);


--
-- Name: provider_byok_keys provider_byok_keys_user_id_provider_slug_key_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.provider_byok_keys
    ADD CONSTRAINT provider_byok_keys_user_id_provider_slug_key_hash_key UNIQUE (user_id, provider_slug, key_hash);


--
-- Name: provider_contracts provider_contracts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.provider_contracts
    ADD CONSTRAINT provider_contracts_pkey PRIMARY KEY (contract_id);


--
-- Name: provider_credits provider_credits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.provider_credits
    ADD CONSTRAINT provider_credits_pkey PRIMARY KEY (id);


--
-- Name: provider_monthly_spend provider_monthly_spend_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.provider_monthly_spend
    ADD CONSTRAINT provider_monthly_spend_pkey PRIMARY KEY (provider_slug, contract_id, year_month);


--
-- Name: provider_pricing provider_pricing_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.provider_pricing
    ADD CONSTRAINT provider_pricing_pkey PRIMARY KEY (id);


--
-- Name: provider_pricing provider_pricing_provider_slug_model_slug_region_modality_c_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.provider_pricing
    ADD CONSTRAINT provider_pricing_provider_slug_model_slug_region_modality_c_key UNIQUE (provider_slug, model_slug, region, modality, contract_id, effective_from);


--
-- Name: publish_task_accounts publish_task_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.publish_task_accounts
    ADD CONSTRAINT publish_task_accounts_pkey PRIMARY KEY (id);


--
-- Name: publish_tasks publish_tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.publish_tasks
    ADD CONSTRAINT publish_tasks_pkey PRIMARY KEY (id);


--
-- Name: resource_analysis resource_analysis_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_analysis
    ADD CONSTRAINT resource_analysis_pkey PRIMARY KEY (resource_id, analysis_level);


--
-- Name: resource_items resource_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_items
    ADD CONSTRAINT resource_items_pkey PRIMARY KEY (id);


--
-- Name: resource_summaries resource_summaries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_summaries
    ADD CONSTRAINT resource_summaries_pkey PRIMARY KEY (id);


--
-- Name: resource_summaries resource_summaries_resource_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_summaries
    ADD CONSTRAINT resource_summaries_resource_id_key UNIQUE (resource_id);


--
-- Name: resource_tags resource_tags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_tags
    ADD CONSTRAINT resource_tags_pkey PRIMARY KEY (resource_id, tag_id);


--
-- Name: resource_transcripts resource_transcripts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_transcripts
    ADD CONSTRAINT resource_transcripts_pkey PRIMARY KEY (id);


--
-- Name: resource_transcripts resource_transcripts_resource_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_transcripts
    ADD CONSTRAINT resource_transcripts_resource_id_key UNIQUE (resource_id);


--
-- Name: resource_versions resource_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_versions
    ADD CONSTRAINT resource_versions_pkey PRIMARY KEY (id);


--
-- Name: resource_versions resource_versions_resource_id_version_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_versions
    ADD CONSTRAINT resource_versions_resource_id_version_number_key UNIQUE (resource_id, version_number);


--
-- Name: resources resources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resources
    ADD CONSTRAINT resources_pkey PRIMARY KEY (id);


--
-- Name: review_annotations review_annotations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.review_annotations
    ADD CONSTRAINT review_annotations_pkey PRIMARY KEY (id);


--
-- Name: review_comments review_comments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.review_comments
    ADD CONSTRAINT review_comments_pkey PRIMARY KEY (id);


--
-- Name: review_status review_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.review_status
    ADD CONSTRAINT review_status_pkey PRIMARY KEY (id);


--
-- Name: script_assets script_assets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_assets
    ADD CONSTRAINT script_assets_pkey PRIMARY KEY (id);


--
-- Name: script_beats script_beats_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_beats
    ADD CONSTRAINT script_beats_pkey PRIMARY KEY (id);


--
-- Name: script_chapters script_chapters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_chapters
    ADD CONSTRAINT script_chapters_pkey PRIMARY KEY (id);


--
-- Name: script_commits script_commits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_commits
    ADD CONSTRAINT script_commits_pkey PRIMARY KEY (id);


--
-- Name: script_ops script_ops_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_ops
    ADD CONSTRAINT script_ops_pkey PRIMARY KEY (id);


--
-- Name: script_projects script_projects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_projects
    ADD CONSTRAINT script_projects_pkey PRIMARY KEY (id);


--
-- Name: script_scenes script_scenes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_scenes
    ADD CONSTRAINT script_scenes_pkey PRIMARY KEY (id);


--
-- Name: script_shots script_shots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_shots
    ADD CONSTRAINT script_shots_pkey PRIMARY KEY (id);


--
-- Name: script_storyboard_links script_storyboard_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_storyboard_links
    ADD CONSTRAINT script_storyboard_links_pkey PRIMARY KEY (id);


--
-- Name: search_logs search_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.search_logs
    ADD CONSTRAINT search_logs_pkey PRIMARY KEY (id);


--
-- Name: share_views share_views_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.share_views
    ADD CONSTRAINT share_views_pkey PRIMARY KEY (id);


--
-- Name: share_views share_views_share_id_viewer_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.share_views
    ADD CONSTRAINT share_views_share_id_viewer_id_key UNIQUE (share_id, viewer_id);


--
-- Name: shares shares_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shares
    ADD CONSTRAINT shares_pkey PRIMARY KEY (id);


--
-- Name: shares shares_share_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shares
    ADD CONSTRAINT shares_share_code_key UNIQUE (share_code);


--
-- Name: signal_sources signal_sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_sources
    ADD CONSTRAINT signal_sources_pkey PRIMARY KEY (id);


--
-- Name: skill_file_versions skill_file_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skill_file_versions
    ADD CONSTRAINT skill_file_versions_pkey PRIMARY KEY (id);


--
-- Name: skill_files skill_files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skill_files
    ADD CONSTRAINT skill_files_pkey PRIMARY KEY (id);


--
-- Name: skill_versions skill_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skill_versions
    ADD CONSTRAINT skill_versions_pkey PRIMARY KEY (id);


--
-- Name: skills skills_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skills
    ADD CONSTRAINT skills_pkey PRIMARY KEY (id);


--
-- Name: smart_collections smart_collections_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.smart_collections
    ADD CONSTRAINT smart_collections_pkey PRIMARY KEY (id);


--
-- Name: social_accounts social_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_accounts
    ADD CONSTRAINT social_accounts_pkey PRIMARY KEY (id);


--
-- Name: social_accounts social_accounts_scope_type_scope_id_platform_platform_user__key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_accounts
    ADD CONSTRAINT social_accounts_scope_type_scope_id_platform_platform_user__key UNIQUE (scope_type, scope_id, platform, platform_user_id);


--
-- Name: zzz_deprecated_storyboard_assets storyboard_assets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_assets
    ADD CONSTRAINT storyboard_assets_pkey PRIMARY KEY (id);


--
-- Name: zzz_deprecated_storyboard_characters storyboard_characters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_characters
    ADD CONSTRAINT storyboard_characters_pkey PRIMARY KEY (id);


--
-- Name: zzz_deprecated_storyboard_edges storyboard_edges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_edges
    ADD CONSTRAINT storyboard_edges_pkey PRIMARY KEY (id);


--
-- Name: zzz_deprecated_storyboard_frame_characters storyboard_frame_characters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_frame_characters
    ADD CONSTRAINT storyboard_frame_characters_pkey PRIMARY KEY (frame_id, character_id);


--
-- Name: zzz_deprecated_storyboard_frames storyboard_frames_node_id_frame_index_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_frames
    ADD CONSTRAINT storyboard_frames_node_id_frame_index_key UNIQUE (node_id, frame_index);


--
-- Name: zzz_deprecated_storyboard_frames storyboard_frames_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_frames
    ADD CONSTRAINT storyboard_frames_pkey PRIMARY KEY (id);


--
-- Name: zzz_deprecated_storyboard_nodes storyboard_nodes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_nodes
    ADD CONSTRAINT storyboard_nodes_pkey PRIMARY KEY (id);


--
-- Name: zzz_deprecated_storyboard_projects storyboard_projects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_projects
    ADD CONSTRAINT storyboard_projects_pkey PRIMARY KEY (id);


--
-- Name: zzz_deprecated_storyboard_video_assets storyboard_video_assets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_video_assets
    ADD CONSTRAINT storyboard_video_assets_pkey PRIMARY KEY (id);


--
-- Name: style_templates style_templates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.style_templates
    ADD CONSTRAINT style_templates_pkey PRIMARY KEY (id);


--
-- Name: system_settings system_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_settings
    ADD CONSTRAINT system_settings_pkey PRIMARY KEY (key);


--
-- Name: system_status system_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_status
    ADD CONSTRAINT system_status_pkey PRIMARY KEY (id);


--
-- Name: tag_groups tag_groups_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_groups
    ADD CONSTRAINT tag_groups_name_key UNIQUE (name);


--
-- Name: tag_groups tag_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_groups
    ADD CONSTRAINT tag_groups_pkey PRIMARY KEY (id);


--
-- Name: tags tags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tags
    ADD CONSTRAINT tags_pkey PRIMARY KEY (id);


--
-- Name: task_flows task_flows_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_flows
    ADD CONSTRAINT task_flows_pkey PRIMARY KEY (id);


--
-- Name: task_tracking task_tracking_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_tracking
    ADD CONSTRAINT task_tracking_pkey PRIMARY KEY (dbos_workflow_id);


--
-- Name: team_invites team_invites_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_invites
    ADD CONSTRAINT team_invites_code_key UNIQUE (code);


--
-- Name: team_invites team_invites_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_invites
    ADD CONSTRAINT team_invites_pkey PRIMARY KEY (id);


--
-- Name: team_members team_members_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_members
    ADD CONSTRAINT team_members_pkey PRIMARY KEY (team_id, user_id);


--
-- Name: team_plans team_plans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_plans
    ADD CONSTRAINT team_plans_pkey PRIMARY KEY (id);


--
-- Name: team_plans team_plans_team_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_plans
    ADD CONSTRAINT team_plans_team_id_key UNIQUE (team_id);


--
-- Name: team_quotas team_quotas_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_quotas
    ADD CONSTRAINT team_quotas_pkey PRIMARY KEY (team_id);


--
-- Name: teams teams_invite_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teams
    ADD CONSTRAINT teams_invite_code_key UNIQUE (invite_code);


--
-- Name: teams teams_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teams
    ADD CONSTRAINT teams_pkey PRIMARY KEY (id);


--
-- Name: temp_tokens temp_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.temp_tokens
    ADD CONSTRAINT temp_tokens_pkey PRIMARY KEY (id);


--
-- Name: temp_tokens temp_tokens_token_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.temp_tokens
    ADD CONSTRAINT temp_tokens_token_key UNIQUE (token);


--
-- Name: topic_groups topic_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.topic_groups
    ADD CONSTRAINT topic_groups_pkey PRIMARY KEY (id);


--
-- Name: tags unique_tag_per_scope; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tags
    ADD CONSTRAINT unique_tag_per_scope UNIQUE (name, type, user_id);


--
-- Name: user_cookies user_cookies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_cookies
    ADD CONSTRAINT user_cookies_pkey PRIMARY KEY (id);


--
-- Name: user_cookies user_cookies_user_id_platform_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_cookies
    ADD CONSTRAINT user_cookies_user_id_platform_key UNIQUE (user_id, platform);


--
-- Name: user_credits user_credits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_credits
    ADD CONSTRAINT user_credits_pkey PRIMARY KEY (user_id);


--
-- Name: user_hidden_sources user_hidden_sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_hidden_sources
    ADD CONSTRAINT user_hidden_sources_pkey PRIMARY KEY (user_id, source_id);


--
-- Name: user_logs user_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_logs
    ADD CONSTRAINT user_logs_pkey PRIMARY KEY (id);


--
-- Name: user_mcp_servers user_mcp_servers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_mcp_servers
    ADD CONSTRAINT user_mcp_servers_pkey PRIMARY KEY (id);


--
-- Name: user_mcp_servers user_mcp_servers_user_id_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_mcp_servers
    ADD CONSTRAINT user_mcp_servers_user_id_name_key UNIQUE (user_id, name);


--
-- Name: user_notifications user_notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_notifications
    ADD CONSTRAINT user_notifications_pkey PRIMARY KEY (user_id, notification_id);


--
-- Name: user_profiles user_profiles_display_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_profiles
    ADD CONSTRAINT user_profiles_display_id_key UNIQUE (display_id);


--
-- Name: user_profiles user_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_profiles
    ADD CONSTRAINT user_profiles_pkey PRIMARY KEY (id);


--
-- Name: user_profiles user_profiles_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_profiles
    ADD CONSTRAINT user_profiles_username_key UNIQUE (username);


--
-- Name: user_schedules user_schedules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_schedules
    ADD CONSTRAINT user_schedules_pkey PRIMARY KEY (id);


--
-- Name: user_settings user_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_settings
    ADD CONSTRAINT user_settings_pkey PRIMARY KEY (id);


--
-- Name: user_settings user_settings_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_settings
    ADD CONSTRAINT user_settings_user_id_key UNIQUE (user_id);


--
-- Name: user_tag_preferences user_tag_preferences_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_tag_preferences
    ADD CONSTRAINT user_tag_preferences_pkey PRIMARY KEY (user_id);


--
-- Name: user_topic_interests user_topic_interests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_topic_interests
    ADD CONSTRAINT user_topic_interests_pkey PRIMARY KEY (user_id);


--
-- Name: ai_agent_versions ux_ai_agent_versions_agent_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_agent_versions
    ADD CONSTRAINT ux_ai_agent_versions_agent_version UNIQUE (agent_id, version_number);


--
-- Name: skill_file_versions ux_skill_file_versions_file_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skill_file_versions
    ADD CONSTRAINT ux_skill_file_versions_file_version UNIQUE (skill_file_id, version_number);


--
-- Name: skill_versions ux_skill_versions_skill_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skill_versions
    ADD CONSTRAINT ux_skill_versions_skill_version UNIQUE (skill_id, version_number);


--
-- Name: resource_access_logs video_access_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_access_logs
    ADD CONSTRAINT video_access_logs_pkey PRIMARY KEY (id);


--
-- Name: parsed_media videos_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parsed_media
    ADD CONSTRAINT videos_pkey PRIMARY KEY (id);


--
-- Name: parsed_media videos_platform_source_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parsed_media
    ADD CONSTRAINT videos_platform_source_unique UNIQUE (platform_id, source_platform);


--
-- Name: worker_registry worker_registry_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.worker_registry
    ADD CONSTRAINT worker_registry_pkey PRIMARY KEY (executor_id);


--
-- Name: workflow_nodes workflow_nodes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_nodes
    ADD CONSTRAINT workflow_nodes_pkey PRIMARY KEY (id);


--
-- Name: workflow_timeout_policy workflow_timeout_policy_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_timeout_policy
    ADD CONSTRAINT workflow_timeout_policy_pkey PRIMARY KEY (task_type);


--
-- Name: agent_tasks_issue_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX agent_tasks_issue_id_idx ON public.agent_tasks USING btree (issue_id) WHERE (issue_id IS NOT NULL);


--
-- Name: boundary_audit_blocked_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX boundary_audit_blocked_at_idx ON public.boundary_audit USING btree (blocked_at DESC);


--
-- Name: boundary_audit_layer_reason_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX boundary_audit_layer_reason_idx ON public.boundary_audit USING btree (layer, reason);


--
-- Name: boundary_audit_user_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX boundary_audit_user_id_idx ON public.boundary_audit USING btree (user_id) WHERE (user_id IS NOT NULL);


--
-- Name: idx_access_overrides_granted_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_access_overrides_granted_by ON public.access_overrides USING btree (granted_by);


--
-- Name: idx_access_overrides_object; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_access_overrides_object ON public.access_overrides USING btree (object_type, object_id);


--
-- Name: idx_access_overrides_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_access_overrides_user_id ON public.access_overrides USING btree (user_id);


--
-- Name: idx_admin_table_prefs_user_table; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_admin_table_prefs_user_table ON public.admin_table_preferences USING btree (user_id, table_key);


--
-- Name: idx_agent_memory_agent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_memory_agent ON public.agent_memory USING btree (agent_id) WHERE (agent_id IS NOT NULL);


--
-- Name: idx_agent_memory_owner; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_memory_owner ON public.agent_memory USING btree (owner_user_id, status);


--
-- Name: idx_agent_memory_promotions_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_memory_promotions_status ON public.agent_memory_promotions USING btree (status, created_at DESC);


--
-- Name: idx_agent_memory_search; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_memory_search ON public.agent_memory USING gin (search_tsv);


--
-- Name: idx_agent_memory_team_shared; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_memory_team_shared ON public.agent_memory USING btree (team_id, visibility) WHERE (visibility = 'shared'::text);


--
-- Name: idx_agent_overrides_team; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_overrides_team ON public.agent_overrides USING btree (team_id) WHERE (team_id IS NOT NULL);


--
-- Name: idx_agent_overrides_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_overrides_user ON public.agent_overrides USING btree (user_id) WHERE (user_id IS NOT NULL);


--
-- Name: idx_agent_run_events_byok; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_run_events_byok ON public.agent_run_events USING btree (byok_key_id) WHERE (byok_key_id IS NOT NULL);


--
-- Name: idx_agent_run_events_cost_gin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_run_events_cost_gin ON public.agent_run_events USING gin (cost_snapshot jsonb_path_ops) WHERE (cost_snapshot IS NOT NULL);


--
-- Name: idx_agent_run_events_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_run_events_created ON public.agent_run_events USING btree (created_at DESC);


--
-- Name: idx_agent_run_events_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_run_events_parent ON public.agent_run_events USING btree (parent_run_id) WHERE (parent_run_id IS NOT NULL);


--
-- Name: idx_agent_run_events_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_run_events_run ON public.agent_run_events USING btree (run_id, iteration);


--
-- Name: idx_agent_run_events_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_run_events_run_id ON public.agent_run_events USING btree (run_id);


--
-- Name: idx_agent_runs_agent_started; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_agent_started ON public.agent_runs USING btree (agent_id, started_at DESC);


--
-- Name: idx_agent_runs_billing; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_billing ON public.agent_runs USING btree (team_id, created_at DESC) WHERE (team_id IS NOT NULL);


--
-- Name: idx_agent_runs_conversation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_conversation ON public.agent_runs USING btree (conversation_id) WHERE (conversation_id IS NOT NULL);


--
-- Name: idx_agent_runs_heartbeat; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_heartbeat ON public.agent_runs USING btree (heartbeat_at) WHERE (status = 'running'::text);


--
-- Name: idx_agent_runs_issue; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_issue ON public.agent_runs USING btree (issue_id) WHERE (issue_id IS NOT NULL);


--
-- Name: idx_agent_runs_liveness_scan; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_liveness_scan ON public.agent_runs USING btree (liveness_state, heartbeat_at) WHERE (status = 'running'::text);


--
-- Name: idx_agent_runs_outcome; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_outcome ON public.agent_runs USING btree (outcome) WHERE (outcome IS NOT NULL);


--
-- Name: idx_agent_runs_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_parent ON public.agent_runs USING btree (parent_run_id) WHERE (parent_run_id IS NOT NULL);


--
-- Name: idx_agent_runs_root_tree; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_root_tree ON public.agent_runs USING btree (root_run_id, started_at) WHERE (root_run_id IS NOT NULL);


--
-- Name: idx_agent_runs_running_agent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_running_agent ON public.agent_runs USING btree (agent_id) WHERE (status = 'running'::text);


--
-- Name: idx_agent_runs_running_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_running_user ON public.agent_runs USING btree (user_id) WHERE (status = 'running'::text);


--
-- Name: idx_agent_runs_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_session ON public.agent_runs USING btree (session_id) WHERE (session_id IS NOT NULL);


--
-- Name: idx_agent_runs_task_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_task_id ON public.agent_runs USING btree (task_id) WHERE (task_id IS NOT NULL);


--
-- Name: idx_agent_runs_useful_action_scan; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_useful_action_scan ON public.agent_runs USING btree (last_useful_action_at) WHERE (status = 'running'::text);


--
-- Name: idx_agent_skills_agent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_skills_agent ON public.agent_skills USING btree (agent_id, sort_order);


--
-- Name: idx_agent_skills_skill; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_skills_skill ON public.agent_skills USING btree (skill_id);


--
-- Name: idx_agent_workers_heartbeat; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_workers_heartbeat ON public.agent_workers USING btree (heartbeat_at) WHERE (state = ANY (ARRAY['idle'::text, 'working'::text, 'waiting_for_other'::text]));


--
-- Name: idx_ai_agents_chat_enabled; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ai_agents_chat_enabled ON public.ai_agents USING btree ((((capability_profile -> 'chat'::text) ->> 'enabled'::text))) WHERE (((capability_profile -> 'chat'::text) ->> 'enabled'::text) = 'true'::text);


--
-- Name: idx_ai_agents_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ai_agents_user ON public.ai_agents USING btree (user_id) WHERE (user_id IS NOT NULL);


--
-- Name: idx_ai_model_prices_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ai_model_prices_lookup ON public.ai_model_prices USING btree (model, provider, effective_at DESC);


--
-- Name: idx_ai_usage_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ai_usage_project ON public.ai_usage_logs USING btree (project_id, created_at);


--
-- Name: idx_ai_usage_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ai_usage_user ON public.ai_usage_logs USING btree (user_id, created_at);


--
-- Name: idx_alert_history_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alert_history_created_at ON public.alert_history USING btree (created_at DESC);


--
-- Name: idx_alert_history_resolved; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alert_history_resolved ON public.alert_history USING btree (resolved) WHERE (resolved = false);


--
-- Name: idx_alert_history_rule_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alert_history_rule_id ON public.alert_history USING btree (rule_id);


--
-- Name: idx_alert_rules_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alert_rules_active ON public.alert_rules USING btree (is_active) WHERE (is_active = true);


--
-- Name: idx_api_key_logs_api_key_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_api_key_logs_api_key_id ON public.api_key_logs USING btree (api_key_id);


--
-- Name: idx_api_keys_key_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_api_keys_key_hash ON public.api_keys USING btree (key_hash);


--
-- Name: idx_api_keys_key_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_api_keys_key_id ON public.api_keys USING btree (key_id);


--
-- Name: idx_api_keys_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_api_keys_user_id ON public.api_keys USING btree (user_id);


--
-- Name: idx_api_request_logs_method; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_api_request_logs_method ON public.api_request_logs USING btree (method);


--
-- Name: idx_api_request_logs_timestamp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_api_request_logs_timestamp ON public.api_request_logs USING btree ("timestamp" DESC);


--
-- Name: idx_api_request_logs_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_api_request_logs_user_id ON public.api_request_logs USING btree (user_id);


--
-- Name: idx_application_logs_level; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_application_logs_level ON public.application_logs USING btree (level);


--
-- Name: idx_application_logs_logged_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_application_logs_logged_at ON public.application_logs USING btree (logged_at DESC);


--
-- Name: idx_application_logs_module; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_application_logs_module ON public.application_logs USING btree (module);


--
-- Name: idx_application_logs_module_logged_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_application_logs_module_logged_at ON public.application_logs USING btree (module, logged_at DESC);


--
-- Name: idx_approval_requests_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_approval_requests_expires ON public.agent_approval_requests USING btree (expires_at) WHERE (status = 'pending'::text);


--
-- Name: idx_approval_requests_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_approval_requests_run ON public.agent_approval_requests USING btree (run_id) WHERE (run_id IS NOT NULL);


--
-- Name: idx_approval_requests_user_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_approval_requests_user_pending ON public.agent_approval_requests USING btree (user_id, created_at DESC) WHERE (status = 'pending'::text);


--
-- Name: idx_audit_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_entity ON public.cost_audit_log USING btree (entity_type, entity_id, changed_at DESC);


--
-- Name: idx_audit_logs_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_action ON public.audit_logs USING btree (action);


--
-- Name: idx_audit_logs_admin_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_admin_id ON public.audit_logs USING btree (admin_id);


--
-- Name: idx_audit_logs_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_created_at ON public.audit_logs USING btree (created_at);


--
-- Name: idx_audit_logs_target_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_target_type ON public.audit_logs USING btree (target_type);


--
-- Name: idx_byok_active_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_byok_active_lookup ON public.provider_byok_keys USING btree (user_id, provider_slug) WHERE (active = true);


--
-- Name: idx_canvases_deleted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_canvases_deleted ON public.canvases USING btree (deleted_at DESC) WHERE (deleted_at IS NOT NULL);


--
-- Name: idx_canvases_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_canvases_kind ON public.canvases USING btree (kind);


--
-- Name: idx_canvases_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_canvases_project ON public.canvases USING btree (project_id);


--
-- Name: idx_canvases_project_updated; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_canvases_project_updated ON public.canvases USING btree (project_id, updated_at DESC);


--
-- Name: idx_collections_owner_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_collections_owner_id ON public.collections USING btree (owner_id);


--
-- Name: idx_collections_team_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_collections_team_id ON public.collections USING btree (team_id);


--
-- Name: idx_commitments_due_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_commitments_due_time ON public.agent_commitments USING btree (trigger_at) WHERE ((status = 'pending'::text) AND (trigger_type = 'time'::text));


--
-- Name: idx_commitments_next_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_commitments_next_session ON public.agent_commitments USING btree (user_id, agent_id) WHERE ((status = 'pending'::text) AND (trigger_type = 'next_session'::text));


--
-- Name: idx_commitments_pending_event; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_commitments_pending_event ON public.agent_commitments USING btree (trigger_event) WHERE ((status = 'pending'::text) AND (trigger_type = 'event'::text));


--
-- Name: idx_commitments_user_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_commitments_user_status ON public.agent_commitments USING btree (user_id, status, created_at DESC) WHERE (user_id IS NOT NULL);


--
-- Name: idx_contracts_provider_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_contracts_provider_status ON public.provider_contracts USING btree (provider_slug, status);


--
-- Name: idx_contracts_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_contracts_tenant ON public.provider_contracts USING btree (tenant_id) WHERE (tenant_id IS NOT NULL);


--
-- Name: idx_conv_ai_meta_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conv_ai_meta_slug ON public.conversation_ai_meta USING btree (agent_slug);


--
-- Name: idx_conversation_members_agent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conversation_members_agent ON public.conversation_members USING btree (agent_id) WHERE (member_type = 'agent'::text);


--
-- Name: idx_conversation_members_user_open; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conversation_members_user_open ON public.conversation_members USING btree (user_id, open) WHERE (member_type = 'user'::text);


--
-- Name: idx_conversations_scope; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conversations_scope ON public.conversations USING btree (scope_id) WHERE (archived_at IS NULL);


--
-- Name: idx_credit_transactions_admin_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_credit_transactions_admin_id ON public.credit_transactions USING btree (admin_id);


--
-- Name: idx_credit_transactions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_credit_transactions_user_id ON public.credit_transactions USING btree (user_id);


--
-- Name: idx_credits_consumable; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_credits_consumable ON public.provider_credits USING btree (provider_slug, contract_id) WHERE ((remaining_usd > (0)::numeric) AND (consumed_at IS NULL));


--
-- Name: idx_crr_canvas; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_crr_canvas ON public.canvas_resource_refs USING btree (canvas_id);


--
-- Name: idx_crr_resource; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_crr_resource ON public.canvas_resource_refs USING btree (resource_id);


--
-- Name: idx_daily_point_gifts_date_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_daily_point_gifts_date_status ON public.daily_point_gifts USING btree (gift_date, status);


--
-- Name: idx_deployment_logs_service_deployed_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_deployment_logs_service_deployed_at ON public.deployment_logs USING btree (service, deployed_at DESC);


--
-- Name: idx_episodes_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_episodes_project ON public.episodes USING btree (project_id, sort_order);


--
-- Name: idx_file_versions_uploaded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_file_versions_uploaded_by ON public.file_versions USING btree (uploaded_by);


--
-- Name: idx_folders_created_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_folders_created_by ON public.folders USING btree (created_by);


--
-- Name: idx_folders_library; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_folders_library ON public.folders USING btree (library_id) WHERE (library_id IS NOT NULL);


--
-- Name: idx_folders_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_folders_parent ON public.folders USING btree (parent_id);


--
-- Name: idx_folders_scope_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_folders_scope_id ON public.folders USING btree (scope_id);


--
-- Name: idx_folders_trashed_scope_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_folders_trashed_scope_id ON public.folders USING btree (scope_id, is_trashed) WHERE (is_trashed = true);


--
-- Name: idx_frontend_error_logs_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_frontend_error_logs_created_at ON public.frontend_error_logs USING btree (created_at DESC);


--
-- Name: idx_frontend_error_logs_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_frontend_error_logs_user_id ON public.frontend_error_logs USING btree (user_id);


--
-- Name: idx_fx_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_fx_lookup ON public.fx_rates USING btree (from_currency, to_currency, effective_at DESC);


--
-- Name: idx_generated_media_scope_sha256; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_generated_media_scope_sha256 ON public.generated_media USING btree (scope_id, content_sha256) WHERE (content_sha256 IS NOT NULL);


--
-- Name: idx_genmedia_agent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_genmedia_agent ON public.generated_media USING btree (agent_id);


--
-- Name: idx_genmedia_canvas; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_genmedia_canvas ON public.generated_media USING btree (canvas_id);


--
-- Name: idx_genmedia_conversation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_genmedia_conversation ON public.generated_media USING btree (conversation_id);


--
-- Name: idx_genmedia_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_genmedia_entity_id ON public.generated_media USING btree (((params ->> 'entity_id'::text)), created_at DESC) WHERE ((params ->> 'entity_id'::text) IS NOT NULL);


--
-- Name: idx_genmedia_node_shot; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_genmedia_node_shot ON public.generated_media USING btree (node_id) WHERE (origin_kind = ANY (ARRAY['shot_generate'::text, 'shot_video'::text]));


--
-- Name: idx_genmedia_origin_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_genmedia_origin_run ON public.generated_media USING btree (origin_run_id);


--
-- Name: idx_genmedia_promoted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_genmedia_promoted ON public.generated_media USING btree (promoted_resource_id);


--
-- Name: idx_genmedia_scope_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_genmedia_scope_created ON public.generated_media USING btree (scope_id, created_at DESC);


--
-- Name: idx_hotspot_user_state_hidden; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hotspot_user_state_hidden ON public.hotspot_user_state USING btree (user_id) WHERE is_hidden;


--
-- Name: idx_hotspot_user_state_saved; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hotspot_user_state_saved ON public.hotspot_user_state USING btree (user_id) WHERE is_saved;


--
-- Name: idx_hotspots_captured; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hotspots_captured ON public.hotspots USING btree (captured_at DESC);


--
-- Name: idx_hotspots_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hotspots_category ON public.hotspots USING btree (category);


--
-- Name: idx_hotspots_heat; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hotspots_heat ON public.hotspots USING btree (heat DESC NULLS LAST);


--
-- Name: idx_inbox_dedup; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_inbox_dedup ON public.agent_inbox USING btree (recipient_agent_id, dedup_key) WHERE (dedup_key IS NOT NULL);


--
-- Name: idx_inbox_recipient_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_inbox_recipient_active ON public.agent_inbox USING btree (recipient_agent_id, created_at DESC);


--
-- Name: idx_inbox_recipient_unread; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_inbox_recipient_unread ON public.agent_inbox USING btree (recipient_agent_id, priority DESC, created_at) WHERE (status = 'unread'::text);


--
-- Name: idx_inspiration_api_tokens_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_inspiration_api_tokens_hash ON public.inspiration_api_tokens USING btree (token_hash);


--
-- Name: idx_inspiration_attachments_note; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_inspiration_attachments_note ON public.inspiration_attachments USING btree (note_id);


--
-- Name: idx_inspiration_attachments_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_inspiration_attachments_user ON public.inspiration_attachments USING btree (user_id);


--
-- Name: idx_inspiration_notes_tags; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_inspiration_notes_tags ON public.inspiration_notes USING gin (tags);


--
-- Name: idx_inspiration_notes_user_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_inspiration_notes_user_date ON public.inspiration_notes USING btree (user_id, note_date DESC);


--
-- Name: idx_inspiration_notes_user_pinned; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_inspiration_notes_user_pinned ON public.inspiration_notes USING btree (user_id, pinned) WHERE (deleted_at IS NULL);


--
-- Name: idx_issue_messages_issue_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_issue_messages_issue_created ON public.issue_messages USING btree (issue_id, created_at);


--
-- Name: idx_issue_messages_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_issue_messages_run ON public.issue_messages USING btree (agent_run_id) WHERE (agent_run_id IS NOT NULL);


--
-- Name: idx_issues_ai_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_issues_ai_session ON public.issues USING btree (ai_session_id) WHERE (ai_session_id IS NOT NULL);


--
-- Name: idx_libraries_created_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_libraries_created_by ON public.libraries USING btree (created_by);


--
-- Name: idx_libraries_scope; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_libraries_scope ON public.libraries USING btree (scope_type, scope_id);


--
-- Name: idx_mediahub_models_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mediahub_models_type ON public.mediahub_models USING btree (type, is_enabled);


--
-- Name: idx_member_quotas_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_member_quotas_user_id ON public.member_quotas USING btree (user_id);


--
-- Name: idx_messages_keyset; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_messages_keyset ON public.messages USING btree (conversation_id, seq DESC);


--
-- Name: idx_messages_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_messages_parent ON public.messages USING btree (conversation_id, parent_id) WHERE (parent_id IS NOT NULL);


--
-- Name: idx_notifications_created_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_notifications_created_by ON public.notifications USING btree (created_by);


--
-- Name: idx_notifications_team; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_notifications_team ON public.notifications USING btree (team_id);


--
-- Name: idx_orders_package_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_orders_package_id ON public.orders USING btree (package_id);


--
-- Name: idx_orders_team_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_orders_team_id ON public.orders USING btree (team_id);


--
-- Name: idx_orders_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_orders_user_id ON public.orders USING btree (user_id);


--
-- Name: idx_outbox_recipient_agent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_outbox_recipient_agent ON public.agent_outbox USING btree (recipient_agent_id, created_at DESC) WHERE (recipient_agent_id IS NOT NULL);


--
-- Name: idx_outbox_recipient_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_outbox_recipient_user ON public.agent_outbox USING btree (recipient_user_id, created_at DESC) WHERE (recipient_user_id IS NOT NULL);


--
-- Name: idx_outbox_sender; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_outbox_sender ON public.agent_outbox USING btree (sender_agent_id, created_at DESC);


--
-- Name: idx_parsed_media_author; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_parsed_media_author ON public.parsed_media USING btree (author);


--
-- Name: idx_parsed_media_cover_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_parsed_media_cover_status ON public.parsed_media USING btree (cover_download_status);


--
-- Name: idx_parsed_media_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_parsed_media_created_at ON public.parsed_media USING btree (created_at DESC);


--
-- Name: idx_parsed_media_download_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_parsed_media_download_status ON public.parsed_media USING btree (video_download_status);


--
-- Name: idx_parsed_media_image_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_parsed_media_image_status ON public.parsed_media USING btree (image_download_status) WHERE (image_download_status <> 'skipped'::public.download_status);


--
-- Name: idx_parsed_media_media_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_parsed_media_media_type ON public.parsed_media USING btree (media_type);


--
-- Name: idx_parsed_media_platform_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_parsed_media_platform_id ON public.parsed_media USING btree (platform_id);


--
-- Name: idx_parsed_media_source_platform; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_parsed_media_source_platform ON public.parsed_media USING btree (source_platform);


--
-- Name: idx_parsed_media_storage_size; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_parsed_media_storage_size ON public.parsed_media USING btree (storage_size);


--
-- Name: idx_parsed_media_view_count; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_parsed_media_view_count ON public.parsed_media USING btree (view_count);


--
-- Name: idx_point_transactions_unique_order; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_point_transactions_unique_order ON public.point_transactions USING btree (reference_type, reference_id) WHERE (((reference_type)::text = 'order'::text) AND (reference_id IS NOT NULL));


--
-- Name: idx_point_transactions_unique_order_refund; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_point_transactions_unique_order_refund ON public.point_transactions USING btree (reference_type, reference_id) WHERE (((reference_type)::text = 'order_refund'::text) AND (reference_id IS NOT NULL));


--
-- Name: idx_point_transactions_unique_refund; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_point_transactions_unique_refund ON public.point_transactions USING btree (team_id, reference_type, reference_id) WHERE (((type)::text = 'refund'::text) AND (reference_id IS NOT NULL));


--
-- Name: idx_point_transactions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_point_transactions_user_id ON public.point_transactions USING btree (user_id);


--
-- Name: idx_project_characters_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_project_characters_project ON public.project_characters USING btree (project_id, sort_order);


--
-- Name: idx_project_collections_created_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_project_collections_created_by ON public.project_collections USING btree (created_by);


--
-- Name: idx_project_collections_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_project_collections_project ON public.project_collections USING btree (project_id);


--
-- Name: idx_project_file_comments_file; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_project_file_comments_file ON public.project_file_comments USING btree (file_id);


--
-- Name: idx_project_file_comments_version; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_project_file_comments_version ON public.project_file_comments USING btree (version_id);


--
-- Name: idx_project_files_folder; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_project_files_folder ON public.project_files USING btree (project_id, folder_id);


--
-- Name: idx_project_files_folder_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_project_files_folder_id ON public.project_files USING btree (folder_id);


--
-- Name: idx_project_files_media; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_project_files_media ON public.project_files USING btree (media_id) WHERE (media_id IS NOT NULL);


--
-- Name: idx_project_files_uploaded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_project_files_uploaded_by ON public.project_files USING btree (uploaded_by);


--
-- Name: idx_project_folders_created_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_project_folders_created_by ON public.project_folders USING btree (created_by);


--
-- Name: idx_project_folders_parent_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_project_folders_parent_id ON public.project_folders USING btree (parent_id);


--
-- Name: idx_project_folders_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_project_folders_project ON public.project_folders USING btree (project_id);


--
-- Name: idx_project_lib_entities_project_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_project_lib_entities_project_type ON public.project_lib_entities USING btree (project_id, entity_type, sort_order);


--
-- Name: idx_project_members_invited_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_project_members_invited_by ON public.project_members USING btree (invited_by);


--
-- Name: idx_project_members_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_project_members_user_id ON public.project_members USING btree (user_id);


--
-- Name: idx_project_stage_history_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_project_stage_history_project ON public.project_stage_history USING btree (project_id, entered_at DESC);


--
-- Name: idx_project_tasks_assignee_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_project_tasks_assignee_id ON public.project_tasks USING btree (assignee_id);


--
-- Name: idx_project_tasks_created_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_project_tasks_created_by ON public.project_tasks USING btree (created_by);


--
-- Name: idx_project_tasks_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_project_tasks_status ON public.project_tasks USING btree (project_id, status);


--
-- Name: idx_project_tasks_workflow_node_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_project_tasks_workflow_node_id ON public.project_tasks USING btree (workflow_node_id);


--
-- Name: idx_project_workflows_team; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_project_workflows_team ON public.project_workflows USING btree (team_id);


--
-- Name: idx_projects_archived_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_projects_archived_at ON public.projects USING btree (archived_at) WHERE (archived_at IS NOT NULL);


--
-- Name: idx_projects_current_stage; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_projects_current_stage ON public.projects USING btree (current_stage_id) WHERE (current_stage_id IS NOT NULL);


--
-- Name: idx_projects_owner; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_projects_owner ON public.projects USING btree (owner_id);


--
-- Name: idx_projects_team; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_projects_team ON public.projects USING btree (team_id);


--
-- Name: idx_projects_workflow_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_projects_workflow_id ON public.projects USING btree (workflow_id);


--
-- Name: idx_provider_pricing_contract; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_provider_pricing_contract ON public.provider_pricing USING btree (contract_id, effective_from DESC) WHERE (contract_id IS NOT NULL);


--
-- Name: idx_provider_pricing_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_provider_pricing_lookup ON public.provider_pricing USING btree (provider_slug, model_slug, modality, effective_from DESC) WHERE (effective_to IS NULL);


--
-- Name: idx_publish_task_accounts_task; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_publish_task_accounts_task ON public.publish_task_accounts USING btree (task_id);


--
-- Name: idx_publish_tasks_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_publish_tasks_user ON public.publish_tasks USING btree (user_id, created_at DESC);


--
-- Name: idx_resource_access_logs_resource_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resource_access_logs_resource_id ON public.resource_access_logs USING btree (resource_id);


--
-- Name: idx_resource_access_logs_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resource_access_logs_user_id ON public.resource_access_logs USING btree (user_id);


--
-- Name: idx_resource_analysis_level; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resource_analysis_level ON public.resource_analysis USING btree (analysis_level);


--
-- Name: idx_resource_items_added_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resource_items_added_by ON public.resource_items USING btree (added_by);


--
-- Name: idx_resource_items_folder; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resource_items_folder ON public.resource_items USING btree (folder_id) WHERE (folder_id IS NOT NULL);


--
-- Name: idx_resource_items_library; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resource_items_library ON public.resource_items USING btree (library_id) WHERE (library_id IS NOT NULL);


--
-- Name: idx_resource_items_resource; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resource_items_resource ON public.resource_items USING btree (resource_id);


--
-- Name: idx_resource_items_scope_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resource_items_scope_id ON public.resource_items USING btree (scope_id);


--
-- Name: idx_resource_tags_resource; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resource_tags_resource ON public.resource_tags USING btree (resource_id);


--
-- Name: idx_resource_tags_tag_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resource_tags_tag_id ON public.resource_tags USING btree (tag_id);


--
-- Name: idx_resource_tags_tagged_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resource_tags_tagged_by ON public.resource_tags USING btree (tagged_by);


--
-- Name: idx_resource_versions_resource; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resource_versions_resource ON public.resource_versions USING btree (resource_id);


--
-- Name: idx_resource_versions_uploaded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resource_versions_uploaded_by ON public.resource_versions USING btree (uploaded_by);


--
-- Name: idx_resources_aspect_bucket; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resources_aspect_bucket ON public.resources USING btree (aspect_bucket);


--
-- Name: idx_resources_creator; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resources_creator ON public.resources USING btree (creator_id);


--
-- Name: idx_resources_file_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resources_file_hash ON public.resources USING btree (file_hash) WHERE ((file_hash IS NOT NULL) AND (is_trashed = false));


--
-- Name: idx_resources_filename_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resources_filename_trgm ON public.resources USING gin (filename public.gin_trgm_ops);


--
-- Name: idx_resources_media_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resources_media_id ON public.resources USING btree (media_id) WHERE (media_id IS NOT NULL);


--
-- Name: idx_resources_notes_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resources_notes_trgm ON public.resources USING gin (notes public.gin_trgm_ops);


--
-- Name: idx_resources_summary_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resources_summary_status ON public.resources USING btree (summary_status) WHERE (summary_status <> 'none'::public.ai_task_status);


--
-- Name: idx_resources_transcript_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resources_transcript_status ON public.resources USING btree (transcript_status) WHERE (transcript_status <> 'none'::public.ai_task_status);


--
-- Name: idx_resources_trashed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resources_trashed ON public.resources USING btree (is_trashed) WHERE (is_trashed = true);


--
-- Name: idx_review_annotations_comment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_review_annotations_comment_id ON public.review_annotations USING btree (comment_id);


--
-- Name: idx_review_comments_author_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_review_comments_author_id ON public.review_comments USING btree (author_id);


--
-- Name: idx_review_comments_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_review_comments_parent ON public.review_comments USING btree (parent_id);


--
-- Name: idx_review_comments_resource; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_review_comments_resource ON public.review_comments USING btree (resource_id);


--
-- Name: idx_review_comments_version_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_review_comments_version_id ON public.review_comments USING btree (version_id);


--
-- Name: idx_review_status_resource; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_review_status_resource ON public.review_status USING btree (resource_id);


--
-- Name: idx_review_status_reviewer_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_review_status_reviewer_id ON public.review_status USING btree (reviewer_id);


--
-- Name: idx_review_status_version_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_review_status_version_id ON public.review_status USING btree (version_id);


--
-- Name: idx_sb_characters_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sb_characters_project ON public.zzz_deprecated_storyboard_characters USING btree (project_id);


--
-- Name: idx_sb_edges_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sb_edges_project ON public.zzz_deprecated_storyboard_edges USING btree (project_id);


--
-- Name: idx_sb_frames_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sb_frames_project ON public.zzz_deprecated_storyboard_frames USING btree (project_id);


--
-- Name: idx_sb_nodes_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sb_nodes_project ON public.zzz_deprecated_storyboard_nodes USING btree (project_id);


--
-- Name: idx_sb_projects_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sb_projects_parent ON public.zzz_deprecated_storyboard_projects USING btree (project_id);


--
-- Name: idx_script_assets_script_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_script_assets_script_id ON public.script_assets USING btree (script_id);


--
-- Name: idx_script_beats_script; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_script_beats_script ON public.script_beats USING btree (script_id, sort_order);


--
-- Name: idx_script_chapters_parent_chapter_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_script_chapters_parent_chapter_id ON public.script_chapters USING btree (parent_chapter_id);


--
-- Name: idx_script_chapters_script_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_script_chapters_script_id ON public.script_chapters USING btree (script_id);


--
-- Name: idx_script_commits_script; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_script_commits_script ON public.script_commits USING btree (script_id, created_at DESC);


--
-- Name: idx_script_ops_scene; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_script_ops_scene ON public.script_ops USING btree (scene_id, op_seq);


--
-- Name: idx_script_projects_created_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_script_projects_created_by ON public.script_projects USING btree (created_by);


--
-- Name: idx_script_projects_episode; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_script_projects_episode ON public.script_projects USING btree (episode_id);


--
-- Name: idx_script_projects_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_script_projects_project_id ON public.script_projects USING btree (project_id);


--
-- Name: idx_script_projects_team_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_script_projects_team_id ON public.script_projects USING btree (team_id);


--
-- Name: idx_script_scenes_script; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_script_scenes_script ON public.script_scenes USING btree (script_id, chapter_id, sort_order);


--
-- Name: idx_script_shots_scene; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_script_shots_scene ON public.script_shots USING btree (scene_id, sort_order);


--
-- Name: idx_script_storyboard_links_chapter_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_script_storyboard_links_chapter_id ON public.script_storyboard_links USING btree (chapter_id);


--
-- Name: idx_script_storyboard_links_storyboard_node_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_script_storyboard_links_storyboard_node_id ON public.script_storyboard_links USING btree (storyboard_node_id);


--
-- Name: idx_script_storyboard_links_storyboard_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_script_storyboard_links_storyboard_project_id ON public.script_storyboard_links USING btree (storyboard_project_id);


--
-- Name: idx_search_logs_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_search_logs_user_id ON public.search_logs USING btree (user_id);


--
-- Name: idx_share_views_share; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_share_views_share ON public.share_views USING btree (share_id);


--
-- Name: idx_share_views_viewer_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_share_views_viewer_id ON public.share_views USING btree (viewer_id);


--
-- Name: idx_shares_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_shares_code ON public.shares USING btree (share_code);


--
-- Name: idx_shares_folder; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_shares_folder ON public.shares USING btree (folder_id) WHERE (folder_id IS NOT NULL);


--
-- Name: idx_shares_library_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_shares_library_id ON public.shares USING btree (library_id);


--
-- Name: idx_shares_project_file_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_shares_project_file_id ON public.shares USING btree (project_file_id);


--
-- Name: idx_shares_resource; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_shares_resource ON public.shares USING btree (resource_id) WHERE (resource_id IS NOT NULL);


--
-- Name: idx_shares_shared_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_shares_shared_by ON public.shares USING btree (shared_by);


--
-- Name: idx_shares_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_shares_status ON public.shares USING btree (status) WHERE ((status)::text = 'active'::text);


--
-- Name: idx_shares_team_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_shares_team_id ON public.shares USING btree (team_id);


--
-- Name: idx_shares_version_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_shares_version_id ON public.shares USING btree (version_id);


--
-- Name: idx_signal_sources_enabled; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_signal_sources_enabled ON public.signal_sources USING btree (enabled) WHERE (enabled = true);


--
-- Name: idx_signal_sources_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_signal_sources_user ON public.signal_sources USING btree (user_id);


--
-- Name: idx_skill_files_skill; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_skill_files_skill ON public.skill_files USING btree (skill_id, sort_order);


--
-- Name: idx_skills_created_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_skills_created_by ON public.skills USING btree (created_by);


--
-- Name: idx_skills_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_skills_project_id ON public.skills USING btree (project_id);


--
-- Name: idx_skills_team_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_skills_team_id ON public.skills USING btree (team_id) WHERE ((status)::text = 'active'::text);


--
-- Name: idx_smart_collections_scope_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_smart_collections_scope_id ON public.smart_collections USING btree (scope_id);


--
-- Name: idx_smart_collections_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_smart_collections_user ON public.smart_collections USING btree (user_id);


--
-- Name: idx_social_accounts_scope; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_social_accounts_scope ON public.social_accounts USING btree (scope_type, scope_id);


--
-- Name: idx_state_history_agent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_state_history_agent ON public.agent_state_history USING btree (agent_id, changed_at DESC);


--
-- Name: idx_storyboard_assets_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_storyboard_assets_project_id ON public.zzz_deprecated_storyboard_assets USING btree (project_id);


--
-- Name: idx_storyboard_edges_source_node_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_storyboard_edges_source_node_id ON public.zzz_deprecated_storyboard_edges USING btree (source_node_id);


--
-- Name: idx_storyboard_edges_target_node_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_storyboard_edges_target_node_id ON public.zzz_deprecated_storyboard_edges USING btree (target_node_id);


--
-- Name: idx_storyboard_frame_characters_character_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_storyboard_frame_characters_character_id ON public.zzz_deprecated_storyboard_frame_characters USING btree (character_id);


--
-- Name: idx_storyboard_projects_created_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_storyboard_projects_created_by ON public.zzz_deprecated_storyboard_projects USING btree (created_by);


--
-- Name: idx_storyboard_projects_team_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_storyboard_projects_team_id ON public.zzz_deprecated_storyboard_projects USING btree (team_id);


--
-- Name: idx_storyboard_video_assets_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_storyboard_video_assets_project_id ON public.zzz_deprecated_storyboard_video_assets USING btree (project_id);


--
-- Name: idx_storyboard_video_assets_source_frame_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_storyboard_video_assets_source_frame_id ON public.zzz_deprecated_storyboard_video_assets USING btree (source_frame_id);


--
-- Name: idx_storyboard_video_assets_source_node_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_storyboard_video_assets_source_node_id ON public.zzz_deprecated_storyboard_video_assets USING btree (source_node_id);


--
-- Name: idx_style_templates_created_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_style_templates_created_by ON public.style_templates USING btree (created_by);


--
-- Name: idx_style_templates_team_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_style_templates_team_id ON public.style_templates USING btree (team_id);


--
-- Name: idx_system_settings_updated_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_system_settings_updated_by ON public.system_settings USING btree (updated_by);


--
-- Name: idx_tag_groups_sort; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tag_groups_sort ON public.tag_groups USING btree (sort_order);


--
-- Name: idx_tags_group_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tags_group_id ON public.tags USING btree (group_id);


--
-- Name: idx_tags_name_zh; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tags_name_zh ON public.tags USING btree (name_zh);


--
-- Name: idx_tags_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tags_user_id ON public.tags USING btree (user_id);


--
-- Name: idx_task_flows_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_task_flows_created ON public.task_flows USING btree (created_at DESC);


--
-- Name: idx_task_flows_user_state; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_task_flows_user_state ON public.task_flows USING btree (user_id, state);


--
-- Name: idx_task_tracking_active_heartbeat; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_task_tracking_active_heartbeat ON public.task_tracking USING btree (heartbeat_at) WHERE (phase = ANY (ARRAY['queued'::text, 'in_progress'::text]));


--
-- Name: idx_task_tracking_active_per_resource_type; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_task_tracking_active_per_resource_type ON public.task_tracking USING btree (resource_id, task_type) WHERE ((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('queued'::character varying)::text, ('processing'::character varying)::text, ('running'::character varying)::text]));


--
-- Name: idx_task_tracking_agent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_task_tracking_agent ON public.task_tracking USING btree (agent_id, created_at DESC) WHERE (agent_id IS NOT NULL);


--
-- Name: idx_task_tracking_agent_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_task_tracking_agent_active ON public.task_tracking USING btree (agent_id, created_at DESC) WHERE ((task_kind = 'agent_task'::text) AND ((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('processing'::character varying)::text])));


--
-- Name: idx_task_tracking_dedup_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_task_tracking_dedup_key ON public.task_tracking USING btree (dedup_key) WHERE (dedup_key IS NOT NULL);


--
-- Name: idx_task_tracking_flow_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_task_tracking_flow_id ON public.task_tracking USING btree (flow_id) WHERE (flow_id IS NOT NULL);


--
-- Name: idx_task_tracking_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_task_tracking_parent ON public.task_tracking USING btree (parent_task_id) WHERE (parent_task_id IS NOT NULL);


--
-- Name: idx_task_tracking_phase; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_task_tracking_phase ON public.task_tracking USING btree (phase) WHERE (phase = ANY (ARRAY['queued'::text, 'dedup_check'::text, 'processing'::text]));


--
-- Name: idx_task_tracking_root; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_task_tracking_root ON public.task_tracking USING btree (root_task_id) WHERE (root_task_id IS NOT NULL);


--
-- Name: idx_task_tracking_task_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_task_tracking_task_kind ON public.task_tracking USING btree (task_kind);


--
-- Name: idx_task_tracking_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_task_tracking_type ON public.task_tracking USING btree (task_type);


--
-- Name: idx_task_tracking_user_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_task_tracking_user_active ON public.task_tracking USING btree (user_id, created_at DESC) WHERE ((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('processing'::character varying)::text]));


--
-- Name: idx_task_tracking_user_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_task_tracking_user_status ON public.task_tracking USING btree (user_id, status);


--
-- Name: idx_tasks_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tasks_active ON public.agent_tasks USING btree (agent_id, started_at DESC) WHERE (lifecycle_status = ANY (ARRAY['in_progress'::text, 'waiting_for_other'::text]));


--
-- Name: idx_tasks_queue; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tasks_queue ON public.agent_tasks USING btree (agent_id, created_at) WHERE (lifecycle_status = ANY (ARRAY['queued'::text, 'assigned'::text]));


--
-- Name: idx_tasks_user_recent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tasks_user_recent ON public.agent_tasks USING btree (user_id, created_at DESC);


--
-- Name: idx_team_invites_created_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_team_invites_created_by ON public.team_invites USING btree (created_by);


--
-- Name: idx_team_invites_team; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_team_invites_team ON public.team_invites USING btree (team_id);


--
-- Name: idx_team_members_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_team_members_user ON public.team_members USING btree (user_id);


--
-- Name: idx_teams_owner_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_teams_owner_id ON public.teams USING btree (owner_id);


--
-- Name: idx_temp_tokens_token; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_temp_tokens_token ON public.temp_tokens USING btree (token);


--
-- Name: idx_temp_tokens_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_temp_tokens_user_id ON public.temp_tokens USING btree (user_id);


--
-- Name: idx_user_cookies_user_platform; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_cookies_user_platform ON public.user_cookies USING btree (user_id, platform);


--
-- Name: idx_user_hidden_sources_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_hidden_sources_user ON public.user_hidden_sources USING btree (user_id);


--
-- Name: idx_user_logs_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_logs_created_at ON public.user_logs USING btree (created_at DESC);


--
-- Name: idx_user_logs_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_logs_user_id ON public.user_logs USING btree (user_id);


--
-- Name: idx_user_mcp_servers_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_mcp_servers_user ON public.user_mcp_servers USING btree (user_id) WHERE (enabled = true);


--
-- Name: idx_user_notifications_notification_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_notifications_notification_id ON public.user_notifications USING btree (notification_id);


--
-- Name: idx_user_schedules_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_schedules_due ON public.user_schedules USING btree (next_fire_at) WHERE (enabled = true);


--
-- Name: idx_user_schedules_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_schedules_user ON public.user_schedules USING btree (user_id, enabled);


--
-- Name: idx_user_settings_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_settings_user_id ON public.user_settings USING btree (user_id);


--
-- Name: idx_workflow_nodes_workflow_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_workflow_nodes_workflow_id ON public.workflow_nodes USING btree (workflow_id);


--
-- Name: issues_assignee_agent_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX issues_assignee_agent_status_idx ON public.issues USING btree (assignee_agent_id, status) WHERE (assignee_agent_id IS NOT NULL);


--
-- Name: issues_assignee_user_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX issues_assignee_user_status_idx ON public.issues USING btree (assignee_user_id, status) WHERE (assignee_user_id IS NOT NULL);


--
-- Name: issues_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX issues_created_at_idx ON public.issues USING btree (created_at DESC);


--
-- Name: issues_created_by_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX issues_created_by_user_idx ON public.issues USING btree (created_by_user_id) WHERE (created_by_user_id IS NOT NULL);


--
-- Name: issues_dbos_workflow_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX issues_dbos_workflow_idx ON public.issues USING btree (dbos_workflow_id) WHERE (dbos_workflow_id IS NOT NULL);


--
-- Name: issues_description_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX issues_description_trgm_idx ON public.issues USING gin (description public.gin_trgm_ops) WHERE (description IS NOT NULL);


--
-- Name: issues_identifier_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX issues_identifier_idx ON public.issues USING btree (identifier);


--
-- Name: issues_identifier_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX issues_identifier_trgm_idx ON public.issues USING gin (identifier public.gin_trgm_ops);


--
-- Name: issues_open_routine_execution_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX issues_open_routine_execution_uq ON public.issues USING btree (origin_kind, origin_id, origin_fingerprint) WHERE ((origin_kind = 'routine'::text) AND (status <> ALL (ARRAY['done'::text, 'cancelled'::text])) AND (hidden_at IS NULL));


--
-- Name: issues_origin_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX issues_origin_idx ON public.issues USING btree (origin_kind, origin_id);


--
-- Name: issues_parent_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX issues_parent_idx ON public.issues USING btree (parent_id);


--
-- Name: issues_project_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX issues_project_status_idx ON public.issues USING btree (project_id, status);


--
-- Name: issues_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX issues_status_idx ON public.issues USING btree (status);


--
-- Name: issues_team_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX issues_team_status_idx ON public.issues USING btree (team_id, status);


--
-- Name: issues_title_trgm_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX issues_title_trgm_idx ON public.issues USING gin (title public.gin_trgm_ops);


--
-- Name: ix_ai_agent_versions_agent_id_desc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ai_agent_versions_agent_id_desc ON public.ai_agent_versions USING btree (agent_id, version_number DESC);


--
-- Name: ix_skill_file_versions_file_id_desc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_skill_file_versions_file_id_desc ON public.skill_file_versions USING btree (skill_file_id, version_number DESC);


--
-- Name: ix_skill_versions_skill_id_desc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_skill_versions_skill_id_desc ON public.skill_versions USING btree (skill_id, version_number DESC);


--
-- Name: project_tasks_issue_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX project_tasks_issue_id_idx ON public.project_tasks USING btree (issue_id) WHERE (issue_id IS NOT NULL);


--
-- Name: task_tracking_issue_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX task_tracking_issue_id_idx ON public.task_tracking USING btree (issue_id) WHERE (issue_id IS NOT NULL);


--
-- Name: uq_agent_memory_promotions_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_agent_memory_promotions_pending ON public.agent_memory_promotions USING btree (memory_id) WHERE (status = 'pending'::text);


--
-- Name: uq_conversation_members; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_conversation_members ON public.conversation_members USING btree (conversation_id, member_type, COALESCE(user_id, agent_id));


--
-- Name: uq_hotspots_dedup; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_hotspots_dedup ON public.hotspots USING btree (dedup_key);


--
-- Name: uq_project_characters_project_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_project_characters_project_name ON public.project_characters USING btree (project_id, name);


--
-- Name: uq_project_lib_entities_ptn; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_project_lib_entities_ptn ON public.project_lib_entities USING btree (project_id, entity_type, name);


--
-- Name: uq_project_stage_history_open; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_project_stage_history_open ON public.project_stage_history USING btree (project_id) WHERE (exited_at IS NULL);


--
-- Name: uq_publish_task_accounts_share_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_publish_task_accounts_share_id ON public.publish_task_accounts USING btree (share_id) WHERE (share_id IS NOT NULL);


--
-- Name: uq_teams_owner_personal; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_teams_owner_personal ON public.teams USING btree (owner_id) WHERE (kind = 'personal'::text);


--
-- Name: ux_agent_overrides_team; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_agent_overrides_team ON public.agent_overrides USING btree (agent_id, team_id) WHERE (team_id IS NOT NULL);


--
-- Name: ux_agent_overrides_user; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_agent_overrides_user ON public.agent_overrides USING btree (agent_id, user_id) WHERE (user_id IS NOT NULL);


--
-- Name: ux_ai_agents_slug_system; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_ai_agents_slug_system ON public.ai_agents USING btree (slug) WHERE (is_system_preset = true);


--
-- Name: ux_ai_agents_slug_user; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_ai_agents_slug_user ON public.ai_agents USING btree (user_id, slug) WHERE ((is_system_preset = false) AND (user_id IS NOT NULL));


--
-- Name: ux_skill_files_path; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_skill_files_path ON public.skill_files USING btree (skill_id, path);


--
-- Name: ux_skills_slug_creator; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_skills_slug_creator ON public.skills USING btree (created_by, slug) WHERE ((created_by IS NOT NULL) AND (slug IS NOT NULL));


--
-- Name: ux_skills_slug_system; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_skills_slug_system ON public.skills USING btree (slug) WHERE ((created_by IS NULL) AND (slug IS NOT NULL));


--
-- Name: dbos_workflow_routing dbos_workflow_routing_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dbos_workflow_routing_touch BEFORE UPDATE ON public.dbos_workflow_routing FOR EACH ROW EXECUTE FUNCTION public.touch_dbos_workflow_routing_updated_at();


--
-- Name: issues issues_touch_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER issues_touch_updated_at BEFORE UPDATE ON public.issues FOR EACH ROW EXECUTE FUNCTION public.touch_issue_updated_at();


--
-- Name: issues issues_update_allowlist; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER issues_update_allowlist BEFORE UPDATE ON public.issues FOR EACH ROW EXECUTE FUNCTION public.issues_enforce_update_allowlist();


--
-- Name: notifications notifications_team_trigger; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER notifications_team_trigger AFTER INSERT ON public.notifications FOR EACH ROW EXECUTE FUNCTION public.notify_team_members();


--
-- Name: smart_collections smart_collections_updated; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER smart_collections_updated BEFORE UPDATE ON public.smart_collections FOR EACH ROW EXECUTE FUNCTION public.update_video_analysis_timestamp();


--
-- Name: team_invites team_invites_code_trigger; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER team_invites_code_trigger BEFORE INSERT ON public.team_invites FOR EACH ROW EXECUTE FUNCTION public.set_team_invite_code();


--
-- Name: teams teams_add_owner_trigger; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER teams_add_owner_trigger AFTER INSERT ON public.teams FOR EACH ROW EXECUTE FUNCTION public.add_owner_as_member();


--
-- Name: teams teams_invite_code_trigger; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER teams_invite_code_trigger BEFORE INSERT ON public.teams FOR EACH ROW EXECUTE FUNCTION public.set_invite_code();


--
-- Name: agent_runs trg_agent_runs_default_last_useful_action; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_agent_runs_default_last_useful_action BEFORE INSERT ON public.agent_runs FOR EACH ROW EXECUTE FUNCTION public.agent_runs_default_last_useful_action();


--
-- Name: agent_runs trg_agent_runs_emit_chat; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_agent_runs_emit_chat AFTER UPDATE OF status ON public.agent_runs FOR EACH ROW EXECUTE FUNCTION public.emit_agent_run_message_on_resolve();


--
-- Name: agent_runs trg_agent_runs_emit_dispatch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_agent_runs_emit_dispatch AFTER INSERT ON public.agent_runs FOR EACH ROW EXECUTE FUNCTION public.emit_agent_run_dispatch_message();


--
-- Name: agent_runs trg_agent_runs_emit_liveness_change; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_agent_runs_emit_liveness_change AFTER UPDATE OF liveness_state ON public.agent_runs FOR EACH ROW EXECUTE FUNCTION public.update_agent_run_message_on_liveness();


--
-- Name: agent_runs trg_agent_runs_track_liveness_change; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_agent_runs_track_liveness_change BEFORE UPDATE OF liveness_state ON public.agent_runs FOR EACH ROW EXECUTE FUNCTION public.agent_runs_track_liveness_change();


--
-- Name: agent_tasks trg_bump_agent_tasks_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_bump_agent_tasks_updated_at BEFORE UPDATE ON public.agent_tasks FOR EACH ROW EXECUTE FUNCTION public.bump_agent_tasks_updated_at();


--
-- Name: canvases trg_canvases_touch_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_canvases_touch_updated_at BEFORE UPDATE ON public.canvases FOR EACH ROW EXECUTE FUNCTION public.canvases_touch_updated_at();


--
-- Name: agent_runs trg_cascade_cancel_run; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_cascade_cancel_run AFTER UPDATE OF cancel_requested ON public.agent_runs FOR EACH ROW EXECUTE FUNCTION public.cascade_cancel_run();


--
-- Name: team_members trg_enforce_personal_team_singleton; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_enforce_personal_team_singleton BEFORE INSERT ON public.team_members FOR EACH ROW EXECUTE FUNCTION public.enforce_personal_team_singleton();


--
-- Name: issues trg_issue_status_change_message; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_issue_status_change_message AFTER UPDATE OF status ON public.issues FOR EACH ROW EXECUTE FUNCTION public.emit_issue_status_change_message();


--
-- Name: deployment_logs trg_notify_on_release_notes; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_notify_on_release_notes AFTER INSERT OR UPDATE ON public.deployment_logs FOR EACH ROW EXECUTE FUNCTION public.notify_on_release_notes();


--
-- Name: project_stages trg_project_stages_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_project_stages_updated_at BEFORE UPDATE ON public.project_stages FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: project_style_profile trg_project_style_profile_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_project_style_profile_updated_at BEFORE UPDATE ON public.project_style_profile FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: zzz_deprecated_storyboard_characters trg_sb_characters_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_sb_characters_updated_at BEFORE UPDATE ON public.zzz_deprecated_storyboard_characters FOR EACH ROW EXECUTE FUNCTION public.update_sb_updated_at();


--
-- Name: zzz_deprecated_storyboard_frames trg_sb_frames_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_sb_frames_updated_at BEFORE UPDATE ON public.zzz_deprecated_storyboard_frames FOR EACH ROW EXECUTE FUNCTION public.update_sb_updated_at();


--
-- Name: zzz_deprecated_storyboard_nodes trg_sb_nodes_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_sb_nodes_updated_at BEFORE UPDATE ON public.zzz_deprecated_storyboard_nodes FOR EACH ROW EXECUTE FUNCTION public.update_sb_updated_at();


--
-- Name: zzz_deprecated_storyboard_projects trg_sb_projects_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_sb_projects_updated_at BEFORE UPDATE ON public.zzz_deprecated_storyboard_projects FOR EACH ROW EXECUTE FUNCTION public.update_sb_updated_at();


--
-- Name: script_assets trg_script_assets_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_script_assets_updated_at BEFORE UPDATE ON public.script_assets FOR EACH ROW EXECUTE FUNCTION public.update_script_chapters_updated_at();


--
-- Name: script_chapters trg_script_chapters_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_script_chapters_updated_at BEFORE UPDATE ON public.script_chapters FOR EACH ROW EXECUTE FUNCTION public.update_script_chapters_updated_at();


--
-- Name: script_projects trg_script_projects_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_script_projects_updated_at BEFORE UPDATE ON public.script_projects FOR EACH ROW EXECUTE FUNCTION public.update_script_projects_updated_at();


--
-- Name: skill_files trg_skill_files_bump_parent; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_skill_files_bump_parent AFTER INSERT OR DELETE OR UPDATE ON public.skill_files FOR EACH ROW EXECUTE FUNCTION public.skill_files_bump_parent_updated_at();


--
-- Name: system_status trg_system_status_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_system_status_updated_at BEFORE UPDATE ON public.system_status FOR EACH ROW EXECUTE FUNCTION public.update_system_status_updated_at();


--
-- Name: task_tracking trg_task_tracking_flow_agg_del; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_task_tracking_flow_agg_del AFTER DELETE ON public.task_tracking REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.update_flow_aggregate_stmt();


--
-- Name: task_tracking trg_task_tracking_flow_agg_ins; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_task_tracking_flow_agg_ins AFTER INSERT ON public.task_tracking REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.update_flow_aggregate_stmt();


--
-- Name: TRIGGER trg_task_tracking_flow_agg_ins ON task_tracking; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TRIGGER trg_task_tracking_flow_agg_ins ON public.task_tracking IS 'Statement-level flow aggregate (mig 310). Replaces the row-level
     trg_task_tracking_flow_aggregate from mig 203.';


--
-- Name: task_tracking trg_task_tracking_flow_agg_upd; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_task_tracking_flow_agg_upd AFTER UPDATE ON public.task_tracking REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.update_flow_aggregate_stmt();


--
-- Name: task_tracking trg_unified_tasks_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_unified_tasks_updated_at BEFORE UPDATE ON public.task_tracking FOR EACH ROW EXECUTE FUNCTION public.update_unified_tasks_updated_at();


--
-- Name: resource_items trigger_check_orphan_resource; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trigger_check_orphan_resource AFTER DELETE ON public.resource_items FOR EACH ROW EXECUTE FUNCTION public.check_orphan_resource();


--
-- Name: user_settings trigger_update_user_settings_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trigger_update_user_settings_updated_at BEFORE UPDATE ON public.user_settings FOR EACH ROW EXECUTE FUNCTION public.update_user_settings_updated_at();


--
-- Name: api_keys update_api_keys_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_api_keys_updated_at BEFORE UPDATE ON public.api_keys FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: authors update_authors_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_authors_updated_at BEFORE UPDATE ON public.authors FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: credit_pricing update_credit_pricing_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_credit_pricing_updated_at BEFORE UPDATE ON public.credit_pricing FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: folders update_folders_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_folders_updated_at BEFORE UPDATE ON public.folders FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: member_quotas update_member_quotas_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_member_quotas_updated_at BEFORE UPDATE ON public.member_quotas FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: orders update_orders_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_orders_updated_at BEFORE UPDATE ON public.orders FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: point_packages update_point_packages_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_point_packages_updated_at BEFORE UPDATE ON public.point_packages FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: point_pricing update_point_pricing_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_point_pricing_updated_at BEFORE UPDATE ON public.point_pricing FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: project_files update_project_files_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_project_files_updated_at BEFORE UPDATE ON public.project_files FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: project_folders update_project_folders_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_project_folders_updated_at BEFORE UPDATE ON public.project_folders FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: project_tasks update_project_tasks_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_project_tasks_updated_at BEFORE UPDATE ON public.project_tasks FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: projects update_projects_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_projects_updated_at BEFORE UPDATE ON public.projects FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: resources update_resources_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_resources_updated_at BEFORE UPDATE ON public.resources FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: system_settings update_system_settings_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_system_settings_updated_at BEFORE UPDATE ON public.system_settings FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: team_quotas update_team_quotas_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_team_quotas_updated_at BEFORE UPDATE ON public.team_quotas FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: user_credits update_user_credits_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_user_credits_updated_at BEFORE UPDATE ON public.user_credits FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: user_profiles update_user_profiles_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_user_profiles_updated_at BEFORE UPDATE ON public.user_profiles FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: parsed_media update_videos_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_videos_updated_at BEFORE UPDATE ON public.parsed_media FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: user_profiles user_profiles_init_credits; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER user_profiles_init_credits AFTER INSERT ON public.user_profiles FOR EACH ROW EXECUTE FUNCTION public.initialize_user_credits();


--
-- Name: access_overrides access_overrides_granted_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.access_overrides
    ADD CONSTRAINT access_overrides_granted_by_fkey FOREIGN KEY (granted_by) REFERENCES auth.users(id);


--
-- Name: access_overrides access_overrides_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.access_overrides
    ADD CONSTRAINT access_overrides_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: admin_table_preferences admin_table_preferences_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_table_preferences
    ADD CONSTRAINT admin_table_preferences_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: agent_approval_requests agent_approval_requests_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_approval_requests
    ADD CONSTRAINT agent_approval_requests_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: agent_commitments agent_commitments_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_commitments
    ADD CONSTRAINT agent_commitments_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.ai_agents(id) ON DELETE CASCADE;


--
-- Name: agent_commitments agent_commitments_fulfillment_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_commitments
    ADD CONSTRAINT agent_commitments_fulfillment_run_id_fkey FOREIGN KEY (fulfillment_run_id) REFERENCES public.agent_runs(id) ON DELETE SET NULL;


--
-- Name: agent_inbox agent_inbox_recipient_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_inbox
    ADD CONSTRAINT agent_inbox_recipient_agent_id_fkey FOREIGN KEY (recipient_agent_id) REFERENCES public.ai_agents(id) ON DELETE CASCADE;


--
-- Name: agent_inbox agent_inbox_reply_to_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_inbox
    ADD CONSTRAINT agent_inbox_reply_to_message_id_fkey FOREIGN KEY (reply_to_message_id) REFERENCES public.agent_inbox(id) ON DELETE SET NULL;


--
-- Name: agent_inbox agent_inbox_sender_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_inbox
    ADD CONSTRAINT agent_inbox_sender_agent_id_fkey FOREIGN KEY (sender_agent_id) REFERENCES public.ai_agents(id) ON DELETE SET NULL;


--
-- Name: agent_memory_promotions agent_memory_promotions_memory_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_memory_promotions
    ADD CONSTRAINT agent_memory_promotions_memory_id_fkey FOREIGN KEY (memory_id) REFERENCES public.agent_memory(id) ON DELETE CASCADE;


--
-- Name: agent_outbox agent_outbox_recipient_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_outbox
    ADD CONSTRAINT agent_outbox_recipient_agent_id_fkey FOREIGN KEY (recipient_agent_id) REFERENCES public.ai_agents(id) ON DELETE SET NULL;


--
-- Name: agent_outbox agent_outbox_sender_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_outbox
    ADD CONSTRAINT agent_outbox_sender_agent_id_fkey FOREIGN KEY (sender_agent_id) REFERENCES public.ai_agents(id) ON DELETE CASCADE;


--
-- Name: agent_overrides agent_overrides_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_overrides
    ADD CONSTRAINT agent_overrides_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.ai_agents(id) ON DELETE CASCADE;


--
-- Name: agent_overrides agent_overrides_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_overrides
    ADD CONSTRAINT agent_overrides_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: agent_run_events agent_run_events_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_run_events
    ADD CONSTRAINT agent_run_events_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.agent_runs(id) ON DELETE CASCADE;


--
-- Name: agent_runs agent_runs_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT agent_runs_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.ai_agents(id) ON DELETE CASCADE;


--
-- Name: agent_runs agent_runs_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT agent_runs_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id) ON DELETE SET NULL;


--
-- Name: agent_runs agent_runs_issue_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT agent_runs_issue_id_fkey FOREIGN KEY (issue_id) REFERENCES public.issues(id) ON DELETE SET NULL;


--
-- Name: agent_runs agent_runs_parent_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT agent_runs_parent_run_id_fkey FOREIGN KEY (parent_run_id) REFERENCES public.agent_runs(id) ON DELETE CASCADE;


--
-- Name: agent_runs agent_runs_root_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT agent_runs_root_run_id_fkey FOREIGN KEY (root_run_id) REFERENCES public.agent_runs(id);


--
-- Name: agent_runs agent_runs_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT agent_runs_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.task_tracking(dbos_workflow_id) ON DELETE SET NULL;


--
-- Name: agent_skills agent_skills_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_skills
    ADD CONSTRAINT agent_skills_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.ai_agents(id) ON DELETE CASCADE;


--
-- Name: agent_skills agent_skills_skill_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_skills
    ADD CONSTRAINT agent_skills_skill_id_fkey FOREIGN KEY (skill_id) REFERENCES public.skills(id) ON DELETE CASCADE;


--
-- Name: agent_state_history agent_state_history_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_state_history
    ADD CONSTRAINT agent_state_history_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.ai_agents(id) ON DELETE CASCADE;


--
-- Name: agent_tasks agent_tasks_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_tasks
    ADD CONSTRAINT agent_tasks_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.ai_agents(id) ON DELETE CASCADE;


--
-- Name: agent_tasks agent_tasks_current_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_tasks
    ADD CONSTRAINT agent_tasks_current_run_id_fkey FOREIGN KEY (current_run_id) REFERENCES public.agent_runs(id) ON DELETE SET NULL;


--
-- Name: agent_tasks agent_tasks_inbox_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_tasks
    ADD CONSTRAINT agent_tasks_inbox_message_id_fkey FOREIGN KEY (inbox_message_id) REFERENCES public.agent_inbox(id) ON DELETE SET NULL;


--
-- Name: agent_tasks agent_tasks_parent_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_tasks
    ADD CONSTRAINT agent_tasks_parent_task_id_fkey FOREIGN KEY (parent_task_id) REFERENCES public.agent_tasks(id) ON DELETE SET NULL;


--
-- Name: agent_tasks agent_tasks_root_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_tasks
    ADD CONSTRAINT agent_tasks_root_task_id_fkey FOREIGN KEY (root_task_id) REFERENCES public.agent_tasks(id) ON DELETE SET NULL;


--
-- Name: agent_workers agent_workers_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_workers
    ADD CONSTRAINT agent_workers_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.ai_agents(id) ON DELETE CASCADE;


--
-- Name: ai_agent_versions ai_agent_versions_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_agent_versions
    ADD CONSTRAINT ai_agent_versions_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.ai_agents(id) ON DELETE CASCADE;


--
-- Name: alert_history alert_history_rule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_history
    ADD CONSTRAINT alert_history_rule_id_fkey FOREIGN KEY (rule_id) REFERENCES public.alert_rules(id) ON DELETE CASCADE;


--
-- Name: alert_rules alert_rules_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_rules
    ADD CONSTRAINT alert_rules_created_by_fkey FOREIGN KEY (created_by) REFERENCES auth.users(id);


--
-- Name: api_key_logs api_key_logs_api_key_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_key_logs
    ADD CONSTRAINT api_key_logs_api_key_id_fkey FOREIGN KEY (api_key_id) REFERENCES public.api_keys(id) ON DELETE CASCADE;


--
-- Name: api_keys api_keys_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: audit_logs audit_logs_admin_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_admin_id_fkey FOREIGN KEY (admin_id) REFERENCES auth.users(id);


--
-- Name: canvas_resource_refs canvas_resource_refs_canvas_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.canvas_resource_refs
    ADD CONSTRAINT canvas_resource_refs_canvas_id_fkey FOREIGN KEY (canvas_id) REFERENCES public.canvases(id) ON DELETE CASCADE;


--
-- Name: canvas_resource_refs canvas_resource_refs_resource_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.canvas_resource_refs
    ADD CONSTRAINT canvas_resource_refs_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES public.resources(id) ON DELETE CASCADE;


--
-- Name: canvases canvases_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.canvases
    ADD CONSTRAINT canvases_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: collections collections_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collections
    ADD CONSTRAINT collections_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE SET NULL;


--
-- Name: collections collections_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collections
    ADD CONSTRAINT collections_user_id_fkey FOREIGN KEY (owner_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: conversation_ai_meta conversation_ai_meta_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_ai_meta
    ADD CONSTRAINT conversation_ai_meta_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id) ON DELETE CASCADE;


--
-- Name: conversation_members conversation_members_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_members
    ADD CONSTRAINT conversation_members_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.ai_agents(id) ON DELETE CASCADE;


--
-- Name: conversation_members conversation_members_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_members
    ADD CONSTRAINT conversation_members_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id) ON DELETE CASCADE;


--
-- Name: conversation_members conversation_members_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_members
    ADD CONSTRAINT conversation_members_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: conversation_memory conversation_memory_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_memory
    ADD CONSTRAINT conversation_memory_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id) ON DELETE CASCADE;


--
-- Name: conversations conversations_scope_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_scope_id_fkey FOREIGN KEY (scope_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: credit_transactions credit_transactions_admin_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_transactions
    ADD CONSTRAINT credit_transactions_admin_id_fkey FOREIGN KEY (admin_id) REFERENCES auth.users(id);


--
-- Name: credit_transactions credit_transactions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_transactions
    ADD CONSTRAINT credit_transactions_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: daily_point_gifts daily_point_gifts_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.daily_point_gifts
    ADD CONSTRAINT daily_point_gifts_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id);


--
-- Name: episodes episodes_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.episodes
    ADD CONSTRAINT episodes_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: file_versions file_versions_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_versions
    ADD CONSTRAINT file_versions_file_id_fkey FOREIGN KEY (file_id) REFERENCES public.project_files(id) ON DELETE CASCADE;


--
-- Name: file_versions file_versions_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_versions
    ADD CONSTRAINT file_versions_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES auth.users(id);


--
-- Name: folders folders_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.folders
    ADD CONSTRAINT folders_created_by_fkey FOREIGN KEY (created_by) REFERENCES auth.users(id);


--
-- Name: folders folders_library_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.folders
    ADD CONSTRAINT folders_library_id_fkey FOREIGN KEY (library_id) REFERENCES public.libraries(id) ON DELETE CASCADE;


--
-- Name: folders folders_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.folders
    ADD CONSTRAINT folders_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.folders(id) ON DELETE CASCADE;


--
-- Name: folders folders_scope_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.folders
    ADD CONSTRAINT folders_scope_id_fkey FOREIGN KEY (scope_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: generated_media generated_media_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.generated_media
    ADD CONSTRAINT generated_media_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id) ON DELETE SET NULL;


--
-- Name: hotspot_user_state hotspot_user_state_hotspot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hotspot_user_state
    ADD CONSTRAINT hotspot_user_state_hotspot_id_fkey FOREIGN KEY (hotspot_id) REFERENCES public.hotspots(id) ON DELETE CASCADE;


--
-- Name: hotspot_user_state hotspot_user_state_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hotspot_user_state
    ADD CONSTRAINT hotspot_user_state_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: hotspots hotspots_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hotspots
    ADD CONSTRAINT hotspots_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.signal_sources(id) ON DELETE SET NULL;


--
-- Name: hotspots hotspots_topic_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hotspots
    ADD CONSTRAINT hotspots_topic_group_id_fkey FOREIGN KEY (topic_group_id) REFERENCES public.topic_groups(id) ON DELETE SET NULL;


--
-- Name: hotspots hotspots_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hotspots
    ADD CONSTRAINT hotspots_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: inspiration_attachments inspiration_attachments_note_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inspiration_attachments
    ADD CONSTRAINT inspiration_attachments_note_id_fkey FOREIGN KEY (note_id) REFERENCES public.inspiration_notes(id) ON DELETE CASCADE;


--
-- Name: issue_messages issue_messages_agent_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issue_messages
    ADD CONSTRAINT issue_messages_agent_run_id_fkey FOREIGN KEY (agent_run_id) REFERENCES public.agent_runs(id) ON DELETE SET NULL;


--
-- Name: issue_messages issue_messages_author_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issue_messages
    ADD CONSTRAINT issue_messages_author_agent_id_fkey FOREIGN KEY (author_agent_id) REFERENCES public.ai_agents(id) ON DELETE SET NULL;


--
-- Name: issue_messages issue_messages_issue_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issue_messages
    ADD CONSTRAINT issue_messages_issue_id_fkey FOREIGN KEY (issue_id) REFERENCES public.issues(id) ON DELETE CASCADE;


--
-- Name: issues issues_assignee_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issues
    ADD CONSTRAINT issues_assignee_agent_id_fkey FOREIGN KEY (assignee_agent_id) REFERENCES public.ai_agents(id) ON DELETE SET NULL;


--
-- Name: issues issues_assignee_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issues
    ADD CONSTRAINT issues_assignee_user_id_fkey FOREIGN KEY (assignee_user_id) REFERENCES auth.users(id) ON DELETE SET NULL;


--
-- Name: issues issues_created_by_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issues
    ADD CONSTRAINT issues_created_by_agent_id_fkey FOREIGN KEY (created_by_agent_id) REFERENCES public.ai_agents(id) ON DELETE SET NULL;


--
-- Name: issues issues_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issues
    ADD CONSTRAINT issues_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES auth.users(id) ON DELETE SET NULL;


--
-- Name: issues issues_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issues
    ADD CONSTRAINT issues_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.issues(id) ON DELETE SET NULL;


--
-- Name: issues issues_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issues
    ADD CONSTRAINT issues_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE SET NULL;


--
-- Name: issues issues_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.issues
    ADD CONSTRAINT issues_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE SET NULL;


--
-- Name: libraries libraries_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.libraries
    ADD CONSTRAINT libraries_created_by_fkey FOREIGN KEY (created_by) REFERENCES auth.users(id);


--
-- Name: member_quotas member_quotas_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.member_quotas
    ADD CONSTRAINT member_quotas_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: member_quotas member_quotas_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.member_quotas
    ADD CONSTRAINT member_quotas_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: message_attachments message_attachments_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.message_attachments
    ADD CONSTRAINT message_attachments_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.messages(id) ON DELETE CASCADE;


--
-- Name: message_refs message_refs_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.message_refs
    ADD CONSTRAINT message_refs_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.messages(id) ON DELETE CASCADE;


--
-- Name: messages messages_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id) ON DELETE CASCADE;


--
-- Name: messages messages_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.messages(id) ON DELETE SET NULL;


--
-- Name: notifications notifications_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_created_by_fkey FOREIGN KEY (created_by) REFERENCES auth.users(id);


--
-- Name: notifications notifications_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: orders orders_package_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_package_id_fkey FOREIGN KEY (package_id) REFERENCES public.point_packages(id);


--
-- Name: orders orders_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: orders orders_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id);


--
-- Name: point_transactions point_transactions_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.point_transactions
    ADD CONSTRAINT point_transactions_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: point_transactions point_transactions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.point_transactions
    ADD CONSTRAINT point_transactions_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id);


--
-- Name: project_characters project_characters_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_characters
    ADD CONSTRAINT project_characters_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: project_collections project_collections_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_collections
    ADD CONSTRAINT project_collections_created_by_fkey FOREIGN KEY (created_by) REFERENCES auth.users(id);


--
-- Name: project_collections project_collections_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_collections
    ADD CONSTRAINT project_collections_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: project_file_comments project_file_comments_author_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_file_comments
    ADD CONSTRAINT project_file_comments_author_id_fkey FOREIGN KEY (author_id) REFERENCES auth.users(id);


--
-- Name: project_file_comments project_file_comments_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_file_comments
    ADD CONSTRAINT project_file_comments_file_id_fkey FOREIGN KEY (file_id) REFERENCES public.project_files(id) ON DELETE CASCADE;


--
-- Name: project_file_comments project_file_comments_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_file_comments
    ADD CONSTRAINT project_file_comments_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.file_versions(id) ON DELETE SET NULL;


--
-- Name: project_files project_files_folder_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_files
    ADD CONSTRAINT project_files_folder_id_fkey FOREIGN KEY (folder_id) REFERENCES public.project_folders(id) ON DELETE SET NULL;


--
-- Name: project_files project_files_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_files
    ADD CONSTRAINT project_files_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: project_files project_files_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_files
    ADD CONSTRAINT project_files_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES auth.users(id);


--
-- Name: project_files project_files_video_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_files
    ADD CONSTRAINT project_files_video_id_fkey FOREIGN KEY (media_id) REFERENCES public.parsed_media(id) ON DELETE SET NULL;


--
-- Name: project_folders project_folders_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_folders
    ADD CONSTRAINT project_folders_created_by_fkey FOREIGN KEY (created_by) REFERENCES auth.users(id);


--
-- Name: project_folders project_folders_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_folders
    ADD CONSTRAINT project_folders_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.project_folders(id) ON DELETE CASCADE;


--
-- Name: project_folders project_folders_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_folders
    ADD CONSTRAINT project_folders_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: project_lib_entities project_lib_entities_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_lib_entities
    ADD CONSTRAINT project_lib_entities_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: project_members project_members_invited_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_members
    ADD CONSTRAINT project_members_invited_by_fkey FOREIGN KEY (invited_by) REFERENCES auth.users(id);


--
-- Name: project_members project_members_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_members
    ADD CONSTRAINT project_members_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: project_members project_members_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_members
    ADD CONSTRAINT project_members_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: project_stage_history project_stage_history_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_stage_history
    ADD CONSTRAINT project_stage_history_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: project_stage_history project_stage_history_stage_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_stage_history
    ADD CONSTRAINT project_stage_history_stage_id_fkey FOREIGN KEY (stage_id) REFERENCES public.project_stages(id);


--
-- Name: project_stage_history project_stage_history_transitioned_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_stage_history
    ADD CONSTRAINT project_stage_history_transitioned_by_fkey FOREIGN KEY (transitioned_by) REFERENCES auth.users(id) ON DELETE SET NULL;


--
-- Name: project_style_profile project_style_profile_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_style_profile
    ADD CONSTRAINT project_style_profile_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: project_style_profile project_style_profile_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_style_profile
    ADD CONSTRAINT project_style_profile_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES auth.users(id) ON DELETE SET NULL;


--
-- Name: project_tasks project_tasks_assignee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_tasks
    ADD CONSTRAINT project_tasks_assignee_id_fkey FOREIGN KEY (assignee_id) REFERENCES auth.users(id);


--
-- Name: project_tasks project_tasks_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_tasks
    ADD CONSTRAINT project_tasks_created_by_fkey FOREIGN KEY (created_by) REFERENCES auth.users(id);


--
-- Name: project_tasks project_tasks_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_tasks
    ADD CONSTRAINT project_tasks_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: project_tasks project_tasks_workflow_node_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_tasks
    ADD CONSTRAINT project_tasks_workflow_node_id_fkey FOREIGN KEY (workflow_node_id) REFERENCES public.workflow_nodes(id) ON DELETE SET NULL;


--
-- Name: project_workflows project_workflows_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_workflows
    ADD CONSTRAINT project_workflows_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: projects projects_current_stage_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_current_stage_id_fkey FOREIGN KEY (current_stage_id) REFERENCES public.project_stages(id) ON DELETE SET NULL;


--
-- Name: projects projects_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES auth.users(id);


--
-- Name: projects projects_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE SET NULL;


--
-- Name: projects projects_workflow_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_workflow_id_fkey FOREIGN KEY (workflow_id) REFERENCES public.project_workflows(id);


--
-- Name: provider_credits provider_credits_contract_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.provider_credits
    ADD CONSTRAINT provider_credits_contract_id_fkey FOREIGN KEY (contract_id) REFERENCES public.provider_contracts(contract_id) ON DELETE SET NULL;


--
-- Name: provider_pricing provider_pricing_contract_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.provider_pricing
    ADD CONSTRAINT provider_pricing_contract_fk FOREIGN KEY (contract_id) REFERENCES public.provider_contracts(contract_id) ON DELETE SET NULL;


--
-- Name: publish_task_accounts publish_task_accounts_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.publish_task_accounts
    ADD CONSTRAINT publish_task_accounts_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.social_accounts(id) ON DELETE CASCADE;


--
-- Name: publish_task_accounts publish_task_accounts_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.publish_task_accounts
    ADD CONSTRAINT publish_task_accounts_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.publish_tasks(id) ON DELETE CASCADE;


--
-- Name: resource_access_logs resource_access_logs_resource_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_access_logs
    ADD CONSTRAINT resource_access_logs_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES public.resources(id) ON DELETE CASCADE;


--
-- Name: resource_analysis resource_analysis_resource_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_analysis
    ADD CONSTRAINT resource_analysis_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES public.resources(id) ON DELETE CASCADE;


--
-- Name: resource_items resource_items_added_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_items
    ADD CONSTRAINT resource_items_added_by_fkey FOREIGN KEY (added_by) REFERENCES auth.users(id);


--
-- Name: resource_items resource_items_folder_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_items
    ADD CONSTRAINT resource_items_folder_id_fkey FOREIGN KEY (folder_id) REFERENCES public.folders(id) ON DELETE SET NULL;


--
-- Name: resource_items resource_items_library_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_items
    ADD CONSTRAINT resource_items_library_id_fkey FOREIGN KEY (library_id) REFERENCES public.libraries(id) ON DELETE CASCADE;


--
-- Name: resource_items resource_items_resource_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_items
    ADD CONSTRAINT resource_items_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES public.resources(id) ON DELETE CASCADE;


--
-- Name: resource_items resource_items_scope_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_items
    ADD CONSTRAINT resource_items_scope_id_fkey FOREIGN KEY (scope_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: resource_summaries resource_summaries_resource_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_summaries
    ADD CONSTRAINT resource_summaries_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES public.resources(id) ON DELETE CASCADE;


--
-- Name: resource_tags resource_tags_resource_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_tags
    ADD CONSTRAINT resource_tags_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES public.resources(id) ON DELETE CASCADE;


--
-- Name: resource_tags resource_tags_tag_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_tags
    ADD CONSTRAINT resource_tags_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES public.tags(id) ON DELETE CASCADE;


--
-- Name: resource_tags resource_tags_tagged_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_tags
    ADD CONSTRAINT resource_tags_tagged_by_fkey FOREIGN KEY (tagged_by) REFERENCES auth.users(id);


--
-- Name: resource_transcripts resource_transcripts_resource_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_transcripts
    ADD CONSTRAINT resource_transcripts_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES public.resources(id) ON DELETE CASCADE;


--
-- Name: resource_versions resource_versions_resource_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_versions
    ADD CONSTRAINT resource_versions_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES public.resources(id) ON DELETE CASCADE;


--
-- Name: resource_versions resource_versions_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_versions
    ADD CONSTRAINT resource_versions_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES auth.users(id);


--
-- Name: resources resources_creator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resources
    ADD CONSTRAINT resources_creator_id_fkey FOREIGN KEY (creator_id) REFERENCES auth.users(id);


--
-- Name: resources resources_video_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resources
    ADD CONSTRAINT resources_video_id_fkey FOREIGN KEY (media_id) REFERENCES public.parsed_media(id) ON DELETE SET NULL;


--
-- Name: review_annotations review_annotations_comment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.review_annotations
    ADD CONSTRAINT review_annotations_comment_id_fkey FOREIGN KEY (comment_id) REFERENCES public.review_comments(id) ON DELETE CASCADE;


--
-- Name: review_comments review_comments_author_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.review_comments
    ADD CONSTRAINT review_comments_author_id_fkey FOREIGN KEY (author_id) REFERENCES auth.users(id);


--
-- Name: review_comments review_comments_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.review_comments
    ADD CONSTRAINT review_comments_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.review_comments(id) ON DELETE CASCADE;


--
-- Name: review_comments review_comments_resource_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.review_comments
    ADD CONSTRAINT review_comments_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES public.resources(id) ON DELETE CASCADE;


--
-- Name: review_comments review_comments_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.review_comments
    ADD CONSTRAINT review_comments_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.resource_versions(id) ON DELETE SET NULL;


--
-- Name: review_status review_status_resource_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.review_status
    ADD CONSTRAINT review_status_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES public.resources(id) ON DELETE CASCADE;


--
-- Name: review_status review_status_reviewer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.review_status
    ADD CONSTRAINT review_status_reviewer_id_fkey FOREIGN KEY (reviewer_id) REFERENCES auth.users(id);


--
-- Name: review_status review_status_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.review_status
    ADD CONSTRAINT review_status_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.resource_versions(id) ON DELETE CASCADE;


--
-- Name: script_assets script_assets_script_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_assets
    ADD CONSTRAINT script_assets_script_id_fkey FOREIGN KEY (script_id) REFERENCES public.script_projects(id) ON DELETE CASCADE;


--
-- Name: script_beats script_beats_script_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_beats
    ADD CONSTRAINT script_beats_script_id_fkey FOREIGN KEY (script_id) REFERENCES public.script_projects(id) ON DELETE CASCADE;


--
-- Name: script_chapters script_chapters_parent_chapter_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_chapters
    ADD CONSTRAINT script_chapters_parent_chapter_id_fkey FOREIGN KEY (parent_chapter_id) REFERENCES public.script_chapters(id);


--
-- Name: script_chapters script_chapters_script_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_chapters
    ADD CONSTRAINT script_chapters_script_id_fkey FOREIGN KEY (script_id) REFERENCES public.script_projects(id) ON DELETE CASCADE;


--
-- Name: script_commits script_commits_script_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_commits
    ADD CONSTRAINT script_commits_script_id_fkey FOREIGN KEY (script_id) REFERENCES public.script_projects(id) ON DELETE CASCADE;


--
-- Name: script_ops script_ops_scene_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_ops
    ADD CONSTRAINT script_ops_scene_id_fkey FOREIGN KEY (scene_id) REFERENCES public.script_scenes(id) ON DELETE CASCADE;


--
-- Name: script_projects script_projects_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_projects
    ADD CONSTRAINT script_projects_created_by_fkey FOREIGN KEY (created_by) REFERENCES auth.users(id);


--
-- Name: script_projects script_projects_episode_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_projects
    ADD CONSTRAINT script_projects_episode_id_fkey FOREIGN KEY (episode_id) REFERENCES public.episodes(id) ON DELETE RESTRICT;


--
-- Name: script_projects script_projects_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_projects
    ADD CONSTRAINT script_projects_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id);


--
-- Name: script_projects script_projects_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_projects
    ADD CONSTRAINT script_projects_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id);


--
-- Name: script_scenes script_scenes_chapter_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_scenes
    ADD CONSTRAINT script_scenes_chapter_id_fkey FOREIGN KEY (chapter_id) REFERENCES public.script_chapters(id) ON DELETE SET NULL;


--
-- Name: script_scenes script_scenes_script_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_scenes
    ADD CONSTRAINT script_scenes_script_id_fkey FOREIGN KEY (script_id) REFERENCES public.script_projects(id) ON DELETE CASCADE;


--
-- Name: script_shots script_shots_scene_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_shots
    ADD CONSTRAINT script_shots_scene_id_fkey FOREIGN KEY (scene_id) REFERENCES public.script_scenes(id) ON DELETE CASCADE;


--
-- Name: script_storyboard_links script_storyboard_links_chapter_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_storyboard_links
    ADD CONSTRAINT script_storyboard_links_chapter_id_fkey FOREIGN KEY (chapter_id) REFERENCES public.script_chapters(id) ON DELETE CASCADE;


--
-- Name: script_storyboard_links script_storyboard_links_storyboard_node_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_storyboard_links
    ADD CONSTRAINT script_storyboard_links_storyboard_node_id_fkey FOREIGN KEY (storyboard_node_id) REFERENCES public.zzz_deprecated_storyboard_nodes(id) ON DELETE SET NULL;


--
-- Name: script_storyboard_links script_storyboard_links_storyboard_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.script_storyboard_links
    ADD CONSTRAINT script_storyboard_links_storyboard_project_id_fkey FOREIGN KEY (storyboard_project_id) REFERENCES public.zzz_deprecated_storyboard_projects(id) ON DELETE CASCADE;


--
-- Name: search_logs search_logs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.search_logs
    ADD CONSTRAINT search_logs_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: share_views share_views_share_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.share_views
    ADD CONSTRAINT share_views_share_id_fkey FOREIGN KEY (share_id) REFERENCES public.shares(id) ON DELETE CASCADE;


--
-- Name: share_views share_views_viewer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.share_views
    ADD CONSTRAINT share_views_viewer_id_fkey FOREIGN KEY (viewer_id) REFERENCES auth.users(id);


--
-- Name: shares shares_folder_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shares
    ADD CONSTRAINT shares_folder_id_fkey FOREIGN KEY (folder_id) REFERENCES public.folders(id) ON DELETE CASCADE;


--
-- Name: shares shares_library_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shares
    ADD CONSTRAINT shares_library_id_fkey FOREIGN KEY (library_id) REFERENCES public.libraries(id) ON DELETE CASCADE;


--
-- Name: shares shares_project_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shares
    ADD CONSTRAINT shares_project_file_id_fkey FOREIGN KEY (project_file_id) REFERENCES public.project_files(id) ON DELETE CASCADE;


--
-- Name: shares shares_resource_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shares
    ADD CONSTRAINT shares_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES public.resources(id) ON DELETE CASCADE;


--
-- Name: shares shares_shared_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shares
    ADD CONSTRAINT shares_shared_by_fkey FOREIGN KEY (shared_by) REFERENCES auth.users(id);


--
-- Name: shares shares_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shares
    ADD CONSTRAINT shares_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id);


--
-- Name: shares shares_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shares
    ADD CONSTRAINT shares_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.file_versions(id) ON DELETE SET NULL;


--
-- Name: signal_sources signal_sources_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_sources
    ADD CONSTRAINT signal_sources_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: skill_file_versions skill_file_versions_skill_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skill_file_versions
    ADD CONSTRAINT skill_file_versions_skill_file_id_fkey FOREIGN KEY (skill_file_id) REFERENCES public.skill_files(id) ON DELETE CASCADE;


--
-- Name: skill_files skill_files_skill_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skill_files
    ADD CONSTRAINT skill_files_skill_id_fkey FOREIGN KEY (skill_id) REFERENCES public.skills(id) ON DELETE CASCADE;


--
-- Name: skill_versions skill_versions_skill_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skill_versions
    ADD CONSTRAINT skill_versions_skill_id_fkey FOREIGN KEY (skill_id) REFERENCES public.skills(id) ON DELETE CASCADE;


--
-- Name: skills skills_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skills
    ADD CONSTRAINT skills_created_by_fkey FOREIGN KEY (created_by) REFERENCES auth.users(id);


--
-- Name: skills skills_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skills
    ADD CONSTRAINT skills_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id);


--
-- Name: skills skills_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skills
    ADD CONSTRAINT skills_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id);


--
-- Name: smart_collections smart_collections_scope_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.smart_collections
    ADD CONSTRAINT smart_collections_scope_id_fkey FOREIGN KEY (scope_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: smart_collections smart_collections_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.smart_collections
    ADD CONSTRAINT smart_collections_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: zzz_deprecated_storyboard_assets storyboard_assets_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_assets
    ADD CONSTRAINT storyboard_assets_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.zzz_deprecated_storyboard_projects(id) ON DELETE CASCADE;


--
-- Name: zzz_deprecated_storyboard_characters storyboard_characters_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_characters
    ADD CONSTRAINT storyboard_characters_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.zzz_deprecated_storyboard_projects(id) ON DELETE CASCADE;


--
-- Name: zzz_deprecated_storyboard_edges storyboard_edges_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_edges
    ADD CONSTRAINT storyboard_edges_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.zzz_deprecated_storyboard_projects(id) ON DELETE CASCADE;


--
-- Name: zzz_deprecated_storyboard_edges storyboard_edges_source_node_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_edges
    ADD CONSTRAINT storyboard_edges_source_node_id_fkey FOREIGN KEY (source_node_id) REFERENCES public.zzz_deprecated_storyboard_nodes(id) ON DELETE CASCADE;


--
-- Name: zzz_deprecated_storyboard_edges storyboard_edges_target_node_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_edges
    ADD CONSTRAINT storyboard_edges_target_node_id_fkey FOREIGN KEY (target_node_id) REFERENCES public.zzz_deprecated_storyboard_nodes(id) ON DELETE CASCADE;


--
-- Name: zzz_deprecated_storyboard_frame_characters storyboard_frame_characters_character_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_frame_characters
    ADD CONSTRAINT storyboard_frame_characters_character_id_fkey FOREIGN KEY (character_id) REFERENCES public.zzz_deprecated_storyboard_characters(id) ON DELETE CASCADE;


--
-- Name: zzz_deprecated_storyboard_frame_characters storyboard_frame_characters_frame_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_frame_characters
    ADD CONSTRAINT storyboard_frame_characters_frame_id_fkey FOREIGN KEY (frame_id) REFERENCES public.zzz_deprecated_storyboard_frames(id) ON DELETE CASCADE;


--
-- Name: zzz_deprecated_storyboard_frames storyboard_frames_node_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_frames
    ADD CONSTRAINT storyboard_frames_node_id_fkey FOREIGN KEY (node_id) REFERENCES public.zzz_deprecated_storyboard_nodes(id) ON DELETE CASCADE;


--
-- Name: zzz_deprecated_storyboard_frames storyboard_frames_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_frames
    ADD CONSTRAINT storyboard_frames_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.zzz_deprecated_storyboard_projects(id) ON DELETE CASCADE;


--
-- Name: zzz_deprecated_storyboard_nodes storyboard_nodes_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_nodes
    ADD CONSTRAINT storyboard_nodes_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.zzz_deprecated_storyboard_projects(id) ON DELETE CASCADE;


--
-- Name: zzz_deprecated_storyboard_projects storyboard_projects_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_projects
    ADD CONSTRAINT storyboard_projects_created_by_fkey FOREIGN KEY (created_by) REFERENCES auth.users(id) ON DELETE SET NULL;


--
-- Name: zzz_deprecated_storyboard_projects storyboard_projects_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_projects
    ADD CONSTRAINT storyboard_projects_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE SET NULL;


--
-- Name: zzz_deprecated_storyboard_projects storyboard_projects_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_projects
    ADD CONSTRAINT storyboard_projects_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: zzz_deprecated_storyboard_video_assets storyboard_video_assets_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_video_assets
    ADD CONSTRAINT storyboard_video_assets_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.zzz_deprecated_storyboard_projects(id) ON DELETE CASCADE;


--
-- Name: zzz_deprecated_storyboard_video_assets storyboard_video_assets_source_frame_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_video_assets
    ADD CONSTRAINT storyboard_video_assets_source_frame_id_fkey FOREIGN KEY (source_frame_id) REFERENCES public.zzz_deprecated_storyboard_frames(id) ON DELETE SET NULL;


--
-- Name: zzz_deprecated_storyboard_video_assets storyboard_video_assets_source_node_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zzz_deprecated_storyboard_video_assets
    ADD CONSTRAINT storyboard_video_assets_source_node_id_fkey FOREIGN KEY (source_node_id) REFERENCES public.zzz_deprecated_storyboard_nodes(id) ON DELETE SET NULL;


--
-- Name: style_templates style_templates_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.style_templates
    ADD CONSTRAINT style_templates_created_by_fkey FOREIGN KEY (created_by) REFERENCES auth.users(id);


--
-- Name: style_templates style_templates_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.style_templates
    ADD CONSTRAINT style_templates_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id);


--
-- Name: system_settings system_settings_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_settings
    ADD CONSTRAINT system_settings_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES auth.users(id);


--
-- Name: tags tags_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tags
    ADD CONSTRAINT tags_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.tag_groups(id) ON DELETE SET NULL;


--
-- Name: tags tags_scope_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tags
    ADD CONSTRAINT tags_scope_id_fkey FOREIGN KEY (scope_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: tags tags_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tags
    ADD CONSTRAINT tags_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: task_tracking task_tracking_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_tracking
    ADD CONSTRAINT task_tracking_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.ai_agents(id) ON DELETE SET NULL;


--
-- Name: task_tracking task_tracking_flow_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_tracking
    ADD CONSTRAINT task_tracking_flow_id_fkey FOREIGN KEY (flow_id) REFERENCES public.task_flows(id) ON DELETE SET NULL;


--
-- Name: task_tracking task_tracking_inbox_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_tracking
    ADD CONSTRAINT task_tracking_inbox_fk FOREIGN KEY (inbox_message_id) REFERENCES public.agent_inbox(id) ON DELETE SET NULL;


--
-- Name: task_tracking task_tracking_parent_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_tracking
    ADD CONSTRAINT task_tracking_parent_fk FOREIGN KEY (parent_task_id) REFERENCES public.task_tracking(dbos_workflow_id) ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED;


--
-- Name: task_tracking task_tracking_root_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_tracking
    ADD CONSTRAINT task_tracking_root_fk FOREIGN KEY (root_task_id) REFERENCES public.task_tracking(dbos_workflow_id) ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED;


--
-- Name: team_invites team_invites_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_invites
    ADD CONSTRAINT team_invites_created_by_fkey FOREIGN KEY (created_by) REFERENCES auth.users(id);


--
-- Name: team_invites team_invites_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_invites
    ADD CONSTRAINT team_invites_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: team_members team_members_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_members
    ADD CONSTRAINT team_members_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: team_members team_members_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_members
    ADD CONSTRAINT team_members_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: team_plans team_plans_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_plans
    ADD CONSTRAINT team_plans_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: team_quotas team_quotas_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_quotas
    ADD CONSTRAINT team_quotas_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: teams teams_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teams
    ADD CONSTRAINT teams_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES auth.users(id);


--
-- Name: temp_tokens temp_tokens_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.temp_tokens
    ADD CONSTRAINT temp_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: topic_groups topic_groups_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.topic_groups
    ADD CONSTRAINT topic_groups_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: task_tracking unified_tasks_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_tracking
    ADD CONSTRAINT unified_tasks_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id);


--
-- Name: user_cookies user_cookies_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_cookies
    ADD CONSTRAINT user_cookies_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: user_credits user_credits_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_credits
    ADD CONSTRAINT user_credits_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: user_hidden_sources user_hidden_sources_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_hidden_sources
    ADD CONSTRAINT user_hidden_sources_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.signal_sources(id) ON DELETE CASCADE;


--
-- Name: user_hidden_sources user_hidden_sources_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_hidden_sources
    ADD CONSTRAINT user_hidden_sources_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: user_logs user_logs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_logs
    ADD CONSTRAINT user_logs_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: user_mcp_servers user_mcp_servers_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_mcp_servers
    ADD CONSTRAINT user_mcp_servers_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: user_notifications user_notifications_notification_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_notifications
    ADD CONSTRAINT user_notifications_notification_id_fkey FOREIGN KEY (notification_id) REFERENCES public.notifications(id) ON DELETE CASCADE;


--
-- Name: user_notifications user_notifications_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_notifications
    ADD CONSTRAINT user_notifications_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: user_profiles user_profiles_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_profiles
    ADD CONSTRAINT user_profiles_id_fkey FOREIGN KEY (id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: user_settings user_settings_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_settings
    ADD CONSTRAINT user_settings_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: user_tag_preferences user_tag_preferences_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_tag_preferences
    ADD CONSTRAINT user_tag_preferences_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: user_topic_interests user_topic_interests_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_topic_interests
    ADD CONSTRAINT user_topic_interests_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: resource_access_logs video_access_logs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_access_logs
    ADD CONSTRAINT video_access_logs_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: workflow_nodes workflow_nodes_workflow_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_nodes
    ADD CONSTRAINT workflow_nodes_workflow_id_fkey FOREIGN KEY (workflow_id) REFERENCES public.project_workflows(id) ON DELETE CASCADE;


--
-- Name: parsed_media Admin can delete videos; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Admin can delete videos" ON public.parsed_media FOR DELETE USING ((EXISTS ( SELECT 1
   FROM public.user_profiles
  WHERE ((user_profiles.id = ( SELECT auth.uid() AS uid)) AND (user_profiles.role = 'admin'::public.user_role)))));


--
-- Name: audit_logs Admins can create audit logs; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Admins can create audit logs" ON public.audit_logs FOR INSERT WITH CHECK ((EXISTS ( SELECT 1
   FROM public.user_profiles
  WHERE ((user_profiles.id = ( SELECT auth.uid() AS uid)) AND (user_profiles.role = 'admin'::public.user_role)))));


--
-- Name: audit_logs Admins can view audit logs; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Admins can view audit logs" ON public.audit_logs FOR SELECT USING ((EXISTS ( SELECT 1
   FROM public.user_profiles
  WHERE ((user_profiles.id = ( SELECT auth.uid() AS uid)) AND (user_profiles.role = 'admin'::public.user_role)))));


--
-- Name: share_views Anyone can create share views; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Anyone can create share views" ON public.share_views FOR INSERT WITH CHECK (true);


--
-- Name: point_packages Anyone can read active packages; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Anyone can read active packages" ON public.point_packages FOR SELECT USING ((is_active = true));


--
-- Name: point_pricing Anyone can read active pricing; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Anyone can read active pricing" ON public.point_pricing FOR SELECT USING ((is_active = true));


--
-- Name: parsed_media Authenticated users can insert videos; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Authenticated users can insert videos" ON public.parsed_media FOR INSERT WITH CHECK ((( SELECT auth.role() AS role) = 'authenticated'::text));


--
-- Name: alert_history Authenticated users can read alert_history; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Authenticated users can read alert_history" ON public.alert_history FOR SELECT USING ((auth.role() = 'authenticated'::text));


--
-- Name: alert_rules Authenticated users can read alert_rules; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Authenticated users can read alert_rules" ON public.alert_rules FOR SELECT USING ((auth.role() = 'authenticated'::text));


--
-- Name: review_comments Authors can delete own comments; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Authors can delete own comments" ON public.review_comments FOR DELETE USING ((( SELECT auth.uid() AS uid) = author_id));


--
-- Name: review_comments Authors can update own comments; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Authors can update own comments" ON public.review_comments FOR UPDATE USING ((( SELECT auth.uid() AS uid) = author_id));


--
-- Name: smart_collections Create own collections; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Create own collections" ON public.smart_collections FOR INSERT WITH CHECK ((user_id = ( SELECT auth.uid() AS uid)));


--
-- Name: resources Creators can delete resources; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Creators can delete resources" ON public.resources FOR DELETE USING ((creator_id = ( SELECT auth.uid() AS uid)));


--
-- Name: resources Creators can update resources; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Creators can update resources" ON public.resources FOR UPDATE USING ((creator_id = ( SELECT auth.uid() AS uid))) WITH CHECK ((creator_id = ( SELECT auth.uid() AS uid)));


--
-- Name: smart_collections Delete own non-preset collections; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Delete own non-preset collections" ON public.smart_collections FOR DELETE USING (((user_id = ( SELECT auth.uid() AS uid)) AND (is_preset = false)));


--
-- Name: libraries Library creators can delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Library creators can delete" ON public.libraries FOR DELETE USING (((created_by = ( SELECT auth.uid() AS uid)) OR (scope_id IN ( SELECT public.get_user_team_ids_text(( SELECT auth.uid() AS uid)) AS get_user_team_ids_text))));


--
-- Name: libraries Library creators can update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Library creators can update" ON public.libraries FOR UPDATE USING (((created_by = ( SELECT auth.uid() AS uid)) OR (scope_id IN ( SELECT public.get_user_team_ids_text(( SELECT auth.uid() AS uid)) AS get_user_team_ids_text)))) WITH CHECK (((created_by = ( SELECT auth.uid() AS uid)) OR (scope_id IN ( SELECT public.get_user_team_ids_text(( SELECT auth.uid() AS uid)) AS get_user_team_ids_text))));


--
-- Name: resource_analysis Manage analysis of owned resources; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Manage analysis of owned resources" ON public.resource_analysis USING ((EXISTS ( SELECT 1
   FROM public.resources
  WHERE ((resources.id = resource_analysis.resource_id) AND (resources.creator_id = auth.uid())))));


--
-- Name: user_hidden_sources Manage own hidden sources; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Manage own hidden sources" ON public.user_hidden_sources USING ((auth.uid() = user_id)) WITH CHECK ((auth.uid() = user_id));


--
-- Name: hotspot_user_state Manage own hotspot state; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Manage own hotspot state" ON public.hotspot_user_state USING ((auth.uid() = user_id)) WITH CHECK ((auth.uid() = user_id));


--
-- Name: signal_sources Manage own sources; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Manage own sources" ON public.signal_sources USING ((auth.uid() = user_id)) WITH CHECK ((auth.uid() = user_id));


--
-- Name: user_topic_interests Manage own topic interest; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Manage own topic interest" ON public.user_topic_interests USING ((auth.uid() = user_id)) WITH CHECK ((auth.uid() = user_id));


--
-- Name: resource_summaries Manage summaries of owned resources; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Manage summaries of owned resources" ON public.resource_summaries USING ((EXISTS ( SELECT 1
   FROM public.resources
  WHERE ((resources.media_id = resource_summaries.resource_id) AND (resources.creator_id = ( SELECT auth.uid() AS uid))))));


--
-- Name: resource_transcripts Manage transcripts of owned resources; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Manage transcripts of owned resources" ON public.resource_transcripts USING ((EXISTS ( SELECT 1
   FROM public.resources
  WHERE ((resources.media_id = resource_transcripts.resource_id) AND (resources.creator_id = ( SELECT auth.uid() AS uid))))));


--
-- Name: team_members Members can view team members; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Members can view team members" ON public.team_members FOR SELECT USING ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: collections Owners can delete collections; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Owners can delete collections" ON public.collections FOR DELETE USING ((owner_id = ( SELECT auth.uid() AS uid)));


--
-- Name: projects Owners can delete projects; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Owners can delete projects" ON public.projects FOR DELETE USING ((owner_id = ( SELECT auth.uid() AS uid)));


--
-- Name: teams Owners can delete their teams; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Owners can delete their teams" ON public.teams FOR DELETE USING ((owner_id = ( SELECT auth.uid() AS uid)));


--
-- Name: projects Owners can update projects; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Owners can update projects" ON public.projects FOR UPDATE USING ((owner_id = ( SELECT auth.uid() AS uid))) WITH CHECK ((owner_id = ( SELECT auth.uid() AS uid)));


--
-- Name: teams Owners can update their teams; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Owners can update their teams" ON public.teams FOR UPDATE USING ((owner_id = ( SELECT auth.uid() AS uid)));


--
-- Name: hotspots Read global or own hotspots; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Read global or own hotspots" ON public.hotspots FOR SELECT USING (((user_id IS NULL) OR (auth.uid() = user_id)));


--
-- Name: signal_sources Read global or own sources; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Read global or own sources" ON public.signal_sources FOR SELECT USING (((user_id IS NULL) OR (auth.uid() = user_id)));


--
-- Name: topic_groups Read global or own topic_groups; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Read global or own topic_groups" ON public.topic_groups FOR SELECT USING (((user_id IS NULL) OR (auth.uid() = user_id)));


--
-- Name: review_status Reviewers can update own status; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Reviewers can update own status" ON public.review_status FOR UPDATE USING ((( SELECT auth.uid() AS uid) = reviewer_id));


--
-- Name: alert_history Service role full access on alert_history; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on alert_history" ON public.alert_history USING ((auth.role() = 'service_role'::text));


--
-- Name: alert_rules Service role full access on alert_rules; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on alert_rules" ON public.alert_rules USING ((auth.role() = 'service_role'::text));


--
-- Name: distribution_oauth_states Service role full access on distribution_oauth_states; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on distribution_oauth_states" ON public.distribution_oauth_states USING ((auth.role() = 'service_role'::text)) WITH CHECK ((auth.role() = 'service_role'::text));


--
-- Name: episodes Service role full access on episodes; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on episodes" ON public.episodes USING ((auth.role() = 'service_role'::text)) WITH CHECK ((auth.role() = 'service_role'::text));


--
-- Name: hotspot_user_state Service role full access on hotspot_user_state; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on hotspot_user_state" ON public.hotspot_user_state USING (((auth.jwt() ->> 'role'::text) = 'service_role'::text));


--
-- Name: hotspots Service role full access on hotspots; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on hotspots" ON public.hotspots USING (((auth.jwt() ->> 'role'::text) = 'service_role'::text));


--
-- Name: project_characters Service role full access on project_characters; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on project_characters" ON public.project_characters USING ((auth.role() = 'service_role'::text)) WITH CHECK ((auth.role() = 'service_role'::text));


--
-- Name: project_file_comments Service role full access on project_file_comments; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on project_file_comments" ON public.project_file_comments USING ((auth.role() = 'service_role'::text)) WITH CHECK ((auth.role() = 'service_role'::text));


--
-- Name: project_lib_entities Service role full access on project_lib_entities; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on project_lib_entities" ON public.project_lib_entities USING ((auth.role() = 'service_role'::text)) WITH CHECK ((auth.role() = 'service_role'::text));


--
-- Name: publish_task_accounts Service role full access on publish_task_accounts; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on publish_task_accounts" ON public.publish_task_accounts USING ((auth.role() = 'service_role'::text)) WITH CHECK ((auth.role() = 'service_role'::text));


--
-- Name: publish_tasks Service role full access on publish_tasks; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on publish_tasks" ON public.publish_tasks USING ((auth.role() = 'service_role'::text)) WITH CHECK ((auth.role() = 'service_role'::text));


--
-- Name: script_beats Service role full access on script_beats; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on script_beats" ON public.script_beats USING ((auth.role() = 'service_role'::text)) WITH CHECK ((auth.role() = 'service_role'::text));


--
-- Name: script_commits Service role full access on script_commits; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on script_commits" ON public.script_commits USING ((auth.role() = 'service_role'::text)) WITH CHECK ((auth.role() = 'service_role'::text));


--
-- Name: script_ops Service role full access on script_ops; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on script_ops" ON public.script_ops USING ((auth.role() = 'service_role'::text)) WITH CHECK ((auth.role() = 'service_role'::text));


--
-- Name: script_scenes Service role full access on script_scenes; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on script_scenes" ON public.script_scenes USING ((auth.role() = 'service_role'::text)) WITH CHECK ((auth.role() = 'service_role'::text));


--
-- Name: script_shots Service role full access on script_shots; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on script_shots" ON public.script_shots USING ((auth.role() = 'service_role'::text)) WITH CHECK ((auth.role() = 'service_role'::text));


--
-- Name: signal_sources Service role full access on signal_sources; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on signal_sources" ON public.signal_sources USING (((auth.jwt() ->> 'role'::text) = 'service_role'::text));


--
-- Name: social_accounts Service role full access on social_accounts; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on social_accounts" ON public.social_accounts USING ((auth.role() = 'service_role'::text)) WITH CHECK ((auth.role() = 'service_role'::text));


--
-- Name: topic_groups Service role full access on topic_groups; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on topic_groups" ON public.topic_groups USING (((auth.jwt() ->> 'role'::text) = 'service_role'::text));


--
-- Name: user_hidden_sources Service role full access on user_hidden_sources; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on user_hidden_sources" ON public.user_hidden_sources USING (((auth.jwt() ->> 'role'::text) = 'service_role'::text));


--
-- Name: user_topic_interests Service role full access on user_topic_interests; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Service role full access on user_topic_interests" ON public.user_topic_interests USING (((auth.jwt() ->> 'role'::text) = 'service_role'::text));


--
-- Name: tag_groups Tag groups visible to all authenticated; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Tag groups visible to all authenticated" ON public.tag_groups FOR SELECT TO authenticated USING (true);


--
-- Name: libraries Team members can create libraries; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Team members can create libraries" ON public.libraries FOR INSERT WITH CHECK (((created_by = ( SELECT auth.uid() AS uid)) AND (scope_id IN ( SELECT public.get_user_team_ids_text(( SELECT auth.uid() AS uid)) AS get_user_team_ids_text))));


--
-- Name: project_workflows Team members can create workflows; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Team members can create workflows" ON public.project_workflows FOR INSERT WITH CHECK ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: project_workflows Team members can delete workflows; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Team members can delete workflows" ON public.project_workflows FOR DELETE USING ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: libraries Team members can read libraries; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Team members can read libraries" ON public.libraries FOR SELECT USING ((scope_id IN ( SELECT public.get_user_team_ids_text(( SELECT auth.uid() AS uid)) AS get_user_team_ids_text)));


--
-- Name: team_plans Team members can read team plan; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Team members can read team plan" ON public.team_plans FOR SELECT USING ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: team_quotas Team members can read team quota; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Team members can read team quota" ON public.team_quotas FOR SELECT USING ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: point_transactions Team members can read team transactions; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Team members can read team transactions" ON public.point_transactions FOR SELECT USING ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: project_workflows Team members can read workflows; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Team members can read workflows" ON public.project_workflows FOR SELECT USING ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: project_workflows Team members can update workflows; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Team members can update workflows" ON public.project_workflows FOR UPDATE USING ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))) WITH CHECK ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: team_invites Team members can view invites; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Team members can view invites" ON public.team_invites FOR SELECT USING ((team_id IN ( SELECT team_members.team_id
   FROM public.team_members
  WHERE (team_members.user_id = ( SELECT auth.uid() AS uid)))));


--
-- Name: team_invites Team owners can create invites; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Team owners can create invites" ON public.team_invites FOR INSERT WITH CHECK ((team_id IN ( SELECT teams.id
   FROM public.teams
  WHERE (teams.owner_id = ( SELECT auth.uid() AS uid)))));


--
-- Name: notifications Team owners can create team notifications; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Team owners can create team notifications" ON public.notifications FOR INSERT WITH CHECK ((((type)::text = 'team'::text) AND (team_id IN ( SELECT teams.id
   FROM public.teams
  WHERE (teams.owner_id = ( SELECT auth.uid() AS uid))))));


--
-- Name: team_invites Team owners can delete invites; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Team owners can delete invites" ON public.team_invites FOR DELETE USING ((team_id IN ( SELECT teams.id
   FROM public.teams
  WHERE (teams.owner_id = ( SELECT auth.uid() AS uid)))));


--
-- Name: team_invites Team owners can update invites; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Team owners can update invites" ON public.team_invites FOR UPDATE USING ((team_id IN ( SELECT teams.id
   FROM public.teams
  WHERE (teams.owner_id = ( SELECT auth.uid() AS uid)))));


--
-- Name: smart_collections Update own collections; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Update own collections" ON public.smart_collections FOR UPDATE USING ((user_id = ( SELECT auth.uid() AS uid)));


--
-- Name: parsed_media Update parsed_media of owned resources; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Update parsed_media of owned resources" ON public.parsed_media FOR UPDATE USING ((EXISTS ( SELECT 1
   FROM public.resources r
  WHERE ((r.media_id = parsed_media.id) AND (r.creator_id = ( SELECT auth.uid() AS uid))))));


--
-- Name: team_members Users can add themselves or owners can add; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can add themselves or owners can add" ON public.team_members FOR INSERT WITH CHECK (((user_id = ( SELECT auth.uid() AS uid)) OR (team_id IN ( SELECT teams.id
   FROM public.teams
  WHERE (teams.owner_id = ( SELECT auth.uid() AS uid))))));


--
-- Name: collections Users can create collections; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can create collections" ON public.collections FOR INSERT WITH CHECK ((owner_id = ( SELECT auth.uid() AS uid)));


--
-- Name: project_collections Users can create collections; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can create collections" ON public.project_collections FOR INSERT WITH CHECK ((created_by = ( SELECT auth.uid() AS uid)));


--
-- Name: file_versions Users can create file versions; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can create file versions" ON public.file_versions FOR INSERT WITH CHECK ((file_id IN ( SELECT pf.id
   FROM public.project_files pf
  WHERE (pf.project_id IN ( SELECT projects.id
           FROM public.projects)))));


--
-- Name: folders Users can create folders; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can create folders" ON public.folders FOR INSERT WITH CHECK (((created_by = ( SELECT auth.uid() AS uid)) AND (scope_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))));


--
-- Name: search_logs Users can create own search logs; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can create own search logs" ON public.search_logs FOR INSERT WITH CHECK ((user_id = ( SELECT auth.uid() AS uid)));


--
-- Name: tags Users can create own tags; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can create own tags" ON public.tags FOR INSERT WITH CHECK (((user_id = ( SELECT auth.uid() AS uid)) AND ((type)::text = 'user'::text)));


--
-- Name: project_files Users can create project files; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can create project files" ON public.project_files FOR INSERT WITH CHECK ((project_id IN ( SELECT projects.id
   FROM public.projects)));


--
-- Name: project_tasks Users can create project tasks; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can create project tasks" ON public.project_tasks FOR INSERT WITH CHECK (((created_by = ( SELECT auth.uid() AS uid)) AND (project_id IN ( SELECT projects.id
   FROM public.projects))));


--
-- Name: projects Users can create projects; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can create projects" ON public.projects FOR INSERT WITH CHECK ((owner_id = ( SELECT auth.uid() AS uid)));


--
-- Name: resource_items Users can create resource items; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can create resource items" ON public.resource_items FOR INSERT WITH CHECK (((added_by = ( SELECT auth.uid() AS uid)) AND (scope_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))));


--
-- Name: resource_tags Users can create resource tags; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can create resource tags" ON public.resource_tags FOR INSERT WITH CHECK (((tagged_by = ( SELECT auth.uid() AS uid)) AND (resource_id IN ( SELECT resources.id
   FROM public.resources))));


--
-- Name: resource_versions Users can create resource versions; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can create resource versions" ON public.resource_versions FOR INSERT WITH CHECK (((uploaded_by = ( SELECT auth.uid() AS uid)) AND (resource_id IN ( SELECT resources.id
   FROM public.resources))));


--
-- Name: resources Users can create resources; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can create resources" ON public.resources FOR INSERT WITH CHECK ((creator_id = ( SELECT auth.uid() AS uid)));


--
-- Name: shares Users can create shares; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can create shares" ON public.shares FOR INSERT WITH CHECK ((shared_by = ( SELECT auth.uid() AS uid)));


--
-- Name: teams Users can create teams; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can create teams" ON public.teams FOR INSERT WITH CHECK ((owner_id = ( SELECT auth.uid() AS uid)));


--
-- Name: file_versions Users can delete file versions; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can delete file versions" ON public.file_versions FOR DELETE USING ((file_id IN ( SELECT pf.id
   FROM public.project_files pf
  WHERE (pf.project_id IN ( SELECT projects.id
           FROM public.projects)))));


--
-- Name: review_annotations Users can delete own annotations; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can delete own annotations" ON public.review_annotations FOR DELETE USING ((EXISTS ( SELECT 1
   FROM public.review_comments rc
  WHERE ((rc.id = review_annotations.comment_id) AND (rc.author_id = ( SELECT auth.uid() AS uid))))));


--
-- Name: project_collections Users can delete own collections; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can delete own collections" ON public.project_collections FOR DELETE USING ((created_by = ( SELECT auth.uid() AS uid)));


--
-- Name: resource_tags Users can delete own resource tags; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can delete own resource tags" ON public.resource_tags FOR DELETE USING ((tagged_by = ( SELECT auth.uid() AS uid)));


--
-- Name: folders Users can delete own scope folders; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can delete own scope folders" ON public.folders FOR DELETE USING ((scope_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: user_settings Users can delete own settings; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can delete own settings" ON public.user_settings FOR DELETE USING ((( SELECT auth.uid() AS uid) = user_id));


--
-- Name: shares Users can delete own shares; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can delete own shares" ON public.shares FOR DELETE USING ((shared_by = ( SELECT auth.uid() AS uid)));


--
-- Name: tags Users can delete own tags; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can delete own tags" ON public.tags FOR DELETE USING (((user_id = ( SELECT auth.uid() AS uid)) AND ((type)::text = 'user'::text)));


--
-- Name: project_files Users can delete project files; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can delete project files" ON public.project_files FOR DELETE USING ((project_id IN ( SELECT projects.id
   FROM public.projects)));


--
-- Name: project_tasks Users can delete project tasks; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can delete project tasks" ON public.project_tasks FOR DELETE USING ((project_id IN ( SELECT projects.id
   FROM public.projects)));


--
-- Name: resource_items Users can delete resource items in scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can delete resource items in scope" ON public.resource_items FOR DELETE USING ((scope_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: workflow_nodes Users can delete workflow nodes; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can delete workflow nodes" ON public.workflow_nodes FOR DELETE USING ((workflow_id IN ( SELECT project_workflows.id
   FROM public.project_workflows)));


--
-- Name: review_annotations Users can insert annotations; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can insert annotations" ON public.review_annotations FOR INSERT WITH CHECK ((( SELECT auth.uid() AS uid) IS NOT NULL));


--
-- Name: user_logs Users can insert own logs; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can insert own logs" ON public.user_logs FOR INSERT WITH CHECK ((( SELECT auth.uid() AS uid) = user_id));


--
-- Name: user_settings Users can insert own settings; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can insert own settings" ON public.user_settings FOR INSERT WITH CHECK ((( SELECT auth.uid() AS uid) = user_id));


--
-- Name: review_comments Users can insert review comments; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can insert review comments" ON public.review_comments FOR INSERT WITH CHECK ((( SELECT auth.uid() AS uid) = author_id));


--
-- Name: review_status Users can insert review status; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can insert review status" ON public.review_status FOR INSERT WITH CHECK ((( SELECT auth.uid() AS uid) = reviewer_id));


--
-- Name: team_members Users can leave or owners can remove; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can leave or owners can remove" ON public.team_members FOR DELETE USING (((user_id = ( SELECT auth.uid() AS uid)) OR (team_id IN ( SELECT teams.id
   FROM public.teams
  WHERE (teams.owner_id = ( SELECT auth.uid() AS uid))))));


--
-- Name: user_cookies Users can manage own cookies; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can manage own cookies" ON public.user_cookies USING ((( SELECT auth.uid() AS uid) = user_id)) WITH CHECK ((( SELECT auth.uid() AS uid) = user_id));


--
-- Name: user_tag_preferences Users can manage own tag preferences; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can manage own tag preferences" ON public.user_tag_preferences USING ((( SELECT auth.uid() AS uid) = user_id)) WITH CHECK ((( SELECT auth.uid() AS uid) = user_id));


--
-- Name: project_folders Users can manage project folders; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can manage project folders" ON public.project_folders USING ((project_id IN ( SELECT projects.id
   FROM public.projects))) WITH CHECK ((project_id IN ( SELECT projects.id
   FROM public.projects)));


--
-- Name: workflow_nodes Users can manage workflow nodes; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can manage workflow nodes" ON public.workflow_nodes FOR INSERT WITH CHECK ((workflow_id IN ( SELECT project_workflows.id
   FROM public.project_workflows)));


--
-- Name: review_annotations Users can read annotations; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can read annotations" ON public.review_annotations FOR SELECT USING ((( SELECT auth.uid() AS uid) IS NOT NULL));


--
-- Name: file_versions Users can read file versions; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can read file versions" ON public.file_versions FOR SELECT USING ((file_id IN ( SELECT pf.id
   FROM public.project_files pf
  WHERE (pf.project_id IN ( SELECT projects.id
           FROM public.projects)))));


--
-- Name: folders Users can read own folders; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can read own folders" ON public.folders FOR SELECT USING ((scope_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: access_overrides Users can read own overrides; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can read own overrides" ON public.access_overrides FOR SELECT USING (((user_id = ( SELECT auth.uid() AS uid)) OR (granted_by = ( SELECT auth.uid() AS uid))));


--
-- Name: projects Users can read own projects; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can read own projects" ON public.projects FOR SELECT USING (((owner_id = ( SELECT auth.uid() AS uid)) OR (team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))));


--
-- Name: resources Users can read own resources; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can read own resources" ON public.resources FOR SELECT USING (((creator_id = ( SELECT auth.uid() AS uid)) OR (id IN ( SELECT resource_items.resource_id
   FROM public.resource_items
  WHERE (resource_items.scope_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))))));


--
-- Name: shares Users can read own shares; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can read own shares" ON public.shares FOR SELECT USING (((shared_by = ( SELECT auth.uid() AS uid)) OR ((status)::text = 'active'::text)));


--
-- Name: project_collections Users can read project collections; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can read project collections" ON public.project_collections FOR SELECT USING (((created_by = ( SELECT auth.uid() AS uid)) OR (is_active = true)));


--
-- Name: project_files Users can read project files; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can read project files" ON public.project_files FOR SELECT USING ((project_id IN ( SELECT projects.id
   FROM public.projects)));


--
-- Name: project_tasks Users can read project tasks; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can read project tasks" ON public.project_tasks FOR SELECT USING ((project_id IN ( SELECT projects.id
   FROM public.projects)));


--
-- Name: resource_items Users can read resource items in scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can read resource items in scope" ON public.resource_items FOR SELECT USING ((scope_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: resource_tags Users can read resource tags; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can read resource tags" ON public.resource_tags FOR SELECT USING ((resource_id IN ( SELECT resources.id
   FROM public.resources)));


--
-- Name: resource_versions Users can read resource versions; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can read resource versions" ON public.resource_versions FOR SELECT USING ((resource_id IN ( SELECT resources.id
   FROM public.resources)));


--
-- Name: review_comments Users can read review comments; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can read review comments" ON public.review_comments FOR SELECT USING ((( SELECT auth.uid() AS uid) IS NOT NULL));


--
-- Name: review_status Users can read review status; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can read review status" ON public.review_status FOR SELECT USING ((( SELECT auth.uid() AS uid) IS NOT NULL));


--
-- Name: share_views Users can read share views; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can read share views" ON public.share_views FOR SELECT USING ((share_id IN ( SELECT shares.id
   FROM public.shares)));


--
-- Name: workflow_nodes Users can read workflow nodes; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can read workflow nodes" ON public.workflow_nodes FOR SELECT USING ((workflow_id IN ( SELECT project_workflows.id
   FROM public.project_workflows)));


--
-- Name: collections Users can update collections; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can update collections" ON public.collections FOR UPDATE USING (((owner_id = ( SELECT auth.uid() AS uid)) OR (team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))));


--
-- Name: file_versions Users can update file versions; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can update file versions" ON public.file_versions FOR UPDATE USING ((file_id IN ( SELECT pf.id
   FROM public.project_files pf
  WHERE (pf.project_id IN ( SELECT projects.id
           FROM public.projects))))) WITH CHECK ((file_id IN ( SELECT pf.id
   FROM public.project_files pf
  WHERE (pf.project_id IN ( SELECT projects.id
           FROM public.projects)))));


--
-- Name: project_collections Users can update own collections; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can update own collections" ON public.project_collections FOR UPDATE USING ((created_by = ( SELECT auth.uid() AS uid))) WITH CHECK ((created_by = ( SELECT auth.uid() AS uid)));


--
-- Name: folders Users can update own scope folders; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can update own scope folders" ON public.folders FOR UPDATE USING ((scope_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))) WITH CHECK ((scope_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: user_settings Users can update own settings; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can update own settings" ON public.user_settings FOR UPDATE USING ((( SELECT auth.uid() AS uid) = user_id)) WITH CHECK ((( SELECT auth.uid() AS uid) = user_id));


--
-- Name: shares Users can update own shares; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can update own shares" ON public.shares FOR UPDATE USING ((shared_by = ( SELECT auth.uid() AS uid))) WITH CHECK ((shared_by = ( SELECT auth.uid() AS uid)));


--
-- Name: tags Users can update own tags; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can update own tags" ON public.tags FOR UPDATE USING (((user_id = ( SELECT auth.uid() AS uid)) AND ((type)::text = 'user'::text)));


--
-- Name: project_files Users can update project files; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can update project files" ON public.project_files FOR UPDATE USING ((project_id IN ( SELECT projects.id
   FROM public.projects))) WITH CHECK ((project_id IN ( SELECT projects.id
   FROM public.projects)));


--
-- Name: project_tasks Users can update project tasks; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can update project tasks" ON public.project_tasks FOR UPDATE USING ((project_id IN ( SELECT projects.id
   FROM public.projects))) WITH CHECK ((project_id IN ( SELECT projects.id
   FROM public.projects)));


--
-- Name: resource_items Users can update resource items in scope; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can update resource items in scope" ON public.resource_items FOR UPDATE USING ((scope_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))) WITH CHECK ((scope_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: user_notifications Users can update their notification status; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can update their notification status" ON public.user_notifications USING ((user_id = ( SELECT auth.uid() AS uid)));


--
-- Name: workflow_nodes Users can update workflow nodes; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can update workflow nodes" ON public.workflow_nodes FOR UPDATE USING ((workflow_id IN ( SELECT project_workflows.id
   FROM public.project_workflows))) WITH CHECK ((workflow_id IN ( SELECT project_workflows.id
   FROM public.project_workflows)));


--
-- Name: user_logs Users can view own logs; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can view own logs" ON public.user_logs FOR SELECT USING ((( SELECT auth.uid() AS uid) = user_id));


--
-- Name: search_logs Users can view own search logs; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can view own search logs" ON public.search_logs FOR SELECT USING ((user_id = ( SELECT auth.uid() AS uid)));


--
-- Name: user_settings Users can view own settings; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can view own settings" ON public.user_settings FOR SELECT USING ((( SELECT auth.uid() AS uid) = user_id));


--
-- Name: notifications Users can view relevant notifications; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can view relevant notifications" ON public.notifications FOR SELECT USING ((((type)::text = 'system'::text) OR (team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))));


--
-- Name: teams Users can view teams they belong to; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can view teams they belong to" ON public.teams FOR SELECT USING (((owner_id = ( SELECT auth.uid() AS uid)) OR (id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))));


--
-- Name: collections Users can view their collections; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Users can view their collections" ON public.collections FOR SELECT USING (((owner_id = ( SELECT auth.uid() AS uid)) OR (team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))));


--
-- Name: smart_collections View own collections; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "View own collections" ON public.smart_collections FOR SELECT USING ((user_id = ( SELECT auth.uid() AS uid)));


--
-- Name: parsed_media View parsed_media of owned resources; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "View parsed_media of owned resources" ON public.parsed_media FOR SELECT USING ((EXISTS ( SELECT 1
   FROM public.resources r
  WHERE ((r.media_id = parsed_media.id) AND (r.creator_id = ( SELECT auth.uid() AS uid))))));


--
-- Name: share_views Viewers can update own share views; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "Viewers can update own share views" ON public.share_views FOR UPDATE USING ((viewer_id = ( SELECT auth.uid() AS uid))) WITH CHECK ((viewer_id = ( SELECT auth.uid() AS uid)));


--
-- Name: access_overrides; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.access_overrides ENABLE ROW LEVEL SECURITY;

--
-- Name: admin_table_preferences; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.admin_table_preferences ENABLE ROW LEVEL SECURITY;

--
-- Name: admin_table_preferences admin_table_preferences_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY admin_table_preferences_delete ON public.admin_table_preferences FOR DELETE USING ((( SELECT auth.uid() AS uid) = user_id));


--
-- Name: admin_table_preferences admin_table_preferences_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY admin_table_preferences_insert ON public.admin_table_preferences FOR INSERT WITH CHECK ((( SELECT auth.uid() AS uid) = user_id));


--
-- Name: admin_table_preferences admin_table_preferences_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY admin_table_preferences_select ON public.admin_table_preferences FOR SELECT USING ((( SELECT auth.uid() AS uid) = user_id));


--
-- Name: admin_table_preferences admin_table_preferences_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY admin_table_preferences_update ON public.admin_table_preferences FOR UPDATE USING ((( SELECT auth.uid() AS uid) = user_id));


--
-- Name: agent_approval_requests; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.agent_approval_requests ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_commitments; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.agent_commitments ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_inbox; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.agent_inbox ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_memory; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.agent_memory ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_memory_promotions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.agent_memory_promotions ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_memory_promotions agent_memory_promotions_service_only; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY agent_memory_promotions_service_only ON public.agent_memory_promotions TO service_role USING (true) WITH CHECK (true);


--
-- Name: agent_memory agent_memory_readable; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY agent_memory_readable ON public.agent_memory FOR SELECT USING (((owner_user_id = ( SELECT auth.uid() AS uid)) OR ((visibility = 'shared'::text) AND (team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: agent_memory agent_memory_write_service_only; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY agent_memory_write_service_only ON public.agent_memory TO service_role USING (true) WITH CHECK (true);


--
-- Name: agent_outbox; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.agent_outbox ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_overrides; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.agent_overrides ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_overrides agent_overrides_team_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY agent_overrides_team_read ON public.agent_overrides FOR SELECT USING ((team_id IN ( SELECT tm.team_id
   FROM public.team_members tm
  WHERE (tm.user_id = auth.uid()))));


--
-- Name: agent_overrides agent_overrides_team_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY agent_overrides_team_write ON public.agent_overrides USING ((team_id IN ( SELECT tm.team_id
   FROM public.team_members tm
  WHERE ((tm.user_id = auth.uid()) AND ((tm.role)::text = ANY ((ARRAY['owner'::character varying, 'admin'::character varying])::text[]))))));


--
-- Name: agent_overrides agent_overrides_user_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY agent_overrides_user_all ON public.agent_overrides USING ((user_id = auth.uid()));


--
-- Name: agent_run_events; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.agent_run_events ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_run_events agent_run_events_select_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY agent_run_events_select_own ON public.agent_run_events FOR SELECT USING ((EXISTS ( SELECT 1
   FROM public.agent_runs r
  WHERE ((r.id = agent_run_events.run_id) AND (r.user_id = auth.uid())))));


--
-- Name: agent_runs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.agent_runs ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_skills; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.agent_skills ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_skills agent_skills_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY agent_skills_delete ON public.agent_skills FOR DELETE USING ((agent_id IN ( SELECT ai_agents.id
   FROM public.ai_agents
  WHERE ((ai_agents.is_system_preset = false) AND ((ai_agents.user_id = auth.uid()) OR (ai_agents.created_by = auth.uid()))))));


--
-- Name: agent_skills agent_skills_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY agent_skills_insert ON public.agent_skills FOR INSERT WITH CHECK ((agent_id IN ( SELECT ai_agents.id
   FROM public.ai_agents
  WHERE ((ai_agents.is_system_preset = false) AND ((ai_agents.user_id = auth.uid()) OR (ai_agents.created_by = auth.uid()))))));


--
-- Name: agent_skills agent_skills_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY agent_skills_read ON public.agent_skills FOR SELECT USING ((agent_id IN ( SELECT ai_agents.id
   FROM public.ai_agents
  WHERE ((ai_agents.is_system_preset = true) OR (ai_agents.user_id = auth.uid()) OR (ai_agents.created_by = auth.uid()) OR (ai_agents.team_id IN ( SELECT team_members.team_id
           FROM public.team_members
          WHERE (team_members.user_id = auth.uid()))) OR (ai_agents.project_id IN ( SELECT projects.id
           FROM public.projects
          WHERE (projects.owner_id = auth.uid())))))));


--
-- Name: agent_state_history; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.agent_state_history ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_tasks; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.agent_tasks ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_workers; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.agent_workers ENABLE ROW LEVEL SECURITY;

--
-- Name: ai_agent_versions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.ai_agent_versions ENABLE ROW LEVEL SECURITY;

--
-- Name: ai_agents; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.ai_agents ENABLE ROW LEVEL SECURITY;

--
-- Name: ai_agents ai_agents_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ai_agents_delete ON public.ai_agents FOR DELETE USING (((is_system_preset = false) AND ((user_id = auth.uid()) OR (created_by = auth.uid()))));


--
-- Name: ai_agents ai_agents_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ai_agents_insert ON public.ai_agents FOR INSERT WITH CHECK (((is_system_preset = false) AND ((user_id = auth.uid()) OR (created_by = auth.uid()))));


--
-- Name: ai_agents ai_agents_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ai_agents_read ON public.ai_agents FOR SELECT USING (((is_system_preset = true) OR (user_id = auth.uid()) OR (created_by = auth.uid()) OR (team_id IN ( SELECT team_members.team_id
   FROM public.team_members
  WHERE (team_members.user_id = auth.uid()))) OR (project_id IN ( SELECT projects.id
   FROM public.projects
  WHERE (projects.owner_id = auth.uid())))));


--
-- Name: ai_agents ai_agents_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ai_agents_update ON public.ai_agents FOR UPDATE USING (((is_system_preset = false) AND ((user_id = auth.uid()) OR (created_by = auth.uid())))) WITH CHECK (((is_system_preset = false) AND ((user_id = auth.uid()) OR (created_by = auth.uid()))));


--
-- Name: ai_model_prices; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.ai_model_prices ENABLE ROW LEVEL SECURITY;

--
-- Name: ai_session_memory; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.ai_session_memory ENABLE ROW LEVEL SECURITY;

--
-- Name: ai_usage_logs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.ai_usage_logs ENABLE ROW LEVEL SECURITY;

--
-- Name: ai_usage_logs ai_usage_logs_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ai_usage_logs_read ON public.ai_usage_logs FOR SELECT USING (((user_id = auth.uid()) OR (team_id IN ( SELECT team_members.team_id
   FROM public.team_members
  WHERE ((team_members.user_id = auth.uid()) AND ((team_members.role)::text = ANY (ARRAY[('owner'::character varying)::text, ('admin'::character varying)::text])))))));


--
-- Name: alert_history; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.alert_history ENABLE ROW LEVEL SECURITY;

--
-- Name: alert_rules; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.alert_rules ENABLE ROW LEVEL SECURITY;

--
-- Name: frontend_error_logs anon_insert_frontend_error_logs; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY anon_insert_frontend_error_logs ON public.frontend_error_logs FOR INSERT TO anon WITH CHECK (true);


--
-- Name: api_key_logs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.api_key_logs ENABLE ROW LEVEL SECURITY;

--
-- Name: api_keys; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.api_keys ENABLE ROW LEVEL SECURITY;

--
-- Name: api_request_logs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.api_request_logs ENABLE ROW LEVEL SECURITY;

--
-- Name: application_logs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.application_logs ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_approval_requests approval_requests_owner_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY approval_requests_owner_all ON public.agent_approval_requests USING ((user_id = auth.uid())) WITH CHECK ((user_id = auth.uid()));


--
-- Name: audit_logs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.audit_logs ENABLE ROW LEVEL SECURITY;

--
-- Name: cost_audit_log audit_service_full; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY audit_service_full ON public.cost_audit_log TO service_role USING (true) WITH CHECK (true);


--
-- Name: system_status auth_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY auth_select ON public.system_status FOR SELECT USING ((( SELECT auth.role() AS role) = 'authenticated'::text));


--
-- Name: frontend_error_logs authenticated_insert_frontend_error_logs; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY authenticated_insert_frontend_error_logs ON public.frontend_error_logs FOR INSERT TO authenticated WITH CHECK (true);


--
-- Name: authors; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.authors ENABLE ROW LEVEL SECURITY;

--
-- Name: boundary_audit; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.boundary_audit ENABLE ROW LEVEL SECURITY;

--
-- Name: provider_byok_keys byok_service_full; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY byok_service_full ON public.provider_byok_keys TO service_role USING (true) WITH CHECK (true);


--
-- Name: canvas_resource_refs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.canvas_resource_refs ENABLE ROW LEVEL SECURITY;

--
-- Name: canvases; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.canvases ENABLE ROW LEVEL SECURITY;

--
-- Name: canvases canvases_member_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY canvases_member_delete ON public.canvases FOR DELETE USING ((EXISTS ( SELECT 1
   FROM public.project_members pm
  WHERE ((pm.project_id = canvases.project_id) AND (pm.user_id = ( SELECT auth.uid() AS uid))))));


--
-- Name: canvases canvases_member_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY canvases_member_insert ON public.canvases FOR INSERT WITH CHECK ((EXISTS ( SELECT 1
   FROM public.project_members pm
  WHERE ((pm.project_id = canvases.project_id) AND (pm.user_id = ( SELECT auth.uid() AS uid))))));


--
-- Name: canvases canvases_member_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY canvases_member_select ON public.canvases FOR SELECT USING ((EXISTS ( SELECT 1
   FROM public.project_members pm
  WHERE ((pm.project_id = canvases.project_id) AND (pm.user_id = ( SELECT auth.uid() AS uid))))));


--
-- Name: canvases canvases_member_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY canvases_member_update ON public.canvases FOR UPDATE USING ((EXISTS ( SELECT 1
   FROM public.project_members pm
  WHERE ((pm.project_id = canvases.project_id) AND (pm.user_id = ( SELECT auth.uid() AS uid)))))) WITH CHECK ((EXISTS ( SELECT 1
   FROM public.project_members pm
  WHERE ((pm.project_id = canvases.project_id) AND (pm.user_id = ( SELECT auth.uid() AS uid))))));


--
-- Name: collections; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.collections ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_commitments commitments_write_service_only; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY commitments_write_service_only ON public.agent_commitments TO service_role USING (true) WITH CHECK (true);


--
-- Name: provider_contracts contracts_service_full; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY contracts_service_full ON public.provider_contracts TO service_role USING (true) WITH CHECK (true);


--
-- Name: conversation_ai_meta; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.conversation_ai_meta ENABLE ROW LEVEL SECURITY;

--
-- Name: conversation_ai_meta conversation_ai_meta_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY conversation_ai_meta_service_all ON public.conversation_ai_meta USING ((((current_setting('request.jwt.claims'::text, true))::jsonb ->> 'role'::text) = 'service_role'::text));


--
-- Name: conversation_members; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.conversation_members ENABLE ROW LEVEL SECURITY;

--
-- Name: conversation_members conversation_members_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY conversation_members_select ON public.conversation_members FOR SELECT USING (public.is_conversation_member(( SELECT auth.uid() AS uid), conversation_id));


--
-- Name: conversation_members conversation_members_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY conversation_members_service_all ON public.conversation_members USING ((((current_setting('request.jwt.claims'::text, true))::jsonb ->> 'role'::text) = 'service_role'::text));


--
-- Name: conversation_memory; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.conversation_memory ENABLE ROW LEVEL SECURITY;

--
-- Name: conversation_memory conversation_memory_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY conversation_memory_service_all ON public.conversation_memory USING ((((current_setting('request.jwt.claims'::text, true))::jsonb ->> 'role'::text) = 'service_role'::text));


--
-- Name: conversations; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.conversations ENABLE ROW LEVEL SECURITY;

--
-- Name: conversations conversations_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY conversations_select ON public.conversations FOR SELECT USING ((((type = 'public'::text) AND (scope_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))) OR public.is_conversation_member(( SELECT auth.uid() AS uid), id)));


--
-- Name: conversations conversations_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY conversations_service_all ON public.conversations USING ((((current_setting('request.jwt.claims'::text, true))::jsonb ->> 'role'::text) = 'service_role'::text));


--
-- Name: cost_audit_log; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cost_audit_log ENABLE ROW LEVEL SECURITY;

--
-- Name: credit_pricing; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.credit_pricing ENABLE ROW LEVEL SECURITY;

--
-- Name: credit_pricing credit_pricing_admin_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY credit_pricing_admin_delete ON public.credit_pricing FOR DELETE USING ((EXISTS ( SELECT 1
   FROM public.user_profiles up
  WHERE ((up.id = ( SELECT auth.uid() AS uid)) AND (up.role = 'admin'::public.user_role)))));


--
-- Name: credit_pricing credit_pricing_admin_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY credit_pricing_admin_insert ON public.credit_pricing FOR INSERT WITH CHECK ((EXISTS ( SELECT 1
   FROM public.user_profiles up
  WHERE ((up.id = ( SELECT auth.uid() AS uid)) AND (up.role = 'admin'::public.user_role)))));


--
-- Name: credit_pricing credit_pricing_admin_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY credit_pricing_admin_update ON public.credit_pricing FOR UPDATE USING ((EXISTS ( SELECT 1
   FROM public.user_profiles up
  WHERE ((up.id = ( SELECT auth.uid() AS uid)) AND (up.role = 'admin'::public.user_role))))) WITH CHECK ((EXISTS ( SELECT 1
   FROM public.user_profiles up
  WHERE ((up.id = ( SELECT auth.uid() AS uid)) AND (up.role = 'admin'::public.user_role)))));


--
-- Name: credit_pricing credit_pricing_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY credit_pricing_select ON public.credit_pricing FOR SELECT USING (true);


--
-- Name: credit_transactions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.credit_transactions ENABLE ROW LEVEL SECURITY;

--
-- Name: credit_transactions credit_transactions_admin_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY credit_transactions_admin_delete ON public.credit_transactions FOR DELETE USING ((EXISTS ( SELECT 1
   FROM public.user_profiles up
  WHERE ((up.id = ( SELECT auth.uid() AS uid)) AND (up.role = 'admin'::public.user_role)))));


--
-- Name: credit_transactions credit_transactions_admin_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY credit_transactions_admin_insert ON public.credit_transactions FOR INSERT WITH CHECK ((EXISTS ( SELECT 1
   FROM public.user_profiles up
  WHERE ((up.id = ( SELECT auth.uid() AS uid)) AND (up.role = 'admin'::public.user_role)))));


--
-- Name: credit_transactions credit_transactions_admin_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY credit_transactions_admin_update ON public.credit_transactions FOR UPDATE USING ((EXISTS ( SELECT 1
   FROM public.user_profiles up
  WHERE ((up.id = ( SELECT auth.uid() AS uid)) AND (up.role = 'admin'::public.user_role))))) WITH CHECK ((EXISTS ( SELECT 1
   FROM public.user_profiles up
  WHERE ((up.id = ( SELECT auth.uid() AS uid)) AND (up.role = 'admin'::public.user_role)))));


--
-- Name: credit_transactions credit_transactions_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY credit_transactions_select ON public.credit_transactions FOR SELECT USING (((user_id = ( SELECT auth.uid() AS uid)) OR (EXISTS ( SELECT 1
   FROM public.user_profiles up
  WHERE ((up.id = ( SELECT auth.uid() AS uid)) AND (up.role = 'admin'::public.user_role))))));


--
-- Name: provider_credits credits_service_full; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY credits_service_full ON public.provider_credits TO service_role USING (true) WITH CHECK (true);


--
-- Name: canvas_resource_refs crr_service_role_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY crr_service_role_all ON public.canvas_resource_refs TO service_role USING (true) WITH CHECK (true);


--
-- Name: daily_point_gifts daily_gifts_user_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY daily_gifts_user_select ON public.daily_point_gifts FOR SELECT USING ((user_id = ( SELECT auth.uid() AS uid)));


--
-- Name: daily_point_gifts; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.daily_point_gifts ENABLE ROW LEVEL SECURITY;

--
-- Name: dbos_workflow_routing; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.dbos_workflow_routing ENABLE ROW LEVEL SECURITY;

--
-- Name: deployment_logs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.deployment_logs ENABLE ROW LEVEL SECURITY;

--
-- Name: deployment_logs deployment_logs_admin_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY deployment_logs_admin_read ON public.deployment_logs FOR SELECT USING ((EXISTS ( SELECT 1
   FROM auth.users
  WHERE (users.id = ( SELECT auth.uid() AS uid)))));


--
-- Name: distribution_oauth_states; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.distribution_oauth_states ENABLE ROW LEVEL SECURITY;

--
-- Name: episodes; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.episodes ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_run_events events_service_full; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY events_service_full ON public.agent_run_events TO service_role USING (true) WITH CHECK (true);


--
-- Name: file_versions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.file_versions ENABLE ROW LEVEL SECURITY;

--
-- Name: folders; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.folders ENABLE ROW LEVEL SECURITY;

--
-- Name: frontend_error_logs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.frontend_error_logs ENABLE ROW LEVEL SECURITY;

--
-- Name: fx_rates; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.fx_rates ENABLE ROW LEVEL SECURITY;

--
-- Name: fx_rates fx_service_full; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY fx_service_full ON public.fx_rates TO service_role USING (true) WITH CHECK (true);


--
-- Name: generated_media; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.generated_media ENABLE ROW LEVEL SECURITY;

--
-- Name: generated_media generated_media_service_role_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY generated_media_service_role_all ON public.generated_media TO service_role USING (true) WITH CHECK (true);


--
-- Name: hotspot_user_state; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.hotspot_user_state ENABLE ROW LEVEL SECURITY;

--
-- Name: hotspots; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.hotspots ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_inbox inbox_sender_readable; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY inbox_sender_readable ON public.agent_inbox FOR SELECT USING ((sender_user_id = auth.uid()));


--
-- Name: agent_inbox inbox_service_full; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY inbox_service_full ON public.agent_inbox TO service_role USING (true) WITH CHECK (true);


--
-- Name: inspiration_api_tokens; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.inspiration_api_tokens ENABLE ROW LEVEL SECURITY;

--
-- Name: inspiration_api_tokens inspiration_api_tokens_owner; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY inspiration_api_tokens_owner ON public.inspiration_api_tokens USING ((auth.uid() = user_id)) WITH CHECK ((auth.uid() = user_id));


--
-- Name: inspiration_attachments; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.inspiration_attachments ENABLE ROW LEVEL SECURITY;

--
-- Name: inspiration_attachments inspiration_attachments_owner; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY inspiration_attachments_owner ON public.inspiration_attachments USING ((auth.uid() = user_id)) WITH CHECK ((auth.uid() = user_id));


--
-- Name: inspiration_notes; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.inspiration_notes ENABLE ROW LEVEL SECURITY;

--
-- Name: inspiration_notes inspiration_notes_owner; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY inspiration_notes_owner ON public.inspiration_notes USING ((auth.uid() = user_id)) WITH CHECK ((auth.uid() = user_id));


--
-- Name: issue_messages; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.issue_messages ENABLE ROW LEVEL SECURITY;

--
-- Name: issue_messages issue_messages_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY issue_messages_insert ON public.issue_messages FOR INSERT TO authenticated WITH CHECK (((EXISTS ( SELECT 1
   FROM public.issues i
  WHERE (i.id = issue_messages.issue_id))) AND (((kind = 'comment'::text) AND (author_user_id = auth.uid())) OR ((kind = 'system_status'::text) AND ((author_user_id = auth.uid()) OR (author_user_id IS NULL))))));


--
-- Name: issue_messages issue_messages_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY issue_messages_select ON public.issue_messages FOR SELECT TO authenticated USING ((EXISTS ( SELECT 1
   FROM public.issues i
  WHERE (i.id = issue_messages.issue_id))));


--
-- Name: issue_messages issue_messages_service_full; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY issue_messages_service_full ON public.issue_messages TO service_role USING (true) WITH CHECK (true);


--
-- Name: issue_sequence; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.issue_sequence ENABLE ROW LEVEL SECURITY;

--
-- Name: issues; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.issues ENABLE ROW LEVEL SECURITY;

--
-- Name: issues issues_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY issues_insert ON public.issues FOR INSERT WITH CHECK (((created_by_user_id = auth.uid()) AND ((team_id IS NULL) OR (EXISTS ( SELECT 1
   FROM public.team_members tm
  WHERE ((tm.team_id = issues.team_id) AND (tm.user_id = auth.uid()))))) AND ((project_id IS NULL) OR (EXISTS ( SELECT 1
   FROM public.projects p
  WHERE ((p.id = issues.project_id) AND ((p.owner_id = auth.uid()) OR ((p.team_id IS NOT NULL) AND (EXISTS ( SELECT 1
           FROM public.team_members tm2
          WHERE ((tm2.team_id = p.team_id) AND (tm2.user_id = auth.uid())))))))))) AND ((assignee_user_id IS NULL) OR (assignee_user_id = auth.uid()) OR ((team_id IS NOT NULL) AND (EXISTS ( SELECT 1
   FROM public.team_members tm3
  WHERE ((tm3.team_id = issues.team_id) AND (tm3.user_id = issues.assignee_user_id))))) OR ((project_id IS NOT NULL) AND (EXISTS ( SELECT 1
   FROM (public.projects p2
     JOIN public.team_members tm4 ON (((tm4.team_id = p2.team_id) AND (tm4.user_id = issues.assignee_user_id))))
  WHERE (p2.id = issues.project_id)))))));


--
-- Name: POLICY issues_insert ON issues; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON POLICY issues_insert ON public.issues IS 'INSERT WITH CHECK enforces created_by_user_id = self AND team/project membership AND assignee scope. Closes /review #2. service_role bypasses (used by DBOS workflows for agent-created issues).';


--
-- Name: issues issues_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY issues_select ON public.issues FOR SELECT USING ((((created_by_user_id = auth.uid()) OR (assignee_user_id = auth.uid()) OR ((team_id IS NOT NULL) AND (EXISTS ( SELECT 1
   FROM public.team_members tm
  WHERE ((tm.team_id = issues.team_id) AND (tm.user_id = auth.uid()))))) OR ((project_id IS NOT NULL) AND (EXISTS ( SELECT 1
   FROM public.projects p
  WHERE ((p.id = issues.project_id) AND ((p.owner_id = auth.uid()) OR ((p.team_id IS NOT NULL) AND (EXISTS ( SELECT 1
           FROM public.team_members tm2
          WHERE ((tm2.team_id = p.team_id) AND (tm2.user_id = auth.uid()))))))))))) AND ((hidden_at IS NULL) OR (created_by_user_id = auth.uid()))));


--
-- Name: issues issues_update_general; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY issues_update_general ON public.issues FOR UPDATE USING (((created_by_user_id = auth.uid()) OR (assignee_user_id = auth.uid()))) WITH CHECK (((created_by_user_id = auth.uid()) OR (assignee_user_id = auth.uid())));


--
-- Name: libraries; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.libraries ENABLE ROW LEVEL SECURITY;

--
-- Name: mediahub_models; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.mediahub_models ENABLE ROW LEVEL SECURITY;

--
-- Name: member_quotas; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.member_quotas ENABLE ROW LEVEL SECURITY;

--
-- Name: member_quotas member_quotas_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY member_quotas_select ON public.member_quotas FOR SELECT USING (((user_id = ( SELECT auth.uid() AS uid)) OR (team_id IN ( SELECT tm.team_id
   FROM public.team_members tm
  WHERE ((tm.user_id = ( SELECT auth.uid() AS uid)) AND ((tm.role)::text = ANY (ARRAY['owner'::text, 'admin'::text])))))));


--
-- Name: message_attachments; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.message_attachments ENABLE ROW LEVEL SECURITY;

--
-- Name: message_attachments message_attachments_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY message_attachments_select ON public.message_attachments FOR SELECT USING ((EXISTS ( SELECT 1
   FROM public.messages m
  WHERE ((m.id = message_attachments.message_id) AND public.is_conversation_member(( SELECT auth.uid() AS uid), m.conversation_id)))));


--
-- Name: message_attachments message_attachments_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY message_attachments_service_all ON public.message_attachments USING ((((current_setting('request.jwt.claims'::text, true))::jsonb ->> 'role'::text) = 'service_role'::text));


--
-- Name: message_refs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.message_refs ENABLE ROW LEVEL SECURITY;

--
-- Name: message_refs message_refs_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY message_refs_select ON public.message_refs FOR SELECT USING ((EXISTS ( SELECT 1
   FROM public.messages m
  WHERE ((m.id = message_refs.message_id) AND public.is_conversation_member(( SELECT auth.uid() AS uid), m.conversation_id)))));


--
-- Name: message_refs message_refs_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY message_refs_service_all ON public.message_refs USING ((((current_setting('request.jwt.claims'::text, true))::jsonb ->> 'role'::text) = 'service_role'::text));


--
-- Name: messages; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.messages ENABLE ROW LEVEL SECURITY;

--
-- Name: messages messages_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY messages_select ON public.messages FOR SELECT USING ((public.is_conversation_member(( SELECT auth.uid() AS uid), conversation_id) AND (EXISTS ( SELECT 1
   FROM public.conversations c
  WHERE ((c.id = messages.conversation_id) AND ((c.history_mode = 'shared'::text) OR (messages.created_at >= public.conversation_member_joined_at(( SELECT auth.uid() AS uid), messages.conversation_id))))))));


--
-- Name: messages messages_service_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY messages_service_all ON public.messages USING ((((current_setting('request.jwt.claims'::text, true))::jsonb ->> 'role'::text) = 'service_role'::text));


--
-- Name: notifications; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;

--
-- Name: mediahub_models nous_models_admin_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY nous_models_admin_delete ON public.mediahub_models FOR DELETE USING ((EXISTS ( SELECT 1
   FROM public.user_profiles up
  WHERE ((up.id = ( SELECT auth.uid() AS uid)) AND (up.role = 'admin'::public.user_role)))));


--
-- Name: mediahub_models nous_models_admin_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY nous_models_admin_insert ON public.mediahub_models FOR INSERT WITH CHECK ((EXISTS ( SELECT 1
   FROM public.user_profiles up
  WHERE ((up.id = ( SELECT auth.uid() AS uid)) AND (up.role = 'admin'::public.user_role)))));


--
-- Name: mediahub_models nous_models_admin_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY nous_models_admin_update ON public.mediahub_models FOR UPDATE USING ((EXISTS ( SELECT 1
   FROM public.user_profiles up
  WHERE ((up.id = ( SELECT auth.uid() AS uid)) AND (up.role = 'admin'::public.user_role))))) WITH CHECK ((EXISTS ( SELECT 1
   FROM public.user_profiles up
  WHERE ((up.id = ( SELECT auth.uid() AS uid)) AND (up.role = 'admin'::public.user_role)))));


--
-- Name: mediahub_models nous_models_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY nous_models_select ON public.mediahub_models FOR SELECT USING (((is_enabled = true) OR (EXISTS ( SELECT 1
   FROM public.user_profiles up
  WHERE ((up.id = ( SELECT auth.uid() AS uid)) AND (up.role = 'admin'::public.user_role))))));


--
-- Name: orders; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.orders ENABLE ROW LEVEL SECURITY;

--
-- Name: orders orders_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY orders_select ON public.orders FOR SELECT USING (((user_id = ( SELECT auth.uid() AS uid)) OR (team_id IN ( SELECT tm.team_id
   FROM public.team_members tm
  WHERE ((tm.user_id = ( SELECT auth.uid() AS uid)) AND ((tm.role)::text = ANY (ARRAY['owner'::text, 'admin'::text])))))));


--
-- Name: agent_outbox outbox_recipient_readable; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY outbox_recipient_readable ON public.agent_outbox FOR SELECT USING ((recipient_user_id = auth.uid()));


--
-- Name: agent_outbox outbox_service_full; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY outbox_service_full ON public.agent_outbox TO service_role USING (true) WITH CHECK (true);


--
-- Name: agent_commitments own_commitments_readable; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY own_commitments_readable ON public.agent_commitments FOR SELECT USING (((user_id = auth.uid()) OR ((user_id IS NULL) AND (EXISTS ( SELECT 1
   FROM public.ai_agents a
  WHERE ((a.id = agent_commitments.agent_id) AND ((a.user_id = auth.uid()) OR ((a.team_id IS NOT NULL) AND (EXISTS ( SELECT 1
           FROM public.team_members tm
          WHERE ((tm.team_id = a.team_id) AND (tm.user_id = auth.uid()))))))))))));


--
-- Name: agent_run_events own_events_readable; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY own_events_readable ON public.agent_run_events FOR SELECT USING ((EXISTS ( SELECT 1
   FROM public.agent_runs r
  WHERE ((r.id = agent_run_events.run_id) AND (r.user_id = auth.uid())))));


--
-- Name: agent_runs own_runs_readable; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY own_runs_readable ON public.agent_runs FOR SELECT USING ((user_id = auth.uid()));


--
-- Name: parsed_media; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.parsed_media ENABLE ROW LEVEL SECURITY;

--
-- Name: point_packages; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.point_packages ENABLE ROW LEVEL SECURITY;

--
-- Name: point_pricing; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.point_pricing ENABLE ROW LEVEL SECURITY;

--
-- Name: point_transactions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.point_transactions ENABLE ROW LEVEL SECURITY;

--
-- Name: ai_model_prices prices_readable_by_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY prices_readable_by_all ON public.ai_model_prices FOR SELECT USING (true);


--
-- Name: ai_model_prices prices_write_service_only; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY prices_write_service_only ON public.ai_model_prices TO service_role USING (true) WITH CHECK (true);


--
-- Name: provider_pricing pricing_service_full; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY pricing_service_full ON public.provider_pricing TO service_role USING (true) WITH CHECK (true);


--
-- Name: project_characters; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.project_characters ENABLE ROW LEVEL SECURITY;

--
-- Name: project_collections; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.project_collections ENABLE ROW LEVEL SECURITY;

--
-- Name: project_file_comments; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.project_file_comments ENABLE ROW LEVEL SECURITY;

--
-- Name: project_files; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.project_files ENABLE ROW LEVEL SECURITY;

--
-- Name: project_folders; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.project_folders ENABLE ROW LEVEL SECURITY;

--
-- Name: project_lib_entities; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.project_lib_entities ENABLE ROW LEVEL SECURITY;

--
-- Name: project_members; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.project_members ENABLE ROW LEVEL SECURITY;

--
-- Name: project_members project_members_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY project_members_delete ON public.project_members FOR DELETE USING (((project_id IN ( SELECT projects.id
   FROM public.projects)) OR ((user_id = ( SELECT auth.uid() AS uid)) AND ((role)::text = 'admin'::text))));


--
-- Name: project_members project_members_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY project_members_insert ON public.project_members FOR INSERT WITH CHECK (((project_id IN ( SELECT projects.id
   FROM public.projects)) OR (project_id IN ( SELECT p.id
   FROM public.projects p
  WHERE (p.owner_id = ( SELECT auth.uid() AS uid))))));


--
-- Name: project_members project_members_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY project_members_select ON public.project_members FOR SELECT USING (((project_id IN ( SELECT projects.id
   FROM public.projects)) OR ((user_id = ( SELECT auth.uid() AS uid)) AND ((role)::text = 'admin'::text))));


--
-- Name: project_members project_members_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY project_members_update ON public.project_members FOR UPDATE USING (((project_id IN ( SELECT projects.id
   FROM public.projects)) OR ((user_id = ( SELECT auth.uid() AS uid)) AND ((role)::text = 'admin'::text)))) WITH CHECK (((project_id IN ( SELECT projects.id
   FROM public.projects)) OR (project_id IN ( SELECT p.id
   FROM public.projects p
  WHERE (p.owner_id = ( SELECT auth.uid() AS uid))))));


--
-- Name: agent_runs project_runs_readable; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY project_runs_readable ON public.agent_runs FOR SELECT USING (((project_id IS NOT NULL) AND (EXISTS ( SELECT 1
   FROM (public.projects p
     LEFT JOIN public.team_members tm ON ((tm.team_id = p.team_id)))
  WHERE ((p.id = agent_runs.project_id) AND ((p.owner_id = auth.uid()) OR (tm.user_id = auth.uid())))))));


--
-- Name: project_stage_history; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.project_stage_history ENABLE ROW LEVEL SECURITY;

--
-- Name: project_stage_history project_stage_history_member_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY project_stage_history_member_all ON public.project_stage_history USING ((project_id IN ( SELECT projects.id
   FROM public.projects
  WHERE ((projects.owner_id = auth.uid()) OR (projects.team_id IN ( SELECT team_members.team_id
           FROM public.team_members
          WHERE (team_members.user_id = auth.uid()))))))) WITH CHECK ((project_id IN ( SELECT projects.id
   FROM public.projects
  WHERE ((projects.owner_id = auth.uid()) OR (projects.team_id IN ( SELECT team_members.team_id
           FROM public.team_members
          WHERE (team_members.user_id = auth.uid())))))));


--
-- Name: project_stage_history project_stage_history_service_role; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY project_stage_history_service_role ON public.project_stage_history USING ((auth.role() = 'service_role'::text)) WITH CHECK ((auth.role() = 'service_role'::text));


--
-- Name: project_stages; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.project_stages ENABLE ROW LEVEL SECURITY;

--
-- Name: project_stages project_stages_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY project_stages_read ON public.project_stages FOR SELECT USING (((auth.role() = 'authenticated'::text) OR (auth.role() = 'service_role'::text)));


--
-- Name: project_stages project_stages_service_role; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY project_stages_service_role ON public.project_stages USING ((auth.role() = 'service_role'::text)) WITH CHECK ((auth.role() = 'service_role'::text));


--
-- Name: project_style_profile; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.project_style_profile ENABLE ROW LEVEL SECURITY;

--
-- Name: project_style_profile project_style_profile_member_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY project_style_profile_member_all ON public.project_style_profile USING ((project_id IN ( SELECT projects.id
   FROM public.projects
  WHERE ((projects.owner_id = auth.uid()) OR (projects.team_id IN ( SELECT team_members.team_id
           FROM public.team_members
          WHERE (team_members.user_id = auth.uid()))))))) WITH CHECK ((project_id IN ( SELECT projects.id
   FROM public.projects
  WHERE ((projects.owner_id = auth.uid()) OR (projects.team_id IN ( SELECT team_members.team_id
           FROM public.team_members
          WHERE (team_members.user_id = auth.uid())))))));


--
-- Name: project_style_profile project_style_profile_service_role; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY project_style_profile_service_role ON public.project_style_profile USING ((auth.role() = 'service_role'::text)) WITH CHECK ((auth.role() = 'service_role'::text));


--
-- Name: project_tasks; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.project_tasks ENABLE ROW LEVEL SECURITY;

--
-- Name: project_workflows; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.project_workflows ENABLE ROW LEVEL SECURITY;

--
-- Name: projects; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.projects ENABLE ROW LEVEL SECURITY;

--
-- Name: provider_byok_keys; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.provider_byok_keys ENABLE ROW LEVEL SECURITY;

--
-- Name: provider_contracts; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.provider_contracts ENABLE ROW LEVEL SECURITY;

--
-- Name: provider_credits; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.provider_credits ENABLE ROW LEVEL SECURITY;

--
-- Name: provider_monthly_spend; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.provider_monthly_spend ENABLE ROW LEVEL SECURITY;

--
-- Name: provider_pricing; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.provider_pricing ENABLE ROW LEVEL SECURITY;

--
-- Name: publish_task_accounts; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.publish_task_accounts ENABLE ROW LEVEL SECURITY;

--
-- Name: publish_tasks; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.publish_tasks ENABLE ROW LEVEL SECURITY;

--
-- Name: ai_agent_versions read own agent versions; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "read own agent versions" ON public.ai_agent_versions FOR SELECT USING ((EXISTS ( SELECT 1
   FROM public.ai_agents a
  WHERE (a.id = ai_agent_versions.agent_id))));


--
-- Name: skill_file_versions read own skill file versions; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "read own skill file versions" ON public.skill_file_versions FOR SELECT USING ((EXISTS ( SELECT 1
   FROM public.skill_files sf
  WHERE (sf.id = skill_file_versions.skill_file_id))));


--
-- Name: skill_versions read own skill versions; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "read own skill versions" ON public.skill_versions FOR SELECT USING ((EXISTS ( SELECT 1
   FROM public.skills s
  WHERE (s.id = skill_versions.skill_id))));


--
-- Name: resource_access_logs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.resource_access_logs ENABLE ROW LEVEL SECURITY;

--
-- Name: resource_access_logs resource_access_logs_user_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY resource_access_logs_user_select ON public.resource_access_logs FOR SELECT USING ((user_id = ( SELECT auth.uid() AS uid)));


--
-- Name: resource_analysis; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.resource_analysis ENABLE ROW LEVEL SECURITY;

--
-- Name: resource_items; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.resource_items ENABLE ROW LEVEL SECURITY;

--
-- Name: resource_summaries; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.resource_summaries ENABLE ROW LEVEL SECURITY;

--
-- Name: resource_tags; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.resource_tags ENABLE ROW LEVEL SECURITY;

--
-- Name: resource_transcripts; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.resource_transcripts ENABLE ROW LEVEL SECURITY;

--
-- Name: resource_versions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.resource_versions ENABLE ROW LEVEL SECURITY;

--
-- Name: resources; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.resources ENABLE ROW LEVEL SECURITY;

--
-- Name: review_annotations; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.review_annotations ENABLE ROW LEVEL SECURITY;

--
-- Name: review_comments; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.review_comments ENABLE ROW LEVEL SECURITY;

--
-- Name: review_status; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.review_status ENABLE ROW LEVEL SECURITY;

--
-- Name: zzz_deprecated_storyboard_assets sb_assets_team_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_assets_team_delete ON public.zzz_deprecated_storyboard_assets FOR DELETE USING ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_assets sb_assets_team_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_assets_team_insert ON public.zzz_deprecated_storyboard_assets FOR INSERT WITH CHECK ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_assets sb_assets_team_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_assets_team_select ON public.zzz_deprecated_storyboard_assets FOR SELECT USING ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_assets sb_assets_team_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_assets_team_update ON public.zzz_deprecated_storyboard_assets FOR UPDATE USING ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))))) WITH CHECK ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_characters sb_characters_team_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_characters_team_delete ON public.zzz_deprecated_storyboard_characters FOR DELETE USING ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_characters sb_characters_team_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_characters_team_insert ON public.zzz_deprecated_storyboard_characters FOR INSERT WITH CHECK ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_characters sb_characters_team_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_characters_team_select ON public.zzz_deprecated_storyboard_characters FOR SELECT USING ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_characters sb_characters_team_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_characters_team_update ON public.zzz_deprecated_storyboard_characters FOR UPDATE USING ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))))) WITH CHECK ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_edges sb_edges_team_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_edges_team_delete ON public.zzz_deprecated_storyboard_edges FOR DELETE USING ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_edges sb_edges_team_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_edges_team_insert ON public.zzz_deprecated_storyboard_edges FOR INSERT WITH CHECK ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_edges sb_edges_team_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_edges_team_select ON public.zzz_deprecated_storyboard_edges FOR SELECT USING ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_edges sb_edges_team_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_edges_team_update ON public.zzz_deprecated_storyboard_edges FOR UPDATE USING ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))))) WITH CHECK ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_frame_characters sb_frame_chars_team_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_frame_chars_team_delete ON public.zzz_deprecated_storyboard_frame_characters FOR DELETE USING ((frame_id IN ( SELECT f.id
   FROM (public.zzz_deprecated_storyboard_frames f
     JOIN public.zzz_deprecated_storyboard_projects p ON ((p.id = f.project_id)))
  WHERE (p.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_frame_characters sb_frame_chars_team_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_frame_chars_team_insert ON public.zzz_deprecated_storyboard_frame_characters FOR INSERT WITH CHECK ((frame_id IN ( SELECT f.id
   FROM (public.zzz_deprecated_storyboard_frames f
     JOIN public.zzz_deprecated_storyboard_projects p ON ((p.id = f.project_id)))
  WHERE (p.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_frame_characters sb_frame_chars_team_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_frame_chars_team_select ON public.zzz_deprecated_storyboard_frame_characters FOR SELECT USING ((frame_id IN ( SELECT f.id
   FROM (public.zzz_deprecated_storyboard_frames f
     JOIN public.zzz_deprecated_storyboard_projects p ON ((p.id = f.project_id)))
  WHERE (p.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_frames sb_frames_team_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_frames_team_delete ON public.zzz_deprecated_storyboard_frames FOR DELETE USING ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_frames sb_frames_team_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_frames_team_insert ON public.zzz_deprecated_storyboard_frames FOR INSERT WITH CHECK ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_frames sb_frames_team_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_frames_team_select ON public.zzz_deprecated_storyboard_frames FOR SELECT USING ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_frames sb_frames_team_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_frames_team_update ON public.zzz_deprecated_storyboard_frames FOR UPDATE USING ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))))) WITH CHECK ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_nodes sb_nodes_team_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_nodes_team_delete ON public.zzz_deprecated_storyboard_nodes FOR DELETE USING ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_nodes sb_nodes_team_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_nodes_team_insert ON public.zzz_deprecated_storyboard_nodes FOR INSERT WITH CHECK ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_nodes sb_nodes_team_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_nodes_team_select ON public.zzz_deprecated_storyboard_nodes FOR SELECT USING ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_nodes sb_nodes_team_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_nodes_team_update ON public.zzz_deprecated_storyboard_nodes FOR UPDATE USING ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))))) WITH CHECK ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_projects sb_projects_team_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_projects_team_delete ON public.zzz_deprecated_storyboard_projects FOR DELETE USING ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: zzz_deprecated_storyboard_projects sb_projects_team_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_projects_team_insert ON public.zzz_deprecated_storyboard_projects FOR INSERT WITH CHECK ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: zzz_deprecated_storyboard_projects sb_projects_team_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_projects_team_select ON public.zzz_deprecated_storyboard_projects FOR SELECT USING ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: zzz_deprecated_storyboard_projects sb_projects_team_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_projects_team_update ON public.zzz_deprecated_storyboard_projects FOR UPDATE USING ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))) WITH CHECK ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: zzz_deprecated_storyboard_video_assets sb_video_assets_team_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_video_assets_team_delete ON public.zzz_deprecated_storyboard_video_assets FOR DELETE USING ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_video_assets sb_video_assets_team_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_video_assets_team_insert ON public.zzz_deprecated_storyboard_video_assets FOR INSERT WITH CHECK ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_video_assets sb_video_assets_team_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_video_assets_team_select ON public.zzz_deprecated_storyboard_video_assets FOR SELECT USING ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: zzz_deprecated_storyboard_video_assets sb_video_assets_team_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sb_video_assets_team_update ON public.zzz_deprecated_storyboard_video_assets FOR UPDATE USING ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))))) WITH CHECK ((project_id IN ( SELECT zzz_deprecated_storyboard_projects.id
   FROM public.zzz_deprecated_storyboard_projects
  WHERE (zzz_deprecated_storyboard_projects.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))));


--
-- Name: script_assets; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.script_assets ENABLE ROW LEVEL SECURITY;

--
-- Name: script_assets script_assets_team_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY script_assets_team_write ON public.script_assets USING ((EXISTS ( SELECT 1
   FROM public.script_projects sp
  WHERE ((sp.id = script_assets.script_id) AND (sp.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))))) WITH CHECK ((EXISTS ( SELECT 1
   FROM public.script_projects sp
  WHERE ((sp.id = script_assets.script_id) AND (sp.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))))));


--
-- Name: script_beats; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.script_beats ENABLE ROW LEVEL SECURITY;

--
-- Name: script_chapters; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.script_chapters ENABLE ROW LEVEL SECURITY;

--
-- Name: script_chapters script_chapters_team_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY script_chapters_team_write ON public.script_chapters USING ((EXISTS ( SELECT 1
   FROM public.script_projects sp
  WHERE ((sp.id = script_chapters.script_id) AND (sp.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))))) WITH CHECK ((EXISTS ( SELECT 1
   FROM public.script_projects sp
  WHERE ((sp.id = script_chapters.script_id) AND (sp.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))))));


--
-- Name: script_commits; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.script_commits ENABLE ROW LEVEL SECURITY;

--
-- Name: script_ops; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.script_ops ENABLE ROW LEVEL SECURITY;

--
-- Name: script_ops script_ops_member_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY script_ops_member_select ON public.script_ops FOR SELECT TO authenticated USING (public.can_read_script_op(scene_id));


--
-- Name: script_projects; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.script_projects ENABLE ROW LEVEL SECURITY;

--
-- Name: script_projects script_projects_team_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY script_projects_team_delete ON public.script_projects FOR DELETE USING ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: script_projects script_projects_team_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY script_projects_team_insert ON public.script_projects FOR INSERT WITH CHECK ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: script_projects script_projects_team_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY script_projects_team_select ON public.script_projects FOR SELECT USING ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: script_projects script_projects_team_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY script_projects_team_update ON public.script_projects FOR UPDATE USING ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))) WITH CHECK ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: script_scenes; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.script_scenes ENABLE ROW LEVEL SECURITY;

--
-- Name: script_shots; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.script_shots ENABLE ROW LEVEL SECURITY;

--
-- Name: script_storyboard_links; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.script_storyboard_links ENABLE ROW LEVEL SECURITY;

--
-- Name: search_logs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.search_logs ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_runs service_role_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY service_role_all ON public.agent_runs TO service_role USING (true) WITH CHECK (true);


--
-- Name: application_logs service_role_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY service_role_all ON public.application_logs TO service_role USING (true) WITH CHECK (true);


--
-- Name: temp_tokens service_role_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY service_role_all ON public.temp_tokens TO service_role USING (true) WITH CHECK (true);


--
-- Name: api_request_logs service_role_api_request_logs; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY service_role_api_request_logs ON public.api_request_logs TO service_role USING (true) WITH CHECK (true);


--
-- Name: frontend_error_logs service_role_frontend_error_logs; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY service_role_frontend_error_logs ON public.frontend_error_logs TO service_role USING (true) WITH CHECK (true);


--
-- Name: ai_session_memory session_memory_write_service_only; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY session_memory_write_service_only ON public.ai_session_memory TO service_role USING (true) WITH CHECK (true);


--
-- Name: share_views; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.share_views ENABLE ROW LEVEL SECURITY;

--
-- Name: shares; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.shares ENABLE ROW LEVEL SECURITY;

--
-- Name: signal_sources; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.signal_sources ENABLE ROW LEVEL SECURITY;

--
-- Name: skill_file_versions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.skill_file_versions ENABLE ROW LEVEL SECURITY;

--
-- Name: skill_files; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.skill_files ENABLE ROW LEVEL SECURITY;

--
-- Name: skill_files skill_files_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY skill_files_delete ON public.skill_files FOR DELETE USING ((skill_id IN ( SELECT skills.id
   FROM public.skills
  WHERE (skills.created_by = auth.uid()))));


--
-- Name: skill_files skill_files_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY skill_files_insert ON public.skill_files FOR INSERT WITH CHECK ((skill_id IN ( SELECT skills.id
   FROM public.skills
  WHERE (skills.created_by = auth.uid()))));


--
-- Name: skill_files skill_files_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY skill_files_read ON public.skill_files FOR SELECT USING ((skill_id IN ( SELECT skills.id
   FROM public.skills
  WHERE ((skills.is_public = true) OR (skills.created_by = auth.uid()) OR (skills.team_id IN ( SELECT team_members.team_id
           FROM public.team_members
          WHERE (team_members.user_id = auth.uid()))) OR (skills.project_id IN ( SELECT projects.id
           FROM public.projects
          WHERE (projects.owner_id = auth.uid())))))));


--
-- Name: skill_files skill_files_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY skill_files_update ON public.skill_files FOR UPDATE USING ((skill_id IN ( SELECT skills.id
   FROM public.skills
  WHERE (skills.created_by = auth.uid())))) WITH CHECK ((skill_id IN ( SELECT skills.id
   FROM public.skills
  WHERE (skills.created_by = auth.uid()))));


--
-- Name: skill_versions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.skill_versions ENABLE ROW LEVEL SECURITY;

--
-- Name: skills; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.skills ENABLE ROW LEVEL SECURITY;

--
-- Name: skills skills_team_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY skills_team_delete ON public.skills FOR DELETE USING ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: skills skills_team_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY skills_team_insert ON public.skills FOR INSERT WITH CHECK ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: skills skills_team_or_public_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY skills_team_or_public_select ON public.skills FOR SELECT USING (((is_public = true) OR (team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))));


--
-- Name: skills skills_team_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY skills_team_update ON public.skills FOR UPDATE USING ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))) WITH CHECK ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: smart_collections; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.smart_collections ENABLE ROW LEVEL SECURITY;

--
-- Name: social_accounts; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.social_accounts ENABLE ROW LEVEL SECURITY;

--
-- Name: provider_monthly_spend spend_service_full; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY spend_service_full ON public.provider_monthly_spend TO service_role USING (true) WITH CHECK (true);


--
-- Name: script_storyboard_links ssl_team_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ssl_team_write ON public.script_storyboard_links USING ((EXISTS ( SELECT 1
   FROM (public.script_chapters sc
     JOIN public.script_projects sp ON ((sp.id = sc.script_id)))
  WHERE ((sc.id = script_storyboard_links.chapter_id) AND (sp.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)))))) WITH CHECK ((EXISTS ( SELECT 1
   FROM (public.script_chapters sc
     JOIN public.script_projects sp ON ((sp.id = sc.script_id)))
  WHERE ((sc.id = script_storyboard_links.chapter_id) AND (sp.team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))))));


--
-- Name: agent_state_history state_history_owner_readable; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY state_history_owner_readable ON public.agent_state_history FOR SELECT USING ((EXISTS ( SELECT 1
   FROM public.ai_agents a
  WHERE ((a.id = agent_state_history.agent_id) AND (a.user_id = auth.uid())))));


--
-- Name: agent_state_history state_history_service_full; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY state_history_service_full ON public.agent_state_history TO service_role USING (true) WITH CHECK (true);


--
-- Name: style_templates; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.style_templates ENABLE ROW LEVEL SECURITY;

--
-- Name: style_templates style_templates_team_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY style_templates_team_delete ON public.style_templates FOR DELETE USING ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: style_templates style_templates_team_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY style_templates_team_insert ON public.style_templates FOR INSERT WITH CHECK ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: style_templates style_templates_team_or_public_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY style_templates_team_or_public_select ON public.style_templates FOR SELECT USING (((is_public = true) OR (team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))));


--
-- Name: style_templates style_templates_team_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY style_templates_team_update ON public.style_templates FOR UPDATE USING ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids))) WITH CHECK ((team_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)));


--
-- Name: system_settings; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.system_settings ENABLE ROW LEVEL SECURITY;

--
-- Name: system_settings system_settings_admin_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY system_settings_admin_delete ON public.system_settings FOR DELETE USING ((EXISTS ( SELECT 1
   FROM public.user_profiles up
  WHERE ((up.id = ( SELECT auth.uid() AS uid)) AND (up.role = 'admin'::public.user_role)))));


--
-- Name: system_settings system_settings_admin_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY system_settings_admin_insert ON public.system_settings FOR INSERT WITH CHECK ((EXISTS ( SELECT 1
   FROM public.user_profiles up
  WHERE ((up.id = ( SELECT auth.uid() AS uid)) AND (up.role = 'admin'::public.user_role)))));


--
-- Name: system_settings system_settings_admin_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY system_settings_admin_select ON public.system_settings FOR SELECT USING ((EXISTS ( SELECT 1
   FROM public.user_profiles up
  WHERE ((up.id = ( SELECT auth.uid() AS uid)) AND (up.role = 'admin'::public.user_role)))));


--
-- Name: system_settings system_settings_admin_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY system_settings_admin_update ON public.system_settings FOR UPDATE USING ((EXISTS ( SELECT 1
   FROM public.user_profiles up
  WHERE ((up.id = ( SELECT auth.uid() AS uid)) AND (up.role = 'admin'::public.user_role))))) WITH CHECK ((EXISTS ( SELECT 1
   FROM public.user_profiles up
  WHERE ((up.id = ( SELECT auth.uid() AS uid)) AND (up.role = 'admin'::public.user_role)))));


--
-- Name: system_status; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.system_status ENABLE ROW LEVEL SECURITY;

--
-- Name: tag_groups; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.tag_groups ENABLE ROW LEVEL SECURITY;

--
-- Name: tags; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.tags ENABLE ROW LEVEL SECURITY;

--
-- Name: tags tags_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tags_select ON public.tags FOR SELECT USING ((((type)::text = ANY (ARRAY['system'::text, 'time'::text])) OR (scope_id IN ( SELECT public.get_user_team_ids(( SELECT auth.uid() AS uid)) AS get_user_team_ids)) OR (user_id = ( SELECT auth.uid() AS uid))));


--
-- Name: task_flows; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.task_flows ENABLE ROW LEVEL SECURITY;

--
-- Name: task_tracking; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.task_tracking ENABLE ROW LEVEL SECURITY;

--
-- Name: task_tracking task_tracking_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY task_tracking_delete ON public.task_tracking FOR DELETE USING ((( SELECT auth.uid() AS uid) = user_id));


--
-- Name: task_tracking task_tracking_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY task_tracking_insert ON public.task_tracking FOR INSERT WITH CHECK ((( SELECT auth.uid() AS uid) = user_id));


--
-- Name: task_tracking task_tracking_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY task_tracking_select ON public.task_tracking FOR SELECT USING ((( SELECT auth.uid() AS uid) = user_id));


--
-- Name: task_tracking task_tracking_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY task_tracking_update ON public.task_tracking FOR UPDATE USING ((( SELECT auth.uid() AS uid) = user_id));


--
-- Name: agent_tasks tasks_owner_readable; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tasks_owner_readable ON public.agent_tasks FOR SELECT USING ((user_id = auth.uid()));


--
-- Name: agent_tasks tasks_service_full; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tasks_service_full ON public.agent_tasks TO service_role USING (true) WITH CHECK (true);


--
-- Name: team_invites; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.team_invites ENABLE ROW LEVEL SECURITY;

--
-- Name: team_members; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.team_members ENABLE ROW LEVEL SECURITY;

--
-- Name: team_plans; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.team_plans ENABLE ROW LEVEL SECURITY;

--
-- Name: team_quotas; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.team_quotas ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_runs team_runs_readable; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY team_runs_readable ON public.agent_runs FOR SELECT USING (((team_id IS NOT NULL) AND (EXISTS ( SELECT 1
   FROM public.team_members tm
  WHERE ((tm.team_id = agent_runs.team_id) AND (tm.user_id = auth.uid()))))));


--
-- Name: teams; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.teams ENABLE ROW LEVEL SECURITY;

--
-- Name: temp_tokens; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.temp_tokens ENABLE ROW LEVEL SECURITY;

--
-- Name: topic_groups; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.topic_groups ENABLE ROW LEVEL SECURITY;

--
-- Name: user_cookies; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.user_cookies ENABLE ROW LEVEL SECURITY;

--
-- Name: user_credits; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.user_credits ENABLE ROW LEVEL SECURITY;

--
-- Name: user_credits user_credits_admin_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY user_credits_admin_delete ON public.user_credits FOR DELETE USING ((EXISTS ( SELECT 1
   FROM public.user_profiles up
  WHERE ((up.id = ( SELECT auth.uid() AS uid)) AND (up.role = 'admin'::public.user_role)))));


--
-- Name: user_credits user_credits_admin_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY user_credits_admin_insert ON public.user_credits FOR INSERT WITH CHECK ((EXISTS ( SELECT 1
   FROM public.user_profiles up
  WHERE ((up.id = ( SELECT auth.uid() AS uid)) AND (up.role = 'admin'::public.user_role)))));


--
-- Name: user_credits user_credits_admin_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY user_credits_admin_update ON public.user_credits FOR UPDATE USING ((EXISTS ( SELECT 1
   FROM public.user_profiles up
  WHERE ((up.id = ( SELECT auth.uid() AS uid)) AND (up.role = 'admin'::public.user_role))))) WITH CHECK ((EXISTS ( SELECT 1
   FROM public.user_profiles up
  WHERE ((up.id = ( SELECT auth.uid() AS uid)) AND (up.role = 'admin'::public.user_role)))));


--
-- Name: user_credits user_credits_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY user_credits_select ON public.user_credits FOR SELECT USING (((user_id = ( SELECT auth.uid() AS uid)) OR (EXISTS ( SELECT 1
   FROM public.user_profiles up
  WHERE ((up.id = ( SELECT auth.uid() AS uid)) AND (up.role = 'admin'::public.user_role))))));


--
-- Name: user_hidden_sources; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.user_hidden_sources ENABLE ROW LEVEL SECURITY;

--
-- Name: user_logs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.user_logs ENABLE ROW LEVEL SECURITY;

--
-- Name: user_mcp_servers; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.user_mcp_servers ENABLE ROW LEVEL SECURITY;

--
-- Name: user_mcp_servers user_mcp_servers_owner_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY user_mcp_servers_owner_all ON public.user_mcp_servers USING ((user_id = auth.uid())) WITH CHECK ((user_id = auth.uid()));


--
-- Name: user_notifications; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.user_notifications ENABLE ROW LEVEL SECURITY;

--
-- Name: user_profiles; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.user_profiles ENABLE ROW LEVEL SECURITY;

--
-- Name: user_settings; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.user_settings ENABLE ROW LEVEL SECURITY;

--
-- Name: user_tag_preferences; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.user_tag_preferences ENABLE ROW LEVEL SECURITY;

--
-- Name: user_topic_interests; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.user_topic_interests ENABLE ROW LEVEL SECURITY;

--
-- Name: worker_registry; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.worker_registry ENABLE ROW LEVEL SECURITY;

--
-- Name: worker_registry worker_registry_service_role_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY worker_registry_service_role_all ON public.worker_registry TO service_role USING (true) WITH CHECK (true);


--
-- Name: agent_workers workers_owner_readable; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY workers_owner_readable ON public.agent_workers FOR SELECT USING ((EXISTS ( SELECT 1
   FROM public.ai_agents a
  WHERE ((a.id = agent_workers.agent_id) AND ((a.user_id = auth.uid()) OR ((a.team_id IS NOT NULL) AND (EXISTS ( SELECT 1
           FROM public.team_members tm
          WHERE ((tm.team_id = a.team_id) AND (tm.user_id = auth.uid()))))))))));


--
-- Name: agent_workers workers_service_full; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY workers_service_full ON public.agent_workers TO service_role USING (true) WITH CHECK (true);


--
-- Name: workflow_nodes; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.workflow_nodes ENABLE ROW LEVEL SECURITY;

--
-- Name: zzz_deprecated_storyboard_assets; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.zzz_deprecated_storyboard_assets ENABLE ROW LEVEL SECURITY;

--
-- Name: zzz_deprecated_storyboard_characters; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.zzz_deprecated_storyboard_characters ENABLE ROW LEVEL SECURITY;

--
-- Name: zzz_deprecated_storyboard_edges; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.zzz_deprecated_storyboard_edges ENABLE ROW LEVEL SECURITY;

--
-- Name: zzz_deprecated_storyboard_frame_characters; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.zzz_deprecated_storyboard_frame_characters ENABLE ROW LEVEL SECURITY;

--
-- Name: zzz_deprecated_storyboard_frames; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.zzz_deprecated_storyboard_frames ENABLE ROW LEVEL SECURITY;

--
-- Name: zzz_deprecated_storyboard_nodes; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.zzz_deprecated_storyboard_nodes ENABLE ROW LEVEL SECURITY;

--
-- Name: zzz_deprecated_storyboard_projects; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.zzz_deprecated_storyboard_projects ENABLE ROW LEVEL SECURITY;

--
-- Name: zzz_deprecated_storyboard_video_assets; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.zzz_deprecated_storyboard_video_assets ENABLE ROW LEVEL SECURITY;

--
-- Name: api_keys 用户可以创建自己的API密钥; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "用户可以创建自己的API密钥" ON public.api_keys FOR INSERT WITH CHECK ((( SELECT auth.uid() AS uid) = user_id));


--
-- Name: api_keys 用户可以删除自己的API密钥; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "用户可以删除自己的API密钥" ON public.api_keys FOR DELETE USING ((( SELECT auth.uid() AS uid) = user_id));


--
-- Name: user_profiles 用户可以插入自己的配置; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "用户可以插入自己的配置" ON public.user_profiles FOR INSERT WITH CHECK ((( SELECT auth.uid() AS uid) = id));


--
-- Name: api_keys 用户可以更新自己的API密钥; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "用户可以更新自己的API密钥" ON public.api_keys FOR UPDATE USING ((( SELECT auth.uid() AS uid) = user_id));


--
-- Name: user_profiles 用户可以更新自己的配置; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "用户可以更新自己的配置" ON public.user_profiles FOR UPDATE USING ((( SELECT auth.uid() AS uid) = id));


--
-- Name: api_key_logs 用户可以查看自己密钥的日志; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "用户可以查看自己密钥的日志" ON public.api_key_logs FOR SELECT USING ((EXISTS ( SELECT 1
   FROM public.api_keys
  WHERE ((api_keys.id = api_key_logs.api_key_id) AND (api_keys.user_id = ( SELECT auth.uid() AS uid))))));


--
-- Name: api_keys 用户可以查看自己的API密钥; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "用户可以查看自己的API密钥" ON public.api_keys FOR SELECT USING ((( SELECT auth.uid() AS uid) = user_id));


--
-- Name: user_profiles 用户可以查看自己的配置; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "用户可以查看自己的配置" ON public.user_profiles FOR SELECT USING ((( SELECT auth.uid() AS uid) = id));


--
-- Name: authors 认证用户可以插入作者; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "认证用户可以插入作者" ON public.authors FOR INSERT WITH CHECK ((( SELECT auth.role() AS role) = 'authenticated'::text));


--
-- Name: authors 认证用户可以查看作者; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY "认证用户可以查看作者" ON public.authors FOR SELECT USING ((( SELECT auth.role() AS role) = 'authenticated'::text));


--
-- PostgreSQL database dump complete
--


