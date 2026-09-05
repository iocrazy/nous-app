"""Per-``origin_kind`` context for an issue, as a registry (spec §1 seam C's
backend twin): ``@register("publish")`` adds a resolver that turns the issue
row into the extra fields that origin's context block renders. No resolver →
``{"kind": <origin_kind>, "origin_id": …}``; a resolver that raises is logged
and falls back to the same default — the progress endpoint never 500s over a
side panel.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from loguru import logger

Resolver = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
_RESOLVERS: dict[str, Resolver] = {}


def register(kind: str) -> Callable[[Resolver], Resolver]:
    def deco(fn: Resolver) -> Resolver:
        if kind in _RESOLVERS:
            raise ValueError(f"origin resolver for {kind!r} already registered")
        _RESOLVERS[kind] = fn
        return fn

    return deco


def registered_kinds() -> list[str]:
    return sorted(_RESOLVERS)


def _unregister_for_tests(kind: str) -> None:
    _RESOLVERS.pop(kind, None)


def default_origin(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": issue.get("origin_kind") or "manual",
        "origin_id": issue.get("origin_id"),
    }


async def resolve_origin(issue: dict[str, Any]) -> dict[str, Any]:
    base = default_origin(issue)
    fn: Optional[Resolver] = _RESOLVERS.get(base["kind"])
    if fn is None:
        return base
    try:
        extra = await fn(issue)
    except Exception as err:  # noqa: BLE001 — a side panel never fails the page
        logger.warning(f"[origin_resolvers] {base['kind']} resolver failed: {err}")
        return base
    return {**base, **(extra or {})}


__all__ = ["default_origin", "register", "registered_kinds", "resolve_origin"]
