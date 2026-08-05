"""Best-effort persistence of per-user BYOK provider connection-test results.

User-side "Test Connection" results (``POST /ai/test-connection`` for cloud
providers, browser-direct probes for local Ollama / LM Studio) used to live
ONLY in React state — gone on reload. This persists the last outcome into
``user_settings.settings_json.ai_provider_health.<provider_key>`` so the
Settings UI can show "Last tested ..." across reloads, mirroring the admin
platform-model probe board (``mediahub_models.last_test_status``).

STORAGE SHAPE — a NEW TOP-LEVEL key in ``settings_json``, deliberately NOT
nested under ``ai_settings``. The canonical settings_json merge
(``_atomic_merge_settings_json``) is a SHALLOW top-level ``||`` merge; nesting
under ``ai_settings`` would clobber sibling keys on the next ai_settings write
(the #485 lesson, one level deeper). Each provider entry is::

    {"status": "ok"|"fail", "detail": str(≤300), "tested_at": ISO-8601 UTC}

ALL persistence here is BEST-EFFORT: a failure (DB down, engine unconfigured,
no user_settings row, even a bad key) logs a warning and NEVER propagates to
the caller — telemetry must not break a connection test.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from loguru import logger

# Provider keys are slugs from the frontend PROVIDER_META (openai / deepseek /
# doubao / volcengine / ollama / lmstudio / …).
_PROVIDER_KEY_RE = re.compile(r"^[a-z0-9_\-]+$")
_MAX_KEY_LEN = 40
_MAX_DETAIL_LEN = 300


# jsonb_set twice: first ensure the ``ai_provider_health`` object exists, then
# set the per-provider entry. The provider key is bound via ``postgresql.array``
# (a real bind parameter, never interpolated) so it cannot inject into the
# jsonb path. If no user_settings row exists the UPDATE is a no-op (best-effort
# telemetry — we do NOT insert rows).
#
# ORM equivalent of the legacy SQL:
#   UPDATE public.user_settings SET settings_json =
#     jsonb_set(
#       jsonb_set(COALESCE(settings_json, '{}'::jsonb), '{ai_provider_health}',
#         COALESCE(settings_json->'ai_provider_health', '{}'::jsonb), true),
#       ARRAY['ai_provider_health', :pk], CAST(:val AS jsonb), true)
#     WHERE user_id = :uid
def _upsert_health_stmt(user_id: str, provider_key: str, entry: dict):
    from sqlalchemy import cast, func, literal
    from sqlalchemy import update as sa_update
    from sqlalchemy.dialects.postgresql import JSONB, array

    from app.models import UserSettings

    empty_jsonb = cast(literal(json.dumps({})), JSONB)
    entry_jsonb = cast(literal(json.dumps(entry)), JSONB)
    existing_health = func.coalesce(
        UserSettings.settings_json.op("->", return_type=JSONB)("ai_provider_health"),
        empty_jsonb,
    )
    with_health_key = func.jsonb_set(
        func.coalesce(UserSettings.settings_json, empty_jsonb),
        array(["ai_provider_health"]),
        existing_health,
        True,
    )
    with_entry = func.jsonb_set(
        with_health_key,
        array(["ai_provider_health", provider_key]),
        entry_jsonb,
        True,
    )
    return (
        sa_update(UserSettings)
        .where(UserSettings.user_id == user_id)
        .values(settings_json=with_entry)
    )


class InvalidProviderKey(ValueError):
    """Raised when a ``provider_key`` fails validation.

    The ``POST /ai/provider-health`` endpoint maps this to a 422; the
    best-effort test-connection hook swallows it (skip silently).
    """


def validate_provider_key(provider_key: str) -> str:
    """Return ``provider_key`` if it is a non-empty, ≤40-char
    ``[a-z0-9_-]`` slug, else raise :class:`InvalidProviderKey`.

    Guards the jsonb path segment (defense in depth — it is also bound) and
    rejects junk before a pointless write.
    """
    if not provider_key or not isinstance(provider_key, str):
        raise InvalidProviderKey("provider_key must be a non-empty string")
    if len(provider_key) > _MAX_KEY_LEN:
        raise InvalidProviderKey(f"provider_key exceeds {_MAX_KEY_LEN} chars")
    if not _PROVIDER_KEY_RE.match(provider_key):
        raise InvalidProviderKey("provider_key must match ^[a-z0-9_-]+$")
    return provider_key


def _health_entry(status: str, detail: str) -> dict:
    """Build the stored value dict — status normalized to ``ok``/``fail``,
    detail truncated to 300 chars, ``tested_at`` = now (UTC, ISO-8601)."""
    return {
        "status": "ok" if status == "ok" else "fail",
        "detail": (detail or "")[:_MAX_DETAIL_LEN],
        "tested_at": datetime.now(timezone.utc).isoformat(),
    }


async def persist_provider_health(
    user_id: str, provider_key: str, status: str, detail: str = ""
) -> bool:
    """Best-effort write of a single provider's health entry. Never raises.

    Validates the key, ensures the engine is configured, then runs one atomic
    ``UPDATE ... jsonb_set`` via the committing ``write_scope()`` ORM session.
    Any failure — bad key, engine unconfigured, DB error, no row — is swallowed
    with a warning and returns ``False``; the connection test it decorates must
    never fail because telemetry could not be stored.

    Returns ``True`` when a row was updated, ``False`` otherwise.
    """
    try:
        validate_provider_key(provider_key)

        from app.core.cache import user_settings_cache
        from app.db import engine as db_engine
        from app.db.session import write_scope

        if not db_engine.is_configured():
            logger.warning(
                "provider-health persist skipped: engine not configured "
                "(user_id={})",
                user_id,
            )
            return False

        entry = _health_entry(status, detail)
        async with write_scope() as session:
            result = await session.execute(
                _upsert_health_stmt(user_id, provider_key, entry)
            )
            rowcount = result.rowcount
        # settings_json changed under the repo's 30s cache — drop the cached
        # copy so the next GET /ai/settings reflects the fresh health entry.
        user_settings_cache.invalidate(user_id)
        return bool(rowcount)
    except Exception as exc:  # noqa: BLE001 — telemetry must not break caller
        logger.warning(
            "provider-health persist failed (best-effort) "
            "user_id={} provider={}: {}",
            user_id,
            provider_key,
            exc,
        )
        return False
