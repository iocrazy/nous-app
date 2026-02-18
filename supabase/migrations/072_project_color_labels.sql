-- 072_project_color_labels.sql
-- Add color_label column to projects for visual organization

ALTER TABLE projects ADD COLUMN IF NOT EXISTS color_label VARCHAR(20);
