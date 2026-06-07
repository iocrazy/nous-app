# Media Asset Foundation (sub-plan 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** Chat/issue uploads land as **temp resources on the shared NAS library** (`/app/downloads/teams|personal/.../temp/`) instead of gateway-local `/tmp`, so the worker (which runs issue turns) can read them — and they become promotable resources later.

**Architecture:** Upgrade the existing `/api/v1/ai-library/chat-attachments/upload` (`ai_library_router.py:2189`) to **route through the existing `ResourcesService` upload** into a reserved `temp` folder, reusing the magic-byte/50MB/image-video-pdf validation. The turn's attachment resolver reads the resource's `file_path` under `settings.DOWNLOAD_PATH` (the shared `/app/downloads` volume both gateway + worker mount) instead of gateway-local `/tmp`. No new storage layer — resources already persist to `DOWNLOAD_PATH/{file_path}` and both containers see it.

**Tech Stack:** FastAPI multipart, `ResourcesService` (`app/services/library/resources_service.py`), `ResourcesRepository` (`scope_type` ∈ `personal|team`, `scope_id`, `folder_id`), `settings.DOWNLOAD_PATH`, `app/agent_framework/multimodal.py::Attachment` (`kind/url/data_url/mime/alt_text`).

**Spec:** `docs/superpowers/specs/2026-05-25-agent-media-context-and-assets-design.md` (eng-reviewed). This is sub-plan 1 of 5.

---

## Why (eng-review findings, verified)
- `/chat-attachments/upload` writes `/tmp/mediahub_chat_attachments` (gateway-local). Compose mounts the shared library `${DOWNLOAD_HOST_PATH:-/volume2/sources/MediaHub.library}:/app/downloads` on **both** `mediahub-app-backend` (gateway) and `mediahub-app-worker`, but **/tmp is NOT shared**. Regular chat turns run on the gateway (can read /tmp); **issue turns run on the worker → cannot read the gateway's /tmp**. So attachments in issue replies are currently unreadable. Storing as a resource on `/app/downloads` fixes this AND makes them promotable.
- Reuse: `ResourcesService` already does upload → disk-write under `DOWNLOAD_PATH` + resource-row create. `scope_type` ∈ `personal|team` (`resources_crud_router.py:53`). Resources read at `Path(settings.DOWNLOAD_PATH)/file_path` (`resources_crud_router.py:449`).

## Reuse these existing utilities (don't re-roll)
- `app/core/file_utils.py` — `sanitize_filename`, `stream_upload_to_disk(upload, target, max_size) -> (size, sha256)`, `sniff_mime(path)`. Use these for the validated streaming write instead of the hand-rolled loop in `upload_chat_attachment`.
- `app/core/scope_guards.py` — `verify_scope_access(auth, scope_type, scope_id)` (403 if the caller doesn't own the personal/team scope). **The temp upload MUST call this** before writing, so a user can't drop a temp resource into someone else's scope.
- `app/services/library/resources_service.py` — the upload→disk+resource-create path (Task 1 reads its signature).

## Decisions (locked)
- Temp = a reserved **`temp` folder** under the scope's resources (NOT an `is_temp` column).
- Scope: session has `team_id` → `("team", team_id)`; else `("personal", user_id)`.
- Keep the existing validation (magic-byte / 50MB / image·video·pdf).
- Reuse media-token (#276/#275) for any URL access; the worker reads the file directly off the shared `DOWNLOAD_PATH`.

## File structure
- **Create** `backend/app/services/library/chat_upload.py` — `resolve_chat_scope` + `save_chat_temp_upload` (validate → ResourcesService into the temp folder → return a resource dict).
- **Modify** `backend/app/api/ai_library_router.py` — `upload_chat_attachment` calls the new helper; return shape gains `resource_id` + `file_path` (keep `url` for back-compat, now a worker-readable path).
- **Modify** the chat-attachment resolver (find it: where `run_session_turn(attachments=…)` payloads become `Attachment`) — build `Attachment` from the resource `file_path` under `DOWNLOAD_PATH`.
- Tests: `backend/tests/test_chat_upload_temp.py`.

---

## Task 1: Scope resolution + temp-folder helper

**Files:**
- Create: `backend/app/services/library/chat_upload.py`
- Test: `backend/tests/test_chat_upload_temp.py`

- [ ] **Step 1: Read the reuse surface first**
Read `app/services/library/resources_service.py` — find the **upload method** that takes a file + `scope_type`/`scope_id`/`folder_id` and writes to `DOWNLOAD_PATH` + creates a resource row. Note its exact signature + return shape. Read `app/repositories/resources_repository.py` for how folders are created/looked up (so the `temp` folder can be get-or-created per scope). Read how `ai_sessions.team_id` is fetched (the messages router / issue_session already read sessions).

- [ ] **Step 2: Write the failing test**
```python
# backend/tests/test_chat_upload_temp.py
from unittest.mock import AsyncMock
import pytest
from app.services.library import chat_upload as m


@pytest.mark.asyncio
async def test_resolve_scope_team_when_session_has_team(monkeypatch):
    monkeypatch.setattr(m, "_get_session_team_id", AsyncMock(return_value=4242))
    assert await m.resolve_chat_scope(session_id="s1", user_id="u1") == ("team", "4242")


@pytest.mark.asyncio
async def test_resolve_scope_personal_when_no_team(monkeypatch):
    monkeypatch.setattr(m, "_get_session_team_id", AsyncMock(return_value=None))
    assert await m.resolve_chat_scope(session_id=None, user_id="u1") == ("personal", "u1")
```

- [ ] **Step 3: Run → fail** — `cd backend && uv run pytest tests/test_chat_upload_temp.py -v`.

- [ ] **Step 4: Implement `resolve_chat_scope`**
```python
"""Chat/issue temp uploads → temp resources on the shared library."""
from __future__ import annotations

from typing import Optional, Tuple

from loguru import logger

TEMP_FOLDER_NAME = "temp"


async def _get_session_team_id(session_id: str) -> Optional[int]:
    from app.db import engine as db_engine

    row = await db_engine.fetch_one(
        "SELECT team_id FROM public.ai_sessions WHERE id = :id", {"id": session_id}
    )
    return row.get("team_id") if row else None


async def resolve_chat_scope(
    *, session_id: Optional[str], user_id: str
) -> Tuple[str, str]:
    """('team', team_id) when the session is team-scoped, else ('personal', user_id)."""
    if session_id:
        team_id = await _get_session_team_id(session_id)
        if team_id:
            return ("team", str(team_id))
    return ("personal", str(user_id))
```

- [ ] **Step 5: Run → pass + commit**
```bash
cd backend && uv run pytest tests/test_chat_upload_temp.py -v
uv run black app/services/library/chat_upload.py tests/test_chat_upload_temp.py && uv run isort app/services/library/chat_upload.py && uv run flake8 app/services/library/chat_upload.py
git add -A && git commit -m "feat(resources): chat-upload scope resolver (team vs personal)"
```

---

## Task 2: `save_chat_temp_upload` — persist a validated file as a temp resource

**Files:**
- Modify: `backend/app/services/library/chat_upload.py`
- Test: `backend/tests/test_chat_upload_temp.py`

- [ ] **Step 1: Write the failing test** (mock `ResourcesService` so no disk/db needed)
```python
@pytest.mark.asyncio
async def test_save_temp_upload_routes_to_resources_temp_folder(monkeypatch):
    monkeypatch.setattr(m, "_get_session_team_id", AsyncMock(return_value=None))
    fake_svc = AsyncMock()
    fake_svc.upload = AsyncMock(return_value={"id": "res-1", "file_path": "personal/u1/temp/x.png"})
    monkeypatch.setattr(m, "_resources_service", lambda: fake_svc)
    monkeypatch.setattr(m, "_ensure_temp_folder", AsyncMock(return_value="folder-temp"))

    out = await m.save_chat_temp_upload(
        user_id="u1", session_id=None,
        file_bytes=b"\x89PNG...", filename="x.png", mime="image/png",
    )
    assert out["resource_id"] == "res-1"
    assert out["file_path"] == "personal/u1/temp/x.png"
    # routed into the temp folder of the personal scope
    kwargs = fake_svc.upload.call_args.kwargs
    assert kwargs["scope_type"] == "personal" and kwargs["scope_id"] == "u1"
    assert kwargs["folder_id"] == "folder-temp"
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** — using the ResourcesService upload signature you read in Task 1 Step 1 (adapt the kwargs to its real param names):
```python
def _resources_service():
    from app.services.library.resources_service import ResourcesService

    return ResourcesService()


async def _ensure_temp_folder(scope_type: str, scope_id: str) -> str:
    """Get-or-create the reserved `temp` folder for a scope; return its id.
    Mirror ResourcesRepository's folder create/lookup (read it in Task 1)."""
    from app.repositories.resources_repository import ResourcesRepository

    repo = ResourcesRepository()
    # Use the repo's existing get-or-create-folder path (by name within scope).
    return await repo.get_or_create_folder(scope_type, scope_id, TEMP_FOLDER_NAME)


async def save_chat_temp_upload(
    *, user_id: str, session_id: Optional[str],
    file_bytes: bytes, filename: str, mime: str,
) -> dict:
    """Persist an already-validated upload as a temp resource on the shared
    library and return {resource_id, file_path, kind, mime, filename, size_bytes}."""
    scope_type, scope_id = await resolve_chat_scope(session_id=session_id, user_id=user_id)
    folder_id = await _ensure_temp_folder(scope_type, scope_id)
    svc = _resources_service()
    res = await svc.upload(  # adapt to ResourcesService.upload's real signature
        scope_type=scope_type, scope_id=scope_id, folder_id=folder_id,
        file_bytes=file_bytes, filename=filename, mime=mime,
    )
    return {
        "resource_id": str(res["id"]),
        "file_path": res["file_path"],
        "kind": _kind_for_mime(mime, filename),
        "mime": mime,
        "filename": filename,
        "size_bytes": len(file_bytes),
    }
```
Add `_kind_for_mime` (image/video/pdf from mime/ext — copy the mapping from `upload_chat_attachment`). **If `ResourcesService.upload` doesn't accept raw bytes** (e.g. it wants an UploadFile/stream), adapt: either add a small bytes-accepting path to the service or wrap the bytes in a SpooledTemporaryFile — pick the minimal change and note it in the commit.

- [ ] **Step 4: Run → pass + commit**
```bash
cd backend && uv run pytest tests/test_chat_upload_temp.py -v
uv run black app/services/library/chat_upload.py && uv run isort app/services/library/chat_upload.py && uv run flake8 app/services/library/chat_upload.py
git add -A && git commit -m "feat(resources): save_chat_temp_upload → temp resource on shared library"
```

---

## Task 3: Route `/chat-attachments/upload` through the temp-resource helper

**Files:**
- Modify: `backend/app/api/ai_library_router.py` (`upload_chat_attachment`, ~2189-2294)
- Test: `backend/tests/test_chat_upload_temp.py`

- [ ] **Step 1: Write the failing test** — assert the endpoint still validates (magic-byte/size/ext) AND now returns `resource_id` + `file_path`, and calls `save_chat_temp_upload` (mock it). Mirror the existing endpoint test style; mock `save_chat_temp_upload` + drive a small multipart body.

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** — keep the existing validation block (ext allow-list, content-length pre-check, streaming read with magic-byte check + size cap) but **buffer the validated bytes** instead of writing `/tmp`; then:
```python
    # (after the validated bytes are in `buf`/known)
    result = await save_chat_temp_upload(
        user_id=str(user_id), session_id=session_id,  # session_id from a new optional query/form field
        file_bytes=buf, filename=filename, mime=mime_guess or "",
    )
    return {
        "kind": result["kind"],
        "resource_id": result["resource_id"],
        "file_path": result["file_path"],          # worker-readable, under DOWNLOAD_PATH
        "url": result["file_path"],                # back-compat key
        "size_bytes": result["size_bytes"],
        "mime": result["mime"],
        "filename": result["filename"],
    }
```
Add an optional `session_id` to the upload (query or form) so scope can be resolved; default None → personal scope. Remove the `/tmp` write + `_reap_old_attachments` for the new path (leave the old `/tmp` helpers only if still referenced elsewhere — grep; otherwise delete). Import `save_chat_temp_upload` from `app.services.library.chat_upload`.

- [ ] **Step 4: Run → pass + commit**
```bash
cd backend && uv run pytest tests/test_chat_upload_temp.py -k upload -v
uv run black app/api/ai_library_router.py && uv run isort app/api/ai_library_router.py && uv run flake8 app/api/ai_library_router.py
git add -A && git commit -m "feat(chat): chat-attachments upload writes a temp resource (shared, worker-readable)"
```

---

## Task 4: Turn reads the attachment from shared storage (not gateway /tmp)

**Files:**
- Modify: the chat-attachment resolver (FIND it: grep `attachments` + `Attachment(` in `app/services/ai/chat/` and `app/agent_framework/`)
- Test: extend `backend/tests/test_chat_upload_temp.py`

- [ ] **Step 1: Read** the resolver that turns the upload payload (`{kind, url|file_path, mime}`) into `multimodal.Attachment` for `run_session_turn(attachments=…)`. Identify where it reads the file (the old code read the `/tmp` path).

- [ ] **Step 2: Write the failing test** — given an attachment payload with `file_path="personal/u1/temp/x.png"`, the resolver builds an `Attachment` whose bytes come from `Path(settings.DOWNLOAD_PATH)/file_path` (mock the file read), not from `/tmp`.

- [ ] **Step 3: Implement** — resolve the attachment file via `Path(settings.DOWNLOAD_PATH) / payload["file_path"]` (works on both gateway + worker since it's the shared mount). For images build `Attachment(kind="image", data_url=<base64 from the file bytes>, mime=…)` (or a media-token URL — but data_url is simplest + worker-local read of the shared file). Keep the existing kind/vision handling.

- [ ] **Step 4: Run → pass + commit**
```bash
cd backend && uv run pytest tests/test_chat_upload_temp.py -v
uv run black <resolver file> && uv run isort <resolver file> && uv run flake8 <resolver file>
git add -A && git commit -m "feat(chat): resolve attachments from shared library path (gateway+worker)"
```

---

## Task 5: Final verification
- [ ] `cd backend && uv run pytest tests/ -q` → all pass.
- [ ] Chain import: `uv run python -c "import app.services.library.chat_upload, app.api.ai_library_router; print('OK')"`.
- [ ] Grep: new uploads no longer depend on `/tmp/mediahub_chat_attachments` (the resolver reads `DOWNLOAD_PATH`).
- [ ] **Deploy note (PR body):** no migration (reuses resources + the shared `/app/downloads` volume). After deploy, an image attached in an **issue reply** (worker turn) is now readable (was broken: gateway /tmp). Watch the worker comes up (auto-handled by #350).

## Self-review checklist
- Spec coverage: uploads → temp resources on shared storage ✓ (Tasks 1-3); worker-readable ✓ (Task 4). Sub-plans 2-5 (lifecycle/UX/@-ref/AI-gen) are separate.
- Reuse: ResourcesService upload + ResourcesRepository folders + multimodal.Attachment + media-token — no new storage layer.
- Open for the implementer to confirm by reading (Task 1 Step 1): `ResourcesService.upload` exact signature (bytes vs UploadFile), `ResourcesRepository` folder get-or-create method name, and the resolver location. Real code is given for everything verified; these 3 are "read the existing method + call it" (not fabricated).
- Known limitation: large videos as base64 data_url is heavy — for video, prefer a media-token URL (revisit in sub-plan 4 with the @-ref cost guardrail).
