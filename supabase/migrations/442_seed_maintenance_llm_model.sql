-- 442: seed system_settings.maintenance_llm_model so the maintenance tier
-- (compaction / session memory / distillation) has an admin-visible row.
--
-- get_maintenance_model() has read this key since the maintenance tier was
-- introduced, but the row was never seeded, so Admin → Settings never showed
-- it — the only way to change the model was a hand-written UPDATE. The
-- governance bundle (GET/PUT /admin/settings/ai-governance) now exposes it
-- with a catalog dropdown; this row makes the generic Settings list show the
-- current value too.
--
-- Blank value = "use DEFAULT_MAINTENANCE_MODEL" (the resolver treats "" as
-- unset), so seeding blank changes nothing at runtime.
INSERT INTO system_settings (key, value, description)
VALUES (
  'maintenance_llm_model',
  to_jsonb(''::text),
  'Platform-catalog model for maintenance LLM calls (conversation compaction, session memory, distillation). Blank = platform default. Set via Admin → Settings → AI Governance.'
)
ON CONFLICT (key) DO NOTHING;
