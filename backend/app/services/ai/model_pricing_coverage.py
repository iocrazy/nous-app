# backend/app/services/ai/model_pricing_coverage.py

"""Does a catalog model have a price row? — the guard behind mig 454.

Why this exists: ``RunRecorder`` snapshots ``ai_model_prices`` at run start and
leaves ``cost_cents`` NULL when no row matches. Nothing else notices — the
Usage dashboard shows '—', the P4 budget hook never fires — so a model added to
the catalog without a price row silently drops out of cost tracking. On
2026-09-05 that was 6 of 7 models in use for the previous 90 days.

Orthogonal to the connectivity probe (``mediahub_model_health``): a model can be
reachable AND unpriced, so this is its own field, never folded into
``last_test_status`` (the "orthogonal results report independently" rule).

Matching mirrors what the recorder actually looks up, observed on production:
most runs record ``model = actual_model`` (``doubao-seed-2-0-lite-260428``), but
the OpenAI-compatible self-hosted path records the catalog ``name``
(``nous-qwen3-llm``) with an empty provider, and an empty provider relaxes the
recorder's lookup to model-only. So a row is "priced" when ANY price row's
``model`` equals either key, provider ignored. Deliberately lenient: a false
"missing" would teach admins to ignore the tag (the 2026-08-14 red-light lesson).

``unknown`` is what a failed lookup reports — a diagnostic that could not run
must not hand back a verdict (``reference-self-concealing-failure-modes``).
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from loguru import logger

from app.services.ai.mediahub_model_health import LOCAL_ENGINE_PROVIDERS

# Closed enum. The admin UI keys its tag colours off these exact strings.
PRICE_COVERAGE = ("priced", "missing", "not_applicable", "unknown")

# Only per-token LLM traffic goes through RunRecorder's price snapshot. Image /
# video / tts / asr / embedding rows are billed by other paths (or not at all)
# and would be permanent false "missing" tags here.
PRICED_MODEL_TYPES = frozenset({"llm"})


def price_coverage_for(
    row: Mapping[str, Any], priced_models: Optional[Iterable[str]]
) -> str:
    """Coverage status for one catalog row given the set of priced model keys.

    ``priced_models`` is ``None`` when the price table could not be read —
    every applicable row then reports ``unknown`` rather than ``missing``.
    """
    if row.get("type") not in PRICED_MODEL_TYPES:
        return "not_applicable"
    if row.get("actual_provider") in LOCAL_ENGINE_PROVIDERS:
        # Runs on the user's own machine under their own subscription; there is
        # no per-token price for the platform to snapshot.
        return "not_applicable"
    if priced_models is None:
        return "unknown"
    priced = set(priced_models)
    keys = {row.get("actual_model") or "", row.get("name") or ""} - {""}
    return "priced" if keys & priced else "missing"


def _priced_models_select_stmt():
    """Column-level select (row-shape lesson) — one string per distinct model."""
    from sqlalchemy import select

    from app.models import AiModelPrices

    return select(AiModelPrices.model).distinct()


async def load_priced_models() -> Optional[set[str]]:
    """All ``ai_model_prices.model`` keys, or ``None`` if the read failed.

    Never raises: the catalog list must still render when the price table is
    unreachable, and the callers turn ``None`` into ``unknown`` per row.
    """
    from app.db.session import read_scope  # deferred — codebase convention

    try:
        async with read_scope() as session:
            rows = (await session.execute(_priced_models_select_stmt())).scalars().all()
        return {str(m) for m in rows if m}
    except Exception as e:  # noqa: BLE001 — diagnostic must degrade, not raise
        logger.warning(f"[model_pricing_coverage] price table read failed: {e}")
        return None
