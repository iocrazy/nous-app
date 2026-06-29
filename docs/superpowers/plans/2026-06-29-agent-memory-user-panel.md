# User-Facing "My Agent Memories" Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an end user view and delete the curated agent memories that belong to them — their OWN private memories + their teams' SHARED memories (RLS-isolated) — via a panel in user Settings, next to the existing Honcho "My Memory" panel.

**Architecture:** A new user-facing (non-admin) backend router `GET /api/v1/agent-memory` (list) + `DELETE /api/v1/agent-memory/{id}` (delete own only), reusing the recall isolation predicate (`owner OR shared+team-member`) as the SOLE live guard (service-role + explicit WHERE, same as Phase A recall; RLS is defense-in-depth). The frontend adds an `AgentMemoriesPanel` (sibling of the existing `MemoryPanel`) rendered inside `AISettings`, so users see both memory systems in one place. View + delete only — NO edit (avoids re-introducing PII into a shared row).

**Tech Stack:** Backend FastAPI + SQLAlchemy Core (asyncpg). User frontend (`frontend/`): React 19 + Vite + TailwindCSS + i18next + lucide-react. Backend lint = black+isort+flake8; loguru `{}`/f-strings. Frontend: `cd frontend && npm run build` + `npm test` (vitest).

## Global Constraints

- **Isolation (paramount):** the list query returns ONLY `owner_user_id = :user_id OR (visibility='shared' AND team_id = ANY(:team_ids))` where `team_ids` = the **authenticated user's own** memberships (`get_user_team_ids(auth.user_id)`), never a client value. Identical predicate to `recall_rows`. Delete is restricted to `owner_user_id = :user_id` — a user can NEVER delete another user's memory, including a team-shared one authored by someone else.
- **No PII leak of other users:** do NOT expose other owners' `owner_user_id` to the client. The list maps each row to an `is_owner: bool` (computed server-side `owner_user_id == auth.user_id`); the client uses `is_owner` to decide deletability, and never sees other users' UUIDs.
- **No edit.** Only list + delete. There is no update/PATCH endpoint.
- **User-auth, not admin.** Endpoints use the regular `AuthDep` (`app.core.deps.AuthDep` → `AuthContext.user_id`), NOT `AdminAuthDep`.
- **Read-mostly / no migration.** No schema change. The DELETE is the only write, scoped to the caller's own rows.
- **UI copy English (Title Case)**, all strings via i18next keys in BOTH `en.json` and `zh.json`. Mirror the existing `MemoryPanel` Tailwind style (ink-* palette, rounded sections).
- **Endpoint base:** routes register on `api_router` (mounted at `/api/v1`) → full paths `/api/v1/agent-memory` and `/api/v1/agent-memory/{memory_id}`. Distinct from the Honcho `/api/v1/ai/memory/*` router.

---

### Task 1: Backend user-facing list + delete endpoint

**Files:**
- Modify: `backend/app/repositories/agent_memory_repository.py` (add `list_user_memories`, `delete_user_memory`)
- Create: `backend/app/api/agent_memory_user_router.py`
- Modify: `backend/app/api/__init__.py` (register the router)
- Modify: `backend/app/schemas/` — add a `UserMemoryItem`/`UserMemoryListResponse` (put in an existing relevant schema module or a new `backend/app/schemas/agent_memory.py`)
- Test: `backend/tests/memory/test_user_memory_repository.py` + `backend/tests/test_agent_memory_user_router.py`

**Interfaces:**
- Produces:
  - `async list_user_memories(*, user_id: str, team_ids: List[int], limit: int = 200) -> List[Dict[str, Any]]` — `read_scope()`; SELECT `id, owner_user_id, title, body_md, kind, scope, visibility, when_to_use, created_at` FROM agent_memory WHERE `status='active' AND (owner_user_id = :user_id OR (visibility='shared' AND team_id = ANY(:team_ids)))` ORDER BY `created_at DESC` LIMIT `:limit`. Returns raw dicts (or [] on error; never raises).
  - `async delete_user_memory(*, memory_id: int, user_id: str) -> bool` — `write_scope()`; `DELETE FROM public.agent_memory WHERE id = :id AND owner_user_id = :user_id`; returns True if a row was deleted (rowcount>0), False otherwise; never raises.
  - `UserMemoryItem(BaseModel)`: `id: int`, `title: str`, `body_md: str`, `kind: str`, `scope: str`, `visibility: str`, `when_to_use: str`, `created_at: str`, `is_owner: bool`. `UserMemoryListResponse(BaseModel)`: `items: List[UserMemoryItem]`.

- [ ] **Step 1: Write the failing tests.**
  - `test_user_memory_repository.py`: `list_user_memories` binds `user_id`/`team_ids` and returns mapped dicts (patch `read_scope` → mock session); `list_user_memories` returns [] on error; `delete_user_memory` issues a DELETE binding `id`+`user_id` and returns True when rowcount>0, False when 0; `delete_user_memory` returns False on error (never raises).
  - `test_agent_memory_user_router.py` (mirror `tests/test_admin_memory_promotions.py` but with a non-admin `AuthDep` MagicMock carrying `.user_id`): patch `app.api.agent_memory_user_router.list_user_memories` + `get_user_team_ids` (AsyncMock); call the list handler; assert `get_user_team_ids` called with `auth.user_id`, `list_user_memories` called with that user_id + the team_ids, and the response maps `is_owner = (row owner == auth.user_id)` correctly for an own row vs a team-shared row owned by someone else (and that the other owner's UUID is NOT in the response). For delete: patch `delete_user_memory` (AsyncMock→True), call the delete handler, assert it forwards `memory_id` + `user_id=auth.user_id` and returns `{"deleted": True}`.

- [ ] **Step 2: Run tests, verify they fail.**

- [ ] **Step 3: Implement.**
  - Repo: add the two functions mirroring `recall_rows`/`write_memory_row` style (read_scope/write_scope, text(), try/except never-raises). For delete, get rowcount via `result.rowcount` from the `session.execute`. Add both to `__all__`.
  - Schemas: define `UserMemoryItem` + `UserMemoryListResponse`.
  - Router `agent_memory_user_router.py`:
    ```python
    from fastapi import APIRouter
    from loguru import logger
    from app.core.deps import AuthDep
    from app.repositories.agent_memory_repository import (
        list_user_memories, delete_user_memory, get_user_team_ids,
    )
    from app.schemas.agent_memory import UserMemoryItem, UserMemoryListResponse

    router = APIRouter(prefix="/agent-memory", tags=["Agent Memories"])

    @router.get("", response_model=UserMemoryListResponse)
    async def list_my_memories(auth: AuthDep):
        team_ids = await get_user_team_ids(auth.user_id)
        rows = await list_user_memories(user_id=auth.user_id, team_ids=team_ids)
        items = [
            UserMemoryItem(
                id=r["id"], title=r.get("title") or "", body_md=r.get("body_md") or "",
                kind=r.get("kind") or "fact", scope=r.get("scope") or "",
                visibility=r.get("visibility") or "private",
                when_to_use=r.get("when_to_use") or "",
                created_at=str(r.get("created_at") or ""),
                is_owner=(str(r.get("owner_user_id")) == str(auth.user_id)),
            )
            for r in rows
        ]
        return UserMemoryListResponse(items=items)

    @router.delete("/{memory_id}")
    async def delete_my_memory(memory_id: int, auth: AuthDep):
        deleted = await delete_user_memory(memory_id=memory_id, user_id=auth.user_id)
        logger.info("[agent_memory] user {} delete memory {}: {}", auth.user_id, memory_id, deleted)
        return {"deleted": deleted}
    ```
  - `api/__init__.py`: `from app.api.agent_memory_user_router import router as agent_memory_user_router` + `api_router.include_router(router=agent_memory_user_router, tags=["Agent Memories"])` alongside the other includes.

- [ ] **Step 4: Run tests, verify pass.** Full `cd backend && uv run pytest tests/memory/ tests/test_agent_memory_user_router.py -q` + import smoke `uv run python -c "import app.api"` (or `import app.main`).

- [ ] **Step 5: Lint** changed py. **Commit:** `feat(memory): user-facing list/delete agent-memory endpoints`

---

### Task 2: Frontend "My Agent Memories" panel in AISettings

**Files:**
- Create: `frontend/services/agentMemoryService.ts`
- Create: `frontend/components/AgentMemoriesPanel.tsx`
- Modify: `frontend/components/AISettings.tsx` (render `<AgentMemoriesPanel />` after `<MemoryPanel />`)
- Modify: `frontend/public/locales/en.json` + `frontend/public/locales/zh.json` (add `agentMemories.*`)
- Test: `frontend/components/AgentMemoriesPanel.test.tsx` (mirror `MemoryPanel.test.tsx`)

**Interfaces:**
- Consumes: backend `GET /api/v1/agent-memory`, `DELETE /api/v1/agent-memory/{id}`.

- [ ] **Step 1: Read** `frontend/services/memoryService.ts` (the `base()`/`getApiUrl()`/`getAuthHeaders()`/`unwrap()` pattern) and `frontend/components/MemoryPanel.tsx` (section/list/delete/loading/error/empty Tailwind structure + `MemoryPanel.test.tsx`) to mirror exactly.

- [ ] **Step 2: Write `agentMemoryService.ts`:**
  ```ts
  import { getApiUrl } from '../utils/apiConfig';
  import { getAuthHeaders } from './parserService';

  export interface AgentMemoryItem {
    id: number; title: string; body_md: string; kind: string;
    scope: string; visibility: string; when_to_use: string;
    created_at: string; is_owner: boolean;
  }
  const base = () => `${getApiUrl()}/api/v1/agent-memory`;

  export const listAgentMemories = async (): Promise<AgentMemoryItem[]> => {
    const res = await fetch(base(), { headers: await getAuthHeaders() });
    if (!res.ok) throw new Error('Failed to load memories');
    const data = await res.json();
    return data.items ?? [];
  };
  export const deleteAgentMemory = async (id: number): Promise<void> => {
    const res = await fetch(`${base()}/${id}`, { method: 'DELETE', headers: await getAuthHeaders() });
    if (!res.ok) throw new Error('Failed to delete memory');
  };
  ```

- [ ] **Step 3: Write `AgentMemoriesPanel.tsx`** mirroring `MemoryPanel.tsx`'s `<section>` shell (ink palette, header with a lucide icon + `t('agentMemories.title')` + subtitle, loading `Loader2`, error banner, empty state). List each `AgentMemoryItem`:
  - title (bold) + a `kind` chip + a scope/visibility chip: `is_owner && visibility==='private'` → "Personal"; `visibility==='shared'` → "Shared with Team"; render `when_to_use` muted + `body_md`.
  - A `Trash2` delete button ONLY when `item.is_owner` (others' team-shared rows render a small "Shared by a teammate" read-only note, no delete). Delete with a confirm (mirror MemoryPanel's confirm pattern or a simple window/confirm-state), call `deleteAgentMemory(id)`, optimistically drop from local list, show error on failure.
  - Load on mount via `useEffect`. All copy via `t('agentMemories.*')`.

- [ ] **Step 4: Render in `AISettings.tsx`** — add `import { AgentMemoriesPanel } from './AgentMemoriesPanel';` and render `<AgentMemoriesPanel />` immediately AFTER `<MemoryPanel />` (around line 1484).

- [ ] **Step 5: i18n** — add to both `en.json` and `zh.json` an `agentMemories` section: `title` ("My Agent Memories" / "我的智能体记忆"), `subtitle` (e.g. "Facts and decisions your AI has saved. Personal ones are yours; team ones are shared." / 中文), `empty` ("No saved memories yet" / "暂无记忆"), `delete` ("Delete"/"删除"), `deleteConfirm` ("Delete this memory?"/"删除这条记忆?"), `personal` ("Personal"/"个人"), `shared` ("Shared with Team"/"团队共享"), `sharedByTeammate` ("Shared by a teammate"/"队友共享"), `loadError` ("Failed to load memories"/"加载失败"), `deleteError` ("Failed to delete"/"删除失败").

- [ ] **Step 6: Write `AgentMemoriesPanel.test.tsx`** (vitest, mirror `MemoryPanel.test.tsx`): mock `agentMemoryService` — `listAgentMemories` returns one own row + one team-shared (is_owner:false); assert both render, the own row shows a delete button and the team-shared row does NOT; clicking delete calls `deleteAgentMemory` with the id and removes the row.

- [ ] **Step 7: Build + test.** `cd frontend && npm run build` (tsc/vite) green + `npm test -- AgentMemoriesPanel` passes. **Commit:** `feat(frontend): My Agent Memories panel in AISettings`

---

### Task 3: Regression + builds + PR

- [ ] **Step 1:** `cd backend && uv run pytest tests/ -k "memory or agent_memory or admin" -q` → all pass.
- [ ] **Step 2:** backend lint (black/isort/flake8) on changed py; `uv run python -c "import app.main"` import smoke; `cd frontend && npm run build` green + `npm test -- AgentMemoriesPanel MemoryPanel` pass.
- [ ] **Step 3:** PR:

```bash
git push -u origin feature/agent-memory-user-panel
gh pr create --base master --head feature/agent-memory-user-panel \
  --title "feat(memory): user-facing My Agent Memories panel" \
  --body "Lets end users view + delete their curated agent memories (own private + their teams' shared) from user Settings, next to the existing Honcho My Memory panel. New user-auth endpoints GET/DELETE /api/v1/agent-memory reuse the recall isolation predicate (own OR shared+team-member); delete is restricted to the caller's own rows; other users' UUIDs are never exposed (server-computed is_owner). View + delete only (no edit → no PII re-introduction into shared rows). No migration. Frontend AgentMemoriesPanel mirrors MemoryPanel, rendered in AISettings; i18n en+zh."
```

---

## Self-Review

**Spec coverage:** user sees own + team-shared → Task 1 `list_user_memories` isolation predicate ✓; user deletes own only → `delete_user_memory` owner-scoped + UI delete gated on `is_owner` ✓; no other-user UUID leak → server-computed `is_owner` ✓; no edit ✓; co-located in AISettings next to MemoryPanel ✓; i18n en+zh ✓; user-auth not admin ✓.

**Placeholder scan:** none — exact SQL, router, service, and panel structure specified.

**Type consistency:** `list_user_memories` dict keys → `UserMemoryItem` fields (Task 1) → TS `AgentMemoryItem` (Task 2) align 1:1 (`is_owner: bool`↔`boolean`, `id: int`↔`number`). `deleteAgentMemory(id: number)` → `DELETE /agent-memory/{memory_id:int}`. `get_user_team_ids` (existing) returns `List[int]` consumed by `list_user_memories(team_ids)`.
