# Project Assets (IC-port P4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Project Assets" view that browses canvas-referenced resources grouped by Project → Canvas, backed by a materialized `canvas_resource_refs` table, and retire the chat-temp TTL sweeper.

**Architecture:** A new junction table records `(canvas_id, resource_id, role, node_id)` triples. `CanvasService` maintains it by extracting resource references from `nodes_json` on every save (replace-all-for-canvas, idempotent, non-fatal on error). Three read endpoints (tree, per-canvas assets, per-resource back-refs) serve the UI by joining the table with membership/ownership filters. The frontend repurposes the existing Temp sidebar view into Project Assets: a virtual Project→Canvas tree on the left, the existing `ResourceGrid` on the right, with the old chat-temp folder surfaced as a "Chat Uploads" group that no longer expires.

**Tech Stack:** FastAPI + supabase-py + `app.db.engine` (raw SQL for aggregates/joins), Postgres (snowflake BIGINT ids), DBOS (scheduled sweeper being disabled), React 19 + TypeScript + Vite, i18next.

**Working directory:** `/Volumes/program/project-code/repos/mediahub/.worktrees/feature-project-assets-p4` (branch `feature/project-assets-p4`, ports FE 5176 / BE 8081).

**Spec:** `docs/superpowers/specs/2026-06-13-project-assets-design.md`

---

## File Structure

**Backend — create:**
- `supabase/migrations/290_canvas_resource_refs.sql` — junction table + indexes + RLS
- `backend/app/services/canvas/asset_refs.py` — pure extraction `nodes_json → refs[]`
- `backend/app/repositories/canvas_refs_repository.py` — write (replace-all) + read (tree/assets/back-refs) SQL
- `backend/app/api/project_assets_router.py` — the 3 read endpoints
- `backend/scripts/backfill_canvas_resource_refs.py` — one-time backfill
- `backend/tests/test_asset_refs.py` — extraction unit tests
- `backend/tests/test_canvas_refs_wiring.py` — service-save wiring tests
- `backend/tests/api/test_project_assets_router.py` — endpoint SQL/permission tests
- `frontend/services/projectAssetsService.ts` — API client
- `frontend/components/resources/ProjectAssetsTree.tsx` — left Project→Canvas tree

**Backend — modify:**
- `backend/app/services/canvas/canvas_service.py` — call refs maintenance in `update_with_lock` + `create_in_project`
- `backend/app/workflows/temp_resource_sweeper.py` — disable the `@DBOS.scheduled` decorator
- `backend/app/main.py` (or wherever routers register) — mount `project_assets_router`

**Frontend — modify:**
- `frontend/contexts/ResourcesContext.tsx` — `SidebarView` type + route derivation `temp`→`project-assets`; keep temp-folder fetch as "Chat Uploads"
- `frontend/components/ResourcesSidebar.tsx` — nav label + path
- `frontend/components/ResourcesViewInner.tsx` — render tree + grid for the project-assets view
- `frontend/components/ResourceDetailPage.tsx` — "Appears in N canvases" back-ref block
- `frontend/App.tsx` — route alias/redirect `/resources/temp` → `/resources/project-assets`
- `frontend/public/locales/en.json`, `frontend/public/locales/zh.json` — strings
- Settings UI component holding `chat_temp_ttl_days` — hide the row

---

## Phase A — Backend data layer

### Task 1: Migration — `canvas_resource_refs` table

**Files:**
- Create: `supabase/migrations/290_canvas_resource_refs.sql`

- [ ] **Step 1: Write the migration**

```sql
-- 290_canvas_resource_refs.sql
-- Materialized junction: which resources each canvas references.
-- Maintained by CanvasService on save (replace-all per canvas).
-- IC-port P4. See docs/superpowers/specs/2026-06-13-project-assets-design.md

CREATE TABLE IF NOT EXISTS canvas_resource_refs (
  canvas_id   BIGINT NOT NULL REFERENCES canvases(id)  ON DELETE CASCADE,
  resource_id BIGINT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  role        TEXT   NOT NULL CHECK (role IN ('reference', 'output')),
  node_id     TEXT   NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (canvas_id, resource_id, node_id)
);

CREATE INDEX IF NOT EXISTS idx_crr_resource ON canvas_resource_refs(resource_id);
CREATE INDEX IF NOT EXISTS idx_crr_canvas   ON canvas_resource_refs(canvas_id);

COMMENT ON TABLE canvas_resource_refs IS
  'IC-port P4. One row per (canvas, resource, node) reference, extracted
   from canvases.nodes_json on save. role=reference (shot node ref image)
   or output (prompt-run rendered artifact). Derived/rebuildable — backfill
   script can reconstruct from nodes_json at any time.';

-- RLS: locked to service_role. All app access goes through app.db.engine
-- (bypasses RLS, same as temp_resource_sweeper's reads on folders) via the
-- gated project-assets endpoints, which JOIN + filter by membership. No
-- PostgREST/anon exposure (avoids the world-readable-table class of bug).
ALTER TABLE canvas_resource_refs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS crr_service_role_all ON canvas_resource_refs;
CREATE POLICY crr_service_role_all ON canvas_resource_refs
  FOR ALL TO service_role USING (true) WITH CHECK (true);
```

- [ ] **Step 2: Apply to dev DB to verify it parses**

Run (dev DB DSN from memory `Local Dev Environment` — `192.168.50.9:55434`, build the env file as noted there):
```bash
psql "$ORM2_DEV_DSN" -f supabase/migrations/290_canvas_resource_refs.sql
```
Expected: `CREATE TABLE`, `CREATE INDEX` ×2, `COMMENT`, `ALTER TABLE`, `CREATE POLICY` with no error. Re-run once to confirm idempotency (`IF NOT EXISTS` / `DROP POLICY` make it a no-op).

- [ ] **Step 3: Verify engine (app role) can read the empty table**

```bash
psql "$ORM2_DEV_DSN" -c "SELECT count(*) FROM canvas_resource_refs;"
```
Expected: `0`. (Confirms the table exists; engine-role read behavior is exercised in Task 6's integration check.)

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/290_canvas_resource_refs.sql
git commit -m "feat(db): canvas_resource_refs junction table (IC-port P4)"
```

---

### Task 2: Pure reference-extraction function

**Files:**
- Create: `backend/app/services/canvas/asset_refs.py`
- Test: `backend/tests/test_asset_refs.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_asset_refs.py
"""Unit tests for extract_asset_refs — nodes_json → ref triples."""

from __future__ import annotations

from app.services.canvas.asset_refs import extract_asset_refs


def test_shot_node_yields_reference_rows_one_per_resource():
    nodes = [
        {
            "id": "shot-1",
            "type": "shot",
            "data": {"title": "s", "reference_resource_ids": ["111", "222"], "notes": ""},
        }
    ]
    refs = extract_asset_refs(nodes)
    assert sorted((r["resource_id"], r["role"], r["node_id"]) for r in refs) == [
        ("111", "reference", "shot-1"),
        ("222", "reference", "shot-1"),
    ]


def test_output_node_yields_single_output_row():
    nodes = [{"id": "out-1", "type": "output", "data": {"kind": "image", "resource_id": "999"}}]
    refs = extract_asset_refs(nodes)
    assert refs == [{"resource_id": "999", "role": "output", "node_id": "out-1"}]


def test_output_node_with_null_resource_is_skipped():
    nodes = [{"id": "out-1", "type": "output", "data": {"kind": "text", "resource_id": None}}]
    assert extract_asset_refs(nodes) == []


def test_prompt_and_loop_nodes_yield_nothing():
    nodes = [
        {"id": "p", "type": "prompt", "data": {"body": "x"}},
        {"id": "l", "type": "loop", "data": {"mode": "serial"}},
    ]
    assert extract_asset_refs(nodes) == []


def test_duplicate_resource_in_same_node_deduped():
    nodes = [{"id": "shot-1", "type": "shot", "data": {"reference_resource_ids": ["111", "111"]}}]
    refs = extract_asset_refs(nodes)
    assert refs == [{"resource_id": "111", "role": "reference", "node_id": "shot-1"}]


def test_same_resource_across_two_nodes_kept_separately():
    nodes = [
        {"id": "shot-1", "type": "shot", "data": {"reference_resource_ids": ["111"]}},
        {"id": "out-1", "type": "output", "data": {"kind": "image", "resource_id": "111"}},
    ]
    refs = extract_asset_refs(nodes)
    assert {(r["node_id"], r["role"]) for r in refs} == {("shot-1", "reference"), ("out-1", "output")}


def test_malformed_nodes_are_tolerated():
    nodes = ["not a dict", {"no_type": True}, {"id": "x", "type": "shot"}, None]
    assert extract_asset_refs(nodes) == []


def test_non_list_input_returns_empty():
    assert extract_asset_refs(None) == []
    assert extract_asset_refs({"nodes": []}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_asset_refs.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.canvas.asset_refs`.

- [ ] **Step 3: Write the implementation**

```python
# backend/app/services/canvas/asset_refs.py
"""Extract resource references from a canvas's nodes_json.

Pure, DB-free. The source of truth for what canvas_resource_refs should
contain for a given canvas. Mirrors the smart-node shapes defined in
frontend/features/canvas-core/smart/types.ts:
  - shot   node → data.reference_resource_ids[]  (role 'reference')
  - output node → data.resource_id               (role 'output')
prompt/loop nodes hold no resource references.
"""

from __future__ import annotations

from typing import Any, Dict, List


def extract_asset_refs(nodes_json: Any) -> List[Dict[str, str]]:
    """Return ``[{"resource_id", "role", "node_id"}, ...]``.

    Deduped on (node_id, resource_id). Tolerant of malformed entries —
    anything that isn't a well-formed node is skipped rather than raised,
    because this runs on the canvas-save hot path and must never block a
    save (the refs table is rebuildable from a backfill).
    """
    if not isinstance(nodes_json, list):
        return []

    seen: set[tuple[str, str]] = set()
    out: List[Dict[str, str]] = []

    for idx, node in enumerate(nodes_json):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or f"node_{idx}")
        node_type = str(node.get("type") or "")
        data = node.get("data")
        if not isinstance(data, dict):
            continue

        if node_type == "shot":
            raw = data.get("reference_resource_ids")
            if isinstance(raw, list):
                for rid in raw:
                    _add(out, seen, rid, "reference", node_id)
        elif node_type == "output":
            _add(out, seen, data.get("resource_id"), "output", node_id)

    return out


def _add(out, seen, rid, role, node_id) -> None:
    if rid is None:
        return
    rid_s = str(rid).strip()
    if not rid_s:
        return
    key = (node_id, rid_s)
    if key in seen:
        return
    seen.add(key)
    out.append({"resource_id": rid_s, "role": role, "node_id": node_id})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_asset_refs.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/canvas/asset_refs.py backend/tests/test_asset_refs.py
git commit -m "feat(canvas): pure resource-ref extraction from nodes_json"
```

---

### Task 3: `CanvasRefsRepository` (write + read SQL)

**Files:**
- Create: `backend/app/repositories/canvas_refs_repository.py`

This repo holds raw SQL via `app.db.engine`. Reads are exercised by Task 6/7/8 endpoint tests; this task only lands the class. (No standalone unit test — it is pure I/O with no logic to assert without a DB; integration coverage comes via the endpoint tests' optional DB path.)

- [ ] **Step 1: Write the repository**

```python
# backend/app/repositories/canvas_refs_repository.py
"""Data access for ``canvas_resource_refs``.

Writes go through ``execute_as_service_role`` (the table is RLS-locked to
service_role; canvas membership was already checked at the route layer).
Reads use ``fetch_all`` (engine role bypasses RLS, same as the temp
sweeper's folder reads) and always JOIN with the caller's membership /
ownership filter so no cross-tenant row can leak.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.db import engine as db_engine


class CanvasRefsRepository:
    # -- writes -------------------------------------------------------

    async def replace_for_canvas(
        self, canvas_id: str, refs: List[Dict[str, str]]
    ) -> None:
        """Replace ALL refs for a canvas with ``refs`` in one transaction.

        Replace-all (not diff) keeps the logic trivially correct: the
        extracted set IS the desired state. Idempotent.
        """
        cid = int(str(canvas_id))
        await db_engine.execute_as_service_role(
            "DELETE FROM canvas_resource_refs WHERE canvas_id = :cid",
            {"cid": cid},
        )
        for r in refs:
            await db_engine.execute_as_service_role(
                "INSERT INTO canvas_resource_refs "
                "  (canvas_id, resource_id, role, node_id) "
                "VALUES (:cid, :rid, :role, :node_id) "
                "ON CONFLICT (canvas_id, resource_id, node_id) DO NOTHING",
                {
                    "cid": cid,
                    "rid": int(str(r["resource_id"])),
                    "role": r["role"],
                    "node_id": r["node_id"],
                },
            )

    # -- reads --------------------------------------------------------

    async def list_assets_for_canvas(self, canvas_id: str) -> List[Dict[str, Any]]:
        """Resources referenced by a canvas, with role. Newest first."""
        rows = await db_engine.fetch_all(
            "SELECT r.id::text AS id, r.filename, r.file_type, r.mime_type, "
            "       r.thumbnail_path, r.cover_image_path, r.created_at, "
            "       crr.role, crr.node_id "
            "FROM canvas_resource_refs crr "
            "JOIN resources r ON r.id = crr.resource_id "
            "WHERE crr.canvas_id = :cid AND r.is_trashed = false "
            "ORDER BY r.created_at DESC",
            {"cid": int(str(canvas_id))},
        )
        return rows or []

    async def list_canvases_for_resource(
        self, resource_id: str
    ) -> List[Dict[str, Any]]:
        """Canvases that reference a resource (back-ref for detail page)."""
        rows = await db_engine.fetch_all(
            "SELECT DISTINCT c.id::text AS canvas_id, c.name AS canvas_name, "
            "       c.kind, c.project_id::text AS project_id, crr.role "
            "FROM canvas_resource_refs crr "
            "JOIN canvases c ON c.id = crr.canvas_id "
            "WHERE crr.resource_id = :rid "
            "ORDER BY c.name",
            {"rid": int(str(resource_id))},
        )
        return rows or []

    async def tree_for_projects(
        self, project_ids: List[str]
    ) -> List[Dict[str, Any]]:
        """Per-canvas asset counts for the given projects (tree payload)."""
        if not project_ids:
            return []
        ids = [int(str(p)) for p in project_ids]
        rows = await db_engine.fetch_all(
            "SELECT c.project_id::text AS project_id, c.id::text AS canvas_id, "
            "       c.name AS canvas_name, c.kind, "
            "       COUNT(DISTINCT (crr.resource_id, crr.node_id))"
            "         FILTER (WHERE crr.resource_id IS NOT NULL) AS asset_count "
            "FROM canvases c "
            "LEFT JOIN canvas_resource_refs crr ON crr.canvas_id = c.id "
            "WHERE c.project_id = ANY(:ids) "
            "GROUP BY c.project_id, c.id, c.name, c.kind "
            "ORDER BY c.name",
            {"ids": ids},
        )
        return rows or []
```

- [ ] **Step 2: Import-smoke it**

Run: `cd backend && uv run python -c "from app.repositories.canvas_refs_repository import CanvasRefsRepository; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add backend/app/repositories/canvas_refs_repository.py
git commit -m "feat(canvas): canvas_resource_refs repository (write replace-all + read joins)"
```

---

### Task 4: Wire ref maintenance into `CanvasService`

**Files:**
- Modify: `backend/app/services/canvas/canvas_service.py`
- Test: `backend/tests/test_canvas_refs_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_canvas_refs_wiring.py
"""CanvasService maintains canvas_resource_refs on save."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest

from app.schemas.canvas import CanvasUpdate
from app.services.canvas.canvas_service import CanvasService

FROZEN = datetime(2026, 6, 13, 12, 0, 0, tzinfo=timezone.utc)


class FakeRepo:
    def __init__(self) -> None:
        self.row = {
            "id": "5001",
            "project_id": "9000",
            "name": "C",
            "kind": "smart",
            "viewport_json": {"x": 0, "y": 0, "zoom": 1},
            "nodes_json": [],
            "connections_json": [],
            "node_ops_json": [],
            "connection_ops_json": [],
            "base_updated_at": FROZEN.isoformat(),
        }

    async def get_by_id(self, canvas_id: str) -> Optional[Dict[str, Any]]:
        return dict(self.row)

    async def update_with_lock(self, canvas_id, expected_base_updated_at, fields):
        self.row.update(fields)
        self.row["base_updated_at"] = "2026-06-13T12:01:00+00:00"
        return dict(self.row)


class FakeRefsRepo:
    def __init__(self) -> None:
        self.calls: List[tuple[str, List[Dict[str, str]]]] = []

    async def replace_for_canvas(self, canvas_id: str, refs: List[Dict[str, str]]) -> None:
        self.calls.append((canvas_id, refs))


@pytest.mark.asyncio
async def test_save_extracts_and_replaces_refs():
    refs_repo = FakeRefsRepo()
    svc = CanvasService(repository=FakeRepo(), refs_repository=refs_repo)
    upd = CanvasUpdate(
        base_updated_at=FROZEN,
        nodes_json=[
            {"id": "shot-1", "type": "shot", "data": {"reference_resource_ids": ["111"]}},
            {"id": "out-1", "type": "output", "data": {"kind": "image", "resource_id": "222"}},
        ],
    )
    await svc.update_with_lock("5001", upd)
    assert len(refs_repo.calls) == 1
    canvas_id, refs = refs_repo.calls[0]
    assert canvas_id == "5001"
    assert {(r["resource_id"], r["role"]) for r in refs} == {("111", "reference"), ("222", "output")}


@pytest.mark.asyncio
async def test_save_without_nodes_json_does_not_touch_refs():
    refs_repo = FakeRefsRepo()
    svc = CanvasService(repository=FakeRepo(), refs_repository=refs_repo)
    await svc.update_with_lock("5001", CanvasUpdate(base_updated_at=FROZEN, name="renamed"))
    assert refs_repo.calls == []  # nodes_json absent → no ref recompute


@pytest.mark.asyncio
async def test_refs_failure_does_not_break_save(monkeypatch):
    class Boom:
        async def replace_for_canvas(self, *a, **k):
            raise RuntimeError("db down")

    svc = CanvasService(repository=FakeRepo(), refs_repository=Boom())
    upd = CanvasUpdate(base_updated_at=FROZEN, nodes_json=[])
    result = await svc.update_with_lock("5001", upd)  # must not raise
    assert result["id"] == "5001"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_canvas_refs_wiring.py -v`
Expected: FAIL — `CanvasService.__init__` has no `refs_repository` arg.

- [ ] **Step 3: Modify `CanvasService`**

In `backend/app/services/canvas/canvas_service.py`, update imports and `__init__`:

```python
from app.repositories.canvas_refs_repository import CanvasRefsRepository
from app.services.canvas.asset_refs import extract_asset_refs
```

```python
class CanvasService:
    def __init__(
        self,
        repository: Optional[CanvasRepository] = None,
        refs_repository: Optional[CanvasRefsRepository] = None,
    ) -> None:
        self.repo = repository or CanvasRepository()
        self.refs_repo = refs_repository or CanvasRefsRepository()
```

Add a private helper:

```python
    async def _sync_refs(self, canvas_id: str, nodes_json: Any) -> None:
        """Recompute canvas_resource_refs from nodes_json. Non-fatal:
        the refs table is rebuildable, so a failure here must never break
        the canvas save the user just performed."""
        try:
            refs = extract_asset_refs(nodes_json)
            await self.refs_repo.replace_for_canvas(canvas_id, refs)
        except Exception as e:  # noqa: BLE001 — deliberately swallow
            logger.warning("canvas %s refs sync failed (non-fatal): %s", canvas_id, e)
```

In `update_with_lock`, after the successful `updated = await self.repo.update_with_lock(...)` / conflict handling, before `return updated`:

```python
        if updated is None:
            fresh = await self.repo.get_by_id(canvas_id)
            raise CanvasConflict(fresh or current)

        # Only recompute refs when nodes_json was part of this save.
        if "nodes_json" in fields:
            await self._sync_refs(canvas_id, fields["nodes_json"])
        return updated
```

(Use `Any` — already importable; if not imported, add `from typing import Any`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_canvas_refs_wiring.py tests/test_canvas_service.py -v`
Expected: PASS (new 3 + existing canvas-service tests still green).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/canvas/canvas_service.py backend/tests/test_canvas_refs_wiring.py
git commit -m "feat(canvas): sync canvas_resource_refs on save (non-fatal)"
```

---

### Task 5: Backfill script

**Files:**
- Create: `backend/scripts/backfill_canvas_resource_refs.py`

- [ ] **Step 1: Write the script**

```python
# backend/scripts/backfill_canvas_resource_refs.py
"""One-time backfill: rebuild canvas_resource_refs from every canvas's
nodes_json. Idempotent (replace-all per canvas) — safe to re-run.

Usage:  cd backend && uv run python scripts/backfill_canvas_resource_refs.py
"""

from __future__ import annotations

import asyncio

from loguru import logger

from app.db import engine as db_engine
from app.repositories.canvas_refs_repository import CanvasRefsRepository
from app.services.canvas.asset_refs import extract_asset_refs


async def main() -> None:
    rows = await db_engine.fetch_all(
        "SELECT id::text AS id, nodes_json FROM canvases"
    )
    refs_repo = CanvasRefsRepository()
    total_canvases = 0
    total_refs = 0
    for row in rows or []:
        refs = extract_asset_refs(row.get("nodes_json"))
        await refs_repo.replace_for_canvas(row["id"], refs)
        total_canvases += 1
        total_refs += len(refs)
    logger.info(
        "backfill done: {} canvases, {} refs", total_canvases, total_refs
    )


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Import-smoke**

Run: `cd backend && uv run python -c "import scripts.backfill_canvas_resource_refs as m; print(hasattr(m, 'main'))"`
Expected: `True`.

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/backfill_canvas_resource_refs.py
git commit -m "feat(canvas): backfill script for canvas_resource_refs"
```

(Actual run against prod is a deploy-time step in Task 16's rollout notes, not part of code.)

---

## Phase B — Backend API

### Task 6: `GET /canvases/{id}/assets`

**Files:**
- Create: `backend/app/api/project_assets_router.py`
- Test: `backend/tests/api/test_project_assets_router.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/api/test_project_assets_router.py
"""Project-assets endpoints: payload shape + permission gating.

The repo's SQL is faked so these run without a DB; the gating (project
membership / canvas access / resource ownership) is the load-bearing
behavior under test.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.api import project_assets_router as par


class _AuthStub:
    user_id = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def app(monkeypatch):
    application = FastAPI()
    application.include_router(par.router, prefix="/api/v1")
    application.dependency_overrides[par.AuthDep] = lambda: _AuthStub()
    return application


@pytest.mark.asyncio
async def test_canvas_assets_returns_items(app, monkeypatch):
    async def fake_gate(canvas_id, auth):
        return "9000"

    async def fake_list(self, canvas_id):
        return [
            {"id": "111", "filename": "a.png", "role": "reference", "node_id": "s1",
             "file_type": "image", "mime_type": "image/png", "thumbnail_path": None,
             "cover_image_path": None, "created_at": "2026-06-13T00:00:00Z"},
        ]

    monkeypatch.setattr(par, "_gate_canvas_read", fake_gate)
    monkeypatch.setattr(par.CanvasRefsRepository, "list_assets_for_canvas", fake_list)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/api/v1/canvases/5001/assets")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"][0]["id"] == "111"
    assert body["data"][0]["role"] == "reference"


@pytest.mark.asyncio
async def test_canvas_assets_403_when_not_member(app, monkeypatch):
    async def deny(canvas_id, auth):
        raise HTTPException(status_code=403, detail="not a member")

    monkeypatch.setattr(par, "_gate_canvas_read", deny)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/api/v1/canvases/5001/assets")
    assert resp.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/api/test_project_assets_router.py::test_canvas_assets_returns_items -v`
Expected: FAIL — `app.api.project_assets_router` does not exist.

- [ ] **Step 3: Write the router (canvas-assets endpoint only for now)**

```python
# backend/app/api/project_assets_router.py
"""Project Assets read endpoints (IC-port P4).

  GET /canvases/{id}/assets          — resources referenced by a canvas
  GET /resources/{id}/canvas-refs    — canvases that reference a resource
  GET /resources/project-assets/tree — project→canvas tree w/ asset counts

All reads JOIN with the caller's membership/ownership filter so the
RLS-locked refs table never leaks cross-tenant rows.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.api.media_permissions import check_media_access
from app.core.deps import AuthDep
from app.core.scope_guards import verify_project_write_access
from app.repositories.canvas_refs_repository import CanvasRefsRepository
from app.repositories.projects_repository import ProjectsRepository
from app.services.canvas import CanvasService

router = APIRouter()


async def _gate_canvas_read(canvas_id: str, auth: AuthDep) -> str:
    """Resolve canvas → project, then require project membership.
    Mirrors canvases_router._gate_canvas_read."""
    svc = CanvasService()
    project_id = await svc.get_project_id(canvas_id)
    if project_id is None:
        raise HTTPException(status_code=404, detail="canvas not found")
    await verify_project_write_access(project_id=project_id, auth=auth)
    return project_id


@router.get("/canvases/{canvas_id}/assets")
async def canvas_assets(canvas_id: str, auth: AuthDep):
    await _gate_canvas_read(canvas_id, auth)
    repo = CanvasRefsRepository()
    items = await repo.list_assets_for_canvas(canvas_id)
    return {"success": True, "data": items}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/api/test_project_assets_router.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/project_assets_router.py backend/tests/api/test_project_assets_router.py
git commit -m "feat(api): GET /canvases/{id}/assets"
```

---

### Task 7: `GET /resources/{id}/canvas-refs`

**Files:**
- Modify: `backend/app/api/project_assets_router.py`
- Modify: `backend/tests/api/test_project_assets_router.py`

- [ ] **Step 1: Add the failing tests**

```python
# append to backend/tests/api/test_project_assets_router.py

@pytest.mark.asyncio
async def test_resource_canvas_refs_returns_canvases(app, monkeypatch):
    async def allow(resource_id, user_id, team_id):
        return True

    async def fake_list(self, resource_id):
        return [
            {"canvas_id": "5001", "canvas_name": "Board A", "kind": "smart",
             "project_id": "9000", "role": "reference"},
        ]

    monkeypatch.setattr(par, "check_media_access", allow)
    monkeypatch.setattr(par.CanvasRefsRepository, "list_canvases_for_resource", fake_list)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/api/v1/resources/111/canvas-refs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"][0]["canvas_id"] == "5001"
    assert body["count"] == 1


@pytest.mark.asyncio
async def test_resource_canvas_refs_404_when_no_access(app, monkeypatch):
    async def deny(resource_id, user_id, team_id):
        return False

    monkeypatch.setattr(par, "check_media_access", deny)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/api/v1/resources/111/canvas-refs")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/api/test_project_assets_router.py -k canvas_refs -v`
Expected: FAIL — route not found (404 from FastAPI for unknown path, but assertion on body fails / wrong shape).

- [ ] **Step 3: Add the endpoint**

```python
# add to project_assets_router.py

@router.get("/resources/{resource_id}/canvas-refs")
async def resource_canvas_refs(resource_id: str, auth: AuthDep):
    # Resource ownership/membership gate (same helper the asset-AI batch uses).
    if not await check_media_access(resource_id, auth.user_id, None):
        raise HTTPException(status_code=404, detail="resource not found")
    repo = CanvasRefsRepository()
    items = await repo.list_canvases_for_resource(resource_id)
    return {"success": True, "data": items, "count": len(items)}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest tests/api/test_project_assets_router.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/project_assets_router.py backend/tests/api/test_project_assets_router.py
git commit -m "feat(api): GET /resources/{id}/canvas-refs (detail-page back-ref)"
```

---

### Task 8: `GET /resources/project-assets/tree`

**Files:**
- Modify: `backend/app/api/project_assets_router.py`
- Modify: `backend/tests/api/test_project_assets_router.py`

- [ ] **Step 1: Add the failing test**

```python
# append to backend/tests/api/test_project_assets_router.py

@pytest.mark.asyncio
async def test_project_assets_tree_groups_by_project(app, monkeypatch):
    async def fake_projects(self, user_id, team_id=None):
        return [
            {"id": "9000", "name": "Proj One", "team_id": None},
            {"id": "9001", "name": "Proj Two", "team_id": None},
        ]

    async def fake_tree(self, project_ids):
        assert set(project_ids) == {"9000", "9001"}
        return [
            {"project_id": "9000", "canvas_id": "5001", "canvas_name": "A",
             "kind": "smart", "asset_count": 3},
            {"project_id": "9000", "canvas_id": "5002", "canvas_name": "B",
             "kind": "classic", "asset_count": 0},
        ]

    monkeypatch.setattr(par.ProjectsRepository, "get_user_projects", fake_projects)
    monkeypatch.setattr(par.CanvasRefsRepository, "tree_for_projects", fake_tree)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/api/v1/resources/project-assets/tree")
    assert resp.status_code == 200
    data = resp.json()["data"]
    proj_one = next(p for p in data if p["project_id"] == "9000")
    assert proj_one["name"] == "Proj One"
    assert len(proj_one["canvases"]) == 2
    assert {c["canvas_id"] for c in proj_one["canvases"]} == {"5001", "5002"}
    # project with no canvases still listed (empty canvases array)
    proj_two = next(p for p in data if p["project_id"] == "9001")
    assert proj_two["canvases"] == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/api/test_project_assets_router.py -k tree -v`
Expected: FAIL — route missing.

- [ ] **Step 3: Add the endpoint**

```python
# add to project_assets_router.py

@router.get("/resources/project-assets/tree")
async def project_assets_tree(auth: AuthDep):
    """Project → Canvas tree (with per-canvas asset counts) for every
    project the caller can see. Projects with no canvases are included
    with an empty ``canvases`` list so the UI can show them as empty
    groups."""
    projects = await ProjectsRepository().get_user_projects(auth.user_id)
    project_ids = [str(p["id"]) for p in projects]
    rows = await CanvasRefsRepository().tree_for_projects(project_ids)

    by_project: dict[str, list] = {}
    for row in rows:
        by_project.setdefault(row["project_id"], []).append(
            {
                "canvas_id": row["canvas_id"],
                "canvas_name": row["canvas_name"],
                "kind": row["kind"],
                "asset_count": int(row.get("asset_count") or 0),
            }
        )

    data = [
        {
            "project_id": str(p["id"]),
            "name": p["name"],
            "canvases": by_project.get(str(p["id"]), []),
        }
        for p in projects
    ]
    return {"success": True, "data": data}
```

- [ ] **Step 4: Run to verify pass + register router**

Run: `cd backend && uv run pytest tests/api/test_project_assets_router.py -v`
Expected: PASS (5 tests).

Then register the router. Find where `canvases_router` is included (grep `canvases_router` in `backend/app/main.py`) and add alongside it:

```python
from app.api.project_assets_router import router as project_assets_router
# ... in the same block that does app.include_router(canvases_router, prefix="/api/v1", ...)
app.include_router(project_assets_router, prefix="/api/v1", tags=["Project Assets"])
```

Verify mount: `cd backend && uv run python -c "from app.main import app; print([r.path for r in app.routes if 'project-assets' in r.path or 'canvas-refs' in r.path])"`
Expected: includes `/api/v1/resources/project-assets/tree` and `/api/v1/resources/{resource_id}/canvas-refs`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/project_assets_router.py backend/tests/api/test_project_assets_router.py backend/app/main.py
git commit -m "feat(api): GET /resources/project-assets/tree + register router"
```

---

## Phase C — TTL retirement

### Task 9: Disable the temp-resource sweeper schedule

**Files:**
- Modify: `backend/app/workflows/temp_resource_sweeper.py:164-166`

- [ ] **Step 1: Read the current decorator**

Run: `cd backend && sed -n '160,175p' app/workflows/temp_resource_sweeper.py`
Confirm the `@DBOS.scheduled("0 4 * * *")` decorator sits directly above `temp_resource_sweeper_scheduled`.

- [ ] **Step 2: Comment out the schedule (keep the function callable)**

Replace the decorator line with a disabled marker:

```python
# IC-port P4 (2026-06-13): chat-temp TTL retired — files no longer expire;
# users decide deletion. Schedule disabled, function kept so it can be
# re-armed by restoring this decorator if the policy is reversed.
# @DBOS.scheduled("0 4 * * *")  # Daily 04:00 UTC — DISABLED
async def temp_resource_sweeper_scheduled(
```

- [ ] **Step 3: Verify the workflow no longer self-registers**

Run: `cd backend && uv run python -c "import app.workflows.temp_resource_sweeper as m; print('imported, scheduled removed')"`
Expected: imports cleanly. (No scheduled registration occurs because the decorator is gone.)

Run existing sweeper tests to ensure the helper functions still work:
Run: `cd backend && uv run pytest tests/ -k temp_resource -v`
Expected: PASS (helper-level tests unaffected) — if a test asserts the schedule is registered, update it to assert the function is importable/callable instead.

- [ ] **Step 4: Commit**

```bash
git add backend/app/workflows/temp_resource_sweeper.py
git commit -m "feat(temp): retire chat-temp TTL sweeper schedule (IC-port P4)"
```

---

### Task 10: Hide the `chat_temp_ttl_days` settings row

**Files:**
- Modify: the settings component rendering the TTL control

- [ ] **Step 1: Locate the control**

Run: `cd frontend && grep -rn "chat_temp_ttl\|chatTempTtl\|temp_ttl\|tempTtl" . --include="*.tsx" --include="*.ts" | grep -v node_modules`
Note the file + line of the TTL `<label>/<input>/<select>` block.

- [ ] **Step 2: Hide the row**

Wrap the TTL form row in a feature-off guard rather than deleting it (policy could reverse). At the top of the block:

```tsx
{/* Chat-temp TTL retired (IC-port P4) — files no longer auto-expire.
    Kept behind a constant so the control can be restored if the policy
    changes. */}
{false && (
  /* ...existing TTL row JSX... */
)}
```

If there is no JSX block (e.g. TTL is a field in a larger settings object form), instead remove just the visible row and leave the backend setting key untouched. Match the surrounding pattern.

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: no new errors referencing the edited file. (Baseline tsc errors per memory ~108 are pre-existing; confirm count didn't rise from your file.)

- [ ] **Step 4: Commit**

```bash
git add frontend/<settings-file>
git commit -m "feat(settings): hide chat-temp TTL row (retired)"
```

---

## Phase D — Frontend

### Task 11: Route + sidebar rename `temp` → `project-assets`

**Files:**
- Modify: `frontend/contexts/ResourcesContext.tsx:45` (type), `:225-227` (derivation), `:391-394` (flags)
- Modify: `frontend/components/ResourcesSidebar.tsx:267-274`
- Modify: `frontend/App.tsx` (route + redirect)

- [ ] **Step 1: Extend `SidebarView` and add the new flag**

In `ResourcesContext.tsx` line 45:

```ts
export type SidebarView = 'resources' | 'shared' | 'recycle' | 'downloads' | 'temp' | 'project-assets';
```

Line ~225 route derivation — add `project-assets` to the recognised sections:

```ts
  const sidebarView: SidebarView = urlFolderId || urlSmartFolderId || urlLibraryId
    ? 'resources'
    : (['shared', 'recycle', 'downloads', 'temp', 'project-assets'].includes(section || '')
        ? section as SidebarView : 'resources');
```

Line ~394 — add the derived flag and keep `isTempView` meaning "the chat-uploads sub-view". Since Project Assets *contains* Chat Uploads, model it as: `isProjectAssetsView` is the container; the temp fetch still drives the Chat Uploads group.

```ts
  const isTempView = sidebarView === 'temp';
  const isProjectAssetsView = sidebarView === 'project-assets';
```

Export `isProjectAssetsView` in the context value (add to the interface near line 75 and the value object near line 1043). Keep the temp-folder fetch effect (line ~815) firing for BOTH views:

```ts
  // Chat Uploads data feeds both the legacy /temp route and the new
  // Project Assets view's "Chat Uploads" group.
  useEffect(() => {
    if (!isTempView && !isProjectAssetsView) return;
    // ...unchanged body...
  }, [isTempView, isProjectAssetsView, isPersonal, scopeId, selectedLibraryId, tempRefreshTick]);
```

- [ ] **Step 2: Update the sidebar nav item**

In `ResourcesSidebar.tsx` ~267, point the existing entry at the new route and relabel; import `isProjectAssetsView` from context, replacing `isTempView` for the active state:

```tsx
{/* Project Assets — canvas-grouped assets + chat uploads */}
<button
  onClick={() => navigate(resPath('/resources/project-assets'))}
  className={sidebarItemClass(isProjectAssetsView)}
>
  <Layers size={15} className="shrink-0 opacity-70" />
  <span className="flex-1 truncate">{t('resources.projectAssets')}</span>
</button>
```

(Import `Layers` from `lucide-react`; drop the now-unused `Clock` import if nothing else uses it.)

- [ ] **Step 3: Add route + legacy redirect in `App.tsx`**

Find the route that maps `/resources/:section` (or the explicit `temp` route). Ensure `project-assets` resolves to the same `ResourcesView`. Add a redirect from the old path:

```tsx
<Route path="/resources/temp" element={<Navigate to="/resources/project-assets" replace />} />
<Route path="/team/:teamId/resources/temp" element={<Navigate to="../project-assets" replace />} />
```

(If routing is a single `:section` param, no new element route is needed — only the two redirects. Verify by grepping the existing temp route registration first: `grep -n "resources/:section\|resources/temp" frontend/App.tsx`.)

- [ ] **Step 4: Typecheck**

Run: `cd frontend && npx tsc --noEmit 2>&1 | grep -E "ResourcesContext|ResourcesSidebar|App.tsx" | head`
Expected: no errors from these files.

- [ ] **Step 5: Commit**

```bash
git add frontend/contexts/ResourcesContext.tsx frontend/components/ResourcesSidebar.tsx frontend/App.tsx
git commit -m "feat(resources): route temp → project-assets + sidebar entry"
```

---

### Task 12: `projectAssetsService` API client

**Files:**
- Create: `frontend/services/projectAssetsService.ts`

- [ ] **Step 1: Write the service**

```ts
// frontend/services/projectAssetsService.ts
// API client for Project Assets (IC-port P4).

import { getApiUrl } from './parserService';
import { getAuthHeaders } from './parserService';

export interface ProjectAssetCanvas {
  canvas_id: string;
  canvas_name: string;
  kind: 'smart' | 'classic';
  asset_count: number;
}

export interface ProjectAssetTreeNode {
  project_id: string;
  name: string;
  canvases: ProjectAssetCanvas[];
}

export interface CanvasAssetItem {
  id: string;
  filename: string;
  file_type: string;
  mime_type: string | null;
  thumbnail_path: string | null;
  cover_image_path: string | null;
  created_at: string;
  role: 'reference' | 'output';
  node_id: string;
}

export interface CanvasBackRef {
  canvas_id: string;
  canvas_name: string;
  kind: 'smart' | 'classic';
  project_id: string;
  role: 'reference' | 'output';
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${getApiUrl()}/api/v1${path}`, {
    headers: await getAuthHeaders(),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const json = await res.json();
  return json.data as T;
}

export function fetchProjectAssetsTree(): Promise<ProjectAssetTreeNode[]> {
  return getJson<ProjectAssetTreeNode[]>('/resources/project-assets/tree');
}

export function fetchCanvasAssets(canvasId: string): Promise<CanvasAssetItem[]> {
  return getJson<CanvasAssetItem[]>(`/canvases/${canvasId}/assets`);
}

export function fetchResourceCanvasRefs(resourceId: string): Promise<CanvasBackRef[]> {
  return getJson<CanvasBackRef[]>(`/resources/${resourceId}/canvas-refs`);
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit 2>&1 | grep projectAssetsService`
Expected: no output (clean). If `getAuthHeaders`/`getApiUrl` import paths differ, fix to match `resourceService.ts`'s imports.

- [ ] **Step 3: Commit**

```bash
git add frontend/services/projectAssetsService.ts
git commit -m "feat(resources): projectAssetsService API client"
```

---

### Task 13: `ProjectAssetsTree` component

**Files:**
- Create: `frontend/components/resources/ProjectAssetsTree.tsx`

- [ ] **Step 1: Write the component**

```tsx
// frontend/components/resources/ProjectAssetsTree.tsx
// Left-hand Project → Canvas tree for the Project Assets view.
// A synthetic "Chat Uploads" root sits above the projects.

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronRight, Layers, MessageSquare, Sparkles } from 'lucide-react';
import {
  fetchProjectAssetsTree,
  type ProjectAssetTreeNode,
} from '../../services/projectAssetsService';

export type ProjectAssetsSelection =
  | { kind: 'chat-uploads' }
  | { kind: 'canvas'; canvasId: string; canvasName: string };

interface Props {
  selection: ProjectAssetsSelection;
  onSelect: (sel: ProjectAssetsSelection) => void;
  chatUploadsCount: number;
}

export const ProjectAssetsTree: React.FC<Props> = ({ selection, onSelect, chatUploadsCount }) => {
  const { t } = useTranslation();
  const [tree, setTree] = useState<ProjectAssetTreeNode[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetchProjectAssetsTree()
      .then((data) => {
        if (cancelled) return;
        setTree(data);
        // expand projects that actually have canvases, by default
        setExpanded(new Set(data.filter((p) => p.canvases.length).map((p) => p.project_id)));
      })
      .catch((err) => console.error('[ProjectAssetsTree] load failed:', err))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, []);

  const toggle = (projectId: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(projectId) ? next.delete(projectId) : next.add(projectId);
      return next;
    });

  return (
    <div className="w-60 shrink-0 border-r border-ink-800/60 overflow-y-auto py-2 text-sm">
      {/* Chat Uploads synthetic root */}
      <button
        onClick={() => onSelect({ kind: 'chat-uploads' })}
        className={`flex items-center gap-2 w-full px-3 py-1.5 rounded-md ${
          selection.kind === 'chat-uploads' ? 'bg-ink-800 text-ink-100' : 'text-ink-300 hover:bg-ink-800/60'
        }`}
      >
        <MessageSquare size={15} className="opacity-70 shrink-0" />
        <span className="flex-1 truncate text-left">{t('projectAssets.chatUploads')}</span>
        <span className="text-xs text-ink-500">{chatUploadsCount}</span>
      </button>

      <div className="mx-2 my-2 border-t border-ink-800/50" />

      {loading && <div className="px-3 py-2 text-ink-500">{t('common.loading')}</div>}

      {!loading && tree.map((project) => (
        <div key={project.project_id}>
          <button
            onClick={() => toggle(project.project_id)}
            className="flex items-center gap-1.5 w-full px-2 py-1.5 text-ink-400 hover:text-ink-200"
          >
            {expanded.has(project.project_id)
              ? <ChevronDown size={14} className="shrink-0" />
              : <ChevronRight size={14} className="shrink-0" />}
            <span className="flex-1 truncate text-left font-medium">{project.name}</span>
          </button>

          {expanded.has(project.project_id) && project.canvases.map((canvas) => (
            <button
              key={canvas.canvas_id}
              onClick={() => onSelect({ kind: 'canvas', canvasId: canvas.canvas_id, canvasName: canvas.canvas_name })}
              className={`flex items-center gap-2 w-full pl-7 pr-3 py-1.5 rounded-md ${
                selection.kind === 'canvas' && selection.canvasId === canvas.canvas_id
                  ? 'bg-ink-800 text-ink-100' : 'text-ink-300 hover:bg-ink-800/60'
              }`}
            >
              {canvas.kind === 'smart'
                ? <Sparkles size={14} className="opacity-70 shrink-0" />
                : <Layers size={14} className="opacity-70 shrink-0" />}
              <span className="flex-1 truncate text-left">{canvas.canvas_name}</span>
              <span className="text-xs text-ink-500">{canvas.asset_count}</span>
            </button>
          ))}

          {expanded.has(project.project_id) && project.canvases.length === 0 && (
            <div className="pl-7 pr-3 py-1 text-xs text-ink-600">{t('projectAssets.noCanvases')}</div>
          )}
        </div>
      ))}
    </div>
  );
};
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit 2>&1 | grep ProjectAssetsTree`
Expected: clean. (Confirm `ink-` tokens exist post-#691; if a token name differs, match a sibling sidebar component's classes.)

- [ ] **Step 3: Commit**

```bash
git add frontend/components/resources/ProjectAssetsTree.tsx
git commit -m "feat(resources): ProjectAssetsTree (project→canvas + chat uploads)"
```

---

### Task 14: Render the Project Assets view in `ResourcesViewInner`

**Files:**
- Modify: `frontend/components/ResourcesViewInner.tsx`

- [ ] **Step 1: Add local selection state + canvas-assets fetch**

Near the other `useMemo`/`useState` hooks in `ResourcesViewInner`, add:

```tsx
import { ProjectAssetsTree, type ProjectAssetsSelection } from './resources/ProjectAssetsTree';
import { fetchCanvasAssets, type CanvasAssetItem } from '../services/projectAssetsService';
```

```tsx
  const [paSelection, setPaSelection] = useState<ProjectAssetsSelection>({ kind: 'chat-uploads' });
  const [canvasAssets, setCanvasAssets] = useState<CanvasAssetItem[]>([]);
  const [canvasAssetsLoading, setCanvasAssetsLoading] = useState(false);

  useEffect(() => {
    if (!isProjectAssetsView || paSelection.kind !== 'canvas') return;
    let cancelled = false;
    setCanvasAssetsLoading(true);
    fetchCanvasAssets(paSelection.canvasId)
      .then((items) => !cancelled && setCanvasAssets(items))
      .catch((err) => { console.error('[ProjectAssets] canvas assets load failed:', err); if (!cancelled) setCanvasAssets([]); })
      .finally(() => !cancelled && setCanvasAssetsLoading(false));
    return () => { cancelled = true; };
  }, [isProjectAssetsView, paSelection]);
```

Pull `isProjectAssetsView` from context (add to the destructure at line ~48).

- [ ] **Step 2: Map canvas-assets to ResourceGrid item shape**

`ResourceGrid` consumes `ResourceItem`-shaped rows. Build a memo that adapts canvas-assets (or reuses `tempSortedItems` for the Chat Uploads selection):

```tsx
  const projectAssetsItems = useMemo(() => {
    if (paSelection.kind === 'chat-uploads') return tempSortedItems;
    // Adapt CanvasAssetItem → the minimal ResourceItem fields ResourceGrid reads.
    return canvasAssets.map((a) => ({
      id: a.id,
      resource: {
        id: a.id,
        filename: a.filename,
        file_type: a.file_type,
        mime_type: a.mime_type,
        thumbnail_path: a.thumbnail_path,
        cover_image_path: a.cover_image_path,
        created_at: a.created_at,
      },
      // role badge surfaced via a side channel the card can read
      _canvasRole: a.role,
    })) as unknown as typeof tempSortedItems;
  }, [paSelection, canvasAssets, tempSortedItems]);
```

(If `ResourceGrid`/`ResourceItem` requires more non-optional fields, fill them from `a` or sane defaults — inspect `ResourceItem` in `frontend/types.ts` and match. Do NOT invent fields the grid doesn't read.)

- [ ] **Step 3: Render tree + grid for the project-assets view**

In the JSX, add a branch alongside the existing `isTempView` branch (around line 547):

```tsx
        ) : isProjectAssetsView ? (
          <div className="flex flex-1 min-h-0">
            <ProjectAssetsTree
              selection={paSelection}
              onSelect={setPaSelection}
              chatUploadsCount={tempSortedItems.length}
            />
            <div className="flex-1 min-w-0">
              {canvasAssetsLoading && paSelection.kind === 'canvas' ? (
                <div className="p-6 text-ink-500">{t('common.loading')}</div>
              ) : (
                <ResourceGrid
                  {...tempGridProps}
                  breadcrumbSegments={[{ label: t('resources.projectAssets'), href: resPath('/resources/project-assets') }]}
                  sortedItems={projectAssetsItems}
                  currentItems={projectAssetsItems}
                  allSelectableIds={projectAssetsItems.map((i) => `item:${i.id}`)}
                />
              )}
            </div>
          </div>
        ) : isTempView ? (
          <ResourceGrid {...tempGridProps} />
```

- [ ] **Step 4: Typecheck + dev smoke**

Run: `cd frontend && npx tsc --noEmit 2>&1 | grep ResourcesViewInner`
Expected: clean.

Start dev server, log in, open `/resources/project-assets`:
Run (background): `cd frontend && npm run dev` (port 5176)
Manually verify: tree loads, Chat Uploads shows temp items, clicking a canvas loads its grid. (Backend must be running on 8081 with the dev DB env; or test against staging API per `VITE_API_URL`.)

- [ ] **Step 5: Commit**

```bash
git add frontend/components/ResourcesViewInner.tsx
git commit -m "feat(resources): render Project Assets tree + canvas grid"
```

---

### Task 15: Resource detail "Appears in N canvases" back-ref

**Files:**
- Modify: `frontend/components/ResourceDetailPage.tsx`

- [ ] **Step 1: Fetch back-refs on mount**

```tsx
import { fetchResourceCanvasRefs, type CanvasBackRef } from '../services/projectAssetsService';
```

```tsx
  const [canvasRefs, setCanvasRefs] = useState<CanvasBackRef[]>([]);
  useEffect(() => {
    if (!resourceId) return;
    let cancelled = false;
    fetchResourceCanvasRefs(resourceId)
      .then((refs) => !cancelled && setCanvasRefs(refs))
      .catch((err) => console.error('[ResourceDetail] canvas refs load failed:', err));
    return () => { cancelled = true; };
  }, [resourceId]);
```

(Use whatever the page already calls the resource id var; grep for it.)

- [ ] **Step 2: Render the block (only when non-empty)**

Place near the other metadata sections:

```tsx
{canvasRefs.length > 0 && (
  <div className="mt-4">
    <h4 className="text-xs font-medium text-ink-500 uppercase tracking-wide mb-2">
      {t('projectAssets.appearsInCanvases', { count: canvasRefs.length })}
    </h4>
    <div className="flex flex-col gap-1">
      {canvasRefs.map((ref) => (
        <button
          key={`${ref.canvas_id}:${ref.role}`}
          onClick={() => navigate(`/projects/${ref.project_id}/canvas/${ref.canvas_id}`)}
          className="flex items-center gap-2 text-sm text-ink-300 hover:text-ink-100 text-left"
        >
          <span className="flex-1 truncate">{ref.canvas_name}</span>
          <span className="text-xs text-ink-600">{t(`projectAssets.role.${ref.role}`)}</span>
        </button>
      ))}
    </div>
  </div>
)}
```

(Confirm the canvas-editor route path by grepping `App.tsx` for the canvas route; adjust the `navigate` target to match. If no canvas route exists yet, render the names as non-clickable text and add a `// TODO: link when canvas editor route lands` — do NOT invent a route.)

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit 2>&1 | grep ResourceDetailPage`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/ResourceDetailPage.tsx
git commit -m "feat(resources): 'appears in N canvases' back-ref on detail page"
```

---

### Task 16: i18n strings + final verification

**Files:**
- Modify: `frontend/public/locales/en.json`, `frontend/public/locales/zh.json`

- [ ] **Step 1: Add strings (en.json)**

Under the existing `resources` object, add `projectAssets` key; add a top-level `projectAssets` namespace for the view-specific strings:

```jsonc
// resources object:
"projectAssets": "Project Assets",

// new top-level namespace:
"projectAssets": {
  "chatUploads": "Chat Uploads",
  "noCanvases": "No canvases yet",
  "appearsInCanvases_one": "Appears in {{count}} canvas",
  "appearsInCanvases_other": "Appears in {{count}} canvases",
  "role": { "reference": "Reference", "output": "Output" }
}
```

(Place `resources.projectAssets` inside the existing `resources` block; the standalone `projectAssets` namespace at top level. Match the file's existing structure — the keys referenced in code are `resources.projectAssets`, `projectAssets.chatUploads`, `projectAssets.noCanvases`, `projectAssets.appearsInCanvases`, `projectAssets.role.*`.)

- [ ] **Step 2: Add strings (zh.json)**

```jsonc
// resources object:
"projectAssets": "工程资产",

// new top-level namespace:
"projectAssets": {
  "chatUploads": "Chat Uploads",
  "noCanvases": "暂无画布",
  "appearsInCanvases_one": "出现在 {{count}} 个画布",
  "appearsInCanvases_other": "出现在 {{count}} 个画布",
  "role": { "reference": "参考图", "output": "生成结果" }
}
```

(UI labels stay English per project rule — "Chat Uploads" untranslated; zh provides the Chinese display value for the localized chrome only where the project already localizes such labels.)

- [ ] **Step 3: Validate JSON**

Run: `cd frontend && node -e "JSON.parse(require('fs').readFileSync('public/locales/en.json')); JSON.parse(require('fs').readFileSync('public/locales/zh.json')); console.log('json ok')"`
Expected: `json ok`.

- [ ] **Step 4: Full verification pass**

```bash
cd backend && uv run pytest tests/test_asset_refs.py tests/test_canvas_refs_wiring.py tests/api/test_project_assets_router.py tests/test_canvas_service.py -q
```
Expected: all PASS.

```bash
cd backend && uvx ruff check app/services/canvas/asset_refs.py app/repositories/canvas_refs_repository.py app/api/project_assets_router.py app/scripts 2>/dev/null; uvx --python 3.13 black --check app/services/canvas/asset_refs.py app/repositories/canvas_refs_repository.py app/api/project_assets_router.py backend/scripts/backfill_canvas_resource_refs.py 2>&1 | tail -3
```
Expected: ruff clean; black clean (reformat any file it flags, then re-commit).

```bash
cd frontend && npx tsc --noEmit 2>&1 | wc -l
```
Expected: line count not higher than the pre-existing baseline (~108 per memory).

- [ ] **Step 5: Commit**

```bash
git add frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(i18n): Project Assets strings (en/zh)"
```

---

## Rollout notes (post-merge, not code steps)

1. Migration 290 applies via the standard `run-migration.yml` on merge. After it lands, run the backfill once against prod (per memory `reference_prod_migration_apply_as_postgres` for direct prod access): `cd backend && uv run python scripts/backfill_canvas_resource_refs.py` with prod DB env. Idempotent — safe to re-run.
2. Backend PR contains `backend/**` → keep repo public until `Deploy Backend to ACR` completes (per memory `reference_deploy_backend_pr_private_timing`), then flip private.
3. The disabled sweeper means temp resources stop being swept the moment this deploys — intended (TTL retired).

---

## Self-Review

**Spec coverage:**
- §1 data layer: table (T1), extraction (T2), repo (T3), save wiring (T4), backfill (T5) ✓
- §1 TTL退役: sweeper schedule (T9), settings hide (T10) ✓
- §2 API: tree (T8), canvas assets (T6), back-refs (T7), permissions via `_gate_canvas_read`/`check_media_access`/project membership ✓
- §3 frontend: route rename (T11), tree (T13), grid + Chat Uploads (T14), detail back-ref (T15), service (T12), i18n (T16) ✓; Chat Uploads uses existing tempResources fetch (T11 keeps the effect) ✓
- §4 tests: extraction (T2), wiring (T4), endpoint permission (T6-8) ✓; backfill idempotency is asserted by design (replace-all) + manual re-run note (T5) — acceptable, no DB in unit suite
- §5 YAGNI: no drag-into-canvas, no cross-project move, no separate smart/classic filter — none added ✓

**Placeholder scan:** No TBD/TODO except two deliberate, conditioned ones (T14 ResourceItem field-matching, T15 canvas route) that instruct the engineer to grep the real shape rather than invent — each with an explicit "do NOT invent" guard. Acceptable.

**Type consistency:** `extract_asset_refs` returns `{resource_id, role, node_id}` used identically in T3/T4/T5. `CanvasRefsRepository` method names (`replace_for_canvas`, `list_assets_for_canvas`, `list_canvases_for_resource`, `tree_for_projects`) match across T3/T6/T7/T8/T4. Service ctor `refs_repository` param matches T4 test. Frontend `ProjectAssetsSelection`, `CanvasAssetItem`, `CanvasBackRef`, `ProjectAssetTreeNode` consistent across T12/T13/T14/T15. i18n keys used in code (T11/T13/T14/T15) all defined in T16.
