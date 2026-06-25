-- 318_hotspot_scoring_pipeline.sql
-- Phase 1 of the rubric scoring pipeline (see Notion Q&A "AI 热点打分流水线"):
-- the LLM scores raw dimensions, code computes the composite + featured cutoff.
--
-- Adds:
--   signal_sources.tier  — credibility prior (1=official/primary, 2=default,
--                          3=generic aggregator / social hot-list). Code uses
--                          it as a multiplier so source fame can't inflate a
--                          weak item's score.
--   hotspots.score_dims  — the LLM's raw per-dimension scores (jsonb), persisted
--                          so a later calibration/learning layer (Phase 3) has
--                          training data. The composite still lives in `score`.

ALTER TABLE signal_sources
    ADD COLUMN IF NOT EXISTS tier SMALLINT NOT NULL DEFAULT 2
        CHECK (tier IN (1, 2, 3));

ALTER TABLE hotspots
    ADD COLUMN IF NOT EXISTS score_dims JSONB;

-- Seed sensible tiers for known system sources (UPDATE-by-name is a no-op when
-- a source isn't present, so this is safe across environments). Everything else
-- stays at the default tier 2.
-- Tier 3 — high-volume social/general hot-lists (noisier).
UPDATE signal_sources SET tier = 3
    WHERE user_id IS NULL AND name IN (
        'Weibo Hot', 'Douyin Hot', 'Bilibili Hot', 'Zhihu Hot', 'V2EX Share'
    );
-- Tier 1 — primary sources (papers / official research feeds).
UPDATE signal_sources SET tier = 1
    WHERE user_id IS NULL AND (
        name ILIKE '%papers%' OR name ILIKE '%arxiv%' OR name ILIKE '%官方%'
    );

-- PostgREST: reload schema cache so the new columns are visible.
NOTIFY pgrst, 'reload schema';
