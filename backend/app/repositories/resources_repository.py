# app/repositories/resources_repository.py

"""
Resources Repository

Data access layer for the resource library: resources, resource_items,
resource_versions, and folders. Uses async Supabase admin client.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin

# Working-set caps for the batch sweepers in this repo. They replace
# unbounded SELECTs that PostgREST silently truncated at 1000 (and the ORM
# twins fetched fully into RAM). Each consuming sweeper re-runs, so a backlog
# larger than one batch drains over successive runs instead of being clipped.
EXPIRED_TRASH_BATCH = 5000
UNTRANSCODED_BATCH = 2000

# AI status fields live on the `resources` table (migration 067). A resource
# is considered "completed" for a step when the column equals this value.
_AI_STATUS_COMPLETED = "completed"
_AI_STATUS_FIELDS: Dict[str, str] = {
    "transcribed": "transcript_status",
    "summarized": "summary_status",
    "analyzed": "visual_analysis_status",
}

# MIME prefixes considered "known" — anything else is the catch-all "other".
_KNOWN_MIME_PREFIXES: tuple[str, ...] = (
    "video/",
    "image/",
    "audio/",
    "application/pdf",
    "application/msword",
    "application/vnd.",
    "text/",
)

# Broad resource-type categories → list of PostgREST filter clauses.
# Kept in sync with the frontend FilterType enum.
_TYPE_CATEGORY_CLAUSES: Dict[str, List[str]] = {
    "video": ["mime_type.like.video/*"],
    "image": ["mime_type.like.image/*"],
    "audio": ["mime_type.like.audio/*"],
    "document": [
        "mime_type.eq.application/pdf",
        "mime_type.like.application/msword*",
        "mime_type.like.application/vnd.*",
        "mime_type.like.text/*",
    ],
}


def _build_mime_or_expr(types: Optional[List[str]]) -> Optional[str]:
    """Translate a list of type categories into a PostgREST ``or=`` clause.

    Returns ``None`` when no filter is needed. Handles the ``other``
    category by building an AND-of-NOT expression against each known
    prefix; PostgREST expresses this as ``and(not.like.*, not.like.*, …)``
    inside the top-level ``or(…)`` group.
    """
    if not types:
        return None

    categories = {t.strip() for t in types if t and t.strip()}
    if not categories:
        return None

    clauses: List[str] = []
    for category in categories:
        if category in _TYPE_CATEGORY_CLAUSES:
            clauses.extend(_TYPE_CATEGORY_CLAUSES[category])
        elif category == "other":
            not_clauses = [
                (f"not.like.{p}*" if p.endswith("/") else f"not.like.{p}*")
                for p in _KNOWN_MIME_PREFIXES
            ]
            # Require the mime to not match ANY known prefix.
            and_expr = "and(" + ",".join(f"mime_type.{c}" for c in not_clauses) + ")"
            clauses.append(and_expr)
        # Silently ignore unknown categories; schema validation happens
        # in the router layer.

    if not clauses:
        return None
    return ",".join(clauses)


class ResourcesRepository:
    """Resource library data access (async)"""

    TABLE_RESOURCES = "resources"
    TABLE_ITEMS = "resource_items"
    TABLE_VERSIONS = "resource_versions"
    TABLE_FOLDERS = "folders"
    TABLE_RESOURCE_TAGS = "resource_tags"

    def __init__(self):
        pass

    async def _get_client(self):
        """Get async client (loop-aware, safe for Celery workers)."""
        return await get_async_supabase_admin()

    # ------------------------------------------------------------------ #
    # Resources CRUD
    # ------------------------------------------------------------------ #

    async def create_resource(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_RESOURCES).insert(data).execute()
            logger.info(f"Created resource: {data.get('filename')}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create resource: {e}")
            raise

    async def get_resource_by_id(self, resource_id: str) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_RESOURCES)
                .select("*")
                .eq("id", resource_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get resource {resource_id}: {e}")
            return None

    async def get_resource_by_media_id(self, media_id: str) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_RESOURCES)
                .select("*")
                .eq("media_id", media_id)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get resource by media_id {media_id}: {e}")
            return None

    async def get_resource_by_platform_id(
        self, platform_id: str
    ) -> Optional[Dict[str, Any]]:
        """Look up resource by the external platform content ID (e.g. douyin aweme_id).

        Two-step: parsed_media.platform_id -> parsed_media.id -> resources.media_id
        """
        try:
            client = await self._get_client()
            # Step 1: find the media by platform_id
            media_result = (
                await client.table("parsed_media")
                .select("id")
                .eq("platform_id", platform_id)
                .limit(1)
                .execute()
            )
            if not media_result.data:
                return None
            media_uuid = media_result.data[0]["id"]
            # Step 2: find the resource by media_id
            return await self.get_resource_by_media_id(media_uuid)
        except Exception as e:
            logger.error(f"Failed to get resource by platform_id {platform_id}: {e}")
            return None

    async def get_resource_by_media_id_and_creator(
        self, media_id: str, creator_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get user's resource for a specific media item."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_RESOURCES)
                .select("*")
                .eq("media_id", media_id)
                .eq("creator_id", creator_id)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(
                f"Failed to get resource for media={media_id}, creator={creator_id}: {e}"
            )
            return None

    async def get_completed_resource_by_url_and_creator(
        self, url: str, creator_id: str
    ) -> Optional[Dict[str, Any]]:
        """L2 dedup probe — does this user already own a fully-downloaded
        resource for this URL? Joins resources → parsed_media via media_id
        and filters on parsed_media.original_url + completed status.

        Returns the resource row (with its ``id``, ``media_id``) when a
        match exists, otherwise None. Caller short-circuits the parse +
        download dispatch when this returns truthy."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_RESOURCES)
                .select(
                    "id, media_id, "
                    "parsed_media!inner(id, platform_id, original_url, "
                    "video_download_status, image_download_status, media_type)"
                )
                .eq("creator_id", creator_id)
                .eq("parsed_media.original_url", url)
                .limit(1)
                .execute()
            )
            row = (result.data or [None])[0]
            if not row:
                return None
            pm = row.get("parsed_media") or {}
            mt = pm.get("media_type")
            is_image = str(mt) in ("2", "68", "image", "images")
            status_field = (
                "image_download_status" if is_image else "video_download_status"
            )
            if pm.get(status_field) == "completed":
                return row
            return None
        except Exception as e:
            logger.debug(
                f"[ResourcesRepo] L2 dedup probe failed url={url[:40]} "
                f"creator={creator_id}: {e}"
            )
            return None

    async def get_owned_platform_ids(
        self, platform_ids: List[str], creator_id: str
    ) -> set:
        """Of the given vids (parsed_media.platform_id), return the subset this
        user has already downloaded (a resources row with file_path set). ONE
        batched, index-backed query for the whole list — no N+1.

        supabase-py fallback path (asyncpg is prod). Chunks the input into
        batches of 100 to avoid URL-length blowups on the PostgREST ``in``
        filter, and queries through the ``parsed_media!inner`` embed so a
        single round-trip per chunk yields the owned platform_ids."""
        if not platform_ids:
            return set()
        owned: set = set()
        try:
            client = await self._get_client()
            for start in range(0, len(platform_ids), 100):
                chunk = platform_ids[start : start + 100]
                result = (
                    await client.table(self.TABLE_RESOURCES)
                    .select("media_id, parsed_media!inner(platform_id)")
                    .eq("creator_id", creator_id)
                    .not_.is_("file_path", "null")
                    .in_("parsed_media.platform_id", chunk)
                    .execute()
                )
                for row in result.data or []:
                    pm = row.get("parsed_media") or {}
                    pid = pm.get("platform_id")
                    if pid is not None:
                        owned.add(pid)
            return owned
        except Exception as e:
            logger.error(
                f"Failed to resolve owned platform_ids for creator "
                f"{creator_id}: {e}"
            )
            return owned

    async def update_resource(
        self, resource_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_RESOURCES)
                .update(data)
                .eq("id", resource_id)
                .execute()
            )
            logger.info(f"Updated resource {resource_id}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update resource {resource_id}: {e}")
            raise

    async def delete_resource(self, resource_id: str) -> bool:
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_RESOURCES)
                .delete()
                .eq("id", resource_id)
                .execute()
            )
            logger.info(f"Deleted resource {resource_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete resource {resource_id}: {e}")
            raise

    async def count_resources_by_media_id(self, media_id: str) -> int:
        """Count how many resources reference a given parsed_media ID.

        A4 — RE-RAISE ON ERROR (data-loss hardening, mirrors the ORM repo): this
        is a CROSS-USER GC reference count whose result drives a DESTRUCTIVE
        decision on SHARED files (``permanent_delete`` / ``cleanup_expired_trash``
        delete the shared physical files + parsed_media row when ``remaining ==
        0``). A transient error returning a fabricated ``0`` would wipe files
        other users still reference, so we log and RE-RAISE — never fabricate 0.
        The service-layer callers catch this and SKIP the shared-file/media GC on
        uncertainty. This path is the LIVE prod default (``USE_ORM_RESOURCES``
        false), so the fix here closes the exposure independent of the ORM swap."""
        try:
            client = await self._get_client()
            result = await (
                client.table(self.TABLE_RESOURCES)
                .select("id", count="exact")
                .eq("media_id", media_id)
                .execute()
            )
            return result.count or 0
        except Exception as e:
            # NEVER return a fabricated 0 — a count failure must abort the
            # caller's shared-file GC, not silently green-light it.
            logger.error(f"Failed to count resources for media {media_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Hash-based duplicate lookup
    # ------------------------------------------------------------------ #

    async def find_by_hash(self, file_hash: str, creator_id: str) -> list[dict]:
        """Find non-trashed resources with the same file hash for a given creator."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_RESOURCES)
                .select(
                    "id, filename, file_type, mime_type, file_size_bytes, "
                    "thumbnail_path, cover_image_path, created_at"
                )
                .eq("file_hash", file_hash)
                .eq("creator_id", creator_id)
                .eq("is_trashed", False)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to find resources by hash: {e}")
            return []

    async def find_resource_item(
        self,
        resource_id: str,
        scope_type: Optional[str],
        scope_id: str,
        folder_id: str | None = None,
    ) -> dict | None:
        """Find a resource_item by resource_id + scope + folder.

        PR-E Phase 1: ``scope_type`` is accepted but unused — ``scope_id``
        is a globally-unique ``teams.id`` snowflake, so it alone scopes
        the row. The parameter remains for call-site compatibility.
        """
        try:
            client = await self._get_client()
            query = (
                client.table(self.TABLE_ITEMS)
                .select("*")
                .eq("resource_id", resource_id)
                .eq("scope_id", scope_id)
            )
            if folder_id:
                query = query.eq("folder_id", folder_id)
            else:
                query = query.is_("folder_id", "null")
            result = await query.limit(1).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to find resource_item: {e}")
            return None

    # ------------------------------------------------------------------ #
    # Resource Items (workspace scoping)
    # ------------------------------------------------------------------ #

    async def create_resource_item(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_ITEMS).insert(data).execute()
            logger.info(
                f"Created resource_item for resource {data.get('resource_id')} "
                f"in scope {data.get('scope_id')}"
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create resource_item: {e}")
            raise

    async def get_resource_items(
        self,
        scope_id: str,
        scope_type: Optional[str] = None,
        folder_id: Optional[str] = None,
        include_trashed: bool = False,
        tag_ids: Optional[List[str]] = None,
        min_rating: Optional[int] = None,
        types: Optional[List[str]] = None,
        platforms: Optional[List[str]] = None,
        ai_transcribed: Optional[bool] = None,
        ai_summarized: Optional[bool] = None,
        ai_analyzed: Optional[bool] = None,
        created_after: Optional[date] = None,
        created_before: Optional[date] = None,
        duration_min: Optional[int] = None,
        duration_max: Optional[int] = None,
        aspect_ratios: Optional[List[str]] = None,
        min_likes: Optional[int] = None,
        min_comments: Optional[int] = None,
        min_favorites: Optional[int] = None,
        min_shares: Optional[int] = None,
        social_combine: str = "and",
        has_comments: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        """List resource_items joined to their resources.

        Optional filters:
        - ``tag_ids``: only items whose resource carries ALL of the given
          tag ids (AND semantics).
        - ``min_rating``: only items whose resource has ``rating`` >= value.
        - ``types``: broad type categories — ``video``, ``image``, ``audio``,
          ``document``, ``other`` — mapped against ``resource.mime_type``.
        - ``platforms``: restrict to resources whose linked
          ``parsed_media.source_platform`` is in the given list (IN / OR
          semantics).  Resources without a linked parsed_media are
          excluded when this filter is applied.
        - ``ai_transcribed`` / ``ai_summarized`` / ``ai_analyzed``: when
          set to True, require the matching resource AI status column to
          equal ``"completed"``. ``None`` / ``False`` means no filter.
        - ``created_after`` / ``created_before``: inclusive date bounds
          on ``resource.created_at``. Dates are interpreted in UTC and
          expanded to full-day boundaries (after: >= 00:00:00 of the day;
          before: <= 23:59:59.999999 of the day).
        - ``duration_min`` / ``duration_max``: inclusive bounds (in
          seconds) on ``resource.duration_seconds``.
        - ``aspect_ratios``: accepted for API-surface parity with the
          frontend chip (e.g. ``"9:16"``, ``"16:9"``, ``"1:1"``,
          ``"4:3"``, ``"other"``). The current PR keeps aspect filtering
          client-side (the ``resolution`` column is a ``"WxH"`` string
          that would need parsing on every row), so this parameter is
          a no-op at the repository layer — left here so the router /
          service contract is stable.
        - ``min_likes`` / ``min_comments`` / ``min_favorites`` /
          ``min_shares``: inclusive lower bounds on the matching
          ``parsed_media.*_count`` columns. Semantics across the four
          thresholds follow ``social_combine`` (``"and"`` — default — or
          ``"or"``). ``has_comments`` additionally forces
          ``comment_count > 0`` (applied with AND on top of the combined
          thresholds). These parameters are accepted at the API surface
          for parity with the frontend chip; like ``aspect_ratios`` they
          are a no-op at the repository layer for now — social-metric
          filtering runs client-side in ``useResourcesDisplay`` because
          the values live on the sibling ``parsed_media`` table and
          pushing the filter down would require either an RPC or a more
          invasive two-step lookup.
        """
        try:
            client = await self._get_client()

            # AND-semantic tag filter: compute the intersection of resource
            # ids tagged with every requested tag, then feed that set into
            # the main query. An empty intersection short-circuits to [].
            matched_resource_ids: Optional[List[str]] = None
            if tag_ids:
                matched_resource_ids = await self._resource_ids_with_all_tags(tag_ids)
                if not matched_resource_ids:
                    return []

            # Platform filter is applied by pre-resolving the set of
            # resource ids whose linked parsed_media.source_platform
            # matches. Done here (rather than via a nested PostgREST
            # filter) to keep the repository independent of inner-join
            # syntax quirks and cheap for the common small-result case.
            if platforms:
                platform_resource_ids = await self._resource_ids_for_platforms(
                    platforms
                )
                if not platform_resource_ids:
                    return []
                if matched_resource_ids is None:
                    matched_resource_ids = platform_resource_ids
                else:
                    platform_set = set(platform_resource_ids)
                    matched_resource_ids = [
                        rid for rid in matched_resource_ids if rid in platform_set
                    ]
                    if not matched_resource_ids:
                        return []

            query = (
                client.table(self.TABLE_ITEMS)
                .select("*, resource:resources!inner(*)")
                .eq("scope_id", scope_id)
            )
            if folder_id:
                query = query.eq("folder_id", folder_id)
            else:
                query = query.is_("folder_id", "null")

            if not include_trashed:
                query = query.eq("resource.is_trashed", False)

            if matched_resource_ids is not None:
                query = query.in_("resource_id", matched_resource_ids)

            if min_rating is not None:
                query = query.gte("resource.rating", int(min_rating))

            mime_ors = _build_mime_or_expr(types)
            if mime_ors:
                # PostgREST `or=` on an embedded column requires the
                # `reference_table` kwarg so the parent parser doesn't
                # try to resolve the column against `resource_items`.
                query = query.or_(mime_ors, reference_table="resources")

            # AI status filters: each flag independently requires the
            # associated column == "completed". Applied on the embedded
            # resources table.
            for flag, column in (
                (ai_transcribed, _AI_STATUS_FIELDS["transcribed"]),
                (ai_summarized, _AI_STATUS_FIELDS["summarized"]),
                (ai_analyzed, _AI_STATUS_FIELDS["analyzed"]),
            ):
                if flag is True:
                    query = query.eq(f"resource.{column}", _AI_STATUS_COMPLETED)

            # Date-added range: inclusive, interpreted as UTC calendar
            # days. A missing bound is simply omitted.
            if created_after is not None:
                start_iso = datetime.combine(
                    created_after, datetime.min.time(), tzinfo=timezone.utc
                ).isoformat()
                query = query.gte("resource.created_at", start_iso)
            if created_before is not None:
                end_iso = datetime.combine(
                    created_before, datetime.max.time(), tzinfo=timezone.utc
                ).isoformat()
                query = query.lte("resource.created_at", end_iso)

            # Duration range (seconds): inclusive gte/lte on
            # ``resource.duration_seconds``. Non-video rows will be NULL
            # here — PostgREST drops them from the result, which matches
            # the intent of the chip (duration is video-specific).
            if duration_min is not None:
                query = query.gte("resource.duration_seconds", int(duration_min))
            if duration_max is not None:
                query = query.lte("resource.duration_seconds", int(duration_max))

            # ``aspect_ratios``: accepted at the API surface but not
            # filtered server-side in this PR. See the docstring above.
            _ = aspect_ratios

            # Social metric filters are accepted for API parity but
            # applied client-side in ``useResourcesDisplay``. See the
            # docstring for the rationale.
            _ = (
                min_likes,
                min_comments,
                min_favorites,
                min_shares,
                social_combine,
                has_comments,
            )

            query = query.order("created_at", desc=True)
            result = await query.execute()
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get resource items: {e}")
            return []

    async def _resource_ids_for_platforms(self, platforms: List[str]) -> List[str]:
        """Return resource ids whose linked ``parsed_media.source_platform``
        is in ``platforms``.

        Two-step lookup (parsed_media -> resources) to avoid relying on
        a PostgREST nested ``in`` filter, which depends on FK
        relationships that are easy to break at migration time.
        """
        cleaned = [p.strip() for p in platforms if p and p.strip()]
        if not cleaned:
            return []
        try:
            client = await self._get_client()
            media_rows = (
                await client.table("parsed_media")
                .select("id")
                .in_("source_platform", cleaned)
                .execute()
            )
            media_ids = [str(row["id"]) for row in (media_rows.data or [])]
            if not media_ids:
                return []
            resource_rows = (
                await client.table(self.TABLE_RESOURCES)
                .select("id")
                .in_("media_id", media_ids)
                .execute()
            )
            return [str(row["id"]) for row in (resource_rows.data or [])]
        except Exception as e:
            logger.error(f"Failed to resolve resource ids for platforms: {e}")
            return []

    async def _resource_ids_with_all_tags(self, tag_ids: List[str]) -> List[str]:
        """Return resource ids that carry every tag in ``tag_ids``.

        Implemented as N separate ``resource_tags`` lookups (one per tag)
        intersected in Python. Correct without relying on PostgREST
        group-by/having, which would require a dedicated RPC.
        """
        if not tag_ids:
            return []
        try:
            client = await self._get_client()
            result_set: Optional[set[str]] = None
            for tag_id in tag_ids:
                rows = (
                    await client.table(self.TABLE_RESOURCE_TAGS)
                    .select("resource_id")
                    .eq("tag_id", tag_id)
                    .execute()
                )
                ids = {str(r["resource_id"]) for r in (rows.data or [])}
                if result_set is None:
                    result_set = ids
                else:
                    result_set &= ids
                if not result_set:
                    return []
            return list(result_set or [])
        except Exception as e:
            logger.error(f"Failed to intersect resource tag ids: {e}")
            return []

    async def get_resource_item(
        self, resource_id: str, scope_type: Optional[str], scope_id: str
    ) -> Optional[Dict[str, Any]]:
        # PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_ITEMS)
                .select("*")
                .eq("resource_id", resource_id)
                .eq("scope_id", scope_id)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get resource_item: {e}")
            return None

    async def get_resource_item_in_folder(
        self,
        resource_id: str,
        scope_type: Optional[str],
        scope_id: str,
        folder_id: str | None,
    ) -> Optional[Dict[str, Any]]:
        """Get a specific resource_item by resource_id + scope + folder_id.

        PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        """
        try:
            client = await self._get_client()
            query = (
                client.table(self.TABLE_ITEMS)
                .select("*")
                .eq("resource_id", resource_id)
                .eq("scope_id", scope_id)
            )
            if folder_id:
                query = query.eq("folder_id", folder_id)
            else:
                query = query.is_("folder_id", "null")
            result = await query.limit(1).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get resource_item in folder: {e}")
            return None

    async def get_first_resource_item(
        self, resource_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get the first resource_item for a resource (any scope)."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_ITEMS)
                .select("*")
                .eq("resource_id", resource_id)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get first resource_item: {e}")
            return None

    async def update_resource_item(
        self, item_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_ITEMS)
                .update(data)
                .eq("id", item_id)
                .execute()
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update resource_item {item_id}: {e}")
            raise

    async def delete_resource_item(self, item_id: str) -> bool:
        """Delete a resource_item by ID. The DB trigger will auto-trash
        the parent resource if this was the last reference."""
        try:
            client = await self._get_client()
            await client.table(self.TABLE_ITEMS).delete().eq("id", item_id).execute()
            logger.info(f"Deleted resource_item {item_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete resource_item {item_id}: {e}")
            raise

    async def count_resource_items(self, resource_id: str) -> int:
        """Count how many resource_items reference a given resource."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_ITEMS)
                .select("id", count="exact")
                .eq("resource_id", resource_id)
                .execute()
            )
            return result.count or 0
        except Exception as e:
            logger.error(f"Failed to count items for resource {resource_id}: {e}")
            return 0

    async def get_expired_trashed_resources(
        self, older_than_days: int = 30, limit: int = EXPIRED_TRASH_BATCH
    ) -> List[Dict[str, Any]]:
        """Find trashed resources older than N days for permanent cleanup.

        Returns at most ``limit`` rows, oldest-trashed first. The explicit
        ``ORDER BY trashed_at`` + ``LIMIT`` replaces an unbounded SELECT that
        PostgREST silently capped at 1000 (and that the ORM twin fetched
        unbounded — OOM at scale). The daily sweeper re-runs, so a backlog
        larger than one batch drains over successive runs.
        """
        try:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=older_than_days)
            ).isoformat()
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_RESOURCES)
                .select("id, file_path, cover_image_path")
                .eq("is_trashed", True)
                .lt("trashed_at", cutoff)
                .order("trashed_at", desc=False)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get expired trashed resources: {e}")
            return []

    async def get_trashed_resources(
        self, scope_type: Optional[str], scope_id: str
    ) -> List[Dict[str, Any]]:
        # PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_ITEMS)
                .select("*, resource:resources!inner(*)")
                .eq("scope_id", scope_id)
                .eq("resource.is_trashed", True)
                .order("created_at", desc=True)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get trashed resources: {e}")
            return []

    # ------------------------------------------------------------------ #
    # Resource Versions
    # ------------------------------------------------------------------ #

    async def create_version(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_VERSIONS).insert(data).execute()
            logger.info(
                f"Created version {data.get('version_number')} "
                f"for resource {data.get('resource_id')}"
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create version: {e}")
            raise

    async def get_versions(self, resource_id: str) -> List[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_VERSIONS)
                .select("*")
                .eq("resource_id", resource_id)
                .order("version_number", desc=True)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get versions for resource {resource_id}: {e}")
            return []

    async def get_version_by_id(self, version_id: str) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_VERSIONS)
                .select("*")
                .eq("id", version_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get version {version_id}: {e}")
            return None

    async def get_version_by_number(
        self, resource_id: str, version_number: int
    ) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_VERSIONS)
                .select("*")
                .eq("resource_id", resource_id)
                .eq("version_number", version_number)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(
                f"Failed to get version {version_number} for {resource_id}: {e}"
            )
            return None

    async def delete_version(self, version_id: str) -> bool:
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_VERSIONS)
                .delete()
                .eq("id", version_id)
                .execute()
            )
            logger.info(f"Deleted version {version_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete version {version_id}: {e}")
            raise

    async def update_version(
        self, version_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_VERSIONS)
                .update(data)
                .eq("id", version_id)
                .execute()
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update version {version_id}: {e}")
            raise

    async def get_untranscoded_video_versions(
        self, limit: int = UNTRANSCODED_BATCH
    ) -> List[Dict[str, Any]]:
        """Get video versions that have never been transcoded (NULL status, has file).

        Returns at most ``limit`` rows ordered by id. The caller queues each +
        marks it ``pending`` (so it leaves this set), making the batch
        re-runnable to drain a backlog past one call — replacing the unbounded
        SELECT that silently capped at 1000.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_VERSIONS)
                .select("id, resource_id, mime_type, file_path")
                .like("mime_type", "video/%")
                .is_("transcode_status", "null")
                .not_.is_("file_path", "null")
                .order("id", desc=False)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get untranscoded video versions: {e}")
            return []

    async def get_next_version_number(self, resource_id: str) -> int:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_VERSIONS)
                .select("version_number")
                .eq("resource_id", resource_id)
                .order("version_number", desc=True)
                .limit(1)
                .execute()
            )
            if result.data:
                return result.data[0]["version_number"] + 1
            return 1
        except Exception as e:
            logger.error(f"Failed to get next version for resource {resource_id}: {e}")
            return 1

    # ------------------------------------------------------------------ #
    # Folders CRUD
    # ------------------------------------------------------------------ #

    async def create_folder(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_FOLDERS).insert(data).execute()
            logger.info(f"Created folder: {data.get('name')}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create folder: {e}")
            raise

    async def get_trashed_folders(
        self, scope_type: Optional[str], scope_id: str
    ) -> List[Dict[str, Any]]:
        """Get all trashed folders. Frontend handles root-level filtering.

        PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        """
        try:
            client = await self._get_client()
            result = await (
                client.table(self.TABLE_FOLDERS)
                .select("*")
                .eq("scope_id", scope_id)
                .eq("is_trashed", True)
                .order("trashed_at", desc=True)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get trashed folders: {e}")
            return []

    async def restore_folder_cascade(self, folder_id: str) -> Dict[str, int]:
        """Restore a folder, all descendant folders, and their resources."""
        restore_data = {"is_trashed": False, "trashed_at": None}
        restored_resources = 0
        restored_folders = 0

        try:
            client = await self._get_client()

            # Get all descendant trashed folders
            all_ids: List[str] = [folder_id]
            queue = [folder_id]
            while queue:
                parent_id = queue.pop(0)
                result = await (
                    client.table(self.TABLE_FOLDERS)
                    .select("id")
                    .eq("parent_id", parent_id)
                    .eq("is_trashed", True)
                    .execute()
                )
                for row in result.data or []:
                    child_id = str(row["id"])
                    all_ids.append(child_id)
                    queue.append(child_id)

            # 1. Restore resources in all affected folders
            for fid in all_ids:
                items_result = await (
                    client.table(self.TABLE_ITEMS)
                    .select("resource_id")
                    .eq("folder_id", fid)
                    .execute()
                )
                resource_ids = [
                    str(item["resource_id"]) for item in (items_result.data or [])
                ]
                for rid in resource_ids:
                    await (
                        client.table(self.TABLE_RESOURCES)
                        .update(restore_data)
                        .eq("id", rid)
                        .eq("is_trashed", True)
                        .execute()
                    )
                    restored_resources += 1

            # 2. Restore all folders
            for fid in all_ids:
                await (
                    client.table(self.TABLE_FOLDERS)
                    .update(restore_data)
                    .eq("id", fid)
                    .execute()
                )
                restored_folders += 1

            logger.info(
                f"Cascade-restored folder {folder_id}: "
                f"{restored_folders} folders, {restored_resources} resources"
            )
            return {
                "restored_folders": restored_folders,
                "restored_resources": restored_resources,
            }
        except Exception as e:
            logger.error(f"Failed to cascade-restore folder {folder_id}: {e}")
            raise

    async def get_folders(
        self, scope_type: Optional[str], scope_id: str, include_trashed: bool = False
    ) -> List[Dict[str, Any]]:
        # PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        try:
            client = await self._get_client()
            query = (
                client.table(self.TABLE_FOLDERS).select("*").eq("scope_id", scope_id)
            )
            if not include_trashed:
                query = query.eq("is_trashed", False)
            query = query.order("sort_order", desc=False)
            result = await query.execute()
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get folders: {e}")
            return []

    async def get_folder_by_id(self, folder_id: str) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_FOLDERS)
                .select("*")
                .eq("id", folder_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get folder {folder_id}: {e}")
            return None

    async def update_folder(
        self, folder_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_FOLDERS)
                .update(data)
                .eq("id", folder_id)
                .execute()
            )
            logger.info(f"Updated folder {folder_id}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update folder {folder_id}: {e}")
            raise

    async def get_descendant_folder_ids(self, folder_id: str) -> List[str]:
        """Recursively get all descendant folder IDs (children, grandchildren, etc.)."""
        all_ids: List[str] = []
        queue = [folder_id]
        try:
            client = await self._get_client()
            while queue:
                parent_id = queue.pop(0)
                result = await (
                    client.table(self.TABLE_FOLDERS)
                    .select("id")
                    .eq("parent_id", parent_id)
                    .eq("is_trashed", False)
                    .execute()
                )
                for row in result.data or []:
                    child_id = str(row["id"])
                    all_ids.append(child_id)
                    queue.append(child_id)
            return all_ids
        except Exception as e:
            logger.error(f"Failed to get descendant folders for {folder_id}: {e}")
            return []

    async def count_folder_contents(self, folder_ids: List[str]) -> Dict[str, int]:
        """Count resources and sub-folders within the given folder IDs."""
        try:
            client = await self._get_client()
            # Count resources via resource_items
            resource_count = 0
            for fid in folder_ids:
                result = await (
                    client.table(self.TABLE_ITEMS)
                    .select("id", count="exact")
                    .eq("folder_id", fid)
                    .execute()
                )
                resource_count += result.count or 0
            # Count sub-folders (excluding the root folder itself)
            subfolder_count = len(folder_ids) - 1 if len(folder_ids) > 1 else 0
            return {
                "resource_count": resource_count,
                "subfolder_count": subfolder_count,
            }
        except Exception as e:
            logger.error(f"Failed to count folder contents: {e}")
            return {"resource_count": 0, "subfolder_count": 0}

    async def trash_folder_cascade(self, folder_id: str) -> Dict[str, int]:
        """Trash a folder, all descendant folders, and their resources."""
        now = datetime.now(timezone.utc).isoformat()
        trash_data = {"is_trashed": True, "trashed_at": now}

        descendant_ids = await self.get_descendant_folder_ids(folder_id)
        all_folder_ids = [folder_id] + descendant_ids

        trashed_resources = 0
        trashed_folders = 0

        try:
            client = await self._get_client()

            # 1. Trash resources in all affected folders
            for fid in all_folder_ids:
                # Get resource_items with location info
                items_result = await (
                    client.table(self.TABLE_ITEMS)
                    .select("resource_id, folder_id, library_id, scope_id")
                    .eq("folder_id", fid)
                    .execute()
                )
                for item in items_result.data or []:
                    rid = str(item["resource_id"])
                    # PR-E 4c: scope_type no longer read/snapshotted (column
                    # being dropped); restore uses last_scope_id + folder/library.
                    update_data = {
                        **trash_data,
                        "last_folder_id": item.get("folder_id"),
                        "last_library_id": item.get("library_id"),
                        "last_scope_id": item.get("scope_id"),
                    }
                    await (
                        client.table(self.TABLE_RESOURCES)
                        .update(update_data)
                        .eq("id", rid)
                        .eq("is_trashed", False)
                        .execute()
                    )
                    trashed_resources += 1

            # 2. Trash all folders (descendants first, then root)
            for fid in reversed(all_folder_ids):
                await (
                    client.table(self.TABLE_FOLDERS)
                    .update(trash_data)
                    .eq("id", fid)
                    .execute()
                )
                trashed_folders += 1

            logger.info(
                f"Cascade-trashed folder {folder_id}: "
                f"{trashed_folders} folders, {trashed_resources} resources"
            )
            return {
                "trashed_folders": trashed_folders,
                "trashed_resources": trashed_resources,
            }
        except Exception as e:
            logger.error(f"Failed to cascade-trash folder {folder_id}: {e}")
            raise

    async def delete_folder(self, folder_id: str) -> bool:
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_FOLDERS).delete().eq("id", folder_id).execute()
            )
            logger.info(f"Deleted folder {folder_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete folder {folder_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Resource Tags
    # ------------------------------------------------------------------ #

    async def add_resource_tag(
        self, resource_id: str, tag_id: str, tagged_by: str
    ) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_RESOURCE_TAGS)
                .insert(
                    {
                        "resource_id": resource_id,
                        "tag_id": tag_id,
                        "tagged_by": tagged_by,
                    }
                )
                .execute()
            )
            logger.info(f"Tagged resource {resource_id} with tag {tag_id}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to tag resource {resource_id}: {e}")
            raise

    async def remove_resource_tag(self, resource_id: str, tag_id: str) -> bool:
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_RESOURCE_TAGS)
                .delete()
                .eq("resource_id", resource_id)
                .eq("tag_id", tag_id)
                .execute()
            )
            logger.info(f"Removed tag {tag_id} from resource {resource_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to remove tag from resource {resource_id}: {e}")
            raise

    async def get_resource_tags(self, resource_id: str) -> List[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_RESOURCE_TAGS)
                .select("*, tag:tags(*)")
                .eq("resource_id", resource_id)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get tags for resource {resource_id}: {e}")
            return []

    # ------------------------------------------------------------------ #
    # Smart Folders
    # ------------------------------------------------------------------ #

    async def get_smart_folders(
        self, scope_type: Optional[str], scope_id: str
    ) -> List[Dict[str, Any]]:
        """Get all smart folders for a scope.

        PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_FOLDERS)
                .select("*")
                .eq("scope_id", scope_id)
                .eq("is_smart", True)
                .eq("is_trashed", False)
                .order("sort_order", desc=False)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get smart folders: {e}")
            return []

    async def create_smart_folder(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a folder with is_smart=true."""
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_FOLDERS).insert(data).execute()
            logger.info(f"Created smart folder: {data.get('name')}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create smart folder: {e}")
            raise

    async def execute_smart_rules(
        self, scope_type: Optional[str], scope_id: str, rules: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Execute smart folder rules against resource_items + resources.

        Build a Supabase PostgREST query from the JSONB rules:
        - For fields on resources table: use resource.{field} in the join
        - For tags: use a subquery on resource_tags
        - For relative dates: compute the absolute date
        - Apply AND/OR logic
        - Apply match/exclude logic
        """
        try:
            client = await self._get_client()
            conditions = rules.get("conditions", [])
            operator = rules.get("operator", "AND")
            match = rules.get("match", True)

            # Separate tag conditions from resource conditions
            tag_conditions = [c for c in conditions if c["field"] == "tags"]
            resource_conditions = [c for c in conditions if c["field"] != "tags"]

            # Base query: resource_items with joined resources
            query = (
                client.table(self.TABLE_ITEMS)
                .select("*, resource:resources!inner(*)")
                .eq("scope_id", scope_id)
                .eq("resource.is_trashed", False)
            )

            if operator == "AND":
                # Apply each resource condition as a filter
                for cond in resource_conditions:
                    query = self._apply_condition(query, cond)
            else:
                # OR: use .or_() with PostgREST format
                if resource_conditions:
                    or_parts = []
                    for cond in resource_conditions:
                        part = self._condition_to_postgrest(cond)
                        if part:
                            or_parts.append(part)
                    if or_parts:
                        query = query.or_(
                            ",".join(or_parts), reference_table="resources"
                        )

            result = await query.order("created_at", desc=True).execute()
            items = result.data or []

            # Post-filter for tag conditions (tags live in resource_tags table)
            if tag_conditions:
                items = await self._filter_by_tags(
                    items, tag_conditions, operator, client
                )

            # Apply match/exclude logic
            if not match:
                # Exclude mode: get ALL items and subtract the matched set
                all_query = (
                    client.table(self.TABLE_ITEMS)
                    .select("*, resource:resources!inner(*)")
                    .eq("scope_id", scope_id)
                    .eq("resource.is_trashed", False)
                    .order("created_at", desc=True)
                )
                all_result = await all_query.execute()
                all_items = all_result.data or []
                matched_ids = {item["id"] for item in items}
                items = [item for item in all_items if item["id"] not in matched_ids]

            return items
        except Exception as e:
            logger.error(f"Failed to execute smart rules: {e}")
            return []

    def _apply_condition(self, query, cond: Dict[str, Any]):
        """Apply a single condition as a PostgREST filter (AND mode)."""
        field = cond["field"]
        op = cond["op"]
        value = self._resolve_value(cond["value"])

        col = f"resource.{field}"

        if op == "eq":
            return query.eq(col, value)
        elif op == "contains":
            return query.ilike(col, f"%{value}%")
        elif op == "starts_with":
            return query.ilike(col, f"{value}%")
        elif op == "gt":
            return query.gt(col, value)
        elif op == "lt":
            return query.lt(col, value)
        elif op == "gte":
            return query.gte(col, value)
        elif op == "lte":
            return query.lte(col, value)
        elif op == "in":
            return query.in_(col, value.split(","))
        return query

    def _condition_to_postgrest(self, cond: Dict[str, Any]) -> Optional[str]:
        """Convert a condition to PostgREST OR filter string."""
        field = cond["field"]
        op = cond["op"]
        value = self._resolve_value(cond["value"])

        if op == "eq":
            return f"{field}.eq.{value}"
        elif op == "contains":
            return f"{field}.ilike.%{value}%"
        elif op == "starts_with":
            return f"{field}.ilike.{value}%"
        elif op == "gt":
            return f"{field}.gt.{value}"
        elif op == "lt":
            return f"{field}.lt.{value}"
        elif op == "gte":
            return f"{field}.gte.{value}"
        elif op == "lte":
            return f"{field}.lte.{value}"
        elif op == "in":
            vals = value.replace(",", '","')
            return f'{field}.in.("{vals}")'
        return None

    def _resolve_value(self, value: str) -> str:
        """Resolve relative dates like 'relative:-7d' to absolute ISO dates."""
        if isinstance(value, str) and value.startswith("relative:"):
            offset_str = value.split(":")[1]
            # Parse -7d, -30d, -1h, etc.
            unit = offset_str[-1]
            amount = int(offset_str[:-1])
            now = datetime.now(timezone.utc)
            if unit == "d":
                target = now + timedelta(days=amount)
            elif unit == "h":
                target = now + timedelta(hours=amount)
            elif unit == "m":
                target = now + timedelta(minutes=amount)
            else:
                target = now + timedelta(days=amount)
            return target.isoformat()
        return value

    async def _filter_by_tags(
        self,
        items: List[Dict[str, Any]],
        tag_conditions: List[Dict[str, Any]],
        operator: str,
        client,
    ) -> List[Dict[str, Any]]:
        """Post-filter items by tag conditions using resource_tags table."""
        if not items:
            return items

        # Get resource IDs from items
        resource_ids = list(
            {item.get("resource_id") for item in items if item.get("resource_id")}
        )
        if not resource_ids:
            return []

        # Fetch all tags for these resources
        tag_result = (
            await client.table(self.TABLE_RESOURCE_TAGS)
            .select("resource_id, tag:tags(name)")
            .in_("resource_id", resource_ids)
            .execute()
        )
        tag_data = tag_result.data or []

        # Build resource_id -> set of tag names
        resource_tags: Dict[str, set] = {}
        for row in tag_data:
            rid = row["resource_id"]
            tag_name = row.get("tag", {}).get("name", "")
            if rid not in resource_tags:
                resource_tags[rid] = set()
            resource_tags[rid].add(tag_name.lower())

        # Apply tag conditions
        def matches_tags(resource_id: str) -> bool:
            tags = resource_tags.get(resource_id, set())
            results = []
            for cond in tag_conditions:
                tag_value = cond["value"].lower()
                if cond["op"] == "contains":
                    results.append(tag_value in tags)
                elif cond["op"] == "not_contains":
                    results.append(tag_value not in tags)
                else:
                    results.append(False)
            if operator == "AND":
                return all(results)
            return any(results)

        return [item for item in items if matches_tags(item.get("resource_id", ""))]

    # ------------------------------------------------------------------ #
    # @-reference picker
    # ------------------------------------------------------------------ #

    async def list_accessible_for_user(
        self,
        *,
        user_id: str,
        q: str = "",
        kinds: list[str] | None = None,
        limit: int = 20,
        cursor: str | None = None,
        scope_team_id: str | None = None,
    ) -> list[dict]:
        """Resources the user can read: own personal + team-shared.

        Returns list of dicts with keys: id, name, mime, size, updated_at,
        scope_type, scope_id. Used by the @-reference picker.

        Args:
            user_id: caller's auth id; used for both personal-owned and
                team-membership filtering.
            q: substring to ILIKE-match against ``filename`` (empty = no filter).
            kinds: optional list of canonical kinds — ``video``/``image``/
                ``doc``/``audio``/``pdf``. Unknown values are silently ignored
                (treated as wildcard), matching the picker UX intent.
            limit: page size; capped at 50 (min 1).
            cursor: reserved for Phase 2 pagination — currently unused.
            scope_team_id: when provided, restricts results to this team
                (membership verified) plus the caller's own personal team.
                When None, returns resources across all the caller's teams.
        """
        # Inline import: matches the pattern used elsewhere in this repo for
        # deferred-load services that would otherwise cause circular imports
        # when ResourcesRepository is constructed during module init.
        from app.db import engine as db_engine

        capped_limit = min(max(int(limit), 1), 50)
        kinds_list = list(kinds or [])

        # PR-E 4c: scope_type column is being dropped; derive the personal/team
        # label from teams.kind (aliased as scope_type so the caller's response
        # shape is unchanged). scope_id is always a teams.id snowflake post PR-C.
        sql_parts = [
            "SELECT r.id::text, r.filename AS name, ",
            "       r.mime_type AS mime, r.file_size AS size, ",
            "       r.updated_at, ri.scope_id::text, ",
            "       CASE WHEN t.kind = 'personal' THEN 'personal' ELSE 'team' END AS scope_type ",
            "FROM public.resources r ",
            "JOIN public.resource_items ri ON ri.resource_id = r.id ",
            "LEFT JOIN public.teams t ON t.id::text = ri.scope_id::text ",
            "WHERE r.is_trashed = false AND ri.is_trashed = false ",
        ]
        params: dict = {"user_id": user_id, "limit": capped_limit}

        # After Spec 1 PR-C, ri.scope_id is always a teams.id snowflake;
        # personal scope is a single-member team containing the user.
        if scope_team_id is not None:
            # Issue-scoped picker: narrow to the current team (only if the
            # caller is a member — no escalation) OR the caller's personal team.
            sql_parts.append(
                "  AND ri.scope_id::text IN ( "
                "        SELECT team_id::text FROM public.team_members "
                "          WHERE user_id = :user_id AND team_id::text = :scope_team_id "
                "        UNION "
                "        SELECT id::text FROM public.teams "
                "          WHERE owner_id::text = :user_id AND kind = 'personal' "
                "      ) "
            )
            params["scope_team_id"] = scope_team_id
        else:
            sql_parts.append(
                "  AND ri.scope_id::text IN ( "
                "        SELECT team_id::text FROM public.team_members WHERE user_id = :user_id "
                "      ) "
            )

        if q:
            sql_parts.append("AND r.filename ILIKE :q_like ")
            params["q_like"] = f"%{q}%"

        if kinds_list:
            sql_parts.append("AND r.mime_type ~ :kinds_re ")
            regex_segments = []
            for k in kinds_list:
                if k == "video":
                    regex_segments.append("^video/")
                elif k == "image":
                    regex_segments.append("^image/")
                elif k == "audio":
                    regex_segments.append("^audio/")
                elif k == "pdf":
                    regex_segments.append("^application/pdf$")
                elif k == "doc":
                    regex_segments.append("^(text/|application/json)")
            params["kinds_re"] = "|".join(regex_segments) if regex_segments else "."

        sql_parts.append("ORDER BY r.updated_at DESC LIMIT :limit")
        rows = await db_engine.fetch_all("".join(sql_parts), params)
        return rows or []

    # ------------------------------------------------------------------ #
    # Temp-folder sweeper helpers
    # ------------------------------------------------------------------ #

    async def list_resources_in_folder(
        self, folder_id: str, *, include_trashed: bool = False
    ) -> list[dict]:
        """Return resources whose resource_items row references this folder.

        ``folder_id`` lives on ``resource_items``, not ``resources``, so we
        join via the PostgREST embed syntax.  The result is flattened to
        ``{"id": resource_id, "created_at": resources.created_at}`` so the
        temp sweeper can compute expiry without knowing the schema detail.
        """
        try:
            client = await self._get_client()
            result = await (
                client.table("resource_items")
                .select(
                    "resource_id, resource:resources!inner(id, created_at, is_trashed)"
                )
                .eq("folder_id", folder_id)
                .execute()
            )
            rows = result.data or []
            out = []
            for row in rows:
                res = row.get("resource") or {}
                if not include_trashed and res.get("is_trashed"):
                    continue
                out.append(
                    {
                        "id": row["resource_id"],
                        "created_at": res.get("created_at"),
                    }
                )
            return out
        except Exception as e:
            logger.error(f"Failed to list resources in folder {folder_id}: {e}")
            raise

    async def soft_delete_resource(self, resource_id: str) -> None:
        """Mark a resource as trashed without removing the file on disk.

        File cleanup is handled by the existing trash-purge pipeline
        (``cleanup_trashed_resources_workflow``), not by this method.
        """
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_RESOURCES)
                .update(
                    {
                        "is_trashed": True,
                        "trashed_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                .eq("id", resource_id)
                .execute()
            )
            logger.info(f"[temp_sweeper] soft-deleted resource {resource_id}")
        except Exception as e:
            logger.error(f"Failed to soft-delete resource {resource_id}: {e}")
            raise


# ─── SQLAlchemy ORM migration factory (Task 5.2) ───────────────────────
#
# The supabase-py → SQLAlchemy 2.0 ORM cutover for the resources surface.
# This REPLACES the earlier asyncpg path (no tri-state): routes
# ``ResourcesRepository`` through the ORM subclass when both
# ``USE_ORM_RESOURCES=true`` and the SQLAlchemy engine is configured
# (``app.db.engine.is_configured``). Half-configured deploys (flag on,
# engine missing) fall back to the legacy supabase-py path with a single
# warning so a misconfigured env never crashes the worker.
#
# The ORM path fixes the silent-rollback P0: every write commits via
# ``write_scope()`` and each folder cascade runs atomically in one
# ``write_scope()`` session.
#
# Call sites should use ``get_resources_repository()`` rather than
# ``ResourcesRepository()`` directly. Existing ``ResourcesRepository()``
# constructors keep working — they bypass the flag and stay on the legacy
# supabase-py path. New code goes through the factory.


def get_resources_repository():
    """Return the right ResourcesRepository implementation per env."""
    from app.core.config import settings

    if settings.USE_ORM_RESOURCES:
        from app.db.engine import is_configured

        if is_configured():
            from app.repositories.resources_repository_orm import (
                ResourcesRepositoryOrm,
            )

            return ResourcesRepositoryOrm()
        logger.warning(
            "USE_ORM_RESOURCES=true but SUPAVISOR_DATABASE_URL is empty "
            "— falling back to supabase-py path"
        )
    return ResourcesRepository()
