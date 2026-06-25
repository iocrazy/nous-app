# Task 4 Report: Router — role gate + preset carve-out + deep-merge + response enrich + audit

## Changes Made

### `backend/app/api/ai_library_router.py`
1. **Imports added**: `ChatPermissionsOut` added to `from app.schemas.ai_library import (...)` block; `from app.services.ai.permissions.agent_chat_caps import agent_chat_caps` added.
2. **`_is_team_owner(user_uuid, team_id) -> bool`** added after `_user_is_admin`: queries `team_members` for `role in ('owner', 'admin')`; fail-closed on any error.
3. **`_can_edit_chat_permissions(agent_repo, agent, user_uuid) -> bool`** added: orchestrates agent-owner → team-owner → platform-admin chain, delegating platform-admin check to existing `_user_is_admin`.
4. **`_with_chat_permissions(row) -> Dict`** added: calls `agent_chat_caps(row)` then injects `chat_permissions` dict from `ChatPermissionsOut.from_caps(caps).model_dump()`.
5. **PATCH `/agents/{slug}` handler rewritten**:
   - Extracts `updates` excluding `skill_ids` AND `chat_permissions`.
   - Preset carve-out: blocks content+skill changes, allows chat-perm-only PATCHes.
   - Role gate: checks `_can_edit_chat_permissions` before any chat_permissions write; 403 on failure.
   - Deep-merge: `{**existing_profile, "chat": {**existing_chat, **new_perms}}` never clobbers unrelated keys.
   - Audit log via `logger.info(...)` on every successful chat_permissions change.
   - Response wrapped with `_with_chat_permissions`.
6. **GET `/agents` list** loop now wraps each row with `_with_chat_permissions`.
7. **GET `/agents/{slug}`** wraps result row with `_with_chat_permissions`.

### `backend/tests/test_ai_library_agent_permissions.py`
4 router tests appended (all imports moved to file top to satisfy flake8 E402):
- `test_patch_chat_permissions_on_preset_merges_and_returns_perms` — happy path on preset: merge verified in storage, response carries resolved perms (review C1).
- `test_patch_chat_permissions_denied_without_role` — `_can_edit_chat_permissions=False` → 403 (review H1).
- `test_patch_content_field_on_preset_still_403` — content-only patch on preset → 403.
- `test_patch_mixed_content_and_perms_on_preset_403` — mixed payload on preset → 403.

## JSONB Encoding Verdict

**Raw dict — no `json.dumps` needed.**

Reasoning: `update_fields_versioned` calls `client.table(...).update(patch).eq(...).execute()` where `patch` is a plain Python dict. `supabase-py` uses `httpx` internally and serializes the entire request body as JSON before sending to PostgREST. PostgREST then interprets dict values as JSONB. This is the same pattern used for `frontmatter_json` (also a JSONB dict) in `SkillRepository`. No `json.dumps` wrapper is needed. No live DB was reachable for round-trip confirmation; verdict based on consistent existing usage across both repos.

## Test Output

```
tests/test_ai_library_agent_permissions.py — 8 passed
regression (tests/ -k ai_library) — 81 passed, 0 failures
```

## Regression Result

81 passed, 4476 deselected — zero regressions in the full AI Library test suite.

## Lint

`black`, `isort`, `flake8` all clean on both changed files (0 errors).

## Concerns

- `_user_uuid` is set twice if both `chat_permissions` is present AND `updates` is non-empty — harmless redundancy since `_coerce_user_uuid` is deterministic, but could be extracted to a single `user_uuid = _coerce_user_uuid(auth.user_id)` at handler entry.
- `_is_team_owner` does a `maybe_single()` query; if the user has multiple rows in `team_members` for the same team (shouldn't happen but not DB-constrained), only the first row is seen. Fail-closed in the ambiguous case, which is safe.
- `_with_chat_permissions` is not applied to `create_agent` POST response (intentional — new agents have no capability_profile, so `agent_chat_caps` returns all-false defaults which match the `AgentOut.chat_permissions` field default).

---

## Review Findings Fix — commit fa29a69f

### Changes Made

#### `backend/app/api/ai_library_router.py`

1. **Fix #1 (Important) — Fail-closed role gate**: Wrapped the final `await _user_is_admin(user_uuid)` call inside `_can_edit_chat_permissions` in `try/except Exception: return False`. The shared `_user_is_admin` helper is left untouched so the delete_skill admin gate and any other caller keeps raise-on-error behavior.

2. **Fix #3 (Minor) — Loguru f-string**: Changed `logger.info("chat_permissions changed by %s on agent %s: %s", auth.user_id, agent["slug"], chat_audit)` → `logger.info(f"chat_permissions changed by {auth.user_id} on agent {agent['slug']}: {chat_audit}")`.

3. **Fix #4 (Minor) — Deduplicate user_uuid**: Hoisted `user_uuid = _coerce_user_uuid(auth.user_id)` to immediately after `agent_uuid = UUID(str(agent["id"]))`, before either of the two previous assignment sites (the role gate block and the `if updates:` block). Removed both downstream assignments.

#### `backend/tests/test_ai_library_agent_permissions.py`

4. **Fix #2 (Important) — Unit tests for `_can_edit_chat_permissions`**: Added 5 `@pytest.mark.asyncio` tests that import and call the real `_can_edit_chat_permissions` function, monkeypatching only its two dependency calls:
   - `test_can_edit_agent_owner_returns_true` — (a) caller IS agent owner → True, short-circuits without calling either dep.
   - `test_can_edit_team_owner_returns_true` — (b) team owner → True, `_is_team_owner` called once, `_user_is_admin` not called.
   - `test_can_edit_stranger_returns_false` — (c) both deps return False → False.
   - `test_can_edit_platform_admin_returns_true` — (d) `_user_is_admin` returns True → True.
   - `test_can_edit_admin_db_error_returns_false` — (e) `_user_is_admin` raises RuntimeError → False (validates fix #1).

### Test Output

```
tests/test_ai_library_agent_permissions.py  — 13 passed (8 original + 5 new)
regression (tests/ -k ai_library)           — 86 passed, 0 failures
```

### Lint

`black`, `isort`, `flake8` all clean on both changed files (0 errors, 0 warnings).

### Concerns

None beyond what was already noted above.
