-- Migration 131's regex covered uid()/role() but missed three
-- policies that call jwt() / current_setting() unwrapped.

DROP POLICY "Service role full access on user_tag_preferences" ON public.user_tag_preferences;
CREATE POLICY "Service role full access on user_tag_preferences" ON public.user_tag_preferences
  AS PERMISSIVE FOR ALL TO public
  USING ((SELECT auth.role()) = 'service_role');

DROP POLICY "Service role full access on user_cookies" ON public.user_cookies;
CREATE POLICY "Service role full access on user_cookies" ON public.user_cookies
  AS PERMISSIVE FOR ALL TO public
  USING ((SELECT auth.role()) = 'service_role');

DROP POLICY unified_tasks_service_all ON public.unified_tasks;
CREATE POLICY unified_tasks_service_all ON public.unified_tasks
  AS PERMISSIVE FOR ALL TO public
  USING ((SELECT auth.role()) = 'service_role')
  WITH CHECK ((SELECT auth.role()) = 'service_role');
