"""Script Versions Router — commit tags + diff + rollback (Phase B P4).

Endpoints:
  POST   /scripts/{script_id}/commits                        — verify_script_access
  GET    /scripts/{script_id}/commits                        — verify_script_read_access
  GET    /scripts/{script_id}/commits/{commit_id}/diff       — verify_script_read_access
  POST   /scripts/{script_id}/commits/{commit_id}/rollback   — verify_script_access
  DELETE /commits/{commit_id}                                — verify_commit_access

The two GET routes use the *_read_access variant (team membership OR an
explicit project_members row on the parent project — 2026-08-12 fix); the
write routes keep the team-only gate unchanged.

Version control rides the append-only ``script_ops`` ledger (spec v3 §5-1): a
commit is a manual tag (per-scene op_seq watermark + scene-set snapshot); diff
replays the ledger between two watermarks; rollback replays the inverse batch
through the existing If-Match protocol, so it is itself a new forward op.

The script-scoped routes take ``verify_script_access`` from the path ``script_id``.
Routes that also carry ``commit_id`` re-check the commit belongs to that script
(404 on mismatch) so a commit id from another script can't be diffed / rolled
back through a script the caller happens to own. Rollback is SYNCHRONOUS (per
scene apply is sub-second; no workflow) and returns a per-scene result list so a
partial failure is surfaced, not hidden.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger

from app.core.deps import AuthDep
from app.core.scope_guards import (
    verify_commit_access,
    verify_script_access,
    verify_script_read_access,
)
from app.repositories.script_commit_repository import get_script_commit_repository
from app.schemas.script import CommitCreate
from app.services.script.version_service import get_version_service

router = APIRouter()


async def _commit_of_script(commit_id: str, script_id: str) -> Dict[str, Any]:
    """Load a commit and assert it belongs to ``script_id`` (404 otherwise).

    Comparison is str-coerced on both sides (#1006: the row's ``script_id`` is a
    native int, the path param is a str — a bare ``!=`` would be always-true and
    silently 404 every valid commit)."""
    commit = await get_script_commit_repository().get(commit_id)
    if not commit or str(commit.get("script_id")) != str(script_id):
        raise HTTPException(status_code=404, detail="Commit not found")
    return commit


@router.post("/scripts/{script_id}/commits")
async def create_commit(
    script_id: str,
    auth: AuthDep,
    body: CommitCreate,
    _guard: None = Depends(verify_script_access),
) -> Dict[str, Any]:
    """Tag the script's current state (snapshot watermarks + scene set)."""
    try:
        commit = await get_version_service().create_commit(
            script_id, body.message, auth.user_id
        )
        return {"success": True, "data": commit}
    except Exception as exc:
        logger.error(f"[Versions] create commit for script {script_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to create commit")


@router.get("/scripts/{script_id}/commits")
async def list_commits(
    script_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_script_read_access),
) -> Dict[str, Any]:
    """List a script's commits, newest first."""
    try:
        commits = await get_script_commit_repository().list_by_script(script_id)
        return {"success": True, "data": commits}
    except Exception as exc:
        logger.error(f"[Versions] list commits for script {script_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to list commits")


@router.get("/scripts/{script_id}/commits/{commit_id}/diff")
async def diff_commit(
    script_id: str,
    commit_id: str,
    auth: AuthDep,
    against: Optional[str] = Query(
        None, description="commit id to diff against, or 'current' / omitted for live"
    ),
    _guard: None = Depends(verify_script_read_access),
) -> Dict[str, Any]:
    """Diff ``commit_id`` → ``against`` (``against`` = another commit id, or
    'current'/omitted for the live state). Reports element-level scene changes
    plus scene-set adds/removes."""
    try:
        commit_a = await _commit_of_script(commit_id, script_id)
        commit_b: Optional[Dict[str, Any]] = None
        if against and against != "current":
            commit_b = await _commit_of_script(against, script_id)
        diff = await get_version_service().compute_diff(script_id, commit_a, commit_b)
        return {"success": True, "data": diff}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[Versions] diff commit {commit_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to diff commit")


@router.post("/scripts/{script_id}/commits/{commit_id}/rollback")
async def rollback_commit(
    script_id: str,
    commit_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_script_access),
) -> Dict[str, Any]:
    """Roll every scene back to its watermark in ``commit_id`` (synchronous).

    A partial failure (some scenes roll back, some don't) is NOT an error: the
    response carries every scene's per-scene status so the editor can retry the
    failed ones. ``success`` is False only when at least one scene failed."""
    try:
        commit_a = await _commit_of_script(commit_id, script_id)
        result = await get_version_service().rollback_to(
            script_id, commit_a["id"], auth.user_id
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Commit not found")
        return {"success": not result["partial_failure"], "data": result}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[Versions] rollback commit {commit_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to roll back commit")


@router.delete("/commits/{commit_id}")
async def delete_commit(
    commit_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_commit_access),
) -> Dict[str, Any]:
    """Delete a commit tag. The ops it referenced live on in script_ops (spec
    §5-1: tags are droppable, history is not)."""
    try:
        await get_script_commit_repository().delete(commit_id)
        return {"success": True}
    except Exception as exc:
        logger.error(f"[Versions] delete commit {commit_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to delete commit")
