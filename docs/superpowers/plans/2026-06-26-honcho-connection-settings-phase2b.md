# Honcho Connection → Settings — Phase 2b Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Move Honcho's connection config (enabled / base_url / workspace_id) from backend env to `system_settings`, so the admin can edit it in the Memory page and apply it with the existing L2 Reload — no backend restart. Behavior stays identical until an admin sets a value (per-field env fallback).

**Architecture:** Mirror the proven `GraphMemoryConfig.from_settings()` + `GraphMemoryService._ensure_config()` pattern. `HonchoMemoryConfig.from_settings()` resolves each field DB > env > default; `HonchoMemoryService` gains `_config_loaded` + `_ensure_config()` (swaps the env-default config for the DB-sourced one on first use, skipped when a client is test-injected). `HonchoProvider.reload()` also resets `_config_loaded` so a settings edit + Reload re-reads. A new `GET/PUT /memory/honcho-connection` admin surface + a "Honcho (L2) connection" section in `MemorySettings.tsx`.

**Tech Stack:** FastAPI + admin auth; React + Arco + TanStack Query; pytest (asyncio). Backend lint = black + isort + flake8 (NOT ruff); loguru `{}`/f-strings NOT `%s`.

## Global Constraints

- **Behavior-neutral until an admin sets a value.** `from_settings` does per-field DB > env > default. When the `honcho_*` keys are absent (the default — no migration seeds them), every field falls back to the SAME env var the current `from_env` reads, so a default deployment is byte-for-byte unchanged. `_ensure_config` is gated (`if self.client is not None or self._config_loaded: return`) so a test-injected client keeps the test's own config.
- Lint = black + isort + flake8; loguru `{}`/f-strings.
- Admin endpoints take `auth: AdminAuthDep` (body/path params may precede it — matches the sibling `update_graph_memory_settings(update, auth, request)`). settings_router is at `/api/v1/admin/settings`.
- No DB migration — the `honcho_*` keys are read-with-fallback and created on first admin write via `repo.upsert_setting`. The GET endpoint returns the EFFECTIVE config (`from_settings`), so the UI shows current reality (env values) before any override.
- UI text English; admin app is `admin/` (Arco).

---

## File Structure

| File | Responsibility |
|------|----------------|
| `backend/app/services/ai/memory/honcho_memory.py` (modify) | `_HONCHO_SETTINGS_MAP`, `_honcho_settings_reader`, `HonchoMemoryConfig.from_settings`, `HonchoMemoryService._config_loaded` + `_ensure_config()` called by the 9 public methods |
| `backend/app/services/ai/memory/providers/honcho_provider.py` (modify) | `reload()` also resets `_config_loaded` |
| `backend/app/schemas/admin.py` (modify) | `HonchoConnectionResponse`, `HonchoConnectionUpdate` |
| `backend/app/api/admin/settings_router.py` (modify) | `GET/PUT /memory/honcho-connection` |
| `backend/tests/test_honcho_connection_settings.py` (new) | from_settings fallback/override + _ensure_config gating + endpoints |
| `admin/src/api/endpoints/settings.ts` (modify) | `useHonchoConnection` / `useUpdateHonchoConnection` hooks |
| `admin/src/pages/settings/MemorySettings.tsx` (modify) | "Honcho (L2) connection" editable section |

---

### Task 1: Backend — `HonchoMemoryConfig.from_settings` + `_ensure_config`

**Files:**
- Modify: `backend/app/services/ai/memory/honcho_memory.py`
- Modify: `backend/app/services/ai/memory/providers/honcho_provider.py`
- Test: `backend/tests/test_honcho_connection_settings.py`

**Interfaces:**
- Produces:
  - module-level `_HONCHO_SETTINGS_MAP: dict[str, tuple[str, str]]` = `{"enabled": ("honcho_memory_enabled", "FEATURE_HONCHO_MEMORY"), "base_url": ("honcho_base_url", "HONCHO_BASE_URL"), "workspace_id": ("honcho_workspace_id", "HONCHO_WORKSPACE_ID")}`.
  - `async def _honcho_settings_reader(key: str) -> Optional[str]` — single-key system_settings read, None on miss/error.
  - `HonchoMemoryConfig.from_settings(cls, *, reader=None, env=None) -> HonchoMemoryConfig` — per-field DB > env > default.
  - `HonchoMemoryService._config_loaded: bool` field (default False) + `async def _ensure_config(self) -> None`.
  - Each of the 9 public async methods starts with `await self._ensure_config()`.
  - `HonchoProvider.reload()` additionally sets `service._config_loaded = False`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_honcho_connection_settings.py
"""Phase 2b — Honcho connection config from system_settings (env fallback)."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.memory.honcho_memory import (
    HonchoMemoryConfig,
    HonchoMemoryService,
)


@pytest.mark.asyncio
async def test_from_settings_falls_back_to_env_when_keys_absent():
    async def reader(_key):
        return None  # no settings rows

    env = {
        "FEATURE_HONCHO_MEMORY": "true",
        "HONCHO_BASE_URL": "http://honcho:18000/",
        "HONCHO_WORKSPACE_ID": "mediahub",
    }
    cfg = await HonchoMemoryConfig.from_settings(reader=reader, env=env)
    assert cfg.enabled is True
    assert cfg.base_url == "http://honcho:18000"  # trailing slash stripped
    assert cfg.workspace_id == "mediahub"


@pytest.mark.asyncio
async def test_from_settings_db_overrides_env():
    rows = {
        "honcho_memory_enabled": "false",
        "honcho_base_url": "http://new-honcho:9000",
        "honcho_workspace_id": "team-1",
    }

    async def reader(key):
        return rows.get(key)

    env = {"FEATURE_HONCHO_MEMORY": "true", "HONCHO_BASE_URL": "http://old:1"}
    cfg = await HonchoMemoryConfig.from_settings(reader=reader, env=env)
    assert cfg.enabled is False  # DB "false" wins over env "true"
    assert cfg.base_url == "http://new-honcho:9000"
    assert cfg.workspace_id == "team-1"


@pytest.mark.asyncio
async def test_ensure_config_skips_when_client_injected():
    # Tests inject a client + their own config; _ensure_config must NOT swap it.
    sentinel = HonchoMemoryConfig(enabled=True, base_url="http://test", workspace_id="w")
    svc = HonchoMemoryService(config=sentinel, client=object())  # type: ignore[arg-type]
    with patch.object(
        HonchoMemoryConfig, "from_settings", new=AsyncMock(side_effect=AssertionError)
    ):
        await svc._ensure_config()  # must not call from_settings
    assert svc.config is sentinel


@pytest.mark.asyncio
async def test_ensure_config_loads_once():
    svc = HonchoMemoryService()  # no client
    loaded = HonchoMemoryConfig(enabled=True, base_url="http://x", workspace_id="w")
    with patch.object(
        HonchoMemoryConfig, "from_settings", new=AsyncMock(return_value=loaded)
    ) as m:
        await svc._ensure_config()
        await svc._ensure_config()  # second call is a no-op
    assert svc.config is loaded
    assert m.await_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_honcho_connection_settings.py -q`
Expected: FAIL — `AttributeError: ... has no attribute 'from_settings'` / `_ensure_config`.

- [ ] **Step 3: Implement in `honcho_memory.py`**

Add near the top (after the existing `_TRUTHY` / `DEFAULT_WORKSPACE` constants), the settings map + reader:

```python
_HONCHO_SETTINGS_MAP: dict[str, tuple[str, str]] = {
    "enabled": ("honcho_memory_enabled", "FEATURE_HONCHO_MEMORY"),
    "base_url": ("honcho_base_url", "HONCHO_BASE_URL"),
    "workspace_id": ("honcho_workspace_id", "HONCHO_WORKSPACE_ID"),
}


async def _honcho_settings_reader(key: str) -> Optional[str]:
    """Read one system_settings value (service-role engine). None on miss/error."""
    try:
        from app.db import engine as db_engine

        if not db_engine.is_configured():
            return None
        value = await db_engine.fetch_val(
            "SELECT value FROM public.system_settings WHERE key = :k", {"k": key}
        )
        return None if value is None else str(value)
    except Exception:  # noqa: BLE001 — settings read must never raise
        logger.warning(f"[honcho] system_settings read failed: {key}")
        return None
```

Add `from_settings` to `HonchoMemoryConfig` (after `from_env`):

```python
    @classmethod
    async def from_settings(cls, *, reader=None, env=None) -> "HonchoMemoryConfig":
        """Resolve from system_settings with per-field env fallback (DB > env >
        default). Behaviour-neutral when the honcho_* keys are absent: every
        field falls back to the same env var from_env() reads. Never raises."""
        read = reader if reader is not None else _honcho_settings_reader
        environ = env if env is not None else os.environ

        async def resolve(field_key: str, default: str = "") -> str:
            db_key, env_key = _HONCHO_SETTINGS_MAP[field_key]
            try:
                db_val = await read(db_key)
            except Exception:  # noqa: BLE001
                logger.warning(f"[honcho] settings read failed: {db_key}")
                db_val = None
            if db_val is not None and str(db_val).strip():
                return str(db_val).strip()
            env_val = environ.get(env_key)
            return env_val.strip() if isinstance(env_val, str) else default

        enabled = (await resolve("enabled")).lower() in _TRUTHY
        base_url = (await resolve("base_url")).rstrip("/")
        workspace_id = (await resolve("workspace_id")) or DEFAULT_WORKSPACE
        return cls(enabled=enabled, base_url=base_url, workspace_id=workspace_id)
```

In `HonchoMemoryService`, add the field + the ensure method (place `_config_loaded` after the existing `_ensured` field, and `_ensure_config` right after `_get_client`):

```python
    _config_loaded: bool = False
```

```python
    async def _ensure_config(self) -> None:
        """Swap the env-default config for the DB-sourced one on first use.
        Skipped when a client is injected (tests set their own config) or after
        the first load. Never raises — a failed settings load keeps env config."""
        if self.client is not None or self._config_loaded:
            return
        self._config_loaded = True  # set first: no retry-storm, no double-load
        try:
            self.config = await HonchoMemoryConfig.from_settings()
        except Exception:  # noqa: BLE001
            logger.warning("[honcho] from_settings failed; keeping env config")
```

Add `await self._ensure_config()` as the FIRST statement (inside the method body, before any `_get_client()` or `self.config` read) of each of these 9 public methods: `add_chat_turn`, `get_user_representation`, `list_conclusions`, `get_conclusion`, `delete_conclusion`, `get_peer_card`, `set_peer_card`, `forget_user`, `get_user_context`.

> Implementer: `HonchoMemoryService` is `@dataclass` (NOT frozen) so `self.config = ...` reassignment is legal. `_config_loaded` must be a dataclass field with a default (`bool = False`), declared alongside `config` / `client` / `_ensured`. Confirm `Optional` and `os` are already imported (they are). Do NOT change `from_env` or the `config` field's `default_factory=HonchoMemoryConfig.from_env` (env default at construction is correct; from_settings applies lazily).

In `backend/app/services/ai/memory/providers/honcho_provider.py`, update `reload()` to also reset `_config_loaded` (so the next call re-reads from_settings):

```python
    async def reload(self) -> None:
        # Drop the cached httpx client AND the config-loaded flag so the next
        # call re-reads HonchoMemoryConfig.from_settings() and reconnects with
        # the new base_url. (Phase 2b: connection config now lives in settings.)
        try:
            service = get_honcho_memory_service()
            if getattr(service, "client", None) is not None:
                try:
                    await service.client.aclose()  # type: ignore[union-attr]
                except Exception:  # noqa: BLE001
                    pass
                service.client = None  # type: ignore[assignment]
            service._config_loaded = False  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            logger.warning("[honcho_provider] reload failed")
```

- [ ] **Step 4: Run tests to verify they pass (new + existing honcho)**

Run: `cd backend && uv run pytest tests/test_honcho_connection_settings.py tests/test_honcho_memory.py tests/memory/test_honcho_provider.py -q`
Expected: PASS — new tests pass AND the existing honcho tests still pass (they inject a client, so `_ensure_config` is skipped → behavior unchanged).

- [ ] **Step 5: Lint + commit**

```bash
cd backend && uv run black app/services/ai/memory/honcho_memory.py app/services/ai/memory/providers/honcho_provider.py tests/test_honcho_connection_settings.py && uv run isort app/services/ai/memory/honcho_memory.py app/services/ai/memory/providers/honcho_provider.py tests/test_honcho_connection_settings.py && uv run flake8 app/services/ai/memory/honcho_memory.py app/services/ai/memory/providers/honcho_provider.py tests/test_honcho_connection_settings.py
cd .. && git add backend/app/services/ai/memory/honcho_memory.py backend/app/services/ai/memory/providers/honcho_provider.py backend/tests/test_honcho_connection_settings.py
git commit -m "feat(memory): Honcho config from system_settings (env fallback) + reload re-reads"
```

---

### Task 2: Backend — Honcho connection admin endpoints

**Files:**
- Modify: `backend/app/schemas/admin.py`
- Modify: `backend/app/api/admin/settings_router.py`
- Test: append to `backend/tests/test_honcho_connection_settings.py`

**Interfaces:**
- Consumes: `HonchoMemoryConfig.from_settings` (Task 1); `get_system_settings_repository().upsert_setting`.
- Produces:
  - `HonchoConnectionResponse(BaseModel)`: `enabled: bool`, `base_url: str`, `workspace_id: str`.
  - `HonchoConnectionUpdate(BaseModel)`: `enabled: Optional[bool] = None`, `base_url: Optional[str] = None`, `workspace_id: Optional[str] = None`.
  - `GET /memory/honcho-connection` → HonchoConnectionResponse (effective config). `PUT /memory/honcho-connection` → upserts provided fields, returns refreshed.

- [ ] **Step 1: Write the failing test (append)**

```python
@pytest.mark.asyncio
async def test_get_honcho_connection_returns_effective_config():
    from app.api.admin.settings_router import get_honcho_connection
    from app.services.ai.memory.honcho_memory import HonchoMemoryConfig
    from unittest.mock import MagicMock

    cfg = HonchoMemoryConfig(enabled=True, base_url="http://h:1", workspace_id="w")
    with patch.object(
        HonchoMemoryConfig, "from_settings", new=AsyncMock(return_value=cfg)
    ):
        resp = await get_honcho_connection(MagicMock())
    assert resp.enabled is True and resp.base_url == "http://h:1" and resp.workspace_id == "w"


@pytest.mark.asyncio
async def test_put_honcho_connection_upserts_only_provided_fields():
    from app.api.admin.settings_router import put_honcho_connection
    from app.schemas.admin import HonchoConnectionUpdate
    from app.services.ai.memory.honcho_memory import HonchoMemoryConfig
    from unittest.mock import MagicMock

    repo = MagicMock()
    repo.upsert_setting = AsyncMock(return_value={})
    auth = MagicMock()
    auth.user_id = "admin-1"
    cfg = HonchoMemoryConfig(enabled=False, base_url="http://h:2", workspace_id="w2")
    with patch(
        "app.api.admin.settings_router.get_system_settings_repository",
        return_value=repo,
    ), patch.object(
        HonchoMemoryConfig, "from_settings", new=AsyncMock(return_value=cfg)
    ):
        await put_honcho_connection(
            HonchoConnectionUpdate(base_url="http://h:2"), auth
        )
    # only base_url provided → only that key upserted
    repo.upsert_setting.assert_awaited_once_with(
        "honcho_base_url", "http://h:2", "admin-1"
    )
```

- [ ] **Step 2: Run → fail** (`cannot import name 'get_honcho_connection'`).

Run: `cd backend && uv run pytest tests/test_honcho_connection_settings.py -q`

- [ ] **Step 3: Implement schemas** (append to `backend/app/schemas/admin.py`):

```python
class HonchoConnectionResponse(BaseModel):
    enabled: bool
    base_url: str
    workspace_id: str


class HonchoConnectionUpdate(BaseModel):
    enabled: Optional[bool] = None
    base_url: Optional[str] = None
    workspace_id: Optional[str] = None
```

- [ ] **Step 4: Implement endpoints** in `settings_router.py` (add the schema names to the `from app.schemas.admin import (...)` block; add `from app.services.ai.memory.honcho_memory import HonchoMemoryConfig` near the top):

```python
# Maps HonchoConnectionUpdate fields → system_settings keys (mirrors
# _HONCHO_SETTINGS_MAP in honcho_memory.py; the DB-key half only).
_HONCHO_CONN_KEY = {
    "enabled": "honcho_memory_enabled",
    "base_url": "honcho_base_url",
    "workspace_id": "honcho_workspace_id",
}


@router.get("/memory/honcho-connection", response_model=HonchoConnectionResponse)
async def get_honcho_connection(auth: AdminAuthDep):
    """Effective Honcho connection config (settings with env fallback)."""
    cfg = await HonchoMemoryConfig.from_settings()
    return HonchoConnectionResponse(
        enabled=cfg.enabled, base_url=cfg.base_url, workspace_id=cfg.workspace_id
    )


@router.put("/memory/honcho-connection", response_model=HonchoConnectionResponse)
async def put_honcho_connection(update: HonchoConnectionUpdate, auth: AdminAuthDep):
    """Upsert the provided connection fields; returns the refreshed effective
    config. Click L2 Reload (Provider Slots) to apply to the live service."""
    repo = get_system_settings_repository()
    fields = update.model_dump(exclude_unset=True)
    for field_name, value in fields.items():
        key = _HONCHO_CONN_KEY[field_name]
        # store enabled as the string "true"/"false" (settings values are text)
        stored = (
            ("true" if value else "false") if field_name == "enabled" else str(value)
        )
        await repo.upsert_setting(key, stored, auth.user_id)
    logger.info(f"[Admin] honcho connection updated: {sorted(fields)}")
    cfg = await HonchoMemoryConfig.from_settings()
    return HonchoConnectionResponse(
        enabled=cfg.enabled, base_url=cfg.base_url, workspace_id=cfg.workspace_id
    )
```

- [ ] **Step 5: Run → pass.** `cd backend && uv run pytest tests/test_honcho_connection_settings.py -q` (expect all pass, incl Task-1 tests).

- [ ] **Step 6: Lint + commit**

```bash
cd backend && uv run black app/schemas/admin.py app/api/admin/settings_router.py tests/test_honcho_connection_settings.py && uv run isort app/schemas/admin.py app/api/admin/settings_router.py tests/test_honcho_connection_settings.py && uv run flake8 app/schemas/admin.py app/api/admin/settings_router.py tests/test_honcho_connection_settings.py
cd .. && git add backend/app/schemas/admin.py backend/app/api/admin/settings_router.py backend/tests/test_honcho_connection_settings.py
git commit -m "feat(memory): admin Honcho connection endpoints (GET/PUT)"
```

---

### Task 3: Frontend — Honcho (L2) connection section

**Files:**
- Modify: `admin/src/api/endpoints/settings.ts`
- Modify: `admin/src/pages/settings/MemorySettings.tsx`

**Interfaces:**
- Consumes: `GET/PUT /api/v1/admin/settings/memory/honcho-connection`.
- Produces: `useHonchoConnection()` (useQuery), `useUpdateHonchoConnection()` (useMutation, invalidates `['settings','honcho-connection']` AND `['settings','memory-control']`). A "Honcho (L2) connection" SectionHeader block with an Enabled Switch + Base URL Input + Workspace Input + a Save button.

- [ ] **Step 1: Add hooks** to `settings.ts`:

```typescript
const HONCHO_CONN_URL = '/api/v1/admin/settings/memory/honcho-connection'

export interface HonchoConnection {
  enabled: boolean
  base_url: string
  workspace_id: string
}

export function useHonchoConnection() {
  return useQuery({
    queryKey: ['settings', 'honcho-connection'],
    queryFn: async () => {
      const { data } = await apiClient.get<HonchoConnection>(HONCHO_CONN_URL)
      return data
    },
  })
}

export function useUpdateHonchoConnection() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (update: Partial<HonchoConnection>) => {
      const { data } = await apiClient.put<HonchoConnection>(HONCHO_CONN_URL, update)
      return data
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['settings', 'honcho-connection'] })
      qc.invalidateQueries({ queryKey: ['settings', 'memory-control'] })
    },
  })
}
```

- [ ] **Step 2: Add the section** to `MemorySettings.tsx`. Add imports: `useHonchoConnection, useUpdateHonchoConnection` from the settings endpoints. Inside the component, near the other hooks:

```tsx
const { data: honcho } = useHonchoConnection()
const updateHoncho = useUpdateHonchoConnection()
const [hcEnabled, setHcEnabled] = useState(false)
const [hcBaseUrl, setHcBaseUrl] = useState('')
const [hcWorkspace, setHcWorkspace] = useState('')

useEffect(() => {
  if (!honcho) return
  setHcEnabled(honcho.enabled)
  setHcBaseUrl(honcho.base_url)
  setHcWorkspace(honcho.workspace_id)
}, [honcho])
```

Add this block right AFTER the "Provider Slots" section (added in Phase 2a) and before the final `<Divider/>`/Save:

```tsx
<Divider />
<SectionHeader
  icon={<IconRobot />}
  title="Honcho (L2) connection"
  subtitle="How MediaHub reaches the Honcho service. After saving, click L2 Reload above to apply. (Honcho's own embedding/LLM live in its container — env-managed on the NAS.)"
/>
<Row label="Enabled" hint="Master toggle for the L2 user-model layer.">
  <Switch checked={hcEnabled} onChange={setHcEnabled} disabled={updateHoncho.isPending} />
</Row>
<Divider style={{ margin: 0 }} />
<Row label="Base URL" hint="e.g. http://192.168.50.9:18000">
  <Input value={hcBaseUrl} onChange={setHcBaseUrl} placeholder="http://…:18000" style={{ width: 260 }} />
</Row>
<Divider style={{ margin: 0 }} />
<Row label="Workspace">
  <Input value={hcWorkspace} onChange={setHcWorkspace} placeholder="mediahub" style={{ width: 260 }} />
</Row>
<div style={{ textAlign: 'right', paddingTop: 12 }}>
  <Button
    type="primary"
    loading={updateHoncho.isPending}
    onClick={() =>
      updateHoncho.mutate(
        { enabled: hcEnabled, base_url: hcBaseUrl.trim(), workspace_id: hcWorkspace.trim() },
        {
          onSuccess: () => Message.success('Honcho connection saved — click L2 Reload to apply'),
          onError: (e: unknown) => Message.error((e as Error)?.message || 'Failed to save'),
        },
      )
    }
  >
    Save Honcho connection
  </Button>
</div>
```

- [ ] **Step 3: Typecheck + build**

Run: `cd admin && npx tsc --noEmit` (clean) then `cd admin && npm run build` (✓ built).

- [ ] **Step 4: Commit**

```bash
git add admin/src/api/endpoints/settings.ts admin/src/pages/settings/MemorySettings.tsx
git commit -m "feat(admin): editable Honcho (L2) connection section"
```

---

### Task 4: Regression + lint + PR

**Files:** none (verification only).

- [ ] **Step 1: Backend regression**

Run: `cd backend && uv run pytest tests/ -k "honcho or memory or settings or graph" -q`
Expected: PASS (all green — new tests + the unchanged Phase-1/2a/honcho tests).

- [ ] **Step 2: Lint + tsc**

Run: `cd backend && uv run black --check app/services/ai/memory/honcho_memory.py app/services/ai/memory/providers/honcho_provider.py app/schemas/admin.py app/api/admin/settings_router.py tests/test_honcho_connection_settings.py && uv run isort --check-only <same files> && uv run flake8 <same files>` then `cd admin && npx tsc --noEmit`.
Expected: clean.

- [ ] **Step 3: Open the PR**

```bash
git push -u origin feature/honcho-connection-settings
gh pr create --base master --head feature/honcho-connection-settings \
  --title "feat(memory): editable Honcho connection via settings (Phase 2b)" \
  --body "Phase 2b — moves Honcho connection config (enabled/base_url/workspace) from backend env to system_settings (per-field env fallback, behavior-neutral until set). Admin Memory page gets an editable Honcho (L2) connection section; HonchoConfig.from_settings + _ensure_config mirror the Graphiti pattern; HonchoProvider.reload now re-reads settings so edit + L2 Reload applies with no backend restart. Honcho's internal embedding/LLM stay container-env (Phase 3a)."
```

---

## Self-Review

**Spec coverage:**
- Honcho connection → settings: Task 1 `from_settings` + `_HONCHO_SETTINGS_MAP`. ✓
- Editable in admin: Task 2 endpoints + Task 3 section. ✓
- Reload applies it: Task 1 `_ensure_config` + `HonchoProvider.reload` resetting `_config_loaded`. ✓
- Behavior-neutral until set: per-field env fallback + no seed migration + `_ensure_config` skipped on injected client. ✓
- Out of scope: Honcho internal embedding/LLM (Phase 3a), Hindsight/Mem0 (Phase 3). ✓

**Placeholder scan:** none.

**Type consistency:** `HonchoConnectionResponse{enabled,base_url,workspace_id}` (Task 2) == TS `HonchoConnection` (Task 3). `HonchoConnectionUpdate` partial fields == the PUT body `Partial<HonchoConnection>`. `_HONCHO_SETTINGS_MAP` (honcho_memory) DB-keys == `_HONCHO_CONN_KEY` (settings_router) values == the keys the migration-free upsert writes. `_ensure_config` gate (`client is not None or _config_loaded`) matches the GraphitiProvider precedent.
