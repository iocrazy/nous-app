-- 349_inspiration_notes.sql
-- Inspiration Notes P1 (spec: docs/superpowers/specs/2026-07-07-inspiration-notes-design.md)
-- memos-style quick-capture notes + multi-format attachments + PAT tokens (endpoints in P4).

CREATE TABLE IF NOT EXISTS inspiration_notes (
  id          BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  user_id     UUID NOT NULL,
  content_md  TEXT NOT NULL DEFAULT '',
  tags        TEXT[] NOT NULL DEFAULT '{}',
  ref_hotspot JSONB NULL,
  pinned      BOOLEAN NOT NULL DEFAULT false,
  note_date   DATE NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at  TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_inspiration_notes_user_date
  ON inspiration_notes (user_id, note_date DESC);
CREATE INDEX IF NOT EXISTS idx_inspiration_notes_user_pinned
  ON inspiration_notes (user_id, pinned) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_inspiration_notes_tags
  ON inspiration_notes USING GIN (tags);

CREATE TABLE IF NOT EXISTS inspiration_attachments (
  id              BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  note_id         BIGINT NOT NULL REFERENCES inspiration_notes(id) ON DELETE CASCADE,
  user_id         UUID NOT NULL,
  storage_backend VARCHAR(32) NOT NULL DEFAULT 'supabase',
  bucket          VARCHAR(64) NOT NULL DEFAULT 'inspiration',
  path            TEXT NOT NULL,
  mime            VARCHAR(255) NOT NULL,
  size_bytes      BIGINT NOT NULL,
  original_name   TEXT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_inspiration_attachments_note
  ON inspiration_attachments (note_id);
CREATE INDEX IF NOT EXISTS idx_inspiration_attachments_user
  ON inspiration_attachments (user_id);

CREATE TABLE IF NOT EXISTS inspiration_api_tokens (
  id           BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  user_id      UUID NOT NULL,
  name         VARCHAR(128) NOT NULL,
  token_hash   VARCHAR(64) NOT NULL,
  last_used_at TIMESTAMPTZ NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at   TIMESTAMPTZ NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_inspiration_api_tokens_hash
  ON inspiration_api_tokens (token_hash);

-- RLS: owner-only(后端全走 service_role,策略防未来直连;模板同 canvases)
ALTER TABLE inspiration_notes ENABLE ROW LEVEL SECURITY;
ALTER TABLE inspiration_attachments ENABLE ROW LEVEL SECURITY;
ALTER TABLE inspiration_api_tokens ENABLE ROW LEVEL SECURITY;

CREATE POLICY inspiration_notes_owner ON inspiration_notes
  FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
CREATE POLICY inspiration_attachments_owner ON inspiration_attachments
  FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
CREATE POLICY inspiration_api_tokens_owner ON inspiration_api_tokens
  FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

-- 活动日历聚合(热力图/月历密度)
CREATE OR REPLACE FUNCTION inspiration_activity(p_user_id uuid, p_from date, p_to date)
RETURNS TABLE(day date, cnt bigint)
LANGUAGE sql STABLE AS $$
  SELECT note_date AS day, COUNT(*) AS cnt
  FROM inspiration_notes
  WHERE user_id = p_user_id AND deleted_at IS NULL
    AND note_date BETWEEN p_from AND p_to
  GROUP BY note_date
  ORDER BY note_date;
$$;

-- 标签聚合(侧栏 Tags 面板 + composer 补全)
CREATE OR REPLACE FUNCTION inspiration_tag_counts(p_user_id uuid)
RETURNS TABLE(tag text, cnt bigint)
LANGUAGE sql STABLE AS $$
  SELECT t.tag, COUNT(*) AS cnt
  FROM inspiration_notes n, unnest(n.tags) AS t(tag)
  WHERE n.user_id = p_user_id AND n.deleted_at IS NULL
  GROUP BY t.tag
  ORDER BY cnt DESC, tag;
$$;

-- 附件上限配置(env→DB 铁律;jsonb 值存数字非字符串)
INSERT INTO system_settings (key, value)
VALUES ('inspiration.max_attachment_mb', '500'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- Private bucket for note attachments (same posture as chat-media).
INSERT INTO storage.buckets (id, name, public)
VALUES ('inspiration', 'inspiration', false)
ON CONFLICT (id) DO NOTHING;
