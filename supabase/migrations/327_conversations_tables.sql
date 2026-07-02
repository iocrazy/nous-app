-- 327_conversations_tables.sql — Unified Conversation epic Phase 1.
-- Canonical conversation timeline (rebuild; legacy channels* untouched, dropped in Phase 3).
-- Snowflake BIGINT PKs; teams.id BIGINT; auth.users.id + ai_agents.id are UUID.

CREATE TABLE IF NOT EXISTS public.conversations (
  id            BIGINT      PRIMARY KEY DEFAULT generate_snowflake_id(),
  type          TEXT        NOT NULL,                 -- open TEXT (no CHECK): direct_agent|group|public|...
  scope_id      BIGINT      NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
  project_id    BIGINT,
  title         TEXT,
  topic         TEXT,
  history_mode  TEXT        NOT NULL DEFAULT 'shared' CHECK (history_mode IN ('shared','joined')),
  last_seq      BIGINT      NOT NULL DEFAULT 0,
  created_by    UUID        NOT NULL,
  archived_at   TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_conversations_scope ON public.conversations(scope_id) WHERE archived_at IS NULL;

CREATE TABLE IF NOT EXISTS public.conversation_members (
  conversation_id BIGINT      NOT NULL REFERENCES public.conversations(id) ON DELETE CASCADE,
  member_type     TEXT        NOT NULL CHECK (member_type IN ('user','agent')),
  user_id         UUID        REFERENCES auth.users(id) ON DELETE CASCADE,
  agent_id        UUID        REFERENCES public.ai_agents(id) ON DELETE CASCADE,
  role            TEXT        NOT NULL DEFAULT 'member',
  last_read_seq   BIGINT      NOT NULL DEFAULT 0,
  mention_count   INTEGER     NOT NULL DEFAULT 0,
  notify_level    TEXT        NOT NULL DEFAULT 'all' CHECK (notify_level IN ('all','mentions','none')),
  open            BOOLEAN     NOT NULL DEFAULT true,
  added_by        UUID,
  joined_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT conversation_members_one_id CHECK (
    (member_type='user'  AND user_id  IS NOT NULL AND agent_id IS NULL) OR
    (member_type='agent' AND agent_id IS NOT NULL AND user_id  IS NULL)
  )
);
-- one membership per (conversation, member) regardless of type
CREATE UNIQUE INDEX IF NOT EXISTS uq_conversation_members
  ON public.conversation_members (conversation_id, member_type, COALESCE(user_id, agent_id));
CREATE INDEX IF NOT EXISTS idx_conversation_members_user_open
  ON public.conversation_members(user_id, open) WHERE member_type='user';
CREATE INDEX IF NOT EXISTS idx_conversation_members_agent
  ON public.conversation_members(agent_id) WHERE member_type='agent';

CREATE TABLE IF NOT EXISTS public.messages (
  id              BIGINT      PRIMARY KEY DEFAULT generate_snowflake_id(),
  conversation_id BIGINT      NOT NULL REFERENCES public.conversations(id) ON DELETE CASCADE,
  seq             BIGINT      NOT NULL,
  parent_id       BIGINT      REFERENCES public.messages(id) ON DELETE SET NULL,
  sender_type     TEXT        NOT NULL DEFAULT 'user' CHECK (sender_type IN ('user','agent','system')),
  sender_id       UUID,
  from_agent_id   UUID,
  type            TEXT        NOT NULL DEFAULT 'text',   -- open TEXT (no CHECK): text|image|media_card|task_card|system|status
  body            JSONB       NOT NULL DEFAULT '{}'::jsonb,
  edited_at       TIMESTAMPTZ,
  deleted_at      TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (conversation_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_messages_keyset ON public.messages(conversation_id, seq DESC);
CREATE INDEX IF NOT EXISTS idx_messages_parent ON public.messages(conversation_id, parent_id) WHERE parent_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.message_attachments (
  message_id          BIGINT  NOT NULL REFERENCES public.messages(id) ON DELETE CASCADE,
  generated_media_id  BIGINT  NOT NULL,
  ord                 INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (message_id, generated_media_id)
);

CREATE TABLE IF NOT EXISTS public.message_refs (
  message_id BIGINT NOT NULL REFERENCES public.messages(id) ON DELETE CASCADE,
  ref_type   TEXT   NOT NULL,                          -- resource|media|message|issue
  ref_id     TEXT   NOT NULL,
  PRIMARY KEY (message_id, ref_type, ref_id)
);

-- Fold the staged-media work (mig 326): chat uploads now reference a conversation, not a channel.
ALTER TABLE public.generated_media DROP CONSTRAINT IF EXISTS generated_media_channel_id_fkey;
ALTER TABLE public.generated_media RENAME COLUMN channel_id TO conversation_id;
ALTER TABLE public.generated_media
    ADD CONSTRAINT generated_media_conversation_id_fkey
    FOREIGN KEY (conversation_id) REFERENCES public.conversations(id) ON DELETE SET NULL;
ALTER INDEX IF EXISTS idx_genmedia_channel RENAME TO idx_genmedia_conversation;
