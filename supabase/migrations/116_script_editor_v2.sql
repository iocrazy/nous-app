-- 116_script_editor_v2.sql
-- Script Editor v2: add content_json and genre fields

ALTER TABLE script_chapters
  ADD COLUMN IF NOT EXISTS content_json JSONB;

ALTER TABLE script_projects
  ADD COLUMN IF NOT EXISTS genre VARCHAR(50);

-- Backfill: wrap existing content as basic TipTap JSON
UPDATE script_chapters
SET content_json = jsonb_build_object(
  'type', 'doc',
  'content', jsonb_build_array(
    jsonb_build_object(
      'type', 'paragraph',
      'content', jsonb_build_array(
        jsonb_build_object('type', 'text', 'text', COALESCE(content, ''))
      )
    )
  )
)
WHERE content_json IS NULL AND content IS NOT NULL AND content != '';

COMMENT ON COLUMN script_chapters.content_json IS 'TipTap ProseMirror JSON document (source of truth)';
COMMENT ON COLUMN script_chapters.content IS 'Plain text derived from content_json (for search and AI)';
COMMENT ON COLUMN script_projects.genre IS 'Story genre/style';
