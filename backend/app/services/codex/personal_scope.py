"""Daemon jobs are filed under the user's personal team (a ``teams.id``
snowflake). Shared by canvas generation and the codex-local chat adapter."""

from __future__ import annotations


async def resolve_personal_scope_id(user_id: str) -> int:
    from app.services.library.resources_service import _resolve_personal_team_id

    return int(await _resolve_personal_team_id(str(user_id)))


__all__ = ["resolve_personal_scope_id"]
