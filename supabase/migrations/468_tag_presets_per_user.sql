-- 468_tag_presets_per_user.sql
--
-- Retire "system tags". They were never the system's — they are the INITIAL
-- tags a user is handed, and from here on each user owns their own copy.
--
-- User's ruling, 2026-09-14: 以后不要系统标签了，就是初始化标签，然后用户都能修改。
--
-- WHAT WAS WRONG
-- ==============
-- The 42 `type='system'` rows are ONE globally shared set (`user_id IS NULL`,
-- visible to everyone through the `tags_select` policy's `type IN
-- ('system','time')` arm). They are read-only today, and the read-only-ness was
-- load-bearing for two different reasons that got conflated:
--
--   1. Automation looked them up by their English display name, so a rename
--      silently broke the AI pipeline. **Fixed already** by mig 467 (`slug`) —
--      that is no longer a reason to forbid edits.
--   2. They are SHARED. Unlocking the shared rows would mean one user's rename
--      is every user's rename. That is the reason this migration exists: the
--      answer is not "lock them", it is "stop sharing them".
--
-- WHAT THIS DOES
-- ==============
--   • `tag_presets` — the template list, seeded here as literal rows. It is not
--     a user-facing table; nothing reads it except the seeding function.
--   • `seed_initial_tags(uuid)` — hand one user their own copy of every preset.
--   • `handle_new_user()` — call it, so every new signup starts stocked.
--   • Backfill every existing user, re-point their `resource_tags` /
--     `note_tags` onto their own copies, then delete the 42 shared rows.
--   • `uniq_tags_slug` → `(user_id, slug)`: a slug is now unique PER USER.
--   • RLS + `merge_tags` drop their `type = 'user'` guards — with no shared
--     rows left, "is it type=user" was only ever a proxy for "is it yours",
--     and `user_id = auth.uid()` says that directly.
--
-- ORDERING SAFETY
-- ===============
-- `run-migration.yml` and `deploy-gpu.yml` fire independently (CLAUDE.md, 已知
-- 缺口), so this may land before OR after the backend that reads it. It is safe
-- either way because the backend went out FIRST (PR #2297): every automation
-- lookup already asks for a specific user's copy and only falls back to the
-- shared row, via `ORDER BY tags.user_id IS NULL ASC`. Before this migration
-- that fallback is the only row; after it, it finds nothing and the user's own
-- copy wins. Neither order has a window where the wrong tag gets attached.
--
-- IDEMPOTENT
-- ==========
-- Re-running is a no-op: the seed is ON CONFLICT DO NOTHING, the re-point and
-- delete steps JOIN on `type='system'` rows that no longer exist, and both
-- index/policy steps are DROP-then-CREATE. (Renames can make run-migration.yml
-- treat a file as new — see the 466 collision this branch already hit.)

-- ---------------------------------------------------------------------------
-- 1. The template list
-- ---------------------------------------------------------------------------
-- Literal rows, not `SELECT ... FROM tags WHERE type='system'`. The preset list
-- is a product decision and belongs in version control; deriving it from
-- whatever a given database happens to hold would make a fresh install and prod
-- disagree silently, and would leave CI's ephemeral schema with an empty list
-- that proves nothing.

CREATE TABLE IF NOT EXISTS public.tag_presets (
  slug           text PRIMARY KEY,
  name           varchar(50) NOT NULL,
  name_zh        varchar(50),
  color          varchar(20),
  icon           varchar(50),
  group_name     varchar(50),
  sort_order     integer NOT NULL DEFAULT 0,
  prompt_trigger boolean NOT NULL DEFAULT false
);

COMMENT ON TABLE public.tag_presets IS
  'The initial tags every user is handed at signup. A template, not a tag: no '
  'user ever sees a row here, and editing one changes nobody''s library — it '
  'only changes what the NEXT new user starts with.';

-- `group_name` is text, not an FK to tag_groups: groups are matched by name at
-- seed time so a preset survives a database where that group row does not exist
-- yet (fresh install, CI's ephemeral schema). A missing group means the seeded
-- tag lands ungrouped, which is recoverable; a failed FK means no tags at all.
COMMENT ON COLUMN public.tag_presets.group_name IS
  'Resolved to tag_groups.id by NAME at seed time; unmatched = ungrouped.';

INSERT INTO public.tag_presets
  (slug, name, name_zh, color, icon, group_name, sort_order, prompt_trigger)
VALUES
  -- AI pipeline triggers (mig 183/220). These three slugs are load-bearing:
  -- app/api/media_fetch_helpers.py::INTENT_TAG_SLUGS and
  -- app/tasks/download_helpers.py both key off them.
  ('transcript',      'Transcript',      '转录',     '#6366f1', 'mic',       'Pipeline', 0, false),
  ('summary',         'Summary',         '总结',     '#10b981', 'file-text', 'Pipeline', 0, false),
  ('analyze',         'Analyze',         '分析',     '#f59e0b', 'eye',       'Pipeline', 0, false),
  -- Auto-classification targets (mig 012 seed; the keys of
  -- classification_service.KEYWORD_MAPPING). Renaming one is now free.
  ('food',            'Food',            '美食',     '#ef4444', NULL, '生活', 0, false),
  ('tutorial',        'Tutorial',        '教程',     '#3b82f6', NULL, '科技', 0, false),
  ('comedy',          'Comedy',          '搞笑',     '#eab308', NULL, '娱乐', 0, false),
  ('dance',           'Dance',           '舞蹈',     '#ec4899', NULL, '娱乐', 0, false),
  ('music',           'Music',           '音乐',     '#8b5cf6', NULL, '娱乐', 0, false),
  ('beauty',          'Beauty',          '颜值',     '#f472b6', NULL, '生活', 0, false),
  ('fashion',         'Fashion',         '时尚',     '#06b6d4', NULL, '生活', 0, false),
  ('gaming',          'Gaming',          '游戏',     '#22c55e', NULL, '娱乐', 0, false),
  ('pets',            'Pets',            '宠物',     '#f97316', NULL, '生活', 0, false),
  ('travel',          'Travel',          '旅行',     '#14b8a6', NULL, '生活', 0, false),
  ('tech',            'Tech',            '科技',     '#6366f1', NULL, '科技', 0, false),
  ('sports',          'Sports',          '运动',     '#84cc16', NULL, '运动', 0, false),
  ('vlog',            'Vlog',            '日常',     '#a855f7', NULL, '生活', 0, false),
  -- Curated vocabulary. No code references these; they are here so a new user
  -- starts with a usable set rather than an empty sidebar.
  ('drama',           'Drama',           '剧情',     '#8b5cf6', NULL, '娱乐', 0, false),
  ('variety',         'Variety',         '综艺',     '#f97316', NULL, '娱乐', 0, false),
  ('cars',            'Cars',            '汽车',     '#64748b', NULL, '生活', 0, false),
  ('family',          'Family',          '亲子',     '#f97316', NULL, '生活', 0, false),
  ('ai',              'AI',              '人工智能', '#6366f1', NULL, '科技', 0, false),
  ('review',          'Review',          '测评',     '#eab308', NULL, '科技', 0, false),
  ('science',         'Science',         '科普',     '#3b82f6', NULL, '科技', 0, false),
  ('beat-sync',       'Beat-sync',       '卡点',     '#ef4444', NULL, '创作', 0, false),
  ('filming',         'Filming',         '拍摄',     '#14b8a6', NULL, '创作', 0, false),
  ('one-take',        'One-take',        '一镜到底', '#84cc16', NULL, '创作', 0, false),
  ('photography',     'Photography',     '摄影',     '#14b8a6', NULL, '创作', 0, false),
  ('post-production', 'Post-production', '后期',     '#8b5cf6', NULL, '创作', 0, false),
  ('recreation',      'Recreation',      '仿拍',     '#eab308', NULL, '创作', 0, false),
  ('script',          'Script',          '文案',     '#3b82f6', NULL, '创作', 0, false),
  ('slow-motion',     'Slow-motion',     '慢动作',   '#06b6d4', NULL, '创作', 0, false),
  ('story',           'Story',           '故事',     '#6366f1', NULL, '创作', 0, false),
  ('timelapse',       'Timelapse',       '延时',     '#64748b', NULL, '创作', 0, false),
  ('transition',      'Transition',      '转场',     '#eab308', NULL, '创作', 0, false),
  ('fitness',         'Fitness',         '健身',     '#22c55e', NULL, '运动', 0, false),
  ('outdoor',         'Outdoor',         '户外',     '#22c55e', NULL, '运动', 0, false),
  ('aesthetic',       'Aesthetic',       '美感',     '#a855f7', NULL, '情感', 0, false),
  ('chill',           'Chill',           '氛围',     '#6366f1', NULL, '情感', 0, false),
  ('emotional',       'Emotional',       '感动',     '#ec4899', NULL, '情感', 0, false),
  ('healing',         'Healing',         '治愈',     '#14b8a6', NULL, '情感', 0, false),
  ('inspiring',       'Inspiring',       '励志',     '#f97316', NULL, '情感', 0, false),
  ('finance',         'Finance',         '财经',     '#ef4444', NULL, '商业', 0, false)
ON CONFLICT (slug) DO NOTHING;

-- Catch a preset list that lost its load-bearing rows in a future edit. These
-- three slugs are the AI pipeline's keys; without them the intent checkboxes
-- attach nothing and — per the pre-467 lesson — say nothing about it.
DO $$
BEGIN
  IF (SELECT count(*) FROM public.tag_presets
       WHERE slug IN ('transcript', 'summary', 'analyze')) <> 3 THEN
    RAISE EXCEPTION
      'tag_presets is missing one of the three AI pipeline slugs — the intent '
      'checkboxes would silently attach nothing';
  END IF;
END $$;

-- Presets are internal. RLS on with no policy = PostgREST sees nothing, while
-- the seeding path (SECURITY DEFINER handle_new_user, or a direct postgres
-- connection) is unaffected.
ALTER TABLE public.tag_presets ENABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- 2. Hand one user their own copy
-- ---------------------------------------------------------------------------
-- SECURITY **INVOKER** on purpose. A SECURITY DEFINER function that takes the
-- target user as a plain parameter is the exact shape mig 463 had to close
-- (CLAUDE.md 2026-09-11): definer + caller-supplied id + EXECUTE for
-- authenticated = "change the uuid, write into someone else's library".
-- Invoker needs no such reasoning — the caller's own RLS applies, and the two
-- real callers (handle_new_user, which is already definer, and psql as
-- postgres) both have the rights they need. The REVOKE below is belt-and-braces.

CREATE OR REPLACE FUNCTION public.seed_initial_tags(p_user uuid)
RETURNS integer
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
  v_added integer;
BEGIN
  INSERT INTO public.tags (
    name, name_zh, type, color, icon, user_id, group_id,
    sort_order, enabled, origin, prompt_trigger, slug
  )
  SELECT p.name, p.name_zh, 'user', p.color, p.icon, p_user, g.id,
         p.sort_order, true, 'curated', p.prompt_trigger, p.slug
    FROM public.tag_presets p
    LEFT JOIN public.tag_groups g ON g.name = p.group_name
   WHERE NOT EXISTS (
           -- Already has it — either a previous seed, or a tag they made
           -- themselves that happens to share the name. Never create a second
           -- one; step 4a stamps the slug onto the row they already own.
           SELECT 1 FROM public.tags t
            WHERE t.user_id = p_user
              AND (t.slug = p.slug OR lower(t.name) = lower(p.name))
         )
  ON CONFLICT ON CONSTRAINT unique_tag_per_scope DO NOTHING;

  GET DIAGNOSTICS v_added = ROW_COUNT;
  RETURN v_added;
END;
$$;

COMMENT ON FUNCTION public.seed_initial_tags(uuid) IS
  'Give one user their own copy of every tag_presets row. Idempotent. Skips '
  'presets the user already has by slug or by name.';

REVOKE EXECUTE ON FUNCTION public.seed_initial_tags(uuid) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.seed_initial_tags(uuid) FROM anon, authenticated;

-- New signups get stocked. handle_new_user already runs on auth.users INSERT
-- (mig 053 → 229) and is SECURITY DEFINER, so the seed runs with the rights it
-- needs. Redefined in full rather than patched: CREATE OR REPLACE replaces the
-- whole body, so the existing profile + personal-team work has to be restated.
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
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

    -- Hand them their initial tags (mig 468). Idempotent; ungrouped if the
    -- tag_groups rows are absent.
    PERFORM public.seed_initial_tags(NEW.id);

    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

-- ⚠️ The TRIGGER is deliberately NOT created here.
--
-- `on_auth_user_created AFTER INSERT ON auth.users EXECUTE handle_new_user()`
-- already exists in prod (mig 053; verified directly —
-- `pg_get_triggerdef` reports exactly that), so redefining the function above
-- is enough for a real signup to get seeded.
--
-- I tried creating it here anyway, because `auth.users` is a Supabase-runtime
-- table: `schema_baseline.sql` dumps only `public` and CI stubs auth.users, so
-- the drift database has never had this trigger and cannot see the wiring. But
-- adding it turned every existing integration test that inserts a synthetic
-- `auth.users` row into a `handle_new_user` call, and five test files went red
-- on a latent NULL-email path — at which point I was editing the production
-- signup function to accommodate test fixtures. That is a bigger blast radius
-- than the thing being fixed, so: no trigger here.
--
-- The wiring is covered instead by
-- `tests/db/test_migration_468_tag_presets_integration.py`, which creates the
-- trigger INSIDE its own rolled-back transaction and signs a user up for real.
-- Same proof, contained.

-- ---------------------------------------------------------------------------
-- 3. A slug is unique PER USER now, not globally — BEFORE anything is forked
-- ---------------------------------------------------------------------------
-- mig 467's global unique was right for one shared row per slug. With a copy
-- per user it would reject every user but the first.
--
-- ⚠️ ORDER MATTERS, and it cost a failed production run to learn it. This
-- section used to sit AFTER the fork, and the fork's very first insert died on
-- `duplicate key value violates unique constraint "uniq_tags_slug"` — the
-- shared row still holds `slug='transcript'` while the global index is still
-- in force, so user #1's copy collides with it. The drift database never
-- caught it because it has no seeded system tags at all: the fork ran over
-- zero rows and reported success. Swap the index first and both the shared row
-- and every user's copy fit.
DROP INDEX IF EXISTS public.uniq_tags_slug;
CREATE UNIQUE INDEX IF NOT EXISTS uniq_tags_user_slug
  ON public.tags (user_id, slug)
  WHERE slug IS NOT NULL;

COMMENT ON COLUMN public.tags.slug IS
  'Stable automation key, unique per user. NULL on a tag the user created '
  'themselves. Never user-editable: the display name (name / name_zh) is the '
  'user''s to change, this is not.';

-- ---------------------------------------------------------------------------
-- 4. Fork the existing shared rows out to every existing user
-- ---------------------------------------------------------------------------

-- 4a. A user who already owns a same-name tag keeps THAT row and inherits the
--     slug — forking would have given them a duplicate, and skipping without
--     the stamp would have left them with no row the automation can find.
UPDATE public.tags t
   SET slug = p.slug
  FROM public.tag_presets p
 WHERE t.type = 'user'
   AND t.slug IS NULL
   AND lower(t.name) = lower(p.name)
   AND NOT EXISTS (
         SELECT 1 FROM public.tags o
          WHERE o.user_id = t.user_id AND o.slug = p.slug
       );

-- 4b. Everyone gets the rest.
SELECT public.seed_initial_tags(id) FROM auth.users;

-- 4c. Re-point the junction rows from the shared tag onto the owner's copy.
--     Ownership comes from the row the link hangs off — resources.creator_id /
--     inspiration_notes.user_id — NOT from whoever attached the tag: the AI
--     attaches tags on the owner's behalf all the time.
--     (⚠️ resources' owner column is `creator_id`, not `user_id` — CLAUDE.md
--     records the 42703 this has already cost once.)
--
--     ⚠️ The shared row is matched to its preset by slug **OR BY NAME**, and the
--     name arm is not decoration: only 16 of the 42 shared rows ever got a slug
--     (mig 467 stamped the automation tags and nothing else). The other 26 —
--     AI, Recreation, Finance, Post-production, Script, … — carry ~380 of
--     production's links between them, and a slug-only match would strand every
--     one of them. The preset list is keyed by slug, so the name is the only
--     bridge from a slug-less shared row to the copy the user now owns.
INSERT INTO public.resource_tags
  (resource_id, tag_id, source, confidence, created_at, tagged_by)
SELECT rt.resource_id, mine.id, rt.source, rt.confidence, rt.created_at, rt.tagged_by
  FROM public.resource_tags rt
  JOIN public.tags shared   ON shared.id = rt.tag_id AND shared.type = 'system'
  JOIN public.tag_presets p ON p.slug = shared.slug
                            OR lower(p.name) = lower(shared.name)
  JOIN public.resources r   ON r.id = rt.resource_id
  JOIN public.tags mine     ON mine.user_id = r.creator_id AND mine.slug = p.slug
ON CONFLICT (resource_id, tag_id) DO NOTHING;

INSERT INTO public.note_tags (note_id, tag_id, created_at)
SELECT nt.note_id, mine.id, nt.created_at
  FROM public.note_tags nt
  JOIN public.tags shared         ON shared.id = nt.tag_id AND shared.type = 'system'
  JOIN public.tag_presets p       ON p.slug = shared.slug
                                  OR lower(p.name) = lower(shared.name)
  JOIN public.inspiration_notes n ON n.id = nt.note_id
  JOIN public.tags mine           ON mine.user_id = n.user_id AND mine.slug = p.slug
ON CONFLICT (note_id, tag_id) DO NOTHING;

-- 4d. Refuse to delete anything we could not re-point.
--     `DELETE FROM tags` cascades to both junctions, so a link with no copy to
--     fall back on is a link about to disappear. Losing a user's tagging is not
--     an acceptable silent outcome — stop and let a human look.
--
--     ⚠️ The question is NOT "are there still rows pointing at a shared tag" —
--     there always are: 4c ADDS the replacement row and leaves the original for
--     the CASCADE to clear. Asking it that way makes the guard fire on a
--     perfectly good migration (it did, on the first draft). The real question
--     is whether each original now has a counterpart on the owner's own copy.
DO $$
DECLARE
  v_orphans integer;
BEGIN
  SELECT
      (SELECT count(*)
         FROM public.resource_tags rt
         JOIN public.tags shared ON shared.id = rt.tag_id AND shared.type = 'system'
        WHERE NOT EXISTS (
                SELECT 1
                  FROM public.tag_presets p
                  JOIN public.resources r ON r.id = rt.resource_id
                  JOIN public.tags mine   ON mine.user_id = r.creator_id
                                         AND mine.slug = p.slug
                  JOIN public.resource_tags kept
                    ON kept.resource_id = rt.resource_id AND kept.tag_id = mine.id
                 WHERE p.slug = shared.slug OR lower(p.name) = lower(shared.name)
              ))
    + (SELECT count(*)
         FROM public.note_tags nt
         JOIN public.tags shared ON shared.id = nt.tag_id AND shared.type = 'system'
        WHERE NOT EXISTS (
                SELECT 1
                  FROM public.tag_presets p
                  JOIN public.inspiration_notes n ON n.id = nt.note_id
                  JOIN public.tags mine           ON mine.user_id = n.user_id
                                                 AND mine.slug = p.slug
                  JOIN public.note_tags kept
                    ON kept.note_id = nt.note_id AND kept.tag_id = mine.id
                 WHERE p.slug = shared.slug OR lower(p.name) = lower(shared.name)
              ))
    INTO v_orphans;

  IF v_orphans > 0 THEN
    RAISE EXCEPTION
      'mig 468: % junction row(s) have no copy to fall back on and would be '
      'deleted with the shared tag. Most likely an owner column is NULL '
      '(resources.creator_id / inspiration_notes.user_id), or a shared tag '
      'matches no preset by slug or by name. Nothing was deleted.', v_orphans;
  END IF;
END $$;

-- 4e. The shared rows are now unreferenced. Goodbye.
DELETE FROM public.tags WHERE type = 'system';

-- There is no such thing as a system tag any more; make that structural rather
-- than a convention. 'time' stays legal — it has zero rows today, but the code
-- that reads it is not part of this change.
ALTER TABLE public.tags DROP CONSTRAINT IF EXISTS tags_type_check;
ALTER TABLE public.tags ADD CONSTRAINT tags_type_check
  CHECK (type::text = ANY (ARRAY['user'::text, 'time'::text]));

-- ---------------------------------------------------------------------------
-- 5. Drop the type='user' guards — "yours" is `user_id`, not `type`
-- ---------------------------------------------------------------------------
-- With no shared rows left, `type = 'user'` was only ever a proxy for "this is
-- editable", and it is now the thing standing between the user and their own
-- initial tags. Ownership is still enforced, by the clause that actually says
-- so: `user_id = auth.uid()`.

DROP POLICY IF EXISTS "Users can create own tags" ON public.tags;
CREATE POLICY "Users can create own tags" ON public.tags
  FOR INSERT WITH CHECK (user_id = (SELECT auth.uid()));

DROP POLICY IF EXISTS "Users can update own tags" ON public.tags;
CREATE POLICY "Users can update own tags" ON public.tags
  FOR UPDATE USING (user_id = (SELECT auth.uid()));

DROP POLICY IF EXISTS "Users can delete own tags" ON public.tags;
CREATE POLICY "Users can delete own tags" ON public.tags
  FOR DELETE USING (user_id = (SELECT auth.uid()));

-- SELECT loses its `type IN ('system','time')` arm: that arm published every
-- shared row to every user, which is precisely what we just stopped doing.
-- Keeping it would leave a hole for any future row that sets type='time'.
DROP POLICY IF EXISTS "tags_select" ON public.tags;
CREATE POLICY "tags_select" ON public.tags
  FOR SELECT USING (
    user_id = (SELECT auth.uid())
    OR scope_id IN (SELECT get_user_team_ids((SELECT auth.uid())))
  );

-- The merge proc refused to merge anything that was not `type='user'`. Same
-- proxy, same fix: ownership via `p_user` is untouched, and that is the check
-- that matters. Body restated verbatim from mig 368 (CREATE OR REPLACE has no
-- patch mode) with ONLY the two guards changed — the source normalisation
-- (dedupe + drop the target from its own source list) and the `bigint` return
-- type are load-bearing and stay byte-identical.
CREATE OR REPLACE FUNCTION merge_tags(
  p_target  text,
  p_sources text[],
  p_user    uuid
) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
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

  -- Target must exist and belong to the caller. (Was: ... AND type = 'user'.)
  IF NOT EXISTS (
    SELECT 1 FROM tags
     WHERE id = v_target AND user_id = p_user
  ) THEN
    RAISE EXCEPTION 'merge_tags: target % is not one of your tags', v_target;
  END IF;

  -- Every source must belong to the caller. (Was: ... OR type <> 'user'.)
  SELECT count(*) INTO v_bad FROM tags
   WHERE id = ANY(v_sources) AND user_id IS DISTINCT FROM p_user;
  IF v_bad > 0 THEN
    RAISE EXCEPTION 'merge_tags: one or more source tags are not yours';
  END IF;

  -- All sources must exist.
  IF (SELECT count(*) FROM tags WHERE id = ANY(v_sources)) <> array_length(v_sources, 1) THEN
    RAISE EXCEPTION 'merge_tags: one or more source tags do not exist';
  END IF;

  -- Re-point resource_tags with dedup against PK (resource_id, tag_id).
  INSERT INTO resource_tags (resource_id, tag_id, source, confidence, created_at)
  SELECT resource_id, v_target, source, confidence, created_at
    FROM resource_tags
   WHERE tag_id = ANY(v_sources)
  ON CONFLICT (resource_id, tag_id) DO NOTHING;

  -- Re-point note_tags with dedup against PK (note_id, tag_id).
  INSERT INTO note_tags (note_id, tag_id, created_at)
  SELECT note_id, v_target, created_at
    FROM note_tags
   WHERE tag_id = ANY(v_sources)
  ON CONFLICT (note_id, tag_id) DO NOTHING;

  -- Delete the source tags (FK ON DELETE CASCADE clears their junction rows).
  DELETE FROM tags WHERE id = ANY(v_sources);

  SELECT count(*) INTO v_count FROM resource_tags WHERE tag_id = v_target;
  RETURN v_count;
END;
$fn$;

-- ---------------------------------------------------------------------------
-- 6. Close an escalation this function has been carrying since mig 368
-- ---------------------------------------------------------------------------
-- `merge_tags` is SECURITY DEFINER, takes the acting user as a PLAIN PARAMETER,
-- and has `GRANT EXECUTE ... TO authenticated`. That is the exact three-part
-- shape CLAUDE.md records from 2026-09-11 (mig 463): the anon/publishable key
-- is baked into the browser bundle, PostgREST exposes
-- `/rest/v1/rpc/merge_tags`, and the function bypasses RLS as its owner — so
-- any signed-in user could pass SOMEBODY ELSE'S uuid and merge (and thereby
-- DELETE) their tags. The `p_user` guard reads like an ownership check but it
-- only checks consistency with whatever uuid the caller typed.
--
-- Nothing needs that grant: the browser calls POST /api/v1/tags/merge, and the
-- backend reaches this proc over a direct SQL session
-- (`tags_repository.merge_tags`). So the fix is the first row of CLAUDE.md's
-- table — make the browser unable to reach it at all.
--
-- ⚠️ CREATE OR REPLACE above preserved the old ACL, as it always does. Revoking
-- has to be explicit and in the same migration, or "I rewrote this function"
-- would mean "I widened a hole that is still open".
REVOKE EXECUTE ON FUNCTION merge_tags(text, text[], uuid) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION merge_tags(text, text[], uuid) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION merge_tags(text, text[], uuid) TO service_role;

NOTIFY pgrst, 'reload schema';
