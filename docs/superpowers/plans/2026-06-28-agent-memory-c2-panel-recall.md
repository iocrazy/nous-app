# Agent Memory Phase C2 — Activate Shared Recall + Admin Review Panel

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate a recall caller's `team_ids` so SHARED memories are actually recalled at agent-turn time (still dark behind `FEATURE_AGENT_MEMORY=False`), and give admins a UI to review/approve/reject/demote promotion proposals.

**Architecture:** Two independent slices. (A) Backend: inside the already-wired, flag-gated `_safe_recall_agent_memory`, fetch the user's team memberships and rebuild the `MemoryContext` with `team_ids` populated — so `recall_rows`' `team_id = ANY(:team_ids)` clause surfaces shared rows the user is entitled to. The work is paid only when the flag is on. (B) Frontend: a "Memory Promotions" section in the admin app's existing `MemorySettings` page, consuming the 4 Phase-C1 endpoints. **No flag is flipped here** — go-live (`FEATURE_AGENT_MEMORY=true` in prod) remains a separate, explicit operator decision after this ships.

**Tech Stack:** Backend FastAPI + SQLAlchemy Core (asyncpg). Frontend admin app: React + TypeScript + Arco Design + React Query + Axios (dir `admin/`, NOT `frontend/`). Backend lint = black+isort+flake8; loguru `{}`/f-strings. Admin build/typecheck = `cd admin && npm run build` (`tsc && vite build`).

## Global Constraints

- **Recall stays dark.** Do NOT change `FEATURE_AGENT_MEMORY`'s default (False) and do NOT flip it anywhere. The new team_ids fetch must sit AFTER the `if not settings.FEATURE_AGENT_MEMORY: return []` gate so it costs nothing when the flag is off.
- **Isolation is preserved by construction.** `team_ids` = the recall **user's own** team memberships (from `team_members WHERE user_id = :uid`), never the session's team or any caller-supplied value. The recall SQL predicate is unchanged: `owner_user_id = :user_id OR (visibility='shared' AND team_id = ANY(:team_ids))`. A user can only ever see shared rows of teams they belong to.
- **`recall_rows` already ignores `project_id`** — project-shared rows carry their project's `team_id` (the C1 backfill), so the team_ids check covers them. Do NOT add a project_id branch to the recall SQL. Populating `MemoryContext.project_id` is optional/cosmetic; team_ids is the load-bearing field.
- **A memory miss must never break a chat turn.** All new fetches are best-effort: on any error, fall back to empty team_ids (degrade to owner-only recall), never raise out of `_safe_recall_agent_memory`.
- **Admin UI mirrors existing patterns exactly** — the new section lives in `admin/src/pages/settings/MemorySettings.tsx` alongside Graph Memory / Honcho; hooks go in `admin/src/api/endpoints/settings.ts` using the existing `useQuery`/`useMutation` + `apiClient` + `queryClient.invalidateQueries` shape. UI copy is English (Title Case headings, button labels).
- **Endpoint base path:** the 4 C1 routes are under `/api/v1/admin/settings` → full paths `/api/v1/admin/settings/memory/promotions[...]` and `/api/v1/admin/settings/memory/{memory_id}/demote`.

---

### Task 1: Activate shared recall (populate team_ids, flag-dark)

**Files:**
- Modify: `backend/app/repositories/agent_memory_repository.py` (add `get_user_team_ids`)
- Modify: `backend/app/services/ai/chat/ai_library_chat_wiring.py` (`_safe_recall_agent_memory`)
- Test: `backend/tests/memory/test_recall_team_ids_activation.py` (new)

**Interfaces:**
- Produces: `async get_user_team_ids(user_id: str) -> List[int]` — `SELECT team_id FROM public.team_members WHERE user_id = :uid` via `read_scope()`; returns a list of BIGINT team ids (or `[]` on error/none); never raises.

- [ ] **Step 1: Write the failing tests** (`test_recall_team_ids_activation.py`):
  - `test_get_user_team_ids_returns_ids`: patch `read_scope` to yield a mock session whose `execute(...).scalars().all()` returns `[10, 20]`; assert `await get_user_team_ids("u1") == [10, 20]` and the bound param is `uid="u1"`.
  - `test_get_user_team_ids_empty_on_error`: `read_scope` raises → returns `[]`.
  - `test_recall_populates_team_ids_when_flag_on`: with `settings.FEATURE_AGENT_MEMORY=True` (monkeypatch), patch `get_user_team_ids` (AsyncMock → `[7]`) and `recall` (AsyncMock → some hits), call `_safe_recall_agent_memory(ctx, "q")` where `ctx` has `user_id="u1", team_ids=()`; assert `recall` was awaited with a ctx whose `team_ids == (7,)`.
  - `test_recall_skips_team_fetch_when_flag_off`: with the flag False, patch `get_user_team_ids` as AsyncMock; call `_safe_recall_agent_memory(ctx, "q")`; assert it returns `[]` AND `get_user_team_ids` was NOT awaited (cost gated behind the flag).
  - `test_recall_degrades_when_team_fetch_fails`: flag True, `get_user_team_ids` raises → `_safe_recall_agent_memory` still calls `recall` with `team_ids=()` (owner-only) and does not raise.

- [ ] **Step 2: Run tests, verify they fail.** `cd backend && uv run pytest tests/memory/test_recall_team_ids_activation.py -v`

- [ ] **Step 3: Implement.**
  - In `agent_memory_repository.py`, add (mirroring `recall_rows`' `read_scope()` style):
    ```python
    _USER_TEAM_IDS_SQL = text(
        "SELECT team_id FROM public.team_members WHERE user_id = :uid"
    )

    async def get_user_team_ids(user_id: str) -> List[int]:
        """The caller's team memberships → list of BIGINT team ids (or [])."""
        try:
            async with read_scope() as session:
                result = await session.execute(_USER_TEAM_IDS_SQL, {"uid": user_id})
                return [int(t) for t in result.scalars().all()]
        except Exception:  # noqa: BLE001 — recall must degrade, never raise
            logger.warning(f"[agent_memory] team-ids read failed user={user_id}")
            return []
    ```
    (Add `get_user_team_ids` to `__all__`.)
  - In `ai_library_chat_wiring.py` `_safe_recall_agent_memory`, AFTER the flag gate, before `recall(...)`:
    ```python
    from dataclasses import replace
    from app.services.ai.memory.agent_memory import recall
    from app.repositories.agent_memory_repository import get_user_team_ids

    team_ids = tuple(await get_user_team_ids(str(ctx.user_id)))
    ctx = replace(ctx, team_ids=team_ids)
    hits = await recall(ctx, query, limit=5)
    ```
    Keep the surrounding try/except (best-effort). `ctx` is a frozen `MemoryContext`; `dataclasses.replace` returns a new instance with `team_ids` set. (Leave `project_id` as-is — unused by recall.)

- [ ] **Step 4: Run tests, verify pass.** Then the full memory suite `cd backend && uv run pytest tests/memory/ -q` + import smoke `uv run python -c "import app.services.ai.chat.ai_library_chat_wiring"`.

- [ ] **Step 5: Lint** changed py files. **Commit:** `feat(memory): populate team_ids for shared recall (flag-dark) (C2)`

---

### Task 2: Admin promotions API hooks + types

**Files:**
- Modify: `admin/src/api/endpoints/settings.ts` (add types + hooks)

**Interfaces:**
- Produces (consumed by Task 3): `usePromotions(status)`, `useApprovePromotion()`, `useRejectPromotion()`, `useDemoteMemory()`.

- [ ] **Step 1: Add the TypeScript types** mirroring `PromotionItem`/`PromotionListResponse`:
  ```typescript
  export interface PromotionItem {
    id: number
    memory_id: number
    proposed_scope: string
    target_team_id: number
    target_project_id: number | null
    title: string
    owner_user_id: string
    original_body_md: string
    scrubbed_body_md: string
    classification_kind: string
    confidence: number
    justification: string
    status: string
    created_at: string
  }
  export interface PromotionListResponse { items: PromotionItem[] }
  ```

- [ ] **Step 2: Add a URL constant + the query hook** (mirror `useGraphMemorySettings` exactly):
  ```typescript
  const PROMOTIONS_URL = '/api/v1/admin/settings/memory/promotions'

  export function usePromotions(status: 'pending' | 'approved' | 'rejected' = 'pending') {
    return useQuery({
      queryKey: ['settings', 'promotions', status],
      queryFn: async () => {
        const { data } = await apiClient.get<PromotionListResponse>(
          PROMOTIONS_URL, { params: { status } },
        )
        return data
      },
    })
  }
  ```

- [ ] **Step 3: Add the three mutation hooks** (mirror `useUpdateGraphMemorySettings`'s mutation + invalidate shape). All invalidate `['settings','promotions']` on success:
  ```typescript
  export function useApprovePromotion() {
    const qc = useQueryClient()
    return useMutation({
      mutationFn: async (proposalId: number) => {
        const { data } = await apiClient.post<{ approved: boolean }>(
          `${PROMOTIONS_URL}/${proposalId}/approve`,
        )
        return data
      },
      onSuccess: () => qc.invalidateQueries({ queryKey: ['settings', 'promotions'] }),
    })
  }
  export function useRejectPromotion() {
    const qc = useQueryClient()
    return useMutation({
      mutationFn: async (proposalId: number) => {
        const { data } = await apiClient.post<{ rejected: boolean }>(
          `${PROMOTIONS_URL}/${proposalId}/reject`,
        )
        return data
      },
      onSuccess: () => qc.invalidateQueries({ queryKey: ['settings', 'promotions'] }),
    })
  }
  export function useDemoteMemory() {
    const qc = useQueryClient()
    return useMutation({
      mutationFn: async (memoryId: number) => {
        const { data } = await apiClient.post<{ demoted: boolean }>(
          `/api/v1/admin/settings/memory/${memoryId}/demote`,
        )
        return data
      },
      onSuccess: () => qc.invalidateQueries({ queryKey: ['settings', 'promotions'] }),
    })
  }
  ```
  (Match the file's existing import of `useQuery`, `useMutation`, `useQueryClient`, `apiClient`. If they aren't already imported, add to the existing import lines — do not duplicate.)

- [ ] **Step 4: Typecheck.** `cd admin && npx tsc --noEmit` (or `npm run build`) → no new errors in the changed file. **Commit:** `feat(admin): promotions review API hooks (C2)`

---

### Task 3: Admin "Memory Promotions" review section

**Files:**
- Modify: `admin/src/pages/settings/MemorySettings.tsx` (add a new section component + render it)

**Interfaces:**
- Consumes: `usePromotions`, `useApprovePromotion`, `useRejectPromotion`, `useDemoteMemory` (Task 2); `PromotionItem` type.

- [ ] **Step 1: Read** `MemorySettings.tsx` to see how the existing Graph Memory / Honcho sections are structured (the `SectionHeader` usage, Arco `Card`/`Table`/`Button`/`Tabs`/`Message` imports, and where sections are rendered in the page's JSX). Mirror that exactly.

- [ ] **Step 2: Add a `PromotionsSection` component** in the same file (or a sibling component file `admin/src/pages/settings/PromotionsSection.tsx` if the page file is large — follow the file-size norms): a `SectionHeader` titled "Memory Promotions" + an Arco `Tabs` with "Pending" / "Approved" / "Rejected", each rendering an Arco `Table` of `usePromotions(status).data?.items`. Columns: Title, Owner (owner_user_id), Scope (proposed_scope), Kind (classification_kind), Confidence (toFixed(2)), Created (created_at), and an Actions column. Use an Arco `Modal` or expandable row to show `original_body_md` vs `scrubbed_body_md` side by side so the admin reviews exactly what will be shared.
  - Pending rows: "Approve" + "Reject" buttons → `useApprovePromotion().mutate(item.id)` / `useRejectPromotion().mutate(item.id)`; on success show Arco `Message.success`.
  - Approved rows: a "Demote" button → `useDemoteMemory().mutate(item.memory_id)` (revoke a shared memory).
  - Loading/empty states via the table's `loading` prop + empty render.

- [ ] **Step 3: Render `<PromotionsSection />`** at the bottom of the MemorySettings page JSX, after the existing sections.

- [ ] **Step 4: Typecheck + build.** `cd admin && npm run build` → succeeds with no type errors. **Commit:** `feat(admin): Memory Promotions review panel section (C2)`

---

### Task 4: Regression + builds + PR

- [ ] **Step 1:** `cd backend && uv run pytest tests/ -k "memory or consolidat or promotion or admin or recall" -q` → all pass.
- [ ] **Step 2:** backend lint (black/isort/flake8) on changed py; `uv run python -c "import app.services.ai.chat.ai_library_chat_wiring"` import smoke. `cd admin && npm run build` → green.
- [ ] **Step 3:** PR:

```bash
git push -u origin feature/agent-memory-c2-panel-recall
gh pr create --base master --head feature/agent-memory-c2-panel-recall \
  --title "feat(memory): shared-recall activation + admin promotion panel — Phase C2" \
  --body "C2 of Phase C. (A) Backend: _safe_recall_agent_memory now populates MemoryContext.team_ids from the user's own team memberships so shared memories are recalled — still dark behind FEATURE_AGENT_MEMORY=False (the team-ids fetch is paid only when the flag is on; degrades to owner-only on any error). Isolation unchanged: team_ids is the user's memberships, recall SQL predicate untouched. (B) Admin: a Memory Promotions review section (Pending/Approved/Rejected tabs) in MemorySettings consuming the 4 C1 endpoints — approve/reject pending proposals, demote shared memories, with original-vs-scrubbed side-by-side. No migration. Go-live (flipping FEATURE_AGENT_MEMORY in prod) remains a separate operator decision."
```

---

## Self-Review

**Spec coverage:** activate shared recall → Task 1 (team_ids populated, flag-dark, isolation-preserving) ✓; admin review panel → Tasks 2-3 (hooks + UI section, 4 endpoints, original-vs-scrubbed review) ✓; go-live left as operator decision (flag untouched) ✓.

**Placeholder scan:** none — exact SQL, hook code, and column lists given.

**Type consistency:** `get_user_team_ids(user_id: str) -> List[int]` (Task 1) feeds `tuple(...)` → `MemoryContext.team_ids: Tuple[int,...]` (matches the frozen dataclass field). `PromotionItem` TS fields (Task 2) match the backend Pydantic `PromotionItem` 1:1 (incl. `target_project_id: number | null`). `useApprovePromotion`/`useRejectPromotion` take `proposal_id` (= `item.id`); `useDemoteMemory` takes `memory_id` (= `item.memory_id`) — matching the endpoint path params.
