"""Resolve attachments whose ``kind == 'resource_ref'`` into metadata-only
references the prompt composer will render in ``<available_resources>``.

Does NOT load resource content (that happens lazily via the ResourceFetch
tool when the agent calls it). Re-validates user accessibility server-side;
the frontend ``scope`` field is treated as a hint, never trusted.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.services.ai._mime_kind import kind_from_mime


async def _fetch_accessible_meta(
    user_id: str, resource_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """Return dict of {id: meta} for those the user can read. Missing ids = not accessible.

    Phase A raw-SQL-to-ORM migration (docs/decisions/2026-08-04-raw-sql-to-
    orm-full-migration.md). Original SQL (kept for reference — same JOINs/
    predicates/columns):
      SELECT r.id::text AS id, r.filename AS name,
             r.mime_type AS mime, r.file_size_bytes AS size,
             r.notes AS brief, r.updated_at,
             ri.scope_id::text AS scope_id,
             t.name AS team_name, t.kind AS scope_kind
        FROM public.resources r
        JOIN public.resource_items ri ON ri.resource_id = r.id
        LEFT JOIN public.teams t ON t.id::text = ri.scope_id::text
       WHERE r.id::text = ANY(:ids) AND r.is_trashed = false
         AND ri.scope_id::text IN (
               SELECT team_id::text FROM public.team_members WHERE user_id = :uid
             )
    """
    from contextlib import nullcontext

    from sqlalchemy import String, cast, select

    from app.db.scope import is_enforced, system_request_scope
    from app.db.session import read_scope
    from app.models import ResourceItems, Resources, TeamMembers, Teams

    if not resource_ids:
        return {}

    id_text = cast(Resources.id, String)
    scope_text = cast(ResourceItems.scope_id, String)
    membership_subq = select(cast(TeamMembers.team_id, String)).where(
        TeamMembers.user_id == user_id
    )

    stmt = (
        select(
            id_text.label("id"),
            Resources.filename.label("name"),
            Resources.mime_type.label("mime"),
            Resources.file_size_bytes.label("size"),
            Resources.notes.label("brief"),
            Resources.updated_at,
            scope_text.label("scope_id"),
            Teams.name.label("team_name"),
            Teams.kind.label("scope_kind"),
        )
        .join(ResourceItems, ResourceItems.resource_id == Resources.id)
        .outerjoin(Teams, Teams.id == ResourceItems.scope_id)
        .where(id_text.in_(resource_ids))
        .where(Resources.is_trashed.is_(False))
        # PR-E 4c: ri.scope_id is always a teams.id snowflake; the
        # personal/team distinction now comes from teams.kind, not the
        # (dropped) scope_type column.
        .where(scope_text.in_(membership_subq))
    )

    # Resources carries UserScoped(creator_id), but this access check is
    # governed by team membership, not creator_id — a team-shared resource
    # must stay visible even when not owned by the caller. Matches the
    # is_enforced-gated system_request_scope pattern used across this
    # migration batch (resources_repository / resource_fetch_tool); no-op
    # today (flag off).
    scope_cm = (
        system_request_scope(reason="resource-ref-resolver-team-membership-access")
        if is_enforced("resources")
        else nullcontext()
    )
    async with scope_cm:
        async with read_scope() as session:
            rows = (await session.execute(stmt)).mappings().all()

    return {
        row["id"]: {
            "id": row["id"],
            "name": row["name"],
            "kind": kind_from_mime(row.get("mime")),
            "mime": row.get("mime"),
            "size": row.get("size"),
            "scope": (
                "personal"
                if row.get("scope_kind") == "personal"
                else f"team:{row.get('team_name') or row['scope_id']}"
            ),
            "updated_at": row["updated_at"],
            "brief": row.get("brief"),
        }
        for row in (rows or [])
    }


async def resolve_resource_refs(
    attachments: list[dict] | None, *, user_id: str
) -> tuple[list[dict], list[str]]:
    """Return ``(refs_for_prompt, warnings_for_user)``.

    ``refs_for_prompt`` is the list of dicts the prompt composer renders.
    ``warnings_for_user`` is a list of human messages like
    ``"Skipped: ghost.md (deleted or no longer accessible)"`` that the UI
    shows above the agent reply.
    """
    if not attachments:
        return [], []

    seen: set[str] = set()
    snapshots: list[dict] = []
    for att in attachments:
        if att.get("kind") != "resource_ref":
            continue
        rid = str(att.get("resource_id", ""))
        if not rid or rid in seen:
            continue
        seen.add(rid)
        snapshots.append(
            {
                "resource_id": rid,
                "name": att.get("name") or rid,
            }
        )

    if not snapshots:
        return [], []

    accessible = await _fetch_accessible_meta(
        user_id, [s["resource_id"] for s in snapshots]
    )

    refs: list[dict] = []
    warnings: list[str] = []
    for snap in snapshots:
        meta = accessible.get(snap["resource_id"])
        if meta is None:
            warnings.append(
                f"Skipped: {snap['name']} (deleted or no longer accessible)"
            )
            logger.info(
                f"[resource_ref_resolver] dropped inaccessible ref "
                f"id={snap['resource_id']!r} name={snap['name']!r} user={user_id}"
            )
            continue
        refs.append(meta)
    return refs, warnings


__all__ = [
    "resolve_resource_refs",
]
