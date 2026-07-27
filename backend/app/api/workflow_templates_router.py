"""Workflow template CRUD + node-bank endpoint (M1 PR-A).

Team-level workflow templates (mig 380) and the read-only node bank (mig 381).
Mounted at ``/workflows`` alongside the DBOS ``workflows_router`` — their paths
must stay disjoint. The DBOS run list sits at ``/workflows/runs`` because a
bare ``GET ""`` there shadowed this router's template list until 2026-07-20
(FastAPI keeps the first-registered route and silently drops the rest);
``tests/test_route_uniqueness.py`` now fails on any such collision.
``stage-library`` is declared before ``{id}`` so the literal wins.

Authorization is the workflow single-source ``resolve_effective_role``: for a
team-scoped surface a team member resolves to manager/editor (both may write);
a non-member resolves to None → 403 on the team routes, 404 on the id routes
(so template existence never leaks across teams). 404 precedes 403.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.core.deps import AuthDep
from app.core.workflow_roles import WRITE_ROLES, resolve_effective_role
from app.repositories.workflow_templates_repository import (
    get_workflow_templates_repository,
)
from app.schemas.workflow import (
    MAX_TEMPLATES_PER_TEAM,
    TemplateCreate,
    TemplateUpdate,
)
from app.services.workflow.template_seeder import ensure_seed_templates

router = APIRouter(prefix="/workflows", tags=["Workflow Templates"])


def _node_to_dict(node) -> dict:
    """Flatten a validated TemplateNodeIn into the repo's node payload."""
    return {
        "name": node.name,
        "sort_order": node.sort_order,
        "parallel_group": node.parallel_group,
        "default_owner_user_id": node.default_owner_user_id,
        "default_owner_agent_id": node.default_owner_agent_id,
        "skip_default": node.skip_default,
        "review_required": node.review_required,
        "deliverable_required": node.deliverable_required,
        "deliverable_label": node.deliverable_label,
        "source_stage_id": node.source_stage_id,
        "duration_days": node.duration_days,
        "completion_policy": node.completion_policy,
        "events": node.events.model_dump(),
        "members": [
            {"user_id": m.user_id, "agent_id": m.agent_id} for m in node.members
        ],
        # mig 390 (M3 PR-I): key generation (slugify + dedupe) happens in the
        # repo write path, not here — this only flattens the validated
        # FormFieldDef list into plain dicts.
        "form_schema": [f.model_dump() for f in node.form_schema],
    }


# ── node bank (must precede /{template_id}) ─────────────────────────────────


@router.get("/stage-library")
async def list_stage_library(auth: AuthDep):
    """The 11-node workflow library (any authenticated user)."""
    repo = get_workflow_templates_repository()
    return {"success": True, "data": await repo.list_stage_library()}


# ── template collection ─────────────────────────────────────────────────────


@router.get("")
async def list_templates(
    auth: AuthDep,
    team_id: str = Query(..., description="Team scope (snowflake id)"),
):
    """List a team's templates, seeding the two built-ins on first access."""
    role = await resolve_effective_role(auth.user_id, team_id=team_id)
    if role is None:
        raise HTTPException(status_code=403, detail="You are not a member of this team")

    repo = get_workflow_templates_repository()
    templates = await ensure_seed_templates(team_id, repo=repo, created_by=auth.user_id)
    return {"success": True, "data": templates}


@router.post("")
async def create_template(
    data: TemplateCreate,
    auth: AuthDep,
    team_id: str = Query(..., description="Team scope (snowflake id)"),
):
    """Create an empty named template (nodes are set via PATCH)."""
    role = await resolve_effective_role(auth.user_id, team_id=team_id)
    if role is None:
        raise HTTPException(status_code=403, detail="You are not a member of this team")
    if role not in WRITE_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient role")

    repo = get_workflow_templates_repository()
    if await repo.count_templates(team_id) >= MAX_TEMPLATES_PER_TEAM:
        raise HTTPException(
            status_code=422,
            detail=f"a team may hold at most {MAX_TEMPLATES_PER_TEAM} templates",
        )
    tpl = await repo.create_template(team_id, data.name, auth.user_id)
    return {"success": True, "data": tpl}


# ── single template ─────────────────────────────────────────────────────────


async def _resolve_template_role(template_id: str, user_id: str):
    """Return (team_id, role) for a template, raising 404 when the template is
    missing OR the caller is not a member of its team (no existence leak)."""
    repo = get_workflow_templates_repository()
    team_id = await repo.get_template_team_id(template_id)
    if team_id is None:
        raise HTTPException(status_code=404, detail="Template not found")
    role = await resolve_effective_role(user_id, team_id=team_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return team_id, role


@router.get("/{template_id}")
async def get_template(template_id: str, auth: AuthDep):
    team_id, _role = await _resolve_template_role(template_id, auth.user_id)
    repo = get_workflow_templates_repository()
    tpl = await repo.get_template(template_id, team_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"success": True, "data": tpl}


@router.patch("/{template_id}")
async def update_template(template_id: str, data: TemplateUpdate, auth: AuthDep):
    team_id, role = await _resolve_template_role(template_id, auth.user_id)
    if role not in WRITE_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient role")

    repo = get_workflow_templates_repository()
    nodes = None if data.nodes is None else [_node_to_dict(n) for n in data.nodes]
    tpl = await repo.update_template(
        template_id,
        team_id,
        name=data.name,
        is_default=data.is_default,
        nodes=nodes,
    )
    if tpl is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"success": True, "data": tpl}


@router.delete("/{template_id}")
async def delete_template(template_id: str, auth: AuthDep):
    team_id, role = await _resolve_template_role(template_id, auth.user_id)
    if role not in WRITE_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient role")

    repo = get_workflow_templates_repository()
    ok = await repo.delete_template(template_id, team_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"success": True, "data": {"deleted": True}}
