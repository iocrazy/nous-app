# Module Control Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single admin "Modules" page (new nav entry) that centrally controls every product module's Processing + Visibility switches, backed by an extensible registry.

**Architecture:** A backend registry (`services/modules/registry.py`) enumerates modules and owns one shared jsonb-safe reader/writer over `system_settings`. The two existing `module_config.py` files become thin shims delegating to it (signatures unchanged → all current call sites untouched). A single admin `GET/PUT /api/v1/admin/settings/modules` endpoint replaces the two scattered per-module endpoints. A new Arco admin page consumes it; the two old toggle UIs are removed. The user-facing app (Sidebar, router, public `/module-status` endpoints) is deliberately untouched.

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy Core (`app.db.engine`) / pytest (backend); React + TypeScript + Arco Design + React Query (admin frontend).

## Global Constraints

- **UI text in English** (labels, buttons). Chinese only allowed as secondary hint text, matching the existing bilingual pattern in `DistributionModule.tsx` (e.g. `Distribution — visibility（用户可见）`).
- **Immutability:** return new dicts/objects; never mutate inputs (frontend: spread; backend: build new payloads).
- **Fail modes are per-module, not global:** Topic Inspiration defaults ON / fail-open; Distribution defaults OFF / fail-closed. Encoded as per-module default values passed to the reader.
- **NEVER read env** for these switches — DB (`system_settings`) is the only source.
- **Shim signatures are frozen:** `is_module_enabled()`, `is_module_visible()`, and the module-level constant `MODULE_CONFIG_KEY` MUST keep their names/signatures in both `module_config.py` files.
- **Backend lint gate before push:** run `black`, `isort`, `flake8` on changed `.py` files (not just pytest).
- **DB read helper:** `await db_engine.fetch_val(sql, params)` returns `.scalar()` (`Any`); asyncpg may return a jsonb column as a JSON *string* — the reader must `json.loads` a str before parsing.
- **Audit log:** admin writes call `create_audit_log(admin_id=..., action=..., target_type="system_setting", target_id=<key>, details={"value": payload})` — same pattern as the existing module endpoints.

---

## File Structure

**New:**
- `backend/app/services/modules/__init__.py` — package marker.
- `backend/app/services/modules/registry.py` — `ModuleDef`, `ModuleState`, `MODULES`, lookups, shared `read_module_state` / `write_module_state` / `parse_module_state` / `list_module_summaries`.
- `backend/tests/services/modules/test_registry.py` — reader/parser unit tests.
- `backend/tests/api/admin/test_modules_endpoint.py` — admin endpoint integration tests.
- `admin/src/pages/settings/Modules.tsx` — the central Modules page.

**Modified:**
- `backend/app/services/topics/module_config.py` — becomes a shim over the registry.
- `backend/app/services/distribution/module_config.py` — becomes a shim over the registry.
- `backend/app/api/admin/settings_router.py` — add `/modules` GET+PUT; remove `/topics-module` and `/distribution-module`; fix imports.
- `admin/src/api/endpoints/settings.ts` — add `ModuleSummary` + `useModules` + `useUpdateModule`; remove the topic/distribution module interfaces, URLs, and hooks.
- `admin/src/App.tsx` — add the `/settings/modules` route.
- `admin/src/layouts/AdminLayout.tsx` — add the nav item + `allMenuKeys` entry.
- `admin/src/pages/settings/TopicScoring.tsx` — remove the module toggle cards + `patchModule` + the two topic-module hook usages/imports.
- `admin/src/pages/settings/index.tsx` — remove the `<DistributionModule />` render + import.

**Deleted:**
- `admin/src/pages/settings/DistributionModule.tsx`.

---

## Task 1: Module registry (backend core)

Build the registry + shared reader/parser/writer. This is the foundation everything else delegates to.

**Files:**
- Create: `backend/app/services/modules/__init__.py`
- Create: `backend/app/services/modules/registry.py`
- Test: `backend/tests/services/modules/test_registry.py`

**Interfaces:**
- Consumes: `app.db.engine` (`is_configured()`, `fetch_val(sql, params)`), `app.repositories.admin.system_settings_repository.get_system_settings_repository`.
- Produces:
  - `@dataclass(frozen=True) class ModuleDef` with fields `id: str`, `key: str`, `label: str`, `enabled_default: bool`, `visible_default: bool`.
  - `@dataclass(frozen=True) class ModuleState` with fields `enabled: bool`, `visible: bool`.
  - `MODULES: list[ModuleDef]`, `MODULES_BY_ID: dict[str, ModuleDef]`, `MODULES_BY_KEY: dict[str, ModuleDef]`.
  - `def parse_module_state(raw: Any, module: ModuleDef) -> ModuleState` — pure, jsonb-string-safe.
  - `async def read_module_state(module: ModuleDef) -> ModuleState`.
  - `async def read_state_for_key(key: str, enabled_default: bool, visible_default: bool) -> ModuleState` — thin helper the shims call.
  - `async def write_module_state(module: ModuleDef, enabled: bool, visible: bool, updated_by) -> ModuleState`.
  - `async def list_module_summaries() -> list[dict]` — `[{id, key, label, enabled, visible, enabled_default, visible_default}]`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/services/modules/test_registry.py`:

```python
import pytest

from app.services.modules import registry
from app.services.modules.registry import (
    MODULES,
    MODULES_BY_ID,
    MODULES_BY_KEY,
    ModuleState,
    parse_module_state,
)


def _mod(module_id):
    return MODULES_BY_ID[module_id]


def test_registry_lists_the_two_modules_with_correct_defaults():
    ids = {m.id for m in MODULES}
    assert ids == {"topic-inspiration", "distribution"}

    topic = MODULES_BY_ID["topic-inspiration"]
    assert topic.key == "topics.module"
    assert topic.enabled_default is True
    assert topic.visible_default is True  # fail-open

    dist = MODULES_BY_ID["distribution"]
    assert dist.key == "distribution.module"
    assert dist.enabled_default is False
    assert dist.visible_default is False  # fail-closed

    # by-key index is consistent with by-id
    assert MODULES_BY_KEY["topics.module"] is topic
    assert MODULES_BY_KEY["distribution.module"] is dist


def test_parse_dict_value_reads_both_fields():
    state = parse_module_state({"enabled": False, "visible": True}, _mod("topic-inspiration"))
    assert state == ModuleState(enabled=False, visible=True)


def test_parse_jsonb_string_value_is_decoded():
    # asyncpg can hand back the jsonb column as a JSON *string*
    state = parse_module_state('{"enabled": true, "visible": false}', _mod("distribution"))
    assert state == ModuleState(enabled=True, visible=False)


def test_parse_missing_key_uses_per_module_defaults():
    # None → topic fails OPEN, distribution fails CLOSED
    assert parse_module_state(None, _mod("topic-inspiration")) == ModuleState(True, True)
    assert parse_module_state(None, _mod("distribution")) == ModuleState(False, False)


def test_parse_partial_blob_uses_default_for_missing_field():
    # only `enabled` present → `visible` falls back to that module's default
    state = parse_module_state({"enabled": False}, _mod("topic-inspiration"))
    assert state == ModuleState(enabled=False, visible=True)


def test_parse_garbage_falls_back_to_defaults():
    assert parse_module_state("not json", _mod("distribution")) == ModuleState(False, False)
    assert parse_module_state(123, _mod("topic-inspiration")) == ModuleState(True, True)
    assert parse_module_state({"enabled": "yes"}, _mod("distribution")) == ModuleState(False, False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/services/modules/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.modules'`.

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/services/modules/__init__.py`:

```python
"""Central registry of product modules with processing/visibility switches."""
```

Create `backend/app/services/modules/registry.py`:

```python
"""Central registry of product modules and their processing/visibility switches.

Each module stores a ``{"enabled": bool, "visible": bool}`` blob under its own
``system_settings`` key. ``enabled`` is the PROCESSING/ACCESS switch (backend
behavior); ``visible`` is the DISPLAY switch (frontend nav + pages). Fail modes
are per-module: Topic Inspiration ships ON and fails OPEN; Distribution is opt-in
and fails CLOSED. This is the single source of truth — the per-feature
``module_config.py`` files are thin shims over it, and the admin ``/modules``
endpoint is driven by ``MODULES``. NEVER reads env.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from loguru import logger


@dataclass(frozen=True)
class ModuleDef:
    id: str
    key: str
    label: str
    enabled_default: bool
    visible_default: bool


@dataclass(frozen=True)
class ModuleState:
    enabled: bool
    visible: bool


MODULES: list[ModuleDef] = [
    ModuleDef(
        id="topic-inspiration",
        key="topics.module",
        label="Topic Inspiration",
        enabled_default=True,
        visible_default=True,
    ),
    ModuleDef(
        id="distribution",
        key="distribution.module",
        label="Distribution",
        enabled_default=False,
        visible_default=False,
    ),
]

MODULES_BY_ID: dict[str, ModuleDef] = {m.id: m for m in MODULES}
MODULES_BY_KEY: dict[str, ModuleDef] = {m.key: m for m in MODULES}


def _coerce_dict(raw: Any) -> Any:
    """The jsonb ``value`` column can come back as a JSON string rather than an
    already-decoded dict. Decode it so the bool parsers see a real dict; anything
    undecodable falls through to the per-module defaults."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return None
    return raw


def _parse_bool_field(raw: Any, field: str, default: bool) -> bool:
    if isinstance(raw, dict):
        v = raw.get(field)
        if isinstance(v, bool):
            return v
    return default


def parse_module_state(raw: Any, module: ModuleDef) -> ModuleState:
    """Pure parse of a stored blob into a ModuleState, jsonb-string-safe.
    Missing/garbage fields fall back to this module's own defaults."""
    data = _coerce_dict(raw)
    return ModuleState(
        enabled=_parse_bool_field(data, "enabled", module.enabled_default),
        visible=_parse_bool_field(data, "visible", module.visible_default),
    )


async def _read_raw(key: str) -> Any:
    """Service-role engine read of the config blob; never raises — any failure
    returns None so the parser falls back to the module's defaults."""
    try:
        from app.db import engine as db_engine

        if not db_engine.is_configured():
            return None
        return await db_engine.fetch_val(
            "SELECT value FROM public.system_settings WHERE key = :k",
            {"k": key},
        )
    except Exception:  # noqa: BLE001
        logger.warning("[modules] config read failed for {} — using defaults", key)
        return None


async def read_module_state(module: ModuleDef) -> ModuleState:
    return parse_module_state(await _read_raw(module.key), module)


async def read_state_for_key(
    key: str, enabled_default: bool, visible_default: bool
) -> ModuleState:
    """Reader for the thin shims that only know their key + defaults."""
    module = MODULES_BY_KEY.get(key) or ModuleDef(
        id=key, key=key, label=key,
        enabled_default=enabled_default, visible_default=visible_default,
    )
    return await read_module_state(module)


async def write_module_state(
    module: ModuleDef, enabled: bool, visible: bool, updated_by
) -> ModuleState:
    from app.repositories.admin.system_settings_repository import (
        get_system_settings_repository,
    )

    payload = {"enabled": bool(enabled), "visible": bool(visible)}
    repo = get_system_settings_repository()
    await repo.upsert_setting(module.key, payload, updated_by)
    return ModuleState(enabled=payload["enabled"], visible=payload["visible"])


async def list_module_summaries() -> list[dict]:
    summaries: list[dict] = []
    for module in MODULES:
        state = await read_module_state(module)
        summaries.append(
            {
                "id": module.id,
                "key": module.key,
                "label": module.label,
                "enabled": state.enabled,
                "visible": state.visible,
                "enabled_default": module.enabled_default,
                "visible_default": module.visible_default,
            }
        )
    return summaries
```

Also create `backend/tests/services/modules/__init__.py` if the test dir needs a package marker (check whether `backend/tests/services/` uses `__init__.py` — mirror the sibling dirs).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/services/modules/test_registry.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Lint + commit**

```bash
cd backend && uv run black app/services/modules/ tests/services/modules/ && uv run isort app/services/modules/ tests/services/modules/ && uv run flake8 app/services/modules/ tests/services/modules/
git add backend/app/services/modules/ backend/tests/services/modules/
git commit -m "feat(modules): central module registry with jsonb-safe reader"
```

---

## Task 2: Convert `module_config.py` files to shims

Delegate both per-feature configs to the registry (fixing the topic jsonb-string latent bug for free). Signatures stay identical so all call sites are untouched.

**Files:**
- Modify: `backend/app/services/topics/module_config.py`
- Modify: `backend/app/services/distribution/module_config.py`
- Test: `backend/tests/services/modules/test_shim_delegation.py` (create)

**Interfaces:**
- Consumes: `app.services.modules.registry.read_state_for_key`, `MODULES_BY_KEY`.
- Produces (unchanged public surface): `topics.module_config.{MODULE_CONFIG_KEY, is_module_enabled, is_module_visible}` and `distribution.module_config.{MODULE_CONFIG_KEY, is_module_enabled, is_module_visible}`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/services/modules/test_shim_delegation.py`:

```python
import pytest

from app.services.distribution import module_config as dist_cfg
from app.services.topics import module_config as topic_cfg
from app.services.modules.registry import ModuleState


@pytest.mark.asyncio
async def test_topic_shim_delegates_to_registry(monkeypatch):
    async def fake_read(key, enabled_default, visible_default):
        assert key == "topics.module"
        assert (enabled_default, visible_default) == (True, True)  # fail-open
        return ModuleState(enabled=False, visible=True)

    monkeypatch.setattr(
        "app.services.topics.module_config.read_state_for_key", fake_read
    )
    assert await topic_cfg.is_module_enabled() is False
    assert await topic_cfg.is_module_visible() is True
    assert topic_cfg.MODULE_CONFIG_KEY == "topics.module"


@pytest.mark.asyncio
async def test_distribution_shim_delegates_to_registry(monkeypatch):
    async def fake_read(key, enabled_default, visible_default):
        assert key == "distribution.module"
        assert (enabled_default, visible_default) == (False, False)  # fail-closed
        return ModuleState(enabled=True, visible=False)

    monkeypatch.setattr(
        "app.services.distribution.module_config.read_state_for_key", fake_read
    )
    assert await dist_cfg.is_module_enabled() is True
    assert await dist_cfg.is_module_visible() is False
    assert dist_cfg.MODULE_CONFIG_KEY == "distribution.module"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/services/modules/test_shim_delegation.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'read_state_for_key'` (the shim doesn't import it yet).

- [ ] **Step 3: Write minimal implementation**

Replace the body of `backend/app/services/topics/module_config.py` with:

```python
"""Topic Inspiration module switches (processing + visibility).

Thin shim over ``app.services.modules.registry`` — the single source of truth.
Two independent global switches stored together in
``system_settings['topics.module']``:

- ``enabled`` — the PROCESSING switch. When off the scheduled tick does nothing.
- ``visible`` — the DISPLAY switch. Controls the frontend nav entry + page.

Both default ON and fail OPEN (missing/garbage/read-error → on). Admin-tunable,
instant, no redeploy. NEVER reads env.
"""

from __future__ import annotations

from app.services.modules.registry import read_state_for_key

MODULE_CONFIG_KEY = "topics.module"
DEFAULT_MODULE_ENABLED = True
DEFAULT_MODULE_VISIBLE = True


async def is_module_enabled() -> bool:
    """Whether the Topic Inspiration pipeline is enabled (processing switch)."""
    state = await read_state_for_key(
        MODULE_CONFIG_KEY, DEFAULT_MODULE_ENABLED, DEFAULT_MODULE_VISIBLE
    )
    return state.enabled


async def is_module_visible() -> bool:
    """Whether the Topic Inspiration surface is shown (display switch)."""
    state = await read_state_for_key(
        MODULE_CONFIG_KEY, DEFAULT_MODULE_ENABLED, DEFAULT_MODULE_VISIBLE
    )
    return state.visible
```

Replace the body of `backend/app/services/distribution/module_config.py` with:

```python
"""Distribution module switches (access + visibility).

Thin shim over ``app.services.modules.registry`` — the single source of truth.
Two independent global switches stored together in
``system_settings['distribution.module']``:

- ``enabled`` — the ACCESS switch. Gates the backend account/OAuth API
  (``require_distribution`` 404s every endpoint when off).
- ``visible`` — the DISPLAY switch. Controls the frontend nav entry + routes.

Opt-in: both default OFF and fail CLOSED so a not-yet-launched module never
exposes its surface or API until an admin turns it on. Admin-tunable, instant,
no redeploy. NEVER reads env.
"""

from __future__ import annotations

from app.services.modules.registry import read_state_for_key

MODULE_CONFIG_KEY = "distribution.module"
DEFAULT_MODULE_ENABLED = False
DEFAULT_MODULE_VISIBLE = False


async def is_module_enabled() -> bool:
    """Whether the Distribution API is reachable (access switch)."""
    state = await read_state_for_key(
        MODULE_CONFIG_KEY, DEFAULT_MODULE_ENABLED, DEFAULT_MODULE_VISIBLE
    )
    return state.enabled


async def is_module_visible() -> bool:
    """Whether the Distribution surface is shown in the frontend (display switch)."""
    state = await read_state_for_key(
        MODULE_CONFIG_KEY, DEFAULT_MODULE_ENABLED, DEFAULT_MODULE_VISIBLE
    )
    return state.visible
```

Note: this drops `parse_module_enabled` / `parse_module_visible` / `_read_raw` from both files. Task 3 removes their only remaining importers (the old admin endpoints), so this is safe — but do Task 3 in the SAME commit stream and run the full suite before pushing.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/services/modules/ -v`
Expected: PASS (all registry + shim tests).

- [ ] **Step 5: Commit** (do NOT push yet — settings_router still imports the removed `parse_*`; Task 3 fixes it)

```bash
cd backend && uv run black app/services/topics/module_config.py app/services/distribution/module_config.py tests/services/modules/test_shim_delegation.py && uv run isort app/services/topics/module_config.py app/services/distribution/module_config.py tests/services/modules/test_shim_delegation.py
git add backend/app/services/topics/module_config.py backend/app/services/distribution/module_config.py backend/tests/services/modules/test_shim_delegation.py
git commit -m "refactor(modules): topic + distribution module_config become registry shims"
```

---

## Task 3: Unified `/modules` admin endpoint (replaces the two old ones)

**Files:**
- Modify: `backend/app/api/admin/settings_router.py`
- Test: `backend/tests/api/admin/test_modules_endpoint.py` (create)

**Interfaces:**
- Consumes: `app.services.modules.registry.{MODULES_BY_ID, list_module_summaries, write_module_state}`, `app.core.admin_deps.AdminAuthDep`, `create_audit_log`.
- Produces: `GET /api/v1/admin/settings/modules` → `list[ModuleSummaryResponse]`; `PUT /api/v1/admin/settings/modules/{module_id}` (body `ModuleSwitchUpdate{enabled, visible}`) → `ModuleSummaryResponse`.

- [ ] **Step 1: Write the failing test**

First find the existing admin-router test style (auth fixture, TestClient) to mirror it:

Run: `cd backend && ls tests/api/admin/ && grep -rln "settings" tests/api/admin/ | head`

Create `backend/tests/api/admin/test_modules_endpoint.py`, mirroring the auth/client fixtures used by the sibling settings tests you just found. The behavioral assertions (adapt the fixture wiring to match the sibling file):

```python
def test_get_modules_lists_both(admin_client):
    resp = admin_client.get("/api/v1/admin/settings/modules")
    assert resp.status_code == 200
    body = resp.json()
    ids = {m["id"] for m in body}
    assert ids == {"topic-inspiration", "distribution"}
    topic = next(m for m in body if m["id"] == "topic-inspiration")
    assert set(topic) == {
        "id", "key", "label", "enabled", "visible",
        "enabled_default", "visible_default",
    }
    assert topic["enabled_default"] is True
    dist = next(m for m in body if m["id"] == "distribution")
    assert dist["enabled_default"] is False


def test_put_module_persists_and_returns_summary(admin_client):
    resp = admin_client.put(
        "/api/v1/admin/settings/modules/distribution",
        json={"enabled": True, "visible": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "distribution"
    assert body["enabled"] is True and body["visible"] is True

    # read-back reflects the write
    got = admin_client.get("/api/v1/admin/settings/modules").json()
    dist = next(m for m in got if m["id"] == "distribution")
    assert dist["enabled"] is True and dist["visible"] is True


def test_put_unknown_module_id_404(admin_client):
    resp = admin_client.put(
        "/api/v1/admin/settings/modules/does-not-exist",
        json={"enabled": True, "visible": True},
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/api/admin/test_modules_endpoint.py -v`
Expected: FAIL — 404 on GET `/modules` (route doesn't exist yet).

- [ ] **Step 3: Write minimal implementation**

3a. Add the response/request schemas. In `backend/app/schemas/admin.py`, add near the existing module schemas:

```python
class ModuleSummaryResponse(BaseModel):
    """One product module's central switch state + its defaults."""

    id: str
    key: str
    label: str
    enabled: bool
    visible: bool
    enabled_default: bool
    visible_default: bool


class ModuleSwitchUpdate(BaseModel):
    """Both switches for one module (whole-blob replace)."""

    enabled: bool
    visible: bool
```

3b. In `backend/app/api/admin/settings_router.py`:

Remove the two old endpoints — delete `get_topics_module_config` + `update_topics_module_config` (lines ~203-234) and `get_distribution_module_config` + `update_distribution_module_config` (lines ~237-271).

Fix imports:
- Delete `from app.services.distribution import module_config as dist_module_config` (line 57) — no longer used after the endpoint removal (verify no other reference remains in the file).
- Change the `from app.services.topics.module_config import (...)` block (lines 70-76) to import only what still remains referenced in the file (grep the file for `is_module_enabled` / `is_module_visible` / `MODULE_CONFIG_KEY` / `parse_module_*`; if none remain, delete the whole import block).
- Remove `TopicModuleConfigResponse` and `DistributionModuleConfigResponse` from the `app.schemas.admin` import list (lines 19-49) — **only after** grepping the file to confirm they have no other use.
- Add to the `app.schemas.admin` import list: `ModuleSummaryResponse`, `ModuleSwitchUpdate`.
- Add: `from app.services.modules.registry import (MODULES_BY_ID, list_module_summaries, write_module_state,)`.

Add the new endpoints (place them where the old `/topics-module` block was):

```python
@router.get("/modules", response_model=list[ModuleSummaryResponse])
async def get_modules(auth: AdminAuthDep):
    """Every product module's central switches (processing + visibility) plus
    each module's default fail-mode. Driven by the module registry."""
    summaries = await list_module_summaries()
    return [ModuleSummaryResponse(**s) for s in summaries]


@router.put("/modules/{module_id}", response_model=ModuleSummaryResponse)
async def update_module(
    module_id: str,
    body: ModuleSwitchUpdate,
    auth: AdminAuthDep,
):
    """Persist both switches for one module (whole-blob replace). ``enabled`` =
    processing/access; ``visible`` = frontend nav + pages. Instant, no redeploy."""
    module = MODULES_BY_ID.get(module_id)
    if module is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown module: {module_id}",
        )
    state = await write_module_state(
        module, body.enabled, body.visible, auth.user_id
    )
    await create_audit_log(
        admin_id=auth.user_id,
        action="update_module_switches",
        target_type="system_setting",
        target_id=module.key,
        details={"module_id": module_id, "value": {
            "enabled": state.enabled, "visible": state.visible,
        }},
    )
    return ModuleSummaryResponse(
        id=module.id,
        key=module.key,
        label=module.label,
        enabled=state.enabled,
        visible=state.visible,
        enabled_default=module.enabled_default,
        visible_default=module.visible_default,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/api/admin/test_modules_endpoint.py tests/services/modules/ -v
```
Expected: PASS. Then confirm nothing else broke from the import removals:
```bash
cd backend && uv run pytest -q
```
Expected: PASS (no import errors from the removed `parse_*` / old endpoints).

- [ ] **Step 5: Lint + commit + push**

```bash
cd backend && uv run black app/api/admin/settings_router.py app/schemas/admin.py tests/api/admin/test_modules_endpoint.py && uv run isort app/api/admin/settings_router.py app/schemas/admin.py tests/api/admin/test_modules_endpoint.py && uv run flake8 app/api/admin/settings_router.py app/schemas/admin.py tests/api/admin/test_modules_endpoint.py
git add backend/app/api/admin/settings_router.py backend/app/schemas/admin.py backend/tests/api/admin/test_modules_endpoint.py
git commit -m "feat(admin): unified /modules endpoint, remove per-module toggle endpoints"
```

---

## Task 4: Admin API hooks (frontend data layer)

**Files:**
- Modify: `admin/src/api/endpoints/settings.ts`

**Interfaces:**
- Consumes: `/api/v1/admin/settings/modules` (GET), `/api/v1/admin/settings/modules/{id}` (PUT).
- Produces: `interface ModuleSummary`, `useModules()`, `useUpdateModule()`.

- [ ] **Step 1: Add the new interface + hooks**

Add to `admin/src/api/endpoints/settings.ts` (near the removed module hooks section):

```typescript
// ── Central module switches (processing + visibility) ─────────────────────
export interface ModuleSummary {
  id: string
  key: string
  label: string
  /** Processing/access switch. */
  enabled: boolean
  /** Display switch — off hides the frontend nav entry + pages. */
  visible: boolean
  enabled_default: boolean
  visible_default: boolean
}

const MODULES_URL = '/api/v1/admin/settings/modules'

export function useModules() {
  return useQuery({
    queryKey: ['settings', 'modules'],
    queryFn: async () => {
      const { data } = await apiClient.get<ModuleSummary[]>(MODULES_URL)
      return data
    },
  })
}

export function useUpdateModule() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (vars: { id: string; enabled: boolean; visible: boolean }) => {
      const { data } = await apiClient.put<ModuleSummary>(
        `${MODULES_URL}/${vars.id}`,
        { enabled: vars.enabled, visible: vars.visible },
      )
      return data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings', 'modules'] })
    },
  })
}
```

- [ ] **Step 2: Remove the dead per-module hooks**

Delete from the same file:
- The `TopicModuleConfig` interface, `TOPIC_MODULE_URL`, `useTopicModuleConfig`, `useUpdateTopicModuleConfig` block (settings.ts ~lines 293-324).
- The `DistributionModuleConfig` interface, `DISTRIBUTION_MODULE_URL`, `useDistributionModuleConfig`, `useUpdateDistributionModuleConfig` block (settings.ts ~lines 326-360).

(Do NOT remove them until Tasks 5 & 6 stop importing them — but since this is one PR, the compiler check in Step 3 will confirm; if you prefer, run Tasks 5+6 edits before this deletion. Order within the commit doesn't matter as long as the final tree compiles.)

- [ ] **Step 3: Type-check**

Run: `cd admin && npx tsc --noEmit`
Expected: errors ONLY in `TopicScoring.tsx` / `DistributionModule.tsx` / `settings/index.tsx` (they still import the deleted hooks). Those are fixed in Tasks 5-6. If any OTHER file errors, a consumer was missed — grep for it and reconcile.

- [ ] **Step 4: Commit** (bundle with Tasks 5-6 so the tree compiles; see Task 6 Step 5)

---

## Task 5: New Modules admin page + route + nav

**Files:**
- Create: `admin/src/pages/settings/Modules.tsx`
- Modify: `admin/src/App.tsx`
- Modify: `admin/src/layouts/AdminLayout.tsx`

**Interfaces:**
- Consumes: `useModules`, `useUpdateModule`, `ModuleSummary` from `../../api/endpoints/settings`; Arco `Card`, `Switch`, `Message`, `Tag`, `Spin`; `SectionHeader`.
- Produces: `export function Modules()`.

- [ ] **Step 1: Create the page**

Create `admin/src/pages/settings/Modules.tsx`:

```typescript
import { Card, Switch, Message, Tag, Spin } from '@arco-design/web-react'
import { IconThunderbolt } from '@arco-design/web-react/icon'
import {
  useModules,
  useUpdateModule,
  type ModuleSummary,
} from '../../api/endpoints/settings'
import { SectionHeader } from './SectionHeader'

/**
 * Module Control Center — one card per product module, each with a Processing
 * switch (backend behavior) and a Visibility switch (frontend nav + pages).
 * Registry-driven: adding a module server-side makes a card appear here with no
 * frontend change. Opt-in (default-off) modules are badged.
 */
export function Modules() {
  const { data: modules, isLoading } = useModules()
  const update = useUpdateModule()

  const patch = (
    m: ModuleSummary,
    partial: Partial<Pick<ModuleSummary, 'enabled' | 'visible'>>,
    okMsg: string,
  ) => {
    update.mutate(
      { id: m.id, enabled: m.enabled, visible: m.visible, ...partial },
      {
        onSuccess: () => Message.success(okMsg),
        onError: () => Message.error('Update failed'),
      },
    )
  }

  if (isLoading || !modules) {
    return (
      <div style={{ padding: 48, textAlign: 'center' }}>
        <Spin />
      </div>
    )
  }

  return (
    <div>
      {modules.map((m) => (
        <Card key={m.id} style={{ marginBottom: 20 }}>
          <SectionHeader
            icon={<IconThunderbolt />}
            title={
              <span>
                {m.label}
                {!m.enabled_default && (
                  <Tag color="orange" size="small" style={{ marginLeft: 8 }}>
                    Opt-in · default off
                  </Tag>
                )}
              </span>
            }
            subtitle={`Processing + visibility switches for the ${m.label} module. Instant, no redeploy.`}
          />
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 4 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, fontSize: 15 }}>
                {m.label} — Processing（后台处理）
              </div>
              <div style={{ fontSize: 12, color: 'var(--color-text-3)', lineHeight: 1.6 }}>
                Backend behavior switch. Off pauses the module's processing / API.
              </div>
            </div>
            <Switch
              checked={m.enabled}
              loading={update.isPending}
              onChange={(v: boolean) =>
                patch(m, { enabled: v }, v ? `${m.label} processing enabled.` : `${m.label} processing paused.`)
              }
            />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 16 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, fontSize: 15 }}>
                {m.label} — Visibility（用户可见）
              </div>
              <div style={{ fontSize: 12, color: 'var(--color-text-3)', lineHeight: 1.6 }}>
                Frontend display switch. Off hides the nav entry + pages for all users.
              </div>
            </div>
            <Switch
              checked={m.visible}
              loading={update.isPending}
              onChange={(v: boolean) =>
                patch(m, { visible: v }, v ? `${m.label} visible to users.` : `${m.label} hidden from users.`)
              }
            />
          </div>
        </Card>
      ))}
    </div>
  )
}
```

Note: `SectionHeader`'s `title` prop must accept a `ReactNode` (it renders a badge). Verify by reading `admin/src/pages/settings/SectionHeader.tsx`; if it types `title: string`, widen it to `React.ReactNode` in that file (small, safe change) OR drop the inline `<Tag>` and render the badge as a separate element below the title.

- [ ] **Step 2: Register the route**

In `admin/src/App.tsx`, add the import near the other settings-page imports (after line 17):

```typescript
import { Modules } from './pages/settings/Modules'
```

Add the route inside the settings routes block (after the `/settings` route, line 69):

```typescript
<Route path="/settings/modules" element={<Modules />} />
```

- [ ] **Step 3: Add the nav item**

In `admin/src/layouts/AdminLayout.tsx`:

Add `/settings/modules` to the `allMenuKeys` array (line 44), right before `'/settings'`:

```typescript
  '/api-keys', '/settings/ai-governance', '/settings/topic-scoring', '/settings/signal-sources', '/settings/memory', '/settings/modules', '/settings',
```

Add the menu item in the **System** `MenuItemGroup`, right after the `/settings` item (line 124):

```tsx
            <MenuItem key="/settings/modules"><IconThunderbolt />Modules</MenuItem>
```

(`IconThunderbolt` is already imported in this file — it's used by Topic Scoring / Transcode items. Confirm; if not, add it to the icon import.)

- [ ] **Step 4: Type-check**

Run: `cd admin && npx tsc --noEmit`
Expected: errors only remain in `TopicScoring.tsx` + `DistributionModule.tsx` + `settings/index.tsx` (fixed in Task 6).

- [ ] **Step 5: Commit** (bundle with Task 6)

---

## Task 6: Remove the two scattered toggle UIs

**Files:**
- Modify: `admin/src/pages/settings/TopicScoring.tsx`
- Modify: `admin/src/pages/settings/index.tsx`
- Delete: `admin/src/pages/settings/DistributionModule.tsx`

**Interfaces:**
- Consumes: nothing new.
- Produces: `TopicScoring` no longer renders module toggles; generic Settings page no longer renders `<DistributionModule />`.

- [ ] **Step 1: Strip the module toggles from `TopicScoring.tsx`**

- Remove `useTopicModuleConfig`, `useUpdateTopicModuleConfig`, `type TopicModuleConfig` from the import block (lines 21-26).
- Remove the `const { data: modData } = useTopicModuleConfig()` + `const updateMod = useUpdateTopicModuleConfig()` lines (60-61).
- Remove the entire `patchModule` function (lines 63-76).
- Remove the two-switch module Card JSX (the block rendering "Topic Inspiration — processing（内容处理）" / "— visibility（用户可见）"). Read the file to find its exact bounds and delete the whole `<Card>...</Card>` for the module toggles, keeping the scoring / prefilter / content-fetch cards intact.
- If `Switch` / `Input` imports become unused after removal, drop them from the Arco import (line 2-11) to satisfy lint.

- [ ] **Step 2: Remove `<DistributionModule />` from the generic Settings page**

In `admin/src/pages/settings/index.tsx`:
- Remove the import `import { DistributionModule } from './DistributionModule'` (line 15).
- Remove the `<DistributionModule />` render (line 176).

- [ ] **Step 3: Delete the dead component + its hooks**

```bash
git rm admin/src/pages/settings/DistributionModule.tsx
```

Then in `admin/src/api/endpoints/settings.ts`, delete the now-unused `TopicModuleConfig` / `DistributionModuleConfig` interfaces + their URLs + their four hooks (if not already removed in Task 4).

- [ ] **Step 4: Type-check + build**

```bash
cd admin && npx tsc --noEmit && npm run build
```
Expected: clean — no references to deleted hooks/components anywhere.

- [ ] **Step 5: Commit + push (Tasks 4-6 together)**

```bash
git add admin/src/api/endpoints/settings.ts admin/src/pages/settings/Modules.tsx admin/src/App.tsx admin/src/layouts/AdminLayout.tsx admin/src/pages/settings/TopicScoring.tsx admin/src/pages/settings/index.tsx
git rm admin/src/pages/settings/DistributionModule.tsx
git commit -m "feat(admin): central Modules page; remove scattered per-module toggles"
git push
```

---

## Task 7: End-to-end verification

**Files:** none (verification only).

- [ ] **Step 1: Backend regression sweep**

```bash
cd backend && uv run pytest -q
```
Expected: PASS. Confirm the public status endpoints still behave: the shims feed `GET /topics/module-status`, `GET /distribution/module-status`, `require_distribution()`, and `workflows/topic_inspiration.py` — all import only `is_module_enabled` / `is_module_visible`, which are unchanged.

- [ ] **Step 2: Manual admin UI check (per feedback_ui_early_visual_ux_pass)**

Run the admin app (`cd admin && npm run dev`) against a dev backend, or use a Vercel/preview build. Verify:
- **System → Modules** nav item appears and routes to the page.
- Two cards render: **Topic Inspiration** (no badge) and **Distribution** (Opt-in badge).
- Toggling Distribution Visibility on → success toast; reload → state persists.
- **Topic Scoring** page no longer shows the two module toggles (only scoring config).
- The generic **Settings** page no longer shows the Distribution card.

- [ ] **Step 3: Confirm user-facing app unaffected**

The user frontend (`frontend/`) was not touched. Sanity-check that the Distribution nav still gates on `visible` and Topic Inspiration still gates on `visible` (they read the unchanged public `/module-status` endpoints). No action needed unless a regression is observed.

- [ ] **Step 4: Verify with `/verify` skill (optional but recommended)**

Drive the admin Modules page end-to-end and observe an actual toggle round-trip (PUT → DB → GET reflects it) rather than trusting the unit tests alone.

---

## Notes for the implementer

- **Fail-mode is per-module** — never collapse `enabled_default`/`visible_default` into a single global constant. Topic = (True, True), Distribution = (False, False).
- **Do not touch `frontend/`** — Sidebar, router, and the user-facing module-status services/hooks are intentionally out of scope (Method C, backlogged).
- **`distribution.douyin`** (credentials) is a different key entirely — do not touch it.
- **Commit discipline:** Tasks 1-3 are backend and must land together-compilable (Task 3 removes the last importers of the parse helpers Task 2 dropped). Tasks 4-6 are frontend and must land together-compilable (the final tree has no references to deleted hooks/components). Run `uv run pytest -q` before the backend push and `npx tsc --noEmit && npm run build` before the frontend push.
- Follow `feedback_backend_lint_gate_before_push`: run black + isort + flake8 on changed `.py` before pushing, not just pytest.
