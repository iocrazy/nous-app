-- 502: rename step 2 of two — nous-qwen3-llm → nous-qwen3-8-27b, plus the
-- two 27B variants nous-engine now serves.
--
--   1. nous_models.name  nous-qwen3-llm → nous-qwen3-8-27b   (twin-guarded),
--      and its stale label ("Nous Qwen3 35B (LLM)" / "qwen3-6-35b") corrected
--   2. INSERT nous-qwen3-8-27b-orcarouter (256K) and nous-qwen3-8-27b-huihui (32K),
--      credential copied from the renamed row
--   3. the known persisted references to the old name (same set as mig 488 §3)
--   4. zero-price ai_model_prices rows for the two new catalog names
--
-- ⚠️ DEPLOY ORDER: THE ALIAS PR MUST BE IN PRODUCTION FIRST
-- ----------------------------------------------------------
-- Unlike mig 488 (mediahub-X ↔ nous-X), this rename is NOT a prefix swap, so the
-- original alias layer cannot resolve it. Step 1 is the explicit rename table in
-- `app.core.catalog_names` (PR A of this pair), which makes every by-name lookup
-- accept `nous-qwen3-llm` and `nous-qwen3-8-27b` in both directions, exact name
-- first. That PR must be deployed BEFORE this migration runs: the one in-use
-- canvas (and any external caller) still says `nous-qwen3-llm`, and without the
-- alias table it stops resolving the moment the row is renamed. With it, a
-- reference this migration misses — or one an old build writes afterwards —
-- keeps resolving. This migration makes the data canonical; it is not what keeps
-- anything working.
--
-- Idempotent and empty-DB safe (schema-drift replays it on a database with no
-- catalog rows): every UPDATE's predicate is false once applied, both INSERTs are
-- NOT EXISTS-guarded and copy from a row that a fresh database does not have, and
-- the reference mapping is derived from the catalog as it stands — on a fresh
-- database it is empty, so mig 454's seeded `nous-qwen3-llm` price row is left
-- exactly as it was.
--
-- No `SET ROLE` anywhere (CLAUDE.md: it drops privileges in CI). No trigger
-- suppression either: nous_models.updated_at moving is correct for a relabel.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Rename the base row, never onto an existing twin
-- ---------------------------------------------------------------------------
-- `name` is unique. If both spellings already exist as distinct rows, each keeps
-- its own name (the alias layer's "exact wins" rule resolves each to itself) and
-- the pair is reported for a human to reconcile.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.nous_models WHERE name = 'nous-qwen3-llm')
       AND EXISTS (SELECT 1 FROM public.nous_models WHERE name = 'nous-qwen3-8-27b') THEN
        RAISE NOTICE '[502] nous-qwen3-llm not renamed: nous-qwen3-8-27b already exists as a separate row';
    END IF;
END $$;

UPDATE public.nous_models
   SET name         = 'nous-qwen3-8-27b',
       display_name = 'Nous Qwen3 8 27B',
       description  = 'Self-hosted qwen3-8-27b (abliterated AWQ) on nous-engine'
 WHERE name = 'nous-qwen3-llm'
   AND NOT EXISTS (
       SELECT 1 FROM public.nous_models WHERE name = 'nous-qwen3-8-27b'
   );

-- A row someone already renamed by hand still carries the stale label; fix the
-- label only while it is the stale one, so an admin-chosen label survives.
-- context_window_tokens stays NULL: the real vLLM --max-model-len is unconfirmed.
UPDATE public.nous_models
   SET display_name = 'Nous Qwen3 8 27B',
       description  = 'Self-hosted qwen3-8-27b (abliterated AWQ) on nous-engine'
 WHERE name = 'nous-qwen3-8-27b'
   AND display_name = 'Nous Qwen3 35B (LLM)';

-- ---------------------------------------------------------------------------
-- 2. The two variants — same engine, same InstanceApiKey, same base_url
-- ---------------------------------------------------------------------------
-- Everything except identity and window is copied from the renamed base row;
-- `api_key` is already in the `enc:v1:` form the repository reveals on read.
-- nous-engine has already granted this key both services (2026-09-24 check of
-- its api_key_grants). No base row (fresh database) → nothing to copy → no-op.
INSERT INTO public.nous_models
    (name, display_name, type, actual_provider, actual_model, api_key, app_id,
     base_url, pricing_type, pricing_value, is_enabled, sort_order, description,
     owner_user_id, context_window_tokens)
SELECT v.name,
       v.display_name,
       'llm',
       'nous',
       v.actual_model,
       b.api_key,
       b.app_id,
       b.base_url,
       b.pricing_type,
       b.pricing_value,
       b.is_enabled,
       b.sort_order,
       v.description,
       b.owner_user_id,
       v.window_tokens
  FROM public.nous_models b
 CROSS JOIN (VALUES
       ('nous-qwen3-8-27b-orcarouter', 'Nous Qwen3 8 27B OrcaRouter (256K)',
        'qwen3-8-27b-orcarouter', 262144,
        'Self-hosted qwen3-8-27b OrcaRouter variant (256K context) on nous-engine'),
       ('nous-qwen3-8-27b-huihui', 'Nous Qwen3 8 27B Huihui (32K)',
        'qwen3-8-27b-huihui', 32768,
        'Self-hosted qwen3-8-27b Huihui variant (32K context) on nous-engine')
     ) AS v(name, display_name, actual_model, window_tokens, description)
 WHERE b.name = 'nous-qwen3-8-27b'
   AND NOT EXISTS (
       SELECT 1 FROM public.nous_models t WHERE t.name = v.name
   );

-- ---------------------------------------------------------------------------
-- 3. Persisted references — exact tokens only
-- ---------------------------------------------------------------------------
-- The single pair applies only when the catalog now resolves `nous-qwen3-llm`
-- to the renamed row: a row named nous-qwen3-8-27b exists and NO row named
-- nous-qwen3-llm does. In the twin case the old name is its own row, so its
-- references are left pointing at it; on a fresh database neither row exists
-- and nothing is rewritten.
--
-- Two token forms: the bare catalog name, and the `nous:<name>` picker form.
-- Matching is whole-value equality — never a substring replace.
-- pg_temp-qualified drops let the body be replayed twice inside one
-- transaction (how the integration test proves idempotency).
DROP TABLE IF EXISTS pg_temp._mig502_token_map;

CREATE TEMP TABLE _mig502_token_map ON COMMIT DROP AS
SELECT t.form, t.old_token, t.new_token
  FROM (VALUES
        ('bare',  'nous-qwen3-llm',      'nous-qwen3-8-27b'),
        ('colon', 'nous:nous-qwen3-llm', 'nous:nous-qwen3-8-27b')
       ) AS t(form, old_token, new_token)
 WHERE EXISTS (SELECT 1 FROM public.nous_models WHERE name = 'nous-qwen3-8-27b')
   AND NOT EXISTS (SELECT 1 FROM public.nous_models WHERE name = 'nous-qwen3-llm');

-- 3a. system_settings — the model-key allowlist of mig 488 §3a (jsonb strings
--     only; api_key and other secrets never match by key). Production today:
--     graph_extractor_model = "nous-qwen3-llm".
UPDATE public.system_settings s
   SET value = to_jsonb(m.new_token)
  FROM _mig502_token_map m
 WHERE (   s.key IN ('maintenance_llm_model', 'graph_embedder_model', 'graph_extractor_model')
        OR (left(s.key, 10) = 'ai_module.' AND right(s.key, 6) = '.model'))
   AND jsonb_typeof(s.value) = 'string'
   AND s.value #>> '{}' = m.old_token;

-- 3b. user_settings…task_assignment.* — ONLY the `nous:<name>` form, as in 488
--     §3b (a bare value feeds ai_router's startswith("nous-") billing check;
--     that check is out of scope, so bare values are not touched).
UPDATE public.user_settings us
   SET settings_json = jsonb_set(
           us.settings_json,
           '{ai_settings,task_assignment}',
           (SELECT jsonb_object_agg(
                       e.key,
                       COALESCE(to_jsonb(m.new_token), e.value))
              FROM jsonb_each(us.settings_json #> '{ai_settings,task_assignment}') e
              LEFT JOIN _mig502_token_map m
                     ON m.form = 'colon'
                    AND jsonb_typeof(e.value) = 'string'
                    AND e.value #>> '{}' = m.old_token))
 WHERE jsonb_typeof(us.settings_json #> '{ai_settings,task_assignment}') = 'object'
   AND EXISTS (
       SELECT 1
         FROM jsonb_each(us.settings_json #> '{ai_settings,task_assignment}') e
         JOIN _mig502_token_map m
           ON m.form = 'colon'
          AND jsonb_typeof(e.value) = 'string'
          AND e.value #>> '{}' = m.old_token
   );

-- 3c. user_settings…ai_providers.nous.disabled_models[] — bare names only
--     (platform_model_visibility resolves them by name). Order preserved.
UPDATE public.user_settings us
   SET settings_json = jsonb_set(
           us.settings_json,
           '{ai_settings,ai_providers,nous,disabled_models}',
           (SELECT jsonb_agg(COALESCE(to_jsonb(m.new_token), el.value) ORDER BY el.ord)
              FROM jsonb_array_elements(
                       us.settings_json #> '{ai_settings,ai_providers,nous,disabled_models}'
                   ) WITH ORDINALITY AS el(value, ord)
              LEFT JOIN _mig502_token_map m
                     ON m.form = 'bare'
                    AND jsonb_typeof(el.value) = 'string'
                    AND el.value #>> '{}' = m.old_token))
 WHERE jsonb_typeof(us.settings_json #> '{ai_settings,ai_providers,nous,disabled_models}') = 'array'
   AND EXISTS (
       SELECT 1
         FROM jsonb_array_elements(
                  us.settings_json #> '{ai_settings,ai_providers,nous,disabled_models}'
              ) AS el(value)
         JOIN _mig502_token_map m
           ON m.form = 'bare'
          AND jsonb_typeof(el.value) = 'string'
          AND el.value #>> '{}' = m.old_token
   );

-- 3d. Agent model fields (text / text[]), both token forms.
UPDATE public.ai_agents a
   SET model = m.new_token
  FROM _mig502_token_map m
 WHERE a.model = m.old_token;

UPDATE public.ai_agents a
   SET fallback_models = ARRAY(
           SELECT COALESCE(m.new_token, f.v)
             FROM unnest(a.fallback_models) WITH ORDINALITY AS f(v, ord)
             LEFT JOIN _mig502_token_map m ON m.old_token = f.v
            ORDER BY f.ord)
 WHERE a.fallback_models && ARRAY(SELECT old_token FROM _mig502_token_map);

UPDATE public.agent_overrides o
   SET model = m.new_token
  FROM _mig502_token_map m
 WHERE o.model = m.old_token;

UPDATE public.agent_overrides o
   SET fallback_models = ARRAY(
           SELECT COALESCE(m.new_token, f.v)
             FROM unnest(o.fallback_models) WITH ORDINALITY AS f(v, ord)
             LEFT JOIN _mig502_token_map m ON m.old_token = f.v
            ORDER BY f.ord)
 WHERE o.fallback_models && ARRAY(SELECT old_token FROM _mig502_token_map);

-- 3e. ai_model_prices keyed by the catalog NAME. RunRecorder._snapshot_price
--     matches `model` exactly with no alias, so a price row left on the old
--     name stops pricing runs of the renamed row (and admin tags it `missing`).
--     Guarded against the (model, provider, effective_at) unique key.
UPDATE public.ai_model_prices p
   SET model = m.new_token
  FROM _mig502_token_map m
 WHERE m.form = 'bare'
   AND p.model = m.old_token
   AND NOT EXISTS (
       SELECT 1 FROM public.ai_model_prices q
        WHERE q.model = m.new_token
          AND q.provider = p.provider
          AND q.effective_at = p.effective_at
   );

-- Deliberately NOT rewritten (records, or resolved through the alias layer):
--   * canvases.nodes_json        — node JSON; provider_slug is resolved by
--                                  find_row_by_catalog_name at use time.
--   * ai_agent_versions.model    — history snapshots.
--   * agent_runs / ai_usage_logs / ai_usage_hourly / point_transactions /
--     run_deliverables / generated_media / search_docs `.model` — what a past
--     run actually recorded.

-- ---------------------------------------------------------------------------
-- 4. Zero prices for the two new catalog names
-- ---------------------------------------------------------------------------
-- The self-hosted path records runs with provider NULL, so the recorder looks
-- the price up by model (the catalog name) alone; without a row the run's cost
-- is NULL and admin shows the model as unpriced. Only for rows that exist, so a
-- fresh database stays untouched. Fixed effective_at makes ON CONFLICT the
-- idempotency guard.
INSERT INTO public.ai_model_prices
    (model, provider, prompt_cents_per_1k, completion_cents_per_1k,
     supports_vision, effective_at)
SELECT n.name, 'nous', 0, 0, FALSE, TIMESTAMPTZ '2026-09-24 00:00:00+00'
  FROM public.nous_models n
 WHERE n.name IN ('nous-qwen3-8-27b-orcarouter', 'nous-qwen3-8-27b-huihui')
ON CONFLICT (model, provider, effective_at) DO NOTHING;

COMMIT;

NOTIFY pgrst, 'reload schema';
