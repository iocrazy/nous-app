-- 317_chat_tables.sql — Team Chat PHASE-1: human group-chat tables.
-- Snowflake BIGINT PKs (migrations/050). teams.id is BIGINT; team_members.user_id is UUID.

CREATE TABLE IF NOT EXISTS public.channels (
  id               BIGINT      PRIMARY KEY DEFAULT generate_snowflake_id(),
  team_id          BIGINT      NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
  type             TEXT        NOT NULL CHECK (type IN ('dm','group','public')),
  history_mode     TEXT        NOT NULL DEFAULT 'shared' CHECK (history_mode IN ('shared','joined')),
  last_message_seq BIGINT      NOT NULL DEFAULT 0,
  name             TEXT,
  topic            TEXT,
  created_by       UUID        NOT NULL,
  is_archived      BOOLEAN     NOT NULL DEFAULT false,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_channels_team ON public.channels(team_id) WHERE is_archived = false;

CREATE TABLE IF NOT EXISTS public.channel_members (
  channel_id    BIGINT      NOT NULL REFERENCES public.channels(id) ON DELETE CASCADE,
  user_id       UUID        NOT NULL,
  last_read_seq BIGINT      NOT NULL DEFAULT 0,
  mention_count INTEGER     NOT NULL DEFAULT 0,
  roles         TEXT[]      NOT NULL DEFAULT '{}',
  open          BOOLEAN     NOT NULL DEFAULT true,
  notify_level  TEXT        NOT NULL DEFAULT 'all' CHECK (notify_level IN ('all','mentions','none')),
  joined_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (channel_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_channel_members_user_open ON public.channel_members(user_id, open);

CREATE TABLE IF NOT EXISTS public.channel_messages (
  id               BIGINT      PRIMARY KEY DEFAULT generate_snowflake_id(),
  channel_id       BIGINT      NOT NULL REFERENCES public.channels(id) ON DELETE CASCADE,
  seq              BIGINT      NOT NULL,
  sender_id        UUID,
  sender_type      TEXT        NOT NULL DEFAULT 'user' CHECK (sender_type IN ('user','agent')),
  content_type     TEXT        NOT NULL DEFAULT 'text' CHECK (content_type IN ('text','media_card','task_card','system')),
  body             JSONB       NOT NULL DEFAULT '{}'::jsonb,
  reply_to_id      BIGINT      REFERENCES public.channel_messages(id) ON DELETE SET NULL,
  from_bot_agent_id UUID,
  edited_at        TIMESTAMPTZ,
  deleted_at       TIMESTAMPTZ,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (channel_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_channel_messages_keyset ON public.channel_messages(channel_id, seq DESC);

CREATE TABLE IF NOT EXISTS public.agent_channels (
  agent_id   UUID        NOT NULL REFERENCES public.ai_agents(id) ON DELETE CASCADE,
  channel_id BIGINT      NOT NULL REFERENCES public.channels(id) ON DELETE CASCADE,
  added_by   UUID        NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (agent_id, channel_id)
);
