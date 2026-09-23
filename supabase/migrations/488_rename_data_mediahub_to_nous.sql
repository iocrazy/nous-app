-- 488: rename step 2 of two — move the persisted `mediahub` spellings to `nous`.
--
-- Three data renames, one schema cleanup:
--   1. resources.mime_type  application/x-mediahub-gallery → application/x-nous-gallery
--   2. nous_models.name     mediahub-X → nous-X   (twin-guarded)
--   3. the known persisted references to a renamed catalog name
--   4. DROP the `mediahub_models` compatibility view from mig 485
--
-- ⚠️ WHY THIS IS SAFE IN EITHER DEPLOY ORDER
-- ------------------------------------------
-- `run-migration.yml` and `deploy-gpu.yml` fire independently (CLAUDE.md: "migration
-- 与代码部署无顺序保证"). Both step-1 PRs are already in production, so the code on
-- BOTH sides of this migration accepts both spellings:
--   * #2388 — every gallery read / filter accepts GALLERY_MIMES (both values), the
--     frontend's isGalleryMime too. Only the write value changes in this PR.
--   * #2390 — NousModelRepository.get_by_name resolves mediahub-X ↔ nous-X in both
--     directions (exact name wins), and admin create/rename of a colliding twin 409s.
-- So a row or reference this migration misses keeps resolving, and a reference the
-- old backend writes after this ran keeps resolving too. This migration makes the
-- data canonical; it is not what keeps anything working.
--
-- Idempotent and empty-DB safe (the schema-drift gate replays it on a database
-- with no rows): every statement is a guarded UPDATE whose predicate is false once
-- applied, and the view drop checks relkind.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Gallery MIME — without bumping resources.updated_at
-- ---------------------------------------------------------------------------
-- An internal data fix is not a user edit, so it must not move the row in
-- "recently updated" orderings. CLAUDE.md's sanctioned way is a transaction-local
-- replica role (never `SET ROLE`): `SET LOCAL` so it cannot leak into the next
-- migration in run-migration.yml's single psql session, and it is reset to
-- `origin` right after this one statement, so sections 2–4 run with every
-- trigger live.
--
-- What replica suppresses here, checked against schema_baseline.sql (prod at
-- watermark 364) + every migration above it: the ONLY trigger on public.resources
-- is `update_resources_updated_at` (BEFORE UPDATE → update_updated_at_column()).
-- No search-column / mirror-table trigger exists (search_docs is maintained by
-- application code, and carries no mime column). RI triggers are skipped as well,
-- but mime_type is not a key column, so there is nothing for them to check.
SET LOCAL session_replication_role = replica;

UPDATE public.resources
   SET mime_type = 'application/x-nous-gallery'
 WHERE mime_type = 'application/x-mediahub-gallery';

SET LOCAL session_replication_role = origin;

-- ---------------------------------------------------------------------------
-- 2. Catalog rows: mediahub-X → nous-X, never onto an existing twin
-- ---------------------------------------------------------------------------
-- `name` carries a unique index. If both spellings already exist as distinct rows,
-- each keeps its own name (the alias layer's "exact wins" rule keeps resolving
-- each to itself) and the pair is reported for a human to reconcile.
DO $$
DECLARE
    twin TEXT;
BEGIN
    FOR twin IN
        SELECT n.name
          FROM public.nous_models n
         WHERE left(n.name, 9) = 'mediahub-'
           AND length(n.name) > 9
           AND EXISTS (
               SELECT 1 FROM public.nous_models t
                WHERE t.name = 'nous-' || substr(n.name, 10)
           )
    LOOP
        RAISE NOTICE '[488] % not renamed: nous-% already exists as a separate row',
            twin, substr(twin, 10);
    END LOOP;
END $$;

UPDATE public.nous_models n
   SET name = 'nous-' || substr(n.name, 10)
 WHERE left(n.name, 9) = 'mediahub-'   -- left(), not LIKE: '_' / '%' are not wildcards here
   AND length(n.name) > 9
   AND NOT EXISTS (
       SELECT 1 FROM public.nous_models t
        WHERE t.name = 'nous-' || substr(n.name, 10)
   );

-- ---------------------------------------------------------------------------
-- 3. Persisted references — ONE derived mapping, exact tokens only
-- ---------------------------------------------------------------------------
-- The mapping is derived from the catalog as it now stands, not typed out:
-- every row named nous-X for which NO row named mediahub-X exists. That is
-- exactly how the alias layer (app.core.catalog_names) resolves `mediahub-X`
-- today, so rewriting a reference with it changes the spelling, never the row it
-- resolves to. It covers the rows renamed in section 2, plus rows that were
-- already nous-X (a stale `mediahub-X` reference to those resolved by alias
-- only). A twin pair is excluded — there `mediahub-X` is its own row.
--
-- Deriving from the current state (rather than "rows renamed in this run") also
-- makes a second run self-consistent: it re-derives the same mapping and finds
-- nothing left to rewrite.
--
-- Two token forms: the bare catalog name, and the `nous:<name>` picker form
-- (user task assignment / agent pickers). Matching is whole-value equality —
-- never a substring replace.
-- pg_temp-qualified so the drop can never reach a real table of the same name.
-- (ON COMMIT DROP already cleans up; the explicit drop lets the body be replayed
-- twice inside one transaction, which is how the integration test proves
-- idempotency.)
DROP TABLE IF EXISTS pg_temp._mig488_token_map;
DROP TABLE IF EXISTS pg_temp._mig488_catalog_map;

CREATE TEMP TABLE _mig488_catalog_map ON COMMIT DROP AS
SELECT 'mediahub-' || substr(n.name, 6) AS old_name,
       n.name                          AS new_name
  FROM public.nous_models n
 WHERE left(n.name, 5) = 'nous-'
   AND length(n.name) > 5
   AND NOT EXISTS (
       SELECT 1 FROM public.nous_models t
        WHERE t.name = 'mediahub-' || substr(n.name, 6)
   );

CREATE TEMP TABLE _mig488_token_map ON COMMIT DROP AS
SELECT 'bare' AS form, old_name AS old_token, new_name AS new_token
  FROM _mig488_catalog_map
UNION ALL
SELECT 'colon', 'nous:' || old_name, 'nous:' || new_name
  FROM _mig488_catalog_map;

-- 3a. system_settings (value is jsonb; a model setting is a jsonb STRING).
--     Keys: maintenance_llm_model, ai_module.<module>.model (embedding,
--     transcription, summarization, topic_scorer, …), and the two graph-memory
--     model keys — graph_memory._apply_catalog resolves both extractor and
--     embedder "model" against the catalog. Only jsonb strings are touched;
--     ai_module.*.api_key and other secrets are never matched by key.
UPDATE public.system_settings s
   SET value = to_jsonb(m.new_token)
  FROM _mig488_token_map m
 WHERE (   s.key IN ('maintenance_llm_model', 'graph_embedder_model', 'graph_extractor_model')
        OR (left(s.key, 10) = 'ai_module.' AND right(s.key, 6) = '.model'))
   AND jsonb_typeof(s.value) = 'string'
   AND s.value #>> '{}' = m.old_token;

-- 3b. user_settings.settings_json.ai_settings.task_assignment.* — ONLY the
--     `nous:mediahub-X` form. A BARE `mediahub-X` value is deliberately left:
--     ai_router's transcription billing charges when the bare value
--     startswith("nous-"), so rewriting it would silently start charging a user
--     who is not charged today. That check is out of scope here.
UPDATE public.user_settings us
   SET settings_json = jsonb_set(
           us.settings_json,
           '{ai_settings,task_assignment}',
           (SELECT jsonb_object_agg(
                       e.key,
                       COALESCE(to_jsonb(m.new_token), e.value))
              FROM jsonb_each(us.settings_json #> '{ai_settings,task_assignment}') e
              LEFT JOIN _mig488_token_map m
                     ON m.form = 'colon'
                    AND jsonb_typeof(e.value) = 'string'
                    AND e.value #>> '{}' = m.old_token))
 WHERE jsonb_typeof(us.settings_json #> '{ai_settings,task_assignment}') = 'object'
   AND EXISTS (
       SELECT 1
         FROM jsonb_each(us.settings_json #> '{ai_settings,task_assignment}') e
         JOIN _mig488_token_map m
           ON m.form = 'colon'
          AND jsonb_typeof(e.value) = 'string'
          AND e.value #>> '{}' = m.old_token
   );

-- 3c. user_settings.settings_json.ai_settings.ai_providers.nous.disabled_models[]
--     — bare catalog names (platform_model_visibility resolves them by name).
--     Order preserved; non-string elements left as they are.
UPDATE public.user_settings us
   SET settings_json = jsonb_set(
           us.settings_json,
           '{ai_settings,ai_providers,nous,disabled_models}',
           (SELECT jsonb_agg(COALESCE(to_jsonb(m.new_token), el.value) ORDER BY el.ord)
              FROM jsonb_array_elements(
                       us.settings_json #> '{ai_settings,ai_providers,nous,disabled_models}'
                   ) WITH ORDINALITY AS el(value, ord)
              LEFT JOIN _mig488_token_map m
                     ON m.form = 'bare'
                    AND jsonb_typeof(el.value) = 'string'
                    AND el.value #>> '{}' = m.old_token))
 WHERE jsonb_typeof(us.settings_json #> '{ai_settings,ai_providers,nous,disabled_models}') = 'array'
   AND EXISTS (
       SELECT 1
         FROM jsonb_array_elements(
                  us.settings_json #> '{ai_settings,ai_providers,nous,disabled_models}'
              ) AS el(value)
         JOIN _mig488_token_map m
           ON m.form = 'bare'
          AND jsonb_typeof(el.value) = 'string'
          AND el.value #>> '{}' = m.old_token
   );

-- 3d. Agent model fields (text / text[]). resolve_nous_model looks these up by
--     catalog name; both token forms are rewritten (neither feeds a billing check).
UPDATE public.ai_agents a
   SET model = m.new_token
  FROM _mig488_token_map m
 WHERE a.model = m.old_token;

UPDATE public.ai_agents a
   SET fallback_models = ARRAY(
           SELECT COALESCE(m.new_token, f.v)
             FROM unnest(a.fallback_models) WITH ORDINALITY AS f(v, ord)
             LEFT JOIN _mig488_token_map m ON m.old_token = f.v
            ORDER BY f.ord)
 WHERE a.fallback_models && ARRAY(SELECT old_token FROM _mig488_token_map);

UPDATE public.agent_overrides o
   SET model = m.new_token
  FROM _mig488_token_map m
 WHERE o.model = m.old_token;

UPDATE public.agent_overrides o
   SET fallback_models = ARRAY(
           SELECT COALESCE(m.new_token, f.v)
             FROM unnest(o.fallback_models) WITH ORDINALITY AS f(v, ord)
             LEFT JOIN _mig488_token_map m ON m.old_token = f.v
            ORDER BY f.ord)
 WHERE o.fallback_models && ARRAY(SELECT old_token FROM _mig488_token_map);

-- 3e. ai_model_prices rows keyed by a catalog NAME. The self-hosted
--     OpenAI-compatible path records the catalog name as the run's model
--     (model_pricing_coverage.py), and RunRecorder._snapshot_price matches
--     `model` exactly with no alias — so a price row left on mediahub-X would
--     stop pricing runs of the renamed row. Most rows are keyed by the upstream
--     actual_model id and never match. Guarded against the
--     (model, provider, effective_at) unique key.
UPDATE public.ai_model_prices p
   SET model = m.new_token
  FROM _mig488_token_map m
 WHERE m.form = 'bare'
   AND p.model = m.old_token
   AND NOT EXISTS (
       SELECT 1 FROM public.ai_model_prices q
        WHERE q.model = m.new_token
          AND q.provider = p.provider
          AND q.effective_at = p.effective_at
   );

-- Deliberately NOT rewritten (the alias layer keeps resolving them, and they are
-- records rather than configuration):
--   * canvases.nodes_json        — node JSON; model names inside are resolved by
--                                  find_row_by_catalog_name at use time.
--   * ai_agent_versions.model    — history snapshots; rewriting history is wrong.
--   * agent_runs / ai_usage_logs / ai_usage_hourly / point_transactions /
--     run_deliverables / generated_media / search_docs `.model` — what a past run
--     actually recorded.
--   * provider_pricing.model_slug — no reader in backend/app.

-- ---------------------------------------------------------------------------
-- 4. Drop mig 485's compatibility view
-- ---------------------------------------------------------------------------
-- Zero readers left: grep over backend/app, backend/tests, backend/scripts,
-- backend/seeds, frontend, admin, deploy, scripts, .github finds only migration
-- files and prose. Only a VIEW is dropped — if `mediahub_models` is somehow a real
-- table, leave it alone and say so. No CASCADE: a dependent object would make this
-- fail loudly, which is what we want.
DO $$
DECLARE
    kind "char";
BEGIN
    SELECT c.relkind INTO kind
      FROM pg_class c
     WHERE c.relname = 'mediahub_models'
       AND c.relnamespace = 'public'::regnamespace;

    IF kind IS NULL THEN
        RAISE NOTICE '[488] public.mediahub_models already gone';
    ELSIF kind = 'v' THEN
        DROP VIEW public.mediahub_models;
        RAISE NOTICE '[488] dropped compatibility view public.mediahub_models';
    ELSE
        RAISE NOTICE '[488] public.mediahub_models has relkind % (not a view) — left untouched', kind;
    END IF;
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';
