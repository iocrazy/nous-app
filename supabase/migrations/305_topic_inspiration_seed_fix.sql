-- 305_topic_inspiration_seed_fix.sql
-- Fix Phase-1 global seed sources for prod reality (China-hosted NAS):
--   - hackernews / v2ex-share: unreachable even via the LAN proxy -> disable.
--   - marktechpost (rss): fetched by the BACKEND (no proxy) -> disable
--     (rss adapter code stays; only this foreign seed is disabled).
--   - Add reachable NewsNow sources (foreign-via-proxy + domestic-direct),
--     verified HTTP 200 with 20-30 items each from the newsnow container.
-- All rows are global (user_id IS NULL).

-- Disable the dead foreign seeds.
UPDATE signal_sources
SET enabled = false
WHERE user_id IS NULL
  AND (
    (kind = 'newsnow' AND config->>'platform_id' IN ('hackernews', 'v2ex-share'))
    OR (kind = 'rss' AND name = 'MarkTechPost')
  );

-- Add verified-working NewsNow sources (idempotent via NOT EXISTS on platform_id).
INSERT INTO signal_sources (user_id, kind, name, config, category)
SELECT NULL, 'newsnow', v.name, jsonb_build_object('platform_id', v.platform_id), v.category
FROM (VALUES
    ('Weibo Hot',        'weibo',                'industry'),
    ('Zhihu Hot',        'zhihu',                'industry'),
    ('36Kr',             '36kr-quick',           'industry'),
    ('IT Home',          'ithome',               'product'),
    ('Juejin',           'juejin',               'tips'),
    ('Bilibili Hot',     'bilibili-hot-search',  'product'),
    ('Douyin Hot',       'douyin',               'product')
) AS v(name, platform_id, category)
WHERE NOT EXISTS (
    SELECT 1 FROM signal_sources s
    WHERE s.user_id IS NULL
      AND s.kind = 'newsnow'
      AND s.config->>'platform_id' = v.platform_id
);

NOTIFY pgrst, 'reload schema';
