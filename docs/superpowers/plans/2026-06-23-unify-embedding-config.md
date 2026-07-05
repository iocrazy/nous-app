# Unify Embedding Config → Settings (resurrect prod semantic search) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `EmbeddingService` resolve its provider/model/key from `system_settings` (the working self-hosted qwen embedder) instead of the unset `OPENAI_API_KEY` env — resurrecting prod semantic search + analyze embeddings — and migrate the pgvector column to the qwen embedding dimension.

**Architecture:** `EmbeddingService` lazily loads embedding config from `system_settings` (reusing the already-configured `graph_embedder_*` keys — the same working `qwen3-embedding-8b` @ `http://10.0.0.10:8000/v1`, 4096-dim, that the memory stack uses). No env reads. The `resource_analysis.content_embedding` column migrates `vector(1536)` → `vector(4096)`; its ivfflat index is dropped (pgvector caps ivfflat/hnsw at 2000 dims; data is empty + scale tiny, so a seq scan is correct for now — `halfvec(4096)` + hnsw is the documented future scale path).

**Tech Stack:** FastAPI + SQLAlchemy async (asyncpg) + pgvector + self-hosted qwen embedder (OpenAI-compatible) + pytest.

**Confirmed prod state (2026-06-23, via NAS):** `OPENAI_API_KEY=` empty in backend+worker → `EmbeddingService.client=None` → embeddings return None; **8 "embedding generation will be disabled" warnings in last 7 days**. `graph_embedder_*` system_settings present + working: `base_url=http://10.0.0.10:8000/v1`, `model=qwen3-embedding-8b`, `dimensions=4096`, `api_key=<set>`, `graph_memory_enabled=true`. `resource_analysis` = 2 rows, **0 with embedding**. `content_embedding vector(1536)` + ivfflat index `idx_resource_analysis_embedding`. `match_videos_by_embedding(query_embedding vector, ...)` arg is untyped `vector` (no 1536 hardcode) → RPC needs no change.

---

## File Structure

- **Create** `backend/app/services/ai/providers/embedding_config.py` — `get_embedding_config()` async reader (system_settings `graph_embedder_*`, no env), returns a small frozen dataclass or `None`.
- **Modify** `backend/app/services/ai/providers/embedding_service.py` — lazy async config load; build `AsyncOpenAI(base_url, api_key)`; use configured model; delete `os.getenv("OPENAI_API_KEY")` / `os.getenv("OPENAI_EMBEDDING_MODEL")`.
- **Create** `supabase/migrations/311_resource_analysis_embedding_4096.sql` — drop ivfflat index, `ALTER COLUMN content_embedding TYPE vector(4096)`, idempotent.
- **Create** `backend/tests/test_embedding_config.py` — config reader tests.
- **Modify** `backend/tests/...` (or new `test_embedding_service.py`) — service resolves from config / disabled when unconfigured.
- **No change**: `search_service.py`, `analyze_l1.py` (already call `EmbeddingService()` + `await generate_embedding(...)`; lazy load keeps the call sites untouched), `match_videos_by_embedding` RPC.

---

### Task 1: Embedding config reader (system_settings, no env)

**Files:**
- Create: `backend/app/services/ai/providers/embedding_config.py`
- Test: `backend/tests/test_embedding_config.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_embedding_config.py
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.providers.embedding_config import (
    EmbeddingConfig,
    get_embedding_config,
)


@pytest.mark.asyncio
async def test_returns_config_from_settings() -> None:
    rows = {
        "graph_embedder_base_url": "http://10.0.0.10:8000/v1",
        "graph_embedder_api_key": "sk-x",
        "graph_embedder_model": "qwen3-embedding-8b",
        "graph_embedder_dimensions": 4096,
    }
    with patch(
        "app.services.ai.providers.embedding_config._read_settings",
        AsyncMock(return_value=rows),
    ):
        cfg = await get_embedding_config()
    assert cfg == EmbeddingConfig(
        base_url="http://10.0.0.10:8000/v1",
        api_key="sk-x",
        model="qwen3-embedding-8b",
        dimensions=4096,
    )


@pytest.mark.asyncio
async def test_none_when_no_base_url_or_key() -> None:
    with patch(
        "app.services.ai.providers.embedding_config._read_settings",
        AsyncMock(return_value={"graph_embedder_model": "qwen3-embedding-8b"}),
    ):
        assert await get_embedding_config() is None


@pytest.mark.asyncio
async def test_none_when_db_unavailable() -> None:
    with patch(
        "app.services.ai.providers.embedding_config._read_settings",
        AsyncMock(return_value={}),
    ):
        assert await get_embedding_config() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_embedding_config.py -q`
Expected: FAIL with `ModuleNotFoundError: app.services.ai.providers.embedding_config`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/ai/providers/embedding_config.py
"""Resolve text-embedding provider config from system_settings (no env).

Reuses the already-configured ``graph_embedder_*`` keys — the single
self-hosted qwen embedder the memory stack uses — so search / analyze /
memory share ONE embedding config. DB is the only source of truth; a
missing/unconfigured deploy yields ``None`` (embeddings disabled, never
raises), mirroring ``graph_memory.from_settings``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from loguru import logger

_KEYS = (
    "graph_embedder_base_url",
    "graph_embedder_api_key",
    "graph_embedder_model",
    "graph_embedder_dimensions",
)


@dataclass(frozen=True)
class EmbeddingConfig:
    base_url: str
    api_key: str
    model: str
    dimensions: int


async def _read_settings() -> Dict[str, Any]:
    """Return the graph_embedder_* system_settings as a dict. Never raises."""
    try:
        from app.db import engine as db_engine

        if not db_engine.is_configured():
            return {}
        placeholders = ",".join(f":k{i}" for i in range(len(_KEYS)))
        params = {f"k{i}": k for i, k in enumerate(_KEYS)}
        rows = await db_engine.fetch_all(
            f"SELECT key, value FROM public.system_settings "
            f"WHERE key IN ({placeholders})",
            params,
        )
        return {r["key"]: r["value"] for r in rows}
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"[embedding_config] settings read failed: {exc}")
        return {}


async def get_embedding_config() -> Optional[EmbeddingConfig]:
    """Resolve the platform embedding config, or None when unconfigured."""
    s = await _read_settings()
    base_url = str(s.get("graph_embedder_base_url") or "").strip()
    api_key = str(s.get("graph_embedder_api_key") or "").strip()
    model = str(s.get("graph_embedder_model") or "").strip()
    if not base_url or not api_key or not model:
        return None
    try:
        dims = int(s.get("graph_embedder_dimensions") or 0)
    except (TypeError, ValueError):
        dims = 0
    return EmbeddingConfig(
        base_url=base_url, api_key=api_key, model=model, dimensions=dims
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_embedding_config.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ai/providers/embedding_config.py backend/tests/test_embedding_config.py
git commit -m "feat(ai): embedding config reader from system_settings (no env)"
```

---

### Task 2: Rewire EmbeddingService to the platform config (kill OPENAI_* env)

**Files:**
- Modify: `backend/app/services/ai/providers/embedding_service.py`
- Test: `backend/tests/test_embedding_service.py` (create)

**Context:** `EmbeddingService.__init__` is sync and is instantiated synchronously by `search_service.py:43` and `analyze_l1.py:109`; `generate_embedding` is async. So config must load **lazily on first `generate_embedding`** (an async `_ensure_client()` like `graph_memory._ensure_config`), NOT in `__init__`. This keeps both call sites unchanged.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_embedding_service.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.providers.embedding_config import EmbeddingConfig
from app.services.ai.providers.embedding_service import EmbeddingService


@pytest.mark.asyncio
async def test_disabled_when_unconfigured_returns_none() -> None:
    with patch(
        "app.services.ai.providers.embedding_service.get_embedding_config",
        AsyncMock(return_value=None),
    ):
        svc = EmbeddingService()
        assert await svc.generate_embedding("hello") is None


@pytest.mark.asyncio
async def test_uses_settings_config_and_model() -> None:
    cfg = EmbeddingConfig(
        base_url="http://10.0.0.10:8000/v1",
        api_key="sk-x",
        model="qwen3-embedding-8b",
        dimensions=4096,
    )
    fake_resp = MagicMock()
    fake_resp.data = [MagicMock(embedding=[0.1] * 4096)]
    fake_client = MagicMock()
    fake_client.embeddings.create = AsyncMock(return_value=fake_resp)

    with patch(
        "app.services.ai.providers.embedding_service.get_embedding_config",
        AsyncMock(return_value=cfg),
    ), patch(
        "app.services.ai.providers.embedding_service.AsyncOpenAI",
        return_value=fake_client,
    ) as mk:
        svc = EmbeddingService()
        out = await svc.generate_embedding("hello")

    assert out == [0.1] * 4096
    mk.assert_called_once_with(api_key="sk-x", base_url="http://10.0.0.10:8000/v1")
    _, kwargs = fake_client.embeddings.create.call_args
    assert kwargs["model"] == "qwen3-embedding-8b"


@pytest.mark.asyncio
async def test_no_openai_env_read() -> None:
    import inspect

    import app.services.ai.providers.embedding_service as mod

    src = inspect.getsource(mod)
    assert "OPENAI_API_KEY" not in src
    assert "OPENAI_EMBEDDING_MODEL" not in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_embedding_service.py -q`
Expected: FAIL (`get_embedding_config` not imported in embedding_service; `OPENAI_API_KEY` still in source)

- [ ] **Step 3: Write minimal implementation**

Replace the top of `embedding_service.py` (imports + `__init__` + add `_ensure_client`) and guard `generate_embedding` on the lazy client:

```python
"""Embedding generation service — provider/model from system_settings."""

from typing import List, Optional

from loguru import logger
from openai import AsyncOpenAI

from app.services.ai.providers.embedding_config import get_embedding_config


class EmbeddingService:
    """Generate text embeddings using the platform-configured embedder.

    Config (base_url / api_key / model / dimensions) comes from
    system_settings (the shared qwen embedder), loaded lazily on first use.
    No env reads. When unconfigured, embedding is disabled (returns None).
    """

    def __init__(self) -> None:
        self.client: Optional[AsyncOpenAI] = None
        self.model: str = ""
        self._loaded = False

    async def _ensure_client(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        cfg = await get_embedding_config()
        if cfg is None:
            logger.warning(
                "Embedding config not set in system_settings; "
                "embedding generation disabled"
            )
            return
        self.client = AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
        self.model = cfg.model

    async def generate_embedding(self, text: str) -> Optional[List[float]]:
        """Generate an embedding vector for ``text`` (None when disabled)."""
        await self._ensure_client()
        if not self.client:
            logger.warning("Embedding client not initialized, skipping")
            return None
        # ... keep the existing body (empty-text guard, truncation, create call,
        #     return response.data[0].embedding, except->log+None) unchanged,
        #     it already uses self.model.
```

> Keep the rest of `generate_embedding` (empty-text guard, `max_chars` truncation, `self.client.embeddings.create(model=self.model, ...)`, the `except` returning None) exactly as-is. Delete the `import os`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_embedding_service.py tests/test_embedding_config.py -q`
Expected: PASS

- [ ] **Step 5: Run the dependent suites (no call-site change expected)**

Run: `cd backend && uv run pytest tests/ -q -k "search or analyze or embedding"`
Expected: PASS (search_service / analyze_l1 still construct `EmbeddingService()` and await `generate_embedding`).

- [ ] **Step 6: Lint + commit**

```bash
cd backend && uv run black app/services/ai/providers/embedding_service.py app/services/ai/providers/embedding_config.py tests/test_embedding_service.py && uv run isort --check-only app/services/ai/providers/embedding_service.py && uv run flake8 app/services/ai/providers/embedding_service.py app/services/ai/providers/embedding_config.py
git add backend/app/services/ai/providers/embedding_service.py backend/tests/test_embedding_service.py
git commit -m "feat(ai): EmbeddingService resolves provider from system_settings, lazy-load, no OPENAI_* env"
```

---

### Task 3: Migrate content_embedding vector(1536) → vector(4096)

**Files:**
- Create: `supabase/migrations/311_resource_analysis_embedding_4096.sql`

**Context:** qwen3-embedding-8b emits 4096-dim vectors; the column is `vector(1536)`. The ivfflat index can't exist at 4096 (pgvector caps ivfflat/hnsw at 2000 dims). 0 rows have embeddings → clean ALTER. Seq scan is correct at this scale; `halfvec(4096)` + hnsw is the future scale path (documented, not built now).

- [ ] **Step 1: Write the migration**

```sql
-- 311_resource_analysis_embedding_4096.sql
-- Switch resource_analysis.content_embedding to the platform qwen embedder's
-- dimension (4096). The prior vector(1536) sized for OpenAI text-embedding-3-small
-- is dead in prod (OPENAI_API_KEY unset). 0 rows carry an embedding, so this is a
-- clean, lossless retype.
--
-- The ivfflat index is DROPPED, not rebuilt: pgvector ivfflat/hnsw cap at 2000
-- dimensions, so a 4096-dim vector cannot be indexed by them. At current scale a
-- sequential scan is fine. Scale path (when row count grows): convert the column
-- to halfvec(4096) and build an hnsw index (halfvec supports up to 4096 dims).

DROP INDEX IF EXISTS public.idx_resource_analysis_embedding;

ALTER TABLE public.resource_analysis
    ALTER COLUMN content_embedding TYPE vector(4096)
    USING NULL;  -- 0 rows populated; existing 1536-typed values (none) are discarded

COMMENT ON COLUMN public.resource_analysis.content_embedding IS
    'pgvector(4096) — qwen3-embedding-8b. Unindexed (4096 > pgvector ivfflat/hnsw
     2000-dim cap); seq scan at current scale, halfvec+hnsw is the scale path.';
```

- [ ] **Step 2: Validate on a throwaway Postgres + pgvector**

Run (mirrors the trigger-test pattern used earlier this session):
```bash
docker run -d --name mh-vec -e POSTGRES_PASSWORD=t -p 55998:5432 pgvector/pgvector:pg15 >/dev/null
# wait ready, then:
docker exec mh-vec psql -U postgres -c "CREATE EXTENSION vector; CREATE TABLE public.resource_analysis(content_embedding vector(1536)); CREATE INDEX idx_resource_analysis_embedding ON public.resource_analysis USING ivfflat (content_embedding vector_cosine_ops) WITH (lists='100');"
docker cp supabase/migrations/311_resource_analysis_embedding_4096.sql mh-vec:/tmp/311.sql
docker exec mh-vec psql -U postgres -v ON_ERROR_STOP=1 -f /tmp/311.sql
docker exec mh-vec psql -U postgres -tAc "SELECT format_type(atttypid,atttypmod) FROM pg_attribute WHERE attrelid='public.resource_analysis'::regclass AND attname='content_embedding';"
docker exec mh-vec psql -U postgres -v ON_ERROR_STOP=1 -f /tmp/311.sql  # idempotency re-run
docker rm -f mh-vec
```
Expected: type prints `vector(4096)`; both applies succeed (idempotent).

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/311_resource_analysis_embedding_4096.sql
git commit -m "fix(db): migrate resource_analysis.content_embedding to vector(4096) for qwen embedder"
```

---

### Task 4: Final verification + ship

- [ ] **Step 1: Full relevant suite + lint gate**

Run:
```bash
cd backend && uv run pytest tests/ -q -k "embedding or search or analyze or governance" \
  && uv run black --check app/services/ai/providers/embedding_service.py app/services/ai/providers/embedding_config.py \
  && uv run flake8 app/services/ai/providers/embedding_service.py app/services/ai/providers/embedding_config.py
```
Expected: all green.

- [ ] **Step 2: Grep gate — no AI provider env left in the embedding path**

Run: `cd backend && grep -rn "OPENAI_API_KEY\|OPENAI_EMBEDDING_MODEL" app/services/ai/providers/embedding_service.py || echo "clean"`
Expected: `clean`.

- [ ] **Step 3: Ship (backend + migration → ACR + Run SQL Migration)**

PR → flip repo public (public-guard watchdog) → CI green → squash merge → watch **Run SQL Migration on NAS** + **Deploy Backend to ACR** to success → flip private. Migration is idempotent + uses `IF EXISTS`; no PostgREST reload needed (no new column).

- [ ] **Step 4: Prod verify (NAS)**

After deploy, confirm the warning stops + an embedding lands:
```bash
ssh -i ~/.ssh/nas_deploy_key -p 1122 heygo@192.168.50.9 \
  'sudo /usr/local/bin/docker exec mediahub-app-backend /app/.venv/bin/python -c "import asyncio; from app.services.ai.providers.embedding_service import EmbeddingService; print(len(asyncio.run(EmbeddingService().generate_embedding(\"hello world\")) or []))"'
```
Expected: prints `4096` (embedding now works against the qwen embedder). Then spot-check `application_logs` that the "embedding generation will be disabled" warning rate drops to 0 going forward.

---

## Self-Review notes
- **No behavior change for configured-correctly deploys:** dev/prod already have `graph_embedder_*` set → embedding now works where it returned None. The only "change" is resurrection of a dead path; nothing that worked before breaks.
- **Call sites untouched:** lazy `_ensure_client` keeps `search_service` + `analyze_l1` construction sync; `generate_embedding` stays the async surface.
- **Dimension safety:** 0 stored embeddings → ALTER is lossless; RPC arg is untyped `vector`; no 1536 hardcode anywhere downstream.
- **Out of scope (deliberately):** image/video-gen governance (dormant — 0 providers registered), nous-center/internal-LLM env, the unified admin "Platform AI" panel. Embedding reuses the existing Memory-panel `graph_embedder_*` config rather than introducing a new admin surface — minimal churn, single source.
