# Team Chat PHASE-1a — Backend Chat Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Build the backend foundation for human group chat — tables, RLS, per-channel sequence ordering, Realtime publication, and REST endpoints to create channels/DMs, send & list messages (keyset), and track unread — so the chat feature has a tested, working API. Frontend subscription + UI is a separate later plan.

**Architecture:** New tables `channels / channel_members / channel_messages / agent_channels` keyed by Snowflake BIGINT. The backend REST layer uses the privileged `db_engine` connection and enforces channel membership **in application code** (explicit checks in the service). **RLS** on the tables is the second boundary that guards the **frontend's direct Supabase Realtime subscription** (the frontend subscribes to `channel_messages` straight from Supabase; RLS ensures non-members receive no events). Message ordering uses a per-channel monotonic `seq` produced by an atomic `UPDATE channels SET last_message_seq=last_message_seq+1 RETURNING` inside the same transaction as the INSERT. Unread is read-diffusion: `channels.last_message_seq − channel_members.last_read_seq`.

**Tech Stack:** Postgres (Supabase) + asyncpg via `app.db.engine` (named `:params`, `eng.begin()` transactions), FastAPI + Pydantic, pytest (unit with mocked repo + integration in `backend/tests/integration`).

## Global Constraints

- **Migration numbering:** next free numbers after 316 — use `317`, `318`, `319`. SQL in `supabase/migrations/`, applied via CI (no live dev-DB apply available; verify SQL validity by inspection like PHASE-0).
- **Snowflake PKs:** all new BIGINT ids use `DEFAULT generate_snowflake_id()` (`migrations/050`).
- **Existing schema facts (verified):** `teams.id` is BIGINT snowflake; `team_members(team_id BIGINT, user_id UUID, role)`; `get_user_team_ids(p_user_id UUID) RETURNS SETOF BIGINT` is `SECURITY DEFINER STABLE` (`migrations/051`); `auth.uid()` returns the user UUID matching `*.user_id`.
- **RLS helper style:** copy `get_user_team_ids` style for `is_channel_member` (`SECURITY DEFINER STABLE`, `GRANT EXECUTE ... TO authenticated`). RLS subqueries wrap `auth.uid()` as `(SELECT auth.uid())` for initplan performance (spec CHAT-SEC-05).
- **Realtime publication:** copy the guarded DO-block from `migrations/297` (REPLICA IDENTITY FULL + `pg_publication_tables` existence check before `ALTER PUBLICATION supabase_realtime ADD TABLE`). ⚠️ Lesson `bug_alter_publication_drop_if_exists`: guard with `pg_publication_tables`, never bare DROP.
- **DB access:** `from app.db import engine as db_engine`; methods `fetch_all/fetch_one/fetch_val/execute/execute_returning_one` with named `:params`. Multi-statement atomicity: `eng = db_engine.get_engine(); async with eng.begin() as conn: await conn.execute(text(sql), params)`.
- **Snowflake-as-str:** coerce BIGINT params via the repo's existing `_bigint()` pattern if binding snowflake strings (lesson `feedback_asyncpg_bigint_str_strict`).
- **Backend uses db_engine + explicit membership checks; never relies on RLS for backend reads/writes.** RLS is only the frontend-Realtime/PostgREST boundary.
- **Auth:** endpoints take `auth: AuthDep` (`from app.core.deps import AuthDep`); `auth.user_id` is the UUID string.
- **Lint gate before each commit:** black + isort + flake8 on changed `.py` (loguru: f-strings, never `%s`).
- **UI text / i18n:** N/A this phase (backend only).

---

## File Structure

**Migrations**
- `supabase/migrations/317_chat_tables.sql` — 4 tables + indexes.
- `supabase/migrations/318_chat_rls.sql` — `is_channel_member` + `channel_member_joined_at` helpers + RLS policies on all 4 tables.
- `supabase/migrations/319_chat_realtime.sql` — publication + REPLICA IDENTITY FULL for `channel_messages` and `channel_members`.

**Backend**
- `backend/app/schemas/chat.py` — Pydantic request/response models.
- `backend/app/repositories/chat_repository.py` — data access (the seq transaction lives here).
- `backend/app/services/chat_service.py` — orchestration + in-app membership/authorization.
- `backend/app/api/chat_router.py` — REST endpoints; registered in the api_router.
- `backend/tests/integration/test_chat_repository.py` — integration (seq atomicity, keyset, unread) — runs against a Postgres in CI.
- `backend/tests/test_chat_router.py` — router tests with a mocked service.

---

## Task 1: Migration 317 — chat tables

**Files:** Create `supabase/migrations/317_chat_tables.sql`

**Interfaces (Produces):** tables `channels`, `channel_members`, `channel_messages`, `agent_channels` with the columns later tasks reference.

- [ ] **Step 1: Write the migration**

```sql
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
```

- [ ] **Step 2: Verify SQL is well-formed** (no live apply available). Inspect: all FKs reference existing tables (`teams`, `ai_agents`), CHECK values match the spec, snowflake default present. If a local postgres is reachable, dry-run; else rely on CI.
- [ ] **Step 3: Commit** — `git add supabase/migrations/317_chat_tables.sql && git commit -m "feat(chat): migration 317 — chat foundation tables"`

---

## Task 2: Migration 318 — RLS + membership helpers

**Files:** Create `supabase/migrations/318_chat_rls.sql`

**Interfaces (Consumes):** Task 1 tables, existing `get_user_team_ids`. **(Produces):** `is_channel_member(uuid,bigint)`, `channel_member_joined_at(uuid,bigint)`, RLS policies.

- [ ] **Step 1: Write the migration**

```sql
-- 318_chat_rls.sql — Team Chat PHASE-1: RLS guarding the frontend Realtime/PostgREST path.
-- Backend uses db_engine (privileged) + in-app checks; these policies protect direct client access.

-- SECURITY DEFINER helpers (mirror get_user_team_ids style) avoid RLS recursion.
CREATE OR REPLACE FUNCTION public.is_channel_member(p_user UUID, p_channel BIGINT)
RETURNS BOOLEAN LANGUAGE SQL SECURITY DEFINER STABLE AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.channel_members
    WHERE channel_id = p_channel AND user_id = p_user
  );
$$;
GRANT EXECUTE ON FUNCTION public.is_channel_member(UUID, BIGINT) TO authenticated;

CREATE OR REPLACE FUNCTION public.channel_member_joined_at(p_user UUID, p_channel BIGINT)
RETURNS TIMESTAMPTZ LANGUAGE SQL SECURITY DEFINER STABLE AS $$
  SELECT joined_at FROM public.channel_members
  WHERE channel_id = p_channel AND user_id = p_user;
$$;
GRANT EXECUTE ON FUNCTION public.channel_member_joined_at(UUID, BIGINT) TO authenticated;

ALTER TABLE public.channels         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.channel_members  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.channel_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_channels   ENABLE ROW LEVEL SECURITY;

-- channels: public channels visible to the team; group/dm only to members.
-- (CHAT-SEC-02 — non-members cannot even see a private group's existence.)
CREATE POLICY channels_select ON public.channels
  FOR SELECT USING (
    (type = 'public' AND team_id IN (SELECT public.get_user_team_ids((SELECT auth.uid()))))
    OR public.is_channel_member((SELECT auth.uid()), id)
  );

-- channel_members: a member sees the member rows of channels they belong to.
CREATE POLICY channel_members_select ON public.channel_members
  FOR SELECT USING (
    public.is_channel_member((SELECT auth.uid()), channel_id)
  );

-- channel_messages (the critical one): member AND history-mode gate.
CREATE POLICY channel_messages_select ON public.channel_messages
  FOR SELECT USING (
    public.is_channel_member((SELECT auth.uid()), channel_id)
    AND (
      EXISTS (
        SELECT 1 FROM public.channels c
        WHERE c.id = channel_messages.channel_id
          AND (
            c.history_mode = 'shared'
            OR channel_messages.created_at >=
                 public.channel_member_joined_at((SELECT auth.uid()), channel_messages.channel_id)
          )
      )
    )
  );

-- agent_channels: visible to members of the channel.
CREATE POLICY agent_channels_select ON public.agent_channels
  FOR SELECT USING (
    public.is_channel_member((SELECT auth.uid()), channel_id)
  );

-- Service-role full access (backend writes use db_engine which connects privileged;
-- this also covers any service_role PostgREST path). Mirrors migration 064 pattern.
CREATE POLICY channels_service_all ON public.channels FOR ALL
  USING (current_setting('request.jwt.claims', true)::jsonb->>'role' = 'service_role');
CREATE POLICY channel_members_service_all ON public.channel_members FOR ALL
  USING (current_setting('request.jwt.claims', true)::jsonb->>'role' = 'service_role');
CREATE POLICY channel_messages_service_all ON public.channel_messages FOR ALL
  USING (current_setting('request.jwt.claims', true)::jsonb->>'role' = 'service_role');
CREATE POLICY agent_channels_service_all ON public.agent_channels FOR ALL
  USING (current_setting('request.jwt.claims', true)::jsonb->>'role' = 'service_role');
```

- [ ] **Step 2: Verify** — helper signatures match the policy calls; no policy references a column that doesn't exist (cross-check Task 1). No write policies for `authenticated` (frontend writes go through the backend, not direct PostgREST) — confirm that's intended (it is: read-only RLS for clients; backend owns writes).
- [ ] **Step 3: Commit** — `git commit -m "feat(chat): migration 318 — chat RLS + membership helpers"`

---

## Task 3: Migration 319 — Realtime publication

**Files:** Create `supabase/migrations/319_chat_realtime.sql`

**Interfaces (Consumes):** Task 1 tables.

- [ ] **Step 1: Write the migration** (copy the guarded DO-block style from `migrations/297`)

```sql
-- 319_chat_realtime.sql — add chat tables to supabase_realtime so the frontend can subscribe.
-- RLS (318) filters events per subscriber. REPLICA IDENTITY FULL so UPDATE payloads carry full rows.
DO $$
BEGIN
  ALTER TABLE public.channel_messages REPLICA IDENTITY FULL;
  IF NOT EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname='supabase_realtime' AND schemaname='public' AND tablename='channel_messages'
  ) THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.channel_messages;
    RAISE NOTICE 'channel_messages added to supabase_realtime.';
  END IF;

  ALTER TABLE public.channel_members REPLICA IDENTITY FULL;
  IF NOT EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname='supabase_realtime' AND schemaname='public' AND tablename='channel_members'
  ) THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.channel_members;
    RAISE NOTICE 'channel_members added to supabase_realtime.';
  END IF;
END $$;
```

- [ ] **Step 2: Verify** — DO-block guards with `pg_publication_tables` (lesson: never bare ADD/DROP). Commit — `git commit -m "feat(chat): migration 319 — chat Realtime publication"`

---

## Task 4: Pydantic schemas

**Files:** Create `backend/app/schemas/chat.py`; Test: extend `backend/tests/test_chat_router.py` later.

**Interfaces (Produces):** `ChannelCreate`, `ChannelOut`, `MessageCreate`, `MessageOut`, `MemberAdd`, `MarkReadIn`.

- [ ] **Step 1: Write a minimal failing import test** in `backend/tests/test_chat_router.py`:

```python
def test_chat_schemas_importable():
    from app.schemas.chat import (
        ChannelCreate, ChannelOut, MessageCreate, MessageOut, MemberAdd, MarkReadIn,
    )
    c = ChannelCreate(type="group", name="Editing Crew", team_id=1, member_ids=[])
    assert c.type == "group"
```

- [ ] **Step 2: Run → fail** (`uv run pytest tests/test_chat_router.py::test_chat_schemas_importable -v`).
- [ ] **Step 3: Implement `backend/app/schemas/chat.py`**

```python
"""Pydantic models for Team Chat (PHASE-1 backend foundation)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class ChannelCreate(BaseModel):
    type: Literal["group", "dm", "public"]
    team_id: int
    name: Optional[str] = None
    topic: Optional[str] = None
    history_mode: Literal["shared", "joined"] = "shared"
    member_ids: list[str] = Field(default_factory=list)  # UUIDs to add besides the creator


class ChannelOut(BaseModel):
    id: int
    team_id: int
    type: str
    history_mode: str
    name: Optional[str] = None
    topic: Optional[str] = None
    last_message_seq: int = 0
    unread: int = 0
    created_at: datetime


class MemberAdd(BaseModel):
    user_ids: list[str] = Field(default_factory=list)


class MessageCreate(BaseModel):
    content_type: Literal["text", "media_card", "task_card"] = "text"
    body: dict[str, Any] = Field(default_factory=dict)
    reply_to_id: Optional[int] = None


class MessageOut(BaseModel):
    id: int
    channel_id: int
    seq: int
    sender_id: Optional[str] = None
    sender_type: str
    content_type: str
    body: dict[str, Any]
    reply_to_id: Optional[int] = None
    edited_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    created_at: datetime


class MarkReadIn(BaseModel):
    last_read_seq: int
```

- [ ] **Step 4: Run → pass.** Lint. Commit — `git commit -m "feat(chat): chat Pydantic schemas"`

---

## Task 5: chat_repository (the seq transaction)

**Files:** Create `backend/app/repositories/chat_repository.py`; Test: `backend/tests/integration/test_chat_repository.py`.

**Interfaces (Produces):** `ChatRepository` with `create_channel`, `add_members`, `is_member`, `get_my_channels`, `send_message`, `list_messages`, `mark_read`; `get_chat_repository()` factory.

- [ ] **Step 1: Write the integration test** (runs against a Postgres in CI / `tests/integration`). Match the existing integration-test fixtures in `backend/tests/integration/` for DB setup; if they use a session-scoped engine fixture, reuse it.

```python
import pytest

from app.repositories.chat_repository import get_chat_repository

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_send_message_assigns_monotonic_seq(chat_team_and_user):
    repo = get_chat_repository()
    team_id, uid = chat_team_and_user
    ch = await repo.create_channel(
        creator_id=uid, team_id=team_id, type="group", name="T", history_mode="shared", member_ids=[]
    )
    m1 = await repo.send_message(channel_id=ch["id"], sender_id=uid, sender_type="user",
                                 content_type="text", body={"text": "a"}, reply_to_id=None)
    m2 = await repo.send_message(channel_id=ch["id"], sender_id=uid, sender_type="user",
                                 content_type="text", body={"text": "b"}, reply_to_id=None)
    assert m2["seq"] == m1["seq"] + 1


@pytest.mark.asyncio
async def test_unread_and_mark_read(chat_team_and_user):
    repo = get_chat_repository()
    team_id, uid = chat_team_and_user
    ch = await repo.create_channel(creator_id=uid, team_id=team_id, type="group",
                                   name="T2", history_mode="shared", member_ids=[])
    await repo.send_message(channel_id=ch["id"], sender_id=uid, sender_type="user",
                            content_type="text", body={"text": "x"}, reply_to_id=None)
    chans = await repo.get_my_channels(uid)
    target = next(c for c in chans if c["id"] == ch["id"])
    assert target["unread"] == 1
    await repo.mark_read(channel_id=ch["id"], user_id=uid, last_read_seq=target["last_message_seq"])
    chans2 = await repo.get_my_channels(uid)
    assert next(c for c in chans2 if c["id"] == ch["id"])["unread"] == 0


@pytest.mark.asyncio
async def test_list_messages_keyset_desc(chat_team_and_user):
    repo = get_chat_repository()
    team_id, uid = chat_team_and_user
    ch = await repo.create_channel(creator_id=uid, team_id=team_id, type="group",
                                   name="T3", history_mode="shared", member_ids=[])
    for i in range(3):
        await repo.send_message(channel_id=ch["id"], sender_id=uid, sender_type="user",
                                content_type="text", body={"i": i}, reply_to_id=None)
    page = await repo.list_messages(channel_id=ch["id"], before_seq=None, limit=2)
    assert [m["seq"] for m in page] == [3, 2]
    page2 = await repo.list_messages(channel_id=ch["id"], before_seq=2, limit=2)
    assert [m["seq"] for m in page2] == [1]
```

Add a `chat_team_and_user` fixture (in this file or the integration `conftest.py`) that inserts a team + a user into `team_members` and yields `(team_id, user_uuid)`, cleaning up after. Follow the existing integration conftest's insert/cleanup style.

- [ ] **Step 2: Run → fail** (module missing).
- [ ] **Step 3: Implement `backend/app/repositories/chat_repository.py`**

```python
"""Data access for Team Chat (PHASE-1). Uses the privileged db_engine; the
service layer enforces membership. RLS guards the separate frontend path."""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import text

from app.db import engine as db_engine


def _bigint(v: Any) -> int:
    return int(v)


class ChatRepository:
    async def create_channel(
        self, *, creator_id: str, team_id: int, type: str, name: Optional[str],
        history_mode: str, member_ids: list[str],
    ) -> dict[str, Any]:
        eng = db_engine.get_engine()
        async with eng.begin() as conn:
            row = (await conn.execute(text(
                """
                INSERT INTO public.channels (team_id, type, history_mode, name, created_by)
                VALUES (:team_id, :type, :history_mode, :name, :creator)
                RETURNING id, team_id, type, history_mode, name, topic, last_message_seq, created_at
                """
            ), {"team_id": _bigint(team_id), "type": type, "history_mode": history_mode,
                "name": name, "creator": creator_id})).mappings().one()
            cid = row["id"]
            members = {creator_id, *member_ids}
            for uid in members:
                await conn.execute(text(
                    """
                    INSERT INTO public.channel_members (channel_id, user_id, roles)
                    VALUES (:cid, :uid, :roles)
                    ON CONFLICT (channel_id, user_id) DO NOTHING
                    """
                ), {"cid": cid, "uid": uid,
                    "roles": ["owner"] if uid == creator_id else []})
            return dict(row)

    async def add_members(self, *, channel_id: int, user_ids: list[str]) -> int:
        if not user_ids:
            return 0
        eng = db_engine.get_engine()
        async with eng.begin() as conn:
            n = 0
            for uid in user_ids:
                res = await conn.execute(text(
                    """
                    INSERT INTO public.channel_members (channel_id, user_id)
                    VALUES (:cid, :uid)
                    ON CONFLICT (channel_id, user_id) DO NOTHING
                    """
                ), {"cid": _bigint(channel_id), "uid": uid})
                n += res.rowcount or 0
            return n

    async def is_member(self, *, channel_id: int, user_id: str) -> bool:
        v = await db_engine.fetch_val(
            """
            SELECT EXISTS (
              SELECT 1 FROM public.channel_members
              WHERE channel_id = :cid AND user_id = :uid
            )
            """,
            {"cid": _bigint(channel_id), "uid": user_id},
        )
        return bool(v)

    async def get_my_channels(self, user_id: str) -> list[dict[str, Any]]:
        rows = await db_engine.fetch_all(
            """
            SELECT c.id, c.team_id, c.type, c.history_mode, c.name, c.topic,
                   c.last_message_seq, c.created_at,
                   GREATEST(c.last_message_seq - cm.last_read_seq, 0) AS unread
              FROM public.channel_members cm
              JOIN public.channels c ON c.id = cm.channel_id
             WHERE cm.user_id = :uid AND c.is_archived = false AND cm.open = true
             ORDER BY c.last_message_seq DESC
            """,
            {"uid": user_id},
        )
        return [dict(r) for r in rows]

    async def send_message(
        self, *, channel_id: int, sender_id: Optional[str], sender_type: str,
        content_type: str, body: dict[str, Any], reply_to_id: Optional[int],
        from_bot_agent_id: Optional[str] = None,
    ) -> dict[str, Any]:
        import json
        eng = db_engine.get_engine()
        async with eng.begin() as conn:
            seq = (await conn.execute(text(
                """
                UPDATE public.channels
                   SET last_message_seq = last_message_seq + 1
                 WHERE id = :cid
                 RETURNING last_message_seq
                """
            ), {"cid": _bigint(channel_id)})).scalar_one()
            row = (await conn.execute(text(
                """
                INSERT INTO public.channel_messages
                  (channel_id, seq, sender_id, sender_type, content_type, body,
                   reply_to_id, from_bot_agent_id)
                VALUES
                  (:cid, :seq, :sender, :stype, :ctype, CAST(:body AS jsonb),
                   :reply, :bot)
                RETURNING id, channel_id, seq, sender_id, sender_type, content_type,
                          body, reply_to_id, edited_at, deleted_at, created_at
                """
            ), {"cid": _bigint(channel_id), "seq": seq, "sender": sender_id,
                "stype": sender_type, "ctype": content_type, "body": json.dumps(body),
                "reply": reply_to_id, "bot": from_bot_agent_id})).mappings().one()
            return dict(row)

    async def list_messages(
        self, *, channel_id: int, before_seq: Optional[int], limit: int,
    ) -> list[dict[str, Any]]:
        rows = await db_engine.fetch_all(
            """
            SELECT id, channel_id, seq, sender_id, sender_type, content_type, body,
                   reply_to_id, edited_at, deleted_at, created_at
              FROM public.channel_messages
             WHERE channel_id = :cid
               AND deleted_at IS NULL
               AND (:before IS NULL OR seq < :before)
             ORDER BY seq DESC
             LIMIT :limit
            """,
            {"cid": _bigint(channel_id), "before": before_seq, "limit": limit},
        )
        return [dict(r) for r in rows]

    async def mark_read(self, *, channel_id: int, user_id: str, last_read_seq: int) -> None:
        await db_engine.execute(
            """
            UPDATE public.channel_members
               SET last_read_seq = LEAST(
                     GREATEST(last_read_seq, :seq),
                     (SELECT last_message_seq FROM public.channels WHERE id = :cid)
                   ),
                   mention_count = 0
             WHERE channel_id = :cid AND user_id = :uid
            """,
            {"cid": _bigint(channel_id), "uid": user_id, "seq": last_read_seq},
        )


_repo: Optional[ChatRepository] = None


def get_chat_repository() -> ChatRepository:
    global _repo
    if _repo is None:
        _repo = ChatRepository()
    return _repo
```

- [ ] **Step 4: Run integration tests → pass** (`uv run pytest tests/integration/test_chat_repository.py -v -m integration`). If no DB is reachable locally, ensure the tests are correctly marked `integration` so CI runs them; note in the report that local run was skipped for lack of DB.
- [ ] **Step 5: Lint. Commit** — `git commit -m "feat(chat): chat_repository with atomic seq + keyset + unread"`

---

## Task 6: chat_service (in-app authorization)

**Files:** Create `backend/app/services/chat_service.py`.

**Interfaces (Consumes):** `ChatRepository`. **(Produces):** `ChatService` with `create_channel`, `add_members`, `list_my_channels`, `post_message`, `get_messages`, `mark_read` — each enforcing membership; `get_chat_service()`.

- [ ] **Step 1: Write a unit test** (`backend/tests/test_chat_service.py`) with a mocked repo asserting authorization:

```python
import pytest
from unittest.mock import AsyncMock

from app.services.chat_service import ChatService


@pytest.mark.asyncio
async def test_post_message_requires_membership():
    repo = AsyncMock()
    repo.is_member.return_value = False
    svc = ChatService(repo)
    with pytest.raises(PermissionError):
        await svc.post_message(channel_id=1, user_id="u", content_type="text",
                               body={"text": "hi"}, reply_to_id=None)
    repo.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_post_message_member_ok():
    repo = AsyncMock()
    repo.is_member.return_value = True
    repo.send_message.return_value = {"id": 9, "seq": 1}
    svc = ChatService(repo)
    out = await svc.post_message(channel_id=1, user_id="u", content_type="text",
                                 body={"text": "hi"}, reply_to_id=None)
    assert out["seq"] == 1
    repo.send_message.assert_awaited_once()
```

- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement `backend/app/services/chat_service.py`**

```python
"""Team Chat orchestration + in-app authorization (PHASE-1)."""
from __future__ import annotations

from typing import Any, Optional

from app.repositories.chat_repository import ChatRepository, get_chat_repository


class ChatService:
    def __init__(self, repo: Optional[ChatRepository] = None) -> None:
        self._repo = repo or get_chat_repository()

    async def create_channel(self, *, user_id: str, team_id: int, type: str,
                             name: Optional[str], history_mode: str,
                             member_ids: list[str]) -> dict[str, Any]:
        return await self._repo.create_channel(
            creator_id=user_id, team_id=team_id, type=type, name=name,
            history_mode=history_mode, member_ids=member_ids)

    async def list_my_channels(self, *, user_id: str) -> list[dict[str, Any]]:
        return await self._repo.get_my_channels(user_id)

    async def add_members(self, *, channel_id: int, user_id: str,
                          user_ids: list[str]) -> int:
        await self._require_member(channel_id, user_id)
        return await self._repo.add_members(channel_id=channel_id, user_ids=user_ids)

    async def post_message(self, *, channel_id: int, user_id: str, content_type: str,
                           body: dict[str, Any], reply_to_id: Optional[int]) -> dict[str, Any]:
        await self._require_member(channel_id, user_id)
        return await self._repo.send_message(
            channel_id=channel_id, sender_id=user_id, sender_type="user",
            content_type=content_type, body=body, reply_to_id=reply_to_id)

    async def get_messages(self, *, channel_id: int, user_id: str,
                           before_seq: Optional[int], limit: int) -> list[dict[str, Any]]:
        await self._require_member(channel_id, user_id)
        return await self._repo.list_messages(
            channel_id=channel_id, before_seq=before_seq, limit=min(max(limit, 1), 100))

    async def mark_read(self, *, channel_id: int, user_id: str, last_read_seq: int) -> None:
        await self._require_member(channel_id, user_id)
        await self._repo.mark_read(channel_id=channel_id, user_id=user_id,
                                   last_read_seq=last_read_seq)

    async def _require_member(self, channel_id: int, user_id: str) -> None:
        if not await self._repo.is_member(channel_id=channel_id, user_id=user_id):
            raise PermissionError("not a member of this channel")


_svc: Optional[ChatService] = None


def get_chat_service() -> ChatService:
    global _svc
    if _svc is None:
        _svc = ChatService()
    return _svc
```

- [ ] **Step 4: Run → pass.** Lint. Commit — `git commit -m "feat(chat): chat_service with membership authorization"`

---

## Task 7: chat_router (REST) + registration

**Files:** Create `backend/app/api/chat_router.py`; register it where `api_router` aggregates routers (find the include site, e.g. `backend/app/api/__init__.py` or `main.py`).

**Interfaces (Consumes):** `ChatService`, schemas, `AuthDep`.

- [ ] **Step 1: Write router tests** (`backend/tests/test_chat_router.py`, append) with the service mocked + auth overridden (mirror the PHASE-0 router-test pattern):

```python
from typing import Any
from unittest.mock import AsyncMock, patch as _patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.chat_router import router


def _client(svc):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    from app.core.deps import get_auth

    class _Auth:
        user_id = "11111111-1111-1111-1111-111111111111"
        email = "u@example.com"

    async def _grant():
        return _Auth()

    app.dependency_overrides[get_auth] = _grant
    return app, svc


def test_post_message_403_when_not_member():
    svc = AsyncMock()
    svc.post_message.side_effect = PermissionError("nope")
    app, _ = _client(svc)
    with _patch("app.api.chat_router.get_chat_service", return_value=svc):
        c = TestClient(app)
        r = c.post("/api/v1/chat/channels/5/messages", json={"content_type": "text", "body": {"text": "hi"}})
    assert r.status_code == 403


def test_post_message_ok():
    svc = AsyncMock()
    svc.post_message.return_value = {
        "id": 9, "channel_id": 5, "seq": 1, "sender_id": "u", "sender_type": "user",
        "content_type": "text", "body": {"text": "hi"}, "reply_to_id": None,
        "edited_at": None, "deleted_at": None, "created_at": "2026-06-25T00:00:00Z",
    }
    app, _ = _client(svc)
    with _patch("app.api.chat_router.get_chat_service", return_value=svc):
        c = TestClient(app)
        r = c.post("/api/v1/chat/channels/5/messages", json={"content_type": "text", "body": {"text": "hi"}})
    assert r.status_code == 200
    assert r.json()["seq"] == 1
```

- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement `backend/app/api/chat_router.py`** (map `PermissionError` → 403)

```python
"""Team Chat REST endpoints (PHASE-1 backend foundation)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status

from app.core.deps import AuthDep
from app.schemas.chat import (
    ChannelCreate, ChannelOut, MarkReadIn, MemberAdd, MessageCreate, MessageOut,
)
from app.services.chat_service import get_chat_service

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/channels", response_model=list[ChannelOut])
async def list_my_channels(auth: AuthDep):
    svc = get_chat_service()
    return await svc.list_my_channels(user_id=auth.user_id)


@router.post("/channels", response_model=ChannelOut)
async def create_channel(payload: ChannelCreate, auth: AuthDep):
    svc = get_chat_service()
    ch = await svc.create_channel(
        user_id=auth.user_id, team_id=payload.team_id, type=payload.type,
        name=payload.name, history_mode=payload.history_mode, member_ids=payload.member_ids)
    return {**ch, "unread": 0}


@router.post("/channels/{channel_id}/members")
async def add_members(channel_id: int, payload: MemberAdd, auth: AuthDep):
    svc = get_chat_service()
    try:
        added = await svc.add_members(channel_id=channel_id, user_id=auth.user_id,
                                      user_ids=payload.user_ids)
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not a member")
    return {"added": added}


@router.get("/channels/{channel_id}/messages", response_model=list[MessageOut])
async def list_messages(channel_id: int, auth: AuthDep,
                        before_seq: Optional[int] = Query(default=None),
                        limit: int = Query(default=30, ge=1, le=100)):
    svc = get_chat_service()
    try:
        return await svc.get_messages(channel_id=channel_id, user_id=auth.user_id,
                                      before_seq=before_seq, limit=limit)
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not a member")


@router.post("/channels/{channel_id}/messages", response_model=MessageOut)
async def post_message(channel_id: int, payload: MessageCreate, auth: AuthDep):
    svc = get_chat_service()
    try:
        return await svc.post_message(channel_id=channel_id, user_id=auth.user_id,
                                      content_type=payload.content_type, body=payload.body,
                                      reply_to_id=payload.reply_to_id)
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not a member")


@router.post("/channels/{channel_id}/read")
async def mark_read(channel_id: int, payload: MarkReadIn, auth: AuthDep):
    svc = get_chat_service()
    try:
        await svc.mark_read(channel_id=channel_id, user_id=auth.user_id,
                            last_read_seq=payload.last_read_seq)
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not a member")
    return {"ok": True}
```

- [ ] **Step 4: Register the router.** Find where other routers are included (grep `include_router` in `backend/app/api/__init__.py` and `backend/app/main.py`). Add `from app.api.chat_router import router as chat_router` and `api_router.include_router(chat_router)` in the SAME place/pattern the other feature routers use (so it lands under `/api/v1`). Verify with `uv run python -c "import app.main"`.
- [ ] **Step 5: Run router tests → pass.** Lint. Commit — `git commit -m "feat(chat): chat REST endpoints + registration"`

---

## Self-Review

**Spec coverage:** DATA-01~10 (Task 1), SEC-01~08 (Tasks 2; SEC-AGENT enforcement is PHASE-2), UNREAD-01/02/04/05 (Tasks 5–7; UNREAD-03 mention write-fanout deferred to PHASE-2 when @mention parsing lands), RT-01/02 (Task 3; RT-03/04/05 frontend deferred), MSG-01/03/04 partial + keyset MSG-01 (Task 5; MSG-02 seq monotonic guaranteed by the transaction; edit/delete endpoints deferred — only soft-delete column + list filter exist now).

**Explicit deferrals (no silent caps):**
- Frontend (Realtime subscription + chat UI) — separate PHASE-1b/PHASE-5 plan.
- Edit/delete message endpoints — columns + list filter exist; endpoints deferred.
- @mention write-fanout (UNREAD-03) + agent summon/broadcast (PHASE-2).
- Backend writes are authorized in-app; RLS has read-only client policies (no client write policies) — intentional (writes go through REST).

**Type/name consistency:** `seq`, `last_message_seq`, `last_read_seq`, `channel_id`, `history_mode`, `content_type` identical across migrations, repo, service, router, schemas. `_bigint()` coerces snowflake params. PermissionError → 403 in every endpoint.

**Integration-test caveat:** repo tests are `@pytest.mark.integration` and need a Postgres; if none is reachable locally they run in CI. Router/service tests are pure-mock and run anywhere.

---

## Execution Handoff
After approval: execute via superpowers:subagent-driven-development (fresh implementer per task + task review), then finishing-a-development-branch.
