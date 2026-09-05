"""``GET /issues/{id}/progress`` → ``issue.rollup`` (spec §1-② / §5).

Read-only, computed at request time from the runs (never from
``execution_state`` alone), same visibility and module gate as the issues
router. Kept out of ``issues_router`` so that file stops growing.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core.deps import AuthDep
from app.services.issues.issue_rollup import load_rollup
from app.services.issues.issue_visibility import assert_issue_visible
from app.services.modules.gate import require_module

router = APIRouter(
    prefix="/issues",
    tags=["Issues"],
    dependencies=[Depends(require_module("todolist"))],
)


@router.get("/{issue_id}/progress")
async def issue_progress(issue_id: int, auth: AuthDep) -> dict[str, Any]:
    issue = await assert_issue_visible(issue_id, auth)
    return await load_rollup(issue)


__all__ = ["router"]
