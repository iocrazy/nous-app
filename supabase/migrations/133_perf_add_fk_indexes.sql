-- =============================================================
-- Performance: add btree indexes on FK columns that lacked one.
-- Supabase lint 0001 (unindexed_foreign_keys). Every CASCADE delete
-- or referential integrity lookup otherwise does a full scan on
-- the child table.
-- =============================================================

CREATE INDEX IF NOT EXISTS idx_access_overrides_granted_by ON public.access_overrides (granted_by);
CREATE INDEX IF NOT EXISTS idx_collections_team_id ON public.collections (team_id);
CREATE INDEX IF NOT EXISTS idx_credit_transactions_admin_id ON public.credit_transactions (admin_id);
CREATE INDEX IF NOT EXISTS idx_file_versions_uploaded_by ON public.file_versions (uploaded_by);
CREATE INDEX IF NOT EXISTS idx_folders_created_by ON public.folders (created_by);
CREATE INDEX IF NOT EXISTS idx_libraries_created_by ON public.libraries (created_by);
CREATE INDEX IF NOT EXISTS idx_member_quotas_user_id ON public.member_quotas (user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_created_by ON public.notifications (created_by);
CREATE INDEX IF NOT EXISTS idx_orders_package_id ON public.orders (package_id);
CREATE INDEX IF NOT EXISTS idx_orders_user_id ON public.orders (user_id);
CREATE INDEX IF NOT EXISTS idx_point_transactions_user_id ON public.point_transactions (user_id);
CREATE INDEX IF NOT EXISTS idx_project_collections_created_by ON public.project_collections (created_by);
CREATE INDEX IF NOT EXISTS idx_project_files_folder_id ON public.project_files (folder_id);
CREATE INDEX IF NOT EXISTS idx_project_files_uploaded_by ON public.project_files (uploaded_by);
CREATE INDEX IF NOT EXISTS idx_project_folders_created_by ON public.project_folders (created_by);
CREATE INDEX IF NOT EXISTS idx_project_folders_parent_id ON public.project_folders (parent_id);
CREATE INDEX IF NOT EXISTS idx_project_members_invited_by ON public.project_members (invited_by);
CREATE INDEX IF NOT EXISTS idx_project_tasks_created_by ON public.project_tasks (created_by);
CREATE INDEX IF NOT EXISTS idx_resource_items_added_by ON public.resource_items (added_by);
CREATE INDEX IF NOT EXISTS idx_resource_tags_tag_id ON public.resource_tags (tag_id);
CREATE INDEX IF NOT EXISTS idx_resource_tags_tagged_by ON public.resource_tags (tagged_by);
CREATE INDEX IF NOT EXISTS idx_resource_versions_uploaded_by ON public.resource_versions (uploaded_by);
CREATE INDEX IF NOT EXISTS idx_review_comments_version_id ON public.review_comments (version_id);
CREATE INDEX IF NOT EXISTS idx_review_status_version_id ON public.review_status (version_id);
CREATE INDEX IF NOT EXISTS idx_script_projects_created_by ON public.script_projects (created_by);
CREATE INDEX IF NOT EXISTS idx_script_storyboard_links_storyboard_node_id ON public.script_storyboard_links (storyboard_node_id);
CREATE INDEX IF NOT EXISTS idx_script_storyboard_links_storyboard_project_id ON public.script_storyboard_links (storyboard_project_id);
CREATE INDEX IF NOT EXISTS idx_shares_version_id ON public.shares (version_id);
CREATE INDEX IF NOT EXISTS idx_skills_created_by ON public.skills (created_by);
CREATE INDEX IF NOT EXISTS idx_storyboard_assets_project_id ON public.storyboard_assets (project_id);
CREATE INDEX IF NOT EXISTS idx_storyboard_edges_source_node_id ON public.storyboard_edges (source_node_id);
CREATE INDEX IF NOT EXISTS idx_storyboard_edges_target_node_id ON public.storyboard_edges (target_node_id);
CREATE INDEX IF NOT EXISTS idx_storyboard_projects_created_by ON public.storyboard_projects (created_by);
CREATE INDEX IF NOT EXISTS idx_storyboard_video_assets_project_id ON public.storyboard_video_assets (project_id);
CREATE INDEX IF NOT EXISTS idx_storyboard_video_assets_source_frame_id ON public.storyboard_video_assets (source_frame_id);
CREATE INDEX IF NOT EXISTS idx_storyboard_video_assets_source_node_id ON public.storyboard_video_assets (source_node_id);
CREATE INDEX IF NOT EXISTS idx_style_templates_created_by ON public.style_templates (created_by);
CREATE INDEX IF NOT EXISTS idx_system_settings_updated_by ON public.system_settings (updated_by);
CREATE INDEX IF NOT EXISTS idx_task_assets_added_by ON public.task_assets (added_by);
CREATE INDEX IF NOT EXISTS idx_team_invites_created_by ON public.team_invites (created_by);
CREATE INDEX IF NOT EXISTS idx_teams_owner_id ON public.teams (owner_id);
CREATE INDEX IF NOT EXISTS idx_temp_tokens_user_id ON public.temp_tokens (user_id);
CREATE INDEX IF NOT EXISTS idx_user_notifications_notification_id ON public.user_notifications (notification_id);
