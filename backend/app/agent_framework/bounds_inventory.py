"""Bounds inventory — introspect what THIS process can handle.

Sprint 5.5 wire-up. Sprint 5 landed BoundsAdvertisement + Registry but
the worker self-registers an empty bound (just worker_id + role). This
module fills in the real capability inventory:

  - workflow names: every callable exported by ``app.workflows`` package
    (the package __init__ imports each module, which runs the
    @DBOS.workflow decorator — once imported, the symbol IS the workflow)
  - agent slugs: distinct ``ai_agents.slug`` values currently loaded
  - providers: from settings — which API keys are configured

Pure functions; no side effects. Composer in main.py calls these at
startup to build the real BoundsAdvertisement, then registers it.

Worker registration shape:
  await asyncio.gather(
      _load_workflows(),
      _load_agents(),
      _load_providers(),
  )
  bound = BoundsAdvertisement(worker_id=..., role=..., workflows=..., ...)
  registry.register(bound)
"""

from __future__ import annotations

import inspect
from types import ModuleType
from typing import Any, Iterable


def inventory_workflow_names(workflows_module: ModuleType) -> frozenset[str]:
    """Names of every callable exported from ``app.workflows`` package.

    The package __init__ imports each workflow module; importing runs the
    @DBOS.workflow decorator (which side-registers the function with
    DBOS). So "exported callable" == "registered workflow" by construction.

    Returns the bare function names (e.g. 'download_workflow') — no
    module prefix. Callers wanting fully-qualified names can prefix with
    ``__module__``.
    """
    names: set[str] = set()
    for name, value in vars(workflows_module).items():
        if name.startswith("_"):
            continue
        if not callable(value):
            continue
        # Skip imported modules (e.g. `from app import workflows` → 'workflows' attr)
        if inspect.ismodule(value):
            continue
        # Defensive: only count things defined in app.workflows.* packages
        mod = getattr(value, "__module__", "")
        if not mod.startswith("app.workflows"):
            continue
        names.add(name)
    return frozenset(names)


async def inventory_agent_slugs(agent_repo: Any) -> frozenset[str]:
    """All distinct ``ai_agents.slug`` values loaded into the DB.

    Returns empty frozenset if the repo is None or the read fails — bound
    advertisement is best-effort. The dispatch gate falls back to "allow"
    on missing inventory rather than blocking dispatch.
    """
    if agent_repo is None:
        return frozenset()
    try:
        # AgentRepository.list_all may be missing in older revisions;
        # fall back to a raw client read in that case.
        if hasattr(agent_repo, "list_all_slugs"):
            slugs = await agent_repo.list_all_slugs()
            return frozenset(slugs)
        # Generic fallback path: read via the underlying admin client.
        client = await agent_repo._get_client()  # noqa: SLF001
        result = await client.table("ai_agents").select("slug").execute()
        return frozenset(row["slug"] for row in (result.data or []) if row.get("slug"))
    except Exception:
        return frozenset()


async def inventory_providers(settings_obj: Any = None) -> frozenset[str]:
    """Provider names that have credentials configured.

    Credentials are DB-only (铁律 2026-07-07): availability comes from the
    enabled rows of the platform ``mediahub_models`` catalog (their
    ``actual_provider`` values), not from env probing — ``settings_obj`` is
    accepted for signature compatibility and ignored. Does NOT verify the
    keys work. Best-effort: a broken catalog read degrades to an empty set
    (the advertisement just claims no provider capability).
    """
    try:
        from app.repositories.mediahub_model_repository import (
            get_mediahub_model_repository,
        )

        rows = await get_mediahub_model_repository().list_all()
    except Exception:  # noqa: BLE001 — inventory must not break startup
        return frozenset()
    return frozenset(
        str(row.get("actual_provider") or "").strip()
        for row in rows
        if row.get("is_enabled") and (row.get("actual_provider") or "").strip()
    )


def merge_lane_capacity(
    *capacity_dicts: Iterable[tuple[str, int]] | dict[str, int]
) -> dict[str, int]:
    """Merge multiple lane→capacity dicts. Last writer wins on conflict
    (matches typical config-overrides-defaults precedence)."""
    out: dict[str, int] = {}
    for d in capacity_dicts:
        if hasattr(d, "items"):
            for k, v in d.items():  # type: ignore[union-attr]
                out[k] = int(v)
        else:
            for k, v in d:
                out[k] = int(v)
    return out


__all__ = [
    "inventory_agent_slugs",
    "inventory_providers",
    "inventory_workflow_names",
    "merge_lane_capacity",
]
