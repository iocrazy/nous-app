# Chat `@`-Reference Resource Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users type `@` in the chat composer to insert a reference chip to an existing library resource, with the agent receiving metadata in `<available_resources>` plus a `ResourceFetch(id, mode?)` tool to load full content on demand.

**Architecture:** tiptap mention extension in `ChatInput`; floating dropdown picker; backend `resource_ref_resolver` produces metadata for prompt_composer; new `ResourceFetch` tool re-validates permissions and dispatches per resource kind. Mirrors the existing `<available_skills>` + `Skill()` lazy-load pattern.

**Tech Stack:** TypeScript / React 19 / @tiptap/react@3 / @tiptap/extension-mention (new dep), FastAPI / DBOS / Supabase / asyncpg, pytest / vitest.

**Spec reference:** `docs/superpowers/specs/2026-05-27-chat-at-reference-resource-design.md` — every section in this plan corresponds to a section in the spec; cross-reference for design rationale.

---

## Pre-flight

- [ ] **Branch off latest master.**

```bash
cd /Volumes/program/project-code/repos/mediahub
git fetch origin
git checkout master && git pull
git checkout -b feature/chat-at-reference-resource
```

- [ ] **Confirm tiptap mention extension can be installed.**

```bash
cd frontend
npm view @tiptap/extension-mention version
# Expect: 3.x.x available (matches the existing @tiptap/* 3.22.2 line)
```

- [ ] **Smoke-check the spec is committed locally.**

```bash
test -f docs/superpowers/specs/2026-05-27-chat-at-reference-resource-design.md && echo OK
```

---

## File map (created/modified)

### Backend
- Create: `backend/app/api/resources_search_router.py` — `GET /api/v1/resources/search`
- Create: `backend/app/services/ai/tools/__init__.py` (new package)
- Create: `backend/app/services/ai/tools/resource_fetch_tool.py` — the `ResourceFetch` tool
- Create: `backend/app/services/ai/chat/resource_ref_resolver.py` — resolve `kind='resource_ref'` attachments to metadata
- Modify: `backend/app/services/ai/chat/ai_library_chat_service.py:223-610` — call resolver; expose tool; pass refs to composer
- Modify: `backend/app/services/ai/prompts/prompt_composer.py` — render `<available_resources>` block
- Modify: `backend/app/repositories/resources_repository.py` — add `list_accessible_for_user(...)` helper
- Modify: `backend/app/main.py` — register new router

### Frontend
- Modify: `frontend/package.json` — add `@tiptap/extension-mention`
- Modify: `frontend/types.ts` — add `ResourceRefAttachment` type
- Create: `frontend/services/resourceSearchService.ts` — REST client + AbortController
- Create: `frontend/hooks/useResourceSearch.ts` — 150 ms debounce + prefix cache
- Create: `frontend/components/chat/ResourcePickerSuggestion.tsx` — dropdown UI
- Create: `frontend/components/chat/ResourceChipNode.tsx` — tiptap NodeView
- Create: `frontend/components/chat/ChatInputResourceMention.ts` — extension wiring
- Modify: `frontend/components/chat/ChatInput.tsx` — textarea → tiptap EditorContent (keep prop surface)
- Modify: `frontend/components/AIChatPanel.tsx` — pass through new attachments path (minor)
- Modify: `frontend/public/locales/en.json` + `frontend/public/locales/zh.json` — picker i18n

### Tests (created alongside)
- `backend/tests/api/test_resources_search_router.py`
- `backend/tests/services/ai/chat/test_resource_ref_resolver.py`
- `backend/tests/services/ai/tools/test_resource_fetch_tool.py`
- `backend/tests/services/ai/prompts/test_prompt_composer_resources.py`
- `frontend/components/chat/ChatInput.test.tsx` (new file; existing ChatInput has no test)
- `frontend/components/chat/ResourcePickerSuggestion.test.tsx`
- `frontend/components/chat/ChatInputResourceMention.test.ts`
- `frontend/hooks/useResourceSearch.test.ts`

---

## Task 1 — Backend: `list_accessible_for_user` repo helper

**Files:**
- Modify: `backend/app/repositories/resources_repository.py`
- Test: `backend/tests/repositories/test_resources_repository_accessible.py` (create)

- [ ] **Step 1 — Write the failing test.**

```python
"""Verify list_accessible_for_user filters by ownership + team membership + q + kinds."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from app.repositories.resources_repository import ResourcesRepository


@pytest.mark.asyncio
async def test_accepts_q_and_kinds_and_limit(monkeypatch):
    repo = ResourcesRepository()
    captured = {}

    async def _fake_fetch(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    with patch("app.db.engine.fetch_all", side_effect=_fake_fetch):
        await repo.list_accessible_for_user(
            user_id="user-1",
            q="story",
            kinds=["video", "image"],
            limit=20,
        )

    assert "ilike" in captured["sql"].lower()
    assert captured["params"]["user_id"] == "user-1"
    assert "story" in captured["params"].get("q_like", "")
    assert "video" in captured["params"]["kinds"]
    assert "image" in captured["params"]["kinds"]
    assert captured["params"]["limit"] == 20


@pytest.mark.asyncio
async def test_default_limit_is_20_and_caps_at_50(monkeypatch):
    repo = ResourcesRepository()
    captured = {}

    async def _fake_fetch(sql, params):
        captured["params"] = params
        return []

    with patch("app.db.engine.fetch_all", side_effect=_fake_fetch):
        await repo.list_accessible_for_user(user_id="u", limit=999)

    assert captured["params"]["limit"] == 50  # capped
```

- [ ] **Step 2 — Run; expect FAIL (method does not exist).**

```bash
cd backend
uv run pytest tests/repositories/test_resources_repository_accessible.py -v
```

- [ ] **Step 3 — Implement the method.**

In `backend/app/repositories/resources_repository.py`, add (near other list methods):

```python
async def list_accessible_for_user(
    self,
    *,
    user_id: str,
    q: str = "",
    kinds: list[str] | None = None,
    limit: int = 20,
    cursor: str | None = None,
) -> list[dict]:
    """Resources the user can read: own personal + team-shared.

    Returns list of {id, name, kind, mime, size, scope, updated_at}.
    Used by the @-reference picker.
    """
    from app.db import engine as db_engine

    capped_limit = min(max(int(limit), 1), 50)
    kinds_list = list(kinds or [])

    sql_parts = [
        "SELECT r.id::text, r.filename AS name, ",
        "       r.mime_type AS mime, r.file_size AS size, ",
        "       r.updated_at, ri.scope_type, ri.scope_id::text ",
        "FROM public.resources r ",
        "JOIN public.resource_items ri ON ri.resource_id = r.id ",
        "WHERE r.is_trashed = false AND ri.is_trashed = false ",
        "  AND ( (ri.scope_type = 'personal' AND ri.scope_id::text = :user_id) ",
        "        OR (ri.scope_type = 'team' AND ri.scope_id IN ( ",
        "             SELECT team_id FROM public.team_members WHERE user_id = :user_id ",
        "        )) ) ",
    ]
    params: dict = {"user_id": user_id, "limit": capped_limit}

    if q:
        sql_parts.append("AND r.filename ILIKE :q_like ")
        params["q_like"] = f"%{q}%"

    if kinds_list:
        sql_parts.append("AND r.mime_type ~ :kinds_re ")
        params["kinds"] = kinds_list
        # Map kinds → regex segments. Caller passes canonical kinds.
        regex_segments = []
        for k in kinds_list:
            if k == "video":
                regex_segments.append("^video/")
            elif k == "image":
                regex_segments.append("^image/")
            elif k == "audio":
                regex_segments.append("^audio/")
            elif k == "pdf":
                regex_segments.append("^application/pdf$")
            elif k == "doc":
                regex_segments.append("^(text/|application/json)")
        params["kinds_re"] = "|".join(regex_segments) if regex_segments else "."

    sql_parts.append("ORDER BY r.updated_at DESC LIMIT :limit")
    rows = await db_engine.fetch_all("".join(sql_parts), params)
    return rows or []
```

- [ ] **Step 4 — Run; expect 2 passed.**

```bash
cd backend
uv run pytest tests/repositories/test_resources_repository_accessible.py -v
```

- [ ] **Step 5 — Commit.**

```bash
git add backend/app/repositories/resources_repository.py \
        backend/tests/repositories/test_resources_repository_accessible.py
git commit -m "feat(resources): list_accessible_for_user repo helper

Filters to resources the user can read: own personal + team-shared.
Supports q (filename ILIKE), kinds (mime regex), limit (capped at 50).
Used by the upcoming @-reference picker endpoint."
```

---

## Task 2 — Backend: `GET /api/v1/resources/search` endpoint

**Files:**
- Create: `backend/app/api/resources_search_router.py`
- Modify: `backend/app/main.py` (register router)
- Test: `backend/tests/api/test_resources_search_router.py`

- [ ] **Step 1 — Write the failing test.**

```python
"""Smoke-test the search router shape + permission gate."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_search_requires_auth():
    r = client.get("/api/v1/resources/search?q=story")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_search_returns_results_and_counts(monkeypatch):
    fake_rows = [
        {"id": "1", "name": "story.md", "kind": "doc", "mime": "text/markdown",
         "size": 100, "scope_type": "personal", "scope_id": "u",
         "updated_at": "2026-05-24T10:00:00Z"},
    ]

    async def _fake_list(**kwargs):
        return fake_rows

    with patch(
        "app.repositories.resources_repository.ResourcesRepository.list_accessible_for_user",
        side_effect=_fake_list,
    ), patch(
        "app.api.resources_search_router.get_current_user",
        return_value={"id": "u"},
    ):
        r = client.get("/api/v1/resources/search?q=story&limit=5")

    assert r.status_code == 200
    body = r.json()
    assert "results" in body and "counts" in body
    assert body["results"][0]["name"] == "story.md"
```

- [ ] **Step 2 — Run; expect FAIL.**

```bash
cd backend
uv run pytest tests/api/test_resources_search_router.py -v
```

- [ ] **Step 3 — Create the router.**

Create `backend/app/api/resources_search_router.py`:

```python
"""GET /api/v1/resources/search — picker backend for chat @-reference.

Returns paginated resources the caller can read (own personal + team-shared)
with optional `q` (filename substring) and `kinds` (csv) filters.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_current_user
from app.repositories.resources_repository import ResourcesRepository

router = APIRouter(prefix="/api/v1/resources", tags=["resources"])


def _kind_from_mime(mime: str | None) -> str:
    m = (mime or "").lower()
    if m.startswith("video/"):
        return "video"
    if m.startswith("image/"):
        return "image"
    if m.startswith("audio/"):
        return "audio"
    if m == "application/pdf":
        return "pdf"
    return "doc"


@router.get("/search")
async def search_resources(
    q: str = Query("", max_length=128),
    kinds: Optional[str] = Query(None, description="csv of video,image,doc,audio,pdf"),
    limit: int = Query(20, ge=1, le=50),
    user=Depends(get_current_user),
):
    """Search visible resources for the @-reference picker."""
    kinds_list: list[str] = []
    if kinds:
        kinds_list = [k.strip() for k in kinds.split(",") if k.strip() in
                      {"video", "image", "doc", "audio", "pdf"}]

    repo = ResourcesRepository()
    rows = await repo.list_accessible_for_user(
        user_id=str(user["id"]),
        q=q,
        kinds=kinds_list or None,
        limit=limit,
    )

    results = []
    counts = {"all": 0, "video": 0, "image": 0, "doc": 0, "audio": 0, "pdf": 0}
    for row in rows:
        kind = _kind_from_mime(row.get("mime"))
        counts[kind] += 1
        counts["all"] += 1
        results.append({
            "id": str(row["id"]),
            "name": row["name"],
            "kind": kind,
            "mime": row.get("mime"),
            "size": row.get("size"),
            "scope": {"type": row["scope_type"], "id": str(row["scope_id"])},
            "updated_at": row["updated_at"],
            "thumbnail_url": None,
        })

    return {"results": results, "counts": counts, "next_cursor": None}
```

- [ ] **Step 4 — Register the router in `backend/app/main.py`.**

Find the block where other routers are included (search for `resources_router` or `app.include_router`). Add:

```python
from app.api.resources_search_router import router as resources_search_router
app.include_router(resources_search_router)
```

- [ ] **Step 5 — Run; expect 2 passed.**

```bash
cd backend
uv run pytest tests/api/test_resources_search_router.py -v
```

- [ ] **Step 6 — Commit.**

```bash
git add backend/app/api/resources_search_router.py \
        backend/app/main.py \
        backend/tests/api/test_resources_search_router.py
git commit -m "feat(api): GET /resources/search for @-reference picker

Returns {results, counts, next_cursor}. Filters by q (substring) +
kinds (csv: video/image/doc/audio/pdf) + limit (1-50). Permission
gated by get_current_user — frontend cannot bypass scope."
```

---

## Task 3 — Backend: `resource_ref_resolver`

**Files:**
- Create: `backend/app/services/ai/chat/resource_ref_resolver.py`
- Test: `backend/tests/services/ai/chat/test_resource_ref_resolver.py`

- [ ] **Step 1 — Write the failing test.**

```python
"""Verify resource_ref_resolver: permission filter, dedup, warning emission."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.chat.resource_ref_resolver import resolve_resource_refs


@pytest.mark.asyncio
async def test_dedupes_same_id():
    attachments = [
        {"kind": "resource_ref", "resource_id": "1", "name": "a.md",
         "mime": "text/markdown", "scope": {"type": "personal", "id": "u"}},
        {"kind": "resource_ref", "resource_id": "1", "name": "a.md",
         "mime": "text/markdown", "scope": {"type": "personal", "id": "u"}},
    ]
    with patch(
        "app.services.ai.chat.resource_ref_resolver._fetch_accessible_meta",
        return_value={"1": {"id": "1", "name": "a.md", "kind": "doc",
                            "mime": "text/markdown", "size": 100,
                            "scope": "personal", "updated_at": "2026-05-24T00:00:00Z",
                            "brief": None}},
    ):
        refs, warnings = await resolve_resource_refs(attachments, user_id="u")

    assert len(refs) == 1, f"expected dedup to 1, got {len(refs)}"
    assert warnings == []


@pytest.mark.asyncio
async def test_inaccessible_becomes_warning_not_ref():
    attachments = [
        {"kind": "resource_ref", "resource_id": "999", "name": "ghost.md",
         "mime": "text/markdown", "scope": {"type": "personal", "id": "u"}},
    ]
    with patch(
        "app.services.ai.chat.resource_ref_resolver._fetch_accessible_meta",
        return_value={},  # nothing accessible
    ):
        refs, warnings = await resolve_resource_refs(attachments, user_id="u")

    assert refs == []
    assert len(warnings) == 1
    assert "ghost.md" in warnings[0]


@pytest.mark.asyncio
async def test_skips_non_resource_ref_kinds():
    attachments = [{"kind": "image", "url": "https://example/img.png"}]
    refs, warnings = await resolve_resource_refs(attachments, user_id="u")
    assert refs == []
    assert warnings == []
```

- [ ] **Step 2 — Run; expect FAIL (module not found).**

```bash
cd backend
uv run pytest tests/services/ai/chat/test_resource_ref_resolver.py -v
```

- [ ] **Step 3 — Implement the resolver.**

Create `backend/app/services/ai/chat/resource_ref_resolver.py`:

```python
"""Resolve attachments whose ``kind == 'resource_ref'`` into metadata-only
references the prompt composer will render in ``<available_resources>``.

Does NOT load resource content (that happens lazily via the ResourceFetch
tool when the agent calls it). Re-validates user accessibility server-side;
the frontend ``scope`` field is treated as a hint, never trusted.
"""

from __future__ import annotations

from typing import Any

from loguru import logger


def _kind_from_mime(mime: str | None) -> str:
    m = (mime or "").lower()
    if m.startswith("video/"):
        return "video"
    if m.startswith("image/"):
        return "image"
    if m.startswith("audio/"):
        return "audio"
    if m == "application/pdf":
        return "pdf"
    return "doc"


async def _fetch_accessible_meta(
    user_id: str, resource_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """Return dict of {id: meta} for those the user can read. Missing ids = not accessible."""
    from app.db import engine as db_engine

    if not resource_ids:
        return {}

    rows = await db_engine.fetch_all(
        """
        SELECT r.id::text AS id, r.filename AS name,
               r.mime_type AS mime, r.file_size AS size,
               r.description AS brief, r.updated_at,
               ri.scope_type, ri.scope_id::text AS scope_id
          FROM public.resources r
          JOIN public.resource_items ri ON ri.resource_id = r.id
         WHERE r.id::text = ANY(:ids)
           AND r.is_trashed = false
           AND ri.is_trashed = false
           AND ( (ri.scope_type = 'personal' AND ri.scope_id::text = :uid)
                 OR (ri.scope_type = 'team' AND ri.scope_id IN (
                       SELECT team_id FROM public.team_members WHERE user_id = :uid
                 )) )
        """,
        {"ids": resource_ids, "uid": user_id},
    )
    return {
        row["id"]: {
            "id": row["id"],
            "name": row["name"],
            "kind": _kind_from_mime(row.get("mime")),
            "mime": row.get("mime"),
            "size": row.get("size"),
            "scope": (
                "personal" if row["scope_type"] == "personal"
                else f"team:{row['scope_id']}"
            ),
            "updated_at": row["updated_at"],
            "brief": row.get("brief"),
        }
        for row in (rows or [])
    }


async def resolve_resource_refs(
    attachments: list[dict] | None, *, user_id: str
) -> tuple[list[dict], list[str]]:
    """Return ``(refs_for_prompt, warnings_for_user)``.

    ``refs_for_prompt`` is the list of dicts the prompt composer renders.
    ``warnings_for_user`` is a list of human messages like
    ``"Skipped: ghost.md (deleted or no longer accessible)"`` that the UI
    shows above the agent reply.
    """
    if not attachments:
        return [], []

    # Dedup snapshot inputs by id, keeping the first occurrence
    seen: set[str] = set()
    snapshots: list[dict] = []
    for att in attachments:
        if att.get("kind") != "resource_ref":
            continue
        rid = str(att.get("resource_id", ""))
        if not rid or rid in seen:
            continue
        seen.add(rid)
        snapshots.append({
            "resource_id": rid,
            "name": att.get("name") or rid,
        })

    if not snapshots:
        return [], []

    accessible = await _fetch_accessible_meta(user_id, [s["resource_id"] for s in snapshots])

    refs: list[dict] = []
    warnings: list[str] = []
    for snap in snapshots:
        meta = accessible.get(snap["resource_id"])
        if meta is None:
            warnings.append(
                f"Skipped: {snap['name']} (deleted or no longer accessible)"
            )
            logger.info(
                f"[resource_ref_resolver] dropped inaccessible ref id={snap['resource_id']!r} name={snap['name']!r} user={user_id}"
            )
            continue
        refs.append(meta)
    return refs, warnings
```

- [ ] **Step 4 — Run; expect 3 passed.**

```bash
cd backend
mkdir -p tests/services/ai/chat
touch tests/__init__.py tests/services/__init__.py \
      tests/services/ai/__init__.py tests/services/ai/chat/__init__.py
uv run pytest tests/services/ai/chat/test_resource_ref_resolver.py -v
```

- [ ] **Step 5 — Commit.**

```bash
git add backend/app/services/ai/chat/resource_ref_resolver.py \
        backend/tests/services/ai/chat/test_resource_ref_resolver.py \
        backend/tests/services/ \
        backend/tests/__init__.py
git commit -m "feat(chat): resource_ref_resolver — metadata-only refs

Filters frontend-supplied resource_ref attachments by server-side
permission (own personal + team-shared). Dedupes by id. Returns
(refs_for_prompt, warnings_for_user) — inaccessible refs become
human-visible warnings instead of LLM-visible refs."
```

---

## Task 4 — Backend: `<available_resources>` block in prompt_composer

**Files:**
- Modify: `backend/app/services/ai/prompts/prompt_composer.py`
- Test: `backend/tests/services/ai/prompts/test_prompt_composer_resources.py`

- [ ] **Step 1 — Write the failing test.**

```python
"""<available_resources> renders when refs present, omits when absent."""

from __future__ import annotations

import pytest

from app.services.ai.prompts.prompt_composer import render_available_resources


def test_renders_block_for_each_ref():
    refs = [
        {"id": "1", "name": "story.md", "kind": "doc", "mime": "text/markdown",
         "size": 2438, "scope": "personal", "updated_at": "2026-05-24T10:00:00Z",
         "brief": None},
        {"id": "2", "name": "pitch.mp4", "kind": "video", "mime": "video/mp4",
         "size": 18_000_000, "scope": "team:alpha",
         "updated_at": "2026-05-20T10:00:00Z", "brief": "Storyboard pitch"},
    ]
    block = render_available_resources(refs)
    assert "<available_resources>" in block
    assert "</available_resources>" in block
    assert 'id="1"' in block
    assert 'name="story.md"' in block
    assert 'name="pitch.mp4"' in block
    assert "ResourceFetch" in block  # tool usage hint included


def test_omits_block_when_no_refs():
    assert render_available_resources([]) == ""
    assert render_available_resources(None) == ""
```

- [ ] **Step 2 — Run; expect FAIL.**

```bash
cd backend
uv run pytest tests/services/ai/prompts/test_prompt_composer_resources.py -v
```

- [ ] **Step 3 — Add the renderer.**

Append to `backend/app/services/ai/prompts/prompt_composer.py`:

```python
def render_available_resources(refs: list[dict] | None) -> str:
    """Render the ``<available_resources>`` block for resource_ref refs.

    Returns empty string when there are no refs so the system message
    cache key stays stable for turns without any @-mention.
    """
    if not refs:
        return ""

    def _fmt_size(n: int | None) -> str:
        if not n:
            return ""
        if n < 1024:
            return f"{n}B"
        if n < 1024 * 1024:
            return f"{n // 1024}KB"
        return f"{n // (1024 * 1024)}MB"

    lines = ["<available_resources>"]
    for r in refs:
        attrs = [
            f'id="{r["id"]}"',
            f'kind="{r["kind"]}"',
            f'mime="{r.get("mime") or ""}"',
            f'scope="{r.get("scope") or ""}"',
            f'size="{_fmt_size(r.get("size"))}"',
            f'updated="{r.get("updated_at") or ""}"',
            f'name="{r["name"]}"',
        ]
        if r.get("brief"):
            # XML-escape minimally — briefs are descriptions, no markup
            brief = r["brief"].replace('"', "'")
            attrs.append(f'brief="{brief}"')
        lines.append(f"  <resource {' '.join(attrs)} />")
    lines.append("</available_resources>")
    lines.append("")
    lines.append("Use the ResourceFetch tool to load any of these on demand:")
    lines.append("  ResourceFetch(resource_id, mode?, args?)")
    lines.append("  - mode for video: summary (default) | transcript | frames")
    lines.append("  - mode for doc: excerpt (default) | full")
    lines.append("  - mode for pdf: excerpt (default) | page (args.page)")
    lines.append("  - mode for image: omit (returns image part)")
    lines.append("  - mode for audio: transcript (default)")
    return "\n".join(lines)
```

- [ ] **Step 4 — Run; expect 2 passed.**

```bash
cd backend
mkdir -p tests/services/ai/prompts
touch tests/services/ai/prompts/__init__.py
uv run pytest tests/services/ai/prompts/test_prompt_composer_resources.py -v
```

- [ ] **Step 5 — Commit.**

```bash
git add backend/app/services/ai/prompts/prompt_composer.py \
        backend/tests/services/ai/prompts/test_prompt_composer_resources.py \
        backend/tests/services/ai/prompts/__init__.py
git commit -m "feat(prompts): render <available_resources> block

Renders one <resource ...> line per ref + a hint paragraph telling
the agent to use ResourceFetch(id, mode?) to load content. Returns
empty string when refs empty so the system message cache key stays
stable for non-mention turns."
```

---

## Task 5 — Backend: `ResourceFetch` tool

**Files:**
- Create: `backend/app/services/ai/tools/__init__.py` (empty)
- Create: `backend/app/services/ai/tools/resource_fetch_tool.py`
- Test: `backend/tests/services/ai/tools/test_resource_fetch_tool.py`

- [ ] **Step 1 — Write the failing test.**

```python
"""Verify ResourceFetch dispatch + available_refs gate + error shapes."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.ai.tools.resource_fetch_tool import resource_fetch


@pytest.mark.asyncio
async def test_rejects_unreferenced_id():
    result = await resource_fetch(
        resource_id="999", mode=None, args=None,
        user_id="u", available_refs={"1", "2"}, request_cache={},
    )
    assert "error" in result
    assert "not referenced" in result["error"]


@pytest.mark.asyncio
async def test_uses_cache_on_second_call():
    cache: dict = {}
    with patch(
        "app.services.ai.tools.resource_fetch_tool._fetch_dispatch",
        return_value={"content": "hello"},
    ) as dispatch_mock:
        await resource_fetch(
            resource_id="1", mode="excerpt", args=None,
            user_id="u", available_refs={"1"}, request_cache=cache,
        )
        await resource_fetch(
            resource_id="1", mode="excerpt", args=None,
            user_id="u", available_refs={"1"}, request_cache=cache,
        )
    assert dispatch_mock.call_count == 1


@pytest.mark.asyncio
async def test_inaccessible_returns_error_not_raise():
    with patch(
        "app.services.ai.tools.resource_fetch_tool._fetch_dispatch",
        side_effect=PermissionError("nope"),
    ):
        result = await resource_fetch(
            resource_id="1", mode=None, args=None,
            user_id="u", available_refs={"1"}, request_cache={},
        )
    assert "error" in result
    assert "not accessible" in result["error"]
```

- [ ] **Step 2 — Run; expect FAIL (module not found).**

```bash
cd backend
mkdir -p app/services/ai/tools tests/services/ai/tools
touch app/services/ai/tools/__init__.py tests/services/ai/tools/__init__.py
uv run pytest tests/services/ai/tools/test_resource_fetch_tool.py -v
```

- [ ] **Step 3 — Implement the tool.**

Create `backend/app/services/ai/tools/resource_fetch_tool.py`:

```python
"""ResourceFetch tool — agents call this to load content for a
``@``-referenced resource.

Mirrors the lazy ``Skill(slug, file)`` pattern: ``<available_resources>``
in the system message gives the agent metadata; the agent calls
``ResourceFetch(id, mode?)`` only when it actually needs the content.

Returns ``{"content": ...}`` on success, ``{"error": "<short>"}`` on any
failure — never raises (the agent must see the error and decide).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from loguru import logger


async def _fetch_dispatch(
    *, resource_id: str, mode: str | None, args: dict | None, user_id: str
) -> dict[str, Any]:
    """Per-kind dispatch. Raises PermissionError if the user can't read
    the resource at fetch time (defends against scope drift between
    ``send_user_message`` and the agent's tool call).

    v1 mode coverage:
      - image:   returns {content: [{type:'image_url', url, mime}]}
      - video:   summary (default) / transcript / frames
      - audio:   transcript (default)
      - doc:     excerpt (default; first 4000 chars) / full (16k token cap)
      - pdf:     excerpt (default) / page (args.page)
    """
    from app.db import engine as db_engine

    rows = await db_engine.fetch_all(
        """
        SELECT r.id::text, r.mime_type AS mime, r.filename AS name,
               r.file_path, r.description AS brief
          FROM public.resources r
          JOIN public.resource_items ri ON ri.resource_id = r.id
         WHERE r.id::text = :rid
           AND r.is_trashed = false AND ri.is_trashed = false
           AND ( (ri.scope_type='personal' AND ri.scope_id::text=:uid)
                 OR (ri.scope_type='team' AND ri.scope_id IN (
                      SELECT team_id FROM public.team_members WHERE user_id=:uid
                 )) )
         LIMIT 1
        """,
        {"rid": resource_id, "uid": user_id},
    )
    if not rows:
        raise PermissionError(f"resource {resource_id} not accessible to {user_id}")

    row = rows[0]
    mime = (row.get("mime") or "").lower()

    # Image
    if mime.startswith("image/"):
        from app.services.media.media_token_service import MediaTokenService
        token = await MediaTokenService.mint_temp_token(resource_id=resource_id, user_id=user_id)
        url = f"/api/v1/media/{resource_id}?token={token}"
        return {"content": [{"type": "image_url", "url": url, "mime": mime}],
                "meta": {"name": row["name"], "kind": "image"}}

    # Video / audio — read from `videos` table joined on parsed_media
    if mime.startswith("video/") or mime.startswith("audio/"):
        m = mode or ("summary" if mime.startswith("video/") else "transcript")
        # NOTE: schema link videos -> parsed_media (id) -> resources (media_id)
        media_rows = await db_engine.fetch_all(
            """
            SELECT v.summary, v.transcript
              FROM public.videos v
              JOIN public.parsed_media pm ON pm.id = v.parsed_media_id
              JOIN public.resources r ON r.media_id = pm.id
             WHERE r.id::text = :rid
             LIMIT 1
            """,
            {"rid": resource_id},
        )
        v = (media_rows or [{}])[0]
        if m == "summary":
            text = v.get("summary")
            if not text:
                return {"error": "summary not available; resource not yet processed"}
            return {"content": text, "meta": {"name": row["name"], "mode": "summary"}}
        if m == "transcript":
            text = v.get("transcript")
            if not text:
                return {"error": "transcript not available; resource not yet processed"}
            return {"content": text, "meta": {"name": row["name"], "mode": "transcript"}}
        if m == "frames":
            # v1: return an explanatory error pointing at the existing
            # keyframe pipeline — implementation deferred to a follow-up.
            return {"error": "mode='frames' not yet implemented in v1; use summary or transcript"}
        return {"error": f"unknown mode {m!r} for video/audio resource"}

    # PDF / doc — read file content from disk via the resource path
    import os
    from pathlib import Path

    fp = row.get("file_path") or ""
    if not fp:
        return {"error": "resource has no file_path on disk"}
    abs_path = Path(os.environ.get("DOWNLOAD_PATH", "/app/downloads")) / fp
    if not abs_path.exists():
        return {"error": f"file missing on disk: {abs_path.name}"}

    if mime == "application/pdf":
        return {"error": "mode='page'/'excerpt' for PDF not yet implemented in v1"}

    # Treat everything else as text doc
    m = mode or "excerpt"
    try:
        raw = abs_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"error": f"failed to read file: {exc!r}"}

    if m == "excerpt":
        excerpt = raw[:4000]
        suffix = ""
        if len(raw) > 4000:
            suffix = f"\n[... truncated, {len(raw) - 4000} bytes remaining; use mode='full' for the entire document]"
        return {"content": excerpt + suffix,
                "meta": {"name": row["name"], "mode": "excerpt", "bytes": len(raw)}}
    if m == "full":
        # 16k token cap ~= 64k char hard cap (conservative)
        cap = 64_000
        if len(raw) > cap:
            return {"content": raw[:cap] + f"\n[... truncated, {len(raw) - cap} bytes remaining]",
                    "meta": {"name": row["name"], "mode": "full", "bytes": len(raw), "truncated": True}}
        return {"content": raw,
                "meta": {"name": row["name"], "mode": "full", "bytes": len(raw)}}
    return {"error": f"unknown mode {m!r} for doc resource"}


async def resource_fetch(
    *,
    resource_id: str,
    mode: Optional[str] = None,
    args: Optional[dict] = None,
    user_id: str,
    available_refs: set[str],
    request_cache: dict,
) -> dict[str, Any]:
    """Public entry point — the runtime registers this as the tool callable.

    ``available_refs`` is the set of resource ids that appeared in
    ``<available_resources>`` for this turn. Calling the tool with an id
    outside that set returns an error — agents must reference what the
    user gave them, not arbitrary ids.
    """
    rid = str(resource_id)
    if rid not in available_refs:
        return {"error": "resource not referenced in this turn"}

    args_hash = hashlib.sha1(
        json.dumps(args or {}, sort_keys=True).encode("utf-8")
    ).hexdigest()[:8]
    cache_key = (rid, mode or "_default_", args_hash)
    if cache_key in request_cache:
        return request_cache[cache_key]

    try:
        result = await _fetch_dispatch(
            resource_id=rid, mode=mode, args=args, user_id=user_id,
        )
    except PermissionError as exc:
        logger.info(f"[resource_fetch] permission denied: {exc!r}")
        return {"error": "resource not accessible"}
    except Exception as exc:
        logger.exception(f"[resource_fetch] dispatch failed: {exc!r}")
        return {"error": f"fetch failed: {exc.__class__.__name__}"}

    request_cache[cache_key] = result
    return result
```

- [ ] **Step 4 — Run; expect 3 passed.**

```bash
cd backend
uv run pytest tests/services/ai/tools/test_resource_fetch_tool.py -v
```

- [ ] **Step 5 — Commit.**

```bash
git add backend/app/services/ai/tools/ \
        backend/tests/services/ai/tools/
git commit -m "feat(ai): ResourceFetch tool

Kind-routed dispatch (image / video / audio / doc / pdf). Re-validates
user accessibility on every call. available_refs gate prevents agents
from fetching arbitrary resource ids. Per-(rid, mode, args) cache
scoped to the request. Errors return {error: '<reason>'} — never raise."
```

---

## Task 6 — Backend: wire resolver + tool into `ai_library_chat_service.send_user_message`

**Files:**
- Modify: `backend/app/services/ai/chat/ai_library_chat_service.py:590-610` (the existing attachment-resolve block)
- Test: extend `backend/tests/services/ai/chat/test_ai_library_chat_service.py` (or create if absent) with an integration-style test

- [ ] **Step 1 — Read the current block.**

```bash
cd backend
sed -n '585,620p' app/services/ai/chat/ai_library_chat_service.py
```

Expected: see existing `if attachments:` block that calls `resolve_attachments` for image/pdf parts.

- [ ] **Step 2 — Write the failing integration-shaped test.**

Create `backend/tests/services/ai/chat/test_send_user_message_resource_refs.py`:

```python
"""resource_ref attachments produce <available_resources> + tool registration."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_resource_refs_become_available_resources_block(monkeypatch):
    """Smoke: send_user_message with one resource_ref attachment ends up
    inserting <available_resources> into the system prompt and registering
    ResourceFetch on the runner."""
    from app.services.ai.chat import ai_library_chat_service as svc

    captured: dict = {}

    async def _fake_resolve_refs(attachments, *, user_id):
        return ([{
            "id": "1", "name": "a.md", "kind": "doc", "mime": "text/markdown",
            "size": 100, "scope": "personal", "updated_at": "2026-05-24T00:00:00Z",
            "brief": None,
        }], [])

    def _fake_render(refs):
        captured["refs"] = refs
        return "<available_resources>\n  <resource id=\"1\" .../>\n</available_resources>"

    with patch.object(svc, "resolve_resource_refs", side_effect=_fake_resolve_refs), \
         patch("app.services.ai.prompts.prompt_composer.render_available_resources",
               side_effect=_fake_render):
        # Call the integration entry point with a minimal stub of dependencies.
        # The exact signature depends on _send_user_message_impl in the file —
        # adapt this fake invocation to the real one when wiring up.
        # For v1 we assert via captured["refs"] that the renderer received the refs.
        pass

    # Wiring sanity: render must have been called with the resolved ref list.
    # (Once the real send_user_message wiring is in place, replace `pass` above
    # with a stubbed call and the assertion below activates.)
    # assert captured.get("refs") and captured["refs"][0]["name"] == "a.md"
```

> **Note:** this test is a placeholder shape — the full integration test depends on `send_user_message`'s exact call surface. Step 4 is where we adjust the test to call the real function with mocks for DB / Redis / model adapter.

- [ ] **Step 3 — Modify `send_user_message` to call the resolver + register the tool.**

In `backend/app/services/ai/chat/ai_library_chat_service.py`, find the existing `if attachments:` block (around line 590-610). Modify it to:

```python
        # G2: resolve attachments → multimodal Attachment[] → vision-aware
        from app.agent_framework.multimodal import build_user_message
        from app.services.ai.chat.chat_attachment_resolver import resolve_attachments
        from app.services.ai.chat.resource_ref_resolver import resolve_resource_refs

        # Split attachments by kind so each goes to its dedicated resolver.
        binary_attachments = [a for a in (attachments or []) if a.get("kind") != "resource_ref"]
        ref_attachments = [a for a in (attachments or []) if a.get("kind") == "resource_ref"]

        # Existing path: binary uploads (image / pdf_page / audio)
        resolved = await resolve_attachments(binary_attachments) if binary_attachments else None

        # New path: @-references — metadata only, no bytes
        resource_refs: list[dict] = []
        ref_warnings: list[str] = []
        if ref_attachments:
            resource_refs, ref_warnings = await resolve_resource_refs(
                ref_attachments, user_id=str(user_id),
            )

        # Build the multimodal user message from binary attachments (unchanged shape)
        supports_vision = await model_supports_vision(composed.model)
        new_user_msg = build_user_message(
            text=text,
            attachments=resolved.attachments if resolved else None,
            supports_vision=supports_vision,
        )

        # Inject resource refs into the system message via prompt_composer
        # (composed.system already contains the persona/skill blocks; we append
        # available_resources if any refs survived permission filtering).
        if resource_refs:
            from app.services.ai.prompts.prompt_composer import render_available_resources
            resources_block = render_available_resources(resource_refs)
            if resources_block:
                composed = composed.with_appended_system(resources_block)

        # Register ResourceFetch on the runner so the agent can call it.
        if resource_refs:
            from app.services.ai.tools.resource_fetch_tool import resource_fetch
            available_ids = {r["id"] for r in resource_refs}
            request_cache: dict = {}

            async def _resource_fetch_bound(*, resource_id, mode=None, args=None):
                return await resource_fetch(
                    resource_id=resource_id, mode=mode, args=args,
                    user_id=str(user_id), available_refs=available_ids,
                    request_cache=request_cache,
                )
            tools.append({
                "name": "ResourceFetch",
                "description": "Load content for a resource the user @-referenced. "
                               "Use sparingly; metadata in <available_resources> is "
                               "often enough.",
                "callable": _resource_fetch_bound,
            })

        # If there are user-visible warnings (skipped refs), surface them
        if ref_warnings:
            new_user_msg = _prepend_user_warnings(new_user_msg, ref_warnings)
```

The supporting helper `_prepend_user_warnings` should be added near the top of the file:

```python
def _prepend_user_warnings(user_msg: dict, warnings: list[str]) -> dict:
    """Prepend warning lines as a separate text part above the original user text.

    The agent sees these alongside the user input so it can acknowledge the
    skipped refs (e.g., "I noticed ghost.md was unavailable...").
    """
    if not warnings:
        return user_msg
    warn_text = "\n".join(f"⚠️ {w}" for w in warnings)
    content = user_msg.get("content")
    if isinstance(content, str):
        user_msg = {**user_msg, "content": f"{warn_text}\n\n{content}"}
    elif isinstance(content, list):
        user_msg = {**user_msg, "content": [
            {"type": "text", "text": warn_text}, *content,
        ]}
    return user_msg
```

> **Note:** The exact `composed.with_appended_system(...)` and `tools.append(...)` signatures depend on the existing data structures in this file. If the file doesn't have `with_appended_system`, add it (one-line helper that returns a new `ComposedPrompt` with the extra string concatenated to `composed.system`). If `tools` isn't a list at that point, look for where the tools/skills inventory is built and append there. Read 50 lines around the existing `if attachments:` block to confirm names.

- [ ] **Step 4 — Refine the test to call the real function.**

After the wiring change, write a test that calls the real `send_user_message` with mocks for the DB layer and the model adapter, asserts:
- `resolve_resource_refs` is invoked with the ref attachments
- `render_available_resources` is invoked with the resolved refs
- The tools registered for the run include one named `"ResourceFetch"`

Use existing tests in `backend/tests/services/ai/chat/` as a template for how to mock the session repo + the model adapter.

- [ ] **Step 5 — Run.**

```bash
cd backend
uv run pytest tests/services/ai/chat/test_send_user_message_resource_refs.py -v
```

- [ ] **Step 6 — Commit.**

```bash
git add backend/app/services/ai/chat/ai_library_chat_service.py \
        backend/tests/services/ai/chat/test_send_user_message_resource_refs.py
git commit -m "feat(chat): wire resource_ref resolver + ResourceFetch tool

send_user_message now splits attachments by kind: existing binary
uploads continue through chat_attachment_resolver; resource_ref
attachments flow through resource_ref_resolver. Resolved refs are
appended to the system message via render_available_resources, and
the ResourceFetch tool is registered with a closure over user_id +
available_refs + a per-request cache. Skipped (deleted) refs become
warnings prepended to the user message."
```

---

## Task 7 — Frontend: add `@tiptap/extension-mention` + ResourceRefAttachment type

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/types.ts`

- [ ] **Step 1 — Install the dependency.**

```bash
cd frontend
npm install @tiptap/extension-mention@^3.22.2 tippy.js@^6.3.7
```

- [ ] **Step 2 — Add types.**

In `frontend/types.ts`, add:

```typescript
/** Reference attachment for chat composer @-mention.
 *  Body shape mirrors the backend `resource_ref` resolver expectation.
 */
export type ResourceRefAttachment = {
  kind: 'resource_ref';
  resource_id: string;        // BIGINT serialized as string (Snowflake)
  name: string;               // snapshot — UI uses this even if resource deleted later
  mime: string;
  scope: { type: 'personal' | 'team'; id: string };
};

/** Search result row from GET /api/v1/resources/search */
export type ResourceSearchResult = {
  id: string;
  name: string;
  kind: 'video' | 'image' | 'doc' | 'audio' | 'pdf';
  mime: string | null;
  size: number | null;
  scope: { type: 'personal' | 'team'; id: string };
  updated_at: string;
  thumbnail_url: string | null;
};

export type ResourceSearchResponse = {
  results: ResourceSearchResult[];
  counts: { all: number; video: number; image: number; doc: number; audio: number; pdf: number };
  next_cursor: string | null;
};
```

- [ ] **Step 3 — Typecheck.**

```bash
cd frontend
npm run typecheck
```

Expected: 0 new errors.

- [ ] **Step 4 — Commit.**

```bash
git add frontend/package.json frontend/package-lock.json frontend/types.ts
git commit -m "deps(frontend): add @tiptap/extension-mention + tippy.js

For the upcoming chat composer @-reference picker."
```

---

## Task 8 — Frontend: `resourceSearchService` + `useResourceSearch` hook

**Files:**
- Create: `frontend/services/resourceSearchService.ts`
- Create: `frontend/hooks/useResourceSearch.ts`
- Test: `frontend/hooks/useResourceSearch.test.ts`

- [ ] **Step 1 — Write the failing test.**

```typescript
// frontend/hooks/useResourceSearch.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useResourceSearch } from './useResourceSearch';
import * as svc from '../services/resourceSearchService';

describe('useResourceSearch', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('debounces 150ms before firing', async () => {
    const spy = vi.spyOn(svc, 'searchResources').mockResolvedValue({
      results: [], counts: { all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 },
      next_cursor: null,
    });
    const { result, rerender } = renderHook(({ q }) => useResourceSearch(q, ''), {
      initialProps: { q: '' },
    });
    rerender({ q: 's' });
    rerender({ q: 'st' });
    rerender({ q: 'sto' });
    // Before debounce window, no call yet
    expect(spy).not.toHaveBeenCalled();
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1), { timeout: 400 });
    expect(spy).toHaveBeenLastCalledWith({ q: 'sto', kinds: '', limit: 20, signal: expect.anything() });
  });

  it('aborts in-flight when query changes', async () => {
    const ctrls: AbortController[] = [];
    const spy = vi.spyOn(svc, 'searchResources').mockImplementation(async ({ signal }) => {
      ctrls.push({ signal } as any);
      return new Promise(() => {}); // never resolves
    });
    const { rerender } = renderHook(({ q }) => useResourceSearch(q, ''), { initialProps: { q: 's' } });
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    rerender({ q: 'st' });
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    expect(ctrls[0].signal.aborted).toBe(true);
  });
});
```

- [ ] **Step 2 — Run; expect FAIL.**

```bash
cd frontend
npm test -- useResourceSearch
```

- [ ] **Step 3 — Implement the service.**

Create `frontend/services/resourceSearchService.ts`:

```typescript
import { getAuthHeaders } from './parserService';
import type { ResourceSearchResponse } from '../types';

const API = import.meta.env.VITE_API_URL || '';

export async function searchResources(params: {
  q: string;
  kinds: string;
  limit?: number;
  signal?: AbortSignal;
}): Promise<ResourceSearchResponse> {
  const headers = await getAuthHeaders();
  const u = new URL(`${API}/api/v1/resources/search`, window.location.origin);
  u.searchParams.set('q', params.q);
  if (params.kinds) u.searchParams.set('kinds', params.kinds);
  if (params.limit) u.searchParams.set('limit', String(params.limit));
  const res = await fetch(u.toString(), { headers, signal: params.signal });
  if (!res.ok) throw new Error(`search failed: ${res.status}`);
  return res.json();
}
```

- [ ] **Step 4 — Implement the hook.**

Create `frontend/hooks/useResourceSearch.ts`:

```typescript
import { useEffect, useRef, useState } from 'react';
import { searchResources } from '../services/resourceSearchService';
import type { ResourceSearchResponse, ResourceSearchResult } from '../types';

const DEBOUNCE_MS = 150;

const EMPTY_RESPONSE: ResourceSearchResponse = {
  results: [],
  counts: { all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 },
  next_cursor: null,
};

export function useResourceSearch(query: string, kinds: string) {
  const [data, setData] = useState<ResourceSearchResponse>(EMPTY_RESPONSE);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const ctrlRef = useRef<AbortController | null>(null);
  const cacheRef = useRef<Map<string, ResourceSearchResponse>>(new Map());

  useEffect(() => {
    const key = `${query}|${kinds}`;
    if (cacheRef.current.has(key)) {
      setData(cacheRef.current.get(key)!);
      return;
    }
    const timer = setTimeout(async () => {
      ctrlRef.current?.abort();
      const ctrl = new AbortController();
      ctrlRef.current = ctrl;
      setLoading(true);
      setError(null);
      try {
        const resp = await searchResources({ q: query, kinds, limit: 20, signal: ctrl.signal });
        if (ctrl.signal.aborted) return;
        cacheRef.current.set(key, resp);
        setData(resp);
      } catch (err) {
        if ((err as { name?: string }).name === 'AbortError') return;
        setError(err as Error);
      } finally {
        if (!ctrl.signal.aborted) setLoading(false);
      }
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query, kinds]);

  return { data, loading, error };
}
```

- [ ] **Step 5 — Run; expect 2 passed.**

```bash
cd frontend
npm test -- useResourceSearch
```

- [ ] **Step 6 — Commit.**

```bash
git add frontend/services/resourceSearchService.ts \
        frontend/hooks/useResourceSearch.ts \
        frontend/hooks/useResourceSearch.test.ts
git commit -m "feat(chat): useResourceSearch hook + REST client

150ms debounce + AbortController on every query change + per-key
in-memory cache. Used by the @-reference picker."
```

---

## Task 9 — Frontend: `ResourcePickerSuggestion` dropdown UI

**Files:**
- Create: `frontend/components/chat/ResourcePickerSuggestion.tsx`
- Test: `frontend/components/chat/ResourcePickerSuggestion.test.tsx`
- Modify: `frontend/public/locales/{en,zh}.json`

- [ ] **Step 1 — Add i18n keys.**

In `frontend/public/locales/en.json`, under `chat`:
```json
"mentionPicker": {
  "all": "All",
  "video": "Video",
  "image": "Image",
  "doc": "Doc",
  "audio": "Audio",
  "pdf": "PDF",
  "noResults": "No resources match \"{{q}}\"",
  "hintKbd": "↑↓ navigate · ↵ insert · Esc cancel"
}
```

Mirror in `zh.json` with Chinese translations: `全部 / 视频 / 图片 / 文档 / 音频 / PDF / 没有匹配 "{{q}}" 的资源 / ↑↓ 选择 · ↵ 插入 · Esc 取消`.

- [ ] **Step 2 — Write the failing test.**

```typescript
// frontend/components/chat/ResourcePickerSuggestion.test.tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ResourcePickerSuggestion } from './ResourcePickerSuggestion';
import type { ResourceSearchResult } from '../../types';

const ROWS: ResourceSearchResult[] = [
  { id: '1', name: 'story.md', kind: 'doc', mime: 'text/markdown', size: 100,
    scope: { type: 'personal', id: 'u' }, updated_at: '2026-05-24T00:00:00Z',
    thumbnail_url: null },
  { id: '2', name: 'pitch.mp4', kind: 'video', mime: 'video/mp4', size: 18000000,
    scope: { type: 'team', id: 't1' }, updated_at: '2026-05-20T00:00:00Z',
    thumbnail_url: null },
];

describe('ResourcePickerSuggestion', () => {
  it('renders rows with name + scope + relative-time', () => {
    const onSelect = vi.fn();
    render(<ResourcePickerSuggestion items={ROWS} query="" loading={false}
                                      counts={{ all: 2, video: 1, image: 0, doc: 1, audio: 0, pdf: 0 }}
                                      activeKind="" onKindChange={() => {}} onSelect={onSelect} />);
    expect(screen.getByText('story.md')).toBeInTheDocument();
    expect(screen.getByText('pitch.mp4')).toBeInTheDocument();
    expect(screen.getByText(/personal/i)).toBeInTheDocument();
  });

  it('calls onSelect when row clicked', () => {
    const onSelect = vi.fn();
    render(<ResourcePickerSuggestion items={ROWS} query="" loading={false}
                                      counts={{ all: 2, video: 1, image: 0, doc: 1, audio: 0, pdf: 0 }}
                                      activeKind="" onKindChange={() => {}} onSelect={onSelect} />);
    fireEvent.click(screen.getByText('story.md').closest('button')!);
    expect(onSelect).toHaveBeenCalledWith(ROWS[0]);
  });

  it('shows empty state when no items', () => {
    const onSelect = vi.fn();
    render(<ResourcePickerSuggestion items={[]} query="xyz" loading={false}
                                      counts={{ all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 }}
                                      activeKind="" onKindChange={() => {}} onSelect={onSelect} />);
    expect(screen.getByText(/no resources match/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3 — Run; expect FAIL (module not found).**

```bash
cd frontend
npm test -- ResourcePickerSuggestion
```

- [ ] **Step 4 — Implement the component.**

Create `frontend/components/chat/ResourcePickerSuggestion.tsx`:

```tsx
import React from 'react';
import { useTranslation } from 'react-i18next';
import { FileText, Image, Video, Music, FileType2 } from 'lucide-react';
import type { ResourceSearchResult, ResourceSearchResponse } from '../../types';

const ICON: Record<ResourceSearchResult['kind'], React.ComponentType<{ size?: number }>> = {
  video: Video, image: Image, audio: Music, doc: FileText, pdf: FileType2,
};

function _formatSize(n: number | null): string {
  if (!n) return '';
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)}KB`;
  return `${Math.round(n / (1024 * 1024))}MB`;
}

function _relative(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const days = Math.floor(diffMs / 86400000);
  if (days === 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 7) return `${days}d ago`;
  if (days < 30) return `${Math.floor(days / 7)}w ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

interface Props {
  items: ResourceSearchResult[];
  query: string;
  loading: boolean;
  counts: ResourceSearchResponse['counts'];
  activeKind: '' | 'video' | 'image' | 'doc' | 'audio' | 'pdf';
  onKindChange: (kind: Props['activeKind']) => void;
  onSelect: (item: ResourceSearchResult) => void;
  activeIndex?: number; // for keyboard nav from parent extension
}

export function ResourcePickerSuggestion({
  items, query, loading, counts, activeKind, onKindChange, onSelect, activeIndex = 0,
}: Props): React.ReactElement {
  const { t } = useTranslation();
  const tabs: { key: Props['activeKind']; label: string; count: number }[] = [
    { key: '',      label: t('chat.mentionPicker.all'),   count: counts.all },
    { key: 'video', label: '🎬 ' + t('chat.mentionPicker.video'), count: counts.video },
    { key: 'image', label: '🖼️ ' + t('chat.mentionPicker.image'), count: counts.image },
    { key: 'doc',   label: '📄 ' + t('chat.mentionPicker.doc'),   count: counts.doc },
  ];

  return (
    <div className="bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl w-[340px] p-1.5"
         data-testid="resource-picker">
      {/* type tabs */}
      <div className="flex gap-1 px-1 pb-1.5 border-b border-zinc-800">
        {tabs.map((tab) => (
          <button key={tab.key || 'all'}
                  onClick={() => onKindChange(tab.key)}
                  className={`text-[11px] px-2 py-0.5 rounded-full ${
                    activeKind === tab.key
                      ? 'bg-indigo-900/60 text-indigo-200'
                      : 'text-zinc-400 hover:text-zinc-200'
                  }`}>
            {tab.label} <span className="opacity-60">{tab.count}</span>
          </button>
        ))}
      </div>

      {/* rows or empty state */}
      {items.length === 0 ? (
        <div className="px-3 py-6 text-center text-[12px] text-zinc-500">
          {loading ? '…' : t('chat.mentionPicker.noResults', { q: query })}
        </div>
      ) : (
        <div className="py-1">
          {items.map((item, idx) => {
            const Icon = ICON[item.kind] ?? FileText;
            const active = idx === activeIndex;
            return (
              <button key={item.id} onClick={() => onSelect(item)}
                      className={`w-full text-left flex items-center gap-2 px-2.5 py-1.5 rounded ${
                        active ? 'bg-indigo-900/60' : 'hover:bg-zinc-800/50'
                      }`}
                      data-testid="resource-picker-row">
                <span className="w-7 h-7 flex items-center justify-center bg-zinc-800 rounded">
                  <Icon size={14} />
                </span>
                <span className="flex-1 min-w-0">
                  <span className="block text-[12px] text-zinc-100 truncate">
                    {item.name}
                    <span className="text-zinc-500"> · {item.scope.type}</span>
                  </span>
                  <span className="block text-[10px] text-zinc-500">
                    {_relative(item.updated_at)} {_formatSize(item.size) && '· ' + _formatSize(item.size)}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      )}

      {/* footer hint */}
      <div className="px-2 py-1 text-[10px] text-zinc-500 border-t border-zinc-800 flex justify-between">
        <span>{items.length > 0 && `${items.length} of ${counts.all}`}</span>
        <span>{t('chat.mentionPicker.hintKbd')}</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 5 — Run; expect 3 passed.**

```bash
cd frontend
npm test -- ResourcePickerSuggestion
```

- [ ] **Step 6 — Commit.**

```bash
git add frontend/components/chat/ResourcePickerSuggestion.tsx \
        frontend/components/chat/ResourcePickerSuggestion.test.tsx \
        frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(chat): ResourcePickerSuggestion dropdown UI

Floating dropdown for @-reference picker — type tabs (All/Video/
Image/Doc) with counts, single-column rows with icon+name+scope+
timestamp, keyboard active-row highlight, empty state, footer kbd
hint. i18n keys added (en+zh)."
```

---

## Task 10 — Frontend: tiptap mention extension + chip node

**Files:**
- Create: `frontend/components/chat/ResourceChipNode.tsx`
- Create: `frontend/components/chat/ChatInputResourceMention.ts`
- Test: `frontend/components/chat/ChatInputResourceMention.test.ts`

- [ ] **Step 1 — Write the failing test.**

```typescript
// frontend/components/chat/ChatInputResourceMention.test.ts
import { describe, it, expect } from 'vitest';
import { Editor } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import { createResourceMentionExtension } from './ChatInputResourceMention';
import type { ResourceSearchResult } from '../../types';

const FAKE_ITEM: ResourceSearchResult = {
  id: '1', name: 'story.md', kind: 'doc', mime: 'text/markdown', size: 100,
  scope: { type: 'personal', id: 'u' }, updated_at: '2026-05-24T00:00:00Z',
  thumbnail_url: null,
};

describe('ChatInputResourceMention', () => {
  it('inserts a resourceRef node when commands.insertResourceRef is called', () => {
    const editor = new Editor({
      extensions: [StarterKit, createResourceMentionExtension({ onPick: () => {} })],
      content: '',
    });
    (editor as any).commands.insertResourceRef(FAKE_ITEM);
    const json = editor.getJSON();
    const found = JSON.stringify(json).includes('"resourceRef"');
    expect(found).toBe(true);
  });

  it('serializes resourceRef chips as markdown-style links on getText', () => {
    const editor = new Editor({
      extensions: [StarterKit, createResourceMentionExtension({ onPick: () => {} })],
      content: '',
    });
    (editor as any).commands.insertResourceRef(FAKE_ITEM);
    // Default getText prints node.text; for atom nodes we expect the chip text
    const text = editor.getText();
    expect(text).toContain('story.md');
  });
});
```

- [ ] **Step 2 — Run; expect FAIL.**

```bash
cd frontend
npm test -- ChatInputResourceMention
```

- [ ] **Step 3 — Create the chip Node.**

Create `frontend/components/chat/ResourceChipNode.tsx`:

```tsx
import React from 'react';
import { NodeViewWrapper, NodeViewProps } from '@tiptap/react';
import { Node, mergeAttributes } from '@tiptap/core';
import { FileText, Image, Video, Music, FileType2 } from 'lucide-react';

const ICON_BY_KIND: Record<string, React.ComponentType<{ size?: number }>> = {
  video: Video, image: Image, audio: Music, doc: FileText, pdf: FileType2,
};

function ResourceChipView({ node, deleteNode }: NodeViewProps): React.ReactElement {
  const { name, kind } = node.attrs;
  const Icon = ICON_BY_KIND[kind] ?? FileText;
  return (
    <NodeViewWrapper as="span" className="inline-block align-baseline">
      <span data-testid="resource-chip"
            className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded
                       bg-indigo-700/60 text-indigo-100 text-[12px] mx-0.5 select-none">
        <Icon size={11} />
        <span className="font-medium">{name}</span>
        <button onClick={deleteNode}
                className="opacity-50 hover:opacity-100 ml-0.5"
                aria-label="remove">×</button>
      </span>
    </NodeViewWrapper>
  );
}

export const ResourceRefNode = Node.create({
  name: 'resourceRef',
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,
  draggable: true,
  addAttributes: () => ({
    resourceId: { default: '' },
    name: { default: '' },
    kind: { default: 'doc' },
    mime: { default: '' },
    scope: { default: { type: 'personal', id: '' } },
  }),
  parseHTML: () => [{ tag: 'span[data-resource-id]' }],
  renderHTML: ({ HTMLAttributes }) =>
    ['span', mergeAttributes(HTMLAttributes, { 'data-resource-id': HTMLAttributes.resourceId,
                                                 class: 'resource-chip' }),
     `📎 ${HTMLAttributes.name}`],
  addNodeView: () => {
    // Lazy — react render wired by tiptap-react via reactNodeViewRenderer
    const { ReactNodeViewRenderer } = require('@tiptap/react');
    return ReactNodeViewRenderer(ResourceChipView);
  },
  renderText: ({ node }) => `@${node.attrs.name}`,
});
```

- [ ] **Step 4 — Create the extension wrapper.**

Create `frontend/components/chat/ChatInputResourceMention.ts`:

```typescript
import { Extension } from '@tiptap/core';
import { ResourceRefNode } from './ResourceChipNode';
import type { ResourceSearchResult } from '../../types';

interface MentionOptions {
  /** Called when user opens the picker — parent renders the popover and
   *  picks the active item. Returns a Promise that resolves with the
   *  chosen result or null if cancelled. */
  onPick: (query: string) => Promise<ResourceSearchResult | null>;
}

export function createResourceMentionExtension(opts: MentionOptions) {
  return Extension.create({
    name: 'resourceMention',
    addExtensions() {
      return [ResourceRefNode];
    },
    addCommands() {
      return {
        insertResourceRef:
          (item: ResourceSearchResult) =>
          ({ commands }: any) => {
            return commands.insertContent({
              type: 'resourceRef',
              attrs: {
                resourceId: item.id,
                name: item.name,
                kind: item.kind,
                mime: item.mime ?? '',
                scope: item.scope,
              },
            });
          },
      } as any;
    },
    // NOTE: full keyboard-triggered '@' suggestion is wired in ChatInput.tsx
    // via tiptap's Suggestion utility; this extension just provides the
    // node + command surface.
  });
}
```

- [ ] **Step 5 — Run; expect 2 passed.**

```bash
cd frontend
npm test -- ChatInputResourceMention
```

- [ ] **Step 6 — Commit.**

```bash
git add frontend/components/chat/ResourceChipNode.tsx \
        frontend/components/chat/ChatInputResourceMention.ts \
        frontend/components/chat/ChatInputResourceMention.test.ts
git commit -m "feat(chat): tiptap @-mention extension + chip node

Atom inline node with React NodeView rendering. Provides
insertResourceRef command + extension wrapper. The '@' trigger
keyboard wiring lives in ChatInput.tsx (next task)."
```

---

## Task 11 — Frontend: rewrite `ChatInput.tsx` from textarea to tiptap

**Files:**
- Modify: `frontend/components/chat/ChatInput.tsx` (full rewrite, keep prop surface)
- Test: `frontend/components/chat/ChatInput.test.tsx` (create)
- Modify (small): `frontend/components/AIChatPanel.tsx` — pass `onMentionInsert` (or similar) to ChatInput so it can hand the picker dropdown to the page

- [ ] **Step 1 — Write the failing test for the new shape.**

```tsx
// frontend/components/chat/ChatInput.test.tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ChatInput } from './ChatInput';

describe('ChatInput (tiptap)', () => {
  it('calls onSend with text + empty attachments when nothing referenced', async () => {
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} />);
    const editor = screen.getByRole('textbox');
    fireEvent.input(editor, { target: { textContent: 'hello' } });
    fireEvent.keyDown(editor, { key: 'Enter' });
    // Note: tiptap async editor — wait one microtask
    await Promise.resolve();
    expect(onSend).toHaveBeenCalled();
    const [text, attachments] = onSend.mock.calls[0];
    expect(text).toContain('hello');
    expect(attachments).toEqual([]);
  });

  it('renders placeholder text', () => {
    render(<ChatInput onSend={() => {}} placeholder="say something" />);
    expect(document.querySelector('[data-placeholder]')?.getAttribute('data-placeholder'))
      .toBe('say something');
  });
});
```

> **Note:** the existing `ChatInput` had `onSend: (message: string) => void` — the new shape passes a second `attachments` argument. Update `AIChatPanel.tsx` to accept it. This is a soft API change: keep the textarea-style first arg as the message text (rendered for backward consumers).

- [ ] **Step 2 — Run; expect FAIL.**

```bash
cd frontend
npm test -- ChatInput.test
```

- [ ] **Step 3 — Rewrite ChatInput.**

Replace `frontend/components/chat/ChatInput.tsx` with:

```tsx
import React, { useCallback, useImperativeHandle, forwardRef } from 'react';
import { EditorContent, useEditor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Placeholder from '@tiptap/extension-placeholder';
import { useTranslation } from 'react-i18next';
import { Send, Paperclip } from 'lucide-react';
import { createResourceMentionExtension } from './ChatInputResourceMention';
import type { ResourceRefAttachment } from '../../types';

export interface ChatInputProps {
  /** Called with the plain text + the array of resource_ref attachments
   *  parsed from the editor. Existing paste/drag attachments still flow
   *  through the parent's attachment-state hook. */
  onSend: (message: string, attachments: ResourceRefAttachment[]) => void;
  onAttach?: () => void;
  /** Optional callback to render the @ picker — receives the current
   *  query and a resolve callback. Wire-up in AIChatPanel. */
  onMentionRequest?: (query: string) => Promise<{ id: string; name: string; kind: ResourceRefAttachment['kind']; mime: string; scope: ResourceRefAttachment['scope'] } | null>;
  disabled?: boolean;
  placeholder?: string;
  onPaste?: React.ClipboardEventHandler;
}

export const ChatInput = forwardRef<HTMLDivElement, ChatInputProps>(function ChatInput(
  { onSend, onAttach, onMentionRequest, disabled = false, placeholder, onPaste },
  ref,
) {
  const { t } = useTranslation();
  const resolvedPlaceholder = placeholder ?? t('chat.typeMessage');

  const mentionExt = React.useMemo(
    () => createResourceMentionExtension({ onPick: onMentionRequest ?? (async () => null) }),
    [onMentionRequest],
  );

  const editor = useEditor({
    extensions: [
      StarterKit.configure({ history: true }),
      Placeholder.configure({ placeholder: resolvedPlaceholder }),
      mentionExt,
    ],
    editorProps: {
      handlePaste: (_view, event) => {
        if (onPaste) onPaste(event as unknown as React.ClipboardEvent);
        return false;
      },
      attributes: { role: 'textbox', 'aria-label': resolvedPlaceholder },
    },
  });

  const handleSend = useCallback(() => {
    if (!editor || disabled) return;
    const text = editor.getText().trim();
    if (!text) return;
    // Walk the doc to collect resourceRef nodes → attachments
    const attachments: ResourceRefAttachment[] = [];
    editor.state.doc.descendants((node) => {
      if (node.type.name === 'resourceRef') {
        attachments.push({
          kind: 'resource_ref',
          resource_id: node.attrs.resourceId,
          name: node.attrs.name,
          mime: node.attrs.mime,
          scope: node.attrs.scope,
        });
      }
      return true;
    });
    onSend(text, attachments);
    editor.commands.clearContent();
  }, [editor, onSend, disabled]);

  // KeyDown Enter handler (Shift+Enter = newline)
  React.useEffect(() => {
    if (!editor) return;
    const el = editor.view.dom;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    };
    el.addEventListener('keydown', handler);
    return () => el.removeEventListener('keydown', handler);
  }, [editor, handleSend]);

  useImperativeHandle(ref, () => editor?.view.dom as HTMLDivElement, [editor]);

  return (
    <div className="flex items-end gap-2 p-2 border-t border-zinc-800 bg-zinc-900">
      {onAttach && (
        <button onClick={onAttach} disabled={disabled}
                className="text-zinc-400 hover:text-zinc-200 disabled:opacity-50 p-2">
          <Paperclip size={18} />
        </button>
      )}
      <EditorContent editor={editor}
                     className="flex-1 max-h-[120px] overflow-y-auto text-sm text-zinc-100" />
      <button onClick={handleSend} disabled={disabled}
              className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white rounded-md p-2">
        <Send size={18} />
      </button>
    </div>
  );
});
```

- [ ] **Step 4 — Update `AIChatPanel.tsx` to pass attachments through.**

Find where `ChatInput` is rendered in `frontend/components/AIChatPanel.tsx`. Change the `onSend` prop to accept the new (text, attachments) signature and merge with the existing paste/drag attachment state:

```tsx
<ChatInput
  onSend={(text, refAttachments) => {
    const allAttachments = [...pasteDragAttachments, ...refAttachments];
    sendMessage(text, allAttachments);
  }}
  onMentionRequest={(query) => openMentionPicker(query)}
  // ... existing props
/>
```

The `openMentionPicker` should be implemented in AIChatPanel: render `ResourcePickerSuggestion` in a Tippy.js popover anchored near the editor caret, use the `useResourceSearch` hook for results, and resolve when the user clicks a row. A minimal-but-working version is acceptable for v1; the popover positioning can be refined in a polish task.

- [ ] **Step 5 — Run; expect tests pass.**

```bash
cd frontend
npm test -- ChatInput.test
npm run typecheck
npm run build
```

- [ ] **Step 6 — Commit.**

```bash
git add frontend/components/chat/ChatInput.tsx \
        frontend/components/chat/ChatInput.test.tsx \
        frontend/components/AIChatPanel.tsx
git commit -m "feat(chat): rewrite ChatInput from textarea to tiptap

EditorContent + StarterKit + Placeholder + resourceMention extension.
onSend signature gains a second 'attachments' argument carrying any
resource_ref chips parsed from the editor doc. AIChatPanel updated
to merge ref attachments with the existing paste/drag attachment
state before calling sendMessage."
```

---

## Task 12 — Manual smoke + open PR

- [ ] **Step 1 — Manual end-to-end smoke on dev.**

```bash
# Backend dev
cd backend
uv run uvicorn app.main:app --reload --port 8081 &

# Frontend dev (separate terminal)
cd frontend
npm run dev
```

Open the dev URL, click into an AI chat, type `@`, verify the picker pops up, click a resource, verify chip inserts, send the message, verify:
- network tab: payload contains `attachments: [{kind: 'resource_ref', resource_id, ...}]`
- backend log: see `[resource_ref_resolver]` activity
- agent reply: agent has the metadata (may or may not call ResourceFetch depending on the task)

- [ ] **Step 2 — Push + open PR.**

```bash
git push -u origin feature/chat-at-reference-resource
cat > /tmp/pr-body.md <<'EOF'
## Sub-plan 4 — Chat `@`-reference resource

Final piece of the media-context epic. Lets users type `@` in the chat composer to attach existing library resources as agent context — mirroring the lazy `Skill(slug, file)` pattern (metadata up front, on-demand fetch).

See `docs/superpowers/specs/2026-05-27-chat-at-reference-resource-design.md` for full design + decisions.

## What's in the box

**Frontend (tiptap migration):**
- `ChatInput.tsx` rewritten from textarea to tiptap EditorContent
- `@tiptap/extension-mention` + tippy.js added
- `ResourcePickerSuggestion` — dropdown UI with type tabs + scope labels + keyboard hints
- `ResourceChipNode` — inline atom node with React NodeView
- `useResourceSearch` hook (150ms debounce, AbortController, per-query cache)

**Backend:**
- `GET /api/v1/resources/search` — picker endpoint (q + kinds + limit, scope-filtered)
- `resource_ref_resolver` — permission-checks + dedupes + emits warnings for inaccessible refs
- `ResourceFetch` tool — kind-routed dispatch (image / video / audio / doc / pdf); per-request cache; re-validates user on every call
- `prompt_composer.render_available_resources` — renders `<available_resources>` block when refs present

## Test plan

- [x] Unit tests: 8 new test files; everything green locally (`uv run pytest` + `npm test`)
- [ ] Manual smoke on dev: type `@`, pick, send, verify network payload + backend log + agent reply
- [ ] Post-deploy soak: 24h to verify no token-cost regression in `agent_runs.cost_cents`

## Out of scope (per spec)

- IssueReplyBox `@`-ref (deferred to follow-up)
- Smart Folder / Library targets
- PDF page extraction (returns `not_implemented` error)
- Video frames extraction (returns `not_implemented` error; use `mode='summary'` or `mode='transcript'`)
EOF
gh pr create --base master --title "feat(chat): @-reference resource picker (media-context sub-plan 4)" --body-file /tmp/pr-body.md
```

---

## Self-review (post-write)

**Spec coverage:**
- ✅ Spec § Architecture → Tasks 6 + 11 wire it up
- ✅ Spec § Frontend file map → Tasks 7-11
- ✅ Spec § Backend file map → Tasks 1-6
- ✅ Spec § ChatInput tiptap rewrite → Task 11
- ✅ Spec § ResourcePickerSuggestion → Task 9
- ✅ Spec § ResourceChipNode → Task 10
- ✅ Spec § GET /resources/search → Task 2
- ✅ Spec § resource_ref_resolver → Task 3
- ✅ Spec § prompt_composer changes → Task 4
- ✅ Spec § ResourceFetch tool → Task 5
- ✅ Spec § wiring → Task 6
- ✅ Spec § permissions → enforced in Tasks 3, 5, 6
- ✅ Spec § testing strategy → covered in each task's TDD steps

**Placeholder scan:** Task 6 step 2 has a literal `pass` in the placeholder test — flagged in plan as "adjust to real signature once wired." This is intentional staging; Task 6 step 4 explicitly tells the engineer to refine it. Acceptable.

**Type consistency:**
- `ResourceRefAttachment.scope = {type, id}` consistent across Tasks 7 (frontend), 3 (resolver), 6 (wiring).
- `<available_resources>` XML format identical in Tasks 4 (renderer) + 5 (tool's `available_refs` set name).
- `kind` enum identical: `video / image / doc / audio / pdf` across Tasks 1, 2, 3, 5, 7, 9.
- `ResourceFetch(resource_id, mode?, args?)` signature identical in Task 5 (impl) + Task 6 (registration) + Task 4 (system message hint).

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-27-chat-at-reference-resource.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task with two-stage review, fast iteration. Tasks 1-5 (backend) are tightly scoped to a single file each — ideal for subagent execution. Tasks 8-11 (frontend) involve more cross-file integration but the spec is detailed.

2. **Inline Execution** — execute tasks in this session using executing-plans, batch checkpoints between backend↔frontend boundary.

Which approach?
