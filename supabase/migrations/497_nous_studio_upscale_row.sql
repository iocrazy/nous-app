-- 497: catalog row for nous-engine's SeedVR2 super-resolution service.
--
-- WHY
-- ---
-- The canvas 放大 route (POST /api/v1/generated-media/{id}/upscale) used to
-- hard-wire the dreamina CLI. It now resolves the first enabled
-- upscale-capable image row, nous-engine before jimeng-cli
-- (db_registry.resolve_upscale_provider). This row is what makes the
-- self-hosted engine the default upscaler.
--
-- CREDENTIAL
-- ----------
-- Copied from an existing platform nous-engine row (same engine, same
-- InstanceApiKey, same base_url). `api_key` is stored as-is — it is already
-- in the `enc:v1:` form the repository reveals on read. The key must ALSO be
-- authorised for `studio-upscale` in the nous-engine admin; until it is, the
-- engine answers 404 model_not_found and the route returns 502 naming it
-- (docs/runbook/nous-engine-image-bridge.md).
--
-- IDEMPOTENT / FRESH DB
-- ---------------------
-- NOT EXISTS guard on the name. On a fresh database (schema-drift's ephemeral
-- one) there is no nous-engine row to copy from, so the SELECT yields nothing
-- and this is a no-op — correct: no engine, no row.
--
-- DEPLOY ORDER IS FREE
-- --------------------
--   * migration first — an old build resolves images via
--     resolve_generation_protocol, which skips `nous` (no generation_family
--     yet) and raises for this row only if it is picked. It never is by
--     default: jimeng rows win the default pick, and the old upscale route
--     never reads the catalog. The old picker WOULD list it for a short
--     window; choosing it fails loudly ("No image provider implementation").
--   * code first — no nous image row exists, upscale falls through to
--     jimeng-cli exactly as before.

BEGIN;

INSERT INTO public.nous_models
    (name, display_name, type, actual_provider, actual_model, api_key,
     base_url, pricing_type, pricing_value, is_enabled, sort_order,
     description)
SELECT 'nous-studio-upscale',
       'Nous Studio Upscale (SeedVR2)',
       'image',
       'nous',
       'studio-upscale',
       src.api_key,
       src.base_url,
       'per_request',
       0,
       TRUE,
       50,
       'SeedVR2 super-resolution on nous-engine (studio-upscale workflow)'
  FROM public.nous_models AS src
 WHERE src.actual_provider = 'nous'
   AND src.owner_user_id IS NULL
   AND coalesce(src.base_url, '') <> ''
   AND NOT EXISTS (
       SELECT 1 FROM public.nous_models WHERE name = 'nous-studio-upscale'
   )
 -- The ASR row first (the credential this row was specified against), then
 -- any other platform nous-engine row — same engine, same key.
 ORDER BY (src.name IN ('nous-moss-asr', 'mediahub-moss-asr')) DESC,
          src.sort_order,
          src.id
 LIMIT 1;

COMMIT;
