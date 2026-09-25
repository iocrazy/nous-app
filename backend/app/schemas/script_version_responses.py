"""Response shapes of the script version routes (commit tags, diff, rollback).

These declare what the routers already sent; nothing here changes the wire.

- Commit rows come out of ``script_commit_repository._row`` (strategy-C
  parity): bigint ``id`` / ``script_id`` stay JSON **numbers**, ``created_at``
  is already an ISO string (declared ``str``).
- Scene ids inside commits, diffs and rollbacks are strings (JSONB object keys
  and ``_scene_snapshot`` both stringify them).
- Optional keys that a branch sometimes omits (``error_code`` / ``error`` on a
  rollback result) are declared with a default and the route sets
  ``response_model_exclude_unset``, so absent stays absent.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


class ScriptCommitSceneSnapshot(BaseModel):
    """One entry of a commit's ``scene_ids`` snapshot
    (``version_service._scene_snapshot``)."""

    id: str
    sort_order: Optional[int]
    heading_int_ext: Optional[str]
    location_text: Optional[str]


class ScriptCommitRow(BaseModel):
    """One ``script_commits`` row (``SELECT *`` shape): ``POST .../commits``."""

    id: int
    script_id: int
    message: str
    # {scene_id: op_seq} — the per-scene ledger high-water at commit time.
    watermarks: Dict[str, int]
    scene_ids: List[ScriptCommitSceneSnapshot]
    created_by: str
    created_at: str


class ScriptCommitListItem(ScriptCommitRow):
    """``GET .../commits``: the row plus the author's display name (``None``
    when ``created_by`` has no profile / a blank username)."""

    author_name: Optional[str]


class ScriptCommitSceneChange(ScriptCommitSceneSnapshot):
    """A scene added or removed between the two diff sides, with the actor who
    last worked on it up to that side's watermark."""

    author: Optional[str]


class ScriptCommitElementChange(BaseModel):
    """One element-level change inside a scene diff, aligned by element id.
    ``before`` / ``after`` are the raw element dicts (``None`` on the side the
    element is missing from)."""

    kind: Literal["added", "removed", "changed", "moved"]
    id: str
    before: Optional[Dict[str, Any]]
    after: Optional[Dict[str, Any]]
    actor: Optional[str]


class ScriptCommitSceneDiff(BaseModel):
    """The element changes of one scene present on both diff sides."""

    scene_id: str
    elements: List[ScriptCommitElementChange]
    author: Optional[str]


class ScriptCommitDiff(BaseModel):
    """``GET .../commits/{id}/diff``. ``authors`` maps actor uuids to display
    names (``copilot`` and unresolved ids are absent)."""

    scenes: List[ScriptCommitSceneDiff]
    scenes_added: List[ScriptCommitSceneChange]
    scenes_removed: List[ScriptCommitSceneChange]
    authors: Dict[str, str]


class ScriptCommitRollbackSceneResult(BaseModel):
    """One scene's rollback outcome. ``error_code`` / ``error`` are only sent
    on ``failed``."""

    scene_id: str
    status: Literal["unchanged", "rolled_back", "failed"]
    error_code: Optional[Literal["conflict", "error"]] = None
    error: Optional[str] = None


class ScriptCommitRollback(BaseModel):
    """``POST .../commits/{id}/rollback``. A partial failure is still a 200:
    the envelope's ``success`` is false and ``results`` lists every scene."""

    commit_id: str
    results: List[ScriptCommitRollbackSceneResult]
    # Scenes created after the commit (reported, not deleted).
    not_deleted: List[str]
    # Scenes that existed at commit time but are gone now (not restored).
    not_resurrected: List[str]
    partial_failure: bool
