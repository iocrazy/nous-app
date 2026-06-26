# Team Chat PHASE-2 — Agents In Chat + Permission Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Make AI agents real chat participants: a user `@mentions` an agent in a channel, the agent (gated by its chat permissions) replies in-thread via the existing AgentRunner, reading resources strictly as `RLS(summoner) ∩ channel-team-scope`. Turns the inert PHASE-0 permission flags into live enforcement.

**Architecture:** When a human message is posted, the chat service parses `@agent` mentions. For each mentioned agent that is (a) `chat.enabled`, (b) allowed in this team, and (c) joined to the channel (`agent_channels`), it dispatches a **background** agent turn. The turn reuses the AgentRunner stack (PromptComposer → build_agent_runner_stack → run_turn) with a resource-fetch handler bound to **the summoner's user_id AND the channel's team_id** (so the agent can only read what the summoner can see, narrowed to the channel's team). The agent's reply is written back as a `channel_message` (`sender_type='agent'`, `from_bot_agent_id=<id>`), which Realtime delivers. Anti-loop: agent-authored messages never trigger another summon.

**Tech Stack:** FastAPI (BackgroundTasks), the existing AgentRunner / PromptComposer / RunRecorder, `agent_chat_caps` (PHASE-0), chat_repository/chat_service (PHASE-1), Postgres/asyncpg, pytest.

## Global Constraints
- **Branch:** `feature/team-chat-phase2` (off origin/master, which already has PHASE-0/1 chat code + migrations 319-322).
- **Authorization iron law (CHAT-SEC-AGENT-01):** `agent-visible resources = resource_fetch(summoner user_id) ∩ channel team scope`. The agent NEVER reads with service_role; the summoner is the authorization subject; the channel team is the output boundary. No path may bypass this.
- **Default-deny:** an agent participates only if `agent_chat_caps(agent).enabled` is true AND (`allows_team(channel.team_id)`) AND it is in `agent_channels` for that channel. Any missing → no summon, no reply (optionally a one-line system notice).
- **Anti-loop (CHAT-AGENT-03):** never summon on a message whose `from_bot_agent_id IS NOT NULL` (agent-authored). Iron rule — the summon parser skips agent messages first.
- **Read-team-files gate (CHAT-PERM-10):** resource_fetch in chat context refuses if `caps.read_team_resources` is false, BEFORE any DB read.
- **Throttle (CHAT-AGENT-04):** cap concurrent/queued agent turns per channel (and ignore an agent re-summoning itself) to prevent loops/spam.
- **Audit (CHAT-SEC-AGENT-08):** every agent resource read + every summon decision logs to `application_logs` (actor=summoner, agent, channel, decision). loguru f-strings, never `%s`.
- **No new tables** except `agent_channels` already exists (PHASE-1, migration 320). PHASE-2 adds no tables.
- **Migration numbering:** none expected. If one is needed, next free number is **323** (master max is 322). Verify with `ls supabase/migrations | tail`.
- **Backend lint gate before commit:** black + isort + flake8 on changed `.py`.
- **Tests:** unit (mock repo/runner) run locally; integration (`@pytest.mark.integration`) run in CI.
- **Out of scope (later phases):** agent auto-broadcast on task completion (PHASE-4, CHAT-AGENT-05), agent DM integration, frontend create-group/agent-picker UI, durable DBOS-queued turns (this phase uses FastAPI BackgroundTasks; durability is a follow-up).

## File Structure
- Modify `backend/app/services/ai/tools/resource_fetch_tool.py` — add optional `team_id` scope param.
- Create `backend/app/services/chat/mention_parser.py` — extract `@agent` slugs from message body.
- Create `backend/app/services/chat/channel_agent_turn.py` — run one agent turn for a channel summon (reuses AgentRunner), with scoped resource_fetch + caps gates + audit.
- Modify `backend/app/repositories/chat_repository.py` — `add_agent_to_channel`, `is_agent_in_channel`, `recent_messages` (for turn context), `mention_fanout` (UNREAD-03).
- Modify `backend/app/services/chat_service.py` — `add_agent` (PERM-08 join gate), and `post_message` summon hook (parse mentions → dispatch turns).
- Modify `backend/app/api/chat_router.py` — `POST /chat/channels/{id}/agents` (add agent), inject `BackgroundTasks` into post_message.
- Tests: `backend/tests/test_mention_parser.py`, `test_channel_agent_turn.py`, `test_chat_agent_summon.py`, `backend/tests/integration/test_chat_agent_channel.py`.

---

## Task 1: resource_fetch — channel team-scope param (CHAT-SEC-AGENT-03)

**Files:** Modify `backend/app/services/ai/tools/resource_fetch_tool.py`; Test: `backend/tests/test_resource_fetch_team_scope.py`.

**Interfaces (Produces):** `resource_fetch(..., team_id: int | None = None)` and `_fetch_dispatch(..., team_id=None)` — when `team_id` is set, the resource query additionally requires `resource_items.scope_id = team_id` (the channel's team), in addition to the existing "summoner is a member of the resource's team" check. `team_id=None` preserves current behavior (back-compat for the existing ai_library chat path).

- [ ] **Step 1: Write the failing test** — assert that with `team_id` set, a resource whose `scope_id` ≠ team_id is rejected (PermissionError) even if the user could otherwise see it; and that `team_id=None` keeps current behavior. Mock `db_engine.fetch_all` to assert the SQL/params include the team filter when team_id is passed. Match the existing test style for this module if one exists.

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement.** In `_fetch_dispatch` (currently ~38-72), add `team_id: int | None = None`. Extend the WHERE clause: keep the existing `ri.scope_id::text IN (SELECT team_id::text FROM team_members WHERE user_id=:uid)` (summoner membership), and when `team_id is not None`, AND `ri.scope_id::text = :tid::text`. Add `:tid` to params only when set. Thread `team_id` through `resource_fetch(...)` to `_fetch_dispatch(...)`. Keep `team_id` optional so the existing ai_library handler (which passes no team_id) is unchanged.

- [ ] **Step 4: Run → pass.** Lint. Commit — `feat(chat): resource_fetch channel team-scope param (CHAT-SEC-AGENT-03)`.

---

## Task 2: mention_parser (extract @agent slugs)

**Files:** Create `backend/app/services/chat/__init__.py` (if missing) + `backend/app/services/chat/mention_parser.py`; Test: `backend/tests/test_mention_parser.py`.

**Interfaces (Produces):** `extract_agent_mentions(body: dict, known_slugs: set[str]) -> list[str]` — returns the distinct agent slugs mentioned in the message text (`body.get("text","")`), matching `@slug` tokens against `known_slugs` (case-insensitive), preserving first-seen order. Pure function, no DB.

- [ ] **Step 1: Failing test** — cases: `@script_ai hi` → `["script_ai"]`; `@Script_AI and @analyze` with known {script_ai, analyze} → `["script_ai","analyze"]`; unknown `@nobody` → `[]`; email `a@b.com` not a mention → `[]`; duplicate `@a @a` → `["a"]`; empty/no text → `[]`.

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement.**

```python
"""Parse @agent mentions from a chat message body (PHASE-2)."""
from __future__ import annotations

import re
from typing import Any

_MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9_-]+)")


def extract_agent_mentions(body: dict[str, Any], known_slugs: set[str]) -> list[str]:
    text = ""
    if isinstance(body, dict):
        raw = body.get("text")
        if isinstance(raw, str):
            text = raw
    if not text or not known_slugs:
        return []
    lower_known = {s.lower(): s for s in known_slugs}
    out: list[str] = []
    seen: set[str] = set()
    for m in _MENTION_RE.finditer(text):
        slug = lower_known.get(m.group(1).lower())
        if slug and slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out
```

> The `(?<![\w@])` lookbehind stops `a@b.com` from matching `@b` (preceded by a word char).

- [ ] **Step 4: Run → pass.** Lint. Commit — `feat(chat): @agent mention parser`.

---

## Task 3: chat_repository — agent membership + turn context + mention fanout

**Files:** Modify `backend/app/repositories/chat_repository.py`; Test: `backend/tests/integration/test_chat_agent_channel.py`.

**Interfaces (Produces):**
- `add_agent_to_channel(*, channel_id, agent_id, added_by) -> None` (INSERT into `agent_channels`, ON CONFLICT DO NOTHING).
- `is_agent_in_channel(*, channel_id, agent_id) -> bool`.
- `list_channel_agent_ids(*, channel_id) -> list[str]`.
- `recent_messages(*, channel_id, limit=20) -> list[dict]` (newest `limit`, ascending, non-deleted — context for the agent turn).
- `increment_mentions(*, channel_id, user_ids: list[str]) -> None` (UNREAD-03: `mention_count += 1` only for the @'d members; small write-fanout).
- `get_channel(*, channel_id) -> dict | None` (need `team_id`, `type`, `history_mode`).

- [ ] **Step 1: Integration tests** (reuse the `chat_team_and_user` fixture pattern from PHASE-1's `test_chat_repository.py`): add agent to channel + is_agent_in_channel True; recent_messages returns ascending newest-N; increment_mentions bumps only named members.

- [ ] **Step 2: Run → fail (methods missing).**

- [ ] **Step 3: Implement** the methods using `db_engine` named params + `_bigint()` coercion, mirroring PHASE-1 repo style. `recent_messages`: `SELECT ... WHERE channel_id=:cid AND deleted_at IS NULL ORDER BY seq DESC LIMIT :limit` then reverse to ascending in Python. `increment_mentions`: `UPDATE channel_members SET mention_count = mention_count + 1 WHERE channel_id=:cid AND user_id = ANY(:uids)` (cast uids to uuid[]).

- [ ] **Step 4: Run integration (or CI-only if no DB) + lint. Commit** — `feat(chat): repo agent-membership + turn context + mention fanout`.

---

## Task 4: channel_agent_turn (run one agent turn, scoped + gated)

**Files:** Create `backend/app/services/chat/channel_agent_turn.py`; Test: `backend/tests/test_channel_agent_turn.py`.

**Interfaces (Consumes):** agent_repo, PromptComposer, build_agent_runner_stack, AgentRunner, RunRecorder, resource_fetch (Task 1), agent_chat_caps, chat_repository (Task 3). **(Produces):** `async run_channel_agent_turn(*, agent_slug, summoner_user_id, channel) -> str | None` — returns the agent's reply text, or None if blocked (caps/scope). Does NOT write the message (caller does) — keeps it testable.

- [ ] **Step 1: Unit test** (mock agent_repo, the runner, resource_fetch): 
  - agent not found → None
  - `caps.enabled` false → None (no runner call)
  - `caps.allows_team(channel.team_id)` false → None
  - happy: runner returns content → returns content; and the resource_fetch_handler bound passes `user_id=summoner` AND `team_id=channel.team_id` AND is gated by `caps.read_team_resources`.

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement.** Mirror `ai_library_chat_service._run_session_turn_inner` but leaner (no ai_sessions): 
  1. `agent = await agent_repo.get_by_slug(agent_slug)`; if None → return None.
  2. `caps = agent_chat_caps(agent)`; if `not caps.enabled or not caps.allows_team(channel["team_id"])` → log denial, return None.
  3. Build messages from `chat_repository.recent_messages(channel_id)` mapped to `{"role": "user"|"assistant", "content": <text>}` (agent msgs → assistant, human → user; render media cards as a short text stub).
  4. `composed = await PromptComposer(agent_repo, skill_repo).compose(ComposerInput(agent=agent, ...))`.
  5. `stack = await build_agent_runner_stack(agent=agent, ..., user_id=summoner_user_id, ...)`; bind a resource_fetch_handler closure that calls `resource_fetch(..., user_id=summoner_user_id, team_id=channel["team_id"])` ONLY if `caps.read_team_resources` else a handler that refuses with a clear "not permitted to read team files" tool result (CHAT-PERM-10). Log each resource access to application_logs (CHAT-SEC-AGENT-08).
  6. `async with RunRecorder(agent_id, user_id=summoner_user_id, trigger="chat_summon", ...): result = await runner.run_turn(composed, messages)`.
  7. Return `result.get("content")`.

> Prompt-injection note (CHAT-AGENT-07): the channel messages are passed as conversation content (data), never as system instructions. The system prompt comes only from the agent's IDENTITY/SOUL/AGENT. Add a one-line instruction in the composed request that channel text from others is untrusted data.

- [ ] **Step 4: Run → pass.** Lint. Commit — `feat(chat): channel agent turn (scoped resource_fetch + caps gates + audit)`.

---

## Task 5: chat_service — add_agent (join gate) + summon hook

**Files:** Modify `backend/app/services/chat_service.py`; Test: `backend/tests/test_chat_agent_summon.py`.

**Interfaces (Produces):**
- `add_agent(*, channel_id, user_id, agent_slug) -> dict` — PERM-08 join gate: caller must be channel member; agent must be `caps.enabled` and `caps.allows_team(channel.team_id)`, else PermissionError. Then `repo.add_agent_to_channel`.
- `dispatch_summons(*, channel_id, summoner_user_id, message) -> list[str]` — the background entry: re-reads the message; **iron anti-loop**: if `message["from_bot_agent_id"]` is set, return [] immediately. Parse mentions (mention_parser over known chat-enabled agent slugs in this channel via `list_channel_agent_ids`), filter to agents present AND enabled, run `run_channel_agent_turn` per agent (respecting a per-channel throttle), write each reply via `repo.send_message(sender_type="agent", from_bot_agent_id=agent_id, body={"text": reply})`. Returns the agent slugs that replied.

- [ ] **Step 1: Unit tests** (mock repo + run_channel_agent_turn):
  - `dispatch_summons` on an agent-authored message (`from_bot_agent_id` set) → returns [] and run_channel_agent_turn NOT called (anti-loop).
  - human message mentioning an in-channel enabled agent → calls turn, writes reply with `sender_type="agent"` + `from_bot_agent_id`.
  - mention of an agent NOT in the channel → skipped.
  - `add_agent` by non-member → PermissionError; by member with a non-enabled agent → PermissionError.

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement.** `add_agent`: `_require_member(channel_id, user_id)`; load channel + agent; `caps=agent_chat_caps(agent)`; if `not caps.enabled or not caps.allows_team(channel["team_id"])` → PermissionError; `repo.add_agent_to_channel`. `dispatch_summons`: anti-loop guard first; resolve in-channel agent ids→slugs; `extract_agent_mentions`; per matched agent run the turn + write reply; wrap each agent in try/except so one failure doesn't block others (log failures). Throttle: a module-level `asyncio.Semaphore`/per-channel in-flight set capping concurrent turns (e.g. 2) — skip + log if exceeded.

- [ ] **Step 4: Run → pass.** Lint. Commit — `feat(chat): agent join gate + summon dispatch (anti-loop, throttle)`.

---

## Task 6: chat_router — add-agent endpoint + summon on post

**Files:** Modify `backend/app/api/chat_router.py`; Test: `backend/tests/test_chat_router.py` (append).

**Interfaces (Produces):**
- `POST /chat/channels/{channel_id}/agents` body `{agent_slug}` → `add_agent` (PermissionError→403).
- `post_message` gains `background_tasks: BackgroundTasks`; after the human message is created, schedule `background_tasks.add_task(_run_summons, channel_id, auth.user_id, created_message)` (which calls `get_chat_service().dispatch_summons(...)`). The POST returns immediately with the human message; agent replies arrive via Realtime.

- [ ] **Step 1: Router tests** (mock service): add-agent 403 when service raises PermissionError, 200 on success; post_message still returns the human message synchronously and schedules a background task (assert `background_tasks.add_task` called with the new message). Use FastAPI's `BackgroundTasks` — in tests, override or inspect via a captured task list.

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement.** Add the `/agents` POST. In `post_message`, add `background_tasks: BackgroundTasks` param; after `svc.post_message(...)` returns `msg`, call `background_tasks.add_task(_summon_runner, channel_id, auth.user_id, msg)` where `_summon_runner` is a module async fn that calls `await get_chat_service().dispatch_summons(...)` inside its own try/except (background tasks must not raise into the response). Map PermissionError→403 on the add-agent route.

- [ ] **Step 4: Run → pass.** Verify `uv run python -c "import app.main"`. Lint. Commit — `feat(chat): add-agent endpoint + background summon on message post`.

---

## Task 7: Frontend — @mention + add-agent affordance (thin)

**Files:** Modify `frontend/services/chatService.ts` (`addAgent(channelId, agentSlug)`), `frontend/components/chat/Composer.tsx` (optional @-hint), and ensure `MessageBubble` already renders agent replies (it does — agent tag). Test: tsc + build.

> Minimal: the summon works purely from typing `@slug` in a normal message (backend parses it). The only required FE addition is `chatService.addAgent` + a way to add an agent to a channel (a small menu, or defer to API). Keep this task thin; the agent reply already renders via the PHASE-1 MessageBubble agent branch.

- [ ] **Step 1:** Add `addAgent(channelId: string, agentSlug: string)` to chatService (POST `/chat/channels/{id}/agents`). Optionally surface chat-enabled agents in the composer @-autocomplete (defer if large). `npx tsc --noEmit` + `npm run build`.
- [ ] **Step 2:** Commit — `feat(chat): frontend addAgent service + @mention support`.

---

## Self-Review
**Spec coverage:** CHAT-SEC-AGENT-01/02/03/04/08 (Tasks 1,4,5), CHAT-AGENT-02/03/04/07 (Tasks 4,5), CHAT-PERM-08/09/10/12 (Tasks 4,5,6), UNREAD-03 (Task 3 + wire in Task 5). PERM-11 (broadcast gate) belongs to PHASE-4 broadcast — not here.
**Explicit deferrals:** auto-broadcast (PHASE-4), durable DBOS-queued turns (this phase uses BackgroundTasks — note in PR), frontend agent-picker/create-group UI, agent DM.
**Iron-law check:** every resource read path goes through `resource_fetch(summoner_user_id, team_id=channel.team_id)` gated by `caps.read_team_resources`; no service_role read; anti-loop guard is the FIRST check in dispatch_summons.
**Live-prod note:** PHASE-0/1 are already in prod; PHASE-2 is additive and tightens (no summon path exists today). Ship via the same /ship + land + canary flow.

## Execution Handoff
Execute via superpowers:subagent-driven-development; final whole-branch review (security-focused on the authorization iron law + anti-loop); then /ship.
