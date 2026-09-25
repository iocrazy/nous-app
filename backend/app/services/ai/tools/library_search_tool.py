"""LibrarySearch — the agent's verb for "find it in my library" (spec
2026-09-16-video-vector-layers-design §4.5, PR 5).

Model-facing spec + the handler the runner calls. The handler is a thin
adapter over ``SearchService.hybrid_search`` (text ILIKE + semantic vector
leg) and the shared ``resource_lookup`` (``parsed_media.id`` -> the caller's
``resources.id``, which is what the detail page opens).

Contract (same as ResourceFetch): keyword arguments, never raises, success is
a JSON-serialisable dict, failure is ``{"error": "<short>"}``. Ids are
strings — Snowflake BIGINTs do not survive a JS number.

Scope: only the caller's OWN library. ``hybrid_search`` has no team argument
yet; team libraries are a Known Limitation (prompts/README.md).

Layers: the search yields ``text``, ``semantic`` and (mig 507) ``visual``
hits. Without a filter, or with ``text`` in it, the hybrid search runs (all
three legs); ``semantic`` without ``text`` runs the document-vector leg alone
(``SearchService.semantic_only``); ``visual`` without ``text`` / ``semantic``
runs the shot-frame leg alone (``SearchService.visual_only``). ``camera`` /
``transcript`` are in the enum so the signature does not change when they
land; asking for them alone is not an error, it is simply empty. A ``visual``
hit carries ``shot`` (the moment to play from); every other hit has
``shot = null``.
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any, Awaitable, Callable, Optional

from loguru import logger

from app.db.scope import is_enforced, system_request_scope
from app.services.library.resource_lookup import fetch_user_resources_by_media_id

LIBRARY_SEARCH_TOOL_NAME = "LibrarySearch"
LAYERS: tuple[str, ...] = ("text", "semantic", "visual", "camera", "transcript")
DEFAULT_LIMIT = 8
MAX_LIMIT = 20
# Same cosine floor the library's own Smart Search uses.
SIMILARITY_THRESHOLD = 0.4
# Titles/descriptions are external text (scraped captions). Capped so a page
# of hits stays a bounded tool result; see Known Limitations on why they are
# not wrapped in neutralize_external_text.
TEXT_MAX_CHARS = 200
# Same cap as the search API's request schemas (app/schemas/search.py).
# Longer queries are cut, not refused, and the result says so.
QUERY_MAX_CHARS = 500


def library_search_spec() -> dict[str, Any]:
    """OpenAI function-calling spec for LibrarySearch (model-facing)."""
    return {
        "type": "function",
        "function": {
            "name": LIBRARY_SEARCH_TOOL_NAME,
            "description": (
                "Search the caller's own resource library (saved videos, "
                "images and other media) by keywords or a natural-language "
                "description. Returns each hit's resource_id, the layer that "
                "matched (text = keyword match in title/description/tags, "
                "semantic = meaning match, visual = a frame of the video looks "
                "like the description) and a score. A visual hit carries shot = "
                "{shot_id, start_ms, end_ms}: the moment to play from. Every "
                "other hit has shot = null. Run several short queries rather "
                "than one long sentence."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords or a short description.",
                    },
                    "layers": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(LAYERS)},
                        "description": (
                            "Only return hits from these layers. Omit for all."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_LIMIT,
                        "default": DEFAULT_LIMIT,
                    },
                },
                "required": ["query"],
            },
        },
    }


def _cap(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    text = str(text)
    return text if len(text) <= TEXT_MAX_CHARS else text[:TEXT_MAX_CHARS] + "…"


def _clamp_limit(raw: Any) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, value))


def _validate_layers(raw: Any) -> tuple[Optional[frozenset[str]], Optional[str]]:
    # An empty list is "no filter", same as omitting it.
    if raw is None or raw == []:
        return None, None
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        return None, "layers must be a list of layer names"
    unknown = sorted(set(raw) - set(LAYERS))
    if unknown:
        return None, f"unknown layer(s): {', '.join(unknown)}"
    return frozenset(raw), None


def _hit_dict(result: Any, resource: Optional[dict]) -> dict[str, Any]:
    return {
        "resource_id": (resource or {}).get("resource_id"),
        "media_id": str(result.media_id),
        "platform_id": result.platform_id or None,
        "title": _cap(result.title),
        "description": _cap(result.description),
        "layer": result.layer,
        "score": round(float(result.similarity), 4),
        "author": result.author,
        "created_at": result.created_at,
        "shot": _shot_dict(getattr(result, "shot", None)),
    }


def _shot_dict(shot: Any) -> Optional[dict[str, Any]]:
    if not shot:
        return None
    return {
        "shot_id": str(shot["shot_id"]),  # Snowflake: string
        "start_ms": int(shot["start_ms"]),
        "end_ms": int(shot["end_ms"]),
    }


def _recount_legs(
    ran: Optional[dict], results: list, wanted: Optional[frozenset[str]]
) -> dict[str, int]:
    """Per-layer counts of the hits the model is shown. A key means that leg
    ran (0 = ran, matched nothing); filtered-out layers are dropped."""
    keys = set(ran or {}) | {r.layer for r in results}
    if wanted is not None:
        keys &= wanted
    return {
        layer: sum(1 for r in results if r.layer == layer)
        for layer in LAYERS
        if layer in keys
    }


async def _search_legs(
    *,
    query: str,
    wanted: Optional[frozenset[str]],
    limit: int,
    user_id: str,
    search_service: Any,
) -> Any:
    """Pick the leg(s) to run. None = nothing that can match was asked for."""
    if wanted is None:
        return await search_service.hybrid_search(
            query, user_id=user_id, limit=limit, threshold=SIMILARITY_THRESHOLD
        )
    if "text" in wanted:
        # A layer filter applied after the merge would underfill the page,
        # so ask for the ceiling and cut after filtering.
        return await search_service.hybrid_search(
            query, user_id=user_id, limit=MAX_LIMIT, threshold=SIMILARITY_THRESHOLD
        )
    if "semantic" in wanted:
        # Through hybrid a page of text hits would skip the vector leg
        # entirely (skipped_full_page); ask it directly.
        return await search_service.semantic_only(
            query, user_id=user_id, limit=limit, threshold=SIMILARITY_THRESHOLD
        )
    if "visual" in wanted:
        # Same reason: the shot-frame leg alone, never skipped by text hits.
        return await search_service.visual_only(
            query, user_id=user_id, limit=limit, threshold=SIMILARITY_THRESHOLD
        )
    return None


async def _run_search(
    *,
    query: str,
    wanted: Optional[frozenset[str]],
    limit: int,
    user_id: str,
    search_service: Any,
) -> dict[str, Any]:
    response = await _search_legs(
        query=query,
        wanted=wanted,
        limit=limit,
        user_id=user_id,
        search_service=search_service,
    )
    if response is None:
        return {
            "query": query,
            "hits": [],
            "legs": {},
            "vector_leg": None,
            "reranked": False,
            "total": 0,
        }
    results = list(response.results)
    if wanted is not None:
        results = [r for r in results if r.layer in wanted]
    results = results[:limit]
    resource_map = await fetch_user_resources_by_media_id(
        user_id, [int(r.media_id) for r in results]
    )
    hits = [_hit_dict(r, resource_map.get(int(r.media_id))) for r in results]
    return {
        "query": query,
        "hits": hits,
        "legs": _recount_legs(response.legs, results, wanted),
        "vector_leg": response.vector_leg,
        "reranked": bool(response.reranked),
        "total": len(hits),
    }


async def library_search(
    *,
    query: Any,
    user_id: str,
    layers: Any = None,
    limit: Any = None,
    search_service: Any = None,
) -> dict[str, Any]:
    """Public entry point. Never raises."""
    if not user_id:
        return {"error": "library search needs a signed-in user"}
    if not isinstance(query, str) or not query.strip():
        return {"error": "query must be a non-empty string"}
    wanted, layer_error = _validate_layers(layers)
    if layer_error:
        return {"error": layer_error}
    if search_service is None:
        from app.services.library.search_service import SearchService

        search_service = SearchService()

    # The @agent conversation path runs in a DBOS worker with no request
    # scope; with SCOPE_ENFORCE_RESOURCES on (production) an unscoped
    # Resources read raises UnscopedQueryError. Every query below filters by
    # user_id explicitly, so SYSTEM does not widen what is returned.
    scope_cm = (
        system_request_scope(reason="library-search-tool-own-library")
        if is_enforced("resources")
        else nullcontext()
    )
    query = query.strip()
    truncated = len(query) > QUERY_MAX_CHARS
    query = query[:QUERY_MAX_CHARS]
    try:
        async with scope_cm:
            result = await _run_search(
                query=query,
                wanted=wanted,
                limit=_clamp_limit(limit),
                user_id=str(user_id),
                search_service=search_service,
            )
        return {**result, "truncated": truncated}
    except Exception as exc:
        logger.exception(f"[library_search] failed: {exc!r}")
        return {"error": f"library search failed: {exc.__class__.__name__}"}


def make_library_search_handler(
    user_id: str, *, search_service: Any = None
) -> Callable[[dict], Awaitable[dict[str, Any]]]:
    """Bind the caller; the runner calls the result with the tool args."""

    async def _handler(args: dict) -> dict[str, Any]:
        return await library_search(
            query=args.get("query"),
            layers=args.get("layers"),
            limit=args.get("limit"),
            user_id=user_id,
            search_service=search_service,
        )

    return _handler
