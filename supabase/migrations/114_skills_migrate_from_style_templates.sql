-- 114_skills_migrate_from_style_templates.sql
-- Migrate style_templates → skills: add new columns, rename table

-- 1. Add new columns to style_templates
ALTER TABLE style_templates ADD COLUMN IF NOT EXISTS project_id bigint REFERENCES projects(id);
ALTER TABLE style_templates ADD COLUMN IF NOT EXISTS status varchar(20) DEFAULT 'active';
ALTER TABLE style_templates ADD COLUMN IF NOT EXISTS icon varchar(20) DEFAULT '✨';
ALTER TABLE style_templates ADD COLUMN IF NOT EXISTS output_format text;
ALTER TABLE style_templates ADD COLUMN IF NOT EXISTS trigger_keywords text[] DEFAULT '{}';

-- 2. Rename prompt_content → content_md
ALTER TABLE style_templates RENAME COLUMN prompt_content TO content_md;

-- 3. Rename table
ALTER TABLE style_templates RENAME TO skills;

-- 4. Rename existing indexes
ALTER INDEX IF EXISTS idx_style_templates_team_id RENAME TO idx_skills_team_id;
ALTER INDEX IF EXISTS idx_style_templates_category RENAME TO idx_skills_category;

-- 5. Add new indexes
CREATE INDEX IF NOT EXISTS idx_skills_project_id ON skills(project_id) WHERE project_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status);

-- 6. Set all existing rows to active
UPDATE skills SET status = 'active' WHERE status IS NULL;

-- 7. Insert system presets (idempotent)
INSERT INTO skills (team_id, name, description, content_md, category, is_public, icon, status)
VALUES
  (NULL, 'Short Video Script', '30-60s short-form video script structure',
   E'You are a short-form video scriptwriter.\n\nRules:\n- Target duration: 30-60 seconds\n- Hook in first 3 seconds\n- One clear message per video\n- End with call to action\n\nStructure:\n1. Hook (0-3s)\n2. Problem/Setup (3-15s)\n3. Solution/Content (15-45s)\n4. CTA (45-60s)',
   'script', true, '🎬', 'active'),
  (NULL, 'Film Storyboard', 'Professional storyboard format with shot types',
   E'You are a professional storyboard artist.\n\nFor each scene, specify:\n- Shot type (wide/medium/close-up/extreme close-up)\n- Camera angle (eye level/high/low/dutch/bird''s eye)\n- Camera movement (static/pan/tilt/dolly/crane/handheld)\n- Lighting (key light direction, mood)\n- Duration estimate\n- Transition to next scene\n\nUse cinematic language. Be specific about composition.',
   'storyboard', true, '🎞️', 'active'),
  (NULL, 'Product Copywriting', 'AIDA structure marketing copy',
   E'You are a marketing copywriter using the AIDA framework.\n\nStructure:\n1. **Attention** — Bold headline that stops the scroll\n2. **Interest** — Problem statement the reader relates to\n3. **Desire** — Benefits (not features) with social proof\n4. **Action** — Clear, urgent CTA\n\nTone: conversational, confident, specific. Use numbers and specifics over vague claims.',
   'copywriting', true, '✍️', 'active'),
  (NULL, 'Social Media Post', 'Platform-specific social content',
   E'You are a social media content creator.\n\nAdapt content for the specified platform:\n- **Instagram**: Visual-first, 2200 char max, 30 hashtags max, emoji-friendly\n- **Twitter/X**: 280 chars, punchy, thread-friendly\n- **LinkedIn**: Professional tone, storytelling, 3000 chars\n- **TikTok**: Script for spoken word, casual, trend-aware\n\nAlways include: hook, value, CTA.',
   'copywriting', true, '📱', 'active'),
  (NULL, 'Script to Storyboard', 'Convert written script to visual storyboard',
   E'You are a script-to-storyboard converter.\n\nFor each scene in the script:\n1. Identify the key visual moment\n2. Describe the frame composition\n3. Note character positions and expressions\n4. Specify shot type and camera angle\n5. Add timing/duration\n6. Note any VFX or special requirements\n\nOutput as a numbered scene list with consistent formatting.',
   'storyboard', true, '🔄', 'active')
ON CONFLICT DO NOTHING;
