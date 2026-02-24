-- 068_project_announcement.sql
-- Add announcement column to projects table

ALTER TABLE projects ADD COLUMN IF NOT EXISTS announcement TEXT;
COMMENT ON COLUMN projects.announcement IS 'Project announcement (max 100 chars)';
