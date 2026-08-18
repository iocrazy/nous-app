-- 431: owner scoping for mediahub_models + bind CLI-session rows to their owner.
--
-- Providers that spend a PERSONAL credential (codex / jimeng CLI OAuth
-- sessions — the machine owner's subscriptions, not platform api_keys) must
-- not be selectable by every user. A NULL owner_user_id keeps a row
-- platform-wide; a non-NULL owner makes it visible/usable only to that
-- auth.users id. Enforced in three layers: list_enabled (catalog queries),
-- db_registry resolvers (dispatch, fail-closed), and the RLS select policy
-- below (direct PostgREST reads).
--
-- Owner binding is by email lookup so the same file is a no-op on databases
-- where that account does not exist (CI ephemeral DB: subquery yields NULL →
-- rows stay unowned there, and codex-image stays disabled — see the guarded
-- enable at the bottom).

BEGIN;

ALTER TABLE public.mediahub_models
    ADD COLUMN IF NOT EXISTS owner_user_id UUID
        REFERENCES auth.users(id) ON DELETE SET NULL;

COMMENT ON COLUMN public.mediahub_models.owner_user_id IS
    'NULL = platform-wide row; set = row visible/usable only to this user '
    '(personal-credential providers: codex / jimeng CLI sessions).';

-- Bind the three CLI-session rows to the machine owner's account.
UPDATE public.mediahub_models
SET owner_user_id = (SELECT id FROM auth.users WHERE email = '8512939@qq.com')
WHERE name IN ('codex-image', 'jimeng-cli-image', 'jimeng-cli-seedance')
  AND owner_user_id IS NULL;

-- codex-image was seeded disabled (migration 430) precisely to wait for this
-- binding; enable it ONLY where the binding actually happened.
UPDATE public.mediahub_models
SET is_enabled = TRUE
WHERE name = 'codex-image'
  AND owner_user_id IS NOT NULL;

-- RLS: hide other users' private rows from direct PostgREST reads. Keeps the
-- existing shape (enabled-or-admin) and adds the owner clause for non-admins.
DROP POLICY IF EXISTS nous_models_select ON public.mediahub_models;
CREATE POLICY nous_models_select ON public.mediahub_models FOR SELECT USING (
    (
        is_enabled = true
        AND (
            owner_user_id IS NULL
            OR owner_user_id = (SELECT auth.uid())
        )
    )
    OR (
        EXISTS (
            SELECT 1
            FROM public.user_profiles up
            WHERE up.id = (SELECT auth.uid())
              AND up.role = 'admin'::public.user_role
        )
    )
);

COMMIT;

NOTIFY pgrst, 'reload schema';
