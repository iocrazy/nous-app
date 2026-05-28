-- supabase/migrations/234_personal_scope_id_remap.sql
-- Remap personal-scope scope_id from auth.user_uuid to personal team snowflake.
-- Pre-condition: mig 230 has run (every personal-scope owner has a personal team).
--
-- After this migration: scope_id ALWAYS refers to teams.id, regardless of scope_type.
-- Application code still works because scope_type='personal' branches are intact
-- (PR-E removes them after this lands).
--
-- See docs/superpowers/specs/2026-05-28-id-unification-design.md § 3.

BEGIN;

-- resource_items
UPDATE public.resource_items ri
   SET scope_id = t.id::text
  FROM public.teams t
 WHERE ri.scope_type = 'personal'
   AND t.kind = 'personal'
   AND t.owner_id::text = ri.scope_id;

-- folders
UPDATE public.folders f
   SET scope_id = t.id::text
  FROM public.teams t
 WHERE f.scope_type = 'personal'
   AND t.kind = 'personal'
   AND t.owner_id::text = f.scope_id;

-- tags
UPDATE public.tags g
   SET scope_id = t.id::text
  FROM public.teams t
 WHERE g.scope_type = 'personal'
   AND t.kind = 'personal'
   AND t.owner_id::text = g.scope_id;

-- smart_collections
UPDATE public.smart_collections sc
   SET scope_id = t.id::text
  FROM public.teams t
 WHERE sc.scope_type = 'personal'
   AND t.kind = 'personal'
   AND t.owner_id::text = sc.scope_id;

COMMIT;
