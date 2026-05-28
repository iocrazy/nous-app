-- supabase/migrations/230_backfill_personal_teams.sql
-- One-shot backfill: every distinct owner of personal-scope rows (in
-- resource_items, folders, tags, smart_collections) gets a personal
-- team if they don't already have one.
--
-- Most users already have a personal team via mig 229's modified
-- handle_new_user. This is the safety net for edge cases.

INSERT INTO public.teams (kind, owner_id, name)
SELECT DISTINCT 'personal', scope_id::uuid, 'Personal'
  FROM public.resource_items
 WHERE scope_type = 'personal'
   AND scope_id IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM public.teams t
        WHERE t.owner_id = public.resource_items.scope_id::uuid
          AND t.kind = 'personal'
   );

INSERT INTO public.teams (kind, owner_id, name)
SELECT DISTINCT 'personal', scope_id::uuid, 'Personal'
  FROM public.folders
 WHERE scope_type = 'personal'
   AND scope_id IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM public.teams t
        WHERE t.owner_id = public.folders.scope_id::uuid
          AND t.kind = 'personal'
   );

INSERT INTO public.teams (kind, owner_id, name)
SELECT DISTINCT 'personal', scope_id::uuid, 'Personal'
  FROM public.tags
 WHERE scope_type = 'personal'
   AND scope_id IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM public.teams t
        WHERE t.owner_id = public.tags.scope_id::uuid
          AND t.kind = 'personal'
   );

INSERT INTO public.teams (kind, owner_id, name)
SELECT DISTINCT 'personal', scope_id::uuid, 'Personal'
  FROM public.smart_collections
 WHERE scope_type = 'personal'
   AND scope_id IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM public.teams t
        WHERE t.owner_id = public.smart_collections.scope_id::uuid
          AND t.kind = 'personal'
   );

-- Mirror into team_members for each newly created personal team.
-- (teams_add_owner_trigger may have already done this; the NOT EXISTS
-- guard makes it idempotent.)
INSERT INTO public.team_members (team_id, user_id, role)
SELECT t.id, t.owner_id, 'owner'
  FROM public.teams t
 WHERE t.kind = 'personal'
   AND NOT EXISTS (
       SELECT 1 FROM public.team_members
        WHERE team_id = t.id AND user_id = t.owner_id
   );
