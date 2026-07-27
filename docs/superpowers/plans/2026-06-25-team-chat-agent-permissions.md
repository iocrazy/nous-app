# Team Chat PHASE-0 — Agent Chat Permissions (AI Library) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let admins configure, in AI Library (Settings → AI Library → Agents), which agents may participate in Team Chat — gating chat entry, reading team files, and auto-broadcasting — defaulting to "denied" for every agent.

**Architecture:** Reuse the existing `ai_agents.capability_profile` JSONB (Phase 4.5 capability gating) by adding a `chat` sub-object. A fail-closed parser (`agent_chat_caps`) reads it. The AI Library agent editor gets a new "Permissions" sub-tab that PATCHes the permissions, deep-merging into `capability_profile` without clobbering the existing Phase 4.5 keys. System-preset agents get a narrow carve-out: only `chat_permissions` is patchable; all other fields stay read-only.

**Tech Stack:** FastAPI + Pydantic + asyncpg/Supabase (Postgres JSONB), React 19 + TypeScript + Tailwind (island UI), i18next, pytest.

**Scope note:** This plan delivers the **data model + parser + AI Library config UI + schema/guard** only (spec IDs CHAT-PERM-01~07, 13~18). The 4 runtime enforcement gates (CHAT-PERM-08~12) depend on the chat tables and are implemented in CHAT-PHASE-2, consuming the `agent_chat_caps` helper built here. This plan ships independently: with no chat tables yet, the permission flags are stored and displayed; nothing reads them for enforcement until PHASE-2.

## Global Constraints

- **No new tables.** Permissions live in `ai_agents.capability_profile` JSONB (`backend/app/models/ai.py:126`).
- **JSONB writes MUST deep-merge**, never replace the whole object — preserve existing keys `tool_blacklist / allowed_skills / max_parallel_delegates / context_budget_tokens / rate_limit_tool_calls_per_min` (lesson: `bug_user_settings_json_clobber`).
- **Fail-closed:** absent / wrong-typed chat config ⇒ all capabilities `false`, `allowed_team_ids` empty. Only a literal JSON `true` counts as enabled.
- **System-preset carve-out:** the existing 403 at `ai_library_router.py:481` must still block content edits on presets, but allow `chat_permissions`.
- **Migration numbering:** next is `316` (315 already used twice). SQL goes in `supabase/migrations/`, applied via PR + CI (`feedback_sql_belongs_in_migrations`).
- **Backend lint gate before commit:** run `black`, `isort`, `flake8` on changed `.py` (`reference_ci_backend_lint_black`).
- **UI:** English text + i18n keys (camelCase keys, Title Case copy). Island UI: zero emoji, no `zinc-*`, use `ink-*`/`indigo`/`amber` tokens.
- **Snowflake BIGINT:** team ids are BIGINT; on the wire treat as numbers but tolerate string coercion (`agent_chat_caps` coerces via `int()`).

---

## File Structure

**Backend**
- Create `supabase/migrations/316_agent_chat_permissions.sql` — column comment + partial index on chat-enabled predicate.
- Create `backend/app/services/ai/permissions/__init__.py` — package marker.
- Create `backend/app/services/ai/permissions/agent_chat_caps.py` — `ChatCaps` frozen dataclass + `agent_chat_caps()` fail-closed parser. Single responsibility: read capability_profile.chat.
- Modify `backend/app/schemas/ai_library.py` — add `ChatPermissionsIn` (write) + `ChatPermissionsOut` (read); add `chat_permissions` to **both** `AgentUpdate` and `AgentOut` (review C1).
- Modify `backend/app/api/ai_library_router.py` — preset carve-out + deep-merge into `capability_profile`; `_can_edit_chat_permissions` role gate (review H1); `_with_chat_permissions` response enrich applied to list/get/patch (review C1); audit log on change (review M3).
- Create `backend/tests/test_agent_chat_caps.py` — unit tests for the parser.
- Create `backend/tests/test_ai_library_agent_permissions.py` — router tests (merge + preset carve-out).

**Frontend**
- Modify `frontend/types.ts:1018` — add `AgentChatPermissions` + `capability_profile` on `AILibraryAgent`.
- Modify `frontend/services/aiLibraryService.ts:82` — `updateAgentChatPermissions(slug, perms)` helper (wraps PATCH with `chat_permissions`).
- Create `frontend/components/AILibrary/PermissionsSection.tsx` — the Permissions sub-tab UI (4 controls).
- Modify `frontend/components/AILibrary/AgentEditor.tsx:41,364,465` — add `'permissions'` sub-tab + render.
- Modify `frontend/components/AILibrary/AILibrarySidebar.tsx:178` — small "Chat" badge on chat-enabled agents.
- Modify `frontend/public/locales/en.json` + `frontend/public/locales/zh.json` — `aiLibrary.permissions.*` keys.

---

## Task 1: Migration — document chat keys + index chat-enabled predicate

**Files:**
- Create: `supabase/migrations/316_agent_chat_permissions.sql`

**Interfaces:**
- Produces: a partial index `idx_ai_agents_chat_enabled` and an updated column comment. Later tasks/phases query `capability_profile -> 'chat' ->> 'enabled' = 'true'` for the add-agent picker.

- [ ] **Step 1: Write the migration SQL**

```sql
-- 316_agent_chat_permissions.sql
-- Team Chat PHASE-0: agent chat capability gating lives inside the existing
-- ai_agents.capability_profile JSONB (Phase 4.5). We add a documented `chat`
-- sub-object: {enabled, read_team_resources, auto_broadcast, allowed_team_ids}.
-- Absent/false everywhere = the agent CANNOT chat (fail-closed; see
-- backend/app/services/ai/permissions/agent_chat_caps.py).
-- No column change is needed (JSONB is schemaless); this migration documents the
-- convention and indexes the "chat-enabled" predicate used by the
-- add-agent-to-group picker and the AI Library "Chat" badge.

COMMENT ON COLUMN public.ai_agents.capability_profile IS
  'Phase 4.5 capability gating + Team Chat PHASE-0. Keys: tool_blacklist, '
  'allowed_skills, max_parallel_delegates, context_budget_tokens, '
  'rate_limit_tool_calls_per_min, '
  'chat{enabled,read_team_resources,auto_broadcast,allowed_team_ids}. '
  'Empty/absent = ungated EXCEPT chat, which is fail-closed (absent = cannot chat).';

-- NOTE: chat.enabled is stored as a JSON boolean (true/false). The `->>`
-- operator textualizes it to the strings 'true'/'false', so the predicate
-- compares against the text 'true' — consistent with how asyncpg/PostgREST
-- write JSON booleans. (agent_chat_caps still requires a real bool when reading.)
CREATE INDEX IF NOT EXISTS idx_ai_agents_chat_enabled
  ON public.ai_agents ((capability_profile -> 'chat' ->> 'enabled'))
  WHERE (capability_profile -> 'chat' ->> 'enabled') = 'true';
```

- [ ] **Step 2: Apply to the dev DB and verify it parses + index exists**

Run (NAS dev DB per `reference_nas_dev_db`; substitute your dev DSN):
```bash
psql "$MEDIAHUB_DEV_DSN" -f supabase/migrations/316_agent_chat_permissions.sql
psql "$MEDIAHUB_DEV_DSN" -c "SELECT indexname FROM pg_indexes WHERE indexname='idx_ai_agents_chat_enabled';"
```
Expected: `CREATE INDEX` then a row `idx_ai_agents_chat_enabled`.

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/316_agent_chat_permissions.sql
git commit -m "feat(chat): migration 316 — document agent chat capability keys + index"
```

---

## Task 2: Backend `agent_chat_caps` parser (fail-closed, pure function)

**Files:**
- Create: `backend/app/services/ai/permissions/__init__.py`
- Create: `backend/app/services/ai/permissions/agent_chat_caps.py`
- Test: `backend/tests/test_agent_chat_caps.py`

**Interfaces:**
- Produces: `ChatCaps` (frozen dataclass: `enabled: bool`, `read_team_resources: bool`, `auto_broadcast: bool`, `allowed_team_ids: tuple[int, ...]`, method `allows_team(team_id: int | None) -> bool`) and `agent_chat_caps(agent: dict | None) -> ChatCaps`. Consumed by PHASE-2 enforcement gates and by router/UI logic.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_agent_chat_caps.py
"""Unit tests for the fail-closed chat-capability parser (CHAT-PERM-07)."""
from __future__ import annotations

import pytest

from app.services.ai.permissions.agent_chat_caps import ChatCaps, agent_chat_caps


def test_none_agent_is_fully_denied():
    caps = agent_chat_caps(None)
    assert caps == ChatCaps()
    assert caps.enabled is False
    assert caps.allowed_team_ids == ()


def test_missing_profile_is_denied():
    assert agent_chat_caps({"id": "x"}).enabled is False


def test_profile_not_dict_is_denied():
    assert agent_chat_caps({"capability_profile": "oops"}).enabled is False


def test_chat_absent_is_denied():
    prof = {"capability_profile": {"tool_blacklist": ["Delegate"]}}
    assert agent_chat_caps(prof).enabled is False


def test_full_chat_object_parses():
    agent = {
        "capability_profile": {
            "chat": {
                "enabled": True,
                "read_team_resources": True,
                "auto_broadcast": True,
                "allowed_team_ids": [101, 202],
            }
        }
    }
    caps = agent_chat_caps(agent)
    assert caps.enabled is True
    assert caps.read_team_resources is True
    assert caps.auto_broadcast is True
    assert caps.allowed_team_ids == (101, 202)


def test_partial_chat_defaults_missing_to_false():
    agent = {"capability_profile": {"chat": {"enabled": True}}}
    caps = agent_chat_caps(agent)
    assert caps.enabled is True
    assert caps.read_team_resources is False
    assert caps.auto_broadcast is False
    assert caps.allowed_team_ids == ()


@pytest.mark.parametrize("bad", ["true", 1, "1", 0, [], {}, "yes"])
def test_non_bool_true_is_not_enabled_fail_closed(bad):
    # Only a literal JSON boolean true counts (incl. int 1 must NOT enable).
    agent = {"capability_profile": {"chat": {"enabled": bad}}}
    assert agent_chat_caps(agent).enabled is False


def test_allowed_team_ids_coerces_and_skips_garbage():
    agent = {
        "capability_profile": {
            "chat": {"enabled": True, "allowed_team_ids": ["303", 404, None, "x"]}
        }
    }
    assert agent_chat_caps(agent).allowed_team_ids == (303, 404)


def test_allowed_team_ids_not_list_is_empty():
    agent = {"capability_profile": {"chat": {"enabled": True, "allowed_team_ids": 5}}}
    assert agent_chat_caps(agent).allowed_team_ids == ()


def test_allows_team_logic():
    open_caps = ChatCaps(enabled=True, allowed_team_ids=())
    assert open_caps.allows_team(999) is True  # empty whitelist = any team
    assert open_caps.allows_team(None) is True

    scoped = ChatCaps(enabled=True, allowed_team_ids=(101,))
    assert scoped.allows_team(101) is True
    assert scoped.allows_team(202) is False
    assert scoped.allows_team(None) is False

    disabled = ChatCaps(enabled=False, allowed_team_ids=())
    assert disabled.allows_team(101) is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/test_agent_chat_caps.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.ai.permissions'`.

- [ ] **Step 3: Create the package marker**

```python
# backend/app/services/ai/permissions/__init__.py
```
(empty file)

- [ ] **Step 4: Implement the parser**

```python
# backend/app/services/ai/permissions/agent_chat_caps.py
"""Fail-closed parser for an agent's Team Chat capabilities (CHAT-PERM-07).

Chat permissions are stored inside ``ai_agents.capability_profile`` JSONB under
the ``chat`` key. Absent or wrong-typed config means the agent CANNOT chat: only
a literal JSON ``true`` grants a boolean capability, and ``allowed_team_ids``
defaults to an empty whitelist meaning "any team it is explicitly added to".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChatCaps:
    enabled: bool = False
    read_team_resources: bool = False
    auto_broadcast: bool = False
    allowed_team_ids: tuple[int, ...] = ()

    def allows_team(self, team_id: int | None) -> bool:
        """True if this agent may operate in a group belonging to ``team_id``.

        Requires ``enabled``. An empty ``allowed_team_ids`` means any team;
        a non-empty whitelist requires membership.
        """
        if not self.enabled:
            return False
        if not self.allowed_team_ids:
            return True
        return team_id is not None and int(team_id) in self.allowed_team_ids


def _as_bool(value: Any) -> bool:
    # Fail-closed: only a literal JSON boolean true grants the capability.
    return value is True


def _as_team_ids(value: Any) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    out: list[int] = []
    for item in value:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return tuple(out)


def agent_chat_caps(agent: dict[str, Any] | None) -> ChatCaps:
    """Parse chat capabilities from an agent row dict. Never raises."""
    if not agent:
        return ChatCaps()
    profile = agent.get("capability_profile")
    if not isinstance(profile, dict):
        return ChatCaps()
    chat = profile.get("chat")
    if not isinstance(chat, dict):
        return ChatCaps()
    return ChatCaps(
        enabled=_as_bool(chat.get("enabled")),
        read_team_resources=_as_bool(chat.get("read_team_resources")),
        auto_broadcast=_as_bool(chat.get("auto_broadcast")),
        allowed_team_ids=_as_team_ids(chat.get("allowed_team_ids")),
    )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/test_agent_chat_caps.py -v`
Expected: PASS (11 passed).

- [ ] **Step 6: Lint + commit**

```bash
cd backend && uv run black app/services/ai/permissions tests/test_agent_chat_caps.py && uv run isort app/services/ai/permissions tests/test_agent_chat_caps.py && uv run flake8 app/services/ai/permissions tests/test_agent_chat_caps.py
git add backend/app/services/ai/permissions backend/tests/test_agent_chat_caps.py
git commit -m "feat(chat): fail-closed agent_chat_caps parser (CHAT-PERM-07)"
```

---

## Task 3: Backend schema — `ChatPermissionsIn` (write) + `ChatPermissionsOut` (read) + `AgentOut.chat_permissions`

> **Review fix C1:** `AgentOut`/`AgentBase` do NOT contain `capability_profile`, and every agent endpoint (list/get/patch) uses `response_model=AgentOut`, so Pydantic strips it — the frontend would never receive chat perms. We expose a **narrow derived** `chat_permissions` field on `AgentOut` (only the chat sub-object; we deliberately do NOT surface the whole `capability_profile`, to avoid leaking `tool_blacklist` etc. on the public list endpoint).

**Files:**
- Modify: `backend/app/schemas/ai_library.py` (add `ChatPermissionsIn`, `ChatPermissionsOut`; add `chat_permissions` to both `AgentUpdate` and `AgentOut`)
- Test: `backend/tests/test_ai_library_agent_permissions.py` (schema portion)

**Interfaces:**
- Consumes: nothing.
- Produces: `ChatPermissionsIn` (all optional, for partial PATCH); `ChatPermissionsOut` (read shape: `enabled/read_team_resources/auto_broadcast: bool`, `allowed_team_ids: list[int]`); `AgentUpdate.chat_permissions: Optional[ChatPermissionsIn]`; `AgentOut.chat_permissions: ChatPermissionsOut`. Consumed by Task 4 (router enrich) + Task 5 (frontend reads `agent.chat_permissions`).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_ai_library_agent_permissions.py
"""Tests for agent chat-permission schema + PATCH carve-out (CHAT-PERM-15/16)."""
from __future__ import annotations

import pytest

from app.schemas.ai_library import AgentUpdate, ChatPermissionsIn


def test_chat_permissions_all_optional():
    p = ChatPermissionsIn()
    assert p.model_dump(exclude_none=True) == {}


def test_chat_permissions_partial():
    p = ChatPermissionsIn(enabled=True)
    assert p.model_dump(exclude_none=True) == {"enabled": True}


def test_agent_update_accepts_chat_permissions():
    u = AgentUpdate(chat_permissions=ChatPermissionsIn(enabled=True, allowed_team_ids=[1, 2]))
    assert u.chat_permissions is not None
    assert u.chat_permissions.enabled is True
    assert u.chat_permissions.allowed_team_ids == [1, 2]


def test_chat_permissions_out_from_caps():
    from app.schemas.ai_library import ChatPermissionsOut
    from app.services.ai.permissions.agent_chat_caps import agent_chat_caps

    caps = agent_chat_caps({"capability_profile": {"chat": {"enabled": True}}})
    out = ChatPermissionsOut.from_caps(caps)
    assert out.enabled is True
    assert out.read_team_resources is False
    assert out.allowed_team_ids == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_ai_library_agent_permissions.py -v`
Expected: FAIL with `ImportError: cannot import name 'ChatPermissionsIn'`.

- [ ] **Step 3: Add the schema**

In `backend/app/schemas/ai_library.py`, immediately before `class AgentUpdate(BaseModel):` (line 58) add:

```python
class ChatPermissionsIn(BaseModel):
    """Partial chat-permission patch merged into capability_profile.chat.

    All fields optional so the client can toggle one at a time. ``None`` means
    "leave unchanged"; absent in storage means denied (see agent_chat_caps).
    """

    enabled: Optional[bool] = None
    read_team_resources: Optional[bool] = None
    auto_broadcast: Optional[bool] = None
    allowed_team_ids: Optional[list[int]] = None


class ChatPermissionsOut(BaseModel):
    """Read shape exposed on AgentOut — the resolved (fail-closed) chat caps.

    Built from agent_chat_caps so the wire value matches enforcement exactly.
    We expose ONLY chat perms, never the raw capability_profile (which holds
    internal gating like tool_blacklist).
    """

    enabled: bool = False
    read_team_resources: bool = False
    auto_broadcast: bool = False
    allowed_team_ids: list[int] = Field(default_factory=list)

    @classmethod
    def from_caps(cls, caps: "ChatCaps") -> "ChatPermissionsOut":
        return cls(
            enabled=caps.enabled,
            read_team_resources=caps.read_team_resources,
            auto_broadcast=caps.auto_broadcast,
            allowed_team_ids=list(caps.allowed_team_ids),
        )
```

Add the import at the top of `ai_library.py` (guarded for typing only is fine, but a plain import avoids a forward-ref string):
```python
from app.services.ai.permissions.agent_chat_caps import ChatCaps
```

Then inside `class AgentUpdate(BaseModel):`, after the `max_concurrent_runs` line (line 81) add:

```python
    # Team Chat PHASE-0: merged into capability_profile.chat by the router
    # (deep-merge, never clobbers the Phase 4.5 keys). Allowed even on
    # system-preset agents (permissions are governance, not content).
    chat_permissions: Optional[ChatPermissionsIn] = None
```

And inside `class AgentOut(AgentBase):`, after the `max_concurrent_runs` line add (review C1 — read path):

```python
    # Team Chat PHASE-0: resolved (fail-closed) chat capabilities. Populated by
    # the router from agent_chat_caps(row); defaults to all-false so clients
    # never have to guess. NOT the raw capability_profile (no internal-gating leak).
    chat_permissions: ChatPermissionsOut = Field(default_factory=ChatPermissionsOut)
```

> **Import-cycle check:** `ai_library.py` importing from `services.ai.permissions.agent_chat_caps` must not create a cycle. `agent_chat_caps.py` (Task 2) imports only stdlib — safe. If a cycle ever appears, drop the import and keep `from_caps` typed with the string `"ChatCaps"` + duck-typed access.

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && uv run pytest tests/test_ai_library_agent_permissions.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Lint + commit**

```bash
cd backend && uv run black app/schemas/ai_library.py tests/test_ai_library_agent_permissions.py && uv run isort app/schemas/ai_library.py tests/test_ai_library_agent_permissions.py && uv run flake8 app/schemas/ai_library.py tests/test_ai_library_agent_permissions.py
git add backend/app/schemas/ai_library.py backend/tests/test_ai_library_agent_permissions.py
git commit -m "feat(chat): AgentUpdate.chat_permissions schema (CHAT-PERM-16)"
```

---

## Task 4: Router — role gate + preset carve-out + deep-merge + response enrich + audit

**Files:**
- Modify: `backend/app/api/ai_library_router.py:461-515`
- Test: `backend/tests/test_ai_library_agent_permissions.py` (router portion, append)

**Interfaces:**
- Consumes: `AgentUpdate.chat_permissions` (Task 3), `ChatPermissionsOut.from_caps` + `agent_chat_caps` (Tasks 2–3), `agent_repo.update_fields_versioned` (existing).
- Produces: PATCH `/agents/{slug}` now (a) **role-gates** chat-permission edits (review H1), (b) allows `chat_permissions` on system presets while still rejecting content edits, (c) deep-merges chat perms into `capability_profile.chat`, (d) **populates `AgentOut.chat_permissions` in every response** (review C1), and (e) **audit-logs the grant/change** (review M3). Also: the GET-by-slug and list handlers must populate `chat_permissions` (same enrich helper).

- [ ] **Step 1 (review M1): Determine JSONB encoding with a real integration probe, not just grep**

First locate the write path:
```bash
cd backend && grep -n "def update_fields_versioned\|json.dumps\|::jsonb\|capability_profile" app/repositories/*agent*.py
```
Then **confirm by writing** — add a throwaway integration test (or use an existing repo fixture) that calls `update_fields_versioned(uuid, {"capability_profile": {"chat": {"enabled": True}}})` against the dev DB and reads it back; assert the round-trip returns a dict with `chat.enabled is True`. This proves whether asyncpg needs `json.dumps`. **Record the verdict here before coding Step 4.** If a raw dict raises `DataError: invalid input for type json`, Step 4 must `json.dumps(...)` the `capability_profile` value (variant noted in Step 4) and add `import json`.

- [ ] **Step 2: Write the failing test (append to test_ai_library_agent_permissions.py)**

```python
from typing import Any, Dict
from unittest.mock import AsyncMock, patch as _patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.ai_library_router import router


def _client_with_repo(existing: Dict[str, Any], captured: dict):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    from app.core.deps import get_auth

    class _Auth:
        user_id = "11111111-1111-1111-1111-111111111111"
        email = "u@example.com"

    async def _grant():
        return _Auth()

    app.dependency_overrides[get_auth] = _grant

    repo = AsyncMock()
    repo.get_by_slug.return_value = existing

    async def _update_fields(agent_uuid, updates, created_by=None):
        captured["updates"] = updates

    repo.update_fields_versioned.side_effect = _update_fields
    repo.get_skill_ids.return_value = []
    repo.update_skill_bindings.return_value = None
    return app, repo


def _preset_agent() -> Dict[str, Any]:
    return {
        "id": "22222222-2222-2222-2222-222222222222",
        "slug": "script_ai",
        "name": "Script AI",
        "is_system_preset": True,
        "capability_profile": {"allowed_skills": ["script-outline"]},
    }


def test_patch_chat_permissions_on_preset_merges_and_returns_perms():
    captured: dict = {}
    app, repo = _client_with_repo(_preset_agent(), captured)
    with _patch("app.api.ai_library_router._repos", return_value=(repo, None)), _patch(
        "app.api.ai_library_router._enrich_agents_with_scope_names",
        new=AsyncMock(side_effect=lambda rows: rows),
    ), _patch(
        # role gate: allow (platform admin). Tested separately below.
        "app.api.ai_library_router._can_edit_chat_permissions",
        new=AsyncMock(return_value=True),
    ):
        # refresh get_by_slug returns the merged row so the response reflects it
        merged = {
            **_preset_agent(),
            "capability_profile": {
                "allowed_skills": ["script-outline"],
                "chat": {"enabled": True, "read_team_resources": True},
            },
        }
        repo.get_by_slug.side_effect = [_preset_agent(), merged]
        client = TestClient(app)
        resp = client.patch(
            "/api/v1/ai-library/agents/script_ai",
            json={"chat_permissions": {"enabled": True, "read_team_resources": True}},
        )
    assert resp.status_code == 200, resp.text
    # storage merged, did NOT clobber the existing Phase 4.5 key
    prof = captured["updates"]["capability_profile"]
    assert prof["allowed_skills"] == ["script-outline"]
    assert prof["chat"]["enabled"] is True
    # review C1: the response actually carries the resolved perms
    body = resp.json()
    assert body["chat_permissions"]["enabled"] is True
    assert body["chat_permissions"]["read_team_resources"] is True


def test_patch_chat_permissions_denied_without_role():
    captured: dict = {}
    app, repo = _client_with_repo(_preset_agent(), captured)
    with _patch("app.api.ai_library_router._repos", return_value=(repo, None)), _patch(
        "app.api.ai_library_router._can_edit_chat_permissions",
        new=AsyncMock(return_value=False),
    ):
        client = TestClient(app)
        resp = client.patch(
            "/api/v1/ai-library/agents/script_ai",
            json={"chat_permissions": {"enabled": True}},
        )
    assert resp.status_code == 403  # review H1: role gate


def test_patch_content_field_on_preset_still_403():
    captured: dict = {}
    app, repo = _client_with_repo(_preset_agent(), captured)
    with _patch("app.api.ai_library_router._repos", return_value=(repo, None)):
        client = TestClient(app)
        resp = client.patch(
            "/api/v1/ai-library/agents/script_ai", json={"name": "Hacked"}
        )
    assert resp.status_code == 403


def test_patch_mixed_content_and_perms_on_preset_403():
    captured: dict = {}
    app, repo = _client_with_repo(_preset_agent(), captured)
    with _patch("app.api.ai_library_router._repos", return_value=(repo, None)):
        client = TestClient(app)
        resp = client.patch(
            "/api/v1/ai-library/agents/script_ai",
            json={"name": "X", "chat_permissions": {"enabled": True}},
        )
    assert resp.status_code == 403
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_ai_library_agent_permissions.py -v`
Expected: FAIL — preset still returns 403 even for chat_permissions-only (current code blocks all preset PATCH).

- [ ] **Step 4: Rewrite the PATCH body (lines 475-503)**

Replace the block from `agent = await agent_repo.get_by_slug(slug)` through the `update_fields_versioned` call with:

```python
    agent_repo, _ = _repos()
    agent = await agent_repo.get_by_slug(slug)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="agent not found"
        )

    agent_uuid = UUID(str(agent["id"]))
    # Content fields (everything except skill bindings and chat permissions).
    updates = payload.model_dump(
        exclude_none=True, exclude={"skill_ids", "chat_permissions"}
    )

    # System-preset carve-out (CHAT-PERM-15): presets stay read-only for content
    # and skill edits, but chat permissions ARE editable (governance, not
    # content). So reject only when a content/skill change is attempted.
    is_preset = bool(agent.get("is_system_preset"))
    content_change = bool(updates) or payload.skill_ids is not None
    if is_preset and content_change:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="system preset agents are read-only except chat permissions",
        )

    # Role gate for chat-permission edits (CHAT-PERM-19 / review H1). The legacy
    # endpoint had NO role check — any logged-in user could PATCH any agent. We
    # only gate the new chat_permissions write here (content edits keep their
    # existing preset-only policy). Grant if: caller owns the agent, OR is owner
    # of the agent's scope team, OR (for presets / platform-scope) is a platform
    # admin.
    if payload.chat_permissions is not None:
        user_uuid = _coerce_user_uuid(auth.user_id)
        if not await _can_edit_chat_permissions(agent_repo, agent, user_uuid):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="not allowed to change this agent's chat permissions",
            )

    # Budget "unlimited" convention: 0 from the client means "clear the cap".
    for budget_field in ("monthly_token_budget", "monthly_cost_cents_budget"):
        if updates.get(budget_field) == 0:
            updates[budget_field] = None

    # Deep-merge chat permissions into capability_profile.chat — never clobber
    # the existing Phase 4.5 keys (CHAT-PERM-01, lesson: user_settings clobber).
    chat_audit: dict | None = None
    if payload.chat_permissions is not None:
        existing_profile = agent.get("capability_profile")
        if not isinstance(existing_profile, dict):
            existing_profile = {}
        before_chat = dict(existing_profile.get("chat") or {})
        existing_chat = dict(before_chat)
        existing_chat.update(payload.chat_permissions.model_dump(exclude_none=True))
        updates["capability_profile"] = {**existing_profile, "chat": existing_chat}
        chat_audit = {"before": before_chat, "after": existing_chat}

    if updates:
        user_uuid = _coerce_user_uuid(auth.user_id)
        await agent_repo.update_fields_versioned(
            agent_uuid, updates, created_by=user_uuid
        )
    if payload.skill_ids is not None:
        await agent_repo.update_skill_bindings(agent_uuid, payload.skill_ids)

    # Audit the grant/change, not just denials (CHAT-PERM-21 / review M3).
    if chat_audit is not None:
        logger.info(
            "chat_permissions changed by %s on agent %s: %s",
            auth.user_id,
            agent["slug"],
            chat_audit,
        )
```

Add the role-gate helper near the other module helpers in `ai_library_router.py`:

```python
async def _can_edit_chat_permissions(agent_repo, agent: Dict[str, Any], user_uuid) -> bool:
    """CHAT-PERM-19: who may edit an agent's chat permissions.

    - Agent owner (user-scoped agent) → allowed.
    - Owner of the agent's scope team → allowed.
    - Platform admin → allowed (covers system presets / platform-scope agents).
    """
    owner_id = agent.get("user_id")
    if owner_id is not None and str(owner_id) == str(user_uuid):
        return True
    team_id = agent.get("team_id")
    if team_id is not None and await _is_team_owner(user_uuid, team_id):
        return True
    return await _is_platform_admin(user_uuid)
```

> **Wire the two helpers to the project's REAL checks (confirmed to exist):**
> - `_is_platform_admin(user_uuid)` → reuse the existing admin concept in
>   `backend/app/core/admin_deps.py` (it checks the user's `role == 'admin'`).
>   Extract/call the same role lookup; do NOT reimplement. This is how a
>   platform admin enables chat on **system-preset** agents (e.g. `script_ai`).
> - `_is_team_owner(user_uuid, team_id)` → use `team_members.role`
>   (`backend/app/core/team_permissions.py` defines TEAM_ROLES owner/admin/…).
>   Treat role `owner` (and optionally `admin`) of that team as allowed. Reuse a
>   team repo/permission helper if present; else a small `team_members` query.
> - The caller's role/claims are on `AuthContext` (`backend/app/core/deps.py:84`,
>   `role` from JWT). Fail closed only on genuine "no signal", never default-allow.

> **JSONB encoding (from Step 1):** if the repo needs a string, wrap the merged
> profile with `json.dumps(...)` in the `updates["capability_profile"] = ...`
> line and add `import json`.

**Response enrichment (review C1) — add the derived `chat_permissions` to every agent response.** Add this helper to `ai_library_router.py` (imports: `from app.services.ai.permissions.agent_chat_caps import agent_chat_caps` and `from app.schemas.ai_library import ChatPermissionsOut`):

```python
def _with_chat_permissions(row: Dict[str, Any]) -> Dict[str, Any]:
    """Inject the resolved (fail-closed) chat perms so AgentOut.chat_permissions
    reflects storage. AgentOut has no capability_profile field, so without this
    the response would always serialize the all-false default."""
    caps = agent_chat_caps(row)
    return {**row, "chat_permissions": ChatPermissionsOut.from_caps(caps).model_dump()}
```

Apply it to the row(s) returned by:
- `PATCH /agents/{slug}` (this handler): wrap `row` before enrich → `row = _with_chat_permissions({**refreshed, "skill_ids": ...})`.
- `GET /agents/{slug}` (handler at line ~353): wrap the returned row.
- `GET /agents` (list, line ~287): map `_with_chat_permissions` over the rows.

- [ ] **Step 5: Run to verify it passes**

Run: `cd backend && uv run pytest tests/test_ai_library_agent_permissions.py -v`
Expected: PASS (all tests in this file).

- [ ] **Step 6: Regression — existing agent-update tests still pass**

Run: `cd backend && uv run pytest tests/test_ai_library_create_agent.py tests/ -k "ai_library" -v`
Expected: PASS (no regressions in AI Library suite).

- [ ] **Step 7: Lint + commit**

```bash
cd backend && uv run black app/api/ai_library_router.py tests/test_ai_library_agent_permissions.py && uv run isort app/api/ai_library_router.py tests/test_ai_library_agent_permissions.py && uv run flake8 app/api/ai_library_router.py tests/test_ai_library_agent_permissions.py
git add backend/app/api/ai_library_router.py backend/tests/test_ai_library_agent_permissions.py
git commit -m "feat(chat): PATCH chat_permissions merge + preset carve-out (CHAT-PERM-08-prep/15)"
```

---

## Task 5: Frontend types + service helper

**Files:**
- Modify: `frontend/types.ts:1018-1050`
- Modify: `frontend/services/aiLibraryService.ts:82-95`

**Interfaces:**
- Produces: `AgentChatPermissions` type; `AILibraryAgent.capability_profile?: { chat?: AgentChatPermissions; [k: string]: unknown }`; `aiLibraryService.updateAgentChatPermissions(slug, perms)`.

- [ ] **Step 1: Add the type** (in `frontend/types.ts`, just above `export interface AILibraryAgent {`)

```ts
export interface AgentChatPermissions {
  enabled?: boolean;
  read_team_resources?: boolean;
  auto_broadcast?: boolean;
  allowed_team_ids?: number[];
}
```

- [ ] **Step 2: Extend `AILibraryAgent`** (add inside the interface body)

```ts
  // Team Chat PHASE-0: resolved chat caps, served by AgentOut.chat_permissions
  // (review C1 — capability_profile itself is NOT exposed on the wire).
  // Always present from the API (defaults all-false); optional here for forward-compat.
  chat_permissions?: AgentChatPermissions;
```

- [ ] **Step 3: Add the service helper** (in `frontend/services/aiLibraryService.ts`, near `updateAgent`)

```ts
  async updateAgentChatPermissions(
    slug: string,
    perms: AgentChatPermissions,
  ): Promise<AILibraryAgent> {
    return this.updateAgent(slug, { chat_permissions: perms } as Partial<AILibraryAgent>);
  },
```
Add `AgentChatPermissions` to the existing `import type { ... } from '../types'` line.

- [ ] **Step 4: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no new errors referencing `AgentChatPermissions` / `capability_profile`.

- [ ] **Step 5: Commit**

```bash
git add frontend/types.ts frontend/services/aiLibraryService.ts
git commit -m "feat(chat): frontend agent chat-permission types + service (CHAT-PERM-18)"
```

---

## Task 6: Frontend `PermissionsSection` component

**Files:**
- Create: `frontend/components/AILibrary/PermissionsSection.tsx`

**Interfaces:**
- Consumes: `AILibraryAgent`, `AgentChatPermissions`, an `onChange(perms: AgentChatPermissions)` callback, a `disabled` flag.
- Produces: `<PermissionsSection agent draft onChange disabled />` — 4 controls (3 toggles + team whitelist). Pure presentational; the parent owns draft + save (Task 7).

- [ ] **Step 1: Implement the component**

```tsx
// frontend/components/AILibrary/PermissionsSection.tsx
import { useTranslation } from 'react-i18next';
import type { AgentChatPermissions } from '../../types';

interface Props {
  value: AgentChatPermissions;
  onChange: (next: AgentChatPermissions) => void;
}

function Toggle({
  label,
  desc,
  checked,
  onToggle,
}: {
  label: string;
  desc: string;
  checked: boolean;
  onToggle: (v: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-4 py-3 border-b border-line">
      <div className="min-w-0">
        <div className="text-sm font-medium text-content">{label}</div>
        <div className="text-xs text-content-3 mt-0.5">{desc}</div>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onToggle(!checked)}
        className={`relative h-6 w-10 flex-none rounded-full transition-colors ${
          checked ? 'bg-indigo-500' : 'bg-ink-700'
        }`}
      >
        <span
          className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-transform ${
            checked ? 'translate-x-[18px]' : 'translate-x-0.5'
          }`}
        />
      </button>
    </div>
  );
}

export default function PermissionsSection({ value, onChange }: Props) {
  const { t } = useTranslation();
  const enabled = value.enabled ?? false;
  const set = (patch: Partial<AgentChatPermissions>) => onChange({ ...value, ...patch });

  return (
    <div className="max-w-xl">
      <p className="text-xs text-content-3 mb-4">
        {t('aiLibrary.permissions.intro')}
      </p>
      <Toggle
        label={t('aiLibrary.permissions.enableChat')}
        desc={t('aiLibrary.permissions.enableChatDesc')}
        checked={enabled}
        onToggle={(v) => set({ enabled: v })}
      />
      <div className={enabled ? '' : 'opacity-40 pointer-events-none'}>
        <Toggle
          label={t('aiLibrary.permissions.readTeamFiles')}
          desc={t('aiLibrary.permissions.readTeamFilesDesc')}
          checked={value.read_team_resources ?? false}
          onToggle={(v) => set({ read_team_resources: v })}
        />
        <Toggle
          label={t('aiLibrary.permissions.autoBroadcast')}
          desc={t('aiLibrary.permissions.autoBroadcastDesc')}
          checked={value.auto_broadcast ?? false}
          onToggle={(v) => set({ auto_broadcast: v })}
        />
      </div>
    </div>
  );
}
```

> Team whitelist (`allowed_team_ids`) needs a team multi-select. Teams aren't
> loaded in this editor yet; ship the 3 toggles first and add the whitelist in a
> follow-up once the team list is wired (CHAT-PERM-05 stays ☐ until then). Empty
> whitelist already = "any team", the safe default.

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors (i18n keys resolve at runtime; types clean).

- [ ] **Step 3: Commit**

```bash
git add frontend/components/AILibrary/PermissionsSection.tsx
git commit -m "feat(chat): AI Library Permissions section component (CHAT-PERM-14)"
```

---

## Task 7: Wire `PermissionsSection` into `AgentEditor` sub-tabs

**Files:**
- Modify: `frontend/components/AILibrary/AgentEditor.tsx:41,364,465`

**Interfaces:**
- Consumes: `PermissionsSection` (Task 6), `aiLibraryService.updateAgentChatPermissions` (Task 5).
- Produces: a new "Permissions" sub-tab that reads `agent.chat_permissions`, edits a local draft, and saves via PATCH.

> **Review H2:** the real `subTabs` is a **bare string array** `const subTabs: SubTab[] = ['dashboard','overview','files','skills','runs','routines','versions']` — NOT `{key,label}` objects. Labels are rendered elsewhere (a label map / switch / button render below line 364). Add the string and the label there.
>
> **Review H3:** the editor has a `readOnly`/`isPreset` mode that hides the main Save button (`{!readOnly && ...}`). The Permissions tab is the **one place editable on system presets**, so its inputs and Save button must **NOT** be gated by `readOnly`. Render the Permissions block and its Save unconditionally (server still enforces the role gate from Task 4).

- [ ] **Step 1: Add `'permissions'` to the SubTab union** (line 42)

```ts
type SubTab = 'dashboard' | 'overview' | 'files' | 'skills' | 'permissions' | 'runs' | 'routines' | 'versions';
```

- [ ] **Step 2: Add `'permissions'` to the `subTabs` array** (line 364, bare string) after `'skills'`:

```ts
  const subTabs: SubTab[] = [
    'dashboard',
    'overview',
    'files',
    'skills',
    'permissions',
    'runs',
    'routines',
    'versions',
  ];
```
Then find where tab buttons render their label from each `SubTab` string (grep the file for `subTabs.map` and how it derives the display text — a `tabLabel()` switch or an inline map). Add the `'permissions'` case → `t('aiLibrary.permissions.tab')`, matching exactly how the other tabs get their labels.

- [ ] **Step 3: Add local permissions draft state** (near the other draft state, ~line 119)

```ts
  const [permDraft, setPermDraft] = useState<AgentChatPermissions>(
    () => agent.chat_permissions ?? {},
  );
  useEffect(() => {
    setPermDraft(agent.chat_permissions ?? {});
  }, [agent.id]);
```
Add `AgentChatPermissions` to the `../../types` import and import `PermissionsSection`.

- [ ] **Step 4: Render the tab + a Save button** — add a conditional block alongside the other `sub === ...` renders (~line 674). **Do NOT wrap in `!readOnly`** (H3 — presets edit perms here):

```tsx
{sub === 'permissions' && (
  <div>
    <PermissionsSection value={permDraft} onChange={setPermDraft} />
    <div className="mt-4 flex justify-end">
      <button
        type="button"
        className="rounded-lg btn-tint-indigo px-4 py-2 text-sm font-medium"
        onClick={async () => {
          const updated = await aiLibraryService.updateAgentChatPermissions(
            agent.slug,
            permDraft,
          );
          setAgent(updated);
          setPermDraft(updated.chat_permissions ?? {});
        }}
      >
        {t('common.save', 'Save')}
      </button>
    </div>
  </div>
)}
```
(`btn-tint-indigo` is the confirmed Save-button class in this file. Reuse the same `setAgent` refresh pattern as the Overview save at line 244. Wrap the `onClick` body in try/catch + a toast on failure to surface the 403 from the role gate — match how Overview save reports errors.)

- [ ] **Step 5: Typecheck + build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: build succeeds.

- [ ] **Step 6: Visual verify in the running app**

Run the app, open Settings → AI Library → Agents → pick `script_ai` → Permissions tab. Toggle "Enable chat", Save, reload — the toggle persists (PATCH succeeded against a system preset). Capture a screenshot for the PR.

- [ ] **Step 7: Commit**

```bash
git add frontend/components/AILibrary/AgentEditor.tsx
git commit -m "feat(chat): Permissions sub-tab in AI Library agent editor (CHAT-PERM-13)"
```

---

## Task 8: i18n keys (en + zh)

**Files:**
- Modify: `frontend/public/locales/en.json`
- Modify: `frontend/public/locales/zh.json`

**Interfaces:**
- Produces: `aiLibrary.permissions.*` keys used by Tasks 6–7.

- [ ] **Step 1: Add to `en.json`** (under the existing `aiLibrary` object)

```json
"permissions": {
  "tab": "Permissions",
  "intro": "Control whether this agent can participate in Team Chat. All permissions are off by default — an agent cannot enter groups or read team files until granted here.",
  "enableChat": "Enable chat",
  "enableChatDesc": "Allow this agent to be added to groups and summoned with @mention.",
  "readTeamFiles": "Read team files",
  "readTeamFilesDesc": "Allow the agent to read resources of the group's team when summoned.",
  "autoBroadcast": "Auto-broadcast",
  "autoBroadcastDesc": "Allow the agent to post automatic updates (e.g. task completion) to its groups."
}
```

- [ ] **Step 2: Add to `zh.json`** (same path)

```json
"permissions": {
  "tab": "权限",
  "intro": "控制此 Agent 是否能参与团队聊天。所有权限默认关闭——未在此授权前，Agent 无法进群或读取团队文件。",
  "enableChat": "启用聊天",
  "enableChatDesc": "允许此 Agent 被加入群组并通过 @ 召唤。",
  "readTeamFiles": "读取团队文件",
  "readTeamFilesDesc": "允许 Agent 在被召唤时读取所在群组团队的素材。",
  "autoBroadcast": "主动播报",
  "autoBroadcastDesc": "允许 Agent 向所在群组发送自动更新（如任务完成）。"
}
```

- [ ] **Step 3: Verify JSON parses + keys resolve**

Run: `cd frontend && node -e "JSON.parse(require('fs').readFileSync('public/locales/en.json','utf8'));JSON.parse(require('fs').readFileSync('public/locales/zh.json','utf8'));console.log('ok')"`
Expected: `ok`. Then reload the app and confirm the Permissions tab shows English labels (no raw `aiLibrary.permissions.tab` keys).

- [ ] **Step 4: Commit**

```bash
git add frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(chat): i18n for agent chat permissions (en/zh)"
```

---

## Task 9: "Chat" badge on chat-enabled agents in the sidebar

**Files:**
- Modify: `frontend/components/AILibrary/AILibrarySidebar.tsx:178-235`

**Interfaces:**
- Consumes: `AILibraryAgent.chat_permissions?.enabled`.
- Produces: a small "Chat" pill next to agents that are chat-enabled, so admins see at a glance which agents can join groups.

- [ ] **Step 1: Add the badge** — in the agent list item render, after the agent name:

```tsx
{agent.chat_permissions?.enabled && (
  <span className="ml-1.5 rounded px-1.5 py-0.5 text-[10px] font-medium text-indigo-300 bg-indigo-500/15">
    {t('aiLibrary.permissions.tab')}
  </span>
)}
```

- [ ] **Step 2: Typecheck + build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Visual verify**

Reload the app; the agent you enabled in Task 7 shows a "Chat" pill in the AI Library sidebar; others do not.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/AILibrary/AILibrarySidebar.tsx
git commit -m "feat(chat): Chat-enabled badge in AI Library sidebar (CHAT-PERM-17)"
```

---

## Self-Review

**Spec coverage (CHAT-PERM IDs delivered here):**
- PERM-01/02/03/04/05 → Tasks 1–4 (capability_profile.chat shape; defaults false; team whitelist schema). PERM-05 whitelist UI deferred (noted in Task 6 + spec note) — keep ☐ until team multi-select lands.
- PERM-06 → Task 1 (index + comment, incl. jsonb-bool textualization note — review L3).
- PERM-07 → Task 2 (`agent_chat_caps`, fail-closed incl. int-1 case — review L1).
- PERM-13/14 → Tasks 6–7 (Permissions sub-tab + controls; H2 bare-string subTabs + H3 readOnly exemption applied).
- PERM-15 → Task 4 (preset carve-out).
- PERM-16 → Task 3 (write schema `ChatPermissionsIn`).
- PERM-17 → Task 9 (badge). PERM-18 → Task 5 (types/service).
- **PERM-19 (role gate, review H1) → Task 4** (`_can_edit_chat_permissions` + denied test; `_is_platform_admin` fail-closed until wired).
- **PERM-20 (response exposure, review C1) → Tasks 3+4** (`ChatPermissionsOut` on AgentOut + `_with_chat_permissions` enrich on list/get/patch).
- **PERM-21 (grant/change audit, review M3) → Task 4** (logger.info on chat_permissions change).
- PERM-08~12 (enforcement gates) → **out of scope here**, implemented in CHAT-PHASE-2 consuming `agent_chat_caps`. Keep ☐.

**Deferred / explicit gaps (no silent caps):**
- Team whitelist UI (`allowed_team_ids`) — schema + parser + Out field ready; the multi-select control ships when the editor loads the team list. Until then the field is settable only via API; UI shows the 3 toggles.
- `_is_platform_admin` — if the repo has no platform-admin concept, it FAILS CLOSED (preset-permission edits blocked) until wired. Flagged in Task 4 + PR description; not a silent default-allow.
- Enforcement is inert until PHASE-2 — flags are stored/shown but nothing gates on them yet. Acceptable: "configure now, enforce when chat lands."

**Type consistency check:** wire field `chat_permissions` ↔ `ChatPermissionsIn` (write) / `ChatPermissionsOut` (read, on AgentOut) ↔ `AgentChatPermissions` (frontend) ↔ storage `capability_profile.chat`. Toggle keys `enabled / read_team_resources / auto_broadcast / allowed_team_ids` identical across migration, parser, both schemas, TS type, and UI. Frontend reads `agent.chat_permissions` everywhere (Tasks 5/6/7/9) — no leftover `capability_profile?.chat` reads. ✔

**Placeholder scan:** every code step contains complete code; deferred items (team-whitelist UI, `_is_platform_admin` wiring, JSONB-encoding variant) are called out as explicit sub-steps with fail-closed defaults, not silent TODOs. ✔

**Review fixes applied (this revision):** C1 (response exposure), H1 (role gate), H2 (bare-string subTabs), H3 (readOnly exemption), M1 (JSONB integration probe), M2 (spec PERM-05 note), M3 (grant audit), L1 (int-1 fail-closed test), L3 (migration comment). ✔

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-25-team-chat-agent-permissions.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration (REQUIRED SUB-SKILL: superpowers:subagent-driven-development).
2. **Inline Execution** — execute tasks in this session with checkpoints (REQUIRED SUB-SKILL: superpowers:executing-plans).
