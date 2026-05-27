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
    """Return dict of {id: meta} for those the user can read. Missing ids = not accessible."""
    from app.db import engine as db_engine

    if not resource_ids:
        return {}

    rows = await db_engine.fetch_all(
        """
        SELECT r.id::text AS id, r.filename AS name,
               r.mime_type AS mime, r.file_size AS size,
               r.description AS brief, r.updated_at,
               ri.scope_type, ri.scope_id::text AS scope_id,
               t.name AS team_name
          FROM public.resources r
          JOIN public.resource_items ri ON ri.resource_id = r.id
          LEFT JOIN public.teams t ON ri.scope_type = 'team' AND t.id = ri.scope_id
         WHERE r.id::text = ANY(:ids)
           AND r.is_trashed = false
           AND ri.is_trashed = false
           AND ( (ri.scope_type = 'personal' AND ri.scope_id::text = :uid)
                 OR (ri.scope_type = 'team' AND ri.scope_id IN (
                       SELECT team_id FROM public.team_members WHERE user_id = :uid
                 )) )
        """,
        {"ids": resource_ids, "uid": user_id},
    )
    return {
        row["id"]: {
            "id": row["id"],
            "name": row["name"],
            "kind": kind_from_mime(row.get("mime")),
            "mime": row.get("mime"),
            "size": row.get("size"),
            "scope": (
                "personal" if row["scope_type"] == "personal"
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
        snapshots.append({
            "resource_id": rid,
            "name": att.get("name") or rid,
        })

    if not snapshots:
        return [], []

    accessible = await _fetch_accessible_meta(user_id, [s["resource_id"] for s in snapshots])

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
