# Memory Control Plane — Phase 2a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Give the admin Memory page a control plane: per-slot (L2/L3) provider selection + a live health dot + a Reload button, backed by three new admin endpoints over the Phase-1 `MemoryProvider` registry.

**Architecture:** Phase 1 shipped `MemoryProvider` + a settings-driven registry (`l2_provider()`/`l3_provider()` read `memory.l2_provider`/`memory.l3_provider`, default honcho/graphiti, `"none"` disables). Phase 2a adds a thin admin REST surface — `GET /memory/control` (list slots + provider + health), `PUT /memory/slot` (switch a slot's provider → writes the setting), `POST /memory/{slot}/reload` (calls `provider.reload()`) — and a "Provider Slots" section in `MemorySettings.tsx`. **The immediate win: Graphiti's `reload()` re-reads `from_settings()`, so editing the existing graph_* config and clicking Reload applies it with no backend restart.**

**Tech Stack:** FastAPI + admin auth (`AdminAuthDep`); React + Arco Design + TanStack Query (`apiClient`); pytest (asyncio). Backend lint = black + isort + flake8 (NOT ruff).

## Global Constraints

- Backend lint = black + isort + flake8 (run on every changed `.py`; project uses flake8, not ruff). loguru calls use `{}`/f-strings, NEVER `%s`.
- Admin endpoints take `auth: AdminAuthDep` as the FIRST param (`from app.core.admin_deps import AdminAuthDep`). The settings_router is mounted at `/api/v1/admin/settings`, so new paths are `/api/v1/admin/settings/memory/control`, `/memory/slot`, `/memory/{slot}/reload`.
- The Phase-1 registry returns a FRESH adapter each call (no cache); reading `memory.l2_provider`/`memory.l3_provider` is via `memory_registry._read_provider_setting(key, default)`. Slot values: l2 ∈ {honcho, none}; l3 ∈ {graphiti, none} (Phase 1 providers only — Mem0/Hindsight arrive Phase 3).
- Writing a slot setting must UPSERT (the `memory.*_provider` rows may not pre-exist — Phase 1 relies on the missing-key default). Use the settings repo's `upsert_setting(key, value, updated_by)`, NOT `update` (which only patches an existing row).
- UI text in English; admin app is `admin/` (Arco), NOT `frontend/`.
- This is additive — no Phase-1 behavior changes.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `backend/app/schemas/admin.py` (modify) | `MemorySlotStatus`, `MemoryControlResponse`, `MemorySlotUpdate`, `MemoryReloadResponse` |
| `backend/app/api/admin/settings_router.py` (modify) | 3 endpoints: GET control, PUT slot, POST reload |
| `backend/tests/test_memory_control_endpoints.py` (new) | endpoint unit tests (registry + repo mocked) |
| `admin/src/api/endpoints/settings.ts` (modify) | `useMemoryControl` / `useSetMemorySlot` / `useReloadMemorySlot` hooks |
| `admin/src/pages/settings/MemorySettings.tsx` (modify) | "Provider Slots" section (dropdown + health dot + Reload) |

---

### Task 1: Backend — schemas + 3 control endpoints

**Files:**
- Modify: `backend/app/schemas/admin.py` (append near the GraphMemory schemas, ~L265)
- Modify: `backend/app/api/admin/settings_router.py`
- Test: `backend/tests/test_memory_control_endpoints.py`

**Interfaces:**
- Produces:
  - `MemorySlotStatus(BaseModel)`: `slot: str`, `provider: str`, `health: bool`.
  - `MemoryControlResponse(BaseModel)`: `slots: list[MemorySlotStatus]`.
  - `MemorySlotUpdate(BaseModel)`: `slot: Literal["l2", "l3"]`, `provider: str`.
  - `MemoryReloadResponse(BaseModel)`: `ok: bool`, `reloaded: Optional[str] = None`.
  - Endpoints: `get_memory_control(auth)` → MemoryControlResponse; `set_memory_slot(update, auth)` → MemoryControlResponse; `reload_memory_slot(slot, auth)` → MemoryReloadResponse.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_memory_control_endpoints.py
"""Admin memory control-plane endpoints (Phase 2a)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _provider(name: str, healthy: bool):
    p = MagicMock()
    p.name = name
    p.health = AsyncMock(return_value=healthy)
    p.reload = AsyncMock()
    return p


@pytest.mark.asyncio
async def test_get_memory_control_lists_slots_with_health():
    from app.api.admin.settings_router import get_memory_control

    l2, l3 = _provider("honcho", True), _provider("graphiti", False)
    with patch(
        "app.api.admin.settings_router.memory_registry.l2_provider",
        new=AsyncMock(return_value=l2),
    ), patch(
        "app.api.admin.settings_router.memory_registry.l3_provider",
        new=AsyncMock(return_value=l3),
    ):
        resp = await get_memory_control(MagicMock())

    by_slot = {s.slot: s for s in resp.slots}
    assert by_slot["l2"].provider == "honcho" and by_slot["l2"].health is True
    assert by_slot["l3"].provider == "graphiti" and by_slot["l3"].health is False


@pytest.mark.asyncio
async def test_get_memory_control_reports_none_when_slot_disabled():
    from app.api.admin.settings_router import get_memory_control

    with patch(
        "app.api.admin.settings_router.memory_registry.l2_provider",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.api.admin.settings_router.memory_registry.l3_provider",
        new=AsyncMock(return_value=_provider("graphiti", True)),
    ):
        resp = await get_memory_control(MagicMock())

    by_slot = {s.slot: s for s in resp.slots}
    assert by_slot["l2"].provider == "none" and by_slot["l2"].health is False


@pytest.mark.asyncio
async def test_set_memory_slot_upserts_and_rejects_invalid():
    from app.api.admin.settings_router import set_memory_slot
    from app.schemas.admin import MemorySlotUpdate
    from fastapi import HTTPException

    repo = MagicMock()
    repo.upsert_setting = AsyncMock(return_value={})
    auth = MagicMock()
    auth.user_id = "admin-1"

    with patch(
        "app.api.admin.settings_router.get_system_settings_repository",
        return_value=repo,
    ), patch(
        "app.api.admin.settings_router.memory_registry.l2_provider",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.api.admin.settings_router.memory_registry.l3_provider",
        new=AsyncMock(return_value=_provider("graphiti", True)),
    ):
        # valid: l2 → none
        await set_memory_slot(MemorySlotUpdate(slot="l2", provider="none"), auth)
        repo.upsert_setting.assert_awaited_once_with(
            "memory.l2_provider", "none", "admin-1"
        )
        # invalid: l2 → graphiti (wrong layer)
        with pytest.raises(HTTPException) as exc:
            await set_memory_slot(MemorySlotUpdate(slot="l2", provider="graphiti"), auth)
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_reload_memory_slot_calls_provider_reload():
    from app.api.admin.settings_router import reload_memory_slot

    l3 = _provider("graphiti", True)
    with patch(
        "app.api.admin.settings_router.memory_registry.l3_provider",
        new=AsyncMock(return_value=l3),
    ):
        resp = await reload_memory_slot("l3", MagicMock())

    assert resp.ok is True and resp.reloaded == "graphiti"
    l3.reload.assert_awaited_once()


@pytest.mark.asyncio
async def test_reload_rejects_bad_slot_and_handles_none():
    from app.api.admin.settings_router import reload_memory_slot
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await reload_memory_slot("l9", MagicMock())
    assert exc.value.status_code == 400

    with patch(
        "app.api.admin.settings_router.memory_registry.l2_provider",
        new=AsyncMock(return_value=None),
    ):
        resp = await reload_memory_slot("l2", MagicMock())
    assert resp.ok is False and resp.reloaded is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_memory_control_endpoints.py -q`
Expected: FAIL — `ImportError: cannot import name 'get_memory_control'` (and the schema imports fail).

- [ ] **Step 3: Implement the schemas**

Append to `backend/app/schemas/admin.py` (near the GraphMemory schemas; ensure `Literal` and `Optional` are imported at the top — add to the existing `from typing import ...` if missing):

```python
class MemorySlotStatus(BaseModel):
    """One memory slot's current provider + liveness (Phase 2a control plane)."""

    slot: str  # "l2" | "l3"
    provider: str  # active provider name, or "none" when the slot is disabled
    health: bool


class MemoryControlResponse(BaseModel):
    slots: list[MemorySlotStatus]


class MemorySlotUpdate(BaseModel):
    slot: Literal["l2", "l3"]
    provider: str  # validated against the slot's allowed set in the endpoint


class MemoryReloadResponse(BaseModel):
    ok: bool
    reloaded: Optional[str] = None  # provider name reloaded, None when slot disabled
```

- [ ] **Step 4: Implement the endpoints**

In `backend/app/api/admin/settings_router.py`, add the import near the other imports:

```python
from app.services.ai.memory import registry as memory_registry
```

Add the schema imports to the existing `from app.schemas.admin import (...)` block:
`MemoryControlResponse, MemoryReloadResponse, MemorySlotStatus, MemorySlotUpdate`.

Add (after the graph-memory endpoints):

```python
# Phase 1 providers only — Mem0/Hindsight (Phase 3) extend these sets.
_VALID_SLOT_PROVIDERS: dict[str, set[str]] = {
    "l2": {"honcho", "none"},
    "l3": {"graphiti", "none"},
}


async def _build_memory_control() -> MemoryControlResponse:
    l2 = await memory_registry.l2_provider()
    l3 = await memory_registry.l3_provider()
    slots = []
    for slot, provider in (("l2", l2), ("l3", l3)):
        slots.append(
            MemorySlotStatus(
                slot=slot,
                provider=provider.name if provider else "none",
                health=(await provider.health()) if provider else False,
            )
        )
    return MemoryControlResponse(slots=slots)


@router.get("/memory/control", response_model=MemoryControlResponse)
async def get_memory_control(auth: AdminAuthDep):
    """List each memory slot's active provider + liveness."""
    return await _build_memory_control()


@router.put("/memory/slot", response_model=MemoryControlResponse)
async def set_memory_slot(update: MemorySlotUpdate, auth: AdminAuthDep):
    """Switch a slot's provider (writes memory.<slot>_provider). Returns the
    refreshed control snapshot."""
    allowed = _VALID_SLOT_PROVIDERS.get(update.slot, set())
    if update.provider not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"provider '{update.provider}' not valid for slot '{update.slot}' "
            f"(allowed: {sorted(allowed)})",
        )
    repo = get_system_settings_repository()
    await repo.upsert_setting(
        f"memory.{update.slot}_provider", update.provider, auth.user_id
    )
    logger.info(f"[Admin] memory slot {update.slot} -> {update.provider}")
    return await _build_memory_control()


@router.post("/memory/{slot}/reload", response_model=MemoryReloadResponse)
async def reload_memory_slot(slot: str, auth: AdminAuthDep):
    """Drop the slot provider's cached client/config so the next call re-reads
    settings — applies a config edit without a backend restart."""
    if slot not in ("l2", "l3"):
        raise HTTPException(status_code=400, detail="slot must be 'l2' or 'l3'")
    provider = await (
        memory_registry.l2_provider() if slot == "l2" else memory_registry.l3_provider()
    )
    if provider is None:
        return MemoryReloadResponse(ok=False, reloaded=None)
    await provider.reload()
    logger.info(f"[Admin] reloaded memory slot {slot} ({provider.name})")
    return MemoryReloadResponse(ok=True, reloaded=provider.name)
```

> Implementer: confirm `logger`, `HTTPException`, and `get_system_settings_repository` are already imported in `settings_router.py` (they are — used by the graph-memory endpoints). Confirm `upsert_setting(key, value, updated_by)` exists on the repo (`backend/app/repositories/admin/system_settings_repository.py` ~L65) and its exact param names; adjust the call if they differ.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_memory_control_endpoints.py -q`
Expected: PASS (5 passed)

- [ ] **Step 6: Lint + commit**

```bash
cd backend && uv run black app/schemas/admin.py app/api/admin/settings_router.py tests/test_memory_control_endpoints.py && uv run isort app/schemas/admin.py app/api/admin/settings_router.py tests/test_memory_control_endpoints.py && uv run flake8 app/schemas/admin.py app/api/admin/settings_router.py tests/test_memory_control_endpoints.py
cd .. && git add backend/app/schemas/admin.py backend/app/api/admin/settings_router.py backend/tests/test_memory_control_endpoints.py
git commit -m "feat(memory): admin control-plane endpoints (list/switch/reload slots)"
```

---

### Task 2: Frontend — Provider Slots section

**Files:**
- Modify: `admin/src/api/endpoints/settings.ts` (add hooks near the GraphMemory hooks, ~L149)
- Modify: `admin/src/pages/settings/MemorySettings.tsx` (add a section after the Embedder section, before the final `<Divider/>`/Save at ~L345)

**Interfaces:**
- Consumes: the Task-1 endpoints at `/api/v1/admin/settings/memory/control` (GET), `/memory/slot` (PUT body `{slot, provider}`), `/memory/{slot}/reload` (POST).
- Produces: `useMemoryControl()` (useQuery → `{ slots: {slot, provider, health}[] }`), `useSetMemorySlot()` (useMutation, var `{slot, provider}`), `useReloadMemorySlot()` (useMutation, var `slot`). A "Provider Slots" `SectionHeader` block in MemorySettings.

- [ ] **Step 1: Add the API hooks**

In `admin/src/api/endpoints/settings.ts`, add (mirroring the existing `GRAPH_MEMORY_URL` hooks + their `useQueryClient` invalidation style):

```typescript
const MEMORY_CONTROL_URL = '/api/v1/admin/settings/memory'

export interface MemorySlotStatus {
  slot: string
  provider: string
  health: boolean
}
export interface MemoryControl {
  slots: MemorySlotStatus[]
}

export function useMemoryControl() {
  return useQuery({
    queryKey: ['settings', 'memory-control'],
    queryFn: async () => {
      const { data } = await apiClient.get<MemoryControl>(`${MEMORY_CONTROL_URL}/control`)
      return data
    },
  })
}

export function useSetMemorySlot() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (vars: { slot: string; provider: string }) => {
      const { data } = await apiClient.put<MemoryControl>(`${MEMORY_CONTROL_URL}/slot`, vars)
      return data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings', 'memory-control'] }),
  })
}

export function useReloadMemorySlot() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (slot: string) => {
      const { data } = await apiClient.post<{ ok: boolean; reloaded: string | null }>(
        `${MEMORY_CONTROL_URL}/${slot}/reload`,
      )
      return data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings', 'memory-control'] }),
  })
}
```

> Implementer: confirm `useQuery`, `useMutation`, `useQueryClient`, and `apiClient` are already imported at the top of `settings.ts` (the GraphMemory hooks use them). Match the existing import style.

- [ ] **Step 2: Add the Provider Slots section to MemorySettings.tsx**

At the top of `MemorySettings.tsx`, add to the existing imports:
- from `'@arco-design/web-react'`: ensure `Select`, `Button`, `Message`, `Tag` are imported (most already are).
- from `'@arco-design/web-react/icon'`: add `IconSync` (reload icon) to the existing icon import.
- from `'../../api/endpoints/settings'`: add `useMemoryControl, useSetMemorySlot, useReloadMemorySlot`.
- add `IconApps` (or reuse an existing icon) for the section header.

Inside `MemorySettings()`, near the other hooks:
```tsx
const { data: control } = useMemoryControl()
const setSlot = useSetMemorySlot()
const reloadSlot = useReloadMemorySlot()

const SLOT_META: Record<string, { label: string; providers: string[] }> = {
  l2: { label: 'L2 · User Model', providers: ['honcho', 'none'] },
  l3: { label: 'L3 · Knowledge Graph', providers: ['graphiti', 'none'] },
}
```

Add this block right BEFORE the final `<Divider />` that precedes the Save button (after the Embedder section). It renders a SectionHeader + one row per slot:

```tsx
<Divider />
<SectionHeader
  icon={<IconApps />}
  title="Provider Slots"
  subtitle="Choose the backend for each memory layer, see its health, and reload it after a config change (no backend restart)."
/>
{(control?.slots ?? []).map((s) => {
  const meta = SLOT_META[s.slot] ?? { label: s.slot, providers: [s.provider] }
  return (
    <div
      key={s.slot}
      style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '14px 0' }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span
          title={s.health ? 'Healthy' : 'Unreachable / disabled'}
          style={{
            display: 'inline-block', width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
            background: s.health ? '#00b42a' : '#f53f3f',
          }}
        />
        <span style={{ fontWeight: 500, fontSize: 14 }}>{meta.label}</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Select
          value={s.provider}
          style={{ width: 160 }}
          onChange={(provider) =>
            setSlot.mutate(
              { slot: s.slot, provider },
              {
                onSuccess: () => Message.success(`${meta.label} → ${provider}`),
                onError: (e: any) => Message.error(e?.message || 'Failed to switch provider'),
              },
            )
          }
        >
          {meta.providers.map((p) => (
            <Select.Option key={p} value={p}>{p}</Select.Option>
          ))}
        </Select>
        <Button
          size="small"
          icon={<IconSync />}
          loading={reloadSlot.isPending}
          disabled={s.provider === 'none'}
          onClick={() =>
            reloadSlot.mutate(s.slot, {
              onSuccess: (r) =>
                r.ok
                  ? Message.success(`Reloaded ${r.reloaded}`)
                  : Message.warning('Slot disabled — nothing to reload'),
              onError: (e: any) => Message.error(e?.message || 'Reload failed'),
            })
          }
        >
          Reload
        </Button>
      </div>
    </div>
  )
})}
```

> Implementer: `IconApps` — if not exported by the Arco icon package, use any neutral icon already imported in the file (e.g. `IconStorage`). The exact icon is cosmetic; do not block on it.

- [ ] **Step 3: Typecheck + build**

Run: `cd admin && npx tsc --noEmit` (expect no output) then `cd admin && npm run build` (expect "✓ built").

- [ ] **Step 4: Commit**

```bash
git add admin/src/api/endpoints/settings.ts admin/src/pages/settings/MemorySettings.tsx
git commit -m "feat(admin): Memory Provider Slots control plane (select + health + reload)"
```

---

### Task 3: Regression + lint + PR

**Files:** none (verification only).

- [ ] **Step 1: Backend regression (memory + settings surface)**

Run: `cd backend && uv run pytest tests/test_memory_control_endpoints.py tests/ -k "memory or settings or graph or honcho" -q`
Expected: PASS (all green — the new endpoints plus the unchanged Phase-1/settings tests).

- [ ] **Step 2: Lint the full changed set**

Run: `cd backend && uv run black --check app/schemas/admin.py app/api/admin/settings_router.py tests/test_memory_control_endpoints.py && uv run isort --check-only app/schemas/admin.py app/api/admin/settings_router.py tests/test_memory_control_endpoints.py && uv run flake8 app/schemas/admin.py app/api/admin/settings_router.py tests/test_memory_control_endpoints.py`
Expected: clean. Then `cd admin && npx tsc --noEmit` clean.

- [ ] **Step 3: Open the PR**

```bash
git push -u origin feature/memory-control-plane
gh pr create --base master --head feature/memory-control-plane \
  --title "feat(memory): admin Memory control plane (Phase 2a)" \
  --body "Phase 2a of the pluggable-memory roadmap. Adds an admin control plane over the Phase-1 provider registry: per-slot (L2/L3) provider select + live health dot + Reload button, via GET /memory/control, PUT /memory/slot, POST /memory/{slot}/reload. Immediate win: Graphiti reload re-reads from_settings, so editing graph_* config + Reload applies it with no backend restart. Phase 2b = migrate Honcho connection config to system_settings + editable Honcho section."
```

---

## Self-Review

**Spec coverage:**
- Per-slot provider select → Task 1 PUT `/memory/slot` + Task 2 `<Select>`. ✓
- Health dot → Task 1 `health` field (calls `provider.health()`) + Task 2 dot. ✓
- Reload button → Task 1 POST `/memory/{slot}/reload` (calls `provider.reload()`) + Task 2 Button. ✓
- Reads existing registry, no Phase-1 behavior change → endpoints only read/reload + write the slot setting. ✓
- Out of scope (Phase 2b): Honcho connection→settings, editable Honcho section. Not in this plan. ✓

**Placeholder scan:** No TBD/TODO. Two cosmetic-icon notes (IconApps fallback) flagged with explicit fallback instructions — not placeholders.

**Type consistency:** `MemorySlotStatus{slot, provider, health}` is identical in the backend schema (Task 1) and the TS interface (Task 2). `MemorySlotUpdate{slot, provider}` matches the PUT body sent by `useSetMemorySlot`. `MemoryReloadResponse{ok, reloaded}` matches the POST response typed in `useReloadMemorySlot`. Slot values l2/l3 and provider sets (honcho|none, graphiti|none) consistent across `_VALID_SLOT_PROVIDERS`, `SLOT_META`, and the tests.
