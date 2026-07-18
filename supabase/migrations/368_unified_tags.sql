-- 368_unified_tags.sql
-- Unified tags: shadow-tag pool (spec docs/superpowers/specs/2026-07-17-unified-tags-design.md)

-- 1) tags.origin: 'curated' = user-managed shelf; 'note' = auto-created from note #tags
ALTER TABLE tags ADD COLUMN IF NOT EXISTS origin VARCHAR(20) NOT NULL DEFAULT 'curated';
ALTER TABLE tags DROP CONSTRAINT IF EXISTS tags_origin_check;
ALTER TABLE tags ADD CONSTRAINT tags_origin_check CHECK (origin IN ('curated', 'note'));

-- 2) note_tags junction (mirror of resource_tags)
CREATE TABLE IF NOT EXISTS note_tags (
  note_id BIGINT NOT NULL REFERENCES inspiration_notes(id) ON DELETE CASCADE,
  tag_id  BIGINT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (note_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_note_tags_tag_id ON note_tags(tag_id);

-- RLS: owner-only via the parent note (后端全走 service_role, 策略防未来直连; 模板同 mig349)
ALTER TABLE note_tags ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS note_tags_owner ON note_tags;
CREATE POLICY note_tags_owner ON note_tags
  FOR ALL
  USING (EXISTS (SELECT 1 FROM inspiration_notes n WHERE n.id = note_id AND n.user_id = auth.uid()))
  WITH CHECK (EXISTS (SELECT 1 FROM inspiration_notes n WHERE n.id = note_id AND n.user_id = auth.uid()));

-- 3) Backfill A: create missing shadow tags (one per user × unseen word)
INSERT INTO tags (name, type, user_id, origin)
SELECT w.word, 'user', w.user_id, 'note'
FROM (
  SELECT DISTINCT n.user_id, lower(t.tag) AS word
  FROM inspiration_notes n, unnest(n.tags) AS t(tag)
  WHERE n.deleted_at IS NULL
) w
WHERE NOT EXISTS (
  SELECT 1 FROM tags tg
  WHERE (lower(tg.name) = w.word OR lower(tg.name_zh) = w.word)
    AND ((tg.type = 'user' AND tg.user_id = w.user_id) OR tg.type IN ('system', 'time'))
)
ON CONFLICT ON CONSTRAINT unique_tag_per_scope DO NOTHING;

-- 4) Backfill B: link every note word to its resolved tag (spec §5 ranking)
INSERT INTO note_tags (note_id, tag_id)
SELECT n.id, resolved.tag_id
FROM inspiration_notes n
CROSS JOIN LATERAL unnest(n.tags) AS t(tag)
CROSS JOIN LATERAL (
  SELECT tg.id AS tag_id
  FROM tags tg
  WHERE (lower(tg.name) = lower(t.tag) OR lower(tg.name_zh) = lower(t.tag))
    AND ((tg.type = 'user' AND tg.user_id = n.user_id) OR tg.type IN ('system', 'time'))
  ORDER BY (tg.type = 'user') DESC, (lower(tg.name) = lower(t.tag)) DESC, tg.created_at ASC
  LIMIT 1
) resolved
WHERE n.deleted_at IS NULL
ON CONFLICT (note_id, tag_id) DO NOTHING;

-- 5) merge_tags v2: also migrate note_tags (spec §4.5)
-- SECURITY DEFINER + in-function ownership checks carried over verbatim from
-- migration 267: the backend calls with the service key, so RLS is bypassed;
-- ownership is enforced here via p_user. Accepts text ids (Python passes
-- snowflake ids as strings) and casts to bigint.
CREATE OR REPLACE FUNCTION merge_tags(
  p_target  text,
  p_sources text[],
  p_user    uuid
) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
  v_target  bigint := p_target::bigint;
  v_sources bigint[];
  v_count   bigint;
  v_bad     int;
BEGIN
  -- Normalize sources: cast to bigint, drop the target if present, dedupe.
  SELECT array_agg(DISTINCT s::bigint)
    INTO v_sources
    FROM unnest(p_sources) AS s
   WHERE s::bigint <> v_target;

  IF v_sources IS NULL OR array_length(v_sources, 1) IS NULL THEN
    RAISE EXCEPTION 'merge_tags: no source tags to merge';
  END IF;

  -- Target must exist and be the caller's own user tag.
  IF NOT EXISTS (
    SELECT 1 FROM tags
     WHERE id = v_target AND type = 'user' AND user_id = p_user
  ) THEN
    RAISE EXCEPTION 'merge_tags: target % is not an owned user tag', v_target;
  END IF;

  -- Every source must be the caller's own user tag.
  SELECT count(*) INTO v_bad FROM tags
   WHERE id = ANY(v_sources) AND (type <> 'user' OR user_id <> p_user);
  IF v_bad > 0 THEN
    RAISE EXCEPTION 'merge_tags: one or more source tags are not owned user tags';
  END IF;

  -- All sources must exist.
  IF (SELECT count(*) FROM tags WHERE id = ANY(v_sources)) <> array_length(v_sources, 1) THEN
    RAISE EXCEPTION 'merge_tags: one or more source tags do not exist';
  END IF;

  -- Re-point resource_tags with dedup against PK (resource_id, tag_id).
  INSERT INTO resource_tags (resource_id, tag_id, source, confidence, created_at)
  SELECT resource_id, v_target, source, confidence, created_at
    FROM resource_tags
   WHERE tag_id = ANY(v_sources)
  ON CONFLICT (resource_id, tag_id) DO NOTHING;

  -- Re-point note_tags with dedup against PK (note_id, tag_id).
  INSERT INTO note_tags (note_id, tag_id, created_at)
  SELECT note_id, v_target, created_at
    FROM note_tags
   WHERE tag_id = ANY(v_sources)
  ON CONFLICT (note_id, tag_id) DO NOTHING;

  -- Delete the source tags (FK ON DELETE CASCADE clears their junction rows).
  DELETE FROM tags WHERE id = ANY(v_sources);

  SELECT count(*) INTO v_count FROM resource_tags WHERE tag_id = v_target;
  RETURN v_count;
END;
$fn$;

REVOKE ALL ON FUNCTION merge_tags(text, text[], uuid) FROM public;
GRANT EXECUTE ON FUNCTION merge_tags(text, text[], uuid) TO service_role, authenticated;

NOTIFY pgrst, 'reload schema';
