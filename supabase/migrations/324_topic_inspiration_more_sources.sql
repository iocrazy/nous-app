-- 324_topic_inspiration_more_sources.sql
-- Add same-beat NewsNow sources so a story lands on 3+ platforms (cross-source
-- heat). Diagnosis (2026-06-26): clustering params are fine — with only 8
-- heterogeneous sources the SAME news rarely appears on 3+ (92% of hotspots
-- have no cross-source twin even at 0.70 cosine). The real lever is source
-- OVERLAP, so add clusters that cover the same beat as the existing feed.
--
-- Every platform_id below was verified live (HTTP 200, 18-30 items) against the
-- prod newsnow container. All rows are global (user_id IS NULL), enabled.
-- tier convention (matches existing seed): 2 = news/media site, 3 = social /
-- community / aggregator. category ∈ scorer buckets (industry/product/tips).

INSERT INTO signal_sources (user_id, kind, name, config, category, tier, enabled)
SELECT NULL, 'newsnow', v.name,
       jsonb_build_object('platform_id', v.platform_id), v.category, v.tier, true
FROM (VALUES
    -- 科技/数码簇 — overlaps IT Home / 36Kr / Juejin
    ('Solidot',        'solidot',       'industry', 2),
    ('SSPAI',          'sspai',         'product',  2),
    ('Coolapk',        'coolapk',       'product',  3),
    ('Product Hunt',   'producthunt',   'product',  2),
    -- 财经簇 — dense mutual overlap (a finance story often hits all four)
    ('Wallstreet CN',  'wallstreetcn',  'industry', 2),
    ('Cailianshe',     'cls',           'industry', 2),
    ('Jin10',          'jin10',         'industry', 2),
    ('Xueqiu',         'xueqiu',        'industry', 3),
    -- 综合/社会簇 — overlaps Weibo / Douyin / Zhihu / Bilibili
    ('Baidu Hot',      'baidu',         'industry', 3),
    ('Toutiao',        'toutiao',       'industry', 3),
    ('The Paper',      'thepaper',      'industry', 2),
    -- 加料簇 — broaden social / news / dev coverage
    ('Kuaishou Hot',   'kuaishou',      'product',  3),
    ('Tieba Hot',      'tieba',         'industry', 3),
    ('Cankaoxiaoxi',   'cankaoxiaoxi',  'industry', 2),
    ('Nowcoder',       'nowcoder',      'tips',     3)
) AS v(name, platform_id, category, tier)
WHERE NOT EXISTS (
    SELECT 1 FROM signal_sources s
    WHERE s.user_id IS NULL
      AND s.kind = 'newsnow'
      AND s.config->>'platform_id' = v.platform_id
);

-- New rows expose immediately to the feed read path / admin list.
NOTIFY pgrst, 'reload schema';
