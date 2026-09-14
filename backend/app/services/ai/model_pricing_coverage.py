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

3b §3.2 起这道守卫也覆盖 image / video：它们的花费来自
``ai_model_prices.per_call_cents``，没有那一行，生成的图在血缘与预算里都是
'—'。两个价目面各查各的，按行的 ``type`` 选（见 ``_PRICE_FACE_BY_TYPE``）。

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

# LLM 走 RunRecorder 的每千 token 价，image / video 走
# ``ai_model_prices.per_call_cents``（3b §3.2）；tts / asr / embedding 仍由别的
# 路径计费，留在外面——它们在这里只会是永久的假 "missing"。
PRICED_MODEL_TYPES = frozenset({"llm", "image", "video"})

# 每种 type 看价目表的哪一面。两个面不可互换：一个只配了每千 token 价的模型，
# 在图片那一行必须报 missing，否则「已配价」这个标签就在说谎。
_PRICE_FACE_BY_TYPE = {"llm": "token", "image": "per_call", "video": "per_call"}


def price_coverage_for(
    row: Mapping[str, Any], priced_models: Optional[Mapping[str, Iterable[str]]]
) -> str:
    """Coverage status for one catalog row given the priced model keys.

    ``priced_models`` maps a price FACE (``token`` / ``per_call``) to the model
    keys that have a row on it; the row's ``type`` picks the face. It is
    ``None`` when the price table could not be read — every applicable row then
    reports ``unknown`` rather than ``missing``.
    """
    face = _PRICE_FACE_BY_TYPE.get(row.get("type"))
    if face is None:
        return "not_applicable"
    if row.get("actual_provider") in LOCAL_ENGINE_PROVIDERS:
        # Runs on the user's own machine under their own subscription; there is
        # no price for the platform to snapshot.
        return "not_applicable"
    if priced_models is None:
        return "unknown"
    priced = set(priced_models.get(face) or ())
    keys = {row.get("actual_model") or "", row.get("name") or ""} - {""}
    return "priced" if keys & priced else "missing"


def _priced_models_select_stmt():
    """Column-level select (row-shape lesson) — one string per distinct model."""
    from sqlalchemy import select

    from app.models import AiModelPrices

    return select(AiModelPrices.model).distinct()


def _per_call_priced_models_select_stmt():
    """同上，但只要**配了每次调用价**的模型（3b §3.2）。

    ``per_call_cents IS NOT NULL`` 不能省：每个 LLM 行都有每千 token 价，不过滤
    的话一个同名媒体模型会被判成「已配价」，而它生成的图在血缘里仍然是 '—'。
    """
    from sqlalchemy import select

    from app.models import AiModelPrices

    return (
        select(AiModelPrices.model)
        .where(AiModelPrices.per_call_cents.isnot(None))
        .distinct()
    )


async def load_priced_models() -> Optional[dict[str, set[str]]]:
    """两个价目面的模型键，或 ``None``（读失败）。

    ``{"token": …, "per_call": …}`` —— 前者是有每千 token 价的（LLM 走它），
    后者是有 ``per_call_cents`` 的（image / video 走它）。两条语句共用一次
    session：这一页每次列表只该付一次连接的钱。

    Never raises: the catalog list must still render when the price table is
    unreachable, and the callers turn ``None`` into ``unknown`` per row.
    """
    from app.db.session import read_scope  # deferred — codebase convention

    try:
        async with read_scope() as session:
            token = (
                (await session.execute(_priced_models_select_stmt())).scalars().all()
            )
            per_call = (
                (await session.execute(_per_call_priced_models_select_stmt()))
                .scalars()
                .all()
            )
        return {
            "token": {str(m) for m in token if m},
            "per_call": {str(m) for m in per_call if m},
        }
    except Exception as e:  # noqa: BLE001 — diagnostic must degrade, not raise
        logger.warning(f"[model_pricing_coverage] price table read failed: {e}")
        return None
