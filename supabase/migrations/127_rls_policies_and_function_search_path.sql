-- =============================================================
-- 1) Add missing policies for tables with RLS on but no policy
-- 2) Batch-lock function search_path for all WARN'd functions
-- (Idempotent: DROP IF EXISTS policies; ALTER FUNCTION is naturally
-- idempotent — re-pinning already-pinned is a no-op.)
-- =============================================================

-- ── daily_point_gifts ────────────────────────────────────────
DROP POLICY IF EXISTS daily_gifts_service_role ON public.daily_point_gifts;
CREATE POLICY daily_gifts_service_role ON public.daily_point_gifts
  FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

DROP POLICY IF EXISTS daily_gifts_user_select ON public.daily_point_gifts;
CREATE POLICY daily_gifts_user_select ON public.daily_point_gifts
  FOR SELECT USING (user_id = auth.uid());


-- ── resource_access_logs ─────────────────────────────────────
DROP POLICY IF EXISTS resource_access_logs_service_role ON public.resource_access_logs;
CREATE POLICY resource_access_logs_service_role ON public.resource_access_logs
  FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

DROP POLICY IF EXISTS resource_access_logs_user_select ON public.resource_access_logs;
CREATE POLICY resource_access_logs_user_select ON public.resource_access_logs
  FOR SELECT USING (user_id = auth.uid());


-- ── Batch-lock search_path on WARN'd functions (fixes
-- lint 0011_function_search_path_mutable). Using
-- pg_catalog + public resolves the attack surface without
-- changing call-site behaviour.
ALTER FUNCTION public.update_daily_statistics()                                                       SET search_path = public, pg_catalog;
ALTER FUNCTION public.get_user_team_ids(uuid)                                                         SET search_path = public, pg_catalog;
ALTER FUNCTION public.get_user_team_ids_text(uuid)                                                    SET search_path = public, pg_catalog;
ALTER FUNCTION public.update_updated_at_column()                                                      SET search_path = public, pg_catalog;
ALTER FUNCTION public.update_video_analysis_timestamp()                                               SET search_path = public, pg_catalog;
ALTER FUNCTION public.update_video_view_stats()                                                       SET search_path = public, pg_catalog;
ALTER FUNCTION public.add_owner_as_member()                                                           SET search_path = public, pg_catalog;
ALTER FUNCTION public.generate_invite_code()                                                          SET search_path = public, pg_catalog;
ALTER FUNCTION public.generate_team_invite_code()                                                     SET search_path = public, pg_catalog;
ALTER FUNCTION public.update_unified_tasks_updated_at()                                               SET search_path = public, pg_catalog;
ALTER FUNCTION public.check_orphan_resource()                                                         SET search_path = public, pg_catalog;
ALTER FUNCTION public.increment_api_key_usage(p_key_id character varying)                             SET search_path = public, pg_catalog;
ALTER FUNCTION public.notify_team_members()                                                           SET search_path = public, pg_catalog;
ALTER FUNCTION public.set_invite_code()                                                               SET search_path = public, pg_catalog;
ALTER FUNCTION public.get_popular_searches(days_back integer, max_results integer)                    SET search_path = public, pg_catalog;
ALTER FUNCTION public.get_search_analytics(days_back integer)                                         SET search_path = public, pg_catalog;
ALTER FUNCTION public.set_team_invite_code()                                                          SET search_path = public, pg_catalog;
ALTER FUNCTION public.initialize_user_credits()                                                       SET search_path = public, pg_catalog;
ALTER FUNCTION public.update_system_status_updated_at()                                               SET search_path = public, pg_catalog;
ALTER FUNCTION public.generate_snowflake_id()                                                         SET search_path = public, pg_catalog;
ALTER FUNCTION public.get_cleanup_suggestions(p_user_id uuid, p_never_viewed_days integer, p_old_unused_days integer, p_limit integer) SET search_path = public, pg_catalog;
ALTER FUNCTION public.get_cleanup_stats(p_user_id uuid)                                               SET search_path = public, pg_catalog;
ALTER FUNCTION public.get_cleanup_data(p_user_id uuid, p_never_viewed_days integer, p_old_unused_days integer, p_limit integer) SET search_path = public, pg_catalog;
ALTER FUNCTION public.get_user_tag_counts(p_user_id uuid, p_limit integer)                            SET search_path = public, pg_catalog;
ALTER FUNCTION public.get_dashboard_stats(p_user_id uuid)                                             SET search_path = public, pg_catalog;
ALTER FUNCTION public.delete_team_with_cleanup(target_team_id bigint)                                 SET search_path = public, pg_catalog;
ALTER FUNCTION public.get_tag_counts_by_ids(p_tag_ids text[])                                         SET search_path = public, pg_catalog;
ALTER FUNCTION public.update_sb_updated_at()                                                          SET search_path = public, pg_catalog;
ALTER FUNCTION public.update_script_projects_updated_at()                                             SET search_path = public, pg_catalog;
ALTER FUNCTION public.update_script_chapters_updated_at()                                             SET search_path = public, pg_catalog;
ALTER FUNCTION public.update_user_settings_updated_at()                                               SET search_path = public, pg_catalog;
