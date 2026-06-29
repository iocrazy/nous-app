# Agent Memory Stats (Observability) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give admins an at-a-glance view of the agent-memory layer's health — whether recall is live, how the store is filling, and the promotion-queue depth — via a pure-SQL stats endpoint surfaced in the admin Memory panel.

**Architecture:** A read-only `GET /api/v1/admin/settings/memory/stats` endpoint runs aggregate SQL over `agent_memory` + `agent_memory_promotions` and reads the `FEATURE_AGENT_MEMORY` flag, returning a `MemoryStatsResponse`. The admin app renders an "Agent Memory Stats" card in `MemorySettings`, next to the C2 Promotions section. No new instrumentation, no migration, no hot-path writes — recall hit-rate is explicitly out of scope (would need a counter on the chat hot path; deferred).

**Tech Stack:** Backend FastAPI + SQLAlchemy Core (asyncpg). Frontend admin app: React + TypeScript + Arco Design + React Query + Axios (dir `admin/`). Backend lint = black+isort+flake8; loguru `{}`/f-strings. Admin build = `cd admin && npm run build`.

## Global Constraints

- **Read-only, zero hot-path impact.** The stats endpoint only runs SELECT aggregates + reads config. Nothing on the chat/recall path changes. No migration.
- **Recall hit-rate is OUT of scope** — do not add any counter/instrumentation to `_safe_recall_agent_memory` or the recall path.
- **Admin-only.** The endpoint uses `AdminAuthDep` (role=admin), like the other `/api/v1/admin/settings/memory/*` routes.
- **Mirror existing patterns exactly.** Repo aggregate in `agent_memory_repository.py` style (`read_scope()` + `text()`, never raises → safe defaults). Endpoint + schema mirror the C1/C2 promotion routes. Admin hook mirrors `usePromotions`; the UI card mirrors the existing MemorySettings section style. UI copy English Title Case.
- **Exact identifiers:** table `public.agent_memory` columns: `visibility` ∈ (private,shared), `scope` ∈ (session,user,agent_user,project,team), `status` ∈ (active,archived,superseded), `created_at`. Table `public.agent_memory_promotions` column `status` ∈ (pending,approved,rejected).
- **Endpoint base path:** routes are under `/api/v1/admin/settings` → full path `/api/v1/admin/settings/memory/stats`.

---

### Task 1: Backend stats repository + endpoint + schema

**Files:**
- Modify: `backend/app/repositories/agent_memory_repository.py` (add `get_memory_stats`)
- Modify: `backend/app/api/admin/settings_router.py` (add `GET /memory/stats`)
- Modify: `backend/app/schemas/admin.py` (add `MemoryStatsResponse`)
- Test: `backend/tests/memory/test_memory_stats.py` (new) + extend `backend/tests/test_admin_memory_promotions.py` or a new admin test for the endpoint

**Interfaces:**
- Produces:
  - `async get_memory_stats() -> Dict[str, Any]` — runs the aggregates via `read_scope()`; returns a dict with keys: `total_active` (int), `by_visibility` (dict[str,int]), `by_scope` (dict[str,int]), `by_status` (dict[str,int]), `created_24h` (int), `created_7d` (int), `last_created_at` (Optional[str ISO]), `promotions` (dict[str,int] with keys pending/approved/rejected). On error returns the same shape with zeros/empty (never raises).
  - `MemoryStatsResponse(BaseModel)`: `recall_enabled: bool`, `total_active: int`, `by_visibility: Dict[str,int]`, `by_scope: Dict[str,int]`, `by_status: Dict[str,int]`, `created_24h: int`, `created_7d: int`, `last_created_at: Optional[str] = None`, `promotions: Dict[str,int]`.

- [ ] **Step 1: Write the failing tests** (`backend/tests/memory/test_memory_stats.py`):
  - `test_get_memory_stats_aggregates`: patch `read_scope` to yield a mock session whose `execute(...).mappings().all()`/`.first()` return canned aggregate rows; assert `get_memory_stats()` maps them into the documented dict shape (e.g. `by_visibility == {"private": 5, "shared": 2}`, `total_active == 7`, `promotions == {"pending": 1, "approved": 0, "rejected": 0}`).
  - `test_get_memory_stats_safe_on_error`: `read_scope` raises → returns the zero/empty-shaped dict, no raise.
  - For the endpoint: in a new `backend/tests/test_admin_memory_stats.py` (mirror `test_admin_memory_promotions.py`): patch `app.api.admin.settings_router.get_memory_stats` (AsyncMock → a stats dict) and monkeypatch `settings.FEATURE_AGENT_MEMORY`; call `memory_stats(auth=...)` directly; assert the response's `recall_enabled` reflects the flag and the counts pass through.

- [ ] **Step 2: Run tests, verify they fail.**

- [ ] **Step 3: Implement.**
  - `get_memory_stats` — use `read_scope()`; two aggregate queries are enough:
    ```python
    _STATS_AM_SQL = text(
        """
        SELECT
          count(*) FILTER (WHERE status='active')                          AS total_active,
          count(*) FILTER (WHERE status='active' AND visibility='private') AS vis_private,
          count(*) FILTER (WHERE status='active' AND visibility='shared')  AS vis_shared,
          count(*) FILTER (WHERE status='active' AND scope='agent_user')   AS scope_agent_user,
          count(*) FILTER (WHERE status='active' AND scope='team')         AS scope_team,
          count(*) FILTER (WHERE status='active' AND scope='project')      AS scope_project,
          count(*) FILTER (WHERE status='active')                          AS st_active,
          count(*) FILTER (WHERE status='archived')                        AS st_archived,
          count(*) FILTER (WHERE status='superseded')                      AS st_superseded,
          count(*) FILTER (WHERE created_at >= now() - interval '24 hours') AS created_24h,
          count(*) FILTER (WHERE created_at >= now() - interval '7 days')   AS created_7d,
          max(created_at)                                                   AS last_created_at
        FROM public.agent_memory
        """
    )
    _STATS_PROMO_SQL = text(
        """
        SELECT
          count(*) FILTER (WHERE status='pending')  AS pending,
          count(*) FILTER (WHERE status='approved') AS approved,
          count(*) FILTER (WHERE status='rejected') AS rejected
        FROM public.agent_memory_promotions
        """
    )
    ```
    Wrap in try/except → on error return `{"total_active":0,"by_visibility":{},"by_scope":{},"by_status":{},"created_24h":0,"created_7d":0,"last_created_at":None,"promotions":{"pending":0,"approved":0,"rejected":0}}`. Coerce `last_created_at` to `str(row["last_created_at"])` (or None). Add to `__all__`.
  - Endpoint in `settings_router.py`:
    ```python
    @router.get("/memory/stats", response_model=MemoryStatsResponse)
    async def memory_stats(auth: AdminAuthDep):
        stats = await get_memory_stats()
        return MemoryStatsResponse(
            recall_enabled=bool(settings.FEATURE_AGENT_MEMORY),
            total_active=stats.get("total_active", 0),
            by_visibility=stats.get("by_visibility", {}),
            by_scope=stats.get("by_scope", {}),
            by_status=stats.get("by_status", {}),
            created_24h=stats.get("created_24h", 0),
            created_7d=stats.get("created_7d", 0),
            last_created_at=stats.get("last_created_at"),
            promotions=stats.get("promotions", {}),
        )
    ```
    Import `get_memory_stats` alongside the existing promotion repo imports; `settings` is already imported in this module (confirm — it's used by other routes). Add `MemoryStatsResponse` to the `app.schemas.admin` import block.

- [ ] **Step 4: Run tests, verify pass.** Full `cd backend && uv run pytest tests/memory/ tests/test_admin_memory_stats.py -q` + import smoke `uv run python -c "import app.api.admin.settings_router"`.

- [ ] **Step 5: Lint** changed py. **Commit:** `feat(memory): admin stats endpoint — store/queue/flag aggregates (observability)`

---

### Task 2: Admin stats hook + "Agent Memory Stats" card

**Files:**
- Modify: `admin/src/api/endpoints/settings.ts` (add `MemoryStats` type + `useMemoryStats`)
- Modify: `admin/src/pages/settings/MemorySettings.tsx` (render a stats card) OR create `admin/src/pages/settings/MemoryStatsCard.tsx` if MemorySettings is large (follow the file-size norm — C2 created a sibling `PromotionsSection.tsx`, so prefer a sibling `MemoryStatsCard.tsx`)

**Interfaces:**
- Consumes: backend `GET /api/v1/admin/settings/memory/stats`.
- Produces: `useMemoryStats()` query hook; `<MemoryStatsCard />`.

- [ ] **Step 1: Add the type + hook** to `settings.ts` (mirror `usePromotions`):
  ```typescript
  export interface MemoryStats {
    recall_enabled: boolean
    total_active: number
    by_visibility: Record<string, number>
    by_scope: Record<string, number>
    by_status: Record<string, number>
    created_24h: number
    created_7d: number
    last_created_at: string | null
    promotions: Record<string, number>
  }
  export function useMemoryStats() {
    return useQuery({
      queryKey: ['settings', 'memory-stats'],
      queryFn: async () => {
        const { data } = await apiClient.get<MemoryStats>('/api/v1/admin/settings/memory/stats')
        return data
      },
    })
  }
  ```
  (No duplicate imports.)

- [ ] **Step 2: Build `MemoryStatsCard.tsx`** — read `MemorySettings.tsx` + `PromotionsSection.tsx` first to mirror the section/card style (SectionHeader, Arco `Card`, `Statistic`/`Descriptions`/`Tag`). Show:
  - A "Recall" status line: `recall_enabled` → a green `Tag` "Active" or grey "Disabled" (so the admin sees if the flag is on).
  - `total_active` as a headline `Statistic`.
  - A small breakdown (Arco `Descriptions` or a compact table): by_visibility (private/shared), by_scope (agent_user/team/project), by_status (active/archived/superseded), created_24h, created_7d, last_created_at.
  - Promotion queue: pending / approved / rejected counts (link visually to the Promotions section).
  - Loading state via the query's `isLoading`; render zeros/"—" gracefully when a key is absent.

- [ ] **Step 3: Render `<MemoryStatsCard />`** in MemorySettings, ABOVE the Promotions section (stats first, then the review queue).

- [ ] **Step 4: Build.** `cd admin && npm run build` → green (no new type errors). **Commit:** `feat(admin): Agent Memory Stats card (observability)`

---

### Task 3: Regression + build + PR

- [ ] **Step 1:** `cd backend && uv run pytest tests/ -k "memory or promotion or admin or stats" -q` → all pass.
- [ ] **Step 2:** backend lint (black/isort/flake8) on changed py; `uv run python -c "import app.api.admin.settings_router"` import smoke; `cd admin && npm run build` → green.
- [ ] **Step 3:** PR:

```bash
git push -u origin feature/agent-memory-stats
gh pr create --base master --head feature/agent-memory-stats \
  --title "feat(memory): Agent Memory Stats panel (observability)" \
  --body "Adds a read-only admin Agent Memory Stats panel: recall flag state (FEATURE_AGENT_MEMORY), store counts (by visibility/scope/status, created 24h/7d, last created), and promotion-queue depth (pending/approved/rejected). Pure-SQL aggregates over agent_memory + agent_memory_promotions — no migration, no hot-path writes, recall hit-rate deferred (would need chat-path instrumentation). Surfaced as a card in Settings → Memory above the C2 Promotions section."
```

---

## Self-Review

**Spec coverage:** flag state → `recall_enabled` (Task 1 endpoint reads settings) ✓; store fill → `total_active`/by_visibility/by_scope/by_status/created_24h/created_7d/last_created_at (Task 1 aggregate) ✓; promotion queue depth → `promotions` (Task 1) ✓; admin UI surfacing → Task 2 card ✓; recall hit-rate explicitly out of scope ✓; read-only/no migration/no hot-path ✓.

**Placeholder scan:** none — exact SQL, schema, hook, and card contents specified.

**Type consistency:** `get_memory_stats() -> Dict` keys (Task 1) map 1:1 to `MemoryStatsResponse` fields; the TS `MemoryStats` interface (Task 2) matches `MemoryStatsResponse` field-for-field (`Record<string,number>` ↔ `Dict[str,int]`, `string | null` ↔ `Optional[str]`, `boolean` ↔ `bool`). `useMemoryStats` reads the same `/memory/stats` path the endpoint serves.
