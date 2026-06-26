# Task 4 Report — Channel Agent Turn

**File:** `backend/app/services/chat/channel_agent_turn.py`
**Tests:** `backend/tests/test_channel_agent_turn.py`
**Branch:** `feature/team-chat-phase2`

## What Was Built

`run_channel_agent_turn(*, agent_slug, summoner_user_id, channel) -> str | None`

A lean channel-context wrapper around the existing agent runtime that runs one
agent turn triggered by an @-mention summon.  The function enforces 5 ordered
gates and returns the agent's reply string, or None if any gate blocks the run.
The caller is responsible for persisting the reply as a bot message.

## Gate Order

1. **Agent exists** — `agent_repo.get_by_slug(agent_slug)`; None → return None.
2. **Capability gate** — `agent_chat_caps(agent)`; blocks if `not caps.enabled`
   or `not caps.allows_team(channel["team_id"])`.  Both conditions logged with
   loguru f-strings at INFO level (CHAT-SEC-AGENT-08).
3. **History build** — `get_chat_repository().recent_messages(channel_id=...)`;
   mapped to `[{role, content}]` via `_render_body` (text, media_card, task_card).
4. **Runtime** — reuses `build_agent_runner_stack` + `PromptComposer` + `RunRecorder`
   (trigger="chat_summon").  No ai_sessions / ai_messages persistence.
5. **Result** — `result.get("content")` or None on empty/error.

## Iron Law Implementation (CHAT-SEC-AGENT-03 / CHAT-PERM-10)

`_build_resource_fetch_handler(...)` returns a closure that:

- Logs every access attempt with summoner, agent, channel, and resource_id
  (CHAT-SEC-AGENT-08 audit trail).
- If `not caps.read_team_resources`: logs a WARNING and returns
  `{"error": "this agent is not permitted to read team files"}` immediately,
  WITHOUT calling resource_fetch at all (CHAT-PERM-10).
- When allowed: calls `resource_fetch(resource_id=rid, user_id=summoner_user_id,
  available_refs={rid}, request_cache=..., team_id=int(team_id))`.
  **Never uses service_role.**

`available_refs={rid}` is intentional: in the channel context there is no
`<available_resources>` system-message block.  The real authorization boundary
is the DB-level RLS enforced via `(user_id=summoner, team_id=channel.team_id)`.

## Prompt-Injection Guard (CHAT-AGENT-07)

Channel messages are passed only as `user_messages=history` to `runner.run_turn`.
They are never merged into the system prompt.  `_UNTRUSTED_CHANNEL_INSTRUCTION`
is injected into `request_instructions` as a one-liner reminding the LLM that
conversation history from other users must be treated as untrusted data.

## Runtime Reuse

The function reuses the same stack as `ai_library_chat_service._run_session_turn_inner`:

- `build_agent_runner_stack` for LLM adapter + fallback chain + delegate tools.
- `PromptComposer.compose(ComposerInput(...))` for identity/soul/agent + skills.
- `RunRecorder` async context manager for telemetry (agent_runs row).
- `runner.resource_fetch_handler = closure` / `finally: = None` cleanup pattern.

## Test Results

```
5 passed in 1.01s
```

| Test | Gate | Assertion |
|------|------|-----------|
| `test_agent_not_found_returns_none` | Gate 1 | None, stack not built |
| `test_caps_disabled_returns_none` | Gate 2a | None (enabled=False), stack not built |
| `test_caps_wrong_team_returns_none` | Gate 2b | None (team_ids restricted), stack not built |
| `test_happy_path_returns_content_and_handler_scoped` | Gate 4/5 | "hello from agent"; resource_fetch called with user_id=SUMMONER, team_id=7 |
| `test_no_read_team_resources_handler_refuses` | CHAT-PERM-10 | Handler returns error dict; resource_fetch NOT called |

Handler verification strategy: mock `run_turn` invokes the handler internally
(while all patches are still active) so the `available_refs={rid}` lazy import
of `resource_fetch` picks up the test mock.

## Lint

- `black`: 1 file reformatted (test file, cosmetic trailing whitespace), 1 unchanged.
- `isort`: no changes.
- `flake8`: 0 errors.

## Concerns / Future Work

- `available_refs={rid}` bypasses the "agent can only read pre-declared refs"
  check.  This is intentional for the channel context where no resource list is
  pre-declared; the DB team-scope is the authoritative boundary.  When channel
  resource pinning (pinned resources in system prompt) is added later, this
  should be updated to use the pinned set.
- `session_id=None` means RunRecorder creates an agent_runs row without a
  session FK.  This is correct per spec (no ai_sessions persistence), but means
  the run is only visible via agent_runs, not the session-based chat history.
