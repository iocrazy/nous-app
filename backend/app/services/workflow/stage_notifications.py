"""Stage arrival/completion/reopen → inbox notification fan-out (M2 PR-E E2).

A node's ``events`` JSONB (mig 386) gates two of the three wiring points that
call ``notify_stage_event``:

  * ``arrival``   — a group just became the active cursor (forward move, or a
    project's first group at creation). Gated by ``notify_on_arrival``
    (default ``True``).
  * ``completion`` — a group just closed on a forward move. Gated by
    ``notify_on_complete`` (default ``False`` — most stages don't want a ping
    on close, only on start).
  * ``reopen``    — a retreat (back) move reopened a previously-closed group.
    Gated by the SAME ``notify_on_arrival`` flag as ``arrival`` (a reopen is,
    from the recipient's point of view, "this stage is active again").

Recipients are the node's owner (user, never agent — an agent owner has no
inbox) plus its user members, deduped, with the acting user excluded (nobody
needs to be told about their own action). An empty recipient set (e.g. an
agent-owned node with no user members) is a silent no-op — never a fallback
ping to some other party.

Best-effort, matching ``notify()`` itself (``services/notifications.py:65``):
the whole function is wrapped in ``try/except Exception: logger.warning`` so a
notification hiccup can NEVER propagate into the advance/reopen/create flow
that triggered it.
"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from loguru import logger

from app.services.notifications import notify

_VERB = {
    "arrival": "started",
    "completion": "completed",
    "reopen": "reopened",
}


async def notify_stage_event(
    *,
    event: Literal["arrival", "completion", "reopen"],
    project_id: str,
    project_name: str,
    node: Dict[str, Any],
    issue_identifier: Optional[str],
    team_id: Optional[int],
    actor_user_id: Optional[str],
) -> None:
    """Best-effort inbox notification for one node's arrival/completion/reopen.

    NEVER raises — any failure (bad node shape, ``notify()`` itself somehow
    misbehaving, etc.) is logged and swallowed so the caller's primary
    operation (advance / reopen / project creation) is never affected.
    """
    try:
        events = node.get("events") or {}
        if event in ("arrival", "reopen"):
            if not events.get("notify_on_arrival", True):
                return
        else:  # completion
            if not events.get("notify_on_complete", False):
                return

        recipients: set[str] = set()
        owner_user_id = node.get("owner_user_id")
        if owner_user_id:
            recipients.add(str(owner_user_id))
        for member in node.get("members") or []:
            member_user_id = member.get("user_id")
            if member_user_id:
                recipients.add(str(member_user_id))

        if actor_user_id is not None:
            recipients.discard(str(actor_user_id))

        if not recipients:
            return

        node_name = node.get("name") or "Stage"
        title = f'Stage "{node_name}" {_VERB[event]} — {project_name}'

        for user_id in recipients:
            await notify(
                user_id,
                "workflow_stage",
                title,
                body=None,
                link_kind="issue" if issue_identifier else None,
                link_id=issue_identifier,
                team_id=team_id,
            )
    except Exception as exc:  # noqa: BLE001 — best-effort: never break the caller
        logger.warning(
            f"[stage_notifications] notify_stage_event failed — event={event} "
            f"project={project_id} node={node.get('id')}: {exc!r}"
        )
