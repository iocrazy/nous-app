-- 221_business_tag_group.sql
--
-- Create the "商业" tag group, fix two awkward EN tag names, and move
-- the 3 finance/business tags into the new group. Sibling to mig 220
-- (Pipeline group) — pins right after it in the sidebar so the two
-- new groups sit together at the bottom.
--
-- Tag moves
-- ---------
--   Finance / 财经            (type=system, was in 科技)
--   quantisation / 量化       → renamed to "Quant"
--                              ("quantisation" is a signal-processing
--                              term; in this library 量化 means
--                              quantitative trading, so "Quant" fits.)
--   Stock speculation / 炒股  → renamed to "Stocks"
--                              ("speculation" carries a pejorative
--                              tilt; "Stocks" is the neutral term
--                              the user actually means.)
--
-- Group naming follows the user's existing convention (all current
-- groups are Chinese: 娱乐 / 生活 / 科技 / 创作 / 运动 / 情感 / 开发
-- / 设计). 商业 is broader than "finance" — leaves room for the user
-- to add 创业 / 营销 / etc. later without re-bucketing.
--
-- Idempotent: re-runs cleanly.

DO $$
DECLARE
  new_group_id BIGINT;
BEGIN
  -- Insert (or look up if already present) the 商业 group.
  INSERT INTO public.tag_groups (name, sort_order)
  SELECT '商业', COALESCE(MAX(sort_order), 0) + 1 FROM public.tag_groups
  ON CONFLICT DO NOTHING
  RETURNING id INTO new_group_id;

  IF new_group_id IS NULL THEN
    SELECT id INTO new_group_id FROM public.tag_groups WHERE name = '商业' LIMIT 1;
  END IF;

  IF new_group_id IS NULL THEN
    RAISE EXCEPTION '商业 tag_group could not be created or located';
  END IF;

  -- Rename misnamed tags. Skip the rename if the target name already
  -- exists (case-insensitive) for the same user_id — defense against
  -- the UNIQUE(name, type, user_id) constraint on re-run.
  UPDATE public.tags
     SET name = 'Quant'
   WHERE name = 'quantisation'
     AND NOT EXISTS (
       SELECT 1 FROM public.tags t2
       WHERE LOWER(t2.name) = 'quant'
         AND t2.type = public.tags.type
         AND COALESCE(t2.user_id::text, '') = COALESCE(public.tags.user_id::text, '')
         AND t2.id <> public.tags.id
     );

  UPDATE public.tags
     SET name = 'Stocks'
   WHERE name = 'Stock speculation'
     AND NOT EXISTS (
       SELECT 1 FROM public.tags t2
       WHERE LOWER(t2.name) = 'stocks'
         AND t2.type = public.tags.type
         AND COALESCE(t2.user_id::text, '') = COALESCE(public.tags.user_id::text, '')
         AND t2.id <> public.tags.id
     );

  -- Reassign group_id. Match by Chinese name to survive the EN
  -- renames above (and to be re-runnable after they've taken effect).
  UPDATE public.tags
     SET group_id = new_group_id
   WHERE name_zh IN ('财经', '量化', '炒股');
END $$;
