-- supabase/migrations/239_reinstall_on_auth_user_created.sql
-- Reinstall the on_auth_user_created trigger + backfill the users it
-- silently skipped.
--
-- Bug timeline:
--   - mig 001 created `on_auth_user_created AFTER INSERT ON auth.users`
--     calling `handle_new_user()` to set up profile + personal team
--   - At some point (Supabase auth schema reset? manual cleanup?) the
--     trigger was dropped from prod. The function itself stayed.
--   - mig 053 + mig 229 updated `handle_new_user` body but neither
--     re-CREATEs the trigger, so they had no effect on new signups.
--   - Result: any user who signed up while the trigger was missing got
--     an auth.users row and nothing else (no user_profile, no personal
--     team, no team_members, no team_quota).
--   - The backend's `_create_team_quota_for_new_user` background task
--     (called from POST /api/v1/auth/signup) silently skips when no
--     team_members row exists, so the welcome bonus never lands either.
--
-- Found 2026-05-29 while auditing the signup path. 3 prod users
-- (8512939@qq.com, test@mediahub.dev, 2384364058@qq.com) all show
-- partial state. PR-A's mig 230 already backfilled personal teams for
-- everyone, so the only gap left is user_profiles + the trigger
-- itself. team_quotas backfill is left to a follow-up because welcome
-- bonus also writes point_transactions and that flow lives in the
-- backend (PointsService.ensure_team_quota).

BEGIN;

-- 1. Reinstall the trigger. CREATE OR REPLACE TRIGGER is idempotent.
CREATE OR REPLACE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_new_user();

-- 2. Backfill missing user_profiles using the same logic
--    handle_new_user() would have used (username from raw_user_meta_data
--    or email local-part).
INSERT INTO public.user_profiles (id, username, role)
SELECT
    u.id,
    COALESCE(
        u.raw_user_meta_data->>'username',
        split_part(u.email, '@', 1)
    ),
    'user'
  FROM auth.users u
 WHERE NOT EXISTS (SELECT 1 FROM public.user_profiles p WHERE p.id = u.id)
ON CONFLICT (id) DO NOTHING;

-- 3. Backfill missing personal teams (defensive — PR-A mig 230 should
--    have caught all pre-existing users, but a user could have signed
--    up between PR-A and this fix).
INSERT INTO public.teams (name, owner_id, kind)
SELECT
    COALESCE(
        u.raw_user_meta_data->>'username',
        split_part(u.email, '@', 1)
    ) || '''s Workspace',
    u.id,
    'personal'
  FROM auth.users u
 WHERE NOT EXISTS (
        SELECT 1 FROM public.teams t
         WHERE t.owner_id = u.id AND t.kind = 'personal'
    )
ON CONFLICT DO NOTHING;
-- teams_add_owner_trigger (mig 009) auto-inserts the team_members row.

-- 4. Sanity check
DO $$
DECLARE
    trg_missing int;
    profile_gap int;
    team_gap int;
BEGIN
    SELECT COUNT(*) INTO trg_missing
      FROM pg_trigger
     WHERE tgname = 'on_auth_user_created' AND NOT tgisinternal;
    IF trg_missing = 0 THEN
        RAISE EXCEPTION 'on_auth_user_created trigger failed to install';
    END IF;

    SELECT COUNT(*) INTO profile_gap
      FROM auth.users u
     WHERE NOT EXISTS (SELECT 1 FROM public.user_profiles p WHERE p.id = u.id);
    IF profile_gap > 0 THEN
        RAISE EXCEPTION 'user_profiles backfill left % rows missing', profile_gap;
    END IF;

    SELECT COUNT(*) INTO team_gap
      FROM auth.users u
     WHERE NOT EXISTS (
            SELECT 1 FROM public.teams t
             WHERE t.owner_id = u.id AND t.kind = 'personal'
        );
    IF team_gap > 0 THEN
        RAISE EXCEPTION 'personal team backfill left % users missing', team_gap;
    END IF;
END $$;

COMMIT;
