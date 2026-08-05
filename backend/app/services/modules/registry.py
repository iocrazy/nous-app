"""Central registry of product modules and their processing/visibility switches.

Each module stores a ``{"enabled": bool, "visible": bool}`` blob under its own
``system_settings`` key. ``enabled`` is the PROCESSING/ACCESS switch (backend
behavior); ``visible`` is the DISPLAY switch (frontend nav + pages). Fail modes
are per-module: Topic Inspiration ships ON and fails OPEN; Distribution is opt-in
and fails CLOSED. This is the single source of truth — the per-feature
``module_config.py`` files are thin shims over it, and the admin ``/modules``
endpoint is driven by ``MODULES``. NEVER reads env.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from loguru import logger


@dataclass(frozen=True)
class ModuleDef:
    id: str
    key: str
    label: str
    enabled_default: bool
    visible_default: bool


@dataclass(frozen=True)
class ModuleState:
    enabled: bool
    visible: bool


MODULES: list[ModuleDef] = [
    ModuleDef(
        id="topic-inspiration",
        key="topics.module",
        label="Topic Inspiration",
        enabled_default=True,
        visible_default=True,
    ),
    ModuleDef(
        id="distribution",
        key="distribution.module",
        label="Distribution",
        enabled_default=False,
        visible_default=False,
    ),
    ModuleDef(
        # Storage-unification write switch (OPS-1 go-live gate). `enabled` =
        # route new library writes (resource uploads, project_files, canvas
        # derives, promote) to the Supabase Storage `library` bucket (sb://
        # content-addressed paths) instead of the filesystem. Readers resolve
        # both shapes, so flipping is forward-only and rollback keeps sb://
        # rows readable. `visible` is unused (no nav surface) and mirrors
        # `enabled` in practice. Opt-in, fails CLOSED: unset / read error
        # keeps every write on the proven filesystem track.
        id="unified-storage",
        key="storage.unified_storage",
        label="Unified Storage (library bucket)",
        enabled_default=False,
        visible_default=False,
    ),
    # ---- 2026-08-03 rollout: six live product modules, all default-ON and
    # fail-OPEN (missing/garbage config must never take a shipped feature
    # down). `enabled` gates the module's API/processing via
    # app.services.modules.gate.require_module; `visible` gates the frontend
    # nav entries + routes via GET /api/v1/modules/status.
    ModuleDef(
        # Gates NEW parse/download initiation (media_fetch_router,
        # media_batch_router) + the hourly retry tick. Reads/playback of
        # already-downloaded media are intentionally NOT gated.
        id="media-parser",
        key="media.module",
        label="Media Parser & Downloads",
        enabled_default=True,
        visible_default=True,
    ),
    ModuleDef(
        # Gates projects_router + project_assets_router + canvases_router.
        id="projects",
        key="projects.module",
        label="Projects (MediaTrack)",
        enabled_default=True,
        visible_default=True,
    ),
    ModuleDef(
        id="shares",
        key="shares.module",
        label="Shares",
        enabled_default=True,
        visible_default=True,
    ),
    ModuleDef(
        # Todolist UI is backed by issues_router.
        id="todolist",
        key="todolist.module",
        label="Todolist (Issues)",
        enabled_default=True,
        visible_default=True,
    ),
    ModuleDef(
        # Gates conversation_router + ai_library_router. Deliberately NOT
        # ai_settings_router / ai_memory_router / script_ai_router (settings
        # infra & the Scripts domain stay independent of this switch).
        id="ai-library",
        key="ai.module",
        label="AI Library & Chat",
        enabled_default=True,
        visible_default=True,
    ),
    ModuleDef(
        id="ideation",
        key="ideation.module",
        label="Ideation Board",
        enabled_default=True,
        visible_default=True,
    ),
]

MODULES_BY_ID: dict[str, ModuleDef] = {m.id: m for m in MODULES}
MODULES_BY_KEY: dict[str, ModuleDef] = {m.key: m for m in MODULES}


def _coerce_dict(raw: Any) -> Any:
    """The jsonb ``value`` column can come back as a JSON string rather than an
    already-decoded dict. Decode it so the bool parsers see a real dict; anything
    undecodable falls through to the per-module defaults."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return None
    return raw


def _parse_bool_field(raw: Any, field: str, default: bool) -> bool:
    if isinstance(raw, dict):
        v = raw.get(field)
        if isinstance(v, bool):
            return v
    return default


def parse_module_state(raw: Any, module: ModuleDef) -> ModuleState:
    """Pure parse of a stored blob into a ModuleState, jsonb-string-safe.
    Missing/garbage fields fall back to this module's own defaults."""
    data = _coerce_dict(raw)
    return ModuleState(
        enabled=_parse_bool_field(data, "enabled", module.enabled_default),
        visible=_parse_bool_field(data, "visible", module.visible_default),
    )


async def _read_raw(key: str) -> Any:
    """Service-role engine read of the config blob; never raises — any failure
    returns None so the parser falls back to the module's defaults."""
    try:
        from sqlalchemy import select

        from app.db import engine as db_engine
        from app.db.session import read_scope
        from app.models import SystemSettings

        if not db_engine.is_configured():
            return None
        async with read_scope() as session:
            return await session.scalar(
                select(SystemSettings.value).where(SystemSettings.key == key)
            )
    except Exception:  # noqa: BLE001
        logger.warning("[modules] config read failed for {} — using defaults", key)
        return None


async def read_module_state(module: ModuleDef) -> ModuleState:
    return parse_module_state(await _read_raw(module.key), module)


async def read_state_for_key(
    key: str, enabled_default: bool, visible_default: bool
) -> ModuleState:
    """Reader for the thin shims that only know their key + defaults.

    Precedence: if ``key`` is a registered module (present in
    ``MODULES_BY_KEY``), that module's own registry defaults take precedence
    and the passed-in ``enabled_default``/``visible_default`` are ignored.
    The passed-in defaults are used ONLY as the fallback ``ModuleDef`` for
    keys that are NOT in the registry. This keeps the registry the single
    source of truth — a caller cannot override a registered module's
    fail-open/fail-closed behavior by passing disagreeing defaults.
    """
    module = MODULES_BY_KEY.get(key) or ModuleDef(
        id=key,
        key=key,
        label=key,
        enabled_default=enabled_default,
        visible_default=visible_default,
    )
    return await read_module_state(module)


async def write_module_state(
    module: ModuleDef, enabled: bool, visible: bool, updated_by
) -> ModuleState:
    from app.repositories.admin.system_settings_repository import (
        get_system_settings_repository,
    )

    payload = {"enabled": bool(enabled), "visible": bool(visible)}
    repo = get_system_settings_repository()
    await repo.upsert_setting(module.key, payload, updated_by)
    return ModuleState(enabled=payload["enabled"], visible=payload["visible"])


async def list_module_summaries() -> list[dict]:
    summaries: list[dict] = []
    for module in MODULES:
        state = await read_module_state(module)
        summaries.append(
            {
                "id": module.id,
                "key": module.key,
                "label": module.label,
                "enabled": state.enabled,
                "visible": state.visible,
                "enabled_default": module.enabled_default,
                "visible_default": module.visible_default,
            }
        )
    return summaries
