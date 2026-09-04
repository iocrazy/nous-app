"""Per-scope TTL settings for chat temp resources.

Personal scope reads/writes ``user_settings.settings_json.chat_temp_ttl_days``.
Team scope reads/writes ``teams.settings_json.chat_temp_ttl_days``.
Returns ``None`` when the value is ``-1`` (never expire). The sweeper that
consumed this was retired in P6 (2026-09-04); the helper stays as a generic
``settings_json`` reader/writer and as the ORM B2 compile-coverage sample.
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
      - New (post-PR-C iterators): the personal team snowflake

    user_settings is keyed by auth.users.id (UUID), so translate the
    snowflake variant by looking up the team's owner. Returns None when
    no matching personal team exists.
    """
    if not scope_id or not str(scope_id).isdigit():
        return scope_id
    # Cast the COLUMN (Teams.id) to text rather than casting the :id bind
    # param to bigint: under asyncpg, casting a bound str param to bigint
    # makes PG infer the param as bigint and binding a str raises DataError
    # ('str' object cannot be encoded). Casting the column keeps the bind a
    # plain text param compared against a text-cast column.
    # (feedback_asyncpg_bigint_str_strict)
    from sqlalchemy import String, cast, select

    from app.db.session import read_scope
    from app.models import Teams

    async with read_scope() as session:
        uid = await session.scalar(
            select(cast(Teams.owner_id, String)).where(
                cast(Teams.id, String) == str(scope_id),
                Teams.kind == "personal",
            )
        )
    return uid


async def _fetch_settings_json(
    scope_type: str, scope_id: str
) -> Optional[dict[str, Any]]:
    """Return the row's ``settings_json`` value (a dict) or None if no row."""
    from sqlalchemy import String, cast, select

    from app.db.session import read_scope  # deferred — codebase convention
    from app.models import Teams, UserSettings

    if scope_type == "personal":
        user_id = await _resolve_personal_user_id(scope_id)
        if user_id is None:
            return None
        async with read_scope() as session:
            raw = await session.scalar(
                select(UserSettings.settings_json).where(
                    UserSettings.user_id == user_id
                )
            )
    else:
        # cast(Teams.id, String) == :scope_id — asyncpg-safe (str param vs
        # bigint column); see _resolve_personal_user_id note.
        async with read_scope() as session:
            raw = await session.scalar(
                select(Teams.settings_json).where(
                    cast(Teams.id, String) == str(scope_id)
                )
            )
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
    """Set a single key inside settings_json without clobbering other keys.

    ORM equivalent of the legacy SQL:
      INSERT INTO public.user_settings (user_id, settings_json)
        VALUES (:scope_id, jsonb_build_object(:key, to_jsonb(CAST(:value AS int))))
        ON CONFLICT (user_id) DO UPDATE SET
        settings_json = COALESCE(public.user_settings.settings_json, '{}'::jsonb)
          || jsonb_build_object(:key, to_jsonb(CAST(:value AS int))),
        updated_at = NOW()
    (teams branch: same jsonb merge, plain UPDATE instead of upsert — teams
    rows always exist by the time a scope_id is known).

    Referencing the mapped column (``UserSettings.settings_json`` /
    ``Teams.settings_json``) rather than ``stmt.excluded.*`` in the SET
    expression is deliberate: inside ``ON CONFLICT ... DO UPDATE``, Postgres
    resolves an unqualified/target-table-qualified column to the EXISTING
    (pre-conflict) row, while ``excluded.*`` is the proposed INSERT value —
    the legacy SQL's ``public.user_settings.settings_json`` reference is the
    former, so the merge is against the row already in the table, matching
    the #485 clobber rule (merge-not-replace).
    """
    import json

    from sqlalchemy import Integer, String, cast, func, literal
    from sqlalchemy import update as sa_update
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.db.session import write_scope
    from app.models import Teams, UserSettings

    empty_jsonb = cast(literal(json.dumps({})), JSONB)
    entry = func.jsonb_build_object(key, func.to_jsonb(cast(value, Integer)))

    if scope_type == "personal":
        # user_settings is UUID-keyed; translate snowflake input to owner.
        user_id = await _resolve_personal_user_id(scope_id)
        if user_id is None:
            raise ValueError(
                f"cannot resolve personal scope_id {scope_id!r} to a user_settings key"
            )
        # user_settings has UNIQUE(user_id) and may not have a row yet — upsert.
        stmt = pg_insert(UserSettings).values(user_id=user_id, settings_json=entry)
        stmt = stmt.on_conflict_do_update(
            index_elements=[UserSettings.user_id],
            set_={
                "settings_json": func.coalesce(
                    UserSettings.settings_json, empty_jsonb
                ).op("||")(entry),
                "updated_at": func.now(),
            },
        )
    else:
        # teams.settings_json was added in migration 225 with default
        # '{}'::jsonb. cast(Teams.id, String) == :scope_id — asyncpg-safe
        # (str param vs bigint column); see _resolve_personal_user_id note.
        stmt = (
            sa_update(Teams)
            .where(cast(Teams.id, String) == str(scope_id))
            .values(
                settings_json=func.coalesce(Teams.settings_json, empty_jsonb).op("||")(
                    entry
                )
            )
        )

    async with write_scope() as session:
        await session.execute(stmt)


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
