"""One object's产出血缘 (三期 3a spec §4).

    GET /api/v1/outputs/{kind}/{ref_id}        — the whole version chain
    GET /api/v1/outputs/{kind}/{ref_id}/diff   — two of those versions, as content

**Not registered is a 404 with ``code: not_registered``, never an empty list.**
"Nobody registered this object" and "this object has no versions" are different
answers: the empty list makes the panel draw an empty provenance block, while
the honest answer is that the object is outside the registry entirely — which
is exactly the state a human edit leaves behind, and the state every historical
object is in.

**Visibility is the owning run's.** A deliverable has no owner of its own; its
run does. When that run answers to an issue, the issue's rule decides (own /
assignee / team member, 404 otherwise, so existence never leaks across teams).
When it answers to no issue — a canvas or chat lane run — only the run's own
user may read it. The gate is applied to the NEWEST version's run: that run is
the one that owns the object as it exists now, and every version of a script
object lives inside the same project scope, so an older version can never be
reachable from a team the newest one is not.

Both endpoints ride the ``todolist`` module gate, like ``/issues`` itself —
the panels that consume them are on the issue detail page.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.core.deps import AuthDep
from app.db.session import read_scope
from app.repositories.run_deliverables_repository import (
    get_run_deliverables_repository,
)
from app.schemas.outputs import OutputDiffResponse, OutputLineageResponse
from app.services.deliverables.diff import build_diff
from app.services.deliverables.kinds import ALL_KINDS
from app.services.deliverables.lineage_view import (
    redact_foreign_issue_links,
    version_of,
)
from app.services.issues.issue_visibility import assert_issue_visible
from app.services.modules.gate import require_module

router = APIRouter(
    prefix="/outputs",
    tags=["Outputs"],
    dependencies=[Depends(require_module("todolist"))],
)


def _reject(status_code: int, code: str, message: str) -> HTTPException:
    """Typed refusal. ``detail`` is a DICT on purpose: production wraps every
    ``HTTPException`` in the ``ErrorResponse`` envelope and only a dict detail
    survives, under ``details``. A string detail collapses to ``http_404`` and
    the client loses the code it needs (CLAUDE.md 2026-09-09)."""
    return HTTPException(
        status_code=status_code, detail={"code": code, "message": message}
    )


async def run_owner_user_id(run_id: Any) -> Optional[str]:
    """The user who owns a run, for the no-issue case. ``None`` when the run
    is gone (a deleted run's deliverables cascade away, so this is a race, not
    a state) — the caller turns that into a 404."""
    from app.models.agents import AgentRuns

    async with read_scope() as session:
        owner = (
            await session.execute(
                select(AgentRuns.user_id).where(AgentRuns.id == int(run_id))
            )
        ).scalar_one_or_none()
    return str(owner) if owner is not None else None


async def _visible_chain(kind: str, ref_id: str, auth) -> List[Dict[str, Any]]:
    """The version chain, newest first, once the caller has proved they may
    read it. Every refusal on this path is a 404 — an object the caller cannot
    see must not be distinguishable from one that was never registered."""
    if kind not in ALL_KINDS:
        raise _reject(
            status.HTTP_400_BAD_REQUEST,
            "unknown_kind",
            f"{kind!r} is not a registered deliverable kind",
        )
    rows = await get_run_deliverables_repository().lineage_for(kind=kind, ref_id=ref_id)
    if not rows:
        raise _reject(
            status.HTTP_404_NOT_FOUND,
            "not_registered",
            f"{kind}/{ref_id} is not in the deliverable registry",
        )
    newest = rows[0]
    issue_id = newest.get("issue_id")
    if issue_id is not None:
        await assert_issue_visible(int(issue_id), auth)
    else:
        owner = await run_owner_user_id(newest.get("run_id"))
        if owner is None or owner != str(auth.user_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="not found"
            )
    return rows


@router.get("/{kind}/{ref_id}", response_model=OutputLineageResponse)
async def get_output_lineage(
    kind: str, ref_id: str, auth: AuthDep
) -> OutputLineageResponse:
    """Every version of one object, newest first, each with the run / issue /
    coordinates / model / spend that produced it."""
    rows = await _visible_chain(kind, ref_id, auth)
    # The gate above proved ONE issue visible — the newest version's. Any
    # older version filed under a different issue keeps its coordinates but
    # loses the link built from ITS team (3a Task 8b). The per-issue endpoint
    # needs no equivalent: its rows were selected BY the issue it already
    # checked, so every row there is on the gated issue by construction.
    versions = redact_foreign_issue_links(
        [version_of(row) for row in rows], gated_issue_id=rows[0].get("issue_id")
    )
    return OutputLineageResponse(
        kind=kind,
        ref_id=str(ref_id),
        latest_version=versions[0]["version"],
        versions=versions,
    )


@router.get("/{kind}/{ref_id}/diff", response_model=OutputDiffResponse)
async def get_output_diff(
    kind: str,
    ref_id: str,
    auth: AuthDep,
    from_version: int = Query(alias="from", ge=1),
    to_version: int = Query(alias="to", ge=1),
) -> OutputDiffResponse:
    """Two registered versions of one object, as content.

    A version number that is not in the chain is a 404 ``version_not_found``,
    not an empty pane: the pane would read as "this version was blank"."""
    rows = await _visible_chain(kind, ref_id, auth)
    by_version = {row.get("version"): row for row in rows}
    missing = [v for v in (from_version, to_version) if v not in by_version]
    if missing:
        raise _reject(
            status.HTTP_404_NOT_FOUND,
            "version_not_found",
            f"{kind}/{ref_id} has no version {', '.join(str(v) for v in missing)}",
        )
    body = await build_diff(
        kind=kind,
        ref_id=str(ref_id),
        from_row=by_version[from_version],
        to_row=by_version[to_version],
    )
    return OutputDiffResponse.model_validate(body)


__all__ = ["router"]
