"""Canvas REST endpoints (Phase 1 of canvas + AI upgrade).

Surface area:
  GET    /api/v1/canvases/{canvas_id}                    — load
  PUT    /api/v1/canvases/{canvas_id}                    — save w/ optimistic lock
  DELETE /api/v1/canvases/{canvas_id}                    — remove
  GET    /api/v1/projects/{project_id}/canvases          — list within project
  POST   /api/v1/projects/{project_id}/canvases          — create within project

Project-membership gating piggy-backs on the existing
``verify_project_*_access`` guards from ``app.core.scope_guards`` for the
project-scoped routes. The canvas-scoped routes resolve the parent
project via the repo and then call the same guard.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Path
from loguru import logger

from app.core.deps import AuthDep
from app.core.scope_guards import (
    ProjectAccess,
    resolve_project_read_access,
    verify_project_read_access,
    verify_project_write_access,
)
from app.repositories.asset_relations_repository import AssetRelationsRepository
from app.repositories.assets_repository import AssetsRepository
from app.repositories.canvas_repository import CanvasRepository
from app.repositories.episode_repository import get_episode_repository
from app.schemas.canvas import (
    CanvasConflictResponse,
    CanvasCreate,
    CanvasGenerationRequest,
    CanvasResponse,
    CanvasTimelineRequest,
    CanvasUpdate,
    CanvasZipRequest,
)
from app.schemas.canvas_run import (
    CanvasPromptRunRequest,
    CanvasPromptRunResponse,
)
from app.services.canvas import CanvasConflict, CanvasService
from app.services.canvas.canvas_run_service import CanvasRunService
from app.services.infra.unified_task_manager import get_task_manager
from app.services.library.resources_service import _resolve_personal_team_id
from app.services.modules.gate import require_module

router = APIRouter(dependencies=[Depends(require_module("projects"))])


def _to_response(row: dict, *, can_edit: bool | None = None) -> dict:
    """Normalise raw DB row → CanvasResponse-shaped dict.

    Supabase returns BIGINT IDs as JSON numbers; we stringify so the
    frontend doesn't lose snowflake precision (the bigIntSafeFetch
    wrapper is for raw client fetches — going through FastAPI we hand
    the stringification ourselves).

    ``can_edit`` (when passed) rides along on the LOAD responses so the
    client knows its write rights before it attempts a write. Omitted
    everywhere else — a list/summary payload has no single canvas whose
    permission it would describe.
    """
    if not row:
        return row
    out = dict(row)
    if "id" in out and out["id"] is not None:
        out["id"] = str(out["id"])
    if "project_id" in out and out["project_id"] is not None:
        out["project_id"] = str(out["project_id"])
    if "episode_id" in out and out["episode_id"] is not None:
        out["episode_id"] = str(out["episode_id"])
    if "asset_id" in out and out["asset_id"] is not None:
        out["asset_id"] = str(out["asset_id"])
    if "created_by" in out and out["created_by"] is not None:
        out["created_by"] = str(out["created_by"])
    if can_edit is not None:
        out["can_edit"] = bool(can_edit)
    return out


async def _gate_canvas_write(canvas_id: str, auth: AuthDep) -> str:
    """Resolve canvas → project, then run the write guard. Returns project_id."""
    svc = CanvasService()
    project_id = await svc.get_project_id(canvas_id)
    if project_id is None:
        raise HTTPException(status_code=404, detail="canvas not found")
    await verify_project_write_access(project_id=project_id, auth=auth)
    return project_id


async def _gate_canvas_read_access(
    canvas_id: str, auth: AuthDep
) -> tuple[str, ProjectAccess]:
    """Resolve canvas → project, run the read guard, return BOTH the
    project_id and the access verdict the guard already resolved.

    Read = owner, or team member, or any project_members row (any role) —
    see ``verify_project_read_access``. Viewer-role project members can
    reach this but fail ``_gate_canvas_write``, which requires
    manager/editor; ``access.can_write`` is exactly that distinction, which
    is why the load responses can report it as ``can_edit`` without asking
    a second time.
    """
    svc = CanvasService()
    project_id = await svc.get_project_id(canvas_id)
    if project_id is None:
        raise HTTPException(status_code=404, detail="canvas not found")
    access = await resolve_project_read_access(project_id=project_id, auth=auth)
    return project_id, access


async def _gate_canvas_read(canvas_id: str, auth: AuthDep) -> str:
    """``_gate_canvas_read_access`` for callers that only need the gate."""
    project_id, _access = await _gate_canvas_read_access(canvas_id, auth)
    return project_id


# ============================================================
# Smart-canvas generation (G4-B1) — static paths MUST register before the
# dynamic /canvases/{canvas_id} below or they get captured as a canvas id.
# ============================================================


@router.post("/canvases/assets/zip")
async def download_canvas_assets_zip(body: CanvasZipRequest, auth: AuthDep):
    """Bundle several generated-media results into one archive (P2-7).

    Only whitelisted ``/api/v1/generated-media/{id}/(file|stream|cover)``
    URLs are accepted — no arbitrary-URL fetch (SSRF surface). Every id is
    scope-checked against the caller's personal team before its bytes are
    read; ids the caller can't see are silently skipped (partial success),
    and an all-empty request 404s.
    """
    import io
    import zipfile

    from fastapi.responses import Response

    from app.repositories.generated_media_repository import GeneratedMediaRepository
    from app.services.canvas.zip_assets import (
        dedupe_zip_name,
        parse_generated_media_id,
    )
    from app.services.library.resources_service import _resolve_personal_team_id

    scope_id = int(await _resolve_personal_team_id(str(auth.user_id)))
    repo = GeneratedMediaRepository()

    buffer = io.BytesIO()
    taken: set[str] = set()
    packed = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in body.items:
            gen_id = parse_generated_media_id(item.url)
            if gen_id is None:
                # Reject the whole request on a non-whitelisted URL — a
                # malformed client (or an attempt at remote fetch) must not
                # silently succeed with a partial archive.
                raise HTTPException(
                    status_code=400,
                    detail="only generated-media serve URLs are allowed",
                )
            row = await repo.get(gen_id, scope_id)
            if not row:
                continue  # not visible to this caller — skip, don't leak
            data = await _read_media_bytes(row)
            if data is None:
                continue
            fallback = f"generated-{gen_id}"
            name = dedupe_zip_name(item.name or fallback, taken)
            archive.writestr(name, data)
            packed += 1

    if packed == 0:
        raise HTTPException(status_code=404, detail="no downloadable assets")

    filename = body.filename or "canvas-assets.zip"
    if not filename.lower().endswith(".zip"):
        filename = f"{filename}.zip"
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _read_media_bytes(row: dict):
    """Read a generated_media row's bytes from whichever backend holds it,
    mirroring generated_media_router._serve_media_row minus the response."""
    import os

    from app.core.config import settings
    from app.services.library.media_storage import ObjectStore, resolve_media_source

    loc = resolve_media_source(row["file_path"])
    if loc.is_object_store:
        try:
            return await ObjectStore(loc.bucket).get_bytes(loc.key)
        except Exception:
            return None
    base = os.path.realpath(settings.DOWNLOAD_PATH)
    real = os.path.realpath(os.path.join(settings.DOWNLOAD_PATH, loc.rel_path))
    if not (real == base or real.startswith(base + os.sep)):
        return None
    if not os.path.isfile(real):
        return None
    with open(real, "rb") as fh:
        return fh.read()


_GENERATION_MODEL_PUBLIC_FIELDS = (
    "name",
    "display_name",
    "type",
    "is_local",
    "sort_order",
)


async def _visible_generation_rows(
    user_id: str, *, include_actual_provider: bool = False
) -> list[dict]:
    """The image/video catalog rows this user may see, in one place.

    ``generation-models`` and ``generation-capabilities`` MUST agree row for
    row — a picker entry with no caps entry means the UI shows a knob it was
    told to hide. Two hand-rolled copies of "list, filter by Settings, keep
    image/video" is exactly the "two predicates that must agree" shape that
    drifts silently, so both endpoints call this and neither re-derives it.
    """
    from app.repositories import mediahub_model_repository as _repo_mod
    from app.services.ai.platform_model_visibility import (
        filter_platform_models_for_user,
    )

    rows = await _repo_mod.get_mediahub_model_repository().list_enabled(
        viewer_user_id=user_id, include_actual_provider=include_actual_provider
    )
    # The user's Settings → platform-model card (master switch + per-model
    # blacklist) applies here too; the picker must show what Settings shows.
    rows = await filter_platform_models_for_user(user_id, rows)
    return [r for r in rows if r.get("type") in ("image", "video")]


@router.get("/canvases/generation-models")
async def list_generation_models(auth: AuthDep) -> dict:
    """Image/video rows from the mediahub_models catalog (public columns
    only — no api_key/base_url) for the composer's model picker."""
    data = [
        {k: r.get(k) for k in _GENERATION_MODEL_PUBLIC_FIELDS}
        for r in await _visible_generation_rows(auth.user_id)
    ]
    return {"success": True, "data": data}


@router.get("/canvases/generation-capabilities")
async def list_generation_capabilities(auth: AuthDep) -> dict:
    """Per-model knob capabilities, keyed by catalog row name.

    Server-side projection of each row's protocol capabilities, so the UI can
    hide what a model cannot honour without ever learning ``actual_provider``
    or re-implementing the registry lookup — one predicate, one place.
    Visibility follows ``generation-models`` exactly because both read
    ``_visible_generation_rows``: a model the picker shows always has an
    entry here.

    ``honours_ratio`` is deliberately NOT exposed: it describes an internal
    strategy, not something the UI can act on.
    """
    from app.services.ai.provider_protocols import resolve_generation_protocol
    from app.services.ai.provider_protocols.base import ProviderCapabilities
    from app.services.generation.aspect import ASPECT_RATIOS

    order = list(ASPECT_RATIOS)  # stable declaration order for the UI grid
    data: dict[str, dict] = {}
    # The provider string is asked for explicitly and consumed HERE: only the
    # derived capability values reach the response, never the provider itself.
    rows = await _visible_generation_rows(auth.user_id, include_actual_provider=True)
    for r in rows:
        proto = resolve_generation_protocol((r.get("actual_provider") or "").lower())
        caps = proto.capabilities if proto else ProviderCapabilities.none()
        data[str(r.get("name"))] = {
            "ratios": [x for x in order if x in caps.ratios],
            "quality": caps.quality,
            "resolution": caps.resolution,
            "max_refs": caps.max_refs,
            "negative": caps.negative,
            "video_modes": sorted(caps.video_modes),
        }
    return {"success": True, "data": data}


@router.get("/canvases/text-models")
async def list_text_models(auth: AuthDep) -> dict:
    """Enabled ``llm`` rows from the mediahub_models catalog (public columns
    only — no api_key/base_url) for the prompt node's text-model picker.

    Same catalog and public-field contract as ``generation-models``; the two
    endpoints differ only in the ``type`` they surface (text vs image/video),
    so the smart-canvas text prompt and the image/video composer read one
    consistent source of truth instead of a hardcoded frontend list."""
    from app.repositories import mediahub_model_repository as _repo_mod

    rows = await _repo_mod.get_mediahub_model_repository().list_enabled(
        "llm", viewer_user_id=auth.user_id
    )
    data = [{k: r.get(k) for k in _GENERATION_MODEL_PUBLIC_FIELDS} for r in rows]
    return {"success": True, "data": data}


@router.get("/canvases/generations/{task_id}")
async def get_canvas_generation(task_id: str, auth: AuthDep) -> dict:
    """Poll one generation task. Reads task_tracking (the UI's single source
    of truth — route C); the durable result lands in metadata.result_url."""
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import TaskTracking

    async with read_scope() as session:
        row = (
            (
                await session.execute(
                    select(
                        TaskTracking.dbos_workflow_id,
                        TaskTracking.phase,
                        TaskTracking.status,
                        TaskTracking.error_msg,
                        TaskTracking.metadata_.label("metadata"),
                    )
                    .where(TaskTracking.dbos_workflow_id == task_id)
                    .where(TaskTracking.user_id == auth.user_id)
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True, "data": dict(row)}


@router.delete("/canvases/generations/{task_id}")
async def cancel_canvas_generation(task_id: str, auth: AuthDep) -> dict:
    """Really cancel one generation task (P1-1). Stop used to only abandon the
    frontend poll while the DBOS task kept burning provider quota; here we ask
    the DBOS engine to cancel it. Ownership is gated through task_tracking (the
    UI's single source of truth — route C); we NEVER PATCH phase ourselves —
    the ``mirror_dbos_lifecycle_to_tracking`` trigger reflects
    phase='cancelled' once the engine flips the workflow to CANCELLED.
    Idempotent: cancelling an already-terminal task is a harmless no-op."""
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import TaskTracking
    from app.services.infra import dbos_orchestrator

    async with read_scope() as session:
        owned = (
            await session.execute(
                select(TaskTracking.dbos_workflow_id)
                .where(TaskTracking.dbos_workflow_id == task_id)
                .where(TaskTracking.user_id == auth.user_id)
            )
        ).scalar()
    if owned is None:
        raise HTTPException(status_code=404, detail="Task not found")
    await dbos_orchestrator.cancel_workflow(task_id)
    return {"success": True}


async def _is_team_member(team_id: str, user_id: str) -> bool:
    """Same membership check as scope_guards.verify_scope_access."""
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import TeamMembers

    async with read_scope() as session:
        member = (
            await session.execute(
                select(TeamMembers.team_id)
                .where(TeamMembers.team_id == int(str(team_id)))
                .where(TeamMembers.user_id == user_id)
                .limit(1)
            )
        ).first()
    return member is not None


@router.get("/canvases/team/{team_id}")
async def list_team_canvases(team_id: str, auth: AuthDep) -> dict:
    """Every project in the team with its canvases embedded as summary
    columns — one query, replacing the canvas landing page's
    fetchProjects + per-project listCanvases N+1 fan-out."""
    if not await _is_team_member(team_id, auth.user_id):
        raise HTTPException(status_code=403, detail="You are not a member of this team")
    rows = await CanvasRepository().list_team_tree(team_id)
    data = [
        {
            "project_id": str(r.get("id")),
            "project_name": r.get("name") or "",
            "canvases": [
                {
                    "id": str(c.get("id")),
                    "name": c.get("name") or "",
                    "kind": c.get("kind") or "smart",
                    "updated_at": c.get("updated_at"),
                }
                for c in (r.get("canvases") or [])
            ],
        }
        for r in rows
    ]
    return {"success": True, "data": data}


@router.get("/canvases/team/{team_id}/trash")
async def list_team_canvas_trash(team_id: str, auth: AuthDep) -> dict:
    """A team's trashed canvases (G9), newest-trashed first. Registered
    BEFORE /canvases/{canvas_id} — static segments must win the match."""
    if not await _is_team_member(team_id, auth.user_id):
        raise HTTPException(status_code=403, detail="You are not a member of this team")
    rows = await CanvasRepository().list_trashed_for_team(team_id)
    data = [
        {
            "id": str(r.get("id")),
            "name": r.get("name") or "",
            "kind": r.get("kind") or "smart",
            "updated_at": r.get("updated_at"),
            "deleted_at": r.get("deleted_at"),
            "project_id": str(r.get("project_id")),
            "project_name": (r.get("projects") or {}).get("name") or "",
        }
        for r in rows
    ]
    return {"success": True, "data": data}


def _episode_rank(siblings: list, episode_id: str) -> int | None:
    """1-based position of ``episode_id`` within ``siblings`` (a project's
    episodes ordered by ``sort_order`` ascending — the shape
    ``EpisodeRepository.list_by_project`` already returns).

    NOT the raw ``sort_order`` value: that column is an ever-increasing
    counter that deletes never renumber (repair round 1 — a mid-project
    delete leaves gaps, so 'EP{sort_order}' can show e.g. EP7 for what is
    actually the project's 3rd remaining episode). Matches the workspace
    sidebar's own ``epNumber = epIdx + 1`` display convention
    (``ProjectWorkspace.tsx``) exactly, so the canvas name and the sidebar
    numbering never disagree."""
    for idx, sib in enumerate(siblings):
        if str(sib.get("id")) == str(episode_id):
            return idx + 1
    return None


def _storyboard_name(ep: dict, rank: int | None) -> str:
    """``EP{n} · Storyboard`` where n is the episode's 1-based rank among
    its project's episodes (see ``_episode_rank``); falls back to the
    episode's title when the episode couldn't be located in its own
    project's list (defensive — should not be reachable in practice)."""
    if rank is not None:
        return f"EP{rank} · Storyboard"
    title = ep.get("title") or "Untitled"
    return f"{title} · Storyboard"


@router.get(
    "/canvases/storyboard",
    summary="Get or create the episode's system storyboard canvas",
)
async def get_or_create_storyboard_canvas(auth: AuthDep, episode_id: str) -> dict:
    """GET /api/v1/canvases/storyboard?episode_id=<id> — idempotent
    get-or-create of the episode's system storyboard canvas (kind=
    'storyboard', shot-nodes-on-canvas spec 2026-08-11 §2).

    Registered BEFORE the dynamic ``/canvases/{canvas_id}`` route below —
    static segments must win the match, or 'storyboard' would be captured
    as a canvas id (FastAPI matches routes in registration order).

    Gate split (2026-08-12 fix): this endpoint used to gate EVERY call —
    including one that only reads an already-existing canvas — behind
    ``verify_project_write_access``, so a viewer-role project member got a
    403 just trying to OPEN an episode's storyboard that another editor had
    already created. It must still gate a genuinely NEW canvas behind write
    access (a GET should not let a read-only visitor conjure a row), so the
    split is: peek first — existing → read gate; missing → write gate.
    """
    ep = await get_episode_repository().get_by_id(episode_id)
    if not ep:
        raise HTTPException(status_code=404, detail={"code": "episode_not_found"})
    project_id = str(ep["project_id"])

    svc = CanvasService()
    existing = await svc.peek_storyboard(project_id, episode_id)
    if existing is not None:
        # Pure read — any project member (owner / team / explicit
        # project_members row, any role) may fetch an existing storyboard.
        # The gate returns what it resolved, so ``can_edit`` is free here
        # rather than a second identical resolution.
        access = await resolve_project_read_access(project_id=project_id, auth=auth)
        return {
            "success": True,
            "data": _to_response(existing, can_edit=access.can_write),
        }

    # No canvas yet: this GET is about to CREATE one, so it must pass the
    # same gate a POST would (read-only visitors should not be able to
    # conjure a new canvas via a GET).
    await verify_project_write_access(project_id=project_id, auth=auth)

    # sort_order-ascending list of the project's own episodes, purely to
    # derive the 1-based display rank for naming (repair round 1 — see
    # _episode_rank's docstring for why raw sort_order is wrong here).
    siblings = await get_episode_repository().list_by_project(project_id)
    rank = _episode_rank(siblings, episode_id)

    row = await svc.get_or_create_storyboard(
        project_id=project_id,
        episode_id=episode_id,
        name=_storyboard_name(ep, rank),
        created_by=auth.user_id,
    )
    if row is None:
        raise HTTPException(status_code=500, detail="storyboard canvas create failed")
    # This branch only runs AFTER verify_project_write_access passed, so the
    # caller demonstrably has write rights — no second round trip to re-ask
    # the same question the read gate's ``can_write`` would answer True.
    return {"success": True, "data": _to_response(row, can_edit=True)}


# ============================================================
# Canvas-scoped routes
# ============================================================


@router.get("/canvases/{canvas_id}")
async def get_canvas(
    auth: AuthDep,
    canvas_id: str = Path(..., description="Snowflake canvas ID"),
) -> dict:
    """Load one canvas.

    The payload carries ``can_edit`` — the SAME verdict
    ``verify_project_write_access`` would reach on the PUT (both resolve
    through ``scope_guards._resolve_project_access``). Before this the
    client had no way to learn its rights except by sending a doomed PUT
    and reading the 403 off it: every viewer load cost one guaranteed-to-
    fail write, and until it came back the UI happily offered edit
    gestures whose results were silently discarded.

    The read gate hands that verdict back (#1828 follow-up): gating and
    reporting used to be two calls resolving the identical (project, user)
    pair — up to three extra SELECTs per load, for an answer already in
    hand.
    """
    _project_id, access = await _gate_canvas_read_access(canvas_id, auth)
    svc = CanvasService()
    row = await svc.get(canvas_id)
    if row is None:
        raise HTTPException(status_code=404, detail="canvas not found")
    return {"success": True, "data": _to_response(row, can_edit=access.can_write)}


@router.put("/canvases/{canvas_id}")
async def update_canvas(
    auth: AuthDep,
    payload: CanvasUpdate,
    canvas_id: str = Path(..., description="Snowflake canvas ID"),
) -> dict:
    await _gate_canvas_write(canvas_id, auth)
    svc = CanvasService()
    try:
        row = await svc.update_with_lock(canvas_id, payload)
        return {"success": True, "data": _to_response(row)}
    except LookupError:
        raise HTTPException(status_code=404, detail="canvas not found")
    except CanvasConflict as conflict:
        body = CanvasConflictResponse(
            current=CanvasResponse(**_to_response(conflict.current))
        )
        raise HTTPException(status_code=409, detail=body.model_dump(mode="json"))
    except Exception as exc:  # pragma: no cover - belt-and-braces
        logger.exception(f"canvas PUT {canvas_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="canvas save failed")


@router.delete("/canvases/{canvas_id}")
async def delete_canvas(
    auth: AuthDep,
    canvas_id: str = Path(..., description="Snowflake canvas ID"),
) -> dict:
    """Soft delete (G9): the canvas moves to the team trash; restore or
    purge from there."""
    await _gate_canvas_write(canvas_id, auth)
    svc = CanvasService()
    ok = await svc.soft_delete(canvas_id)
    if not ok:
        raise HTTPException(status_code=404, detail="canvas not found")
    return {"success": True}


@router.post("/canvases/{canvas_id}/restore")
async def restore_canvas(
    auth: AuthDep,
    canvas_id: str = Path(..., description="Snowflake canvas ID"),
) -> dict:
    await _gate_canvas_write(canvas_id, auth)
    svc = CanvasService()
    ok = await svc.restore(canvas_id)
    if not ok:
        raise HTTPException(status_code=404, detail="canvas not found in trash")
    return {"success": True}


@router.delete("/canvases/{canvas_id}/purge")
async def purge_canvas(
    auth: AuthDep,
    canvas_id: str = Path(..., description="Snowflake canvas ID"),
) -> dict:
    """Permanent delete — only valid for canvases already in the trash."""
    await _gate_canvas_write(canvas_id, auth)
    svc = CanvasService()
    ok = await svc.purge(canvas_id)
    if not ok:
        raise HTTPException(status_code=404, detail="canvas not found in trash")
    return {"success": True}


# ============================================================
# Project-scoped routes
# ============================================================


@router.get("/projects/{project_id}/canvases")
async def list_project_canvases(
    auth: AuthDep,
    project_id: str = Path(..., description="Snowflake project ID"),
) -> dict:
    """List a project's canvases — a pure read. 2026-08-12 fix: this GET was
    gated behind ``verify_project_write_access``, so a viewer-role project
    member 403'd just listing canvases (inconsistent with the trash-list
    sibling right below, which already used the read guard)."""
    await verify_project_read_access(project_id=project_id, auth=auth)
    svc = CanvasService()
    rows = await svc.list_for_project(project_id)
    return {"success": True, "data": [_to_response(r) for r in rows]}


@router.get("/projects/{project_id}/canvases/trash")
async def list_project_canvas_trash(
    auth: AuthDep,
    project_id: str = Path(..., description="Snowflake project ID"),
) -> dict:
    """A project's trashed canvases (workspace Trash module). Project-scoped
    so it never needs the team-id sentinel the team trash endpoint carries."""
    await verify_project_read_access(project_id=project_id, auth=auth)
    svc = CanvasService()
    rows = await svc.list_trashed_for_project(project_id)
    data = [
        {
            "id": str(r.get("id")),
            "name": r.get("name") or "",
            "kind": r.get("kind") or "smart",
            "updated_at": r.get("updated_at"),
            "deleted_at": r.get("deleted_at"),
            "project_id": str(r.get("project_id")),
        }
        for r in rows
    ]
    return {"success": True, "data": data}


async def _require_asset_in_project_scope(project_id: str, asset_id: str) -> None:
    """404 unless ``asset_id`` is readable from the project's asset scope.

    ``canvases.asset_id`` (mig 446) FKs to ``assets(id)`` and the FK does not
    care whose asset it is — so without this a member of team A could hang their
    canvas off team B's asset and get a 201 for it.

    An asset's scope is a ``teams.id``. A project's is its ``team_id``, or, when
    that is NULL (a personal project), the OWNER's personal team — the same
    resolution ``GET /projects/{id}/assets`` performs; using the CALLER's team
    would check the wrong scope for a collaborator. ``AssetsRepository.get``
    also admits global system presets (scope_id NULL), which are readable from
    every scope by design.

    Every refusal is the same 404: whether the id does not exist, is soft
    deleted, or belongs to another team is not something the caller is entitled
    to tell apart.
    """
    exists, team_id, owner_id = await AssetRelationsRepository().project_team_id(
        int(project_id)
    )
    if not exists:  # pragma: no cover - the write guard above already 404s
        raise HTTPException(status_code=404, detail="project not found")
    if team_id is None:
        if owner_id is None:
            raise HTTPException(status_code=404, detail="asset_not_found")
        try:
            team_id = int(await _resolve_personal_team_id(str(owner_id)))
        except ValueError:
            # Legacy owner with no personal team: no scope to check against, so
            # no asset can be proven to belong here.
            raise HTTPException(status_code=404, detail="asset_not_found")
    if await AssetsRepository().get(int(asset_id), int(team_id)) is None:
        raise HTTPException(status_code=404, detail="asset_not_found")


@router.post("/projects/{project_id}/canvases")
async def create_project_canvas(
    auth: AuthDep,
    payload: CanvasCreate,
    project_id: str = Path(..., description="Snowflake project ID"),
) -> dict:
    await verify_project_write_access(project_id=project_id, auth=auth)
    if payload.asset_id is not None:
        await _require_asset_in_project_scope(project_id, payload.asset_id)
    svc = CanvasService()
    row = await svc.create_in_project(project_id, payload, created_by=auth.user_id)
    if row is None:
        raise HTTPException(status_code=500, detail="canvas create failed")
    return {"success": True, "data": _to_response(row)}


@router.post("/canvases/{canvas_id}/timeline-runs")
async def dispatch_timeline_run(
    auth: AuthDep,
    payload: CanvasTimelineRequest,
    canvas_id: str = Path(..., description="Snowflake canvas ID"),
) -> dict:
    """Dispatch a timeline-director film (G8): one DBOS task chains the
    segments (t2v → tail-frame-guided i2v), concats, and registers the
    durable film. Single task row — per-segment progress decorates its
    metadata."""
    import uuid as _uuid

    from app.services.infra import dbos_orchestrator
    from app.workflows.canvas_timeline import canvas_timeline_workflow

    await _gate_canvas_write(canvas_id, auth)

    wf_id = str(_uuid.uuid4())
    summary = payload.segments[0].prompt[:60]
    await get_task_manager().create(
        user_id=auth.user_id,
        task_type="canvas_timeline",  # ≤20 chars (VARCHAR(20))
        title=f"Timeline film ({len(payload.segments)} segments)",
        subtitle=summary,
        dbos_workflow_id=wf_id,
        metadata={
            "canvas_id": canvas_id,
            "node_id": payload.node_id,
            "kind": "video",
            "segments_total": len(payload.segments),
        },
    )
    await dbos_orchestrator.start_workflow_routed(
        "canvas_timeline",
        dbos_workflow_callable=canvas_timeline_workflow,
        dbos_workflow_kwargs={
            "segments": [seg.model_dump() for seg in payload.segments],
            "model": payload.model,
            "aspect": payload.aspect,
            "canvas_id": int(canvas_id),
            "node_id": payload.node_id,
            "user_id": auth.user_id,
        },
        workflow_id=wf_id,
    )
    return {"success": True, "data": {"task_id": wf_id}}


@router.post("/canvases/{canvas_id}/generations")
async def dispatch_canvas_generations(
    auth: AuthDep,
    payload: CanvasGenerationRequest,
    canvas_id: str = Path(..., description="Snowflake canvas ID"),
) -> dict:
    """Dispatch ``count``× image/video generation tasks for one node.

    Each item is an independent DBOS workflow with its own task_tracking row
    (the queue is the concurrency governor — the frontend never opens its own
    parallelism). ``count`` clamps to 8 (Infinite's cap); video always 1.
    Results arrive via GET /canvases/generations/{task_id} (metadata.result_url).
    """
    await _gate_canvas_write(canvas_id, auth)
    count = 1 if payload.kind == "video" else max(1, min(payload.count, 8))
    canvas_id_int = int(canvas_id) if canvas_id.isdigit() else None

    from app.services.infra import dbos_orchestrator
    from app.workflows.canvas_generation import canvas_generation_workflow

    mgr = get_task_manager()
    task_ids: list[str] = []
    for index in range(count):
        wf_id = str(uuid.uuid4())
        task_id = await mgr.create(
            user_id=auth.user_id,
            task_type="canvas_gen",  # ≤20 chars (task_tracking.task_type VARCHAR(20))
            title=f"Generate {payload.kind}",
            subtitle=payload.prompt[:80],
            dbos_workflow_id=wf_id,
            metadata={
                "canvas_id": canvas_id,
                "node_id": payload.node_id,
                "kind": payload.kind,
                "index": index + 1,
                "count": count,
            },
        )
        await dbos_orchestrator.start_workflow_routed(
            "canvas_generation",
            dbos_workflow_callable=canvas_generation_workflow,
            dbos_workflow_kwargs={
                "kind": payload.kind,
                "prompt": payload.prompt,
                "model": payload.model,
                "params": payload.params,
                "canvas_id": canvas_id_int,
                "node_id": payload.node_id,
                "user_id": auth.user_id,
                "source_url": payload.source_url,
            },
            workflow_id=wf_id,
        )
        task_ids.append(task_id)
    return {"success": True, "task_ids": task_ids}


# ============================================================
# Smart-mode prompt run
# ============================================================


@router.post("/canvases/runs/prompts")
async def run_canvas_prompt(
    auth: AuthDep,
    payload: CanvasPromptRunRequest,
) -> dict:
    """Execute one smart-canvas prompt run.

    Failure modes (adapter init / LLM call / empty body) are returned
    in-band via {ok: false, error}; the HTTP layer always returns 200
    unless gating fails. The frontend's `backendRunner` distinguishes
    ok vs failed by reading the body, not the status code.
    """
    await _gate_canvas_write(payload.canvas_id, auth)
    svc = CanvasRunService()
    result = await svc.run_prompt(
        body=payload.body,
        provider_slug=payload.provider_slug,
        agent_id=payload.agent_id,
    )
    body = CanvasPromptRunResponse(ok=result.ok, text=result.text, error=result.error)
    return {"success": True, "data": body.model_dump(mode="json")}


@router.get("/canvases/providers/nous-center/verify")
async def verify_nous_center_protocol(auth: AuthDep) -> dict:
    """Confirm the nous-center service is reachable + the contract holds.

    Drives the "Verify protocol" button in the AI settings UI. Result
    is always in-band: {ok, base_url?, workflows_visible? | error}.
    Any authenticated user can probe (the call is read-only and uses
    the server-side service token, not the user's identity).
    """
    from app.core.config import get_settings
    from app.services.canvas.nous_center_verify import verify_nous_center

    # Touch auth so the dep injection is exercised — the value isn't
    # used, but the route still requires a logged-in caller.
    _ = auth.user_id

    settings = get_settings()
    result = await verify_nous_center(settings)
    return {"success": True, "data": result}
