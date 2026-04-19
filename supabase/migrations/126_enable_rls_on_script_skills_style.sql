-- =============================================================
-- Enable RLS + team-based policies on the 6 public tables that had
-- RLS disabled (script_projects / script_chapters / script_assets /
-- script_storyboard_links / skills / style_templates).
--
-- Pattern (matches storyboard_projects convention):
--   * service_role has unrestricted ALL
--   * team-scoped SELECT/INSERT/UPDATE/DELETE gated by
--     team_id IN (SELECT get_user_team_ids(auth.uid()))
--   * Child tables (chapters/assets/links) inherit via EXISTS
--     on parent script_projects
-- =============================================================

-- ── script_projects (top-level: has team_id + created_by) ─────
ALTER TABLE public.script_projects ENABLE ROW LEVEL SECURITY;

CREATE POLICY script_projects_service_role ON public.script_projects
  FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

CREATE POLICY script_projects_team_select ON public.script_projects
  FOR SELECT USING (team_id IN (SELECT public.get_user_team_ids(auth.uid())));

CREATE POLICY script_projects_team_insert ON public.script_projects
  FOR INSERT WITH CHECK (team_id IN (SELECT public.get_user_team_ids(auth.uid())));

CREATE POLICY script_projects_team_update ON public.script_projects
  FOR UPDATE USING (team_id IN (SELECT public.get_user_team_ids(auth.uid())))
             WITH CHECK (team_id IN (SELECT public.get_user_team_ids(auth.uid())));

CREATE POLICY script_projects_team_delete ON public.script_projects
  FOR DELETE USING (team_id IN (SELECT public.get_user_team_ids(auth.uid())));


-- ── script_chapters (child: gate via script_id → script_projects) ─
ALTER TABLE public.script_chapters ENABLE ROW LEVEL SECURITY;

CREATE POLICY script_chapters_service_role ON public.script_chapters
  FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

CREATE POLICY script_chapters_team_select ON public.script_chapters
  FOR SELECT USING (EXISTS (
    SELECT 1 FROM public.script_projects sp
    WHERE sp.id = script_chapters.script_id
      AND sp.team_id IN (SELECT public.get_user_team_ids(auth.uid()))
  ));

CREATE POLICY script_chapters_team_write ON public.script_chapters
  FOR ALL
  USING (EXISTS (
    SELECT 1 FROM public.script_projects sp
    WHERE sp.id = script_chapters.script_id
      AND sp.team_id IN (SELECT public.get_user_team_ids(auth.uid()))
  ))
  WITH CHECK (EXISTS (
    SELECT 1 FROM public.script_projects sp
    WHERE sp.id = script_chapters.script_id
      AND sp.team_id IN (SELECT public.get_user_team_ids(auth.uid()))
  ));


-- ── script_assets (child: gate via script_id → script_projects) ──
ALTER TABLE public.script_assets ENABLE ROW LEVEL SECURITY;

CREATE POLICY script_assets_service_role ON public.script_assets
  FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

CREATE POLICY script_assets_team_select ON public.script_assets
  FOR SELECT USING (EXISTS (
    SELECT 1 FROM public.script_projects sp
    WHERE sp.id = script_assets.script_id
      AND sp.team_id IN (SELECT public.get_user_team_ids(auth.uid()))
  ));

CREATE POLICY script_assets_team_write ON public.script_assets
  FOR ALL
  USING (EXISTS (
    SELECT 1 FROM public.script_projects sp
    WHERE sp.id = script_assets.script_id
      AND sp.team_id IN (SELECT public.get_user_team_ids(auth.uid()))
  ))
  WITH CHECK (EXISTS (
    SELECT 1 FROM public.script_projects sp
    WHERE sp.id = script_assets.script_id
      AND sp.team_id IN (SELECT public.get_user_team_ids(auth.uid()))
  ));


-- ── script_storyboard_links (child: gate via chapter_id → script_chapters → script_projects) ─
ALTER TABLE public.script_storyboard_links ENABLE ROW LEVEL SECURITY;

CREATE POLICY ssl_service_role ON public.script_storyboard_links
  FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

CREATE POLICY ssl_team_select ON public.script_storyboard_links
  FOR SELECT USING (EXISTS (
    SELECT 1 FROM public.script_chapters sc
    JOIN public.script_projects sp ON sp.id = sc.script_id
    WHERE sc.id = script_storyboard_links.chapter_id
      AND sp.team_id IN (SELECT public.get_user_team_ids(auth.uid()))
  ));

CREATE POLICY ssl_team_write ON public.script_storyboard_links
  FOR ALL
  USING (EXISTS (
    SELECT 1 FROM public.script_chapters sc
    JOIN public.script_projects sp ON sp.id = sc.script_id
    WHERE sc.id = script_storyboard_links.chapter_id
      AND sp.team_id IN (SELECT public.get_user_team_ids(auth.uid()))
  ))
  WITH CHECK (EXISTS (
    SELECT 1 FROM public.script_chapters sc
    JOIN public.script_projects sp ON sp.id = sc.script_id
    WHERE sc.id = script_storyboard_links.chapter_id
      AND sp.team_id IN (SELECT public.get_user_team_ids(auth.uid()))
  ));


-- ── skills (top-level: team_id + is_public flag) ───────────────
ALTER TABLE public.skills ENABLE ROW LEVEL SECURITY;

CREATE POLICY skills_service_role ON public.skills
  FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

-- SELECT: own team OR public skills readable by anyone authed
CREATE POLICY skills_team_or_public_select ON public.skills
  FOR SELECT USING (
    is_public = true
    OR team_id IN (SELECT public.get_user_team_ids(auth.uid()))
  );

CREATE POLICY skills_team_insert ON public.skills
  FOR INSERT WITH CHECK (team_id IN (SELECT public.get_user_team_ids(auth.uid())));

CREATE POLICY skills_team_update ON public.skills
  FOR UPDATE USING (team_id IN (SELECT public.get_user_team_ids(auth.uid())))
             WITH CHECK (team_id IN (SELECT public.get_user_team_ids(auth.uid())));

CREATE POLICY skills_team_delete ON public.skills
  FOR DELETE USING (team_id IN (SELECT public.get_user_team_ids(auth.uid())));


-- ── style_templates (top-level: team_id + is_public flag) ──────
ALTER TABLE public.style_templates ENABLE ROW LEVEL SECURITY;

CREATE POLICY style_templates_service_role ON public.style_templates
  FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

CREATE POLICY style_templates_team_or_public_select ON public.style_templates
  FOR SELECT USING (
    is_public = true
    OR team_id IN (SELECT public.get_user_team_ids(auth.uid()))
  );

CREATE POLICY style_templates_team_insert ON public.style_templates
  FOR INSERT WITH CHECK (team_id IN (SELECT public.get_user_team_ids(auth.uid())));

CREATE POLICY style_templates_team_update ON public.style_templates
  FOR UPDATE USING (team_id IN (SELECT public.get_user_team_ids(auth.uid())))
             WITH CHECK (team_id IN (SELECT public.get_user_team_ids(auth.uid())));

CREATE POLICY style_templates_team_delete ON public.style_templates
  FOR DELETE USING (team_id IN (SELECT public.get_user_team_ids(auth.uid())));
