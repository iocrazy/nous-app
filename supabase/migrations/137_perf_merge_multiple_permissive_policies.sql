-- =============================================================
-- Supabase lint 0006 (multiple_permissive_policies).
-- Postgres evaluates every PERMISSIVE policy on every row for a
-- given (role, cmd). Overlapping permissive policies multiply the
-- per-row cost without adding security.
--
-- Strategies used here:
--   A) Drop redundant SELECT-only policy where an ALL policy with
--      the same qual already covers SELECT.
--   B) Drop a broken "Service role full access" policy that
--      accidentally grants TO public WITH qual=true (security hole).
--   C) Merge multiple SELECT policies into one with OR'd quals.
--   D) Split "admin ALL + user SELECT" into admin-per-cmd policies
--      plus a single merged SELECT that ORs admin and user quals.
--   E) project_members: combine admin-ALL + four user cmd policies
--      into four per-cmd policies with OR'd quals.
-- =============================================================

-- -------------------------------------------------------------
-- Category A: drop redundant SELECT policies (ALL covers SELECT).
-- -------------------------------------------------------------
DROP POLICY IF EXISTS script_assets_team_select ON public.script_assets;
DROP POLICY IF EXISTS script_chapters_team_select ON public.script_chapters;
DROP POLICY IF EXISTS ssl_team_select ON public.script_storyboard_links;
DROP POLICY IF EXISTS "View summaries of owned resources" ON public.resource_summaries;
DROP POLICY IF EXISTS "View transcripts of owned resources" ON public.resource_transcripts;
DROP POLICY IF EXISTS "Users can read project folders" ON public.project_folders;
DROP POLICY IF EXISTS "Users can view their notification status" ON public.user_notifications;

-- -------------------------------------------------------------
-- Category B: drop the broken public-access task_assets policy.
-- Named "Service role full access" but TO public WITH qual=true,
-- i.e. every anonymous caller could read/write task_assets.
-- Safe to drop — real service_role bypasses RLS via BYPASSRLS.
-- -------------------------------------------------------------
DROP POLICY IF EXISTS "Service role full access on task_assets" ON public.task_assets;

-- -------------------------------------------------------------
-- Category C: merge multiple SELECT policies into one (OR).
-- -------------------------------------------------------------

-- member_quotas: own quota OR team owner/admin of member's team
DROP POLICY IF EXISTS "Members can read own quota" ON public.member_quotas;
DROP POLICY IF EXISTS "Owner or admin can read all team member quotas" ON public.member_quotas;
CREATE POLICY member_quotas_select ON public.member_quotas
  AS PERMISSIVE FOR SELECT TO public
  USING (
    user_id = (SELECT auth.uid())
    OR team_id IN (
      SELECT tm.team_id FROM public.team_members tm
      WHERE tm.user_id = (SELECT auth.uid())
        AND tm.role::text = ANY (ARRAY['owner','admin'])
    )
  );

-- orders: own orders OR team owner/admin of order's team
DROP POLICY IF EXISTS "Users can read own orders" ON public.orders;
DROP POLICY IF EXISTS "Owner or admin can read team orders" ON public.orders;
CREATE POLICY orders_select ON public.orders
  AS PERMISSIVE FOR SELECT TO public
  USING (
    user_id = (SELECT auth.uid())
    OR team_id IN (
      SELECT tm.team_id FROM public.team_members tm
      WHERE tm.user_id = (SELECT auth.uid())
        AND tm.role::text = ANY (ARRAY['owner','admin'])
    )
  );

-- tags: system+time, team-scoped to members, or user's own
DROP POLICY IF EXISTS "System tags visible to all" ON public.tags;
DROP POLICY IF EXISTS "Team tags visible to members" ON public.tags;
DROP POLICY IF EXISTS "User tags visible to owner" ON public.tags;
CREATE POLICY tags_select ON public.tags
  AS PERMISSIVE FOR SELECT TO public
  USING (
    type::text IN ('system','time')
    OR (scope_type::text = 'team'
        AND scope_id IN (SELECT public.get_user_team_ids_text((SELECT auth.uid()))))
    OR user_id = (SELECT auth.uid())
  );

-- -------------------------------------------------------------
-- Category D: split "admin ALL + user SELECT" into admin-per-cmd
-- plus a single merged SELECT (admin OR user).
-- -------------------------------------------------------------

-- credit_pricing: pricing is public-readable; admin manages writes.
DROP POLICY IF EXISTS "Admins can manage pricing" ON public.credit_pricing;
DROP POLICY IF EXISTS "Anyone can view pricing" ON public.credit_pricing;
CREATE POLICY credit_pricing_select ON public.credit_pricing
  AS PERMISSIVE FOR SELECT TO public USING (true);
CREATE POLICY credit_pricing_admin_insert ON public.credit_pricing
  AS PERMISSIVE FOR INSERT TO public
  WITH CHECK (EXISTS (
    SELECT 1 FROM public.user_profiles up
    WHERE up.id = (SELECT auth.uid()) AND up.role = 'admin'::user_role
  ));
CREATE POLICY credit_pricing_admin_update ON public.credit_pricing
  AS PERMISSIVE FOR UPDATE TO public
  USING (EXISTS (
    SELECT 1 FROM public.user_profiles up
    WHERE up.id = (SELECT auth.uid()) AND up.role = 'admin'::user_role
  ))
  WITH CHECK (EXISTS (
    SELECT 1 FROM public.user_profiles up
    WHERE up.id = (SELECT auth.uid()) AND up.role = 'admin'::user_role
  ));
CREATE POLICY credit_pricing_admin_delete ON public.credit_pricing
  AS PERMISSIVE FOR DELETE TO public
  USING (EXISTS (
    SELECT 1 FROM public.user_profiles up
    WHERE up.id = (SELECT auth.uid()) AND up.role = 'admin'::user_role
  ));

-- credit_transactions: own rows visible; admin manages.
DROP POLICY IF EXISTS "Admins can manage all transactions" ON public.credit_transactions;
DROP POLICY IF EXISTS "Users can view own transactions" ON public.credit_transactions;
CREATE POLICY credit_transactions_select ON public.credit_transactions
  AS PERMISSIVE FOR SELECT TO public
  USING (
    user_id = (SELECT auth.uid())
    OR EXISTS (
      SELECT 1 FROM public.user_profiles up
      WHERE up.id = (SELECT auth.uid()) AND up.role = 'admin'::user_role
    )
  );
CREATE POLICY credit_transactions_admin_insert ON public.credit_transactions
  AS PERMISSIVE FOR INSERT TO public
  WITH CHECK (EXISTS (
    SELECT 1 FROM public.user_profiles up
    WHERE up.id = (SELECT auth.uid()) AND up.role = 'admin'::user_role
  ));
CREATE POLICY credit_transactions_admin_update ON public.credit_transactions
  AS PERMISSIVE FOR UPDATE TO public
  USING (EXISTS (
    SELECT 1 FROM public.user_profiles up
    WHERE up.id = (SELECT auth.uid()) AND up.role = 'admin'::user_role
  ))
  WITH CHECK (EXISTS (
    SELECT 1 FROM public.user_profiles up
    WHERE up.id = (SELECT auth.uid()) AND up.role = 'admin'::user_role
  ));
CREATE POLICY credit_transactions_admin_delete ON public.credit_transactions
  AS PERMISSIVE FOR DELETE TO public
  USING (EXISTS (
    SELECT 1 FROM public.user_profiles up
    WHERE up.id = (SELECT auth.uid()) AND up.role = 'admin'::user_role
  ));

-- nous_models: enabled models visible to everyone; admin manages.
DROP POLICY IF EXISTS nous_models_admin_all ON public.nous_models;
DROP POLICY IF EXISTS nous_models_public_read ON public.nous_models;
CREATE POLICY nous_models_select ON public.nous_models
  AS PERMISSIVE FOR SELECT TO public
  USING (
    is_enabled = true
    OR EXISTS (
      SELECT 1 FROM public.user_profiles up
      WHERE up.id = (SELECT auth.uid()) AND up.role = 'admin'::user_role
    )
  );
CREATE POLICY nous_models_admin_insert ON public.nous_models
  AS PERMISSIVE FOR INSERT TO public
  WITH CHECK (EXISTS (
    SELECT 1 FROM public.user_profiles up
    WHERE up.id = (SELECT auth.uid()) AND up.role = 'admin'::user_role
  ));
CREATE POLICY nous_models_admin_update ON public.nous_models
  AS PERMISSIVE FOR UPDATE TO public
  USING (EXISTS (
    SELECT 1 FROM public.user_profiles up
    WHERE up.id = (SELECT auth.uid()) AND up.role = 'admin'::user_role
  ))
  WITH CHECK (EXISTS (
    SELECT 1 FROM public.user_profiles up
    WHERE up.id = (SELECT auth.uid()) AND up.role = 'admin'::user_role
  ));
CREATE POLICY nous_models_admin_delete ON public.nous_models
  AS PERMISSIVE FOR DELETE TO public
  USING (EXISTS (
    SELECT 1 FROM public.user_profiles up
    WHERE up.id = (SELECT auth.uid()) AND up.role = 'admin'::user_role
  ));

-- system_settings: publicly readable; admin manages writes.
DROP POLICY IF EXISTS "Admins can manage settings" ON public.system_settings;
DROP POLICY IF EXISTS "Anyone can view settings" ON public.system_settings;
CREATE POLICY system_settings_select ON public.system_settings
  AS PERMISSIVE FOR SELECT TO public USING (true);
CREATE POLICY system_settings_admin_insert ON public.system_settings
  AS PERMISSIVE FOR INSERT TO public
  WITH CHECK (EXISTS (
    SELECT 1 FROM public.user_profiles up
    WHERE up.id = (SELECT auth.uid()) AND up.role = 'admin'::user_role
  ));
CREATE POLICY system_settings_admin_update ON public.system_settings
  AS PERMISSIVE FOR UPDATE TO public
  USING (EXISTS (
    SELECT 1 FROM public.user_profiles up
    WHERE up.id = (SELECT auth.uid()) AND up.role = 'admin'::user_role
  ))
  WITH CHECK (EXISTS (
    SELECT 1 FROM public.user_profiles up
    WHERE up.id = (SELECT auth.uid()) AND up.role = 'admin'::user_role
  ));
CREATE POLICY system_settings_admin_delete ON public.system_settings
  AS PERMISSIVE FOR DELETE TO public
  USING (EXISTS (
    SELECT 1 FROM public.user_profiles up
    WHERE up.id = (SELECT auth.uid()) AND up.role = 'admin'::user_role
  ));

-- user_credits: own row visible; admin manages.
DROP POLICY IF EXISTS "Admins can manage all credits" ON public.user_credits;
DROP POLICY IF EXISTS "Users can view own credits" ON public.user_credits;
CREATE POLICY user_credits_select ON public.user_credits
  AS PERMISSIVE FOR SELECT TO public
  USING (
    user_id = (SELECT auth.uid())
    OR EXISTS (
      SELECT 1 FROM public.user_profiles up
      WHERE up.id = (SELECT auth.uid()) AND up.role = 'admin'::user_role
    )
  );
CREATE POLICY user_credits_admin_insert ON public.user_credits
  AS PERMISSIVE FOR INSERT TO public
  WITH CHECK (EXISTS (
    SELECT 1 FROM public.user_profiles up
    WHERE up.id = (SELECT auth.uid()) AND up.role = 'admin'::user_role
  ));
CREATE POLICY user_credits_admin_update ON public.user_credits
  AS PERMISSIVE FOR UPDATE TO public
  USING (EXISTS (
    SELECT 1 FROM public.user_profiles up
    WHERE up.id = (SELECT auth.uid()) AND up.role = 'admin'::user_role
  ))
  WITH CHECK (EXISTS (
    SELECT 1 FROM public.user_profiles up
    WHERE up.id = (SELECT auth.uid()) AND up.role = 'admin'::user_role
  ));
CREATE POLICY user_credits_admin_delete ON public.user_credits
  AS PERMISSIVE FOR DELETE TO public
  USING (EXISTS (
    SELECT 1 FROM public.user_profiles up
    WHERE up.id = (SELECT auth.uid()) AND up.role = 'admin'::user_role
  ));

-- -------------------------------------------------------------
-- Category E: project_members — collapse to per-cmd policies that
-- OR the admin qual (project owner or self-admin role) with the
-- user qual (membership via projects RLS).
-- -------------------------------------------------------------
DROP POLICY IF EXISTS "Admins can manage members" ON public.project_members;
DROP POLICY IF EXISTS "Users can add project members" ON public.project_members;
DROP POLICY IF EXISTS "Users can remove project members" ON public.project_members;
DROP POLICY IF EXISTS "Users can read project members" ON public.project_members;
DROP POLICY IF EXISTS "Users can update project members" ON public.project_members;

CREATE POLICY project_members_select ON public.project_members
  AS PERMISSIVE FOR SELECT TO public
  USING (
    project_id IN (SELECT id FROM public.projects)
    OR (user_id = (SELECT auth.uid()) AND role::text = 'admin')
  );

CREATE POLICY project_members_insert ON public.project_members
  AS PERMISSIVE FOR INSERT TO public
  WITH CHECK (
    project_id IN (SELECT id FROM public.projects)
    OR project_id IN (
      SELECT p.id FROM public.projects p
      WHERE p.owner_id = (SELECT auth.uid())
    )
  );

CREATE POLICY project_members_update ON public.project_members
  AS PERMISSIVE FOR UPDATE TO public
  USING (
    project_id IN (SELECT id FROM public.projects)
    OR (user_id = (SELECT auth.uid()) AND role::text = 'admin')
  )
  WITH CHECK (
    project_id IN (SELECT id FROM public.projects)
    OR project_id IN (
      SELECT p.id FROM public.projects p
      WHERE p.owner_id = (SELECT auth.uid())
    )
  );

CREATE POLICY project_members_delete ON public.project_members
  AS PERMISSIVE FOR DELETE TO public
  USING (
    project_id IN (SELECT id FROM public.projects)
    OR (user_id = (SELECT auth.uid()) AND role::text = 'admin')
  );
