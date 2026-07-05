# Generated Media — Tier 1 + Canvas Producer + Library (Plan 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture every AI-generated media item (starting with canvas `image_gen`/`video_gen`) into a dedicated, cheap `generated_media` table with full provenance, and surface it in a scope-level "Generations" library — without touching the hot `resources` table.

**Architecture:** Two-tier design. Tier 1 = new `generated_media` table (high-churn, isolated, provenance as first-class columns). A generic sink `register_generated_media(K)` downloads a provider URL to disk (capped + atomic) and inserts one row. Canvas ClassicMode image/video ops call K best-effort. A read-only Generations library (backend endpoint + frontend synthetic root) lists Tier-1 rows per scope. Promotion into `resources` (Tier 2) and the agent generation tools are later plans (Plan 2/3).

**Tech Stack:** FastAPI + SQLAlchemy Core (`app/db/engine.py` named-param helpers), asyncpg over Supavisor, Postgres (snowflake bigint ids), React 19 + Vite frontend.

## Global Constraints

- New backend `.py` must pass **black + isort + flake8** (NOT ruff) on changed files before push. (`reference_ci_backend_lint_black`)
- New frontend `.tsx` uses `ink-*` / island theme tokens, **never `zinc-*`** (CI guard).
- UI text English + i18n keys (`t('...')`); communicate with user in Chinese.
- Snowflake bigint ids via `server_default=text("generate_snowflake_id()")`; never Python-side default.
- `db_engine` commit rule: writes with RETURNING use `execute_returning_one`/`execute_returning_val` (`eng.begin()`); never `fetch_*` (no commit). (`project_orm2_migration`)
- `scope_id` is always a `teams.id` snowflake (bigint), never the user UUID. Resolve the user's personal team via `app.services.library.resources_service._resolve_personal_team_id(user_id)`.
- Backend reads/writes this table via the direct engine (service-role / BYPASSRLS); the table is RLS service-role-only so PostgREST never exposes it to anon/authenticated (mirror `worker_registry`, #541/#542).
- TDD: failing test first; commit per task. Files ≤ ~400 lines.

---

## File Structure

- Create `supabase/migrations/306_generated_media.sql` — table + RLS + indexes.
- Create `backend/app/models/generated_media.py` — `GeneratedMedia` ORM model (own domain file; export from `app/models/__init__.py`).
- Create `backend/app/services/library/generated_media_service.py` — K: `register_generated_media`, kind/ext helpers, URL→disk download.
- Create `backend/app/repositories/generated_media_repository.py` — list (keyset) / get / delete.
- Create `backend/app/api/generated_media_router.py` — `GET /generated-media`, `GET /{id}`, `GET /{id}/file`, `DELETE /{id}`; register in `app/main.py`.
- Modify `backend/app/services/canvas/canvas_run_service.py` — after `_run_image_gen`/`_run_video_gen`, best-effort call K (producer A); thread `canvas_id`.
- Modify `backend/app/api/canvases_router.py` — pass `canvas_id` into `run_classic_node`.
- Create `frontend/services/generatedMediaService.ts` — `fetchGenerations`, `generatedMediaFileUrl`.
- Modify `frontend/components/resources/ProjectAssetsTree.tsx` — add `{ kind: 'generations' }` synthetic root.
- Modify `frontend/components/ResourcesViewInner.tsx` — load Generations list when selected.
- Tests under `backend/tests/` and `frontend/` mirroring each.

---

## Task 1: `generated_media` table migration

**Files:**
- Create: `supabase/migrations/306_generated_media.sql`
- Test: `backend/tests/test_generated_media_migration.py`

**Interfaces:**
- Produces: table `public.generated_media` with columns per spec §3; service-role-only RLS.

- [ ] **Step 1: Write the migration SQL**

```sql
-- 306 — generated_media: Tier-1 store for AI-generated media (sub-plan 5).
--
-- High-churn, isolated from the hot `resources` table. Every generation lands
-- here cheaply with provenance as first-class columns; only KEPT/USED items get
-- promoted into `resources` (Plan 3). Backend writes via the direct engine
-- (BYPASSRLS); RLS is service-role-only so PostgREST never exposes it to
-- anon/authenticated (mirrors worker_registry / log-table hardening #541/#542).

CREATE TABLE IF NOT EXISTS public.generated_media (
    id                   BIGINT      PRIMARY KEY DEFAULT generate_snowflake_id(),
    scope_id             BIGINT      NOT NULL,           -- teams.id (owning scope)
    creator_id          UUID        NOT NULL,            -- the user
    media_kind           TEXT        NOT NULL,           -- 'image' | 'video'
    mime                 TEXT,
    file_path            TEXT        NOT NULL,
    file_size_bytes      BIGINT,
    origin_kind          TEXT        NOT NULL,           -- 'agent_run' | 'canvas_run'
    origin_run_id        TEXT,
    agent_id             UUID,
    canvas_id            BIGINT,
    node_id              TEXT,
    prompt               TEXT,
    model                TEXT,
    provider             TEXT,
    params               JSONB       NOT NULL DEFAULT '{}'::jsonb,
    cost_cents           NUMERIC,
    parent_resource_id   BIGINT,
    derivation_kind      TEXT,
    promoted_resource_id BIGINT,                          -- set on promote (Plan 3)
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_genmedia_scope_created
    ON public.generated_media (scope_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_genmedia_origin_run ON public.generated_media (origin_run_id);
CREATE INDEX IF NOT EXISTS idx_genmedia_agent      ON public.generated_media (agent_id);
CREATE INDEX IF NOT EXISTS idx_genmedia_canvas     ON public.generated_media (canvas_id);
CREATE INDEX IF NOT EXISTS idx_genmedia_promoted   ON public.generated_media (promoted_resource_id);

ALTER TABLE public.generated_media ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS generated_media_service_role_all ON public.generated_media;
CREATE POLICY generated_media_service_role_all ON public.generated_media
    FOR ALL TO service_role USING (true) WITH CHECK (true);

COMMENT ON TABLE public.generated_media IS
    'Tier-1 store for AI-generated media (sub-plan 5). Cheap/high-churn; promote '
    'to resources on keep/use. Backend-only (service-role RLS).';
```

- [ ] **Step 2: Write a column-presence test (reflection against dev)**

```python
# backend/tests/test_generated_media_migration.py
import re
from pathlib import Path

MIG = Path(__file__).resolve().parents[2] / "supabase/migrations/306_generated_media.sql"


def test_migration_defines_table_columns_and_rls():
    sql = MIG.read_text()
    for col in (
        "id", "scope_id", "creator_id", "media_kind", "file_path",
        "origin_kind", "origin_run_id", "canvas_id", "params",
        "promoted_resource_id", "created_at",
    ):
        assert re.search(rf"\b{col}\b", sql), f"missing column {col}"
    assert "generate_snowflake_id()" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "TO service_role" in sql
```

- [ ] **Step 3: Run test**

Run: `cd backend && uv run pytest tests/test_generated_media_migration.py -q`
Expected: PASS (file-content assertions).

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/306_generated_media.sql backend/tests/test_generated_media_migration.py
git commit -m "feat(genmedia): generated_media table migration (Tier-1, RLS service-role-only)"
```

---

## Task 2: `GeneratedMedia` ORM model

**Files:**
- Create: `backend/app/models/generated_media.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_generated_media_model.py`

**Interfaces:**
- Produces: `from app.models import GeneratedMedia` — table `generated_media`, columns matching the migration.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_generated_media_model.py
def test_generated_media_model_shape():
    from app.models import GeneratedMedia
    cols = set(GeneratedMedia.__table__.columns.keys())
    assert {
        "id", "scope_id", "creator_id", "media_kind", "file_path",
        "origin_kind", "origin_run_id", "canvas_id", "params",
        "promoted_resource_id", "created_at",
    } <= cols
    assert GeneratedMedia.__tablename__ == "generated_media"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_generated_media_model.py -q`
Expected: FAIL — `ImportError: cannot import name 'GeneratedMedia'`.

- [ ] **Step 3: Write the model**

```python
# backend/app/models/generated_media.py
"""GeneratedMedia ORM model — Tier-1 store for AI-generated media (sub-plan 5)."""
from __future__ import annotations

import datetime
import uuid

from sqlalchemy import BigInteger, DateTime, Index, Numeric, PrimaryKeyConstraint, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


class GeneratedMedia(Base):
    __tablename__ = "generated_media"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="generated_media_pkey"),
        Index("idx_genmedia_scope_created", "scope_id", "created_at"),
        Index("idx_genmedia_origin_run", "origin_run_id"),
        Index("idx_genmedia_canvas", "canvas_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    scope_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    creator_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    media_kind: Mapped[str] = mapped_column(Text, nullable=False)
    mime: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    origin_kind: Mapped[str] = mapped_column(Text, nullable=False)
    origin_run_id: Mapped[str | None] = mapped_column(Text)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    canvas_id: Mapped[int | None] = mapped_column(BigInteger)
    node_id: Mapped[str | None] = mapped_column(Text)
    prompt: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    cost_cents: Mapped[float | None] = mapped_column(Numeric)
    parent_resource_id: Mapped[int | None] = mapped_column(BigInteger)
    derivation_kind: Mapped[str | None] = mapped_column(Text)
    promoted_resource_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
```

- [ ] **Step 4: Export it**

In `backend/app/models/__init__.py` add `from app.models.generated_media import GeneratedMedia  # noqa: F401` and add `"GeneratedMedia"` to `__all__`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_generated_media_model.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/generated_media.py backend/app/models/__init__.py backend/tests/test_generated_media_model.py
git commit -m "feat(genmedia): GeneratedMedia ORM model"
```

---

## Task 3: K — download provider URL to disk (capped + atomic)

**Files:**
- Create: `backend/app/services/library/generated_media_service.py`
- Test: `backend/tests/test_generated_media_download.py`

**Interfaces:**
- Produces: `media_kind_from_mime(mime: str) -> str`, `ext_for(mime: str, kind: str) -> str`, `async def _download_to(dest_path: str, source_url: str, *, max_bytes: int = 512*1024*1024) -> int` (returns bytes written; capped + atomic `.part`).

- [ ] **Step 1: Write the failing test (kind/ext pure helpers)**

```python
# backend/tests/test_generated_media_download.py
from app.services.library import generated_media_service as gm


def test_media_kind_from_mime():
    assert gm.media_kind_from_mime("image/png") == "image"
    assert gm.media_kind_from_mime("video/mp4") == "video"
    assert gm.media_kind_from_mime("application/octet-stream") == "image"  # default


def test_ext_for():
    assert gm.ext_for("image/png", "image") == ".png"
    assert gm.ext_for("video/mp4", "video") == ".mp4"
    assert gm.ext_for("", "image") == ".png"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_generated_media_download.py -q`
Expected: FAIL — module/functions not defined.

- [ ] **Step 3: Implement helpers + download**

```python
# backend/app/services/library/generated_media_service.py
"""K — register AI-generated media into the Tier-1 generated_media store."""
from __future__ import annotations

import mimetypes
import os
from pathlib import Path

import aiofiles
import httpx
from loguru import logger

from app.boundary import cap_aiter
from app.core.config import settings

_DEFAULT_MAX_BYTES = 512 * 1024 * 1024  # 512 MiB ceiling per generation


def media_kind_from_mime(mime: str) -> str:
    m = (mime or "").lower()
    if m.startswith("video/"):
        return "video"
    return "image"  # default for images / unknown


def ext_for(mime: str, kind: str) -> str:
    guessed = mimetypes.guess_extension((mime or "").split(";")[0].strip() or "")
    if guessed:
        return guessed
    return ".mp4" if kind == "video" else ".png"


async def _download_to(dest_path: str, source_url: str, *, max_bytes: int = _DEFAULT_MAX_BYTES) -> int:
    """Stream source_url → dest_path, byte-capped + atomic (.part → os.replace)."""
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    written = 0
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            async with client.stream("GET", source_url) as resp:
                resp.raise_for_status()
                async with aiofiles.open(part, "wb") as fp:
                    async for chunk in cap_aiter(resp.aiter_bytes(), max_bytes):
                        await fp.write(chunk)
                        written += len(chunk)
        os.replace(part, dest)
        return written
    except BaseException:
        Path(part).unlink(missing_ok=True)
        logger.opt(exception=True).warning("[genmedia] download failed: %s", source_url)
        raise
```

- [ ] **Step 4: Add a download integration test (httpx mocked via respx OR a local file server)**

```python
# append to backend/tests/test_generated_media_download.py
import pytest


@pytest.mark.asyncio
async def test_download_to_writes_atomically(tmp_path, monkeypatch):
    import app.services.library.generated_media_service as gm

    class _Resp:
        def raise_for_status(self): ...
        async def aiter_bytes(self):
            yield b"hello "
            yield b"world"
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def stream(self, *a, **k): return _Resp()

    monkeypatch.setattr(gm.httpx, "AsyncClient", lambda *a, **k: _Client())
    dest = tmp_path / "x" / "media.png"
    n = await gm._download_to(str(dest), "http://example/x.png")
    assert n == 11
    assert dest.read_bytes() == b"hello world"
    assert not (tmp_path / "x" / "media.png.part").exists()
```

- [ ] **Step 5: Run tests to verify pass**

Run: `cd backend && uv run pytest tests/test_generated_media_download.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/library/generated_media_service.py backend/tests/test_generated_media_download.py
git commit -m "feat(genmedia): K download helper (capped + atomic)"
```

---

## Task 4: K — `register_generated_media`

**Files:**
- Modify: `backend/app/services/library/generated_media_service.py`
- Test: `backend/tests/test_generated_media_register.py`

**Interfaces:**
- Consumes: `_download_to`, `media_kind_from_mime`, `ext_for` (Task 3); `app.db.engine.execute_returning_one`; `_resolve_personal_team_id` (resources_service).
- Produces:
  - `@dataclass GenerationOrigin(kind, run_id=None, agent_id=None, canvas_id=None, node_id=None, prompt=None, model=None, provider=None, params=None, cost_cents=None, parent_resource_id=None, derivation_kind=None)`
  - `async def register_generated_media(*, user_id: str, scope_id: int, source_url: str, mime: str, origin: GenerationOrigin) -> dict` — returns the inserted `generated_media` row dict.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_generated_media_register.py
import pytest


@pytest.mark.asyncio
async def test_register_inserts_row_with_provenance(tmp_path, monkeypatch):
    import app.services.library.generated_media_service as gm

    async def _fake_download(dest_path, source_url, **k):
        from pathlib import Path
        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        Path(dest_path).write_bytes(b"img")
        return 3

    captured = {}

    async def _fake_execute_returning_one(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return {"id": 999, **params}

    monkeypatch.setattr(gm, "_download_to", _fake_download)
    monkeypatch.setattr(gm.settings, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(gm.db_engine, "execute_returning_one", _fake_execute_returning_one)

    origin = gm.GenerationOrigin(
        kind="canvas_run", run_id="r1", canvas_id=7, node_id="n1",
        prompt="a cat", model="m", provider="p", params={"size": "1024"},
    )
    row = await gm.register_generated_media(
        user_id="u-uuid", scope_id=42, source_url="http://x/y.png",
        mime="image/png", origin=origin,
    )
    assert row["id"] == 999
    p = captured["params"]
    assert p["scope_id"] == 42 and p["creator_id"] == "u-uuid"
    assert p["media_kind"] == "image" and p["origin_kind"] == "canvas_run"
    assert p["canvas_id"] == 7 and p["prompt"] == "a cat"
    assert "generations/" in p["file_path"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_generated_media_register.py -q`
Expected: FAIL — `GenerationOrigin`/`register_generated_media` not defined.

- [ ] **Step 3: Implement**

```python
# append to backend/app/services/library/generated_media_service.py
import json
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from app.db import engine as db_engine


@dataclass
class GenerationOrigin:
    kind: str  # 'agent_run' | 'canvas_run'
    run_id: Optional[str] = None
    agent_id: Optional[str] = None
    canvas_id: Optional[int] = None
    node_id: Optional[str] = None
    prompt: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    params: dict[str, Any] = field(default_factory=dict)
    cost_cents: Optional[float] = None
    parent_resource_id: Optional[int] = None
    derivation_kind: Optional[str] = None


async def register_generated_media(
    *, user_id: str, scope_id: int, source_url: str, mime: str, origin: GenerationOrigin
) -> dict:
    """Download a generated media URL into Tier-1 and insert one row. Returns it."""
    kind = media_kind_from_mime(mime)
    gen_uuid = _uuid.uuid4().hex
    rel = f"teams/{scope_id}/generations/{gen_uuid}/media{ext_for(mime, kind)}"
    dest = f"{settings.DOWNLOAD_PATH}/{rel}"
    size = await _download_to(dest, source_url)
    row = await db_engine.execute_returning_one(
        "INSERT INTO public.generated_media "
        "(scope_id, creator_id, media_kind, mime, file_path, file_size_bytes, "
        " origin_kind, origin_run_id, agent_id, canvas_id, node_id, prompt, model, "
        " provider, params, cost_cents, parent_resource_id, derivation_kind) "
        "VALUES (:scope_id, :creator_id, :media_kind, :mime, :file_path, :file_size_bytes, "
        " :origin_kind, :origin_run_id, :agent_id, :canvas_id, :node_id, :prompt, :model, "
        " :provider, CAST(:params AS jsonb), :cost_cents, :parent_resource_id, :derivation_kind) "
        "RETURNING *",
        {
            "scope_id": scope_id,
            "creator_id": user_id,
            "media_kind": kind,
            "mime": mime,
            "file_path": rel,
            "file_size_bytes": size,
            "origin_kind": origin.kind,
            "origin_run_id": origin.run_id,
            "agent_id": origin.agent_id,
            "canvas_id": origin.canvas_id,
            "node_id": origin.node_id,
            "prompt": origin.prompt,
            "model": origin.model,
            "provider": origin.provider,
            "params": json.dumps(origin.params or {}),
            "cost_cents": origin.cost_cents,
            "parent_resource_id": origin.parent_resource_id,
            "derivation_kind": origin.derivation_kind,
        },
    )
    return row or {}
```

- [ ] **Step 4: Run test to verify pass**

Run: `cd backend && uv run pytest tests/test_generated_media_register.py -q`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
cd backend && uv run black app/services/library/generated_media_service.py tests/test_generated_media_register.py && uv run isort app/services/library/generated_media_service.py tests/test_generated_media_register.py && uv run flake8 app/services/library/generated_media_service.py tests/test_generated_media_register.py
git add backend/app/services/library/generated_media_service.py backend/tests/test_generated_media_register.py
git commit -m "feat(genmedia): register_generated_media sink"
```

---

## Task 5: Repository — list (keyset) / get / delete

**Files:**
- Create: `backend/app/repositories/generated_media_repository.py`
- Test: `backend/tests/test_generated_media_repository.py`

**Interfaces:**
- Consumes: `app.db.engine.fetch_all`, `fetch_one`, `execute`.
- Produces: `GeneratedMediaRepository` with
  - `async def list_for_scope(scope_id: int, *, kind: str | None = None, cursor: str | None = None, limit: int = 30) -> dict` → `{"items": [...], "next_cursor": str | None}` (keyset on `created_at,id` DESC).
  - `async def get(gen_id: int, scope_id: int) -> dict | None`
  - `async def delete(gen_id: int, scope_id: int) -> bool`

- [ ] **Step 1: Write the failing test (cursor encode/decode pure helpers)**

```python
# backend/tests/test_generated_media_repository.py
from app.repositories.generated_media_repository import _encode_cursor, _decode_cursor


def test_cursor_roundtrip():
    c = _encode_cursor("2026-06-21T00:00:00+00:00", 123)
    ts, gid = _decode_cursor(c)
    assert ts == "2026-06-21T00:00:00+00:00" and gid == 123


def test_decode_bad_cursor_returns_none():
    assert _decode_cursor("garbage") is None
    assert _decode_cursor(None) is None
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && uv run pytest tests/test_generated_media_repository.py -q`
Expected: FAIL — import error.

- [ ] **Step 3: Implement repository**

```python
# backend/app/repositories/generated_media_repository.py
"""Data access for generated_media (Tier-1). Keyset list by (created_at, id) DESC."""
from __future__ import annotations

import base64
import json
from typing import Optional

from app.db import engine as db_engine

_COLS = (
    "id, scope_id, creator_id, media_kind, mime, file_path, file_size_bytes, "
    "origin_kind, origin_run_id, agent_id, canvas_id, node_id, prompt, model, "
    "provider, params, cost_cents, parent_resource_id, derivation_kind, "
    "promoted_resource_id, created_at"
)


def _encode_cursor(created_at: str, gen_id: int) -> str:
    return base64.urlsafe_b64encode(json.dumps([created_at, gen_id]).encode()).decode()


def _decode_cursor(cursor: Optional[str]) -> Optional[tuple[str, int]]:
    if not cursor:
        return None
    try:
        ts, gid = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return str(ts), int(gid)
    except Exception:  # noqa: BLE001
        return None


class GeneratedMediaRepository:
    async def list_for_scope(
        self, scope_id: int, *, kind: Optional[str] = None,
        cursor: Optional[str] = None, limit: int = 30,
    ) -> dict:
        limit = max(1, min(int(limit), 100))
        params: dict = {"scope_id": scope_id, "limit": limit + 1}
        where = ["scope_id = :scope_id"]
        if kind:
            where.append("media_kind = :kind")
            params["kind"] = kind
        decoded = _decode_cursor(cursor)
        if decoded:
            params["c_ts"], params["c_id"] = decoded
            where.append("(created_at, id) < (CAST(:c_ts AS timestamptz), :c_id)")
        rows = await db_engine.fetch_all(
            f"SELECT {_COLS} FROM public.generated_media "
            f"WHERE {' AND '.join(where)} ORDER BY created_at DESC, id DESC LIMIT :limit",
            params,
        ) or []
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = _encode_cursor(str(last["created_at"]), int(last["id"]))
            rows = rows[:limit]
        return {"items": rows, "next_cursor": next_cursor}

    async def get(self, gen_id: int, scope_id: int) -> Optional[dict]:
        return await db_engine.fetch_one(
            f"SELECT {_COLS} FROM public.generated_media "
            "WHERE id = :id AND scope_id = :scope_id",
            {"id": gen_id, "scope_id": scope_id},
        )

    async def delete(self, gen_id: int, scope_id: int) -> bool:
        n = await db_engine.execute(
            "DELETE FROM public.generated_media WHERE id = :id AND scope_id = :scope_id",
            {"id": gen_id, "scope_id": scope_id},
        )
        return bool(n)
```

- [ ] **Step 4: Run test to verify pass**

Run: `cd backend && uv run pytest tests/test_generated_media_repository.py -q`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
cd backend && uv run black app/repositories/generated_media_repository.py tests/test_generated_media_repository.py && uv run isort app/repositories/generated_media_repository.py tests/test_generated_media_repository.py && uv run flake8 app/repositories/generated_media_repository.py tests/test_generated_media_repository.py
git add backend/app/repositories/generated_media_repository.py backend/tests/test_generated_media_repository.py
git commit -m "feat(genmedia): repository (keyset list/get/delete)"
```

---

## Task 6: Router — list / detail / file / delete

**Files:**
- Create: `backend/app/api/generated_media_router.py`
- Modify: `backend/app/main.py` (register router)
- Test: `backend/tests/test_generated_media_router.py`

**Interfaces:**
- Consumes: `GeneratedMediaRepository` (Task 5); `_resolve_personal_team_id`; `AuthDep`; `settings.DOWNLOAD_PATH`.
- Produces routes (prefix `/api/v1`):
  - `GET /generated-media?kind=&cursor=&limit=` → `{ data: { items, next_cursor } }`
  - `GET /generated-media/{id}` → `{ data: row }` (404 if not in scope)
  - `GET /generated-media/{id}/file` → FileResponse
  - `DELETE /generated-media/{id}` → `{ data: { deleted: bool } }`

- [ ] **Step 1: Write the failing test (scope isolation via repo monkeypatch)**

```python
# backend/tests/test_generated_media_router.py
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_list_uses_caller_personal_scope(monkeypatch):
    import app.api.generated_media_router as r

    async def _fake_scope(uid): return "42"
    async def _fake_list(scope_id, **k):
        assert scope_id == 42
        return {"items": [{"id": 1}], "next_cursor": None}

    monkeypatch.setattr(r, "_resolve_personal_team_id", _fake_scope)
    monkeypatch.setattr(r.GeneratedMediaRepository, "list_for_scope", lambda self, sid, **k: _fake_list(sid, **k))
    # auth override: inject a fake AuthDep returning a user; mirror existing router tests
    # (see backend/tests for the standard auth dependency override helper)
    ...
```

(Use the repo's existing auth-override test helper — grep `app.dependency_overrides` in `backend/tests/` for the established pattern; wire it the same way.)

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && uv run pytest tests/test_generated_media_router.py -q`
Expected: FAIL — router missing.

- [ ] **Step 3: Implement router**

```python
# backend/app/api/generated_media_router.py
"""Generations library — read-only Tier-1 surfacing (sub-plan 5)."""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.core.auth import AuthDep  # match the project's AuthDep import path
from app.core.config import settings
from app.repositories.generated_media_repository import GeneratedMediaRepository
from app.services.library.resources_service import _resolve_personal_team_id

router = APIRouter(prefix="/generated-media", tags=["generated-media"])


async def _scope(auth) -> int:
    return int(await _resolve_personal_team_id(str(auth.user_id)))


@router.get("")
async def list_generations(
    auth: AuthDep,
    kind: str | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(30, ge=1, le=100),
) -> dict:
    page = await GeneratedMediaRepository().list_for_scope(
        await _scope(auth), kind=kind, cursor=cursor, limit=limit
    )
    return {"data": page}


@router.get("/{gen_id}")
async def get_generation(gen_id: int, auth: AuthDep) -> dict:
    row = await GeneratedMediaRepository().get(gen_id, await _scope(auth))
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    return {"data": row}


@router.get("/{gen_id}/file")
async def get_generation_file(gen_id: int, auth: AuthDep):
    row = await GeneratedMediaRepository().get(gen_id, await _scope(auth))
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    path = os.path.join(settings.DOWNLOAD_PATH, row["file_path"])
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="file missing")
    return FileResponse(path, media_type=row.get("mime") or "application/octet-stream")


@router.delete("/{gen_id}")
async def delete_generation(gen_id: int, auth: AuthDep) -> dict:
    ok = await GeneratedMediaRepository().delete(gen_id, await _scope(auth))
    return {"data": {"deleted": ok}}
```

(Verify `AuthDep` import path + the `{ "data": ... }` envelope against a sibling router, e.g. `project_assets_router.py`, before finalizing.)

- [ ] **Step 4: Register in main.py**

In `backend/app/main.py`, import and `app.include_router(generated_media_router.router, prefix="/api/v1")` alongside the other resource routers.

- [ ] **Step 5: Run tests to verify pass**

Run: `cd backend && uv run pytest tests/test_generated_media_router.py -q`
Expected: PASS.

- [ ] **Step 6: Lint + commit**

```bash
cd backend && uv run black app/api/generated_media_router.py tests/test_generated_media_router.py && uv run isort app/api/generated_media_router.py tests/test_generated_media_router.py && uv run flake8 app/api/generated_media_router.py tests/test_generated_media_router.py
git add backend/app/api/generated_media_router.py backend/app/main.py backend/tests/test_generated_media_router.py
git commit -m "feat(genmedia): read-only Generations library endpoints"
```

---

## Task 7: Producer A — capture canvas image_gen / video_gen

**Files:**
- Modify: `backend/app/services/canvas/canvas_run_service.py` (`run_classic_node`, `_run_image_gen`, `_run_video_gen`)
- Modify: `backend/app/api/canvases_router.py` (pass `canvas_id`)
- Test: `backend/tests/test_canvas_generation_capture.py`

**Interfaces:**
- Consumes: `register_generated_media`, `GenerationOrigin` (Task 4); `_resolve_personal_team_id`.
- Produces: after a successful canvas image/video op, a `generated_media` row (origin_kind='canvas_run') is created best-effort; canvas run result unchanged.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_canvas_generation_capture.py
import pytest


@pytest.mark.asyncio
async def test_image_gen_captures_generated_media(monkeypatch):
    import app.services.canvas.canvas_run_service as crs

    calls = {}

    async def _fake_register(**kwargs):
        calls.update(kwargs)
        return {"id": 1}

    async def _fake_scope(uid): return "42"
    monkeypatch.setattr(crs, "register_generated_media", _fake_register)
    monkeypatch.setattr(crs, "_resolve_personal_team_id", _fake_scope)

    svc = crs.CanvasRunService()
    monkeypatch.setattr(
        svc, "_storyboard_ai_service",
        lambda: type("S", (), {"generate_image": staticmethod(
            lambda **k: {"image_url": "http://x/y.png"})})(),
    )
    res = await svc._run_image_gen(
        node={"data": {"prompt": "cat"}}, body="cat", node_id="n1",
        project_id="7", user_id="u-uuid", canvas_id=99,
    )
    assert res.ok
    assert calls["source_url"] == "http://x/y.png"
    assert calls["origin"].kind == "canvas_run" and calls["origin"].canvas_id == 99
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && uv run pytest tests/test_canvas_generation_capture.py -q`
Expected: FAIL — `_run_image_gen` has no `user_id`/`canvas_id` params / no capture.

- [ ] **Step 3: Implement capture (image + video)**

In `_run_image_gen` / `_run_video_gen`: add `user_id: str | None = None`, `canvas_id: int | None = None` params (thread through `run_classic_node`). After obtaining `image_url`/`video_url`, before returning `ok`:

```python
# canvas_run_service.py — after image_url is resolved, best-effort capture
if image_url and user_id:
    try:
        from app.services.library.generated_media_service import (
            GenerationOrigin, register_generated_media,
        )
        from app.services.library.resources_service import _resolve_personal_team_id

        scope_id = int(await _resolve_personal_team_id(str(user_id)))
        await register_generated_media(
            user_id=str(user_id), scope_id=scope_id, source_url=str(image_url),
            mime="image/png",
            origin=GenerationOrigin(
                kind="canvas_run", run_id=None, canvas_id=canvas_id, node_id=node_id,
                prompt=prompt, model=params.get("model"),
                provider=params.get("provider_name"), params=params,
                derivation_kind="image_gen",
            ),
        )
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).warning("[genmedia] canvas capture failed (non-fatal)")
```

(Mirror for `_run_video_gen` with `mime="video/mp4"`, `video_url`, `derivation_kind="video_gen"`. Import `register_generated_media`/`GenerationOrigin`/`_resolve_personal_team_id` at module top so the monkeypatched test names resolve.)

Thread `user_id` + `canvas_id` from `run_classic_node` down, and from `canvases_router.py` (the `/canvases/runs/classic-node` route — it has `auth.user_id` and the `canvas_id` from the request body) into `run_classic_node(..., user_id=str(auth.user_id), canvas_id=...)`.

- [ ] **Step 4: Run test to verify pass**

Run: `cd backend && uv run pytest tests/test_canvas_generation_capture.py -q`
Expected: PASS.

- [ ] **Step 5: Run the existing canvas suite (no regression)**

Run: `cd backend && uv run pytest tests/ -k "canvas" -q`
Expected: PASS (capture is additive + best-effort).

- [ ] **Step 6: Lint + commit**

```bash
cd backend && uv run black app/services/canvas/canvas_run_service.py app/api/canvases_router.py tests/test_canvas_generation_capture.py && uv run isort <same> && uv run flake8 <same>
git add backend/app/services/canvas/canvas_run_service.py backend/app/api/canvases_router.py backend/tests/test_canvas_generation_capture.py
git commit -m "feat(genmedia): capture canvas image/video generations into Tier-1"
```

---

## Task 8: Frontend — Generations synthetic root + list view

**Files:**
- Create: `frontend/services/generatedMediaService.ts`
- Modify: `frontend/components/resources/ProjectAssetsTree.tsx`
- Modify: `frontend/components/ResourcesViewInner.tsx`
- Modify: `frontend/public/locales/en.json`, `frontend/public/locales/zh.json`
- Test: `frontend/services/generatedMediaService.test.ts`

**Interfaces:**
- Consumes: `GET /api/v1/generated-media`, `GET /api/v1/generated-media/{id}/file` (Task 6).
- Produces: `fetchGenerations(cursor?, kind?)`, `generatedMediaFileUrl(id)`; `ProjectAssetsSelection | { kind: 'generations' }`.

- [ ] **Step 1: Write the failing service test**

```ts
// frontend/services/generatedMediaService.test.ts
import { describe, it, expect, vi } from 'vitest';
import { fetchGenerations } from './generatedMediaService';

describe('fetchGenerations', () => {
  it('calls the generated-media endpoint and unwraps data', async () => {
    const json = { data: { items: [{ id: '1' }], next_cursor: null } };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(json) }));
    const page = await fetchGenerations();
    expect(page.items).toHaveLength(1);
    expect(page.next_cursor).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify fail**

Run: `cd frontend && npx vitest run services/generatedMediaService.test.ts`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement service**

```ts
// frontend/services/generatedMediaService.ts
import { getApiUrl } from './apiBase';        // match the project's helper
import { getAuthHeaders } from './parserService';

export interface GenerationItem {
  id: string; media_kind: string; mime?: string; prompt?: string;
  model?: string; provider?: string; created_at: string; canvas_id?: string;
  origin_kind: string;
}
export interface GenerationsPage { items: GenerationItem[]; next_cursor: string | null; }

export async function fetchGenerations(cursor?: string, kind?: string): Promise<GenerationsPage> {
  const qs = new URLSearchParams();
  if (cursor) qs.set('cursor', cursor);
  if (kind) qs.set('kind', kind);
  const res = await fetch(`${getApiUrl()}/api/v1/generated-media?${qs}`, {
    headers: await getAuthHeaders(),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()).data as GenerationsPage;
}

export function generatedMediaFileUrl(id: string): string {
  return `${getApiUrl()}/api/v1/generated-media/${id}/file`;
}
```

- [ ] **Step 4: Run service test to verify pass**

Run: `cd frontend && npx vitest run services/generatedMediaService.test.ts`
Expected: PASS.

- [ ] **Step 5: Add the synthetic root + view wiring**

- In `ProjectAssetsTree.tsx`: extend `ProjectAssetsSelection` with `| { kind: 'generations' }`; add a button above/below "Chat Uploads" (mirror its JSX exactly, `Sparkles` icon from lucide-react, label `t('projectAssets.generations', 'Generations')`, use `ink-*`/island tokens — NO `zinc-*`).
- In `ResourcesViewInner.tsx`: when `selection.kind === 'generations'`, call `fetchGenerations` and render the grid with thumbnails via `generatedMediaFileUrl(id)` + a detail panel showing provenance (prompt/model/provider/created_at).
- Add i18n keys `projectAssets.generations` to `en.json` ("Generations") and `zh.json` ("Generations" — UI English per rules; zh value mirrors).

- [ ] **Step 6: Typecheck + build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: 0 new errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/services/generatedMediaService.ts frontend/services/generatedMediaService.test.ts frontend/components/resources/ProjectAssetsTree.tsx frontend/components/ResourcesViewInner.tsx frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(genmedia): Generations library synthetic root + list view"
```

---

## Self-Review notes (for the implementer)

- **Spec coverage:** Tasks 1-2 = `generated_media` table+model (spec §3). Task 3-4 = K download+register (§4). Task 5-6 = Generations library (§5). Task 7 = producer A canvas (§7). Task 8 = frontend synthetic root (§5). Promotion (P, §6), agent tools (B, §8), storyboard/TTS/nav (§11) are Plan 2/3 — intentionally NOT here.
- **Deferred to Plan 2/3:** `promoted_resource_id` column exists now (Task 1) but is only written by Plan 3's promote.
- **Verify-before-final:** Task 6 `AuthDep` import path + response envelope, and Task 7 `canvases_router` route name + how `canvas_id` arrives — confirm against current code before finalizing those two tasks (they touch existing files).
- After all tasks: run `cd backend && uv run pytest tests/ -k "genmedia or canvas or generated_media" -q` and the frontend vitest + tsc; lint changed files (black/isort/flake8) before pushing; ship per the repo's public→merge→private flow.
