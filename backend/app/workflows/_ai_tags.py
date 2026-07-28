"""Shared AI-tag write-through for caption_asset / classify_asset.

Extracted verbatim from ``classify_asset.py`` (the original locus,
2026-06-12) so ``caption_asset`` can reuse the exact same find-or-create
+ junction-upsert sequence for its new semantic tags instead of
duplicating it: same group-cache-per-call, same confidence constant,
same ``system_request_scope`` wrapping (tags/tag_groups are shared
vocabulary tables, not tenant rows — a USER scope would reject or
mis-filter the find-or-create paths; #608 precedent).
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from loguru import logger

AI_TAG_CONFIDENCE = 0.8


async def write_ai_tags(
    *,
    resource_id: str,
    user_id: str,
    entries: Iterable[dict[str, Any]],
    scope_name: str,
    log_prefix: str,
) -> int:
    """Find-or-create + attach AI tags to a resource. Returns attached count.

    Each item in ``entries`` needs ``group`` + ``en`` (canonical tag
    name) and may carry ``zh``. Runs the whole sequence under
    ``system_request_scope(scope_name)`` — callers must NOT already be
    inside a user ``request_scope`` (matches the non-nesting pattern
    ``classify_asset_workflow`` uses: the resource read/write happens
    under the user scope, then that scope exits before this runs).
    """
    from app.db.scope import system_request_scope
    from app.repositories.tags_repository import get_tags_repository

    tags_repo = get_tags_repository()
    attached = 0
    async with system_request_scope(scope_name):
        group_cache: dict[str, Optional[str]] = {}
        for entry in entries:
            group_name = entry["group"]
            if group_name not in group_cache:
                group = await tags_repo.get_or_create_group(group_name)
                group_cache[group_name] = (
                    str(group["id"]) if group and group.get("id") else None
                )

            tag = await tags_repo.get_tag_by_name(entry["en"], user_id)
            if not tag and entry.get("zh"):
                tag = await tags_repo.get_tag_by_name(entry["zh"], user_id)
            if not tag:
                tag = await tags_repo.create_tag(
                    name=entry["en"],
                    user_id=user_id,
                    name_zh=entry.get("zh") or None,
                    group_id=group_cache[group_name],
                )
            if not tag or not tag.get("id"):
                logger.warning(
                    f"{log_prefix} could not resolve tag " f"'{entry['en']}' — skipped"
                )
                continue
            await tags_repo.add_tag_to_resource(
                resource_id=str(resource_id),
                tag_id=str(tag["id"]),
                confidence=AI_TAG_CONFIDENCE,
                source="ai",
            )
            attached += 1
    return attached
