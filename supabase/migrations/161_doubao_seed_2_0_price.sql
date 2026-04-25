-- 161: Register price for the Doubao seed-2.0-pro model used by M3 persistent agents.
--
-- Both `summarize` and `analyze` agents use `doubao-seed-2-0-pro-260215` as
-- their primary model. Without a row in `ai_model_prices`, RunRecorder leaves
-- `cost_cents` NULL on every workforce-dispatched run. The Usage dashboard
-- therefore can't show cost rollups for M3 traffic.
--
-- Rates mirror the existing `doubao-pro` row (¥/cents per 1k tokens; the unit
-- is consistent across all rows in this table). When Volcengine publishes
-- official seed-2.0 pricing we should re-snapshot, but the order-of-magnitude
-- match means cost_cents stops being NULL today.

INSERT INTO ai_model_prices
    (model, provider, prompt_cents_per_1k, completion_cents_per_1k, effective_at)
VALUES
    ('doubao-seed-2-0-pro-260215', 'doubao', 0.080000, 0.200000, NOW())
ON CONFLICT (model, provider, effective_at) DO NOTHING;
