# backend/app/api/projects_router.py

"""
Projects Router

MediaTrack project management API endpoints: project CRUD, file uploads,
file management, and video linking.
Requires authentication (JWT or API Key).
"""

from typing import Any, Dict, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from loguru import logger

from app.core.config import settings
from app.core.deps import AuthDep
from app.core.scope_guards import (
    verify_project_read_access,
    verify_project_write_access,
)
from app.repositories.generated_media_repository import GeneratedMediaRepository
from app.schemas.projects import (
    AddMemberRequest,
    CreateCollectionRequest,
    CreateCommentRequest,
    CreateFolderRequest,
    CreateShareRequest,
    GenerateMissingResponse,
    LinkMediaRequest,
    MoveFileRequest,
    ProjectCreate,
    ProjectFileUpdate,
    ProjectSuggestionsResponse,
    ProjectUpdate,
    RecentItemsResponse,
    RenameFolderRequest,
    ReviewStatusUpdate,
    StyleProfileUpdate,
    UpdateMemberRoleRequest,
)
from app.schemas.workflow import (
    BLOCK_DEPS_PENDING,
    AdvancePreview,
    AttachWorkflowRequest,
    DepsBackwardOnly,
    NodeCreate,
    NodeDeleteBlocked,
    NodePatch,
    ProjectWorkflowOut,
    ReinstantiatePerEpisodeRequest,
    WorkflowAlreadyInstantiated,
)
from app.services.library.projects_service import ProjectsService
from app.services.modules.gate import require_module

router = APIRouter(
    prefix="/projects",
    dependencies=[Depends(require_module("projects"))],
)

MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500 MB


# ============================================
# Project endpoints
# ============================================


@router.get("")
async def list_projects(
    auth: AuthDep,
    project_type: Optional[str] = Query(None, description="Filter by project type"),
    starred: Optional[bool] = Query(None, description="Filter starred projects only"),
    team_id: Optional[str] = Query(
        None, description="Filter by team ID (null = personal)"
    ),
    archived: Optional[bool] = Query(
        None,
        description=(
            "true = archived only, false = active only, "
            "omitted = both (the frontend fetches both and filters locally)"
        ),
    ),
):
    """
    List all projects accessible to the current user. All filters are
    pushed down to SQL in the service/repo layer (no in-memory filtering).

    - **project_type**: Optional filter (internal, external, personal).
    - **starred**: Optional filter for starred projects.
    - **team_id**: Optional team filter. If provided, returns team projects.
      If omitted, returns all user projects (backward-compatible).
    - **archived**: true = archived only, false = active only, omitted =
      both (frontend fetches once and splits into Active/Archived views).
    """
    try:
        svc = ProjectsService()
        projects = await svc.get_projects_with_counts(
            auth.user_id,
            team_id=team_id,
            project_type=project_type,
            starred=starred,
            archived=archived,
        )
        return {"success": True, "data": projects}
    except Exception as e:
        logger.error(f"Failed to list projects: {e}")
        raise HTTPException(status_code=500, detail="Failed to list projects")


@router.post("")
async def create_project(data: ProjectCreate, auth: AuthDep):
    """Create a new project owned by the current user."""
    try:
        svc = ProjectsService()
        project = await svc.create_project(
            user_id=auth.user_id,
            data=data.model_dump(exclude_none=True),
        )
        return {"success": True, "data": project}
    except Exception as e:
        logger.error(f"Failed to create project: {e}")
        raise HTTPException(status_code=500, detail="Failed to create project")


@router.get("/suggestions", response_model=ProjectSuggestionsResponse)
async def get_project_suggestions(
    auth: AuthDep,
    team_id: Optional[str] = Query(
        None, description="Filter by team ID (null = personal)"
    ),
) -> ProjectSuggestionsResponse:
    """Batch 'one next step' queue data source for the homepage (PR-8 Task
    A, G7). MUST stay declared before ``/{project_id}`` below — FastAPI /
    Starlette match routes in registration order, and both are a single
    path segment under this prefix, so a later declaration would be
    swallowed as ``project_id="suggestions"``.
    """
    svc = ProjectsService()
    items = await svc.get_project_suggestions(auth.user_id, team_id=team_id)
    return ProjectSuggestionsResponse(items=items)


@router.get("/recent-items", response_model=RecentItemsResponse)
async def get_recent_items(
    auth: AuthDep,
    limit: int = Query(8, ge=1, le=20, description="Max items (capped at 20)"),
) -> RecentItemsResponse:
    """Recently-edited scripts + canvases across the caller's projects, merged
    and sorted by ``updated_at`` desc, capped at ``limit``.

    MUST stay declared before ``/{project_id}`` below — like ``/suggestions``,
    ``recent-items`` is a single path segment under this prefix, so a later
    declaration would be swallowed as ``project_id="recent-items"`` (FastAPI /
    Starlette match routes in registration order).
    """
    svc = ProjectsService()
    items = await svc.get_recent_items(auth.user_id, limit=limit)
    return RecentItemsResponse(items=items)


@router.get("/{project_id}")
async def get_project(
    project_id: str,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_read_access),
):
    """Get a single project by ID with file count, plus this caller's
    ``effective_role``.

    ``effective_role`` (manager / editor / viewer / external / null) comes
    from ``workflow_roles.resolve_effective_role`` — the same resolver the
    workflow write surface already gates on — so the workspace UI has ONE
    place to learn what the current user may do in this project, instead of
    inferring it from whichever endpoint happens to 403 first.
    """
    try:
        from app.core.workflow_roles import resolve_effective_role

        svc = ProjectsService()
        project = await svc.get_project(project_id)
        if isinstance(project, dict):
            project["effective_role"] = await resolve_effective_role(
                auth.user_id, project_id=project_id
            )
        return {"success": True, "data": project}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to get project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get project")


@router.put("/{project_id}")
async def update_project(
    project_id: str,
    data: ProjectUpdate,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_write_access),
):
    """Update a project. Owner, team member, or manager/editor project member."""
    try:
        svc = ProjectsService()
        project = await svc.update_project(
            project_id=project_id,
            user_id=auth.user_id,
            data=data.model_dump(exclude_none=True),
        )
        return {"success": True, "data": project}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to update project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update project")


@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_write_access),
):
    """Delete a project and all its files. Only the owner can delete."""
    try:
        svc = ProjectsService()
        await svc.delete_project(project_id=project_id, user_id=auth.user_id)
        return {"success": True, "message": "Project deleted"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to delete project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete project")


# ============================================
# Style profile (Phase 4 M8 — one row per project)
# ============================================


@router.get("/{project_id}/style-profile")
async def get_style_profile(
    project_id: str,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_read_access),
):
    """The project's style profile, or null when none has been saved yet.

    Access: owner, team member, or any project_members role (read guard)."""
    from app.repositories.project_style_profile_repository import (
        get_project_style_profile_repository,
    )

    try:
        profile = await get_project_style_profile_repository().get(int(project_id))
        return {"success": True, "data": profile}
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid project id")
    except Exception as e:
        logger.error(f"Failed to get style profile for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get style profile")


@router.put("/{project_id}/style-profile")
async def put_style_profile(
    project_id: str,
    data: StyleProfileUpdate,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_write_access),
):
    """Create-or-merge the project's style profile. Absent fields keep
    their stored value — a PUT carrying only style_md never clobbers
    visual_style / reference_links."""
    from app.repositories.project_style_profile_repository import (
        get_project_style_profile_repository,
    )

    try:
        profile = await get_project_style_profile_repository().upsert(
            int(project_id),
            style_md=data.style_md,
            visual_style=data.visual_style,
            reference_links=data.reference_links,
            updated_by=auth.user_id,
        )
        return {"success": True, "data": profile}
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid project id")
    except Exception as e:
        logger.error(f"Failed to save style profile for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to save style profile")


# ============================================
# SOP stage endpoints (Phase 5b)
# ============================================


@router.get("/stages/catalog")
async def list_stage_catalog(auth: AuthDep):
    """Global catalog of all SOP stages, ordered by sort_order."""
    from app.repositories.project_stages_repository import (
        get_project_stages_repository,
    )

    return {
        "success": True,
        "data": await get_project_stages_repository().list_catalog(),
    }


@router.get("/{project_id}/stage_history")
async def get_stage_history(
    project_id: str,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_read_access),
):
    """Append-only SOP stage transition history for a project."""
    from app.repositories.project_stages_repository import (
        get_project_stages_repository,
    )

    try:
        return {
            "success": True,
            "data": await get_project_stages_repository().history(int(project_id)),
        }
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid project id")
    except Exception as e:
        logger.error(f"Failed to get stage history for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get stage history")


@router.post(
    "/{project_id}/storyboard/generate-missing",
    response_model=GenerateMissingResponse,
)
async def generate_missing_frames(
    project_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_project_write_access),
) -> GenerateMissingResponse:
    """Batch-generate every empty storyboard shot in the project (flag-gated).

    Flag ``FEATURE_SHOT_GENERATE`` off → 404 (existence hidden), mirroring
    ``script_shots_router.py::generate_shot``."""
    if not settings.FEATURE_SHOT_GENERATE:
        raise HTTPException(status_code=404, detail="Not Found")
    svc = ProjectsService()
    data = await svc.generate_missing_frames(project_id, auth.user_id)
    return GenerateMissingResponse(**data)


# ============================================
# Workflow nodes (M1 PR-B) — instance nodes, node tweaks, advance/retreat
# ============================================


async def _require_project_episode(project_id: str, episode_id: str) -> dict:
    """Fetch a client-supplied ``episode_id`` and assert it belongs to the path
    ``project_id`` (B2 T3 review, defense-in-depth over the A-line trust-boundary
    lesson).

    The four workflow-cursor endpoints take ``episode_id`` as a QUERY param — it
    is caller-controlled and must never be trusted to belong to the path
    project. Without this check ``episode_repository.get_by_id`` happily returns
    a FOREIGN project's episode, whose ``current_node_id`` would then leak back
    in the workflow response (existence + cursor disclosure). 404 (never echo)
    on missing or cross-project. Returns the validated episode dict so callers
    reuse its cursor without a second read."""
    from app.repositories.episode_repository import get_episode_repository

    episode = await get_episode_repository().get_by_id(episode_id)
    if episode is None or str(episode.get("project_id")) != str(project_id):
        raise HTTPException(status_code=404, detail="Episode not found")
    return episode


@router.get("/{project_id}/workflow", response_model=ProjectWorkflowOut)
async def get_project_workflow(
    project_id: str,
    auth: AuthDep,
    episode_id: str = Query(
        ..., description="Scope the workflow view to a single episode (B2)."
    ),
    _project_guard: None = Depends(verify_project_read_access),
) -> ProjectWorkflowOut:
    """The project's workflow: instance nodes (+ per-node status/members), the
    active-group cursor, and the count of currently-running agent runs.

    Empty ``nodes`` (``has_workflow=false``) for a No-workflow project — the
    Overview renders no workflow region in that case.

    ``episode_id`` (B2 T3): the node set and the cursor are read per-episode
    (``list_nodes_by_episode`` + ``episodes.current_node_id``) so the view
    reflects only that episode, never a sibling's template-cloned twins.
    Role/permission resolution and ``agents_active`` stay project-level (the
    response STRUCTURE is untouched here — the per-episode re-shape is B5)."""
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )
    from app.repositories.projects_repository import get_projects_repository

    repo = get_project_stage_nodes_repository()
    projects_repo = get_projects_repository()
    # Ownership check first — a foreign episode_id 404s here and never
    # reaches list_nodes_by_episode / cursor echo.
    episode = await _require_project_episode(project_id, episode_id)
    nodes = await repo.list_nodes_by_episode(project_id, episode_id)
    current_node_id = episode.get("current_node_id")
    agents_active = await repo.count_running_agent_runs(project_id)

    # Filed-file count per node's deliverable folder — one file scan, tallied by
    # folder_id (spec §5's "N files filed"). Best-effort: an unreadable store
    # leaves every count at 0, never fails the workflow read.
    counts: dict[str, int] = {}
    if any(n.get("folder_id") for n in nodes):
        try:
            files = await projects_repo.get_project_files(project_id)
            for f in files:
                fid = f.get("folder_id")
                if fid is not None:
                    counts[str(fid)] = counts.get(str(fid), 0) + 1
        except Exception as exc:  # noqa: BLE001 — count is decoration, not core
            logger.warning(
                f"[workflow] file count scan failed for {project_id}: {exc!r}"
            )
    enriched = [
        {**n, "deliverable_file_count": counts.get(str(n.get("folder_id")), 0)}
        for n in nodes
    ]

    return ProjectWorkflowOut(
        has_workflow=bool(nodes),
        current_node_id=(str(current_node_id) if current_node_id is not None else None),
        agents_active=agents_active,
        nodes=enriched,
    )


@router.post("/{project_id}/workflow")
async def attach_project_workflow(
    project_id: str,
    payload: AttachWorkflowRequest,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_write_access),
):
    """Attach a workflow template to an EXISTING project that has none yet.

    M1.x opt-in migration path (spec: rather than a lossy bulk script mapping
    the legacy 3-stage SOP onto an 11-node template for every project, the
    user opts a project in one at a time and picks the template themselves).
    Shares the exact instantiation path ``instantiate_project_workflow`` the
    create-project flow uses (mirror issues, deliverable folders, stage-hook
    dispatch, autopilot tick — all the same arrival machinery).

    Guards, in order:
      1. ``verify_project_write_access`` (owner / team member / project
         manager-editor) — same gate every other project-mutation endpoint
         in this router uses.
      2. A fast-path 409 if the project already has any instance nodes —
         this is an optimization (skip team/template lookups on the common
         "already set up" case), NOT the correctness guarantee: two
         concurrent attaches (double-click, a client retry, two
         collaborators) can both pass this plain SELECT before either
         commits. The actual guarantee is ``expect_fresh=True`` below, which
         holds a per-project Postgres advisory lock across the real
         check-and-insert inside ``instantiate_from_template`` — the losing
         side of a race raises ``WorkflowAlreadyInstantiated``, caught here
         and mapped to the same 409.
      3. The chosen template must belong to the project's own scope (its
         team, or — for a personal project, team_id IS NULL — the OWNER's
         personal team, the same boundary translation
         ``project_stage_issues._resolve_issue_team_id`` already uses) —
         otherwise 404, so a caller can never graft another team's template
         onto a project it doesn't own the scope of, and existence never
         leaks across teams.

    Note: ``projects.workflow_id`` (FK → the legacy, unrelated
    ``project_workflows`` table from mig 047) is NOT consulted here — it
    predates this M1 template system and is never written by it. The actual
    "does this project already have a workflow" signal is instance-node
    existence, same as ``GET /{project_id}/workflow``'s ``has_workflow``.
    """
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )
    from app.repositories.projects_repository import get_projects_repository
    from app.repositories.team_repository import get_team_repository
    from app.repositories.workflow_templates_repository import (
        get_workflow_templates_repository,
    )
    from app.services.workflow.instantiation import instantiate_project_workflow

    projects_repo = get_projects_repository()
    project = await projects_repo.get_project_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    nodes_repo = get_project_stage_nodes_repository()
    existing_nodes = await nodes_repo.list_nodes(project_id)
    if existing_nodes:
        raise HTTPException(status_code=409, detail="Project already has a workflow")

    team_id = project.get("team_id")
    if team_id is None:
        owner_id = project.get("owner_id")
        team_id = (
            await get_team_repository().get_personal_team_id(str(owner_id))
            if owner_id
            else None
        )

    template_team_id = await get_workflow_templates_repository().get_template_team_id(
        payload.template_id
    )
    if (
        team_id is None
        or template_team_id is None
        or str(template_team_id) != str(team_id)
    ):
        raise HTTPException(status_code=404, detail="Template not found")

    try:
        nodes = await instantiate_project_workflow(
            project_id,
            payload.template_id,
            method=payload.method,
            user_id=auth.user_id,
            expect_fresh=True,
        )
    except WorkflowAlreadyInstantiated:
        raise HTTPException(status_code=409, detail="Project already has a workflow")
    return {"success": True, "data": nodes}


@router.post("/{project_id}/workflow/reinstantiate-per-episode")
async def reinstantiate_workflow_per_episode(
    project_id: str,
    auth: AuthDep,
    payload: Optional[ReinstantiatePerEpisodeRequest] = None,
    _project_guard: None = Depends(verify_project_write_access),
):
    """B3 backfill (§6): ignite a legacy project-level workflow into per-episode
    chains — atomically drop the legacy nodes, fan the template out per episode,
    set each episode's cursor, and cancel the old mirror issues.

    A one-shot owner/manager action (``verify_project_write_access``), run once
    against the sleeping production project after deploy. Idempotent: a project
    already per-episode returns ``{"converted": false}`` and touches nothing, so
    a re-run is harmless. ``method`` optionally overrides the value inferred from
    the legacy chain's skip state.
    """
    from app.services.workflow.instantiation import (
        reinstantiate_project_per_episode,
    )

    result = await reinstantiate_project_per_episode(
        str(project_id),
        user_id=auth.user_id,
        method=payload.method if payload else None,
    )
    return {"success": True, "data": result}


@router.post("/{project_id}/workflow/surface-completion/sync")
async def sync_surface_completion_for_project(
    project_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_project_write_access),
) -> Dict[str, Any]:
    """B4 点火/修复:重算全项目每集的 surface 完成态(幂等,只正向)。
    部署后既有产物不会自己触发回流 hook,owner 调一次此端点补齐。"""
    from app.services.workflow.surface_completion import (
        sync_project_surface_completion,
    )

    try:
        data = await sync_project_surface_completion(project_id)
        return {"success": True, "data": data}
    except Exception as exc:
        logger.error(
            f"[Workflow] surface-completion sync for {project_id} failed: {exc}"
        )
        raise HTTPException(status_code=500, detail="Surface completion sync failed")


@router.get("/{project_id}/workflow/nodes/{node_id}/board")
async def get_stage_board(
    project_id: str,
    node_id: str,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_read_access),
):
    """Stage Board aggregate (M2 PR-F F1): one node's full row + its mirror
    issue (with sub-issues) + the files filed into its deliverable folder —
    the single data source the Stage Board workspace module reads from.

    Auth mirrors ``GET /{project_id}/workflow`` above: project-level read
    access only, no extra role gate (this is a pure read). 404s before any
    role concern — a missing/foreign node 404s the same way a missing project
    already does via the guard.

    ``issue`` is ``null`` both when the node never grew a mirror issue and
    when the project predates the three-segment origin id (a legacy
    two-segment mirror simply doesn't match the origin this builds) — either
    way a quiet null, never a 500.
    """
    from app.repositories.issue_repository import get_issue_repository
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )
    from app.services.library.project_stage_issues import (
        ORIGIN_KIND,
        build_stage_origin_id,
    )

    nodes_repo = get_project_stage_nodes_repository()
    node = await nodes_repo.get_node(node_id, project_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")

    issues_repo = get_issue_repository()

    def _issue_ref(row: dict) -> dict:
        return {
            "id": str(row["id"]),
            "identifier": row.get("identifier"),
            "title": row.get("title"),
            "status": row.get("status"),
            "assignee": {
                "user_id": (
                    str(row["assignee_user_id"])
                    if row.get("assignee_user_id")
                    else None
                ),
                "agent_id": (
                    str(row["assignee_agent_id"])
                    if row.get("assignee_agent_id")
                    else None
                ),
            },
        }

    issue_out: Optional[dict] = None
    try:
        origin_id = build_stage_origin_id(project_id, node_id)
        mirrors = await issues_repo.list_by_origin(ORIGIN_KIND, origin_id)
    except Exception as exc:  # noqa: BLE001 — a missing/broken mirror reads as null
        logger.warning(
            f"[stage-board] mirror lookup failed for project {project_id} "
            f"node {node_id}: {exc!r}"
        )
        mirrors = []
    if mirrors:
        mirror = mirrors[0]
        try:
            children = await issues_repo.list_children(int(mirror["id"]))
        except Exception as exc:  # noqa: BLE001 — sub-issue list is enrichment only
            logger.warning(
                f"[stage-board] sub-issue lookup failed for issue "
                f"{mirror.get('id')}: {exc!r}"
            )
            children = []
        issue_out = {
            **_issue_ref(mirror),
            "sub_issues": [_issue_ref(c) for c in children],
        }

    files_out: list = []
    folder_id = node.get("folder_id")
    if folder_id:
        try:
            raw_files = await nodes_repo.list_folder_files(folder_id)
        except Exception as exc:  # noqa: BLE001 — an unreadable store reads as empty
            logger.warning(
                f"[stage-board] file listing failed for project {project_id} "
                f"node {node_id}: {exc!r}"
            )
            raw_files = []

        source_ids = [
            f["source_issue_id"] for f in raw_files if f.get("source_issue_id")
        ]
        id_map: dict = {}
        if source_ids:
            try:
                id_map = await issues_repo.map_identifiers([int(i) for i in source_ids])
            except Exception as exc:  # noqa: BLE001 — the back-link chip is decoration
                logger.warning(
                    f"[stage-board] source-issue identifier map failed for "
                    f"project {project_id} node {node_id}: {exc!r}"
                )
        files_out = [
            {
                "id": str(f["id"]),
                "filename": f.get("filename"),
                "size": f.get("file_size_bytes"),
                "created_at": f.get("created_at"),
                "source_issue_identifier": (
                    id_map.get(str(f["source_issue_id"]))
                    if f.get("source_issue_id")
                    else None
                ),
            }
            for f in raw_files
        ]

    return {
        "success": True,
        "data": {"node": node, "issue": issue_out, "files": files_out},
    }


@router.patch("/{project_id}/workflow/nodes/{node_id}")
async def patch_workflow_node(
    project_id: str,
    node_id: str,
    payload: NodePatch,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_write_access),
):
    """In-place tweak of a live node (owner / members / schedule / skipped).

    Writes the instance only — never the template. Two different gates,
    depending on which fields the body touches (Task 7, workspace IA
    redesign spec §5):

    - CONFIG fields (owner_user_id/owner_agent_id/members/planned_start/
      planned_due/brief) require ``can_edit_node_config``: the project
      manager OR the node's episode's ``owner_id`` (Task 6) — tighter than,
      and NOT layered on top of, the base role gate below (an episode owner
      need not hold a project_members row at all).
    - Everything else (skipped/form_data/depends_on — instance STRUCTURE,
      not owner-configurable content) keeps the pre-existing behavior
      UNCHANGED: an effective role of manager/editor (the workflow
      single-source, on top of the write guard).
    - MIXED body (touches CONFIG fields AND a structure field in the same
      request, e.g. ``{"brief": "x", "skipped": true}``): 评审 Critical #1 —
      passing ``can_edit_node_config`` alone must NOT be enough to also
      apply the structure fields. Pre-branch, every PATCH required
      WRITE_ROLES; the config gate is intentionally narrower (an episode
      owner needs no project_members row at all), so an episode owner with
      no WRITE_ROLES membership could otherwise smuggle a structural change
      through by piggybacking it on a config field they ARE allowed to
      touch. Both gates must pass for a mixed body — see below."""
    from app.core.workflow_roles import WRITE_ROLES, resolve_effective_role
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )
    from app.services.workflow.node_authz import can_edit_node_config

    fields = payload.model_fields_set
    CONFIG_FIELDS = {
        "owner_user_id",
        "owner_agent_id",
        "members",
        "planned_start",
        "planned_due",
        "brief",
    }
    repo = get_project_stage_nodes_repository()

    if fields & CONFIG_FIELDS:
        node_for_authz = await repo.get_node(node_id, project_id)
        if node_for_authz is None:
            raise HTTPException(status_code=404, detail="Node not found")
        if not await can_edit_node_config(
            project_id, node_for_authz.get("episode_id"), auth.user_id
        ):
            raise HTTPException(
                status_code=403, detail={"code": "node_config_forbidden"}
            )
        # Mixed body: also ALSO require WRITE_ROLES for the structure fields
        # riding along (see docstring "MIXED body" above). An episode owner
        # with no WRITE_ROLES membership may PATCH config fields alone; the
        # moment the same body touches skipped/form_data/depends_on it needs
        # the base role gate too, on top of (not instead of) the config gate.
        if fields - CONFIG_FIELDS:
            role = await resolve_effective_role(auth.user_id, project_id=project_id)
            if role not in WRITE_ROLES:
                raise HTTPException(status_code=403, detail="Insufficient role")
    else:
        role = await resolve_effective_role(auth.user_id, project_id=project_id)
        if role not in WRITE_ROLES:
            raise HTTPException(status_code=403, detail="Insufficient role")

    kwargs: dict = {}
    if "owner_user_id" in fields or "owner_agent_id" in fields:
        kwargs["_set_owner"] = True
        kwargs["owner_user_id"] = (
            str(payload.owner_user_id) if payload.owner_user_id else None
        )
        kwargs["owner_agent_id"] = (
            str(payload.owner_agent_id) if payload.owner_agent_id else None
        )
    if "planned_start" in fields:
        kwargs["_set_schedule_start"] = True
        kwargs["planned_start"] = payload.planned_start
    if "planned_due" in fields:
        kwargs["_set_schedule_due"] = True
        kwargs["planned_due"] = payload.planned_due
    if "members" in fields and payload.members is not None:
        kwargs["members"] = [
            {
                "user_id": str(m.user_id) if m.user_id else None,
                "agent_id": str(m.agent_id) if m.agent_id else None,
            }
            for m in payload.members
        ]
    if "skipped" in fields:
        kwargs["skipped"] = payload.skipped
    if "form_data" in fields and payload.form_data is not None:
        kwargs["form_data"] = payload.form_data
    if "depends_on" in fields and payload.depends_on is not None:
        kwargs["depends_on"] = payload.depends_on
    if "brief" in fields and payload.brief is not None:
        kwargs["brief"] = payload.brief

    try:
        row = await repo.update_node(node_id, project_id, **kwargs)
    except DepsBackwardOnly as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": exc.reason,
                "message": (
                    "A node's dependency must point at another node earlier "
                    "in the workflow (self-dependencies and forward "
                    "references are both rejected)"
                ),
            },
        ) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return {"success": True, "data": row}


@router.post("/{project_id}/workflow/nodes")
async def add_workflow_node(
    project_id: str,
    payload: NodeCreate,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_write_access),
):
    """Add a node to a live instance — from the node bank (source_stage_id) or
    blank (name). ARRANGEMENT (Task 7, spec §5): manager only — tightened
    from the prior manager/editor gate, no episode-owner carve-out (an
    episode owner configures their episode's node CONTENT, not the
    workflow's STRUCTURE)."""
    from app.services.workflow.node_authz import require_arrangement_role
    from app.services.workflow.node_mutations import add_project_node

    await require_arrangement_role(project_id, auth.user_id)

    try:
        node = await add_project_node(
            project_id,
            source_stage_id=payload.source_stage_id,
            name=payload.name,
            sort_order=payload.sort_order,
            parallel_group=payload.parallel_group,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"success": True, "data": node}


@router.delete("/{project_id}/workflow/nodes/{node_id}")
async def delete_workflow_node(
    project_id: str,
    node_id: str,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_write_access),
):
    """Remove a node from a live instance. Guarded: only a still-pending node
    with no mirror issue that is not part of the active group is removable —
    otherwise 409 with a machine reason (skip ≠ delete). ARRANGEMENT (Task 7,
    spec §5): manager only — tightened from the prior manager/editor gate."""
    from app.services.workflow.node_authz import require_arrangement_role
    from app.services.workflow.node_mutations import delete_project_node

    await require_arrangement_role(project_id, auth.user_id)

    try:
        await delete_project_node(project_id, node_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Node not found") from exc
    except NodeDeleteBlocked as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from exc
    return {"success": True, "data": {"deleted": True}}


@router.post("/{project_id}/workflow/nodes/{node_id}/start-early")
async def start_workflow_node_early(
    project_id: str,
    node_id: str,
    auth: AuthDep,
    episode_id: str = Query(
        ...,
        description="Scope the dependency check to a single episode (B2).",
    ),
    _project_guard: None = Depends(verify_project_write_access),
):
    """Manual "Start early" (M4 Autopilot, task O2 brief): begin work on a
    node ahead of the normal cursor flow, once its dependencies are actually
    satisfied — the hand-operated twin of the autopilot engine's own
    auto_start step (``app.workflows.autopilot._auto_start_pass``), sharing
    the exact same dependency predicate (``_unmet_dependency_names``, no
    co-arrival/closing-group exemptions — this is a standalone future node,
    not a group's forward advance) and the same ``node_start.start_node_now``
    execution path.

    404 (node not found) is raised BEFORE 403 (insufficient role) — the
    project-level guard above already 404s a missing PROJECT; this handles
    the missing NODE. manager/editor only (``WRITE_ROLES``, the workflow
    single-source — mirrors ``patch_workflow_node`` / ``post_advance``).

    An agent-owned node is NEVER auto-dispatched from here (``dispatch=False``
    always) — it still goes through the M3 confirm gate, same as any other
    manually-surfaced "Agent run ready" prompt; only autopilot's own quota-
    cleared auto-start path actually dispatches.
    """
    from app.core.workflow_roles import WRITE_ROLES, resolve_effective_role
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )
    from app.services.workflow.advance_service import _unmet_dependency_names
    from app.services.workflow.node_start import _open_mirror_issue, start_node_now

    nodes_repo = get_project_stage_nodes_repository()
    node = await nodes_repo.get_node(node_id, project_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")

    role = await resolve_effective_role(auth.user_id, project_id=project_id)
    if role not in WRITE_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient role")

    if node.get("skipped") or node.get("status") != "pending":
        raise HTTPException(
            status_code=422,
            detail={
                "code": "NODE_NOT_PENDING",
                "message": "Only a not-yet-started, non-skipped node can be started early",
            },
        )

    # Review fix I4 (adjacent minor): a cancelled mirror issue projects the
    # node BACK to status='pending' (issue_repository._ISSUE_TO_NODE_STATUS
    # has no node-level "cancelled" state), which is indistinguishable from a
    # genuinely-fresh node by the check above alone — without this, start-
    # early would return a fake 200 (start_node_now's own I4 guard silently
    # no-ops on a cancelled mirror, so nothing actually happens but the
    # caller can't tell). Surface it as a distinct, actionable 422 instead.
    existing_issue = await _open_mirror_issue(project_id, node_id)
    if existing_issue is not None and existing_issue.get("status") == "cancelled":
        raise HTTPException(
            status_code=422,
            detail={
                "code": "NODE_CANCELLED",
                "message": (
                    "This node's task was cancelled — restore it in the "
                    "Todolist before starting it early"
                ),
            },
        )

    # B2 T3 (+ review Important fix): the dependency map must be scoped so it
    # sees this node's OWN episode's dependencies — never a sibling episode's.
    #
    # The security hole this closes: ``episode_id`` is a client-supplied query
    # param. A write-role user passing an episode_id that does NOT match the
    # node's real episode would make ``list_nodes_by_episode`` return a node set
    # that OMITS this node's true same-episode dependencies; ``_unmet_dependency_names``
    # then treats every absent dep as "satisfied-by-absence" → DEPS_PENDING is
    # bypassed and an unmet node starts anyway. So we never trust the query
    # episode to scope the map: it must (a) belong to this project and (b)
    # equal the node's own ``episode_id`` — otherwise 404/422. A legacy node
    # with no episode (``node_episode_id is None``) always fails (b) against
    # a non-null query episode_id, so it 422s defensively rather than silently
    # falling back to a project-wide read. The map is then always drawn from
    # the node's TRUE episode (``list_nodes_by_episode``).
    node_episode_id = node.get("episode_id")
    await _require_project_episode(project_id, episode_id)
    if str(episode_id) != str(node_episode_id):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "EPISODE_MISMATCH",
                "message": (
                    "episode_id does not match this node's episode — "
                    "dependency checks must run against the node's own episode"
                ),
            },
        )
    nodes = await nodes_repo.list_nodes_by_episode(project_id, node_episode_id)
    node_by_id = {str(n["id"]): n for n in nodes}
    waiting_on = _unmet_dependency_names([node], node_by_id, exempt_ids=set())
    if waiting_on:
        raise HTTPException(
            status_code=422,
            detail={"code": BLOCK_DEPS_PENDING, "waiting_on": waiting_on},
        )

    updated = await start_node_now(
        project_id,
        node,
        actor_user_id=str(auth.user_id),
        dispatch=False,
    )
    return {"success": True, "data": updated}


@router.get("/{project_id}/advance-preview", response_model=AdvancePreview)
async def get_advance_preview(
    project_id: str,
    auth: AuthDep,
    direction: str = Query("forward", pattern="^(forward|back)$"),
    episode_id: str = Query(
        ..., description="Scope the advance cursor to a single episode (B2)."
    ),
    _project_guard: None = Depends(verify_project_read_access),
) -> AdvancePreview:
    """Pure-read ruling on a forward/back move (same predicate as /advance).

    ``episode_id`` (B2 T3): the ruling is computed against that episode's
    node set + its own ``episodes.current_node_id`` cursor (validated to
    belong to this project first)."""
    from app.services.workflow.advance_service import compute_advance_preview

    await _require_project_episode(project_id, episode_id)
    return await compute_advance_preview(
        project_id, auth.user_id, direction, episode_id
    )


@router.post("/{project_id}/advance")
async def post_advance(
    project_id: str,
    auth: AuthDep,
    direction: str = Query("forward", pattern="^(forward|back)$"),
    episode_id: str = Query(
        ..., description="Scope the advance cursor to a single episode (B2)."
    ),
    _project_guard: None = Depends(verify_project_write_access),
):
    """Advance/retreat the workflow cursor. Recomputes the predicate server-side
    (never trusts the client) and 409s with the blocked reason if it no longer
    clears.

    ``episode_id`` (B2 T3): moves that episode's own
    ``episodes.current_node_id`` cursor and leaves the project column / sibling
    episodes untouched."""
    from app.services.workflow.advance_service import execute_advance

    await _require_project_episode(project_id, episode_id)
    preview = await execute_advance(project_id, auth.user_id, direction, episode_id)
    if not preview.will_advance:
        raise HTTPException(
            status_code=409, detail=preview.blocked_reason or "Advance blocked"
        )
    return {"success": True, "data": preview.model_dump()}


# ============================================
# Project entities — Characters/Locations ASSETS view (PR-10a, G13)
# ============================================


@router.get("/{project_id}/entities")
async def get_project_entities(
    project_id: str,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_read_access),
):
    """Project-wide Characters/Locations, derived from every non-deleted
    script's scenes (character cues in content_json + scene location_text).
    Not authored anywhere — same derived-not-synced model the editor rail
    uses, rolled up project-wide with per-entity episode attribution."""
    try:
        svc = ProjectsService()
        data = await svc.get_project_entities(project_id)
        return {"success": True, "data": data}
    except Exception as e:
        logger.error(f"Failed to get entities for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get project entities")


# ============================================
# RETIRED in the same PR as mig 447: the project-local character /
# location / prop CRUD that lived here — GET|POST /{id}/characters,
# PATCH|DELETE /{id}/characters/{cid}, POST /{id}/characters/extract, and
# the /{id}/lib/{entity_type} quintet — read `project_characters` /
# `project_lib_entities`. That PR deleted these handlers; mig 447 itself
# only renamed the two tables. Those rows migrated to `assets` +
# `asset_project_refs` (production run 2026-09-02, reconciled
# all-present) and the workspace pages now read
# GET /{project_id}/assets in assets_router. The endpoints had zero
# frontend callers at deletion; their two repositories and Pydantic
# schemas went with them. The tables themselves were renamed `_legacy_*`
# for one release cycle and DROPped by mig 451 (P6, spec §3.8).
#
# GET /{project_id}/entities ABOVE IS NOT PART OF THAT — it derives the
# cast from script scenes and never touched either table. It is what
# `projectsService.fetchProjectEntities` calls. Do not fold it in here.
# ============================================


# ============================================
# Project renders — shot-generated media listing (PR-10a, G14)
# ============================================


@router.get("/{project_id}/renders")
async def list_project_renders(
    project_id: str,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_read_access),
    episode_id: Optional[str] = Query(None, description="Filter to one episode"),
    cursor: Optional[str] = Query(None, description="Keyset pagination cursor"),
    limit: int = Query(50, ge=1, le=100),
):
    """Project-wide shot renders (images + videos), derived by joining
    generated_media.node_id (= str(shot_id) for shot-origin rows) back
    through script_shots -> script_scenes -> script_projects. Newest-first,
    cursor-paginated the same way as GET /generated-media."""
    try:
        repo = GeneratedMediaRepository()
        data = await repo.list_for_project(
            project_id, episode_id=episode_id, cursor=cursor, limit=limit
        )
        return {"success": True, "data": data}
    except Exception as e:
        logger.error(f"Failed to list renders for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list project renders")


# ============================================
# File endpoints (nested under project)
# ============================================


@router.get("/{project_id}/files")
async def list_files(
    project_id: str,
    auth: AuthDep,
    include_trashed: bool = Query(False, description="Include trashed files"),
    folder_id: Optional[str] = Query(None, description="Filter by folder ID"),
    source_issue_id: Optional[str] = Query(
        None, description="Filter to files filed from this mirror issue"
    ),
    _project_guard: None = Depends(verify_project_read_access),
):
    """List files in a project, optionally filtered by folder or source issue."""
    try:
        svc = ProjectsService()
        files = await svc.get_project_files(
            project_id, include_trashed, folder_id, source_issue_id=source_issue_id
        )
        return {"success": True, "data": files}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to list files for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list files")


@router.post("/{project_id}/files/upload")
async def upload_file(
    project_id: str,
    auth: AuthDep,
    file: UploadFile = File(...),
    notes: Optional[str] = Query(None, description="Optional notes for the file"),
    source_issue_id: Optional[str] = Query(
        None,
        description=(
            "Mirror issue this file is filed from (Deliverables dropzone). Routes "
            "the file into the node's stage folder and back-links it to the issue."
        ),
    ),
    _project_guard: None = Depends(verify_project_write_access),
):
    """
    Upload a file to a project.

    Max file size: 500 MB. Video files will have metadata extracted via ffprobe.

    `_project_guard` enforces that the caller owns the project or is a member
    of its team before the handler runs.
    """
    try:
        if file.size and file.size > MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=413, detail="File too large. Maximum size is 500 MB."
            )

        svc = ProjectsService()
        result = await svc.upload_file(
            project_id=project_id,
            user_id=auth.user_id,
            file=file,
            notes=notes,
            source_issue_id=source_issue_id,
        )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to upload file to project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload file")


@router.post("/{project_id}/files/link-media")
async def link_media(
    project_id: str,
    data: LinkMediaRequest,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_write_access),
):
    """Link an existing media item from the library to this project."""
    try:
        svc = ProjectsService()
        result = await svc.link_media(
            project_id=project_id,
            media_id=data.media_id,
            user_id=auth.user_id,
        )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to link media to project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to link media")


@router.get("/{project_id}/files/{file_id}")
async def get_file_info(
    project_id: str,
    file_id: str,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_read_access),
):
    """Get detailed info for a single file."""
    try:
        svc = ProjectsService()
        file_record = await svc.get_file_info(project_id, file_id)
        return {"success": True, "data": file_record}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to get file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get file info")


@router.get("/{project_id}/files/{file_id}/download")
async def download_file(
    project_id: str,
    file_id: str,
    request: Request,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_read_access),
):
    """Download a project file's current content, forcing a browser download.

    Storage unification: ``project_files.file_path`` may be a legacy
    filesystem-relative path OR an ``sb://`` object-store row (dual-track
    uploads post-epic) — ``serve_stored_file`` is the ONE reader that
    handles both, so this endpoint never special-cases the shape.

    Forces ``Content-Disposition: attachment`` (the nginx direct-serve 302
    that once had to be excluded here was retired 2026-09-07).
    """
    from urllib.parse import quote as urlquote

    from app.services.library.media_serving import serve_stored_file

    try:
        svc = ProjectsService()
        file_record = await svc.get_file_info(project_id, file_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    file_path = file_record.get("file_path")
    if not file_path:
        raise HTTPException(status_code=404, detail="No file available")

    # Mirror Starlette's FileResponse Content-Disposition formatting
    # (filename= for ascii, filename*=utf-8''... otherwise) — same helper
    # used by the version-download endpoint.
    filename = file_record.get("filename") or "download"
    quoted = urlquote(filename)
    if quoted != filename:
        disposition = f"attachment; filename*=utf-8''{quoted}"
    else:
        disposition = f'attachment; filename="{filename}"'

    try:
        return await serve_stored_file(
            file_path,
            mime=file_record.get("mime_type", "application/octet-stream"),
            request=request,
            disposition=disposition,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to serve file {file_id} for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to serve file")


@router.put("/{project_id}/files/{file_id}")
async def update_file(
    project_id: str,
    file_id: str,
    data: ProjectFileUpdate,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_write_access),
):
    """Update file metadata (rename, add notes, trash/restore)."""
    try:
        svc = ProjectsService()
        result = await svc.update_file(
            project_id, file_id, data.model_dump(exclude_none=True)
        )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to update file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update file")


@router.put("/{project_id}/files/{file_id}/restore")
async def restore_file(
    project_id: str,
    file_id: str,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_write_access),
):
    """Restore a trashed file."""
    try:
        svc = ProjectsService()
        result = await svc.restore_file(project_id, file_id)
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to restore file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to restore file")


@router.get("/{project_id}/shares")
async def list_project_shares(
    project_id: str,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_read_access),
):
    """List all shares for files in this project."""
    try:
        svc = ProjectsService()
        shares = await svc.list_shares(project_id)
        return {"success": True, "data": shares}
    except Exception as e:
        logger.error(f"Failed to list shares for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list project shares")


@router.post("/{project_id}/shares")
async def create_share(
    project_id: str,
    data: CreateShareRequest,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_write_access),
):
    """Create a share link for a project file."""
    try:
        svc = ProjectsService()
        share = await svc.create_share(
            project_id, data.model_dump(exclude_none=True), auth.user_id
        )
        return {"success": True, "data": share}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create share for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to create share")


# ============================================
# Folder endpoints
# ============================================


@router.get("/{project_id}/folders")
async def list_folders(
    project_id: str,
    auth: AuthDep,
    parent_id: Optional[str] = Query(
        None, description="Parent folder ID, null for root"
    ),
    _project_guard: None = Depends(verify_project_read_access),
):
    """List folders in a project."""
    try:
        svc = ProjectsService()
        folders = await svc.list_folders(project_id, parent_id)
        return {"success": True, "data": folders}
    except Exception as e:
        logger.error(f"Failed to list folders for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list folders")


@router.post("/{project_id}/folders")
async def create_folder(
    project_id: str,
    data: CreateFolderRequest,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_write_access),
):
    """Create a new folder in a project."""
    try:
        svc = ProjectsService()
        folder = await svc.create_folder(
            project_id, data.name, auth.user_id, data.parent_id
        )
        return {"success": True, "data": folder}
    except Exception as e:
        logger.error(f"Failed to create folder in project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to create folder")


@router.put("/{project_id}/folders/{folder_id}")
async def rename_folder(
    project_id: str,
    folder_id: str,
    data: RenameFolderRequest,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_write_access),
):
    """Rename a folder."""
    try:
        svc = ProjectsService()
        folder = await svc.rename_folder(project_id, folder_id, data.name)
        return {"success": True, "data": folder}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to rename folder {folder_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to rename folder")


@router.delete("/{project_id}/folders/{folder_id}")
async def delete_folder(
    project_id: str,
    folder_id: str,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_write_access),
):
    """Delete a folder (files inside are moved to parent)."""
    try:
        svc = ProjectsService()
        await svc.delete_folder(project_id, folder_id)
        return {"success": True, "message": "Folder deleted"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to delete folder {folder_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete folder")


@router.put("/{project_id}/files/{file_id}/move")
async def move_file(
    project_id: str,
    file_id: str,
    data: MoveFileRequest,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_write_access),
):
    """Move a file to a different folder."""
    try:
        svc = ProjectsService()
        result = await svc.move_file(project_id, file_id, data.folder_id)
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to move file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to move file")


@router.delete("/{project_id}/files/{file_id}")
async def delete_file(
    project_id: str,
    file_id: str,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_write_access),
):
    """Permanently delete a file record."""
    try:
        svc = ProjectsService()
        await svc.delete_file(project_id, file_id)
        return {"success": True, "message": "File deleted"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to delete file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete file")


# ============================================
# Version endpoints
# ============================================


@router.get("/{project_id}/files/{file_id}/versions")
async def list_versions(
    project_id: str,
    file_id: str,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_read_access),
):
    """List all versions of a file."""
    try:
        svc = ProjectsService()
        versions = await svc.get_file_versions(project_id, file_id)
        return {"success": True, "data": versions}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to list versions for file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list versions")


@router.post("/{project_id}/files/{file_id}/versions")
async def upload_version(
    project_id: str,
    file_id: str,
    auth: AuthDep,
    file: UploadFile = File(...),
    notes: Optional[str] = Query(None, description="Optional notes for this version"),
    _project_guard: None = Depends(verify_project_write_access),
):
    """Upload a new version of a file.

    `_project_guard` enforces project ownership / team membership before run.
    """
    try:
        if file.size and file.size > MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=413, detail="File too large. Maximum size is 500 MB."
            )
        svc = ProjectsService()
        result = await svc.upload_new_version(
            project_id=project_id,
            file_id=file_id,
            user_id=auth.user_id,
            file=file,
            notes=notes,
        )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to upload version for file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload version")


# ============================================
# Comment endpoints
# ============================================


@router.get("/{project_id}/files/{file_id}/comments")
async def list_comments(
    project_id: str,
    file_id: str,
    auth: AuthDep,
    version_id: Optional[str] = Query(None, description="Filter by version ID"),
    _project_guard: None = Depends(verify_project_read_access),
):
    """List comments on a file."""
    try:
        svc = ProjectsService()
        comments = await svc.get_file_comments(project_id, file_id, version_id)
        return {"success": True, "data": comments}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to list comments for file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list comments")


@router.post("/{project_id}/files/{file_id}/comments")
async def add_comment(
    project_id: str,
    file_id: str,
    data: CreateCommentRequest,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_write_access),
):
    """Add a comment to a file."""
    try:
        svc = ProjectsService()
        result = await svc.add_comment(
            project_id=project_id,
            file_id=file_id,
            author_id=auth.user_id,
            content=data.content,
            timestamp_seconds=data.timestamp_seconds,
            version_id=data.version_id,
            drawing_data=data.drawing_data,
        )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to add comment to file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to add comment")


@router.delete("/{project_id}/files/{file_id}/comments/{comment_id}")
async def delete_comment(
    project_id: str,
    file_id: str,
    comment_id: str,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_write_access),
):
    """Delete a comment. Only the author can delete their own comment."""
    try:
        svc = ProjectsService()
        await svc.delete_comment(comment_id, auth.user_id)
        return {"success": True, "message": "Comment deleted"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to delete comment {comment_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete comment")


# ============================================
# Review status endpoint
# ============================================


@router.put("/{project_id}/files/{file_id}/review-status")
async def update_review_status(
    project_id: str,
    file_id: str,
    data: ReviewStatusUpdate,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_write_access),
):
    """Update the review status of a file."""
    try:
        svc = ProjectsService()
        result = await svc.update_review_status(
            project_id=project_id,
            file_id=file_id,
            user_id=auth.user_id,
            review_status=data.review_status,
        )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to update review status for file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update review status")


# ============================================
# Project Members
# ============================================


@router.get("/{project_id}/members")
async def list_members(
    project_id: str,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_read_access),
):
    """List all members of a project."""
    try:
        svc = ProjectsService()
        members = await svc.list_members(project_id)
        return {"success": True, "data": members}
    except Exception as e:
        logger.error(f"Failed to list members for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list members")


@router.post("/{project_id}/members")
async def add_member(
    project_id: str,
    data: AddMemberRequest,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_write_access),
):
    """Add a member to a project."""
    try:
        svc = ProjectsService()
        member = await svc.add_member(project_id, data.user_id, data.role, auth.user_id)
        return {"success": True, "data": member}
    except Exception as e:
        error_msg = str(e)
        if "duplicate key" in error_msg or "unique" in error_msg.lower():
            raise HTTPException(
                status_code=409, detail="User is already a member of this project"
            )
        logger.error(f"Failed to add member to project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to add member")


@router.put("/{project_id}/members/{member_id}")
async def update_member_role(
    project_id: str,
    member_id: str,
    data: UpdateMemberRoleRequest,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_write_access),
):
    """Update a member's role."""
    try:
        svc = ProjectsService()
        member = await svc.update_member_role(project_id, member_id, data.role)
        return {"success": True, "data": member}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to update member {member_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update member role")


@router.delete("/{project_id}/members/{member_id}")
async def remove_member(
    project_id: str,
    member_id: str,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_write_access),
):
    """Remove a member from a project."""
    try:
        svc = ProjectsService()
        await svc.remove_member(project_id, member_id)
        return {"success": True, "message": "Member removed"}
    except Exception as e:
        logger.error(f"Failed to remove member {member_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove member")


# ============================================
# Collection endpoints
# ============================================


@router.get("/{project_id}/collections")
async def list_collections(
    project_id: str,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_read_access),
):
    """List all collection links for a project."""
    try:
        svc = ProjectsService()
        collections = await svc.list_collections(project_id)
        return {"success": True, "data": collections}
    except Exception as e:
        logger.error(f"Failed to list collections for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list collections")


@router.post("/{project_id}/collections")
async def create_collection(
    project_id: str,
    data: CreateCollectionRequest,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_write_access),
):
    """Create a collection link for external file uploads."""
    try:
        svc = ProjectsService()
        collection = await svc.create_collection(
            project_id, data.model_dump(exclude_none=True), auth.user_id
        )
        return {"success": True, "data": collection}
    except Exception as e:
        logger.error(f"Failed to create collection for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to create collection")


@router.delete("/{project_id}/collections/{collection_id}")
async def delete_collection(
    project_id: str,
    collection_id: str,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_write_access),
):
    """Delete a collection link."""
    try:
        svc = ProjectsService()
        await svc.delete_collection(project_id, collection_id)
        return {"success": True, "message": "Collection deleted"}
    except Exception as e:
        logger.error(f"Failed to delete collection {collection_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete collection")
