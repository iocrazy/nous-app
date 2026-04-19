-- =============================================================
-- Add FK indexes that migration 134's bulk unused-index DROP removed.
-- Lint 0001 was re-satisfied by migration 133 for 42 FKs; these 34
-- FKs still lack an index. FK indexes matter for CASCADE deletes and
-- every referential join.
-- =============================================================

CREATE INDEX IF NOT EXISTS idx_access_overrides_user_id ON public.access_overrides (user_id);
CREATE INDEX IF NOT EXISTS idx_api_key_logs_api_key_id ON public.api_key_logs (api_key_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_admin_id ON public.audit_logs (admin_id);
CREATE INDEX IF NOT EXISTS idx_collections_owner_id ON public.collections (owner_id);
CREATE INDEX IF NOT EXISTS idx_credit_transactions_user_id ON public.credit_transactions (user_id);
CREATE INDEX IF NOT EXISTS idx_orders_team_id ON public.orders (team_id);
CREATE INDEX IF NOT EXISTS idx_project_members_user_id ON public.project_members (user_id);
CREATE INDEX IF NOT EXISTS idx_project_tasks_assignee_id ON public.project_tasks (assignee_id);
CREATE INDEX IF NOT EXISTS idx_project_tasks_workflow_node_id ON public.project_tasks (workflow_node_id);
CREATE INDEX IF NOT EXISTS idx_projects_workflow_id ON public.projects (workflow_id);
CREATE INDEX IF NOT EXISTS idx_resource_access_logs_user_id ON public.resource_access_logs (user_id);
CREATE INDEX IF NOT EXISTS idx_review_annotations_comment_id ON public.review_annotations (comment_id);
CREATE INDEX IF NOT EXISTS idx_review_comments_author_id ON public.review_comments (author_id);
CREATE INDEX IF NOT EXISTS idx_review_status_reviewer_id ON public.review_status (reviewer_id);
CREATE INDEX IF NOT EXISTS idx_script_assets_script_id ON public.script_assets (script_id);
CREATE INDEX IF NOT EXISTS idx_script_chapters_script_id ON public.script_chapters (script_id);
CREATE INDEX IF NOT EXISTS idx_script_chapters_parent_chapter_id ON public.script_chapters (parent_chapter_id);
CREATE INDEX IF NOT EXISTS idx_script_projects_team_id ON public.script_projects (team_id);
CREATE INDEX IF NOT EXISTS idx_script_storyboard_links_chapter_id ON public.script_storyboard_links (chapter_id);
CREATE INDEX IF NOT EXISTS idx_search_logs_user_id ON public.search_logs (user_id);
CREATE INDEX IF NOT EXISTS idx_share_views_viewer_id ON public.share_views (viewer_id);
CREATE INDEX IF NOT EXISTS idx_shares_project_file_id ON public.shares (project_file_id);
CREATE INDEX IF NOT EXISTS idx_shares_team_id ON public.shares (team_id);
CREATE INDEX IF NOT EXISTS idx_shares_library_id ON public.shares (library_id);
CREATE INDEX IF NOT EXISTS idx_skills_project_id ON public.skills (project_id);
CREATE INDEX IF NOT EXISTS idx_storyboard_frame_characters_character_id ON public.storyboard_frame_characters (character_id);
CREATE INDEX IF NOT EXISTS idx_storyboard_projects_team_id ON public.storyboard_projects (team_id);
CREATE INDEX IF NOT EXISTS idx_style_templates_team_id ON public.style_templates (team_id);
CREATE INDEX IF NOT EXISTS idx_tags_user_id ON public.tags (user_id);
CREATE INDEX IF NOT EXISTS idx_tags_group_id ON public.tags (group_id);
CREATE INDEX IF NOT EXISTS idx_task_assets_task_id ON public.task_assets (task_id);
CREATE INDEX IF NOT EXISTS idx_task_assets_file_id ON public.task_assets (file_id);
CREATE INDEX IF NOT EXISTS idx_user_logs_user_id ON public.user_logs (user_id);
CREATE INDEX IF NOT EXISTS idx_workflow_nodes_workflow_id ON public.workflow_nodes (workflow_id);
