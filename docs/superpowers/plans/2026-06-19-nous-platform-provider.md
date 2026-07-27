# Nous Platform Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface admin-configured platform AI models (`nous_models`) on the user side as a single "Nous" provider — selectable as an agent's LLM model and as a transcription (ASR) option — running on the platform key, gated by a default-off global + per-module admin master switch.

**Architecture:** Reclassify `nous_models` by model TYPE (`llm|embedding|tts|asr`) instead of feature category. A shared resolver step (`resolve_nous_model`) runs in both branches of `resolve_task_provider_config` and in the transcription `load_transcribe_inputs` step: it looks a resolved model name up across all `nous_models` rows and, when found, returns the platform provider config (fail-closed on disabled/gated/missing). No new tables; reuse the existing agent + `task_assignment` + governance machinery. Admin master control adds a global `nous.user_enabled` (default false) and per-module `ai_module.<m>.nous_allowed` (default true) to `system_settings`.

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy 2.0 ORM (asyncpg) / DBOS workflows / Pydantic v2 / pytest. React 19 + TypeScript + Vite (`frontend/`). Arco Design + React Query (`admin/`). Backend lint = **black + isort + flake8** (NOT ruff); loguru uses f-strings (no `%s`).

**Branch:** `feature/nous-platform-provider` · **Worktree:** `/Volumes/program/project-code/repos/nous/.worktrees/feature-orm-2-migration` (current).

**Run backend tests:** `cd backend && uv run pytest tests/<file> -v` · **Lint:** `cd backend && uv run black <files> && uv run isort <files> && uv run flake8 <files>`
**Frontend typecheck/build:** `cd frontend && npx tsc --noEmit && npm run build` · **Admin:** `cd admin && npx tsc --noEmit && npm run build`

---

## Key Decisions Locked (from the design spec)

1. `category` column → **renamed `type`**; CHECK swapped to `('llm','embedding','tts','asr')`; nullable `description text` added. Prod table is **empty** (0 rows) so there is no real remap; the migration still includes a defensive legacy→type remap so it is safe to apply on any environment (dev) that happens to hold rows.
2. v1 consumer wiring: **`llm → agents`** (agent model picker), **`asr → transcription`** (transcription dropdown). `embedding`/`tts` are admin-configurable but have no user consumer in v1.
3. Resolution is **full-table by name**: found+enabled+gated-on → platform config; found+disabled OR gate-closed → **fail-closed** (RuntimeError); not found → unchanged BYOK path.
4. Transcription stores the nous choice as **`nous:<name>`** in `task_assignment.transcription` (mirrors the existing `provider:model` shape).
5. Admin master control: **global `nous.user_enabled` (default `false`)** + per-module **`ai_module.<m>.nous_allowed` (default `true` once global is on)**. Enforced in the resolver (fail-closed) AND hidden in the UI.
6. The agent prompt is **preserved**: when an agent's model is a nous model, the resolver still returns the user's resolved agent slug so the caller composes that agent's prompt with the platform model.
7. **No new adapter factory work**: the resolver returns the existing `(provider_key, provider_config, model)` shape, so existing callers (`AIProviderFactory`, `WhisperService`, the `volcengine` ASR branch) already construct adapters from it. A nous ASR model whose `actual_provider='volcengine'` automatically routes through `_run_volcengine_asr`.

---

## File Structure

**Backend — create**
- `supabase/migrations/302_nous_models_type_and_description.sql` — column rename + CHECK swap + description + index.
- `backend/tests/test_nous_resolver.py` — shared resolver unit tests.
- `backend/tests/test_nous_governance.py` — nous global + per-module gate unit tests.

**Backend — modify**
- `backend/app/models/ai.py:424-468` — `NousModels`: `category`→`type` column + CheckConstraint, add `description`.
- `backend/app/schemas/nous.py` — `type` enum (4 places) + `description`.
- `backend/app/repositories/nous_repository.py` + `nous_repository_orm.py` — `category`→`type` (param, select, filter, `_PUBLIC_COLS`).
- `backend/app/api/admin/nous_router.py:30-48` — `_to_response`: `type` + `description`.
- `backend/app/services/ai/governance/ai_governance.py` — add `is_nous_globally_enabled` + `is_nous_allowed`.
- `backend/app/services/ai/providers/ai_provider_helpers.py` — add `resolve_nous_model`, wire into both branches of `resolve_task_provider_config`.
- `backend/app/workflows/ai_transcription.py:97-122` — `nous:` branch in `load_transcribe_inputs`.
- `backend/app/api/ai_settings_router.py:184-215` — `GET /mediahub-models` `?type=` + `GET /governance` nous extension.
- `backend/app/schemas/admin.py:273-327` — governance schemas: `nous_allowed` per module + global `nous_user_enabled`.
- `backend/app/api/admin/settings_router.py:195-319` — read/write nous governance keys.
- `backend/tests/test_ai_governance_user_endpoint.py:106-146` — accommodate new nous keys.

**Frontend — modify**
- `frontend/types.ts:568-585` — `NousModelPublic` (`type`+`description`), `AIGovernanceFlags` (nous fields).
- `frontend/services/aiService.ts:439-510` — `getNousModels(type?)`, governance defaults.
- `frontend/components/AISettings.tsx` — transcription options (`type==='asr'`, `nous:` value) + Nous provider card.
- `frontend/components/AILibrary/AgentEditor.tsx` — nous `llm` model group.

**Admin — modify**
- `admin/src/pages/ai/index.tsx` — `TYPE_OPTIONS`, `type` field, `description` field + column.
- `admin/src/pages/settings/AIGovernance.tsx` — global + per-module nous toggles.
- `admin/src/api/endpoints/settings.ts:87-113` — nous types in governance read/write.

---

# Phase 1 — Backend data layer (type classification)

### Task 1: Migration 302 — `category` → `type`, CHECK swap, `description`

**Files:**
- Create: `supabase/migrations/302_nous_models_type_and_description.sql`

- [ ] **Step 1: Write the migration**

```sql
-- 302 — Nous models: classify by model TYPE, not feature category.
--
-- The user-side "Nous provider" feature selects platform models by their
-- intrinsic model TYPE (one LLM serves many features), not by a feature
-- category. Rename category → type, swap the CHECK constraint to the type
-- enum, and add an admin usage-note column.
--
-- Prod `nous_models` is EMPTY (0 rows, verified 2026-06-19) so there is no
-- real remap. The defensive UPDATE below is a no-op on prod and only matters
-- if a non-prod env (dev) holds legacy rows — it keeps the ADD CONSTRAINT
-- from failing there. Apply order: drop old constraint → rename → remap →
-- add new constraint → add column → reindex.

ALTER TABLE public.nous_models DROP CONSTRAINT IF EXISTS nous_models_category_check;

ALTER TABLE public.nous_models RENAME COLUMN category TO type;

-- Defensive legacy→type remap (no-op on empty prod):
UPDATE public.nous_models SET type = 'asr' WHERE type = 'transcription';
UPDATE public.nous_models SET type = 'llm' WHERE type IN ('summarization', 'analysis');

ALTER TABLE public.nous_models
  ADD CONSTRAINT nous_models_type_check
  CHECK (type IN ('llm', 'embedding', 'tts', 'asr'));

ALTER TABLE public.nous_models ADD COLUMN IF NOT EXISTS description TEXT;

DROP INDEX IF EXISTS idx_nous_models_category;
CREATE INDEX IF NOT EXISTS idx_nous_models_type ON public.nous_models(type, is_enabled);
```

- [ ] **Step 2: Dry-run verify against dev DB (BEGIN/ROLLBACK)**

Confirm the migration parses and applies on a real Postgres. Use the dev DB (NAS `mediahub-sb-dev`, see MEMORY — DSN pulled internally, never committed). Wrap in a transaction and roll back:

```bash
# Pseudo — actual DSN assembled from NAS env, not stored here:
psql "$DEV_DSN" -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
\i supabase/migrations/302_nous_models_type_and_description.sql
SELECT column_name FROM information_schema.columns
  WHERE table_name='nous_models' AND column_name IN ('type','description');
ROLLBACK;
SQL
```
Expected: both `type` and `description` rows returned; no errors.

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/302_nous_models_type_and_description.sql
git commit -m "feat(nous): migration 302 — classify nous_models by type + description"
```

> NOTE for the controller: the migration is **applied** by merging to master (triggers `run-migration.yml`). After merge, confirm that workflow reaches `success` (migration CI green ≠ applied — see MEMORY `bug_alter_publication_drop_if_exists`). Also `NOTIFY pgrst, 'reload schema'` is handled by the runner; the `description` column must be visible to PostgREST before the admin REST path reads it.

---

### Task 2: ORM model — `NousModels.type` + `description`

**Files:**
- Modify: `backend/app/models/ai.py:424-468`
- Test: `backend/tests/test_nous_model_orm.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_nous_model_orm.py
"""NousModels ORM model reflects migration 302 (type column + description)."""

from app.models import NousModels


def test_nous_models_has_type_column_not_category():
    attrs = {p.key for p in NousModels.__mapper__.column_attrs}
    assert "type" in attrs
    assert "category" not in attrs
    assert "description" in attrs


def test_nous_models_type_check_constraint_uses_type_enum():
    constraints = {c.name: c for c in NousModels.__table__.constraints if c.name}
    cc = constraints["nous_models_type_check"]
    sqltext = str(cc.sqltext)
    for value in ("llm", "embedding", "tts", "asr"):
        assert value in sqltext
    assert "nous_models_category_check" not in constraints
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_nous_model_orm.py -v`
Expected: FAIL — `category` still present, `nous_models_type_check` missing.

- [ ] **Step 3: Edit the model**

In `backend/app/models/ai.py`, replace the `category` CheckConstraint (lines 427-430) and column (line 445), and add `description`:

```python
        CheckConstraint(
            "type = ANY (ARRAY['llm'::text, 'embedding'::text, 'tts'::text, 'asr'::text])",
            name="nous_models_type_check",
        ),
```

```python
    type: Mapped[str] = mapped_column(Text, nullable=False)
```

Add after `base_url` (line 468):

```python
    description: Mapped[Optional[str]] = mapped_column(Text)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_nous_model_orm.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && uv run black app/models/ai.py tests/test_nous_model_orm.py && uv run isort app/models/ai.py tests/test_nous_model_orm.py && uv run flake8 app/models/ai.py tests/test_nous_model_orm.py
cd .. && git add backend/app/models/ai.py backend/tests/test_nous_model_orm.py
git commit -m "feat(nous): ORM model type column + description"
```

---

### Task 3: Pydantic schemas — `type` enum + `description`

**Files:**
- Modify: `backend/app/schemas/nous.py`
- Test: `backend/tests/test_nous_schemas.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_nous_schemas.py
"""nous schemas use the type enum (not category) + carry description."""

import pytest
from pydantic import ValidationError

from app.schemas.nous import (
    NousModelCreate,
    NousModelPublic,
    NousModelResponse,
    NousModelUpdate,
)


def _create_kwargs(**over):
    base = dict(
        name="nous-llm",
        display_name="Nous LLM",
        type="llm",
        actual_provider="doubao",
        actual_model="doubao-pro-32k",
        api_key="sk-x",
    )
    base.update(over)
    return base


def test_create_accepts_type_enum_and_description():
    m = NousModelCreate(**_create_kwargs(description="fast, cheap"))
    assert m.type == "llm"
    assert m.description == "fast, cheap"


def test_create_rejects_legacy_category_value():
    with pytest.raises(ValidationError):
        NousModelCreate(**_create_kwargs(type="transcription"))


def test_update_type_optional_and_description():
    m = NousModelUpdate(type="asr", description="short clips")
    assert m.type == "asr"
    assert m.description == "short clips"


def test_public_and_response_expose_type_and_description():
    assert "type" in NousModelPublic.model_fields
    assert "description" in NousModelPublic.model_fields
    assert "type" in NousModelResponse.model_fields
    assert "description" in NousModelResponse.model_fields
    assert "category" not in NousModelPublic.model_fields
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_nous_schemas.py -v`
Expected: FAIL — `type` field missing.

- [ ] **Step 3: Rewrite `backend/app/schemas/nous.py`**

```python
# backend/app/schemas/nous.py

"""Pydantic schemas for Nous models API."""

from typing import Literal, Optional

from pydantic import BaseModel

NousModelType = Literal["llm", "embedding", "tts", "asr"]


class NousModelCreate(BaseModel):
    """Request body for creating a Nous model."""

    name: str
    display_name: str
    type: NousModelType
    description: Optional[str] = None
    actual_provider: str
    actual_model: str
    api_key: str
    app_id: Optional[str] = None
    base_url: Optional[str] = None
    pricing_type: Literal["per_hour", "per_request", "per_token"] = "per_hour"
    pricing_value: float = 8
    is_enabled: bool = True
    sort_order: int = 0


class NousModelUpdate(BaseModel):
    """Request body for updating a Nous model (all fields optional)."""

    name: Optional[str] = None
    display_name: Optional[str] = None
    type: Optional[NousModelType] = None
    description: Optional[str] = None
    actual_provider: Optional[str] = None
    actual_model: Optional[str] = None
    api_key: Optional[str] = None
    app_id: Optional[str] = None
    base_url: Optional[str] = None
    pricing_type: Optional[Literal["per_hour", "per_request", "per_token"]] = None
    pricing_value: Optional[float] = None
    is_enabled: Optional[bool] = None
    sort_order: Optional[int] = None


class NousModelResponse(BaseModel):
    """Admin response — api_key masked."""

    id: str
    name: str
    display_name: str
    type: str
    description: Optional[str] = None
    actual_provider: str
    actual_model: str
    api_key_masked: str
    app_id: Optional[str] = None
    base_url: Optional[str] = None
    pricing_type: str
    pricing_value: float
    is_enabled: bool
    sort_order: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class NousModelPublic(BaseModel):
    """Public response — no API key or provider details."""

    name: str
    display_name: str
    type: str
    description: Optional[str] = None
    pricing_type: str
    pricing_value: float
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_nous_schemas.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && uv run black app/schemas/nous.py tests/test_nous_schemas.py && uv run isort app/schemas/nous.py tests/test_nous_schemas.py && uv run flake8 app/schemas/nous.py tests/test_nous_schemas.py
cd .. && git add backend/app/schemas/nous.py backend/tests/test_nous_schemas.py
git commit -m "feat(nous): schemas use type enum + description"
```

---

### Task 4: Repositories — `category` → `type` (REST + ORM)

**Files:**
- Modify: `backend/app/repositories/nous_repository.py:35-58`
- Modify: `backend/app/repositories/nous_repository_orm.py:99-151`
- Test: extend existing `backend/tests/test_nous_repository.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_nous_repository.py`:

```python
@pytest.mark.asyncio
async def test_list_enabled_filters_on_type_column():
    """list_enabled(type_filter=...) selects + filters the `type` column."""
    from app.repositories.nous_repository import NousRepository

    repo = NousRepository()

    captured = {}

    class _Q:
        def select(self, cols):
            captured["select"] = cols
            return self

        def eq(self, col, val):
            captured.setdefault("eq", []).append((col, val))
            return self

        def order(self, col):
            return self

        async def execute(self):
            class _R:
                data = []

            return _R()

    class _Client:
        def table(self, _name):
            return _Q()

    import unittest.mock as m

    with m.patch.object(repo, "_get_client", m.AsyncMock(return_value=_Client())):
        await repo.list_enabled("llm")

    assert "type" in captured["select"]
    assert "category" not in captured["select"]
    assert ("type", "llm") in captured["eq"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_nous_repository.py::test_list_enabled_filters_on_type_column -v`
Expected: FAIL — select still contains `category`, filter uses `category`.

- [ ] **Step 3: Edit the REST repo (`nous_repository.py`)**

Replace `list_enabled` (lines 35-58):

```python
    async def list_enabled(
        self, type_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List enabled Nous models, optionally filtered by model type.

        Returns public fields only (no api_key, app_id, base_url).
        """
        try:
            client = await self._get_client()
            query = (
                client.table(self.TABLE)
                .select(
                    "id, name, display_name, type, description, "
                    "pricing_type, pricing_value, sort_order"
                )
                .eq("is_enabled", True)
                .order("sort_order")
            )
            if type_filter:
                query = query.eq("type", type_filter)
            result = await query.execute()
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to list enabled nous models: {e}")
            return []
```

- [ ] **Step 4: Edit the ORM repo (`nous_repository_orm.py`)**

Update `_PUBLIC_COLS` (lines 100-108):

```python
# Public columns exposed by list_enabled (no api_key / app_id / base_url).
_PUBLIC_COLS = (
    NousModels.id,
    NousModels.name,
    NousModels.display_name,
    NousModels.type,
    NousModels.description,
    NousModels.pricing_type,
    NousModels.pricing_value,
    NousModels.sort_order,
)
```

Update `list_enabled` (lines 134-151):

```python
    async def list_enabled(
        self, type_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        try:
            stmt = (
                select(*_PUBLIC_COLS)
                .where(NousModels.is_enabled.is_(True))
                .order_by(NousModels.sort_order)
            )
            if type_filter:
                stmt = stmt.where(NousModels.type == type_filter)
            async with read_scope() as session:
                result = await session.execute(stmt)
                # Partial-column SELECT → mappings() gives DB-column-keyed rows.
                return [_parity(dict(m)) for m in result.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to list enabled nous models: {e}")
            return []
```

> The ORM `create`/`update` already pass through `_NOUS_ATTRS`-filtered dict keys, so once the model exposes `type`/`description` (Task 2) those columns write automatically — no change needed there.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_nous_repository.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd backend && uv run black app/repositories/nous_repository.py app/repositories/nous_repository_orm.py tests/test_nous_repository.py && uv run isort app/repositories/nous_repository.py app/repositories/nous_repository_orm.py tests/test_nous_repository.py && uv run flake8 app/repositories/nous_repository.py app/repositories/nous_repository_orm.py tests/test_nous_repository.py
cd .. && git add backend/app/repositories/nous_repository.py backend/app/repositories/nous_repository_orm.py backend/tests/test_nous_repository.py
git commit -m "feat(nous): repos filter/select type instead of category"
```

---

### Task 5: Admin nous response mapping — `type` + `description`

**Files:**
- Modify: `backend/app/api/admin/nous_router.py:30-48`
- Test: `backend/tests/test_admin_nous_response.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_admin_nous_response.py
"""admin nous _to_response maps type + description (api_key still masked)."""

from app.api.admin.nous_router import _to_response


def test_to_response_maps_type_and_description():
    row = {
        "id": 123,
        "name": "nous-asr",
        "display_name": "Nous ASR",
        "type": "asr",
        "description": "short clips",
        "actual_provider": "volcengine",
        "actual_model": "seed-asr",
        "api_key": "sk-supersecret",
        "pricing_type": "per_hour",
        "pricing_value": 8,
        "is_enabled": True,
        "sort_order": 0,
    }
    resp = _to_response(row)
    assert resp.type == "asr"
    assert resp.description == "short clips"
    assert resp.api_key_masked.endswith("cret")
    assert "supersecret" not in resp.api_key_masked
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_admin_nous_response.py -v`
Expected: FAIL — `NousModelResponse` has no `type`/`description` provided by `_to_response` (KeyError on `row["category"]`).

- [ ] **Step 3: Edit `_to_response` (`nous_router.py:30-48`)**

Replace the `category=row["category"]` line and add `description`:

```python
def _to_response(row: dict) -> NousModelResponse:
    """Convert DB row to admin response with masked API key."""
    return NousModelResponse(
        id=str(row["id"]),
        name=row["name"],
        display_name=row["display_name"],
        type=row["type"],
        description=row.get("description"),
        actual_provider=row["actual_provider"],
        actual_model=row["actual_model"],
        api_key_masked=_mask_key(row.get("api_key", "")),
        app_id=row.get("app_id"),
        base_url=row.get("base_url"),
        pricing_type=row["pricing_type"],
        pricing_value=float(row["pricing_value"]),
        is_enabled=row["is_enabled"],
        sort_order=row["sort_order"],
        created_at=str(row.get("created_at", "")),
        updated_at=str(row.get("updated_at", "")),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_admin_nous_response.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && uv run black app/api/admin/nous_router.py tests/test_admin_nous_response.py && uv run isort app/api/admin/nous_router.py tests/test_admin_nous_response.py && uv run flake8 app/api/admin/nous_router.py tests/test_admin_nous_response.py
cd .. && git add backend/app/api/admin/nous_router.py backend/tests/test_admin_nous_response.py
git commit -m "feat(nous): admin response maps type + description"
```

---

# Phase 2 — Governance gates (nous master control)

### Task 6: `is_nous_globally_enabled` + `is_nous_allowed`

**Files:**
- Modify: `backend/app/services/ai/governance/ai_governance.py`
- Test: `backend/tests/test_nous_governance.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_nous_governance.py
"""nous master control gates: global default-OFF, per-module default-ON."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.governance import ai_governance as gov


@pytest.mark.asyncio
async def test_global_default_off_when_absent():
    with patch.object(gov, "_read_raw", new=AsyncMock(return_value=None)):
        assert await gov.is_nous_globally_enabled() is False


@pytest.mark.asyncio
async def test_global_true_when_set():
    with patch.object(gov, "_read_raw", new=AsyncMock(return_value=True)):
        assert await gov.is_nous_globally_enabled() is True


@pytest.mark.asyncio
async def test_module_allowed_false_when_global_off():
    # Global off → module always disallowed regardless of per-module value.
    async def _read(key):
        return None  # global absent (off), module absent

    with patch.object(gov, "_read_raw", side_effect=_read):
        assert await gov.is_nous_allowed("transcription") is False


@pytest.mark.asyncio
async def test_module_default_on_when_global_on():
    async def _read(key):
        if key == "nous.user_enabled":
            return True
        return None  # per-module absent → default-on

    with patch.object(gov, "_read_raw", side_effect=_read):
        assert await gov.is_nous_allowed("transcription") is True


@pytest.mark.asyncio
async def test_module_off_when_explicitly_disabled():
    async def _read(key):
        if key == "nous.user_enabled":
            return True
        if key == "ai_module.caption.nous_allowed":
            return False
        return None

    with patch.object(gov, "_read_raw", side_effect=_read):
        assert await gov.is_nous_allowed("caption") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_nous_governance.py -v`
Expected: FAIL — `is_nous_globally_enabled` / `is_nous_allowed` not defined.

- [ ] **Step 3: Add the gates to `ai_governance.py`**

Add the global key constant near `CHAT_MODULE` (after line 54):

```python
NOUS_GLOBAL_KEY = "nous.user_enabled"
```

Add the two functions before `__all__` (after `_get_module_governance_inner`, line 159):

```python
async def is_nous_globally_enabled() -> bool:
    """Master switch for the user-side Nous platform-provider feature.

    Reads ``nous.user_enabled`` (system_settings). DEFAULT-OFF: absent or any
    non-``True`` value → False, so platform-cost exposure is opt-in only.
    DB unreachable (``_read_raw`` returns None) ⇒ False (fail-closed for cost).
    """
    return (await _read_raw(NOUS_GLOBAL_KEY)) is True


async def is_nous_allowed(module: str) -> bool:
    """Whether users may use platform Nous models for ``module``.

    Two layers (AND): the global switch must be on, AND the per-module
    ``ai_module.<module>.nous_allowed`` must not be explicitly false.
    Per-module DEFAULT-ON once the global switch is on (absent → allowed).
    Unexpected per-module type degrades to allowed (degrade-safe), but the
    global switch alone still fully gates the feature.
    """
    if not await is_nous_globally_enabled():
        return False
    raw = await _read_raw(f"ai_module.{module}.nous_allowed")
    if raw is None:
        return True
    if isinstance(raw, bool):
        return raw
    logger.warning(
        "[governance] ai_module.%s.nous_allowed has unexpected type %s "
        "— treating as allowed (degrade-safe)",
        module,
        type(raw).__name__,
    )
    return True
```

Add both names to `__all__` (line 162-168):

```python
__all__ = [
    "AIModuleGovernance",
    "ALL_MODULES",
    "CHAT_MODULE",
    "NOUS_GLOBAL_KEY",
    "TASK_MODULES",
    "get_module_governance",
    "is_nous_allowed",
    "is_nous_globally_enabled",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_nous_governance.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && uv run black app/services/ai/governance/ai_governance.py tests/test_nous_governance.py && uv run isort app/services/ai/governance/ai_governance.py tests/test_nous_governance.py && uv run flake8 app/services/ai/governance/ai_governance.py tests/test_nous_governance.py
cd .. && git add backend/app/services/ai/governance/ai_governance.py backend/tests/test_nous_governance.py
git commit -m "feat(nous): global + per-module master control gates"
```

---

# Phase 3 — Shared resolver + ASR wiring

### Task 7: `resolve_nous_model` helper + wire into `resolve_task_provider_config`

**Files:**
- Modify: `backend/app/services/ai/providers/ai_provider_helpers.py`
- Test: `backend/tests/test_nous_resolver.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_nous_resolver.py
"""Shared nous resolver: platform config on match, fail-closed otherwise."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.governance.ai_governance import AIModuleGovernance


def _enabled_row():
    return {
        "name": "nous-llm",
        "type": "llm",
        "is_enabled": True,
        "actual_provider": "doubao",
        "actual_model": "doubao-pro-32k",
        "api_key": "platform-key",
        "base_url": "https://ark.example.com/v1",
        "app_id": None,
    }


@pytest.mark.asyncio
async def test_resolve_nous_returns_none_for_non_nous_name():
    from app.services.ai.providers import ai_provider_helpers as h

    repo = MagicMock()
    repo.get_by_name = AsyncMock(return_value=None)
    with patch(
        "app.repositories.nous_repository.get_nous_repository", return_value=repo
    ):
        result = await h.resolve_nous_model("gpt-4o", "visual_analysis")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_nous_returns_platform_config_when_enabled_and_allowed():
    from app.services.ai.providers import ai_provider_helpers as h

    repo = MagicMock()
    repo.get_by_name = AsyncMock(return_value=_enabled_row())
    with patch(
        "app.repositories.nous_repository.get_nous_repository", return_value=repo
    ):
        with patch(
            "app.services.ai.governance.ai_governance.is_nous_allowed",
            new=AsyncMock(return_value=True),
        ):
            provider_key, cfg, model = await h.resolve_nous_model(
                "nous-llm", "visual_analysis"
            )
    assert provider_key == "doubao"
    assert cfg["api_key"] == "platform-key"
    assert cfg["base_url"] == "https://ark.example.com/v1"
    assert cfg["model"] == "doubao-pro-32k"
    assert model == "doubao-pro-32k"


@pytest.mark.asyncio
async def test_resolve_nous_fail_closed_when_disabled():
    from app.services.ai.providers import ai_provider_helpers as h

    row = _enabled_row()
    row["is_enabled"] = False
    repo = MagicMock()
    repo.get_by_name = AsyncMock(return_value=row)
    with patch(
        "app.repositories.nous_repository.get_nous_repository", return_value=repo
    ):
        with patch(
            "app.services.ai.governance.ai_governance.is_nous_allowed",
            new=AsyncMock(return_value=True),
        ):
            with pytest.raises(RuntimeError, match="no longer available"):
                await h.resolve_nous_model("nous-llm", "visual_analysis")


@pytest.mark.asyncio
async def test_resolve_nous_fail_closed_when_gate_off():
    from app.services.ai.providers import ai_provider_helpers as h

    repo = MagicMock()
    repo.get_by_name = AsyncMock(return_value=_enabled_row())
    with patch(
        "app.repositories.nous_repository.get_nous_repository", return_value=repo
    ):
        with patch(
            "app.services.ai.governance.ai_governance.is_nous_allowed",
            new=AsyncMock(return_value=False),
        ):
            with pytest.raises(RuntimeError, match="disabled for this feature"):
                await h.resolve_nous_model("nous-llm", "visual_analysis")


@pytest.mark.asyncio
async def test_agent_path_uses_nous_platform_config_and_keeps_slug():
    """When an agent's model is an enabled nous model, resolve_task_provider_config
    returns the platform config but KEEPS the resolved agent slug (prompt preserved)."""
    from app.services.ai.providers import ai_provider_helpers as h

    fake_agent = {"model": "nous-llm", "slug": "my-analyze"}
    mock_repo = MagicMock()
    mock_repo.get_by_slug = AsyncMock(return_value=fake_agent)

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=AIModuleGovernance(allowed=True)),
    ):
        with patch(
            "app.repositories.agent_repository.get_agent_repository",
            return_value=mock_repo,
        ):
            with patch.object(
                h,
                "get_ai_settings",
                new=AsyncMock(
                    return_value={"task_assignment": {"visual_analysis": "my-analyze"}}
                ),
            ):
                with patch.object(
                    h,
                    "resolve_nous_model",
                    new=AsyncMock(
                        return_value=("doubao", {"model": "doubao-pro-32k"}, "doubao-pro-32k")
                    ),
                ):
                    pk, cfg, model, slug = await h.resolve_task_provider_config(
                        user_id="u1",
                        task_key="visual_analysis",
                        default_slug="analyze",
                    )

    assert pk == "doubao"
    assert model == "doubao-pro-32k"
    assert slug == "my-analyze"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_nous_resolver.py -v`
Expected: FAIL — `resolve_nous_model` not defined.

- [ ] **Step 3: Add `resolve_nous_model` and wire it in**

In `backend/app/services/ai/providers/ai_provider_helpers.py`, add the helper after `get_provider_config` (after line 41):

```python
async def resolve_nous_model(
    model_name: str, module: str
) -> Optional[Tuple[str, Dict[str, Any], str]]:
    """Resolve a model name against the platform ``nous_models`` registry.

    Full-table lookup by ``name`` (enabled + disabled), then:
      - found + enabled + nous allowed for ``module`` → return
        ``(actual_provider, {api_key, base_url, model, app_id}, actual_model)``
        — the platform config, ready for the existing adapter factory.
      - found + (disabled OR nous gated off) → **fail-closed** (RuntimeError);
        never silently fall back to a guessed BYOK provider.
      - not found → ``None`` (an ordinary BYOK model name like ``gpt-4o``).
    """
    from app.repositories.nous_repository import get_nous_repository
    from app.services.ai.governance.ai_governance import is_nous_allowed

    if not model_name:
        return None
    repo = get_nous_repository()
    row = await repo.get_by_name(model_name)
    if not row:
        return None  # ordinary BYOK model name — leave the caller's path intact.

    if not await is_nous_allowed(module):
        raise RuntimeError(
            f"Platform model '{model_name}' is disabled for this feature."
        )
    if not row.get("is_enabled"):
        raise RuntimeError(f"Platform model '{model_name}' is no longer available.")

    provider_config: Dict[str, Any] = {
        "api_key": row.get("api_key", ""),
        "base_url": row.get("base_url") or "",
        "model": row["actual_model"],
        "app_id": row.get("app_id") or "",
    }
    return row["actual_provider"], provider_config, row["actual_model"]
```

Wire into the **governance-locked branch** of `resolve_task_provider_config`. Replace the block at lines 94-115 (from the `# Derive provider_key…` comment through the locked `return`):

```python
        # Admin may lock a module directly TO a platform Nous model name —
        # run the shared nous lookup first.
        nous = await resolve_nous_model(governance.model, task_key)
        if nous is not None:
            n_provider_key, n_provider_config, n_model = nous
            logger.info(
                f"[governance] {task_key} locked to nous model "
                f"{governance.model!r} → provider {n_provider_key!r}"
            )
            return n_provider_key, n_provider_config, n_model, default_slug

        # Derive provider_key from the admin-set model prefix.
        # Unknown or missing prefix → "" (generic OpenAI-compatible; the qwen
        # adapter accepts a custom base_url + api_key for any endpoint).
        try:
            derived_key = (
                provider_key_for_model(governance.model) if governance.model else ""
            )
        except ValueError:
            derived_key = ""
        provider_config: Dict[str, Any] = {
            "api_key": governance.api_key,
            "base_url": governance.base_url,
            "model": governance.model,
        }
        logger.info(
            f"[governance] {task_key} locked by admin; using admin config "
            f"(provider_key={derived_key!r} model={governance.model!r})"
        )
        # Return the same tuple shape callers expect.
        # agent_slug = default_slug so the caller composes the module's
        # built-in default agent prompt (not a user-assigned one).
        return derived_key, provider_config, governance.model, default_slug
```

Wire into the **unlocked agent branch**. Insert immediately after the `if not model:` early-return block (after line 141, before `try: provider_key = provider_key_for_model(model)`):

```python
    # Shared nous lookup: if the agent's model names a platform Nous model,
    # return the platform config while KEEPING resolved_slug so the caller
    # still composes THIS agent's custom prompt (prompt preserved).
    nous = await resolve_nous_model(model, task_key)
    if nous is not None:
        n_provider_key, n_provider_config, n_model = nous
        return n_provider_key, n_provider_config, n_model, resolved_slug

```

- [ ] **Step 4: Run tests to verify they pass (incl. regression)**

Run: `cd backend && uv run pytest tests/test_nous_resolver.py tests/test_ai_governance_resolver.py -v`
Expected: PASS — new nous tests green AND the existing governance-resolver tests still pass (a non-nous model name returns `None` from `resolve_nous_model` since `get_by_name` returns `None`; note those tests don't mock the nous repo, so confirm `get_by_name` degrades to `None` on no DB — it swallows exceptions and returns `None`, so the BYOK path is untouched).

- [ ] **Step 5: Commit**

```bash
cd backend && uv run black app/services/ai/providers/ai_provider_helpers.py tests/test_nous_resolver.py && uv run isort app/services/ai/providers/ai_provider_helpers.py tests/test_nous_resolver.py && uv run flake8 app/services/ai/providers/ai_provider_helpers.py tests/test_nous_resolver.py
cd .. && git add backend/app/services/ai/providers/ai_provider_helpers.py backend/tests/test_nous_resolver.py
git commit -m "feat(nous): shared resolver wired into both task-provider branches"
```

---

### Task 8: ASR `nous:` branch in `load_transcribe_inputs`

**Files:**
- Modify: `backend/app/workflows/ai_transcription.py:97-122`
- Test: extend `backend/tests/test_nous_resolver.py` (or `test_ai_transcription_sql.py`)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_nous_resolver.py`:

```python
@pytest.mark.asyncio
async def test_transcription_nous_ref_routes_to_platform_config():
    """task_assignment.transcription = 'nous:<name>' → platform ASR config,
    provider_key = actual_provider (so a volcengine nous model still routes
    through the volcengine ASR branch)."""
    import app.workflows.ai_transcription as trans_mod
    from app.services.ai.governance.ai_governance import AIModuleGovernance

    fake_media_row = {
        "id": 1,
        "extract_audio_path": "/tmp/a.mp3",
        "download_path": None,
        "platform_id": "p1",
        "resource_id": 7,
    }
    fake_settings_row = {
        "settings_json": {
            "ai_settings": {
                "whisper_provider": "openai",
                "preferred_language": "en",
                "ai_providers": {},
                "task_assignment": {"transcription": "nous:nous-asr"},
            }
        }
    }

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=AIModuleGovernance(allowed=True)),
    ):
        with patch(
            "app.db.engine.fetch_one",
            side_effect=[fake_media_row, fake_settings_row],
        ):
            with patch(
                "app.services.ai.providers.ai_provider_helpers.resolve_nous_model",
                new=AsyncMock(
                    return_value=(
                        "volcengine",
                        {
                            "api_key": "plat-key",
                            "base_url": "",
                            "model": "seed-asr",
                            "app_id": "app-1",
                        },
                        "seed-asr",
                    )
                ),
            ):
                result = await trans_mod.load_transcribe_inputs(1, "u1")

    assert result["provider_key"] == "volcengine"
    assert result["provider_config"]["api_key"] == "plat-key"
    assert result["provider_config"]["app_id"] == "app-1"
    # task_assignment is normalized to provider:model so the volcengine branch
    # picks the right ASR resource from the model part.
    assert result["task_assignment"] == "volcengine:seed-asr"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_nous_resolver.py::test_transcription_nous_ref_routes_to_platform_config -v`
Expected: FAIL — current code returns `provider_key="openai"` (whisper_provider) and `task_assignment="nous:nous-asr"`.

- [ ] **Step 3: Add the `nous:` branch in `load_transcribe_inputs`**

In `backend/app/workflows/ai_transcription.py`, after the `task_assignment = ...` line (line 112) and before the final `return {...}` (line 114), insert:

```python
    # Nous platform ASR: task_assignment.transcription = 'nous:<model_name>'.
    # Resolve to the platform provider config and route through the SAME ASR
    # dispatch (a volcengine nous model → _run_volcengine_asr automatically).
    if task_assignment.startswith("nous:"):
        from app.services.ai.providers.ai_provider_helpers import resolve_nous_model

        nous_name = task_assignment.split(":", 1)[1]
        nous = await resolve_nous_model(nous_name, "transcription")
        if nous is None:
            raise RuntimeError(
                f"transcription references unknown platform model '{nous_name}'"
            )
        n_provider_key, n_provider_config, n_model = nous
        return {
            "audio_path": audio_path,
            "resource_id": str(media_row["resource_id"]),
            "platform_id": media_row["platform_id"],
            "provider_key": n_provider_key,
            "provider_config": n_provider_config,
            "language": ai_settings.get("preferred_language", "auto"),
            "task_assignment": f"{n_provider_key}:{n_model}",
        }
```

- [ ] **Step 4: Run tests to verify they pass (incl. regression)**

Run: `cd backend && uv run pytest tests/test_nous_resolver.py tests/test_ai_governance_resolver.py -v`
Expected: PASS — including the existing `test_transcription_locked_*` tests (the nous branch is after the governance gate and only fires on the `nous:` prefix).

- [ ] **Step 5: Commit**

```bash
cd backend && uv run black app/workflows/ai_transcription.py tests/test_nous_resolver.py && uv run isort app/workflows/ai_transcription.py tests/test_nous_resolver.py && uv run flake8 app/workflows/ai_transcription.py tests/test_nous_resolver.py
cd .. && git add backend/app/workflows/ai_transcription.py backend/tests/test_nous_resolver.py
git commit -m "feat(nous): transcription resolves nous:<name> to platform ASR config"
```

---

# Phase 4 — Public endpoints

### Task 9: `GET /ai/mediahub-models ?type=` + `GET /ai/governance` nous extension

**Files:**
- Modify: `backend/app/api/ai_settings_router.py:184-215`
- Modify: `backend/tests/test_ai_governance_user_endpoint.py:106-146`
- Test: `backend/tests/test_nous_public_endpoint.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_nous_public_endpoint.py
"""Public mediahub-models endpoint honors ?type=; governance exposes nous gates."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.governance.ai_governance import AIModuleGovernance


@pytest.mark.asyncio
async def test_list_nous_models_passes_type_filter():
    from app.api.ai_settings_router import list_nous_models

    repo = MagicMock()
    repo.list_enabled = AsyncMock(return_value=[{"name": "nous-llm", "type": "llm"}])
    with patch(
        "app.repositories.nous_repository.get_nous_repository", return_value=repo
    ):
        result = await list_nous_models(type="llm")
    repo.list_enabled.assert_awaited_once_with("llm")
    assert result == {"models": [{"name": "nous-llm", "type": "llm"}]}


@pytest.mark.asyncio
async def test_governance_includes_nous_enabled_and_modules():
    from app.api.ai_settings_router import get_ai_governance

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=AIModuleGovernance(allowed=True)),
    ):
        with patch(
            "app.services.ai.governance.ai_governance.is_nous_globally_enabled",
            new=AsyncMock(return_value=True),
        ):
            with patch(
                "app.services.ai.governance.ai_governance.is_nous_allowed",
                new=AsyncMock(return_value=True),
            ):
                fake_auth = MagicMock()
                result = await get_ai_governance(fake_auth)

    assert result["nous_enabled"] is True
    assert isinstance(result["nous_modules"], dict)
    assert result["nous_modules"]["transcription"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_nous_public_endpoint.py -v`
Expected: FAIL — `list_nous_models` takes `category`, governance has no `nous_enabled`.

- [ ] **Step 3: Edit the endpoints (`ai_settings_router.py`)**

Replace `get_ai_governance` (lines 184-201):

```python
@router.get("/governance")
async def get_ai_governance(auth: AuthDep):
    """Return per-module ``user_allowed`` booleans plus the Nous master-control
    state for all governed AI modules.

    The frontend uses this to hide locked modules' BYOK config and to gate the
    Nous provider card + per-module Nous options.  Only booleans are returned —
    no keys, no admin config.  Absent BYOK settings ⇒ True (default-open); the
    Nous global switch is default-OFF.
    """
    from app.services.ai.governance.ai_governance import (
        ALL_MODULES,
        get_module_governance,
        is_nous_allowed,
        is_nous_globally_enabled,
    )

    result: dict = {}
    for module in sorted(ALL_MODULES):
        g = await get_module_governance(module)
        result[module] = g.allowed

    result["nous_enabled"] = await is_nous_globally_enabled()
    result["nous_modules"] = {
        module: await is_nous_allowed(module) for module in sorted(ALL_MODULES)
    }
    return result
```

Replace `list_nous_models` (lines 204-215):

```python
@router.get("/mediahub-models")
async def list_nous_models(type: str = None):
    """List enabled Nous models (public, no API keys), optionally filtered by
    model type (``llm`` / ``embedding`` / ``tts`` / ``asr``).

    Returns models available for users to select. If none are configured,
    returns an empty list.
    """
    from app.repositories.nous_repository import get_nous_repository

    repo = get_nous_repository()
    models = await repo.list_enabled(type)
    return {"models": models}
```

- [ ] **Step 4: Update the two strict existing governance tests**

In `backend/tests/test_ai_governance_user_endpoint.py`, the new `nous_enabled`/`nous_modules` keys break `test_governance_endpoint_exact_module_set` (line 106) and `test_governance_endpoint_no_key_material` (line 128). Patch the nous gates and relax the assertions:

Replace `test_governance_endpoint_exact_module_set` (lines 106-120):

```python
@pytest.mark.asyncio
async def test_governance_endpoint_exact_module_set():
    """Module bool keys must match ALL_MODULES; nous keys are additive."""
    from app.api.ai_settings_router import get_ai_governance

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=_gov_allowed()),
    ):
        with patch(
            "app.services.ai.governance.ai_governance.is_nous_globally_enabled",
            new=AsyncMock(return_value=False),
        ):
            with patch(
                "app.services.ai.governance.ai_governance.is_nous_allowed",
                new=AsyncMock(return_value=False),
            ):
                from unittest.mock import MagicMock

                fake_auth = MagicMock()
                result = await get_ai_governance(fake_auth)

    module_keys = set(result.keys()) - {"nous_enabled", "nous_modules"}
    assert module_keys == ALL_MODULES
```

Replace `test_governance_endpoint_no_key_material` (lines 128-146):

```python
@pytest.mark.asyncio
async def test_governance_endpoint_no_key_material():
    """Module values must be plain booleans — no api_key, base_url, model.
    (nous_modules is a nested bool map and is exempt from this flat check.)"""
    from app.api.ai_settings_router import get_ai_governance

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=_gov_locked()),
    ):
        with patch(
            "app.services.ai.governance.ai_governance.is_nous_globally_enabled",
            new=AsyncMock(return_value=False),
        ):
            with patch(
                "app.services.ai.governance.ai_governance.is_nous_allowed",
                new=AsyncMock(return_value=False),
            ):
                from unittest.mock import MagicMock

                fake_auth = MagicMock()
                result = await get_ai_governance(fake_auth)

    for module, value in result.items():
        if module == "nous_modules":
            assert isinstance(value, dict)
            continue
        assert isinstance(
            value, bool
        ), f"Module {module!r}: expected bool, got {type(value).__name__!r} = {value!r}"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_nous_public_endpoint.py tests/test_ai_governance_user_endpoint.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd backend && uv run black app/api/ai_settings_router.py tests/test_nous_public_endpoint.py tests/test_ai_governance_user_endpoint.py && uv run isort app/api/ai_settings_router.py tests/test_nous_public_endpoint.py tests/test_ai_governance_user_endpoint.py && uv run flake8 app/api/ai_settings_router.py tests/test_nous_public_endpoint.py tests/test_ai_governance_user_endpoint.py
cd .. && git add backend/app/api/ai_settings_router.py backend/tests/test_nous_public_endpoint.py backend/tests/test_ai_governance_user_endpoint.py
git commit -m "feat(nous): public mediahub-models ?type= + governance nous gates"
```

---

# Phase 5 — Admin governance backend (master control write)

### Task 10: Admin governance schemas + read/write nous keys

**Files:**
- Modify: `backend/app/schemas/admin.py:273-327`
- Modify: `backend/app/api/admin/settings_router.py:195-319`
- Test: `backend/tests/test_admin_ai_governance_settings.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_admin_ai_governance_settings.py` (match its existing import/mock style — it patches `get_system_settings_repository`):

```python
@pytest.mark.asyncio
async def test_update_writes_global_nous_and_per_module_nous_allowed():
    """PUT writes nous.user_enabled + ai_module.<m>.nous_allowed."""
    from app.api.admin.settings_router import update_ai_governance_settings
    from app.schemas.admin import (
        AIGovernanceUpdate,
        ChatModuleGovernanceUpdate,
        TaskModuleGovernanceUpdate,
    )

    writes = []

    repo = MagicMock()
    repo.upsert_setting = AsyncMock(side_effect=lambda k, v, u: writes.append((k, v)))
    repo.list_non_transcode = AsyncMock(return_value=[])

    update = AIGovernanceUpdate(
        nous_user_enabled=True,
        chat=ChatModuleGovernanceUpdate(nous_allowed=False),
        transcription=TaskModuleGovernanceUpdate(nous_allowed=True),
    )

    fake_auth = MagicMock()
    fake_auth.user_id = "admin-1"
    fake_request = MagicMock()
    fake_request.client = None

    with patch(
        "app.api.admin.settings_router.get_system_settings_repository",
        return_value=repo,
    ):
        with patch(
            "app.api.admin.settings_router.create_audit_log", new=AsyncMock()
        ):
            await update_ai_governance_settings(update, fake_auth, fake_request)

    assert ("nous.user_enabled", True) in writes
    assert ("ai_module.chat.nous_allowed", False) in writes
    assert ("ai_module.transcription.nous_allowed", True) in writes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_admin_ai_governance_settings.py::test_update_writes_global_nous_and_per_module_nous_allowed -v`
Expected: FAIL — `nous_user_enabled` / `nous_allowed` not on the schemas; router does not write the keys.

- [ ] **Step 3: Extend the schemas (`schemas/admin.py`)**

Add `nous_allowed` to the response + update module schemas, and `nous_user_enabled` to the top-level response + update.

`ChatModuleGovernanceResponse` (line 273) → add field:

```python
class ChatModuleGovernanceResponse(BaseModel):
    """Governance state for the chat module (toggle only — agent owns the model)."""

    user_allowed: bool = True
    nous_allowed: bool = True
```

`TaskModuleGovernanceResponse` (line 279) → add field:

```python
class TaskModuleGovernanceResponse(BaseModel):
    """Governance state for a task module (toggle + admin base_url/model/key)."""

    user_allowed: bool = True
    nous_allowed: bool = True
    base_url: str = ""
    model: str = ""
    api_key_set: bool = False
```

`AIGovernanceResponse` (line 288) → add the global flag (after the docstring, before `chat`):

```python
    nous_user_enabled: bool = False
```

`ChatModuleGovernanceUpdate` (line 301) → add:

```python
    nous_allowed: Optional[bool] = None
```

`TaskModuleGovernanceUpdate` (line 307) → add:

```python
    nous_allowed: Optional[bool] = None
```

`AIGovernanceUpdate` (line 317) → add the global flag (before `chat`):

```python
    nous_user_enabled: Optional[bool] = None
```

- [ ] **Step 4: Extend the router read/write (`settings_router.py`)**

In `_read_governance_settings` (line 195), read the nous keys. The existing `data` dict only contains keys starting with `ai_module.` (the comprehension at lines 200-204 filters on that prefix), so the per-module `ai_module.<m>.nous_allowed` keys ARE in `data`, but the top-level `nous.user_enabled` is NOT — read it from the raw rows separately. After the `get_str` helper (line 215) add:

```python
    def get_nous(module: str) -> bool:
        v = data.get(f"ai_module.{module}.nous_allowed")
        if isinstance(v, bool):
            return v
        return True  # default-on (gated by the global switch)

    # nous.user_enabled is NOT ai_module.*-prefixed, so it is absent from `data`.
    # Read it directly from the full row set (default-off when absent).
    nous_global_on = any(
        r.get("key") == "nous.user_enabled" and r.get("value") is True for r in rows
    )
```

Then add `nous_user_enabled=nous_global_on` to the `AIGovernanceResponse(...)` constructor (line 217) and a `nous_allowed=get_nous("<module>")` arg to each module response. Example for `chat` (line 218) and `transcription` (line 221):

```python
    return AIGovernanceResponse(
        nous_user_enabled=nous_global_on,
        chat=ChatModuleGovernanceResponse(
            user_allowed=get_bool("ai_module.chat.user_allowed"),
            nous_allowed=get_nous("chat"),
        ),
        transcription=TaskModuleGovernanceResponse(
            user_allowed=get_bool("ai_module.transcription.user_allowed"),
            nous_allowed=get_nous("transcription"),
            base_url=get_str("ai_module.transcription.base_url"),
            model=get_str("ai_module.transcription.model"),
            api_key_set=bool(get_str("ai_module.transcription.api_key")),
        ),
        # …repeat nous_allowed=get_nous("<m>") for translation, visual_analysis,
        #   caption, classification, summarization (each already present)…
    )
```

> Repeat the `nous_allowed=get_nous("<module>")` line inside each of the remaining `TaskModuleGovernanceResponse(...)` blocks (translation/visual_analysis/caption/classification/summarization), exactly mirroring the transcription block above.

In `update_ai_governance_settings` (line 267), write the global switch first and the per-module `nous_allowed` in the loop. Add at the top of the function body, right after `data = update.model_dump(exclude_unset=True)` (line 281):

```python
    written: list[str] = []

    # Global Nous master switch (top-level, not under a module).
    if data.get("nous_user_enabled") is not None:
        await repo.upsert_setting(
            "nous.user_enabled", data["nous_user_enabled"], auth.user_id
        )
        written.append("nous.user_enabled")
```

(Remove the now-duplicate `written: list[str] = []` line that was at line 283.)

Inside the `for module, module_data in data.items()` loop, skip the global key and write `nous_allowed`. At the top of the loop body (after `if not module_data: continue`, line 285-286) add a guard, then the nous_allowed write next to `user_allowed`:

```python
        if module == "nous_user_enabled":
            continue  # handled above; not a per-module dict
```

And after the `user_allowed` write block (line 288-292) add:

```python
        if "nous_allowed" in module_data and module_data["nous_allowed"] is not None:
            key = f"ai_module.{module}.nous_allowed"
            await repo.upsert_setting(key, module_data["nous_allowed"], auth.user_id)
            written.append(key)
```

> NOTE: `data.items()` now includes the scalar `nous_user_enabled`. The `if not module_data:` check (line 285) would treat a truthy bool as a dict later — the explicit `if module == "nous_user_enabled": continue` guard above prevents `"user_allowed" in module_data` from raising on a bool. Place that guard BEFORE the `if not module_data` check is not required, but placing it right after is safe because `True`/`False` are not falsy-skipped consistently — add the guard as the FIRST statement in the loop body to be safe.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_admin_ai_governance_settings.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd backend && uv run black app/schemas/admin.py app/api/admin/settings_router.py tests/test_admin_ai_governance_settings.py && uv run isort app/schemas/admin.py app/api/admin/settings_router.py tests/test_admin_ai_governance_settings.py && uv run flake8 app/schemas/admin.py app/api/admin/settings_router.py tests/test_admin_ai_governance_settings.py
cd .. && git add backend/app/schemas/admin.py backend/app/api/admin/settings_router.py backend/tests/test_admin_ai_governance_settings.py
git commit -m "feat(nous): admin governance read/write global + per-module nous control"
```

- [ ] **Step 7: Backend full-suite sanity**

Run: `cd backend && uv run pytest tests/test_nous_resolver.py tests/test_nous_governance.py tests/test_nous_public_endpoint.py tests/test_nous_schemas.py tests/test_nous_repository.py tests/test_admin_nous_response.py tests/test_admin_ai_governance_settings.py tests/test_ai_governance_resolver.py tests/test_ai_governance_user_endpoint.py -v`
Expected: all PASS.

---

# Phase 6 — Frontend (user side)

### Task 11: Types + service (`type`/`description`, nous governance flags)

**Files:**
- Modify: `frontend/types.ts:568-585`
- Modify: `frontend/services/aiService.ts:439-510`

- [ ] **Step 1: Edit `frontend/types.ts`**

Replace `NousModelPublic` (lines 578-585):

```typescript
// Nous Platform Model (admin-configured, runs on the platform key)
export type NousModelType = 'llm' | 'embedding' | 'tts' | 'asr';

export interface NousModelPublic {
  name: string;
  display_name: string;
  type: NousModelType;
  description?: string | null;
  pricing_type: 'per_hour' | 'per_request' | 'per_token';
  pricing_value: number;
}
```

Extend `AIGovernanceFlags` (lines 568-576) with the nous master-control fields:

```typescript
export interface AIGovernanceFlags {
  chat: boolean;
  transcription: boolean;
  translation: boolean;
  visual_analysis: boolean;
  caption: boolean;
  classification: boolean;
  summarization: boolean;
  /** Global Nous master switch (default false). */
  nous_enabled?: boolean;
  /** Per-module Nous allow flags (default-on once nous_enabled). */
  nous_modules?: Record<string, boolean>;
}
```

- [ ] **Step 2: Edit `frontend/services/aiService.ts`**

Rename the `getNousModels` param `category` → `type` (lines 439-448):

```typescript
export const getNousModels = async (type?: string): Promise<NousModelPublic[]> => {
  const apiUrl = getApiUrl();
  const params = type ? `?type=${type}` : '';
  const response = await fetch(`${apiUrl}/api/v1/ai/mediahub-models${params}`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) return [];
  const data = await response.json();
  return data.models || [];
};
```

Extend `GOVERNANCE_ALL_ALLOWED` (lines 480-488) with default nous fields:

```typescript
export const GOVERNANCE_ALL_ALLOWED: AIGovernanceFlags = {
  chat: true,
  transcription: true,
  translation: true,
  visual_analysis: true,
  caption: true,
  classification: true,
  summarization: true,
  nous_enabled: false,
  nous_modules: {},
};
```

- [ ] **Step 3: Verify typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors from `types.ts`/`aiService.ts`. (Errors will surface in `AISettings.tsx`/`AgentEditor.tsx` consumers — fixed in Tasks 12-13.)

- [ ] **Step 4: Commit**

```bash
git add frontend/types.ts frontend/services/aiService.ts
git commit -m "feat(nous): frontend types + service use type + nous governance flags"
```

---

### Task 12: AISettings — transcription ASR options + Nous provider card

**Files:**
- Modify: `frontend/components/AISettings.tsx:664-696` (transcription options)
- Modify: `frontend/components/AISettings.tsx:432-434` (fetch llm/asr split or keep all), `1127-1130` (Nous card)

- [ ] **Step 1: Update `getTranscriptionOptions` (lines 676-689)**

The block already appends nous models but filters `m.category === 'transcription'` and stores `model.name`. Change to filter `type === 'asr'` and store `nous:<name>`:

```typescript
    // Append Nous platform ASR models (gated by admin master control).
    const nousAllowed =
      governance.nous_enabled &&
      (governance.nous_modules?.transcription ?? true);
    const matchingNousModels = nousAllowed
      ? nousModels.filter((m) => m.type === 'asr')
      : [];
    for (const model of matchingNousModels) {
      const pricingLabel =
        model.pricing_type === 'per_hour'
          ? `${model.pricing_value} pts/hr`
          : model.pricing_type === 'per_request'
            ? `${model.pricing_value} pts`
            : `${model.pricing_value} pts/1k tokens`;
      options.push({
        value: `nous:${model.name}`,
        label: `${model.display_name} (Nous · ${pricingLabel})`,
      });
    }
```

- [ ] **Step 2: Add the Nous provider card**

After the provider-map render loop closes (the `Object.entries(PROVIDER_META).map(...)` block starting line 1130), inside the `providerTab === 'text'` panel, add an informational Nous card. Add it just before the closing `</div>` of the `space-y-4` container (locate the matching close after the `.map(...)` block). The card lists enabled `llm` Nous models (badge + description), no key input, and only renders when `governance.nous_enabled`:

```tsx
          {governance.nous_enabled && (
            <div className="bg-ink-950 border border-indigo-700/40 rounded-xl overflow-hidden">
              <div className="px-6 py-4 flex items-center gap-4">
                <div className="p-2 rounded-lg bg-indigo-500/10 text-indigo-400">
                  <Sparkles size={18} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-ink-100">Nous (Platform)</span>
                    <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-indigo-500/15 text-indigo-300">
                      Platform-managed
                    </span>
                  </div>
                  <p className="text-xs text-ink-400 mt-0.5">
                    Platform-provided models. No API key required — select one as an
                    agent's model or a transcription option.
                  </p>
                </div>
              </div>
              <div className="px-6 pb-4 space-y-1.5">
                {nousModels.filter((m) => m.type === 'llm').length === 0 ? (
                  <p className="text-xs text-ink-500">No platform LLM models available.</p>
                ) : (
                  nousModels
                    .filter((m) => m.type === 'llm')
                    .map((m) => (
                      <div
                        key={m.name}
                        className="flex items-center justify-between text-sm text-ink-200 border-t border-ink-800 pt-1.5 first:border-t-0 first:pt-0"
                      >
                        <span className="font-medium">{m.display_name}</span>
                        <span className="text-xs text-ink-500">
                          {m.description || m.type}
                        </span>
                      </div>
                    ))
                )}
              </div>
            </div>
          )}
```

> `Sparkles` is already imported (used by `PROVIDER_META.openai`). `nousModels` state (line 410) and `governance` state (line 397) already exist; `getNousModels()` already fetches on mount (line 433) with no type filter, so both `llm` and `asr` rows are present in `nousModels`.

- [ ] **Step 3: Verify typecheck + build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: PASS (the `m.category` reference is gone; `m.type` now matches the type).

- [ ] **Step 4: Commit**

```bash
git add frontend/components/AISettings.tsx
git commit -m "feat(nous): AISettings ASR options (nous:<name>) + Nous provider card"
```

---

### Task 13: AgentEditor — Nous LLM model group

**Files:**
- Modify: `frontend/components/AILibrary/AgentEditor.tsx:94` (modelGroups), `750-760` (display names), plus new state/fetch near the top of the component.

- [ ] **Step 1: Add `nous` to `PROVIDER_DISPLAY_NAMES` (lines 750-760)**

```typescript
const PROVIDER_DISPLAY_NAMES: Record<string, string> = {
  openai: 'OpenAI',
  deepseek: 'DeepSeek',
  doubao: 'Doubao',
  minimax: 'MiniMax',
  kimi: 'Kimi',
  qwen: 'Qwen',
  volcengine: 'Volcengine',
  ollama: 'Ollama',
  lmstudio: 'LM Studio',
  nous: 'Nous (Platform)',
};
```

- [ ] **Step 2: Fetch nous llm models + governance, append a model group**

Near the existing `modelGroups` memo (line 94), add state + effects (place imports for `getNousModels`, `getAIGovernance`, `GOVERNANCE_ALL_ALLOWED`, and the `NousModelPublic`/`AIGovernanceFlags` types at the top of the file if not already imported from `../../services/aiService` / `../../types`):

```typescript
  const [nousLlm, setNousLlm] = useState<NousModelPublic[]>([]);
  const [nousEnabled, setNousEnabled] = useState(false);

  useEffect(() => {
    getNousModels('llm').then(setNousLlm).catch(() => {});
    getAIGovernance()
      .then((g) => setNousEnabled(Boolean(g.nous_enabled)))
      .catch(() => setNousEnabled(false));
  }, []);

  const modelGroups = useMemo(() => {
    const base = getAvailableModels(aiSettings);
    if (nousEnabled && nousLlm.length > 0) {
      base.push({
        providerKey: 'nous',
        providerName: PROVIDER_DISPLAY_NAMES.nous,
        models: nousLlm.map((m) => m.name),
      });
    }
    return base;
  }, [aiSettings, nousEnabled, nousLlm]);
```

> Replace the existing single-line `const modelGroups = useMemo(() => getAvailableModels(aiSettings), [aiSettings]);` (line 94) with the block above. `renderModelSelect` (line 797) already renders arbitrary `ProviderModelGroup` optgroups and shows a stale/orphan value, so selecting a Nous model writes `agent.model = nous_models.name` with no further change. The existing per-module nous gate is enforced server-side at resolve time; the agent picker only needs the global switch to surface the group.

- [ ] **Step 3: Verify typecheck + build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/AILibrary/AgentEditor.tsx
git commit -m "feat(nous): agent editor offers Nous platform LLM models"
```

---

# Phase 7 — Admin UI

### Task 14: Admin nous form — `type` dropdown + `description`

**Files:**
- Modify: `admin/src/pages/ai/index.tsx:12-46, 139-155, 224-232`

- [ ] **Step 1: Replace category constants with type (lines 12-46)**

```tsx
interface NousModel {
  id: string
  name: string
  display_name: string
  type: string
  description?: string
  actual_provider: string
  actual_model: string
  api_key_masked: string
  app_id?: string
  base_url?: string
  pricing_type: string
  pricing_value: number
  is_enabled: boolean
  sort_order: number
  created_at: string
  updated_at: string
}

const TYPE_OPTIONS = [
  { label: 'LLM', value: 'llm' },
  { label: 'Embedding', value: 'embedding' },
  { label: 'TTS', value: 'tts' },
  { label: 'ASR', value: 'asr' },
]

const PRICING_TYPE_OPTIONS = [
  { label: 'Per Hour', value: 'per_hour' },
  { label: 'Per Request', value: 'per_request' },
  { label: 'Per Token', value: 'per_token' },
]

const TYPE_COLORS: Record<string, string> = {
  llm: 'arcoblue',
  embedding: 'green',
  tts: 'orange',
  asr: 'purple',
}
```

- [ ] **Step 2: Update the Type column (lines 150-155)**

```tsx
    {
      title: 'Type',
      dataIndex: 'type',
      width: 110,
      render: (val: string) => <Tag color={TYPE_COLORS[val] || 'gray'}>{val}</Tag>,
    },
```

- [ ] **Step 3: Update the form — Type dropdown + Description field (lines 227-232)**

Replace the `Category` FormItem (line 230) with a `Type` dropdown and add a `Description` field after `Display Name`:

```tsx
          <FormItem label="Display Name" field="display_name" rules={[{ required: true }]}>
            <Input placeholder="e.g. Nous LLM (Volcengine 2.0)" />
          </FormItem>
          <FormItem label="Description" field="description">
            <Input placeholder="Optional usage note, e.g. fast, cheap, short clips" />
          </FormItem>
          <FormItem label="Type" field="type" rules={[{ required: true }]}>
            <Select options={TYPE_OPTIONS} />
          </FormItem>
```

- [ ] **Step 4: Verify typecheck + build**

Run: `cd admin && npx tsc --noEmit && npm run build`
Expected: PASS (no remaining `category`/`CATEGORY_*` references — grep to confirm: `grep -n "category\|CATEGORY" admin/src/pages/ai/index.tsx` returns nothing).

- [ ] **Step 5: Commit**

```bash
git add admin/src/pages/ai/index.tsx
git commit -m "feat(nous): admin model form uses type dropdown + description"
```

---

### Task 15: Admin AIGovernance — global + per-module Nous toggles

**Files:**
- Modify: `admin/src/api/endpoints/settings.ts:80-113`
- Modify: `admin/src/pages/settings/AIGovernance.tsx`

- [ ] **Step 1: Extend the admin governance types (`settings.ts`)**

`AIGovernanceChatSettings` (line 76) and `AIGovernanceTaskSettings` (line 80) → add `nous_allowed`; `AIGovernanceSettings` (line 87) → add `nous_user_enabled`; `AIGovernanceModuleUpdate` (line 97) → add `nous_allowed`; `AIGovernanceUpdate` (line 105) → add `nous_user_enabled`:

```typescript
export interface AIGovernanceChatSettings {
  user_allowed: boolean
  nous_allowed: boolean
}

export interface AIGovernanceTaskSettings {
  user_allowed: boolean
  nous_allowed: boolean
  base_url: string
  model: string
  api_key_set: boolean
}

export interface AIGovernanceSettings {
  nous_user_enabled: boolean
  chat: AIGovernanceChatSettings
  transcription: AIGovernanceTaskSettings
  translation: AIGovernanceTaskSettings
  visual_analysis: AIGovernanceTaskSettings
  caption: AIGovernanceTaskSettings
  classification: AIGovernanceTaskSettings
  summarization: AIGovernanceTaskSettings
}
```

```typescript
export interface AIGovernanceModuleUpdate {
  user_allowed: boolean
  nous_allowed?: boolean
  base_url?: string
  model?: string
  /** Only send when non-blank — blank keeps the stored key unchanged. */
  api_key?: string
}

export interface AIGovernanceUpdate {
  nous_user_enabled?: boolean
  chat?: AIGovernanceModuleUpdate
  transcription?: AIGovernanceModuleUpdate
  translation?: AIGovernanceModuleUpdate
  visual_analysis?: AIGovernanceModuleUpdate
  caption?: AIGovernanceModuleUpdate
  classification?: AIGovernanceModuleUpdate
  summarization?: AIGovernanceModuleUpdate
}
```

- [ ] **Step 2: Add nous state to `AIGovernance.tsx`**

Extend `TaskModuleLocalState` (line 43) and `LocalState` (line 50):

```typescript
interface TaskModuleLocalState {
  user_allowed: boolean
  nous_allowed: boolean
  base_url: string
  model: string
  api_key: string
}

interface LocalState {
  nous_user_enabled: boolean
  chat_allowed: boolean
  chat_nous_allowed: boolean
  transcription: TaskModuleLocalState
  translation: TaskModuleLocalState
  visual_analysis: TaskModuleLocalState
  caption: TaskModuleLocalState
  classification: TaskModuleLocalState
  summarization: TaskModuleLocalState
}
```

Update `defaultTaskState` (line 100) and `defaultLocalState` (line 104):

```typescript
function defaultTaskState(): TaskModuleLocalState {
  return { user_allowed: true, nous_allowed: true, base_url: '', model: '', api_key: '' }
}

function defaultLocalState(): LocalState {
  return {
    nous_user_enabled: false,
    chat_allowed: true,
    chat_nous_allowed: true,
    transcription: defaultTaskState(),
    translation: defaultTaskState(),
    visual_analysis: defaultTaskState(),
    caption: defaultTaskState(),
    classification: defaultTaskState(),
    summarization: defaultTaskState(),
  }
}
```

- [ ] **Step 3: Hydrate the nous fields (lines 123-164)**

In the `useEffect` that hydrates from `data`, add `nous_user_enabled`, `chat_nous_allowed`, and `nous_allowed` to each module:

```typescript
    setState({
      nous_user_enabled: data.nous_user_enabled,
      chat_allowed: data.chat.user_allowed,
      chat_nous_allowed: data.chat.nous_allowed,
      transcription: {
        user_allowed: data.transcription.user_allowed,
        nous_allowed: data.transcription.nous_allowed,
        base_url: data.transcription.base_url ?? '',
        model: data.transcription.model ?? '',
        api_key: '',
      },
      // …repeat nous_allowed: data.<m>.nous_allowed for translation,
      //   visual_analysis, caption, classification, summarization…
    })
```

> Add the `nous_allowed: data.<module>.nous_allowed` line to each of the remaining 5 module objects in this `setState`, mirroring the transcription block.

- [ ] **Step 4: Include nous in the save payload (lines 177-195)**

```typescript
    const taskUpdate = (m: TaskModuleLocalState): AIGovernanceModuleUpdate => ({
      user_allowed: m.user_allowed,
      nous_allowed: m.nous_allowed,
      base_url: m.base_url,
      model: m.model,
      ...(m.api_key.trim() ? { api_key: m.api_key.trim() } : {}),
    })

    const payload: AIGovernanceUpdate = {
      nous_user_enabled: state.nous_user_enabled,
      chat: { user_allowed: state.chat_allowed, nous_allowed: state.chat_nous_allowed },
      transcription: taskUpdate(state.transcription),
      translation: taskUpdate(state.translation),
      visual_analysis: taskUpdate(state.visual_analysis),
      caption: taskUpdate(state.caption),
      classification: taskUpdate(state.classification),
      summarization: taskUpdate(state.summarization),
    }
```

- [ ] **Step 5: Render the global Nous switch + per-module nous toggles**

Add the global switch at the top of the card body, before the `Chat` section header (line 230):

```tsx
      {/* ── Nous master control ─────────────────────────────────────────── */}
      <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--color-text-2)' }}>Nous Platform (user-side)</div>
      <Row
        label="Enable Nous for users"
        hint="Master switch. OFF ⇒ no platform models surface anywhere. Default OFF — turn on only when you accept platform-key cost exposure."
      >
        <Switch
          checked={state.nous_user_enabled}
          onChange={(checked) => setState((prev) => ({ ...prev, nous_user_enabled: checked }))}
          disabled={updateMutation.isPending}
        />
      </Row>
      <Divider />
```

Add a per-module Nous toggle. In the Chat `Row` block (lines 232-241) add a second Row after it:

```tsx
      <Row label="Allow Nous models (chat)" hint="Only effective when the master switch is on.">
        <Switch
          checked={state.chat_nous_allowed}
          onChange={(checked) => setState((prev) => ({ ...prev, chat_nous_allowed: checked }))}
          disabled={updateMutation.isPending || !state.nous_user_enabled}
        />
      </Row>
```

In the task-module loop (lines 244-298), add a Nous toggle Row right after the `Allow user config` Row (line 252-258):

```tsx
            <Row label="Allow Nous models" hint="Only effective when the master switch is on.">
              <Switch
                checked={m.nous_allowed}
                onChange={(checked) => setTaskField(key, 'nous_allowed', checked)}
                disabled={updateMutation.isPending || !state.nous_user_enabled}
              />
            </Row>
```

Also clear nous-related transient fields on save success if needed (no api_key for nous, so the existing `onSuccess` block is unchanged).

- [ ] **Step 6: Verify typecheck + build**

Run: `cd admin && npx tsc --noEmit && npm run build`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add admin/src/api/endpoints/settings.ts admin/src/pages/settings/AIGovernance.tsx
git commit -m "feat(nous): admin governance UI — global + per-module Nous toggles"
```

---

# Phase 8 — Final verification

### Task 16: Full-stack verification

- [ ] **Step 1: Backend suite (nous + governance + transcription regression)**

Run: `cd backend && uv run pytest tests/test_nous_resolver.py tests/test_nous_governance.py tests/test_nous_public_endpoint.py tests/test_nous_schemas.py tests/test_nous_repository.py tests/test_nous_model_orm.py tests/test_admin_nous_response.py tests/test_admin_ai_governance_settings.py tests/test_ai_governance_resolver.py tests/test_ai_governance_user_endpoint.py tests/test_ai_governance.py tests/test_ai_transcription_sql.py -v`
Expected: all PASS.

- [ ] **Step 2: Backend lint clean**

Run: `cd backend && uv run black --check app/ && uv run isort --check app/ && uv run flake8 app/services/ai app/api app/repositories app/schemas app/workflows/ai_transcription.py`
Expected: clean.

- [ ] **Step 3: Frontend + admin build**

Run: `cd frontend && npx tsc --noEmit && npm run build` then `cd admin && npx tsc --noEmit && npm run build`
Expected: both build green.

- [ ] **Step 4: Grep for stale `category` references in nous surfaces**

Run: `grep -rn "category" backend/app/repositories/nous_repository.py backend/app/repositories/nous_repository_orm.py backend/app/schemas/nous.py backend/app/api/admin/nous_router.py frontend/components/AISettings.tsx admin/src/pages/ai/index.tsx`
Expected: no matches (all converted to `type`).

- [ ] **Step 5: Final commit (if any lint fixups)**

```bash
git add -A && git commit -m "chore(nous): lint + final verification" || echo "nothing to commit"
```

---

## Post-merge / deploy notes (controller, not the implementer)

1. **Migration 302 must apply on prod.** Merging to master triggers `run-migration.yml`. Confirm it reaches `success` (CI green ≠ applied). Verify column rename live: `SELECT column_name FROM information_schema.columns WHERE table_name='nous_models' AND column_name IN ('type','description');` and that PostgREST sees `description` (the runner issues `NOTIFY pgrst`).
2. **Default-off is the safety net.** `nous.user_enabled` is absent → false; nothing surfaces until an admin flips the global switch in Admin → Settings → AI Config Governance.
3. **Watchtower deploy race:** after the backend image deploys, check `docker ps -a` on NAS for `mediahub-app-backend` / `mediahub-worker` stuck in "Created"/"Exited" and `docker start` them (see MEMORY `bug_worker_down_after_deploy`).
4. **CI on a private repo:** if Actions are blocked, flip the repo public for the run, then back (see MEMORY). Do not merge while private if CI can't run.
5. **Smoke test:** admin creates one `llm` nous model + one `asr` nous model, enables the global switch → user sees the Nous card, can pick the llm model as an agent's model, and the asr model in the transcription dropdown; run one transcription end-to-end to confirm the `nous:<name>` path resolves the platform key.
