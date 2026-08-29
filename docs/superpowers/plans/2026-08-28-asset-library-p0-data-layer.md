# Asset Library P0 — Data Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the asset-library data layer — six new tables, two existing-table extensions, ORM models, repositories, a service with readiness/link/loadout invariants, the `/api/v1/assets` router, and a dry-run-by-default migration workflow that plans (does not yet execute) the `project_characters` / `project_lib_entities` → `assets` move.

**Architecture:** `assets` is the single source of truth for entities; `asset_files` attaches existing `resources` rows to typed slots without moving them; `asset_links` / `asset_loadouts` / `asset_project_refs` are pure relation tables. Everything is ORM (no `text()` SQL), scoped by an explicit `scope_id` predicate in every repo method (the `ProjectCharacters` pattern — no scope mixin, so the choke point stays inert). Readiness is a pure function over slot counts, never stored. The router only exposes what P1/P2 need; canvas/agent/generated wiring is later phases.

**Tech Stack:** PostgreSQL (Supabase, Snowflake BIGINT ids), SQLAlchemy 2.x async ORM (`app.db.session.read_scope/write_scope`), FastAPI, Pydantic v2, DBOS workflow for the backfill, pytest + httpx ASGITransport.

**Spec:** `docs/superpowers/specs/2026-08-28-asset-library-loadout-design.md` (§3 data model, §4 migration, §5.1 API, §7 invariants, §9 P0 row)

## Global Constraints

- Python **3.13** (`.python-version`); run backend commands as `cd backend && uv run …`.
- **No new `text()` raw SQL** in app code (CLAUDE.md 2026-08-04). Migrations are SQL files; app code is ORM only. The backfill workflow reads/writes through ORM too.
- Snowflake BIGINT ids are **JSON strings** at the API boundary (`str()` in `_serialize`), and test fixtures must exercise **numeric** ids at the repo boundary (CLAUDE.md 2026-08-12).
- Every new public table **must** have an ORM model whose columns / nullability / types / FKs match the SQL exactly — `tests/db/test_schema_drift.py` gates 1–6 run in CI against baseline + migrations above the watermark.
- Migration numbers: latest is `444`; this plan uses **445** and **446**. Run `git fetch origin master && ls supabase/migrations | tail -1` before creating them; if taken, renumber both and every reference in this plan (memory: `reference-migration-number-collision-check`).
- Migrations end with `NOTIFY pgrst, 'reload schema';`. Never `SET ROLE service_role`.
- UI text is English; this phase has no UI.
- Lint: `black` (88 cols) + `isort` (profile black) + `flake8`; run `cd backend && uv run black <files> && uv run isort <files>` before each commit.
- Commit only files this plan touches; do not `git add -A`.

---

## File Structure

| Path | Responsibility |
|---|---|
| `supabase/migrations/445_asset_library_core.sql` | Create `assets`, `asset_loadouts`, `asset_files`, `asset_links`, `asset_project_refs`, `canvas_asset_refs` |
| `supabase/migrations/446_asset_library_generated_canvas_ext.sql` | `generated_media.review_state` + `source_asset_id`; `canvases.asset_id`; widen `canvases.kind` and `generated_media.origin_kind` |
| `backend/app/models/assets.py` | Six ORM models (one file: they change together) |
| `backend/app/models/__init__.py` | Export the six models |
| `backend/app/models/generated_media.py`, `backend/app/models/canvas.py` | New columns |
| `backend/app/services/assets/__init__.py` | Package marker |
| `backend/app/services/assets/slots.py` | Slot table per type, `readiness()` pure function, link-type rules |
| `backend/app/schemas/assets.py` | Pydantic request/response models |
| `backend/app/repositories/assets_repository.py` | `assets` CRUD, list with derived counts |
| `backend/app/repositories/asset_relations_repository.py` | `asset_files` / `asset_links` / `asset_loadouts` / `asset_project_refs` |
| `backend/app/services/assets/assets_service.py` | Invariants: default loadout, link type check, loadout ⊆ links, 409 on duplicate name |
| `backend/app/api/assets_router.py` + `backend/app/api/__init__.py` | `/assets` routes |
| `backend/app/workflows/backfill_assets_from_project_entities.py` | DBOS workflow, `dry_run=True` default; pure `plan_migration()` |
| `backend/tests/migrations/test_445_asset_library_core.py` | Integration (skips without DB) |
| `backend/tests/services/assets/test_slots.py` | Pure function tests |
| `backend/tests/repositories/test_assets_repository_serialize.py` | Serialization / numeric-id tests |
| `backend/tests/services/assets/test_assets_service.py` | Invariant tests with fake repos |
| `backend/tests/api/test_assets_router.py` | Router shape + gating |
| `backend/tests/workflows/test_backfill_assets_plan.py` | `plan_migration()` merge rules |

---

### Task 1: Migration 445 — core tables

**Files:**
- Create: `supabase/migrations/445_asset_library_core.sql`
- Test: `backend/tests/migrations/test_445_asset_library_core.py`

**Interfaces:**
- Produces: tables `assets`, `asset_loadouts`, `asset_files`, `asset_links`, `asset_project_refs`, `canvas_asset_refs` with the exact columns below (Task 3 mirrors them 1:1).

- [ ] **Step 1: Confirm the migration number is free**

Run: `git fetch origin master && ls supabase/migrations | tail -1`
Expected: `444_codex_local_llm_catalog.sql`. If a `445_*` exists on master, renumber this plan's 445→next free and 446→next+1 everywhere before continuing.

- [ ] **Step 2: Write the integration test (skips without a DB)**

```python
# backend/tests/migrations/test_445_asset_library_core.py
"""Verify mig 445 creates the asset-library core tables with the exact shape
the ORM models in app/models/assets.py mirror (schema-drift gates 1-6)."""

from __future__ import annotations

import os

import pytest

from app.db import engine as db_engine

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("SUPAVISOR_DATABASE_URL")
        and not os.environ.get("INTEGRATION_DATABASE_URL"),
        reason="integration DB URL not set",
    ),
]

_TABLES = (
    "assets",
    "asset_loadouts",
    "asset_files",
    "asset_links",
    "asset_project_refs",
    "canvas_asset_refs",
)


async def _columns(table: str) -> dict[str, dict]:
    rows = await db_engine.fetch_all(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=:t",
        {"t": table},
    )
    return {r["column_name"]: r for r in rows}


@pytest.mark.parametrize("table", _TABLES)
async def test_table_exists(table):
    cols = await _columns(table)
    assert cols, f"{table} missing"


async def test_assets_columns():
    cols = await _columns("assets")
    assert cols["asset_type"]["is_nullable"] == "NO"
    assert cols["prompt_positive"]["is_nullable"] == "YES"
    assert cols["attrs"]["data_type"] == "jsonb"
    assert cols["deleted_at"]["is_nullable"] == "YES"


async def test_assets_unique_name_per_scope_type_is_partial():
    rows = await db_engine.fetch_all(
        "SELECT indexdef FROM pg_indexes WHERE tablename='assets' "
        "AND indexname='uq_assets_scope_type_name'",
        {},
    )
    assert rows and "WHERE (deleted_at IS NULL)" in rows[0]["indexdef"]


async def test_asset_loadouts_single_default_per_asset():
    rows = await db_engine.fetch_all(
        "SELECT indexdef FROM pg_indexes WHERE tablename='asset_loadouts' "
        "AND indexname='uq_loadout_default'",
        {},
    )
    assert rows and "WHERE is_default" in rows[0]["indexdef"]


async def test_asset_links_relation_check():
    rows = await db_engine.fetch_all(
        "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint "
        "WHERE conrelid='asset_links'::regclass AND conname='asset_links_relation_check'",
        {},
    )
    assert rows and "wears" in rows[0]["def"] and "voice_of" in rows[0]["def"]
```

(Reads of `information_schema` / `pg_*` are the documented raw-SQL exception.)

- [ ] **Step 3: Run the test to confirm it skips locally / fails on a DB without the migration**

Run: `cd backend && uv run pytest tests/migrations/test_445_asset_library_core.py -v`
Expected: `SKIPPED` (no DB URL). With `INTEGRATION_DATABASE_URL` set: FAIL "assets missing".

- [ ] **Step 4: Write the migration**

```sql
-- 445_asset_library_core.sql
--
-- Asset Library (spec 2026-08-28-asset-library-loadout-design §3).
--
-- Why NEW tables instead of widening project_characters / project_lib_entities:
-- assets are TEAM-scoped and referenced by many projects (decision 1); the old
-- tables are project-private and will be migrated INTO these (§4) then dropped
-- in P6. Files are never moved or copied — asset_files only points at existing
-- resources rows (decision 5). readiness is derived in code, never stored.
--
-- Creation order matters: asset_files has an FK to asset_loadouts.

-- 1) assets ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS assets (
    id                 BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    scope_id           BIGINT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    asset_type         TEXT NOT NULL
                       CHECK (asset_type IN ('character','location','prop','costume','prompt','audio')),
    -- audio: music|sfx|voice ; prompt: character|storyboard|product|lighting|…
    subtype            TEXT,
    name               TEXT NOT NULL,
    -- character: lead/support/antagonist/'' ; location: exterior/interior ; others ''.
    role_tag           TEXT NOT NULL DEFAULT '',
    description        TEXT NOT NULL DEFAULT '',
    -- Type-specific attributes (location.time_of_day[], audio.duration_ms,
    -- audio.loop_range, prompt.placeholders[], board_layout). Opaque here.
    attrs              JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Consistency prompt (character/location/prop/costume) or template body (prompt).
    prompt_positive    TEXT,
    prompt_negative    TEXT,
    prompt_positive_zh TEXT,
    prompt_negative_zh TEXT,
    -- prompt type only: {"midjourney": "--ar 1:1 …", "seedream": "…", "flux": "…"}
    platform_params    JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Card portrait; NULL = first file in the primary slot.
    cover_file_id      BIGINT REFERENCES resources(id) ON DELETE SET NULL,
    source             TEXT NOT NULL DEFAULT 'manual'
                       CHECK (source IN ('manual','script_import','generated','migrated','duplicated','system_preset')),
    duplicated_from    BIGINT REFERENCES assets(id) ON DELETE SET NULL,
    is_system_preset   BOOLEAN NOT NULL DEFAULT false,
    tags               JSONB NOT NULL DEFAULT '{}'::jsonb,
    sort_order         INT NOT NULL DEFAULT 0,
    created_by         UUID,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at         TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_assets_scope_type
    ON assets (scope_id, asset_type) WHERE deleted_at IS NULL;

-- Decision 14: same-name-same-type within a scope must not silently create a
-- second entity. Partial so a soft-deleted row frees the name.
CREATE UNIQUE INDEX IF NOT EXISTS uq_assets_scope_type_name
    ON assets (scope_id, asset_type, lower(name)) WHERE deleted_at IS NULL;

-- 2) asset_loadouts ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_loadouts (
    id           BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    -- character assets only (service-enforced).
    asset_id     BIGINT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    is_default   BOOLEAN NOT NULL DEFAULT false,
    -- ⊆ this character's `wears` / `holds` link targets (service-enforced).
    costume_ids  BIGINT[] NOT NULL DEFAULT '{}',
    prop_ids     BIGINT[] NOT NULL DEFAULT '{}',
    prompt_extra TEXT,
    sort_order   INT NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_asset_loadouts_asset ON asset_loadouts (asset_id, sort_order);
CREATE UNIQUE INDEX IF NOT EXISTS uq_loadout_default ON asset_loadouts (asset_id) WHERE is_default;

-- 3) asset_files ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_files (
    asset_id     BIGINT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    resource_id  BIGINT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    -- Slot names live in app/services/assets/slots.py; 'unsorted' always valid.
    slot         TEXT NOT NULL,
    loadout_id   BIGINT REFERENCES asset_loadouts(id) ON DELETE SET NULL,
    sort_order   INT NOT NULL DEFAULT 0,
    note         TEXT,
    attached_by  UUID,
    attached_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (asset_id, resource_id, slot)
);

CREATE INDEX IF NOT EXISTS idx_asset_files_resource ON asset_files (resource_id);

-- 4) asset_links ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_links (
    from_asset_id BIGINT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    to_asset_id   BIGINT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    relation      TEXT NOT NULL
                  CONSTRAINT asset_links_relation_check
                  CHECK (relation IN ('wears','holds','ambience_of','voice_of')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (from_asset_id, to_asset_id, relation),
    CONSTRAINT asset_links_no_self CHECK (from_asset_id <> to_asset_id)
);

CREATE INDEX IF NOT EXISTS idx_asset_links_to ON asset_links (to_asset_id, relation);

-- 5) asset_project_refs --------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_project_refs (
    asset_id    BIGINT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    project_id  BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    linked_by   UUID,
    linked_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (asset_id, project_id)
);

CREATE INDEX IF NOT EXISTS idx_apr_project ON asset_project_refs (project_id);

-- 6) canvas_asset_refs (mirrors canvas_resource_refs; maintained in P4) --------
CREATE TABLE IF NOT EXISTS canvas_asset_refs (
    canvas_id   BIGINT NOT NULL REFERENCES canvases(id) ON DELETE CASCADE,
    asset_id    BIGINT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    node_id     TEXT NOT NULL,
    loadout_id  BIGINT REFERENCES asset_loadouts(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (canvas_id, asset_id, node_id)
);

CREATE INDEX IF NOT EXISTS idx_car_asset ON canvas_asset_refs (asset_id);

-- PostgREST schema reload (CI-migration-skips-reload trap).
NOTIFY pgrst, 'reload schema';
```

- [ ] **Step 5: Apply locally and run the test**

Run: `psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -f supabase/migrations/445_asset_library_core.sql && cd backend && INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres uv run pytest tests/migrations/test_445_asset_library_core.py -v`
Expected: 9 PASS. (If the local Supabase is not running, run against nous-db via `docker exec -i nous-db psql -U postgres -p 55434 -d postgres < …` on gpupc, or skip and rely on CI's schema-drift job.)

- [ ] **Step 6: Commit**

```bash
git add supabase/migrations/445_asset_library_core.sql backend/tests/migrations/test_445_asset_library_core.py
git commit -m "feat(assets): mig 445 — asset library core tables (assets/files/links/loadouts/project_refs/canvas_refs)"
```

---

### Task 2: Migration 446 — generated_media + canvases extensions

**Files:**
- Create: `supabase/migrations/446_asset_library_generated_canvas_ext.sql`
- Test: append to `backend/tests/migrations/test_445_asset_library_core.py`

**Interfaces:**
- Produces: `generated_media.review_state` (TEXT NOT NULL DEFAULT 'unreviewed'), `generated_media.source_asset_id` (BIGINT NULL FK assets), `canvases.asset_id` (BIGINT NULL FK assets), `canvases.kind` accepts `'costume'`, `generated_media.origin_kind` accepts `'storyboard' | 'cover_studio' | 'chat_upload'`.

- [ ] **Step 1: Append tests**

```python
# append to backend/tests/migrations/test_445_asset_library_core.py


async def test_generated_media_review_state_default():
    cols = await _columns("generated_media")
    assert cols["review_state"]["is_nullable"] == "NO"
    assert cols["source_asset_id"]["is_nullable"] == "YES"
    rows = await db_engine.fetch_all(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_name='generated_media' AND column_name='review_state'",
        {},
    )
    assert "'unreviewed'" in (rows[0]["column_default"] or "")


async def test_canvases_asset_id_and_costume_kind():
    cols = await _columns("canvases")
    assert cols["asset_id"]["is_nullable"] == "YES"
    rows = await db_engine.fetch_all(
        "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint "
        "WHERE conrelid='canvases'::regclass AND conname='canvases_kind_check'",
        {},
    )
    assert rows and "'costume'" in rows[0]["def"]
```

- [ ] **Step 2: Run to verify fail (on a DB) / skip (without)**

Run: `cd backend && uv run pytest tests/migrations/test_445_asset_library_core.py -k "review_state or costume" -v`
Expected: SKIPPED without DB; FAIL `KeyError: 'review_state'` with DB.

- [ ] **Step 3: Write the migration**

First check the current `origin_kind` constraint name and values:
Run: `grep -hn "origin_kind" supabase/migrations/*.sql | grep -i check | tail -2`
Use the constraint name it prints (expected `generated_media_origin_kind_check`) in the DROP below; keep every existing value and add the three new ones.

```sql
-- 446_asset_library_generated_canvas_ext.sql
--
-- Asset Library P0 (spec §3.7): generated_media becomes the "Generated" inbox
-- (review_state), can remember which asset a generation was dispatched from
-- (source_asset_id → pre-fills Save-as-Asset), and canvases can be owned by an
-- asset (the entity's optional workshop canvas — decision 13). project_id on
-- canvases stays NOT NULL: it records which project the canvas was opened from.

-- 1) generated_media -------------------------------------------------------------
ALTER TABLE generated_media
    ADD COLUMN IF NOT EXISTS review_state TEXT NOT NULL DEFAULT 'unreviewed'
        CONSTRAINT generated_media_review_state_check
        CHECK (review_state IN ('unreviewed','saved','in_assets','deleted')),
    ADD COLUMN IF NOT EXISTS source_asset_id BIGINT REFERENCES assets(id) ON DELETE SET NULL;

-- Already-promoted rows are "saved" (P1 backfills in_assets once asset_files exist).
UPDATE generated_media SET review_state = 'saved'
 WHERE promoted_resource_id IS NOT NULL AND review_state = 'unreviewed';

CREATE INDEX IF NOT EXISTS idx_genmedia_scope_state_created
    ON generated_media (scope_id, review_state, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_genmedia_source_asset
    ON generated_media (source_asset_id) WHERE source_asset_id IS NOT NULL;

ALTER TABLE generated_media DROP CONSTRAINT IF EXISTS generated_media_origin_kind_check;
ALTER TABLE generated_media
    ADD CONSTRAINT generated_media_origin_kind_check
    CHECK (origin_kind IN ('canvas_run','agent_run','storyboard','cover_studio','chat_upload'));

-- 2) canvases --------------------------------------------------------------------
ALTER TABLE canvases
    ADD COLUMN IF NOT EXISTS asset_id BIGINT REFERENCES assets(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_canvases_asset ON canvases (asset_id) WHERE asset_id IS NOT NULL;

ALTER TABLE canvases DROP CONSTRAINT IF EXISTS canvases_kind_check;
ALTER TABLE canvases
    ADD CONSTRAINT canvases_kind_check
    CHECK (kind IN ('smart', 'lite', 'classic', 'character', 'location', 'prop', 'costume', 'storyboard'));

NOTIFY pgrst, 'reload schema';
```

If the grep in this step shows `origin_kind` has **no** CHECK constraint today, delete the two `origin_kind` ALTER statements — do not invent a constraint the baseline never had.

- [ ] **Step 4: Apply and test**

Run: `psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -f supabase/migrations/446_asset_library_generated_canvas_ext.sql && cd backend && INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres uv run pytest tests/migrations/test_445_asset_library_core.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/446_asset_library_generated_canvas_ext.sql backend/tests/migrations/test_445_asset_library_core.py
git commit -m "feat(assets): mig 446 — generated_media review_state/source_asset_id, canvases.asset_id, costume kind"
```

---

### Task 3: ORM models

**Files:**
- Create: `backend/app/models/assets.py`
- Modify: `backend/app/models/__init__.py` (import block near line 101 and `__all__` near line 317)
- Modify: `backend/app/models/generated_media.py` (add two columns after `promoted_resource_id`)
- Modify: `backend/app/models/canvas.py` (`Canvases`: add `asset_id` after `kind`)
- Test: `backend/tests/db/test_schema_drift.py` (existing; gates the shape)

**Interfaces:**
- Produces: classes `Assets`, `AssetLoadouts`, `AssetFiles`, `AssetLinks`, `AssetProjectRefs`, `CanvasAssetRefs` importable from `app.models`.

- [ ] **Step 1: Write a quick import test**

```python
# backend/tests/repositories/test_assets_repository_serialize.py  (first test; file grows in Task 6)
from app.models import (
    AssetFiles,
    AssetLinks,
    AssetLoadouts,
    AssetProjectRefs,
    Assets,
    CanvasAssetRefs,
)


def test_asset_models_map_expected_tables():
    assert Assets.__tablename__ == "assets"
    assert AssetLoadouts.__tablename__ == "asset_loadouts"
    assert AssetFiles.__tablename__ == "asset_files"
    assert AssetLinks.__tablename__ == "asset_links"
    assert AssetProjectRefs.__tablename__ == "asset_project_refs"
    assert CanvasAssetRefs.__tablename__ == "canvas_asset_refs"
    # asset_files has FK to asset_loadouts — creation order dependency
    fk_targets = {fk.column.table.name for fk in AssetFiles.__table__.foreign_keys}
    assert fk_targets == {"assets", "resources", "asset_loadouts"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/repositories/test_assets_repository_serialize.py -v`
Expected: FAIL `ImportError: cannot import name 'Assets'`.

- [ ] **Step 3: Write the models**

```python
# backend/app/models/assets.py
"""Asset Library ORM models (mig 445) — spec 2026-08-28-asset-library-loadout-design §3.

No scope mixin on purpose (same stance as ``ProjectCharacters``): every repo
method carries an explicit ``scope_id`` predicate, so the choke point stays
inert. Column shapes mirror the migration 1:1 — tests/db/test_schema_drift.py
enforces it.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base

ASSET_TYPES = ("character", "location", "prop", "costume", "prompt", "audio")
ASSET_SOURCES = (
    "manual",
    "script_import",
    "generated",
    "migrated",
    "duplicated",
    "system_preset",
)
LINK_RELATIONS = ("wears", "holds", "ambience_of", "voice_of")


class Assets(Base):
    __tablename__ = "assets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id"], ["public.teams.id"], ondelete="CASCADE", name="assets_scope_id_fkey"
        ),
        ForeignKeyConstraint(
            ["cover_file_id"],
            ["public.resources.id"],
            ondelete="SET NULL",
            name="assets_cover_file_id_fkey",
        ),
        ForeignKeyConstraint(
            ["duplicated_from"],
            ["public.assets.id"],
            ondelete="SET NULL",
            name="assets_duplicated_from_fkey",
        ),
        PrimaryKeyConstraint("id", name="assets_pkey"),
        CheckConstraint(
            "asset_type IN ('character','location','prop','costume','prompt','audio')",
            name="assets_asset_type_check",
        ),
        CheckConstraint(
            "source IN ('manual','script_import','generated','migrated','duplicated','system_preset')",
            name="assets_source_check",
        ),
        Index(
            "idx_assets_scope_type",
            "scope_id",
            "asset_type",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    scope_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    asset_type: Mapped[str] = mapped_column(Text, nullable=False)
    subtype: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role_tag: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    description: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    attrs: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    prompt_positive: Mapped[str | None] = mapped_column(Text)
    prompt_negative: Mapped[str | None] = mapped_column(Text)
    prompt_positive_zh: Mapped[str | None] = mapped_column(Text)
    prompt_negative_zh: Mapped[str | None] = mapped_column(Text)
    platform_params: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    cover_file_id: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'manual'")
    )
    duplicated_from: Mapped[int | None] = mapped_column(BigInteger)
    is_system_preset: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    tags: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class AssetLoadouts(Base):
    __tablename__ = "asset_loadouts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["asset_id"], ["public.assets.id"], ondelete="CASCADE", name="asset_loadouts_asset_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="asset_loadouts_pkey"),
        Index("idx_asset_loadouts_asset", "asset_id", "sort_order"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    asset_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    costume_ids: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False, server_default=text("'{}'::bigint[]")
    )
    prop_ids: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False, server_default=text("'{}'::bigint[]")
    )
    prompt_extra: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class AssetFiles(Base):
    __tablename__ = "asset_files"
    __table_args__ = (
        ForeignKeyConstraint(
            ["asset_id"], ["public.assets.id"], ondelete="CASCADE", name="asset_files_asset_id_fkey"
        ),
        ForeignKeyConstraint(
            ["resource_id"],
            ["public.resources.id"],
            ondelete="CASCADE",
            name="asset_files_resource_id_fkey",
        ),
        ForeignKeyConstraint(
            ["loadout_id"],
            ["public.asset_loadouts.id"],
            ondelete="SET NULL",
            name="asset_files_loadout_id_fkey",
        ),
        PrimaryKeyConstraint("asset_id", "resource_id", "slot", name="asset_files_pkey"),
        Index("idx_asset_files_resource", "resource_id"),
        {"schema": "public"},
    )

    asset_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    resource_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slot: Mapped[str] = mapped_column(Text, primary_key=True)
    loadout_id: Mapped[int | None] = mapped_column(BigInteger)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    note: Mapped[str | None] = mapped_column(Text)
    attached_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    attached_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class AssetLinks(Base):
    __tablename__ = "asset_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["from_asset_id"],
            ["public.assets.id"],
            ondelete="CASCADE",
            name="asset_links_from_asset_id_fkey",
        ),
        ForeignKeyConstraint(
            ["to_asset_id"], ["public.assets.id"], ondelete="CASCADE", name="asset_links_to_asset_id_fkey"
        ),
        PrimaryKeyConstraint("from_asset_id", "to_asset_id", "relation", name="asset_links_pkey"),
        CheckConstraint(
            "relation IN ('wears','holds','ambience_of','voice_of')",
            name="asset_links_relation_check",
        ),
        CheckConstraint("from_asset_id <> to_asset_id", name="asset_links_no_self"),
        Index("idx_asset_links_to", "to_asset_id", "relation"),
        {"schema": "public"},
    )

    from_asset_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    to_asset_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    relation: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class AssetProjectRefs(Base):
    __tablename__ = "asset_project_refs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["asset_id"], ["public.assets.id"], ondelete="CASCADE", name="asset_project_refs_asset_id_fkey"
        ),
        ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            ondelete="CASCADE",
            name="asset_project_refs_project_id_fkey",
        ),
        PrimaryKeyConstraint("asset_id", "project_id", name="asset_project_refs_pkey"),
        Index("idx_apr_project", "project_id"),
        {"schema": "public"},
    )

    asset_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    linked_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    linked_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class CanvasAssetRefs(Base):
    """Derived canvas→asset references (mig 445). Maintained by CanvasService
    in P4; rebuildable from nodes_json."""

    __tablename__ = "canvas_asset_refs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["canvas_id"], ["public.canvases.id"], ondelete="CASCADE", name="canvas_asset_refs_canvas_id_fkey"
        ),
        ForeignKeyConstraint(
            ["asset_id"], ["public.assets.id"], ondelete="CASCADE", name="canvas_asset_refs_asset_id_fkey"
        ),
        ForeignKeyConstraint(
            ["loadout_id"],
            ["public.asset_loadouts.id"],
            ondelete="SET NULL",
            name="canvas_asset_refs_loadout_id_fkey",
        ),
        PrimaryKeyConstraint("canvas_id", "asset_id", "node_id", name="canvas_asset_refs_pkey"),
        Index("idx_car_asset", "asset_id"),
        {"schema": "public"},
    )

    canvas_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    asset_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    node_id: Mapped[str] = mapped_column(Text, primary_key=True)
    loadout_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
```

Register in `backend/app/models/__init__.py` — next to the `generated_media` import block:

```python
from app.models.assets import (  # noqa: F401
    AssetFiles,
    AssetLinks,
    AssetLoadouts,
    AssetProjectRefs,
    Assets,
    CanvasAssetRefs,
)
```

and add the six names to `__all__` (alphabetical position near `"Assets"`).

In `backend/app/models/generated_media.py`, after `promoted_resource_id`:

```python
    # mig 446: Generated inbox state — 'unreviewed'|'saved'|'in_assets'|'deleted'.
    review_state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'unreviewed'")
    )
    # mig 446: the asset this generation was dispatched from (pre-fills Save-as-Asset).
    source_asset_id: Mapped[int | None] = mapped_column(BigInteger)
```

and add to `__table_args__`:

```python
        ForeignKeyConstraint(
            ["source_asset_id"],
            ["public.assets.id"],
            ondelete="SET NULL",
            name="generated_media_source_asset_id_fkey",
        ),
        CheckConstraint(
            "review_state IN ('unreviewed','saved','in_assets','deleted')",
            name="generated_media_review_state_check",
        ),
        Index("idx_genmedia_scope_state_created", "scope_id", "review_state", "created_at"),
```

(import `CheckConstraint` if not already imported). Also append `"source_asset_id"` to `_BIGINT_COLS` in `backend/app/repositories/generated_media_repository.py` and add `GeneratedMedia.review_state, GeneratedMedia.source_asset_id` to `_GM_COLS` so reads carry them.

In `backend/app/models/canvas.py`, class `Canvases`, after `kind`:

```python
    # mig 446: owning asset for an entity workshop canvas (decision 13). NULL for
    # ordinary canvases; project_id stays the project it was opened from.
    asset_id: Mapped[int | None] = mapped_column(BigInteger)
```

and in its `__table_args__` add:

```python
        ForeignKeyConstraint(
            ["asset_id"], ["public.assets.id"], ondelete="SET NULL", name="canvases_asset_id_fkey"
        ),
```

and update the `kind` CheckConstraint string there (if one exists in the model) to include `'costume'`.

- [ ] **Step 4: Run the import test + the whole models package import**

Run: `cd backend && uv run pytest tests/repositories/test_assets_repository_serialize.py -v && uv run python -c "import app.models; print('ok')"`
Expected: PASS, `ok`.

- [ ] **Step 5: Run schema-drift against the local DB (if available)**

Run: `cd backend && INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres uv run pytest tests/db/test_schema_drift.py -v`
Expected: all 6 gates PASS. Fix any nullability/type mismatch in the model (not the SQL) until green. Without a local DB, CI's `schema-drift.yml` is the gate — do not merge red.

- [ ] **Step 6: Commit**

```bash
cd backend && uv run black app/models/assets.py app/models/__init__.py app/models/generated_media.py app/models/canvas.py app/repositories/generated_media_repository.py && uv run isort app/models/assets.py
git add backend/app/models/assets.py backend/app/models/__init__.py backend/app/models/generated_media.py backend/app/models/canvas.py backend/app/repositories/generated_media_repository.py backend/tests/repositories/test_assets_repository_serialize.py
git commit -m "feat(assets): ORM models for asset library tables + generated_media/canvases extensions"
```

---

### Task 4: Slot table, readiness and link rules (pure functions)

**Files:**
- Create: `backend/app/services/assets/__init__.py` (empty)
- Create: `backend/app/services/assets/slots.py`
- Test: `backend/tests/services/assets/__init__.py` (empty), `backend/tests/services/assets/test_slots.py`

**Interfaces:**
- Produces:
  - `PRIMARY_SLOT: dict[str, str | None]` — type → primary slot name (`prompt` → `None`)
  - `SLOTS: dict[str, tuple[str, ...]]` — type → all valid slot names (excluding `'unsorted'`)
  - `is_valid_slot(asset_type: str, slot: str) -> bool`
  - `readiness(asset_type: str, slot_counts: dict[str, int], prompt_positive: str | None) -> Readiness` where `Readiness = TypedDict(state: Literal['ready','draft'], missing: list[str])`
  - `LINK_RULES: dict[str, tuple[str, str]]` — relation → (from_type, to_type); `ambience_of` from-type is `'audio'` with subtype check done by `link_allowed`
  - `link_allowed(relation, from_type, from_subtype, to_type) -> bool`

- [ ] **Step 1: Write the tests**

```python
# backend/tests/services/assets/test_slots.py
import pytest

from app.services.assets.slots import (
    PRIMARY_SLOT,
    SLOTS,
    is_valid_slot,
    link_allowed,
    readiness,
)


def test_primary_slots_per_type():
    assert PRIMARY_SLOT == {
        "character": "sheet",
        "location": "establishing",
        "prop": "turnaround",
        "costume": "flat",
        "prompt": None,
        "audio": "primary",
    }


def test_every_type_has_slots_and_unsorted_is_always_valid():
    for t in PRIMARY_SLOT:
        assert SLOTS[t]
        assert is_valid_slot(t, "unsorted")
    assert is_valid_slot("character", "stills")
    assert not is_valid_slot("character", "flat")
    assert not is_valid_slot("nope", "sheet")


@pytest.mark.parametrize(
    "atype,counts,prompt,expected",
    [
        ("character", {"sheet": 1}, None, {"state": "ready", "missing": []}),
        ("character", {"stills": 5}, None, {"state": "draft", "missing": ["sheet"]}),
        ("character", {}, None, {"state": "draft", "missing": ["sheet"]}),
        ("location", {"establishing": 2, "keyframes": 3}, None, {"state": "ready", "missing": []}),
        ("costume", {"worn": 1}, None, {"state": "draft", "missing": ["flat"]}),
        ("audio", {"primary": 1}, None, {"state": "ready", "missing": []}),
        ("prompt", {}, "A multi-camera…", {"state": "ready", "missing": []}),
        ("prompt", {"examples": 2}, "   ", {"state": "draft", "missing": ["prompt_positive"]}),
    ],
)
def test_readiness(atype, counts, prompt, expected):
    assert readiness(atype, counts, prompt) == expected


def test_link_rules():
    assert link_allowed("wears", "character", None, "costume")
    assert not link_allowed("wears", "costume", None, "character")
    assert link_allowed("holds", "character", None, "prop")
    assert link_allowed("ambience_of", "audio", "sfx", "location")
    assert link_allowed("ambience_of", "audio", "music", "location")
    assert not link_allowed("ambience_of", "audio", "voice", "location")
    assert link_allowed("voice_of", "audio", "voice", "character")
    assert not link_allowed("voice_of", "audio", "sfx", "character")
    assert not link_allowed("wears", "character", None, "prop")
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && uv run pytest tests/services/assets/test_slots.py -v`
Expected: FAIL `ModuleNotFoundError: app.services.assets`.

- [ ] **Step 3: Implement**

```python
# backend/app/services/assets/slots.py
"""Slot table + readiness (spec §3.6) and link-type rules (spec §3.3).

Pure functions; no DB. Slots are code constants on purpose — users cannot
define slots in v1 (decision 15). readiness is DERIVED, never stored.
"""

from __future__ import annotations

from typing import Literal, TypedDict

# type → primary slot (drives readiness). prompt has no file slot: its body is
# the "primary".
PRIMARY_SLOT: dict[str, str | None] = {
    "character": "sheet",
    "location": "establishing",
    "prop": "turnaround",
    "costume": "flat",
    "prompt": None,
    "audio": "primary",
}

# type → every named slot (primary first). 'unsorted' is implicit for all.
SLOTS: dict[str, tuple[str, ...]] = {
    "character": ("sheet", "stills", "expressions", "extras", "worn"),
    "location": ("establishing", "keyframes", "details", "layout"),
    "prop": ("turnaround", "in_scene", "details"),
    "costume": ("flat", "worn", "details"),
    "prompt": ("examples",),
    "audio": ("primary", "variants"),
}

UNSORTED = "unsorted"


def is_valid_slot(asset_type: str, slot: str) -> bool:
    slots = SLOTS.get(asset_type)
    if slots is None:
        return False
    return slot == UNSORTED or slot in slots


class Readiness(TypedDict):
    state: Literal["ready", "draft"]
    missing: list[str]


def readiness(
    asset_type: str, slot_counts: dict[str, int], prompt_positive: str | None
) -> Readiness:
    """ready iff the primary slot has ≥1 file (prompt: non-blank body)."""
    primary = PRIMARY_SLOT.get(asset_type)
    if asset_type == "prompt":
        ok = bool(prompt_positive and prompt_positive.strip())
        return {"state": "ready" if ok else "draft", "missing": [] if ok else ["prompt_positive"]}
    if primary is None:
        return {"state": "draft", "missing": []}
    ok = slot_counts.get(primary, 0) > 0
    return {"state": "ready" if ok else "draft", "missing": [] if ok else [primary]}


# relation → (from_type, to_type). audio subtypes are checked in link_allowed.
LINK_RULES: dict[str, tuple[str, str]] = {
    "wears": ("character", "costume"),
    "holds": ("character", "prop"),
    "ambience_of": ("audio", "location"),
    "voice_of": ("audio", "character"),
}

_AUDIO_SUBTYPE_FOR_RELATION: dict[str, frozenset[str]] = {
    "ambience_of": frozenset({"sfx", "music"}),
    "voice_of": frozenset({"voice"}),
}


def link_allowed(
    relation: str, from_type: str, from_subtype: str | None, to_type: str
) -> bool:
    rule = LINK_RULES.get(relation)
    if rule is None or rule != (from_type, to_type):
        return False
    allowed_sub = _AUDIO_SUBTYPE_FOR_RELATION.get(relation)
    if allowed_sub is not None and (from_subtype or "") not in allowed_sub:
        return False
    return True
```

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/services/assets/test_slots.py -v`
Expected: 12 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/assets/__init__.py backend/app/services/assets/slots.py backend/tests/services/assets/__init__.py backend/tests/services/assets/test_slots.py
git commit -m "feat(assets): slot table, derived readiness and link-type rules"
```

---

### Task 5: Pydantic schemas

**Files:**
- Create: `backend/app/schemas/assets.py`
- Test: `backend/tests/services/assets/test_schemas.py`

**Interfaces:**
- Produces: `AssetType`, `LinkRelation`, `AssetCreate`, `AssetUpdate`, `AssetResponse`, `AssetDetailResponse`, `AttachFileRequest`, `AttachFilesBatchRequest`, `LinkRequest`, `LoadoutCreate`, `LoadoutUpdate`, `LoadoutResponse`, `ProjectRefRequest`, `AssetFileResponse`.

- [ ] **Step 1: Write the test**

```python
# backend/tests/services/assets/test_schemas.py
import pytest
from pydantic import ValidationError

from app.schemas.assets import AssetCreate, AssetUpdate, AttachFileRequest, LoadoutCreate


def test_asset_create_requires_name_and_valid_type():
    a = AssetCreate(asset_type="character", name="Sang Yao")
    assert a.role_tag == "" and a.attrs == {} and a.tags == {}
    with pytest.raises(ValidationError):
        AssetCreate(asset_type="video", name="x")
    with pytest.raises(ValidationError):
        AssetCreate(asset_type="prop", name="")


def test_asset_update_none_means_unchanged():
    u = AssetUpdate(description="new")
    assert u.model_dump(exclude_none=True) == {"description": "new"}


def test_attach_file_request_defaults_unsorted():
    r = AttachFileRequest(resource_id="727145299382534145")
    assert r.slot == "unsorted" and r.loadout_id is None


def test_loadout_create_ids_are_strings():
    lo = LoadoutCreate(name="Night raid", costume_ids=["1"], prop_ids=[])
    assert lo.costume_ids == ["1"]
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && uv run pytest tests/services/assets/test_schemas.py -v`
Expected: FAIL `ModuleNotFoundError: app.schemas.assets`.

- [ ] **Step 3: Implement**

```python
# backend/app/schemas/assets.py
"""Pydantic schemas for the asset library (mig 445/446).

Snowflake ids are strings at this boundary (bigIntSafeFetch discipline).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

AssetType = Literal["character", "location", "prop", "costume", "prompt", "audio"]
AssetSource = Literal[
    "manual", "script_import", "generated", "migrated", "duplicated", "system_preset"
]
LinkRelation = Literal["wears", "holds", "ambience_of", "voice_of"]
ReadinessState = Literal["ready", "draft"]


class AssetCreate(BaseModel):
    asset_type: AssetType
    name: str = Field(..., min_length=1, max_length=200)
    subtype: Optional[str] = Field(default=None, max_length=40)
    role_tag: str = Field(default="", max_length=40)
    description: str = Field(default="", max_length=20000)
    attrs: Dict[str, Any] = Field(default_factory=dict)
    prompt_positive: Optional[str] = Field(default=None, max_length=20000)
    prompt_negative: Optional[str] = Field(default=None, max_length=20000)
    prompt_positive_zh: Optional[str] = Field(default=None, max_length=20000)
    prompt_negative_zh: Optional[str] = Field(default=None, max_length=20000)
    platform_params: Dict[str, Any] = Field(default_factory=dict)
    tags: Dict[str, Any] = Field(default_factory=dict)
    source: AssetSource = "manual"


class AssetUpdate(BaseModel):
    """PATCH payload — None means "leave unchanged"."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    subtype: Optional[str] = Field(default=None, max_length=40)
    role_tag: Optional[str] = Field(default=None, max_length=40)
    description: Optional[str] = Field(default=None, max_length=20000)
    attrs: Optional[Dict[str, Any]] = None
    prompt_positive: Optional[str] = Field(default=None, max_length=20000)
    prompt_negative: Optional[str] = Field(default=None, max_length=20000)
    prompt_positive_zh: Optional[str] = Field(default=None, max_length=20000)
    prompt_negative_zh: Optional[str] = Field(default=None, max_length=20000)
    platform_params: Optional[Dict[str, Any]] = None
    cover_file_id: Optional[str] = None
    tags: Optional[Dict[str, Any]] = None
    sort_order: Optional[int] = None


class AssetReadiness(BaseModel):
    state: ReadinessState
    missing: List[str] = Field(default_factory=list)


class AssetResponse(BaseModel):
    id: str
    scope_id: str
    asset_type: AssetType
    subtype: Optional[str] = None
    name: str
    role_tag: str = ""
    description: str = ""
    attrs: Dict[str, Any] = Field(default_factory=dict)
    prompt_positive: Optional[str] = None
    prompt_negative: Optional[str] = None
    prompt_positive_zh: Optional[str] = None
    prompt_negative_zh: Optional[str] = None
    platform_params: Dict[str, Any] = Field(default_factory=dict)
    cover_file_id: Optional[str] = None
    source: AssetSource = "manual"
    duplicated_from: Optional[str] = None
    is_system_preset: bool = False
    tags: Dict[str, Any] = Field(default_factory=dict)
    sort_order: int = 0
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    # derived
    readiness: AssetReadiness
    file_counts_by_slot: Dict[str, int] = Field(default_factory=dict)
    project_ids: List[str] = Field(default_factory=list)
    loadout_count: int = 0


class AssetFileResponse(BaseModel):
    asset_id: str
    resource_id: str
    slot: str
    loadout_id: Optional[str] = None
    sort_order: int = 0
    note: Optional[str] = None
    attached_at: datetime


class AssetLinkResponse(BaseModel):
    from_asset_id: str
    to_asset_id: str
    relation: LinkRelation


class LoadoutCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    costume_ids: List[str] = Field(default_factory=list)
    prop_ids: List[str] = Field(default_factory=list)
    prompt_extra: Optional[str] = Field(default=None, max_length=20000)


class LoadoutUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    costume_ids: Optional[List[str]] = None
    prop_ids: Optional[List[str]] = None
    prompt_extra: Optional[str] = Field(default=None, max_length=20000)
    is_default: Optional[bool] = None
    sort_order: Optional[int] = None


class LoadoutResponse(BaseModel):
    id: str
    asset_id: str
    name: str
    is_default: bool
    costume_ids: List[str] = Field(default_factory=list)
    prop_ids: List[str] = Field(default_factory=list)
    prompt_extra: Optional[str] = None
    sort_order: int = 0
    created_at: datetime


class AssetDetailResponse(AssetResponse):
    files: List[AssetFileResponse] = Field(default_factory=list)
    links: List[AssetLinkResponse] = Field(default_factory=list)  # outgoing
    linked_by: List[AssetLinkResponse] = Field(default_factory=list)  # incoming
    loadouts: List[LoadoutResponse] = Field(default_factory=list)


class AttachFileRequest(BaseModel):
    resource_id: str
    slot: str = "unsorted"
    loadout_id: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=2000)


class AttachFilesBatchRequest(BaseModel):
    items: List[AttachFileRequest] = Field(..., min_length=1, max_length=200)


class LinkRequest(BaseModel):
    to_asset_id: str
    relation: LinkRelation


class ProjectRefRequest(BaseModel):
    project_id: str
```

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/services/assets/test_schemas.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/assets.py backend/tests/services/assets/test_schemas.py
git commit -m "feat(assets): pydantic schemas for assets API"
```

---

### Task 6: AssetsRepository

**Files:**
- Create: `backend/app/repositories/assets_repository.py`
- Test: `backend/tests/repositories/test_assets_repository_serialize.py` (extend)

**Interfaces:**
- Consumes: `Assets`, `AssetFiles`, `AssetProjectRefs`, `AssetLoadouts` models; `readiness()` from Task 4.
- Produces class `AssetsRepository` with:
  - `async def create(self, scope_id: int, fields: dict, created_by: str | None) -> dict` — raises `DuplicateAssetName(existing_id: int)` on unique violation
  - `async def get(self, asset_id: int, scope_id: int) -> dict | None` (soft-deleted excluded)
  - `async def list(self, scope_id: int, *, asset_type: str | None, project_id: int | None, q: str | None, limit: int, offset: int) -> list[dict]`
  - `async def update(self, asset_id: int, scope_id: int, fields: dict) -> dict | None`
  - `async def soft_delete(self, asset_id: int, scope_id: int) -> bool`
  - `async def find_by_name(self, scope_id: int, asset_type: str, name: str) -> dict | None`
  - `async def slot_counts(self, asset_ids: list[int]) -> dict[int, dict[str, int]]`
  - `async def project_ids(self, asset_ids: list[int]) -> dict[int, list[int]]`
  - `async def loadout_counts(self, asset_ids: list[int]) -> dict[int, int]`
  - module-level `_serialize(row: dict) -> dict` (BIGINT → str, datetime → ISO) and `_BIGINT_COLS`

- [ ] **Step 1: Extend the test file**

```python
# append to backend/tests/repositories/test_assets_repository_serialize.py
import datetime

from app.repositories.assets_repository import (
    DuplicateAssetName,
    _BIGINT_COLS,
    _serialize,
    with_derived,
)


def test_serialize_stringifies_every_bigint_and_isoformats_datetimes():
    now = datetime.datetime(2026, 8, 28, tzinfo=datetime.timezone.utc)
    row = {
        "id": 727145299382534145,
        "scope_id": 727145299382534200,
        "cover_file_id": None,
        "duplicated_from": 727145299382534201,
        "created_by": "11111111-1111-1111-1111-111111111111",
        "created_at": now,
        "attrs": {"k": 1},
        "name": "Sang Yao",
    }
    out = _serialize(row)
    assert out["id"] == "727145299382534145"
    assert out["duplicated_from"] == "727145299382534201"
    assert out["cover_file_id"] is None
    assert out["created_at"] == now.isoformat()
    assert out["attrs"] == {"k": 1}
    assert set(_BIGINT_COLS) >= {"id", "scope_id", "cover_file_id", "duplicated_from"}


def test_with_derived_attaches_readiness_counts_projects_loadouts():
    row = {"id": 5, "asset_type": "character", "prompt_positive": None}
    out = with_derived(
        row,
        slot_counts={5: {"sheet": 1, "stills": 3}},
        project_ids={5: [900, 901]},
        loadout_counts={5: 2},
    )
    assert out["readiness"] == {"state": "ready", "missing": []}
    assert out["file_counts_by_slot"] == {"sheet": 1, "stills": 3}
    assert out["project_ids"] == ["900", "901"]
    assert out["loadout_count"] == 2


def test_with_derived_missing_maps_default_to_draft():
    out = with_derived({"id": 6, "asset_type": "prop", "prompt_positive": None}, {}, {}, {})
    assert out["readiness"] == {"state": "draft", "missing": ["turnaround"]}
    assert out["project_ids"] == [] and out["loadout_count"] == 0


def test_duplicate_asset_name_carries_existing_id():
    err = DuplicateAssetName(existing_id=42)
    assert err.existing_id == 42 and "42" in str(err)
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && uv run pytest tests/repositories/test_assets_repository_serialize.py -v`
Expected: FAIL `ImportError … assets_repository`.

- [ ] **Step 3: Implement**

```python
# backend/app/repositories/assets_repository.py
"""Data access for ``assets`` (mig 445).

ORM-backed (read_scope/write_scope). Every method carries an explicit
``scope_id`` predicate — the model has no scope mixin (ProjectCharacters
stance), so tenancy lives here. Snowflake BIGINTs ride as strings at the API
boundary via ``_serialize``; the derived fields (readiness / counts) are
computed by ``with_derived`` from batch queries so list pages cost O(1) round
trips, not O(n).
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError

from app.db.session import read_scope, write_scope
from app.models import AssetFiles, AssetLoadouts, AssetProjectRefs, Assets
from app.services.assets.slots import readiness

_BIGINT_COLS = ("id", "scope_id", "cover_file_id", "duplicated_from")


class DuplicateAssetName(Exception):
    """uq_assets_scope_type_name hit — the router turns this into 409."""

    def __init__(self, existing_id: int):
        self.existing_id = existing_id
        super().__init__(f"asset with same name/type exists: {existing_id}")


def _row_dict(obj: Assets) -> Dict[str, Any]:
    return {col.name: getattr(obj, col.name) for col in obj.__table__.columns}


def _serialize(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, val in row.items():
        if key in _BIGINT_COLS and val is not None:
            out[key] = str(val)
        elif isinstance(val, datetime.datetime):
            out[key] = val.isoformat()
        elif key == "created_by" and val is not None:
            out[key] = str(val)
        else:
            out[key] = val
    return out


def with_derived(
    row: Dict[str, Any],
    slot_counts: Dict[int, Dict[str, int]],
    project_ids: Dict[int, List[int]],
    loadout_counts: Dict[int, int],
) -> Dict[str, Any]:
    """Attach readiness / file_counts_by_slot / project_ids / loadout_count.
    ``row['id']`` must be the native int here (call before _serialize)."""
    aid = int(row["id"])
    counts = slot_counts.get(aid, {})
    out = dict(row)
    out["readiness"] = readiness(row["asset_type"], counts, row.get("prompt_positive"))
    out["file_counts_by_slot"] = counts
    out["project_ids"] = [str(p) for p in project_ids.get(aid, [])]
    out["loadout_count"] = loadout_counts.get(aid, 0)
    return out


class AssetsRepository:
    TABLE = "assets"

    async def create(
        self, scope_id: int, fields: Dict[str, Any], created_by: Optional[str]
    ) -> Dict[str, Any]:
        try:
            async with write_scope() as session:
                obj = Assets(**fields, scope_id=int(scope_id), created_by=created_by)
                session.add(obj)
                await session.flush()
                await session.refresh(obj)
                return _row_dict(obj)
        except IntegrityError as e:
            if "uq_assets_scope_type_name" not in str(e.orig):
                raise
            existing = await self.find_by_name(
                scope_id, fields["asset_type"], fields["name"]
            )
            raise DuplicateAssetName(existing_id=int(existing["id"]) if existing else 0)

    async def find_by_name(
        self, scope_id: int, asset_type: str, name: str
    ) -> Optional[Dict[str, Any]]:
        stmt = (
            select(Assets)
            .where(Assets.scope_id == int(scope_id))
            .where(Assets.asset_type == asset_type)
            .where(func.lower(Assets.name) == name.lower())
            .where(Assets.deleted_at.is_(None))
            .limit(1)
        )
        async with read_scope() as session:
            obj = (await session.execute(stmt)).scalar_one_or_none()
        return _row_dict(obj) if obj else None

    async def get(self, asset_id: int, scope_id: int) -> Optional[Dict[str, Any]]:
        stmt = (
            select(Assets)
            .where(Assets.id == int(asset_id))
            .where(Assets.scope_id == int(scope_id))
            .where(Assets.deleted_at.is_(None))
        )
        async with read_scope() as session:
            obj = (await session.execute(stmt)).scalar_one_or_none()
        return _row_dict(obj) if obj else None

    async def list(
        self,
        scope_id: int,
        *,
        asset_type: Optional[str] = None,
        project_id: Optional[int] = None,
        q: Optional[str] = None,
        limit: int = 60,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        stmt = (
            select(Assets)
            .where(Assets.scope_id == int(scope_id))
            .where(Assets.deleted_at.is_(None))
        )
        if asset_type:
            stmt = stmt.where(Assets.asset_type == asset_type)
        if project_id is not None:
            stmt = stmt.where(
                Assets.id.in_(
                    select(AssetProjectRefs.asset_id).where(
                        AssetProjectRefs.project_id == int(project_id)
                    )
                )
            )
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(
                or_(Assets.name.ilike(like), Assets.description.ilike(like))
            )
        stmt = (
            stmt.order_by(Assets.sort_order.asc(), Assets.updated_at.desc(), Assets.id.desc())
            .limit(limit)
            .offset(max(0, int(offset)))
        )
        async with read_scope() as session:
            objs = (await session.execute(stmt)).scalars().all()
        return [_row_dict(o) for o in objs]

    async def update(
        self, asset_id: int, scope_id: int, fields: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if not fields:
            return await self.get(asset_id, scope_id)
        fields = {**fields, "updated_at": datetime.datetime.now(datetime.timezone.utc)}
        try:
            async with write_scope() as session:
                res = await session.execute(
                    sa_update(Assets)
                    .where(Assets.id == int(asset_id))
                    .where(Assets.scope_id == int(scope_id))
                    .where(Assets.deleted_at.is_(None))
                    .values(**fields)
                    .returning(Assets)
                )
                obj = res.scalar_one_or_none()
                return _row_dict(obj) if obj else None
        except IntegrityError as e:
            if "uq_assets_scope_type_name" not in str(e.orig):
                raise
            current = await self.get(asset_id, scope_id)
            existing = await self.find_by_name(
                scope_id, current["asset_type"], fields["name"]
            ) if current else None
            raise DuplicateAssetName(existing_id=int(existing["id"]) if existing else 0)

    async def soft_delete(self, asset_id: int, scope_id: int) -> bool:
        async with write_scope() as session:
            res = await session.execute(
                sa_update(Assets)
                .where(Assets.id == int(asset_id))
                .where(Assets.scope_id == int(scope_id))
                .where(Assets.deleted_at.is_(None))
                .values(deleted_at=datetime.datetime.now(datetime.timezone.utc))
            )
            return (res.rowcount or 0) > 0

    # ── batch derived lookups (list pages) ────────────────────────────────

    async def slot_counts(self, asset_ids: List[int]) -> Dict[int, Dict[str, int]]:
        if not asset_ids:
            return {}
        stmt = (
            select(AssetFiles.asset_id, AssetFiles.slot, func.count())
            .where(AssetFiles.asset_id.in_([int(a) for a in asset_ids]))
            .group_by(AssetFiles.asset_id, AssetFiles.slot)
        )
        out: Dict[int, Dict[str, int]] = {}
        async with read_scope() as session:
            for aid, slot, n in (await session.execute(stmt)).all():
                out.setdefault(int(aid), {})[slot] = int(n)
        return out

    async def project_ids(self, asset_ids: List[int]) -> Dict[int, List[int]]:
        if not asset_ids:
            return {}
        stmt = select(AssetProjectRefs.asset_id, AssetProjectRefs.project_id).where(
            AssetProjectRefs.asset_id.in_([int(a) for a in asset_ids])
        )
        out: Dict[int, List[int]] = {}
        async with read_scope() as session:
            for aid, pid in (await session.execute(stmt)).all():
                out.setdefault(int(aid), []).append(int(pid))
        return out

    async def loadout_counts(self, asset_ids: List[int]) -> Dict[int, int]:
        if not asset_ids:
            return {}
        stmt = (
            select(AssetLoadouts.asset_id, func.count())
            .where(AssetLoadouts.asset_id.in_([int(a) for a in asset_ids]))
            .group_by(AssetLoadouts.asset_id)
        )
        async with read_scope() as session:
            return {int(a): int(n) for a, n in (await session.execute(stmt)).all()}
```

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/repositories/test_assets_repository_serialize.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
cd backend && uv run black app/repositories/assets_repository.py && uv run isort app/repositories/assets_repository.py
git add backend/app/repositories/assets_repository.py backend/tests/repositories/test_assets_repository_serialize.py
git commit -m "feat(assets): AssetsRepository with scope predicates, 409 duplicate detection, batch derived counts"
```

---

### Task 7: Relations repository (files / links / loadouts / project refs)

**Files:**
- Create: `backend/app/repositories/asset_relations_repository.py`
- Test: `backend/tests/repositories/test_asset_relations_repository.py`

**Interfaces:**
- Produces class `AssetRelationsRepository` with:
  - files: `attach(asset_id, resource_id, slot, *, loadout_id, note, attached_by) -> dict` (upsert on PK — re-attach updates loadout/note), `detach(asset_id, resource_id, slot) -> bool`, `list_files(asset_id) -> list[dict]`, `resource_in_scope(resource_id, scope_id) -> bool`
  - links: `add_link(from_id, to_id, relation) -> dict` (idempotent), `remove_link(from_id, to_id, relation) -> bool`, `list_links(asset_id) -> tuple[list[dict], list[dict]]` (outgoing, incoming), `link_targets(from_id, relation) -> set[int]`
  - loadouts: `create_loadout(asset_id, fields) -> dict`, `update_loadout(loadout_id, asset_id, fields) -> dict | None`, `delete_loadout(loadout_id, asset_id) -> bool`, `list_loadouts(asset_id) -> list[dict]`, `set_default(loadout_id, asset_id) -> None` (clears others in one transaction), `strip_from_loadouts(asset_id, *, costume_id=None, prop_id=None) -> int`
  - project refs: `link_project(asset_id, project_id, linked_by) -> bool`, `unlink_project(asset_id, project_id) -> bool`, `list_project_ids(asset_id) -> list[int]`
  - module-level `_serialize_file`, `_serialize_link`, `_serialize_loadout`

- [ ] **Step 1: Write serialization tests (no DB)**

```python
# backend/tests/repositories/test_asset_relations_repository.py
import datetime

from app.repositories.asset_relations_repository import (
    _serialize_file,
    _serialize_link,
    _serialize_loadout,
)

NOW = datetime.datetime(2026, 8, 28, tzinfo=datetime.timezone.utc)


def test_serialize_file_numeric_ids_become_strings():
    out = _serialize_file(
        {
            "asset_id": 727145299382534145,
            "resource_id": 727145299382534146,
            "slot": "sheet",
            "loadout_id": None,
            "sort_order": 0,
            "note": None,
            "attached_by": None,
            "attached_at": NOW,
        }
    )
    assert out["asset_id"] == "727145299382534145"
    assert out["resource_id"] == "727145299382534146"
    assert out["loadout_id"] is None
    assert out["attached_at"] == NOW.isoformat()


def test_serialize_link():
    out = _serialize_link({"from_asset_id": 1, "to_asset_id": 2, "relation": "wears", "created_at": NOW})
    assert out == {"from_asset_id": "1", "to_asset_id": "2", "relation": "wears", "created_at": NOW.isoformat()}


def test_serialize_loadout_arrays_become_string_lists():
    out = _serialize_loadout(
        {
            "id": 10,
            "asset_id": 5,
            "name": "Night raid",
            "is_default": False,
            "costume_ids": [727145299382534147],
            "prop_ids": [],
            "prompt_extra": None,
            "sort_order": 0,
            "created_at": NOW,
        }
    )
    assert out["id"] == "10" and out["asset_id"] == "5"
    assert out["costume_ids"] == ["727145299382534147"] and out["prop_ids"] == []
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && uv run pytest tests/repositories/test_asset_relations_repository.py -v`
Expected: FAIL import error.

- [ ] **Step 3: Implement**

```python
# backend/app/repositories/asset_relations_repository.py
"""Data access for asset_files / asset_links / asset_loadouts / asset_project_refs
(mig 445). Relation rows only — the invariants (slot validity, link types,
loadout ⊆ links) live in AssetsService; this layer is dumb and idempotent.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import (
    AssetFiles,
    AssetLinks,
    AssetLoadouts,
    AssetProjectRefs,
    ResourceItems,
)


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime.datetime) else v


def _serialize_file(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "asset_id": str(row["asset_id"]),
        "resource_id": str(row["resource_id"]),
        "slot": row["slot"],
        "loadout_id": str(row["loadout_id"]) if row.get("loadout_id") is not None else None,
        "sort_order": row.get("sort_order", 0),
        "note": row.get("note"),
        "attached_by": str(row["attached_by"]) if row.get("attached_by") else None,
        "attached_at": _iso(row.get("attached_at")),
    }


def _serialize_link(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "from_asset_id": str(row["from_asset_id"]),
        "to_asset_id": str(row["to_asset_id"]),
        "relation": row["relation"],
        "created_at": _iso(row.get("created_at")),
    }


def _serialize_loadout(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(row["id"]),
        "asset_id": str(row["asset_id"]),
        "name": row["name"],
        "is_default": bool(row["is_default"]),
        "costume_ids": [str(c) for c in (row.get("costume_ids") or [])],
        "prop_ids": [str(p) for p in (row.get("prop_ids") or [])],
        "prompt_extra": row.get("prompt_extra"),
        "sort_order": row.get("sort_order", 0),
        "created_at": _iso(row.get("created_at")),
    }


def _row(obj) -> Dict[str, Any]:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


class AssetRelationsRepository:
    # ── files ──────────────────────────────────────────────────────────────

    async def resource_in_scope(self, resource_id: int, scope_id: int) -> bool:
        stmt = (
            select(ResourceItems.resource_id)
            .where(ResourceItems.resource_id == int(resource_id))
            .where(ResourceItems.scope_id == int(scope_id))
            .limit(1)
        )
        async with read_scope() as session:
            return (await session.execute(stmt)).first() is not None

    async def attach(
        self,
        asset_id: int,
        resource_id: int,
        slot: str,
        *,
        loadout_id: Optional[int] = None,
        note: Optional[str] = None,
        attached_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        stmt = pg_insert(AssetFiles).values(
            asset_id=int(asset_id),
            resource_id=int(resource_id),
            slot=slot,
            loadout_id=int(loadout_id) if loadout_id is not None else None,
            note=note,
            attached_by=attached_by,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[AssetFiles.asset_id, AssetFiles.resource_id, AssetFiles.slot],
            set_={"loadout_id": stmt.excluded.loadout_id, "note": stmt.excluded.note},
        ).returning(AssetFiles)
        async with write_scope() as session:
            obj = (await session.execute(stmt)).scalar_one()
            return _row(obj)

    async def detach(self, asset_id: int, resource_id: int, slot: str) -> bool:
        async with write_scope() as session:
            res = await session.execute(
                sa_delete(AssetFiles)
                .where(AssetFiles.asset_id == int(asset_id))
                .where(AssetFiles.resource_id == int(resource_id))
                .where(AssetFiles.slot == slot)
            )
            return (res.rowcount or 0) > 0

    async def list_files(self, asset_id: int) -> List[Dict[str, Any]]:
        stmt = (
            select(AssetFiles)
            .where(AssetFiles.asset_id == int(asset_id))
            .order_by(AssetFiles.slot.asc(), AssetFiles.sort_order.asc(), AssetFiles.attached_at.asc())
        )
        async with read_scope() as session:
            return [_row(o) for o in (await session.execute(stmt)).scalars().all()]

    # ── links ──────────────────────────────────────────────────────────────

    async def add_link(self, from_id: int, to_id: int, relation: str) -> Dict[str, Any]:
        stmt = (
            pg_insert(AssetLinks)
            .values(from_asset_id=int(from_id), to_asset_id=int(to_id), relation=relation)
            .on_conflict_do_nothing()
        )
        async with write_scope() as session:
            await session.execute(stmt)
            obj = (
                await session.execute(
                    select(AssetLinks)
                    .where(AssetLinks.from_asset_id == int(from_id))
                    .where(AssetLinks.to_asset_id == int(to_id))
                    .where(AssetLinks.relation == relation)
                )
            ).scalar_one()
            return _row(obj)

    async def remove_link(self, from_id: int, to_id: int, relation: str) -> bool:
        async with write_scope() as session:
            res = await session.execute(
                sa_delete(AssetLinks)
                .where(AssetLinks.from_asset_id == int(from_id))
                .where(AssetLinks.to_asset_id == int(to_id))
                .where(AssetLinks.relation == relation)
            )
            return (res.rowcount or 0) > 0

    async def list_links(
        self, asset_id: int
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        async with read_scope() as session:
            out = (
                await session.execute(
                    select(AssetLinks).where(AssetLinks.from_asset_id == int(asset_id))
                )
            ).scalars().all()
            inc = (
                await session.execute(
                    select(AssetLinks).where(AssetLinks.to_asset_id == int(asset_id))
                )
            ).scalars().all()
        return [_row(o) for o in out], [_row(o) for o in inc]

    async def link_targets(self, from_id: int, relation: str) -> Set[int]:
        stmt = (
            select(AssetLinks.to_asset_id)
            .where(AssetLinks.from_asset_id == int(from_id))
            .where(AssetLinks.relation == relation)
        )
        async with read_scope() as session:
            return {int(t) for (t,) in (await session.execute(stmt)).all()}

    # ── loadouts ───────────────────────────────────────────────────────────

    async def create_loadout(self, asset_id: int, fields: Dict[str, Any]) -> Dict[str, Any]:
        async with write_scope() as session:
            obj = AssetLoadouts(asset_id=int(asset_id), **fields)
            session.add(obj)
            await session.flush()
            await session.refresh(obj)
            return _row(obj)

    async def update_loadout(
        self, loadout_id: int, asset_id: int, fields: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if not fields:
            rows = await self.list_loadouts(asset_id)
            return next((r for r in rows if int(r["id"]) == int(loadout_id)), None)
        async with write_scope() as session:
            res = await session.execute(
                sa_update(AssetLoadouts)
                .where(AssetLoadouts.id == int(loadout_id))
                .where(AssetLoadouts.asset_id == int(asset_id))
                .values(**fields)
                .returning(AssetLoadouts)
            )
            obj = res.scalar_one_or_none()
            return _row(obj) if obj else None

    async def delete_loadout(self, loadout_id: int, asset_id: int) -> bool:
        async with write_scope() as session:
            res = await session.execute(
                sa_delete(AssetLoadouts)
                .where(AssetLoadouts.id == int(loadout_id))
                .where(AssetLoadouts.asset_id == int(asset_id))
                .where(AssetLoadouts.is_default.is_(False))
            )
            return (res.rowcount or 0) > 0

    async def list_loadouts(self, asset_id: int) -> List[Dict[str, Any]]:
        stmt = (
            select(AssetLoadouts)
            .where(AssetLoadouts.asset_id == int(asset_id))
            .order_by(AssetLoadouts.is_default.desc(), AssetLoadouts.sort_order.asc(), AssetLoadouts.id.asc())
        )
        async with read_scope() as session:
            return [_row(o) for o in (await session.execute(stmt)).scalars().all()]

    async def set_default(self, loadout_id: int, asset_id: int) -> None:
        async with write_scope() as session:
            await session.execute(
                sa_update(AssetLoadouts)
                .where(AssetLoadouts.asset_id == int(asset_id))
                .values(is_default=False)
            )
            await session.execute(
                sa_update(AssetLoadouts)
                .where(AssetLoadouts.id == int(loadout_id))
                .where(AssetLoadouts.asset_id == int(asset_id))
                .values(is_default=True)
            )

    async def strip_from_loadouts(
        self, asset_id: int, *, costume_id: Optional[int] = None, prop_id: Optional[int] = None
    ) -> int:
        """Remove a costume/prop id from every loadout of ``asset_id`` (called
        when the corresponding link is removed). Returns rows touched."""
        touched = 0
        rows = await self.list_loadouts(asset_id)
        async with write_scope() as session:
            for r in rows:
                new_c = [c for c in r["costume_ids"] if costume_id is None or int(c) != int(costume_id)]
                new_p = [p for p in r["prop_ids"] if prop_id is None or int(p) != int(prop_id)]
                if new_c != list(r["costume_ids"]) or new_p != list(r["prop_ids"]):
                    await session.execute(
                        sa_update(AssetLoadouts)
                        .where(AssetLoadouts.id == int(r["id"]))
                        .values(costume_ids=new_c, prop_ids=new_p)
                    )
                    touched += 1
        return touched

    # ── project refs ───────────────────────────────────────────────────────

    async def link_project(self, asset_id: int, project_id: int, linked_by: Optional[str]) -> bool:
        stmt = (
            pg_insert(AssetProjectRefs)
            .values(asset_id=int(asset_id), project_id=int(project_id), linked_by=linked_by)
            .on_conflict_do_nothing()
        )
        async with write_scope() as session:
            res = await session.execute(stmt)
            return (res.rowcount or 0) > 0

    async def unlink_project(self, asset_id: int, project_id: int) -> bool:
        async with write_scope() as session:
            res = await session.execute(
                sa_delete(AssetProjectRefs)
                .where(AssetProjectRefs.asset_id == int(asset_id))
                .where(AssetProjectRefs.project_id == int(project_id))
            )
            return (res.rowcount or 0) > 0

    async def list_project_ids(self, asset_id: int) -> List[int]:
        stmt = select(AssetProjectRefs.project_id).where(AssetProjectRefs.asset_id == int(asset_id))
        async with read_scope() as session:
            return [int(p) for (p,) in (await session.execute(stmt)).all()]
```

Verified: `ResourceItems` (app/models, `__tablename__ = "resource_items"`) exposes `resource_id` and `scope_id`.

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/repositories/test_asset_relations_repository.py -v && uv run python -c "import app.repositories.asset_relations_repository"`
Expected: 3 PASS, import ok.

- [ ] **Step 5: Commit**

```bash
cd backend && uv run black app/repositories/asset_relations_repository.py && uv run isort app/repositories/asset_relations_repository.py
git add backend/app/repositories/asset_relations_repository.py backend/tests/repositories/test_asset_relations_repository.py
git commit -m "feat(assets): relations repository — files/links/loadouts/project refs"
```

---

### Task 8: AssetsService — invariants

**Files:**
- Create: `backend/app/services/assets/assets_service.py`
- Test: `backend/tests/services/assets/test_assets_service.py`

**Interfaces:**
- Consumes: `AssetsRepository`, `AssetRelationsRepository`, `slots.py`.
- Produces class `AssetsService(assets_repo=None, relations_repo=None)` with typed errors `AssetError(status: int, code: str, detail: str, extra: dict)` and methods:
  - `create_asset(scope_id, payload: AssetCreate, user_id) -> dict` — creates; for `character` also creates `Default` loadout (`is_default=True`); on `DuplicateAssetName` raises `AssetError(409, "asset_exists", …, {"existing_asset_id": str})`
  - `get_asset(asset_id, scope_id) -> dict` (detail: files/links/linked_by/loadouts + derived) or `AssetError(404, "asset_not_found")`
  - `list_assets(scope_id, **filters) -> list[dict]` (serialized + derived)
  - `update_asset(asset_id, scope_id, payload: AssetUpdate) -> dict` — `AssetError(403, "system_preset_readonly")` if `is_system_preset`
  - `delete_asset(asset_id, scope_id) -> None`
  - `attach_file(asset_id, scope_id, req: AttachFileRequest, user_id) -> dict` — validates slot (`422 invalid_slot`), resource in scope (`404 resource_not_found`), loadout belongs to asset (`422 loadout_mismatch`)
  - `detach_file(asset_id, scope_id, resource_id, slot) -> None`
  - `add_link(asset_id, scope_id, req: LinkRequest) -> dict` — both assets in scope, `link_allowed` else `422 link_not_allowed`
  - `remove_link(asset_id, scope_id, to_asset_id, relation) -> None` — also `strip_from_loadouts`
  - `create_loadout(asset_id, scope_id, payload: LoadoutCreate) -> dict` — asset must be `character` (`422 loadouts_character_only`); costume/prop ids ⊆ link targets (`422 loadout_not_subset`)
  - `update_loadout(asset_id, scope_id, loadout_id, payload: LoadoutUpdate) -> dict`
  - `delete_loadout(asset_id, scope_id, loadout_id) -> None` (`422 cannot_delete_default`)
  - `link_project(asset_id, scope_id, project_id, user_id) -> None` / `unlink_project(...) -> None`

- [ ] **Step 1: Write the tests with fake repos**

```python
# backend/tests/services/assets/test_assets_service.py
"""AssetsService invariants with in-memory fake repos (no DB)."""

from __future__ import annotations

import datetime

import pytest

from app.repositories.assets_repository import DuplicateAssetName
from app.schemas.assets import AssetCreate, AttachFileRequest, LinkRequest, LoadoutCreate
from app.services.assets.assets_service import AssetError, AssetsService

NOW = datetime.datetime(2026, 8, 28, tzinfo=datetime.timezone.utc)
SCOPE = 727145299382534200
USER = "11111111-1111-1111-1111-111111111111"


class FakeAssetsRepo:
    def __init__(self):
        self.rows: dict[int, dict] = {}
        self._next = 1000

    def _row(self, **kw):
        base = {
            "subtype": None, "role_tag": "", "description": "", "attrs": {},
            "prompt_positive": None, "prompt_negative": None, "prompt_positive_zh": None,
            "prompt_negative_zh": None, "platform_params": {}, "cover_file_id": None,
            "source": "manual", "duplicated_from": None, "is_system_preset": False,
            "tags": {}, "sort_order": 0, "created_by": USER, "created_at": NOW,
            "updated_at": NOW, "deleted_at": None,
        }
        base.update(kw)
        return base

    async def create(self, scope_id, fields, created_by):
        for r in self.rows.values():
            if r["asset_type"] == fields["asset_type"] and r["name"].lower() == fields["name"].lower():
                raise DuplicateAssetName(existing_id=r["id"])
        self._next += 1
        row = self._row(id=self._next, scope_id=scope_id, **fields)
        self.rows[self._next] = row
        return row

    async def get(self, asset_id, scope_id):
        r = self.rows.get(int(asset_id))
        return r if r and r["scope_id"] == scope_id else None

    async def list(self, scope_id, **kw):
        return [r for r in self.rows.values() if r["scope_id"] == scope_id]

    async def update(self, asset_id, scope_id, fields):
        self.rows[int(asset_id)].update(fields)
        return self.rows[int(asset_id)]

    async def soft_delete(self, asset_id, scope_id):
        return self.rows.pop(int(asset_id), None) is not None

    async def slot_counts(self, ids):
        return {}

    async def project_ids(self, ids):
        return {}

    async def loadout_counts(self, ids):
        return {}


class FakeRelationsRepo:
    def __init__(self):
        self.files, self.links, self.loadouts, self.refs = [], [], {}, []
        self._next = 5000
        self.in_scope_resources = {727145299382534146}

    async def resource_in_scope(self, resource_id, scope_id):
        return int(resource_id) in self.in_scope_resources

    async def attach(self, asset_id, resource_id, slot, **kw):
        row = {"asset_id": asset_id, "resource_id": resource_id, "slot": slot,
               "loadout_id": kw.get("loadout_id"), "sort_order": 0, "note": kw.get("note"),
               "attached_by": kw.get("attached_by"), "attached_at": NOW}
        self.files.append(row)
        return row

    async def detach(self, asset_id, resource_id, slot):
        return True

    async def list_files(self, asset_id):
        return [f for f in self.files if f["asset_id"] == asset_id]

    async def add_link(self, f, t, rel):
        row = {"from_asset_id": f, "to_asset_id": t, "relation": rel, "created_at": NOW}
        self.links.append(row)
        return row

    async def remove_link(self, f, t, rel):
        return True

    async def list_links(self, asset_id):
        return ([l for l in self.links if l["from_asset_id"] == asset_id],
                [l for l in self.links if l["to_asset_id"] == asset_id])

    async def link_targets(self, f, rel):
        return {l["to_asset_id"] for l in self.links if l["from_asset_id"] == f and l["relation"] == rel}

    async def create_loadout(self, asset_id, fields):
        self._next += 1
        row = {"id": self._next, "asset_id": asset_id, "is_default": False, "costume_ids": [],
               "prop_ids": [], "prompt_extra": None, "sort_order": 0, "created_at": NOW, **fields}
        self.loadouts[self._next] = row
        return row

    async def update_loadout(self, lid, asset_id, fields):
        self.loadouts[lid].update(fields)
        return self.loadouts[lid]

    async def delete_loadout(self, lid, asset_id):
        row = self.loadouts.get(lid)
        if not row or row["is_default"]:
            return False
        del self.loadouts[lid]
        return True

    async def list_loadouts(self, asset_id):
        return [l for l in self.loadouts.values() if l["asset_id"] == asset_id]

    async def set_default(self, lid, asset_id):
        for l in self.loadouts.values():
            l["is_default"] = l["id"] == lid

    async def strip_from_loadouts(self, asset_id, costume_id=None, prop_id=None):
        return 0

    async def link_project(self, a, p, u):
        self.refs.append((a, p))
        return True

    async def unlink_project(self, a, p):
        return True

    async def list_project_ids(self, a):
        return [p for (x, p) in self.refs if x == a]


@pytest.fixture
def svc():
    return AssetsService(assets_repo=FakeAssetsRepo(), relations_repo=FakeRelationsRepo())


@pytest.mark.asyncio
async def test_create_character_creates_default_loadout(svc):
    a = await svc.create_asset(SCOPE, AssetCreate(asset_type="character", name="Sang Yao"), USER)
    assert a["id"].isdigit()  # serialized
    los = await svc.relations.list_loadouts(int(a["id"]))
    assert len(los) == 1 and los[0]["is_default"] and los[0]["name"] == "Default"


@pytest.mark.asyncio
async def test_create_prop_has_no_loadout(svc):
    a = await svc.create_asset(SCOPE, AssetCreate(asset_type="prop", name="Blade"), USER)
    assert await svc.relations.list_loadouts(int(a["id"])) == []


@pytest.mark.asyncio
async def test_duplicate_name_is_409_with_existing_id(svc):
    a = await svc.create_asset(SCOPE, AssetCreate(asset_type="location", name="Bamboo Grove"), USER)
    with pytest.raises(AssetError) as ei:
        await svc.create_asset(SCOPE, AssetCreate(asset_type="location", name="bamboo grove"), USER)
    assert ei.value.status == 409 and ei.value.code == "asset_exists"
    assert ei.value.extra["existing_asset_id"] == a["id"]


@pytest.mark.asyncio
async def test_attach_validates_slot_and_scope(svc):
    a = await svc.create_asset(SCOPE, AssetCreate(asset_type="character", name="Yi Heng"), USER)
    with pytest.raises(AssetError) as ei:
        await svc.attach_file(int(a["id"]), SCOPE, AttachFileRequest(resource_id="727145299382534146", slot="flat"), USER)
    assert ei.value.status == 422 and ei.value.code == "invalid_slot"
    with pytest.raises(AssetError) as ei:
        await svc.attach_file(int(a["id"]), SCOPE, AttachFileRequest(resource_id="1", slot="sheet"), USER)
    assert ei.value.status == 404 and ei.value.code == "resource_not_found"
    f = await svc.attach_file(int(a["id"]), SCOPE, AttachFileRequest(resource_id="727145299382534146", slot="sheet"), USER)
    assert f["slot"] == "sheet" and f["resource_id"] == "727145299382534146"


@pytest.mark.asyncio
async def test_attach_loadout_must_belong_to_asset(svc):
    a = await svc.create_asset(SCOPE, AssetCreate(asset_type="character", name="A"), USER)
    b = await svc.create_asset(SCOPE, AssetCreate(asset_type="character", name="B"), USER)
    b_default = (await svc.relations.list_loadouts(int(b["id"])))[0]
    with pytest.raises(AssetError) as ei:
        await svc.attach_file(int(a["id"]), SCOPE, AttachFileRequest(resource_id="727145299382534146", slot="stills", loadout_id=str(b_default["id"])), USER)
    assert ei.value.code == "loadout_mismatch"


@pytest.mark.asyncio
async def test_link_type_rules_enforced(svc):
    c = await svc.create_asset(SCOPE, AssetCreate(asset_type="character", name="C"), USER)
    robe = await svc.create_asset(SCOPE, AssetCreate(asset_type="costume", name="Robe"), USER)
    blade = await svc.create_asset(SCOPE, AssetCreate(asset_type="prop", name="Blade"), USER)
    link = await svc.add_link(int(c["id"]), SCOPE, LinkRequest(to_asset_id=robe["id"], relation="wears"))
    assert link["relation"] == "wears"
    with pytest.raises(AssetError) as ei:
        await svc.add_link(int(c["id"]), SCOPE, LinkRequest(to_asset_id=blade["id"], relation="wears"))
    assert ei.value.code == "link_not_allowed"
    with pytest.raises(AssetError) as ei:
        await svc.add_link(int(robe["id"]), SCOPE, LinkRequest(to_asset_id=c["id"], relation="wears"))
    assert ei.value.code == "link_not_allowed"


@pytest.mark.asyncio
async def test_loadout_must_be_subset_of_links_and_character_only(svc):
    c = await svc.create_asset(SCOPE, AssetCreate(asset_type="character", name="C"), USER)
    robe = await svc.create_asset(SCOPE, AssetCreate(asset_type="costume", name="Robe"), USER)
    hood = await svc.create_asset(SCOPE, AssetCreate(asset_type="costume", name="Hood"), USER)
    await svc.add_link(int(c["id"]), SCOPE, LinkRequest(to_asset_id=robe["id"], relation="wears"))
    lo = await svc.create_loadout(int(c["id"]), SCOPE, LoadoutCreate(name="Day", costume_ids=[robe["id"]]))
    assert lo["costume_ids"] == [robe["id"]] and lo["is_default"] is False
    with pytest.raises(AssetError) as ei:
        await svc.create_loadout(int(c["id"]), SCOPE, LoadoutCreate(name="Night", costume_ids=[hood["id"]]))
    assert ei.value.code == "loadout_not_subset"
    with pytest.raises(AssetError) as ei:
        await svc.create_loadout(int(robe["id"]), SCOPE, LoadoutCreate(name="x"))
    assert ei.value.code == "loadouts_character_only"


@pytest.mark.asyncio
async def test_cannot_delete_default_loadout(svc):
    c = await svc.create_asset(SCOPE, AssetCreate(asset_type="character", name="C"), USER)
    default = (await svc.relations.list_loadouts(int(c["id"])))[0]
    with pytest.raises(AssetError) as ei:
        await svc.delete_loadout(int(c["id"]), SCOPE, int(default["id"]))
    assert ei.value.code == "cannot_delete_default"


@pytest.mark.asyncio
async def test_get_detail_composes_files_links_loadouts_and_readiness(svc):
    c = await svc.create_asset(SCOPE, AssetCreate(asset_type="character", name="C"), USER)
    d = await svc.get_asset(int(c["id"]), SCOPE)
    assert d["readiness"] == {"state": "draft", "missing": ["sheet"]}
    assert d["files"] == [] and d["links"] == [] and len(d["loadouts"]) == 1
    with pytest.raises(AssetError) as ei:
        await svc.get_asset(999, SCOPE)
    assert ei.value.status == 404


@pytest.mark.asyncio
async def test_system_preset_is_readonly(svc):
    p = await svc.create_asset(SCOPE, AssetCreate(asset_type="prompt", name="Grid", source="system_preset"), USER)
    svc.assets.rows[int(p["id"])]["is_system_preset"] = True
    from app.schemas.assets import AssetUpdate
    with pytest.raises(AssetError) as ei:
        await svc.update_asset(int(p["id"]), SCOPE, AssetUpdate(description="x"))
    assert ei.value.status == 403 and ei.value.code == "system_preset_readonly"
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && uv run pytest tests/services/assets/test_assets_service.py -v`
Expected: FAIL import error.

- [ ] **Step 3: Implement**

```python
# backend/app/services/assets/assets_service.py
"""AssetsService — the invariants layer (spec §3, §7).

Repos are dumb; this is where slot validity, link-type rules, loadout ⊆ links,
default-loadout-on-character, 409-on-duplicate and system-preset read-only are
enforced. Every failure is a typed AssetError the router maps 1:1 to HTTP —
no silent no-ops (CLAUDE.md "触发路径必须类型化失败回显").
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.repositories.asset_relations_repository import (
    AssetRelationsRepository,
    _serialize_file,
    _serialize_link,
    _serialize_loadout,
)
from app.repositories.assets_repository import (
    AssetsRepository,
    DuplicateAssetName,
    _serialize,
    with_derived,
)
from app.schemas.assets import (
    AssetCreate,
    AssetUpdate,
    AttachFileRequest,
    LinkRequest,
    LoadoutCreate,
    LoadoutUpdate,
)
from app.services.assets.slots import is_valid_slot, link_allowed


class AssetError(Exception):
    def __init__(self, status: int, code: str, detail: str, extra: Optional[Dict[str, Any]] = None):
        self.status, self.code, self.detail = status, code, detail
        self.extra = extra or {}
        super().__init__(f"{code}: {detail}")


class AssetsService:
    def __init__(
        self,
        assets_repo: Optional[AssetsRepository] = None,
        relations_repo: Optional[AssetRelationsRepository] = None,
    ):
        self.assets = assets_repo or AssetsRepository()
        self.relations = relations_repo or AssetRelationsRepository()

    # ── helpers ────────────────────────────────────────────────────────────

    async def _require(self, asset_id: int, scope_id: int) -> Dict[str, Any]:
        row = await self.assets.get(int(asset_id), int(scope_id))
        if not row:
            raise AssetError(404, "asset_not_found", "Asset not found")
        return row

    async def _derived(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ids = [int(r["id"]) for r in rows]
        sc, pi, lc = (
            await self.assets.slot_counts(ids),
            await self.assets.project_ids(ids),
            await self.assets.loadout_counts(ids),
        )
        return [_serialize(with_derived(r, sc, pi, lc)) for r in rows]

    # ── assets ─────────────────────────────────────────────────────────────

    async def create_asset(self, scope_id: int, payload: AssetCreate, user_id: Optional[str]) -> Dict[str, Any]:
        fields = payload.model_dump()
        try:
            row = await self.assets.create(int(scope_id), fields, user_id)
        except DuplicateAssetName as e:
            raise AssetError(
                409,
                "asset_exists",
                "An asset with this name and type already exists in this scope",
                {"existing_asset_id": str(e.existing_id)},
            )
        if row["asset_type"] == "character":
            await self.relations.create_loadout(int(row["id"]), {"name": "Default", "is_default": True})
        return (await self._derived([row]))[0]

    async def list_assets(self, scope_id: int, **filters) -> List[Dict[str, Any]]:
        rows = await self.assets.list(int(scope_id), **filters)
        return await self._derived(rows)

    async def get_asset(self, asset_id: int, scope_id: int) -> Dict[str, Any]:
        row = await self._require(asset_id, scope_id)
        out = (await self._derived([row]))[0]
        files = await self.relations.list_files(int(asset_id))
        outgoing, incoming = await self.relations.list_links(int(asset_id))
        loadouts = await self.relations.list_loadouts(int(asset_id))
        out["files"] = [_serialize_file(f) for f in files]
        out["links"] = [_serialize_link(l) for l in outgoing]
        out["linked_by"] = [_serialize_link(l) for l in incoming]
        out["loadouts"] = [_serialize_loadout(l) for l in loadouts]
        return out

    async def update_asset(self, asset_id: int, scope_id: int, payload: AssetUpdate) -> Dict[str, Any]:
        row = await self._require(asset_id, scope_id)
        if row.get("is_system_preset"):
            raise AssetError(403, "system_preset_readonly", "System presets are read-only; duplicate to edit")
        fields = payload.model_dump(exclude_none=True)
        if "cover_file_id" in fields:
            fields["cover_file_id"] = int(fields["cover_file_id"])
        try:
            updated = await self.assets.update(int(asset_id), int(scope_id), fields)
        except DuplicateAssetName as e:
            raise AssetError(409, "asset_exists", "Name already used by another asset of this type",
                             {"existing_asset_id": str(e.existing_id)})
        return (await self._derived([updated]))[0]

    async def delete_asset(self, asset_id: int, scope_id: int) -> None:
        await self._require(asset_id, scope_id)
        await self.assets.soft_delete(int(asset_id), int(scope_id))

    # ── files ──────────────────────────────────────────────────────────────

    async def attach_file(self, asset_id: int, scope_id: int, req: AttachFileRequest, user_id: Optional[str]) -> Dict[str, Any]:
        row = await self._require(asset_id, scope_id)
        if not is_valid_slot(row["asset_type"], req.slot):
            raise AssetError(422, "invalid_slot", f"Slot '{req.slot}' is not valid for {row['asset_type']}")
        if not await self.relations.resource_in_scope(int(req.resource_id), int(scope_id)):
            raise AssetError(404, "resource_not_found", "Resource not found in this scope")
        loadout_id = int(req.loadout_id) if req.loadout_id else None
        if loadout_id is not None:
            owned = {int(l["id"]) for l in await self.relations.list_loadouts(int(asset_id))}
            if loadout_id not in owned:
                raise AssetError(422, "loadout_mismatch", "Loadout does not belong to this asset")
        f = await self.relations.attach(
            int(asset_id), int(req.resource_id), req.slot,
            loadout_id=loadout_id, note=req.note, attached_by=user_id,
        )
        return _serialize_file(f)

    async def detach_file(self, asset_id: int, scope_id: int, resource_id: int, slot: str) -> None:
        await self._require(asset_id, scope_id)
        if not await self.relations.detach(int(asset_id), int(resource_id), slot):
            raise AssetError(404, "file_not_attached", "File is not attached to this slot")

    # ── links ──────────────────────────────────────────────────────────────

    async def add_link(self, asset_id: int, scope_id: int, req: LinkRequest) -> Dict[str, Any]:
        src = await self._require(asset_id, scope_id)
        dst = await self.assets.get(int(req.to_asset_id), int(scope_id))
        if not dst:
            raise AssetError(404, "asset_not_found", "Target asset not found")
        if not link_allowed(req.relation, src["asset_type"], src.get("subtype"), dst["asset_type"]):
            raise AssetError(
                422, "link_not_allowed",
                f"{req.relation} is not allowed from {src['asset_type']}/{src.get('subtype') or '-'} to {dst['asset_type']}",
            )
        return _serialize_link(await self.relations.add_link(int(asset_id), int(req.to_asset_id), req.relation))

    async def remove_link(self, asset_id: int, scope_id: int, to_asset_id: int, relation: str) -> None:
        await self._require(asset_id, scope_id)
        if not await self.relations.remove_link(int(asset_id), int(to_asset_id), relation):
            raise AssetError(404, "link_not_found", "Link not found")
        if relation == "wears":
            await self.relations.strip_from_loadouts(int(asset_id), costume_id=int(to_asset_id))
        elif relation == "holds":
            await self.relations.strip_from_loadouts(int(asset_id), prop_id=int(to_asset_id))

    # ── loadouts ───────────────────────────────────────────────────────────

    async def _check_subset(self, asset_id: int, costume_ids: List[str], prop_ids: List[str]) -> None:
        wears = await self.relations.link_targets(int(asset_id), "wears")
        holds = await self.relations.link_targets(int(asset_id), "holds")
        bad_c = [c for c in costume_ids if int(c) not in wears]
        bad_p = [p for p in prop_ids if int(p) not in holds]
        if bad_c or bad_p:
            raise AssetError(422, "loadout_not_subset",
                             "Loadout may only reference costumes/props linked to this character",
                             {"costume_ids": bad_c, "prop_ids": bad_p})

    async def create_loadout(self, asset_id: int, scope_id: int, payload: LoadoutCreate) -> Dict[str, Any]:
        row = await self._require(asset_id, scope_id)
        if row["asset_type"] != "character":
            raise AssetError(422, "loadouts_character_only", "Only characters have loadouts")
        await self._check_subset(asset_id, payload.costume_ids, payload.prop_ids)
        lo = await self.relations.create_loadout(int(asset_id), {
            "name": payload.name,
            "costume_ids": [int(c) for c in payload.costume_ids],
            "prop_ids": [int(p) for p in payload.prop_ids],
            "prompt_extra": payload.prompt_extra,
        })
        return _serialize_loadout(lo)

    async def update_loadout(self, asset_id: int, scope_id: int, loadout_id: int, payload: LoadoutUpdate) -> Dict[str, Any]:
        await self._require(asset_id, scope_id)
        fields = payload.model_dump(exclude_none=True)
        make_default = fields.pop("is_default", None)
        if "costume_ids" in fields or "prop_ids" in fields:
            await self._check_subset(asset_id, fields.get("costume_ids", []), fields.get("prop_ids", []))
            if "costume_ids" in fields:
                fields["costume_ids"] = [int(c) for c in fields["costume_ids"]]
            if "prop_ids" in fields:
                fields["prop_ids"] = [int(p) for p in fields["prop_ids"]]
        lo = await self.relations.update_loadout(int(loadout_id), int(asset_id), fields)
        if not lo:
            raise AssetError(404, "loadout_not_found", "Loadout not found")
        if make_default:
            await self.relations.set_default(int(loadout_id), int(asset_id))
            lo = {**lo, "is_default": True}
        return _serialize_loadout(lo)

    async def delete_loadout(self, asset_id: int, scope_id: int, loadout_id: int) -> None:
        await self._require(asset_id, scope_id)
        current = {int(l["id"]): l for l in await self.relations.list_loadouts(int(asset_id))}
        if int(loadout_id) not in current:
            raise AssetError(404, "loadout_not_found", "Loadout not found")
        if current[int(loadout_id)]["is_default"]:
            raise AssetError(422, "cannot_delete_default", "Make another loadout default first")
        await self.relations.delete_loadout(int(loadout_id), int(asset_id))

    # ── project refs ───────────────────────────────────────────────────────

    async def link_project(self, asset_id: int, scope_id: int, project_id: int, user_id: Optional[str]) -> None:
        await self._require(asset_id, scope_id)
        await self.relations.link_project(int(asset_id), int(project_id), user_id)

    async def unlink_project(self, asset_id: int, scope_id: int, project_id: int) -> None:
        await self._require(asset_id, scope_id)
        if not await self.relations.unlink_project(int(asset_id), int(project_id)):
            raise AssetError(404, "project_ref_not_found", "Asset is not linked to this project")
```

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/services/assets/ -v`
Expected: all PASS (10 service + earlier).

- [ ] **Step 5: Commit**

```bash
cd backend && uv run black app/services/assets/assets_service.py && uv run isort app/services/assets/assets_service.py
git add backend/app/services/assets/assets_service.py backend/tests/services/assets/test_assets_service.py
git commit -m "feat(assets): AssetsService — default loadout, link rules, loadout subset, 409/403/422 typed errors"
```

---

### Task 9: `/assets` router

**Files:**
- Create: `backend/app/api/assets_router.py`
- Modify: `backend/app/api/__init__.py` (import + `include_router` after `generated_media_router`)
- Test: `backend/tests/api/test_assets_router.py`

**Interfaces:**
- Consumes: `AssetsService`, `AssetError`, schemas.
- Produces routes (prefix `/assets`, all `AuthDep`, `scope_id` query = team id, membership-gated):
  - `GET /assets?scope_id&type&project_id&q&limit&offset` → `{success, data: [AssetResponse]}`
  - `POST /assets?scope_id` body `AssetCreate` → 201 `{success, data}`; 409 `{success:false, error:{code:'asset_exists', existing_asset_id}}`
  - `GET /assets/{id}?scope_id` → `AssetDetailResponse`
  - `PATCH /assets/{id}?scope_id` body `AssetUpdate`
  - `DELETE /assets/{id}?scope_id` → `{success, data:{deleted:true}}`
  - `POST /assets/{id}/files?scope_id` body `AttachFileRequest` | `AttachFilesBatchRequest` → 201
  - `DELETE /assets/{id}/files/{resource_id}/{slot}?scope_id`
  - `POST /assets/{id}/links?scope_id` body `LinkRequest` → 201; `DELETE /assets/{id}/links/{to_asset_id}/{relation}?scope_id`
  - `POST /assets/{id}/loadouts?scope_id` → 201; `PATCH /assets/{id}/loadouts/{lid}?scope_id`; `DELETE …`
  - `POST /assets/{id}/project-refs?scope_id` body `ProjectRefRequest` → 201; `DELETE /assets/{id}/project-refs/{project_id}?scope_id`
  - `GET /projects/{project_id}/assets?type` → same shape as list, scope resolved from the project (uses `verify_project_read_access`)

- [ ] **Step 1: Write the router tests**

```python
# backend/tests/api/test_assets_router.py
"""Assets endpoints: payload shape + error mapping + scope gating (no DB)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import assets_router as ar
from app.core.deps import get_auth
from app.services.assets.assets_service import AssetError

USER = "11111111-1111-1111-1111-111111111111"


class _AuthStub:
    user_id = USER


class _FakeService:
    def __init__(self):
        self.calls = []

    async def list_assets(self, scope_id, **f):
        self.calls.append(("list", scope_id, f))
        return [{"id": "1", "name": "Sang Yao", "asset_type": "character"}]

    async def create_asset(self, scope_id, payload, user_id):
        if payload.name == "dup":
            raise AssetError(409, "asset_exists", "exists", {"existing_asset_id": "7"})
        return {"id": "2", "name": payload.name, "asset_type": payload.asset_type}

    async def get_asset(self, asset_id, scope_id):
        if asset_id == 404:
            raise AssetError(404, "asset_not_found", "nope")
        return {"id": str(asset_id), "files": [], "links": [], "linked_by": [], "loadouts": []}

    async def attach_file(self, asset_id, scope_id, req, user_id):
        return {"asset_id": str(asset_id), "resource_id": req.resource_id, "slot": req.slot}

    async def add_link(self, asset_id, scope_id, req):
        raise AssetError(422, "link_not_allowed", "no", {})


@pytest.fixture
def app(monkeypatch):
    application = FastAPI()
    application.include_router(ar.router, prefix="/api/v1")

    async def _fake_auth():
        return _AuthStub()

    async def _member_ok(scope_id, user_id):
        return scope_id != "666"

    application.dependency_overrides[get_auth] = _fake_auth
    monkeypatch.setattr(ar, "_is_member", _member_ok)
    fake = _FakeService()
    monkeypatch.setattr(ar, "_service", lambda: fake)
    application.state.fake = fake
    return application


@pytest.mark.asyncio
async def test_list_passes_filters_and_wraps(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/assets?scope_id=9000&type=character&project_id=55&q=sang")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True and body["data"][0]["name"] == "Sang Yao"
    _, scope, f = app.state.fake.calls[0]
    assert scope == 9000 and f["asset_type"] == "character" and f["project_id"] == 55 and f["q"] == "sang"


@pytest.mark.asyncio
async def test_non_member_403(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/assets?scope_id=666")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_create_201_and_409_shape(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/v1/assets?scope_id=9000", json={"asset_type": "prop", "name": "Blade"})
        assert r.status_code == 201 and r.json()["data"]["id"] == "2"
        r = await c.post("/api/v1/assets?scope_id=9000", json={"asset_type": "prop", "name": "dup"})
    assert r.status_code == 409
    err = r.json()
    assert err["success"] is False
    assert err["error"]["code"] == "asset_exists" and err["error"]["existing_asset_id"] == "7"


@pytest.mark.asyncio
async def test_get_404_mapping(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/assets/404?scope_id=9000")
    assert r.status_code == 404 and r.json()["error"]["code"] == "asset_not_found"


@pytest.mark.asyncio
async def test_attach_single_and_batch(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/v1/assets/5/files?scope_id=9000", json={"resource_id": "727145299382534146", "slot": "sheet"})
        assert r.status_code == 201 and r.json()["data"]["slot"] == "sheet"
        r = await c.post("/api/v1/assets/5/files?scope_id=9000", json={"items": [{"resource_id": "1"}, {"resource_id": "2", "slot": "stills"}]})
    assert r.status_code == 201 and len(r.json()["data"]) == 2


@pytest.mark.asyncio
async def test_link_422_mapping(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/v1/assets/5/links?scope_id=9000", json={"to_asset_id": "6", "relation": "wears"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "link_not_allowed"
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && uv run pytest tests/api/test_assets_router.py -v`
Expected: FAIL import error.

- [ ] **Step 3: Implement the router**

```python
# backend/app/api/assets_router.py
"""Asset Library — /assets (spec §5.1, P0 subset).

Scope = ``?scope_id=`` (a teams.id snowflake, personal scopes are the user's
personal team). Gate = team membership. Every AssetError maps to
``{success:false, error:{code, detail, ...extra}}`` with its status — typed
failure echo, never a silent no-op.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.core.deps import AuthDep
from app.core.scope_guards import verify_project_read_access
from app.db.session import read_scope
from app.models import Projects, TeamMembers
from app.schemas.assets import (
    AssetCreate,
    AssetUpdate,
    AttachFileRequest,
    AttachFilesBatchRequest,
    LinkRequest,
    LoadoutCreate,
    LoadoutUpdate,
    ProjectRefRequest,
)
from app.services.assets.assets_service import AssetError, AssetsService

router = APIRouter(tags=["assets"])


def _service() -> AssetsService:
    return AssetsService()


async def _is_member(scope_id: str, user_id: str) -> bool:
    async with read_scope() as session:
        row = (
            await session.execute(
                select(TeamMembers.team_id)
                .where(TeamMembers.team_id == int(str(scope_id)))
                .where(TeamMembers.user_id == uuid.UUID(str(user_id)))
                .limit(1)
            )
        ).first()
    return row is not None


async def _gate(scope_id: str, auth) -> int:
    if not await _is_member(scope_id, auth.user_id):
        raise HTTPException(status_code=403, detail="You are not a member of this scope")
    return int(scope_id)


def _ok(data: Any, code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=code, content={"success": True, "data": data})


def _err(e: AssetError) -> JSONResponse:
    return JSONResponse(
        status_code=e.status,
        content={"success": False, "error": {"code": e.code, "detail": e.detail, **e.extra}},
    )


# ── list / create ───────────────────────────────────────────────────────────


@router.get("/assets")
async def list_assets(
    auth: AuthDep,
    scope_id: str = Query(...),
    type: Optional[str] = Query(None, pattern="^(character|location|prop|costume|prompt|audio)$"),
    project_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None, max_length=200),
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    sid = await _gate(scope_id, auth)
    try:
        rows = await _service().list_assets(
            sid,
            asset_type=type,
            project_id=int(project_id) if project_id else None,
            q=q,
            limit=limit,
            offset=offset,
        )
    except AssetError as e:
        return _err(e)
    return _ok(rows)


@router.post("/assets", status_code=status.HTTP_201_CREATED)
async def create_asset(payload: AssetCreate, auth: AuthDep, scope_id: str = Query(...)):
    sid = await _gate(scope_id, auth)
    try:
        return _ok(await _service().create_asset(sid, payload, auth.user_id), 201)
    except AssetError as e:
        return _err(e)


@router.get("/projects/{project_id}/assets")
async def list_project_assets(
    project_id: str,
    auth: AuthDep,
    type: Optional[str] = Query(None, pattern="^(character|location|prop|costume|prompt|audio)$"),
):
    # Guard returns None (raises 404/403 itself); load the project's team after.
    await verify_project_read_access(project_id, auth)
    async with read_scope() as session:
        team_id = (
            await session.execute(select(Projects.team_id).where(Projects.id == int(project_id)))
        ).scalar_one_or_none()
    if team_id is None:
        # Personal project (projects.team_id NULL) → the caller's personal team.
        from app.services.library.resources_service import _resolve_personal_team_id

        team_id = int(await _resolve_personal_team_id(auth.user_id))
    try:
        rows = await _service().list_assets(
            int(team_id), asset_type=type, project_id=int(project_id), q=None, limit=200, offset=0
        )
    except AssetError as e:
        return _err(e)
    return _ok(rows)


# ── single asset ────────────────────────────────────────────────────────────


@router.get("/assets/{asset_id}")
async def get_asset(asset_id: int, auth: AuthDep, scope_id: str = Query(...)):
    sid = await _gate(scope_id, auth)
    try:
        return _ok(await _service().get_asset(asset_id, sid))
    except AssetError as e:
        return _err(e)


@router.patch("/assets/{asset_id}")
async def update_asset(asset_id: int, payload: AssetUpdate, auth: AuthDep, scope_id: str = Query(...)):
    sid = await _gate(scope_id, auth)
    try:
        return _ok(await _service().update_asset(asset_id, sid, payload))
    except AssetError as e:
        return _err(e)


@router.delete("/assets/{asset_id}")
async def delete_asset(asset_id: int, auth: AuthDep, scope_id: str = Query(...)):
    sid = await _gate(scope_id, auth)
    try:
        await _service().delete_asset(asset_id, sid)
    except AssetError as e:
        return _err(e)
    return _ok({"deleted": True})


# ── files ───────────────────────────────────────────────────────────────────


@router.post("/assets/{asset_id}/files", status_code=status.HTTP_201_CREATED)
async def attach_files(
    asset_id: int,
    payload: Union[AttachFilesBatchRequest, AttachFileRequest],
    auth: AuthDep,
    scope_id: str = Query(...),
):
    sid = await _gate(scope_id, auth)
    svc = _service()
    try:
        if isinstance(payload, AttachFilesBatchRequest):
            out: List[Dict[str, Any]] = []
            for item in payload.items:
                out.append(await svc.attach_file(asset_id, sid, item, auth.user_id))
            return _ok(out, 201)
        return _ok(await svc.attach_file(asset_id, sid, payload, auth.user_id), 201)
    except AssetError as e:
        return _err(e)


@router.delete("/assets/{asset_id}/files/{resource_id}/{slot}")
async def detach_file(asset_id: int, resource_id: int, slot: str, auth: AuthDep, scope_id: str = Query(...)):
    sid = await _gate(scope_id, auth)
    try:
        await _service().detach_file(asset_id, sid, resource_id, slot)
    except AssetError as e:
        return _err(e)
    return _ok({"detached": True})


# ── links ───────────────────────────────────────────────────────────────────


@router.post("/assets/{asset_id}/links", status_code=status.HTTP_201_CREATED)
async def add_link(asset_id: int, payload: LinkRequest, auth: AuthDep, scope_id: str = Query(...)):
    sid = await _gate(scope_id, auth)
    try:
        return _ok(await _service().add_link(asset_id, sid, payload), 201)
    except AssetError as e:
        return _err(e)


@router.delete("/assets/{asset_id}/links/{to_asset_id}/{relation}")
async def remove_link(asset_id: int, to_asset_id: int, relation: str, auth: AuthDep, scope_id: str = Query(...)):
    sid = await _gate(scope_id, auth)
    try:
        await _service().remove_link(asset_id, sid, to_asset_id, relation)
    except AssetError as e:
        return _err(e)
    return _ok({"removed": True})


# ── loadouts ────────────────────────────────────────────────────────────────


@router.post("/assets/{asset_id}/loadouts", status_code=status.HTTP_201_CREATED)
async def create_loadout(asset_id: int, payload: LoadoutCreate, auth: AuthDep, scope_id: str = Query(...)):
    sid = await _gate(scope_id, auth)
    try:
        return _ok(await _service().create_loadout(asset_id, sid, payload), 201)
    except AssetError as e:
        return _err(e)


@router.patch("/assets/{asset_id}/loadouts/{loadout_id}")
async def update_loadout(asset_id: int, loadout_id: int, payload: LoadoutUpdate, auth: AuthDep, scope_id: str = Query(...)):
    sid = await _gate(scope_id, auth)
    try:
        return _ok(await _service().update_loadout(asset_id, sid, loadout_id, payload))
    except AssetError as e:
        return _err(e)


@router.delete("/assets/{asset_id}/loadouts/{loadout_id}")
async def delete_loadout(asset_id: int, loadout_id: int, auth: AuthDep, scope_id: str = Query(...)):
    sid = await _gate(scope_id, auth)
    try:
        await _service().delete_loadout(asset_id, sid, loadout_id)
    except AssetError as e:
        return _err(e)
    return _ok({"deleted": True})


# ── project refs ────────────────────────────────────────────────────────────


@router.post("/assets/{asset_id}/project-refs", status_code=status.HTTP_201_CREATED)
async def link_project(asset_id: int, payload: ProjectRefRequest, auth: AuthDep, scope_id: str = Query(...)):
    sid = await _gate(scope_id, auth)
    await verify_project_read_access(payload.project_id, auth)
    try:
        await _service().link_project(asset_id, sid, int(payload.project_id), auth.user_id)
    except AssetError as e:
        return _err(e)
    return _ok({"linked": True}, 201)


@router.delete("/assets/{asset_id}/project-refs/{project_id}")
async def unlink_project(asset_id: int, project_id: int, auth: AuthDep, scope_id: str = Query(...)):
    sid = await _gate(scope_id, auth)
    try:
        await _service().unlink_project(asset_id, sid, project_id)
    except AssetError as e:
        return _err(e)
    return _ok({"unlinked": True})
```

Verified against the repo: `verify_project_read_access(project_id, auth)` returns `None` and raises 404/403 itself; `TeamMembers.user_id` is `Uuid` (bind a `uuid.UUID`); `Projects.team_id` is nullable `BigInteger`.

Register in `backend/app/api/__init__.py` right after the `generated_media_router` line:

```python
from app.api.assets_router import router as assets_router
…
api_router.include_router(router=assets_router, tags=["Assets"])
```

- [ ] **Step 4: Run router tests + import the app**

Run: `cd backend && uv run pytest tests/api/test_assets_router.py -v && uv run python -c "from app.api import api_router; print(len(api_router.routes))"`
Expected: 6 PASS; app imports.

- [ ] **Step 5: Run the module-gates test that enumerates routers (it may need the new prefix listed)**

Run: `cd backend && uv run pytest tests/api/test_module_gates_mounted.py tests/api/test_modules_status_endpoint.py -v`
Expected: PASS. If a test asserts every mounted router belongs to a module, add `/assets` to the same module as `resources` following what that test's fixture expects.

- [ ] **Step 6: Commit**

```bash
cd backend && uv run black app/api/assets_router.py app/api/__init__.py && uv run isort app/api/assets_router.py
git add backend/app/api/assets_router.py backend/app/api/__init__.py backend/tests/api/test_assets_router.py
git commit -m "feat(assets): /assets router — CRUD, files, links, loadouts, project refs with typed error envelope"
```

---

### Task 10: Migration-planning workflow (dry-run)

**Files:**
- Create: `backend/app/workflows/backfill_assets_from_project_entities.py`
- Test: `backend/tests/workflows/test_backfill_assets_plan.py`

**Interfaces:**
- Consumes: `ProjectCharacters`, `ProjectLibEntities` (existing models), `Projects` (for team), `Assets`, `AssetProjectRefs`, `AssetLoadouts`.
- Produces:
  - pure `plan_migration(characters: list[dict], entities: list[dict], project_team: dict[int, int]) -> MigrationPlan` where `MigrationPlan = {"assets": [PlannedAsset], "merges": [Merge], "counts": {...}}`, `PlannedAsset = {"scope_id", "asset_type", "name", "role_tag", "description", "tags", "cover_url", "source": "migrated", "project_ids": [..], "legacy": [("project_characters"|"project_lib_entities", id), ...]}`, `Merge = {"scope_id","asset_type","name","legacy": [...]}`
  - DBOS workflow `backfill_assets_from_project_entities(dry_run: bool = True, run_user_id: str | None = None) -> dict` — `dry_run=True` only returns the plan counts + merge list; `dry_run=False` writes assets + project refs + default loadouts, idempotent by `(scope_id, asset_type, lower(name))` (existing asset → only add missing project refs), and records `attrs.merged_from` / `attrs.legacy_ids`. Execution is **P3**; P0 ships it dry-run-only and the router does not expose it.

- [ ] **Step 1: Write the plan tests**

```python
# backend/tests/workflows/test_backfill_assets_plan.py
from app.workflows.backfill_assets_from_project_entities import plan_migration

TEAM_A, TEAM_B = 100, 200
P1, P2, P3 = 11, 12, 13  # P1,P2 in team A; P3 in team B
PROJECT_TEAM = {P1: TEAM_A, P2: TEAM_A, P3: TEAM_B}


def _char(id, project_id, name, **kw):
    return {"id": id, "project_id": project_id, "name": name, "role_tag": kw.get("role_tag", ""),
            "description": kw.get("description", ""), "tags": kw.get("tags", {}),
            "portrait_url": kw.get("portrait_url"), "source": kw.get("source", "manual")}


def _ent(id, project_id, entity_type, name, **kw):
    return {"id": id, "project_id": project_id, "entity_type": entity_type, "name": name,
            "badge_tag": kw.get("badge_tag", ""), "description": kw.get("description", ""),
            "tags": kw.get("tags", {}), "cover_url": kw.get("cover_url"), "source": kw.get("source", "manual")}


def test_one_character_becomes_one_asset_with_project_ref():
    plan = plan_migration([_char(1, P1, "Sang Yao", role_tag="lead")], [], PROJECT_TEAM)
    assert plan["counts"] == {"characters": 1, "entities": 0, "assets": 1, "merges": 0, "skipped_unknown_project": 0}
    a = plan["assets"][0]
    assert a["scope_id"] == TEAM_A and a["asset_type"] == "character" and a["role_tag"] == "lead"
    assert a["project_ids"] == [P1] and a["legacy"] == [("project_characters", 1)]
    assert a["source"] == "migrated"


def test_same_name_same_team_merges_and_keeps_both_project_refs():
    plan = plan_migration([_char(1, P1, "Old Zhang"), _char(2, P2, "old zhang", description="v2")], [], PROJECT_TEAM)
    assert plan["counts"]["assets"] == 1 and plan["counts"]["merges"] == 1
    a = plan["assets"][0]
    assert sorted(a["project_ids"]) == [P1, P2]
    assert a["legacy"] == [("project_characters", 1), ("project_characters", 2)]
    assert a["description"] == "v2"  # longest description wins
    assert plan["merges"][0]["name"] == "Old Zhang"


def test_same_name_different_team_does_not_merge():
    plan = plan_migration([_char(1, P1, "Old Zhang"), _char(3, P3, "Old Zhang")], [], PROJECT_TEAM)
    assert plan["counts"]["assets"] == 2 and plan["counts"]["merges"] == 0


def test_entities_map_type_and_badge_tag():
    plan = plan_migration([], [_ent(9, P1, "location", "Bamboo Grove", badge_tag="exterior", cover_url="x.png")], PROJECT_TEAM)
    a = plan["assets"][0]
    assert a["asset_type"] == "location" and a["role_tag"] == "exterior" and a["cover_url"] == "x.png"
    assert a["legacy"] == [("project_lib_entities", 9)]


def test_character_and_prop_with_same_name_do_not_merge():
    plan = plan_migration([_char(1, P1, "Blade")], [_ent(9, P1, "prop", "Blade")], PROJECT_TEAM)
    assert plan["counts"]["assets"] == 2


def test_unknown_project_is_skipped_and_counted():
    plan = plan_migration([_char(1, 999, "Ghost")], [], PROJECT_TEAM)
    assert plan["counts"]["assets"] == 0 and plan["counts"]["skipped_unknown_project"] == 1
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && uv run pytest tests/workflows/test_backfill_assets_plan.py -v`
Expected: FAIL import error.

- [ ] **Step 3: Implement**

```python
# backend/app/workflows/backfill_assets_from_project_entities.py
"""backfill_assets_from_project_entities — project_characters + project_lib_entities
→ team-scoped assets (spec §4). THE BACKFILL PARADIGM (backfill_issue_scope.py):
DBOS workflow, ``dry_run=True`` by default, idempotent, failures raise.

P0 ships the PLANNER and the dry-run path. Execution (dry_run=False) is wired
here so P3 only has to flip the flag after the merge list has been reviewed by
a human — same-name-same-type rows inside one team MERGE into one asset (the
first cross-project reuse win, and the one place a wrong merge would hurt).

Task Center: pass the dispatching admin's ``run_user_id`` (a real auth.users
row) so the run shows up; the all-zero system id never creates a row.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple, TypedDict

from dbos import DBOS
from loguru import logger
from sqlalchemy import select

from app.db.session import read_scope, write_scope
from app.models import (
    AssetLoadouts,
    AssetProjectRefs,
    Assets,
    ProjectCharacters,
    ProjectLibEntities,
    Projects,
)

SYSTEM_RUN_USER_ID = "00000000-0000-0000-0000-000000000000"

LegacyRef = Tuple[str, int]


class PlannedAsset(TypedDict):
    scope_id: int
    asset_type: str
    name: str
    role_tag: str
    description: str
    tags: dict
    cover_url: Optional[str]
    source: str
    project_ids: List[int]
    legacy: List[LegacyRef]


class Merge(TypedDict):
    scope_id: int
    asset_type: str
    name: str
    legacy: List[LegacyRef]


class MigrationPlan(TypedDict):
    assets: List[PlannedAsset]
    merges: List[Merge]
    counts: Dict[str, int]


def _key(scope_id: int, asset_type: str, name: str) -> Tuple[int, str, str]:
    return (scope_id, asset_type, name.strip().lower())


def plan_migration(
    characters: List[Dict[str, Any]],
    entities: List[Dict[str, Any]],
    project_team: Dict[int, int],
) -> MigrationPlan:
    """Pure: group legacy rows by (team, type, lower(name)); merge duplicates.
    Merge policy: first row's display name, longest description, union of tags
    (first-wins per key), first non-null cover, all project ids, all legacy ids."""
    groups: "OrderedDict[Tuple[int, str, str], PlannedAsset]" = OrderedDict()
    skipped = 0

    def _fold(row: Dict[str, Any], asset_type: str, role_tag: str, cover: Optional[str], table: str) -> None:
        nonlocal skipped
        pid = int(row["project_id"])
        team = project_team.get(pid)
        if team is None:
            skipped += 1
            return
        k = _key(team, asset_type, row["name"])
        legacy: LegacyRef = (table, int(row["id"]))
        if k not in groups:
            groups[k] = {
                "scope_id": team,
                "asset_type": asset_type,
                "name": row["name"].strip(),
                "role_tag": role_tag or "",
                "description": row.get("description") or "",
                "tags": dict(row.get("tags") or {}),
                "cover_url": cover,
                "source": "migrated",
                "project_ids": [pid],
                "legacy": [legacy],
            }
            return
        g = groups[k]
        if pid not in g["project_ids"]:
            g["project_ids"].append(pid)
        g["legacy"].append(legacy)
        if len(row.get("description") or "") > len(g["description"]):
            g["description"] = row["description"]
        for tk, tv in (row.get("tags") or {}).items():
            g["tags"].setdefault(tk, tv)
        if g["cover_url"] is None and cover:
            g["cover_url"] = cover
        if not g["role_tag"] and role_tag:
            g["role_tag"] = role_tag

    for c in characters:
        _fold(c, "character", c.get("role_tag") or "", c.get("portrait_url"), "project_characters")
    for e in entities:
        _fold(e, e["entity_type"], e.get("badge_tag") or "", e.get("cover_url"), "project_lib_entities")

    assets = list(groups.values())
    merges: List[Merge] = [
        {"scope_id": a["scope_id"], "asset_type": a["asset_type"], "name": a["name"], "legacy": a["legacy"]}
        for a in assets
        if len(a["legacy"]) > 1
    ]
    return {
        "assets": assets,
        "merges": merges,
        "counts": {
            "characters": len(characters),
            "entities": len(entities),
            "assets": len(assets),
            "merges": len(merges),
            "skipped_unknown_project": skipped,
        },
    }


async def _load_inputs() -> Tuple[List[dict], List[dict], Dict[int, int]]:
    def _rows(objs) -> List[dict]:
        return [{c.name: getattr(o, c.name) for c in o.__table__.columns} for o in objs]

    async with read_scope() as session:
        chars = _rows((await session.execute(select(ProjectCharacters))).scalars().all())
        ents = _rows((await session.execute(select(ProjectLibEntities))).scalars().all())
        projs = (await session.execute(select(Projects.id, Projects.team_id))).all()
    project_team: Dict[int, int] = {}
    for pid, team_id in projs:
        if team_id is not None:
            project_team[int(pid)] = int(team_id)
    return chars, ents, project_team


async def _apply(plan: MigrationPlan, run_user_id: str) -> Dict[str, int]:
    created = existing = refs = 0
    for a in plan["assets"]:
        async with write_scope() as session:
            found = (
                await session.execute(
                    select(Assets)
                    .where(Assets.scope_id == a["scope_id"])
                    .where(Assets.asset_type == a["asset_type"])
                    .where(Assets.name.ilike(a["name"]))
                    .where(Assets.deleted_at.is_(None))
                    .limit(1)
                )
            ).scalar_one_or_none()
            if found is None:
                obj = Assets(
                    scope_id=a["scope_id"],
                    asset_type=a["asset_type"],
                    name=a["name"],
                    role_tag=a["role_tag"],
                    description=a["description"],
                    tags=a["tags"],
                    source="migrated",
                    attrs={"legacy_ids": [list(x) for x in a["legacy"]],
                           "merged_from": [list(x) for x in a["legacy"]] if len(a["legacy"]) > 1 else [],
                           "legacy_cover_url": a["cover_url"]},
                    created_by=run_user_id,
                )
                session.add(obj)
                await session.flush()
                asset_id = int(obj.id)
                created += 1
                if a["asset_type"] == "character":
                    session.add(AssetLoadouts(asset_id=asset_id, name="Default", is_default=True))
            else:
                asset_id = int(found.id)
                existing += 1
            have = {
                int(p) for (p,) in (
                    await session.execute(
                        select(AssetProjectRefs.project_id).where(AssetProjectRefs.asset_id == asset_id)
                    )
                ).all()
            }
            for pid in a["project_ids"]:
                if pid not in have:
                    session.add(AssetProjectRefs(asset_id=asset_id, project_id=pid, linked_by=run_user_id))
                    refs += 1
    return {"created": created, "existing": existing, "project_refs_added": refs}


@DBOS.workflow()
async def backfill_assets_from_project_entities(
    dry_run: bool = True, run_user_id: Optional[str] = None
) -> Dict[str, Any]:
    run_user_id = run_user_id or SYSTEM_RUN_USER_ID
    chars, ents, project_team = await _load_inputs()
    plan = plan_migration(chars, ents, project_team)
    logger.info("[backfill-assets] plan counts={} dry_run={}", plan["counts"], dry_run)
    out: Dict[str, Any] = {"dry_run": dry_run, "counts": plan["counts"], "merges": plan["merges"][:200]}
    if dry_run:
        return out
    out["applied"] = await _apply(plan, run_user_id)
    # Reconciliation (spec §4): assets created + existing == planned assets.
    if out["applied"]["created"] + out["applied"]["existing"] != plan["counts"]["assets"]:
        raise RuntimeError(f"[backfill-assets] reconciliation failed: {out}")
    return out
```

Verified: `ProjectLibEntities` and `Projects` (with nullable `team_id: BigInteger`) exist under those names. Note `cover_url`/`portrait_url` are **not** resolved to `resources` rows in P0 (kept in `attrs.legacy_cover_url`); P3 resolves them to `cover_file_id` when it executes.

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/workflows/test_backfill_assets_plan.py -v && uv run python -c "import app.workflows.backfill_assets_from_project_entities"`
Expected: 6 PASS; import ok (the DBOS decorator registers at import — if import needs DBOS configured, follow how `tests/workflows/` import other `@DBOS.workflow` modules, e.g. the conftest there).

- [ ] **Step 5: Commit**

```bash
cd backend && uv run black app/workflows/backfill_assets_from_project_entities.py && uv run isort app/workflows/backfill_assets_from_project_entities.py
git add backend/app/workflows/backfill_assets_from_project_entities.py backend/tests/workflows/test_backfill_assets_plan.py
git commit -m "feat(assets): dry-run migration planner workflow — project entities → team assets with merge list"
```

---

### Task 11: Whole-suite check, CLAUDE.md note, PR

**Files:**
- Modify: `CLAUDE.md` — add one row under "核心数据表" for `assets` + a one-line pointer to the spec in the AI Library / 项目结构 area (keep it to ≤6 lines).

- [ ] **Step 1: Run the full backend unit suite**

Run: `cd backend && uv run pytest -q -x --ignore=tests/integration`
Expected: green. Fix anything the new models broke (typical: `test_tool_descriptor_allowlist` untouched; `test_schema_assertions` untouched; `test_module_gates_mounted` may need the `/assets` prefix mapped — see Task 9 Step 5).

- [ ] **Step 2: Run the lint trio on touched files**

Run: `cd backend && uv run black --check app/models/assets.py app/repositories/assets_repository.py app/repositories/asset_relations_repository.py app/services/assets app/api/assets_router.py app/schemas/assets.py app/workflows/backfill_assets_from_project_entities.py && uv run isort --check-only $(git diff --name-only origin/master -- backend | grep '\.py$') && uv run flake8 $(git diff --name-only origin/master -- backend | grep '\.py$')`
Expected: no output, exit 0.

- [ ] **Step 3: Add the CLAUDE.md rows**

In the `核心数据表` table add:

```
| `assets` / `asset_files` / `asset_links` / `asset_loadouts` / `asset_project_refs` | 资产库语义层（角色/场景/道具/服装/提示词/音频实体；文件只挂关联不搬家）— 见 `docs/superpowers/specs/2026-08-28-asset-library-loadout-design.md` | BIGINT Snowflake |
```

- [ ] **Step 4: Commit and open the PR**

```bash
git add CLAUDE.md
git commit -m "docs: register asset library tables in CLAUDE.md"
```

Then `/ship` (repo convention) with title `feat(assets): P0 data layer — asset library tables, models, service, /assets API, dry-run migration planner`. In the PR body list: mig 445/446, the six tables, the P0-only scope (no UI, migration not executed), and the follow-up phases from spec §9.

- [ ] **Step 5: After merge — verify the migration ran and schema-drift is green**

Run: `gh run list --workflow=run-migration.yml -L 1 && gh run list --workflow=schema-drift.yml -L 1`
Expected: both `completed success`. Then on gpupc: `docker exec nous-db psql -U postgres -p 55434 -d postgres -c "\d assets" | head -5` shows the table.

---

## Self-review (done while writing)

- **Spec coverage (P0 row of §9):** tables (T1), generated_media/canvases ext (T2), ORM+repo (T3, T6, T7), basic API (T9), migration script dry-run + reconciliation (T10). `canvas_asset_refs` table + model exist (T1/T3); maintenance is P4 per spec. `readiness` derived (T4/T6). 409 duplicate → "已存在，是否关联" (T6/T8/T9). System preset read-only (T8). Loadout ⊆ links + strip on unlink (T8). Files never move (T7 — only `asset_files` writes).
- **Not in P0 (by spec):** `/generated` endpoints & `save-as-asset` (P1), `duplicate`, `generate-slot`, `bundle`, prompt translate/regenerate (P2), `import-from-script` rewire (P3), `canvas-refs` endpoints (P4).
- **Type consistency:** `AssetError(status, code, detail, extra)` used identically in T8/T9; `_serialize` / `with_derived` names match T6↔T8; relations repo method names match T7↔T8 fakes; `LoadoutCreate.costume_ids: List[str]` → service `int()`s them → repo stores `list[int]` → `_serialize_loadout` stringifies back.
- **Placeholders:** none. Model/guard names were verified in-repo while writing (`ResourceItems`, `ProjectLibEntities`, `Projects.team_id` nullable, `verify_project_read_access → None`, `TeamMembers.user_id: Uuid`). Remaining conditional steps are real runtime checks (migration number collision, `origin_kind` CHECK existence, module-gate test fixture).
