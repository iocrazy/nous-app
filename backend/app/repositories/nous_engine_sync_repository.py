# backend/app/repositories/nous_engine_sync_repository.py

"""Data access for the nous-engine model sync (``services/ai/nous_engine_sync``).

Separate from ``NousModelRepository`` on purpose, for one reason: credentials.
The general repository reveals ``api_key`` on every read and re-encrypts it on
every write (which needs ``NOUS_TOKEN_ENCRYPTION_KEY``). A synced row must carry
the SAME stored credential as the engine row it was copied from, so here the
``enc:v1:`` value is read and written verbatim — exactly what migration 502 did
in SQL. The plaintext never passes through this module.

Writes raise (the service records the failure per service and keeps going);
reads raise too — the caller decides what a failed read means.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import AiModelPrices, NousModels

NOUS_ENGINE_PROVIDER = "nous"

# Columns a sync reads from engine rows: identity, the credential/endpoint set
# a new row copies, and the fields an existing row may have updated (window,
# enabled flag, ready-driven ok/idle status).
_ENGINE_ROW_COLS = (
    NousModels.id,
    NousModels.name,
    NousModels.type,
    NousModels.actual_model,
    NousModels.api_key,
    NousModels.app_id,
    NousModels.base_url,
    NousModels.pricing_type,
    NousModels.pricing_value,
    NousModels.is_enabled,
    NousModels.sort_order,
    NousModels.owner_user_id,
    NousModels.context_window_tokens,
    NousModels.last_test_status,
)

# ``last_test_detail`` for a ready-driven status — the same strings the hourly
# readiness probe writes (``nous_model_health._probe_nous_engine_readiness``),
# so the admin sees one vocabulary whichever writer ran last.
_READY_DETAIL = "loaded"


class NousEngineSyncRepository:
    """Reads/writes the nous-engine slice of ``nous_models`` + its zero prices."""

    async def list_engine_rows(self) -> List[Dict[str, Any]]:
        """Every ``actual_provider='nous'`` row, ``api_key`` left as stored."""
        stmt = (
            select(*_ENGINE_ROW_COLS)
            .where(NousModels.actual_provider == NOUS_ENGINE_PROVIDER)
            .order_by(NousModels.sort_order, NousModels.id)
        )
        async with read_scope() as session:
            result = await session.execute(stmt)
            return [dict(m) for m in result.mappings().all()]

    async def names_taken(self, names: Sequence[str]) -> set[str]:
        """Which of ``names`` already exist anywhere in the catalog."""
        if not names:
            return set()
        async with read_scope() as session:
            result = await session.execute(
                select(NousModels.name).where(NousModels.name.in_(list(names)))
            )
            return set(result.scalars().all())

    async def max_sort_order(self) -> int:
        async with read_scope() as session:
            result = await session.execute(select(func.max(NousModels.sort_order)))
            return int(result.scalar() or 0)

    async def insert_engine_row(
        self, values: Dict[str, Any], *, supports_vision: bool
    ) -> Dict[str, Any]:
        """Insert one catalog row and its zero price row in ONE transaction.

        The price row is keyed by the catalog name with ``provider='nous'``
        (mig 502 §4 shape): self-hosted runs are recorded with provider NULL,
        so the recorder finds the price by model alone. It is written only when
        no price row for that name exists yet — a zero row must never shadow a
        real price someone already set. ``ON CONFLICT DO NOTHING`` on the
        ``(model, provider, effective_at)`` key covers the concurrent case.
        """
        async with write_scope() as session:
            result = await session.execute(
                pg_insert(NousModels)
                .values(**values)
                .returning(NousModels.id, NousModels.name)
            )
            created = dict(result.mappings().one())
            priced = await session.execute(
                select(AiModelPrices.id)
                .where(AiModelPrices.model == created["name"])
                .limit(1)
            )
            if priced.first() is None:
                await session.execute(
                    pg_insert(AiModelPrices)
                    .values(
                        model=created["name"],
                        provider=NOUS_ENGINE_PROVIDER,
                        prompt_cents_per_1k=0,
                        completion_cents_per_1k=0,
                        supports_vision=supports_vision,
                    )
                    .on_conflict_do_nothing(
                        constraint="ai_model_prices_model_provider_effective_at_key"
                    )
                )
            return created

    async def set_context_window(self, row_id: int, tokens: int) -> bool:
        """Write ``context_window_tokens`` on one row; True when a row matched."""
        async with write_scope() as session:
            result = await session.execute(
                update(NousModels)
                .where(NousModels.id == int(row_id))
                .values(context_window_tokens=tokens, updated_at=func.now())
                .returning(NousModels.id)
            )
            return result.first() is not None

    async def disable_rows(self, ids: Sequence[int]) -> List[str]:
        """Disable the given rows in ONE transaction; returns the names that
        were actually flipped (already-disabled rows are not re-written)."""
        if not ids:
            return []
        async with write_scope() as session:
            result = await session.execute(
                update(NousModels)
                .where(NousModels.id.in_([int(i) for i in ids]))
                .where(NousModels.is_enabled.is_(True))
                .values(is_enabled=False, updated_at=func.now())
                .returning(NousModels.name)
            )
            return list(result.scalars().all())

    async def record_ready(self, row_id: int, ready: bool) -> bool:
        """Mirror the engine's ``ready`` into ``last_test_*``: ``ok``/``loaded``
        or ``idle``/``authorized, not loaded``. Same write shape as
        ``NousModelRepository.record_test_result`` — only the last_test_*
        columns + ``last_tested_at``, ``code`` cleared, ``updated_at`` untouched
        (a status reading is not an edit). True when a row matched."""
        from app.services.ai.nous_model_health import _IDLE_DETAIL

        async with write_scope() as session:
            result = await session.execute(
                update(NousModels)
                .where(NousModels.id == int(row_id))
                .values(
                    last_test_status="ok" if ready else "idle",
                    last_test_detail=_READY_DETAIL if ready else _IDLE_DETAIL,
                    last_test_code=None,
                    last_tested_at=func.now(),
                )
                .returning(NousModels.id)
            )
            return result.first() is not None


def get_nous_engine_sync_repository() -> NousEngineSyncRepository:
    return NousEngineSyncRepository()
