"""Ideation topic-pool CRUD (M1.5).

Team-level ideation topics (mig 382) — the global "before you make a project"
pool. Mounted at ``/ideation/topics`` (NOT ``/topics``: that prefix is already
owned by the hotspots/signal-feed router, so the ideation surface takes a
disjoint prefix; the spec's ``/topics`` path is superseded here).

Authorization is the workflow single-source ``resolve_effective_role``: for a
team-scoped surface a team member resolves to manager/editor (both may write); a
non-member resolves to None → 403 on the team routes, 404 on the id routes (so
topic existence never leaks across teams). 404 precedes 403.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.row_guard import NOT_FOUND_OR_OUT_OF_SCOPE
from app.core.deps import AuthDep
from app.core.workflow_roles import WRITE_ROLES, resolve_effective_role
from app.repositories.topics_repository import get_topics_repository
from app.schemas.ideation import (
    IdeationTopicDeleteResponse,
    IdeationTopicListResponse,
    IdeationTopicResponse,
    TopicCreate,
    TopicUpdate,
)
from app.services.modules.gate import require_module

router = APIRouter(
    prefix="/ideation/topics",
    tags=["Ideation"],
    dependencies=[Depends(require_module("ideation"))],
)


# ── topic collection ─────────────────────────────────────────────────────────


def _topic_not_found() -> HTTPException:
    """Missing, out of the caller's teams, or a junk id: all the same typed 404."""
    return HTTPException(
        status_code=404,
        detail={"code": NOT_FOUND_OR_OUT_OF_SCOPE, "message": "Topic not found"},
    )


def _is_snowflake(value: str) -> bool:
    """A non-numeric id used to reach ``int()`` in the repository: a 500."""
    try:
        int(str(value))
    except ValueError:
        return False
    return True


async def _team_role(team_id: str, user_id: str) -> Optional[str]:
    """The caller's role in ``team_id``; a junk id is "not a member" (403)."""
    if not _is_snowflake(team_id):
        return None
    return await resolve_effective_role(user_id, team_id=team_id)


@router.get("", response_model=IdeationTopicListResponse)
async def list_topics(
    auth: AuthDep,
    team_id: str = Query(..., description="Team scope (snowflake id)"),
    status: Optional[str] = Query(
        None,
        pattern="^(candidate|shortlisted|produced|archived)$",
        description="Optional status filter",
    ),
):
    """List a team's ideation topics, optionally filtered by status."""
    role = await _team_role(team_id, auth.user_id)
    if role is None:
        raise HTTPException(status_code=403, detail="You are not a member of this team")

    repo = get_topics_repository()
    topics = await repo.list_topics(team_id, status=status)
    return {"success": True, "data": topics}


@router.post("", response_model=IdeationTopicResponse)
async def create_topic(
    data: TopicCreate,
    auth: AuthDep,
    team_id: str = Query(..., description="Team scope (snowflake id)"),
):
    """Create a topic (candidate by default)."""
    role = await _team_role(team_id, auth.user_id)
    if role is None:
        raise HTTPException(status_code=403, detail="You are not a member of this team")
    if role not in WRITE_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient role")

    repo = get_topics_repository()
    topic = await repo.create_topic(
        team_id,
        title=data.title,
        created_by=auth.user_id,
        cover_url=data.cover_url,
        excerpt=data.excerpt,
        note_id=data.note_id,
        resource_id=data.resource_id,
        media_id=data.media_id,
        inspiration_topic_id=data.inspiration_topic_id,
    )
    return {"success": True, "data": topic}


# ── single topic ─────────────────────────────────────────────────────────────


async def _resolve_topic_role(topic_id: str, user_id: str):
    """Return (team_id, role) for a topic, raising 404 when the topic is missing
    OR the caller is not a member of its team (no existence leak)."""
    if not _is_snowflake(topic_id):
        raise _topic_not_found()
    repo = get_topics_repository()
    team_id = await repo.get_topic_team_id(topic_id)
    if team_id is None:
        raise _topic_not_found()
    role = await resolve_effective_role(user_id, team_id=team_id)
    if role is None:
        raise _topic_not_found()
    return team_id, role


@router.patch("/{topic_id}", response_model=IdeationTopicResponse)
async def update_topic(topic_id: str, data: TopicUpdate, auth: AuthDep):
    team_id, role = await _resolve_topic_role(topic_id, auth.user_id)
    if role not in WRITE_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient role")

    repo = get_topics_repository()
    # exclude_unset so an omitted field means "leave unchanged", not "clear".
    patch = data.model_dump(exclude_unset=True)
    topic = await repo.update_topic(
        topic_id,
        team_id,
        title=patch.get("title"),
        cover_url=patch.get("cover_url"),
        excerpt=patch.get("excerpt"),
        status=patch.get("status"),
    )
    if topic is None:
        raise _topic_not_found()
    return {"success": True, "data": topic}


@router.delete("/{topic_id}", response_model=IdeationTopicDeleteResponse)
async def delete_topic(topic_id: str, auth: AuthDep):
    team_id, role = await _resolve_topic_role(topic_id, auth.user_id)
    if role not in WRITE_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient role")

    repo = get_topics_repository()
    ok = await repo.delete_topic(topic_id, team_id)
    if not ok:
        raise _topic_not_found()
    return {"success": True, "data": {"deleted": True}}
