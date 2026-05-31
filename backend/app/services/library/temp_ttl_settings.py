"""Per-scope TTL settings for chat temp resources.

Personal scope reads/writes ``user_settings.settings_json.chat_temp_ttl_days``.
Team scope reads/writes ``teams.settings_json.chat_temp_ttl_days``.
Returns ``None`` when the value is ``-1`` (never expire) so the sweeper
can skip the scope cleanly.
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

# Default applied when no row / no key exists. Locked at 30 days per the
# sub-plan 2 design decision.
DEFAULT_TTL_DAYS = 30

# Sentinel stored in settings_json for "never expire".
_NEVER = -1

_VALID_SCOPES = ("personal", "team")


def _check_scope(scope_type: str) -> None:
    if scope_type not in _VALID_SCOPES:
        raise ValueError(
            f"unsupported scope_type {scope_type!r}; expected one of {_VALID_SCOPES}"
        )


async def _resolve_personal_user_id(scope_id: str) -> Optional[str]:
    """Translate a personal scope_id to the user_settings.user_id key.

    Callers pass scope_id in two shapes depending on origin:
      - Old (UI panels): the user UUID directly
      - New (post-PR-C iterators, e.g. temp_resource_sweeper): the
        personal team snowflake

    user_settings is keyed by auth.users.id (UUID), so translate the
    snowflake variant by looking up the team's owner. Returns None when
    no matching personal team exists.
    """
    from app.db import engine as db_engine

    if not scope_id or not str(scope_id).isdigit():
        return scope_id
    # Compare via id::text = :id (text param), NOT id = CAST(:id AS bigint):
    # under asyncpg, `CAST($1 AS bigint)` makes PG infer $1 as bigint, so
    # binding a str raises DataError ('str' object cannot be encoded). The
    # ::text form keeps $1 a text param. (feedback_asyncpg_bigint_str_strict)
    row = await db_engine.fetch_one(
        "SELECT owner_id::text AS uid FROM public.teams "
        "WHERE id::text = :id AND kind = 'personal'",
        {"id": str(scope_id)},
    )
    return row["uid"] if row else None


async def _fetch_settings_json(
    scope_type: str, scope_id: str
) -> Optional[dict[str, Any]]:
    """Return the row's ``settings_json`` value (a dict) or None if no row."""
    from app.db import engine as db_engine  # deferred — codebase convention

    if scope_type == "personal":
        user_id = await _resolve_personal_user_id(scope_id)
        if user_id is None:
            return None
        sql = (
            "SELECT settings_json FROM public.user_settings "
            "WHERE user_id = :scope_id"
        )
        row = await db_engine.fetch_one(sql, {"scope_id": user_id})
    else:
        # id::text = :scope_id — asyncpg-safe (str param vs bigint column);
        # see _resolve_personal_user_id note.
        sql = "SELECT settings_json FROM public.teams WHERE id::text = :scope_id"
        row = await db_engine.fetch_one(sql, {"scope_id": str(scope_id)})
    if row is None:
        return None
    raw = row.get("settings_json")
    # asyncpg returns jsonb as dict already; defensive parse for the str case.
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        import json

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(
                f"[temp_ttl] {scope_type}/{scope_id} settings_json is not valid JSON: {raw!r}"
            )
            return None
    return None


async def get_chat_temp_ttl_days(scope_type: str, scope_id: str) -> Optional[int]:
    """Return TTL in days for the scope. None means 'never expire'."""
    _check_scope(scope_type)
    settings = await _fetch_settings_json(scope_type, str(scope_id))
    if settings is None:
        return DEFAULT_TTL_DAYS
    raw = settings.get("chat_temp_ttl_days")
    if raw is None:
        return DEFAULT_TTL_DAYS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            f"[temp_ttl] {scope_type}/{scope_id} chat_temp_ttl_days not int: {raw!r}; "
            "falling back to default"
        )
        return DEFAULT_TTL_DAYS
    if value == _NEVER:
        return None
    if value <= 0:
        # Malformed (0 or negative-not-NEVER) → fall back to default.
        logger.warning(
            f"[temp_ttl] {scope_type}/{scope_id} chat_temp_ttl_days invalid ({value}); "
            "falling back to default"
        )
        return DEFAULT_TTL_DAYS
    return value


async def _upsert_settings_key(
    scope_type: str, scope_id: str, key: str, value: Any
) -> None:
    """Set a single key inside settings_json without clobbering other keys."""
    from app.db import engine as db_engine

    if scope_type == "personal":
        # user_settings is UUID-keyed; translate snowflake input to owner.
        user_id = await _resolve_personal_user_id(scope_id)
        if user_id is None:
            raise ValueError(
                f"cannot resolve personal scope_id {scope_id!r} to a user_settings key"
            )
        # user_settings has UNIQUE(user_id) and may not have a row yet — upsert.
        # NOTE: use CAST(:value AS int), NOT :value::int — SQLAlchemy's text()
        # bind-param parser treats ``::`` as a Postgres cast and eats one ``:``
        # from the param name, so ``:value::int`` registers as bind ``valu``
        # and the params dict no longer matches → CompileError at execute time.
        sql = (
            "INSERT INTO public.user_settings (user_id, settings_json) "
            "VALUES (:scope_id, jsonb_build_object(:key, to_jsonb(CAST(:value AS int)))) "
            "ON CONFLICT (user_id) DO UPDATE SET "
            "settings_json = COALESCE(public.user_settings.settings_json, '{}'::jsonb) "
            "|| jsonb_build_object(:key, to_jsonb(CAST(:value AS int))), "
            "updated_at = NOW()"
        )
        params = {"scope_id": user_id, "key": key, "value": value}
    else:
        # teams.settings_json was added in migration 225 with default '{}'::jsonb.
        sql = (
            "UPDATE public.teams SET settings_json = "
            "COALESCE(settings_json, '{}'::jsonb) "
            "|| jsonb_build_object(:key, to_jsonb(CAST(:value AS int))) "
            "WHERE id::text = :scope_id"
        )
        params = {"scope_id": str(scope_id), "key": key, "value": value}
    await db_engine.execute(sql, params)


async def set_chat_temp_ttl_days(scope_type: str, scope_id: str, ttl_days: int) -> None:
    """Persist a new TTL. ``ttl_days`` must be a positive int or -1 ('never')."""
    _check_scope(scope_type)
    if ttl_days != _NEVER and ttl_days <= 0:
        raise ValueError(
            f"ttl_days must be a positive int or -1 (never); got {ttl_days}"
        )
    await _upsert_settings_key(
        scope_type, str(scope_id), "chat_temp_ttl_days", ttl_days
    )
