"""Issue-side deliverable uploads (M2-W1).

A file uploaded from a mirror issue's Deliverables dropzone is filed into the
owning project's files — routed into the node's stage folder when the issue is a
workflow-node mirror, or the project root otherwise (spec §2). Two seams:

  * ``resolve_deliverable_folder`` turns a ``source_issue_id`` into the folder a
    file should land in (materializing the node's stage folder if needed), or
    ``None`` for a non-mirror issue (root upload).
  * ``record_deliverable_filed`` drops a "filed <filename>" line on the issue
    timeline. It is emitted as an authored ``comment`` (not ``system_status``):
    the ``issue_messages`` CHECK ties ``system_status`` to a status transition,
    which an upload has none of — so a status-less system row is impossible
    without fabricating a transition. The comment carries a
    ``meta.deliverable_upload`` marker so the timeline renders it as a system-
    style line rather than a chat bubble.

Both are best-effort: a hiccup degrades to a root upload / no timeline line and
never blocks the upload itself.
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from app.services.library.project_stage_issues import (
    ORIGIN_KIND,
    parse_stage_origin_id,
)


async def resolve_deliverable_folder(
    project_id: str, source_issue_id: str, user_id: Optional[str]
) -> Optional[str]:
    """Folder a file filed from ``source_issue_id`` should land in.

    Returns the node's stage folder id when the issue is a workflow-node mirror
    for THIS project (materializing the folder if absent), else ``None`` (the
    file lands in the project root). Best-effort: any error → ``None``.
    """
    from app.repositories.issue_repository import get_issue_repository
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )
    from app.services.workflow.node_folders import ensure_node_folder

    try:
        issue = await get_issue_repository().get_by_id(int(str(source_issue_id)))
    except Exception as exc:  # noqa: BLE001 — unresolved issue → root upload
        logger.warning(
            f"[deliverable_uploads] issue lookup failed for {source_issue_id}: {exc!r}"
        )
        return None
    if not issue or issue.get("origin_kind") != ORIGIN_KIND:
        return None

    origin_pid, node_id = parse_stage_origin_id(str(issue.get("origin_id") or ""))
    # Only a new-format origin that names THIS project resolves to a node folder.
    if origin_pid is None or str(origin_pid) != str(project_id) or not node_id:
        return None

    node = await get_project_stage_nodes_repository().get_node(node_id, str(project_id))
    if node is None:
        return None
    return await ensure_node_folder(str(project_id), node, user_id)


async def record_deliverable_filed(
    issue_id: str,
    filename: str,
    file_id: str,
    *,
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> None:
    """Drop a "filed <filename>" line on the issue timeline (best-effort).

    Emitted as an authored comment with a ``deliverable_upload`` meta marker (see
    module docstring for why not ``system_status``). Never raises."""
    from sqlalchemy import insert

    from app.db.session import write_scope
    from app.models import IssueMessages

    row: dict[str, Any] = {
        "issue_id": int(str(issue_id)),
        "kind": "comment",
        "body": f"Filed deliverable: {filename}",
        "meta": {
            "deliverable_upload": True,
            "file_id": str(file_id),
            "filename": filename,
        },
    }
    # The comment CHECK needs an author (user XOR agent). Uploads are user-driven
    # today; an agent uploader stamps the agent instead so the row reads "Agent".
    if agent_id:
        row["author_agent_id"] = str(agent_id)
    elif user_id:
        row["author_user_id"] = str(user_id)
    else:
        # No author → the comment CHECK would reject it; skip the timeline line.
        return
    try:
        async with write_scope() as session:
            await session.execute(insert(IssueMessages).values(row))
    except Exception as exc:  # noqa: BLE001 — timeline line is enrichment
        logger.warning(
            f"[deliverable_uploads] timeline line failed for issue {issue_id}: {exc!r}"
        )
