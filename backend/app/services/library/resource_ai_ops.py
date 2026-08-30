"""Reusable AI operations over a stored resource — prompt translate + caption.

Extracted from ``app/api/resources_ai_router.py`` when the asset library grew
its own prompt translate / regenerate endpoints. Both surfaces must drive the
SAME agents (the user's assigned ``translation`` and ``caption`` agents,
resolved through ``resolve_task_ai_config``); a second implementation would be
a second set of provider-resolution bugs, and importing a router from a service
is the layering inversion this file exists to avoid.

What moved here verbatim (behaviour unchanged, the router keeps calling it):

* :func:`build_translate_plan` — which prompt fields have a source to translate.
  Grew one parameter (``field_pairs``) so the asset shelf can point it at
  ``prompt_positive`` / ``prompt_positive_zh`` instead of ``gen_prompt`` /
  ``gen_prompt_zh``. Default = the resources columns, so the existing call site
  is byte-identical.
* :func:`translate_fields` — resolve the translation agent once, then one
  ``TranslateService.translate`` per plan entry.

What is NEW here rather than moved:

* :func:`caption_resource_for_caller` — the vision caption call, run
  SYNCHRONOUSLY. The resources surface reaches the same agent through the
  ``caption_asset`` DBOS workflow (``resources_ai_router._single_asset_ai``),
  because there it writes ``resources.gen_prompt`` and the caller only needs a
  task id back. The asset shelf needs the RESULT in the response (it writes
  ``assets.prompt_positive`` and answers with the updated asset), so the
  dispatch helper is not what it can reuse — the agent call underneath it is.
  ``_single_asset_ai`` therefore stays in the router: nothing here calls it.

  ⚠️ That makes the asset regenerate endpoint a long request (a vision call,
  seconds to tens of seconds) with no Task Center row. If it needs progress or
  cancellation later, the move is to give it its own workflow, not to make this
  function slower to fail.

**Provider failures propagate raw** (``AllModelsFailed`` / ``LLMCallError``).
Each caller maps them: the resources router lets them reach the typed provider
surface (``core/provider_errors.py`` → 503/502/504), the assets service turns
them into ``AssetError(503, ...)``. Normalizing here would take that choice
away from both. :func:`is_provider_failure` is how a caller recognizes one
without importing the LLM stack itself.

Every heavy import is function-local on purpose: this module is imported by
``assets_service``, which is imported by the assets router, and none of those
should drag the LLM / DBOS stacks in at process start.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger

# (en_field, zh_field) for ``resources``. The asset shelf passes its own pairs.
PROMPT_FIELD_PAIRS: List[Tuple[str, str]] = [
    ("gen_prompt", "gen_prompt_zh"),
    ("gen_prompt_negative", "gen_prompt_negative_zh"),
]


class CaptionSourceUnavailable(Exception):
    """There is nothing for a vision model to look at (a 4xx, not a 5xx).

    An album (captioned per slide), an audio row, a video whose cover was never
    downloaded, an image with no stored file. Kept distinct from
    :class:`CaptionAgentFailed` because the two need OPPOSITE user actions —
    "this file will never be captionable" vs "the provider is down, retry" —
    and one code for both sends every user to Settings → AI.
    """


class CaptionAgentFailed(Exception):
    """The agent ran and produced nothing usable (a 5xx)."""


class CaptionAgentPaused(Exception):
    """The caption agent is paused (``ai_agents.paused_reason``) — the run was
    refused pre-flight by ``RunRecorder``, before any model call.

    Its own type because the fix is a DIFFERENT action from every other 5xx
    here: resume the agent (or raise its budget), not "check whether the model
    supports vision". ``CaptionService.caption`` swallows ``AgentPausedError``
    into a plain ``None``, so this is recovered from the agent row rather than
    from the exception — see :func:`_agent_paused_reason`.
    """


def is_provider_failure(exc: BaseException) -> bool:
    """True for the LLM-class failures every AI caller must surface verbatim.

    A predicate rather than an exported tuple so the LLM modules stay a
    function-local import; callers write
    ``except Exception as e: ... if is_provider_failure(e)`` and keep their own
    bugs 500-ing instead of disguising them as a provider outage.
    """
    from app.services.ai.llm.llm_fallback_chain import AllModelsFailed
    from app.services.ai.llm.llm_retry_middleware import LLMCallError

    return isinstance(exc, (AllModelsFailed, LLMCallError))


def build_translate_plan(
    resource: Dict[str, Any],
    target_lang: str,
    field_pairs: Optional[Sequence[Tuple[str, str]]] = None,
) -> List[Tuple[str, str, str]]:
    """(source_field, target_field, source_text) per non-empty source side.

    target_lang='zh' reads the EN columns; 'en' reads the ZH columns.
    Empty/whitespace sources are skipped so a positive-only asset still
    translates cleanly.
    """
    plan: List[Tuple[str, str, str]] = []
    for en_field, zh_field in field_pairs or PROMPT_FIELD_PAIRS:
        source_field, target_field = (
            (en_field, zh_field) if target_lang == "zh" else (zh_field, en_field)
        )
        text = (resource.get(source_field) or "").strip()
        if text:
            plan.append((source_field, target_field, text))
    return plan


async def translate_fields(
    plan: Sequence[Tuple[str, str, str]],
    *,
    target_lang: str,
    user_id: Optional[str],
    resource_id: Optional[str] = None,
) -> Dict[str, str]:
    """Run the user's translation agent over a plan; target_field → new text.

    A field whose translation came back empty is ABSENT from the result rather
    than present-and-blank: the callers write the returned dict straight onto
    the row, and a blank value would erase the target the run failed to fill.

    ``AllModelsFailed`` / ``LLMCallError`` propagate (see the module docstring).
    """
    # resolve_task_ai_config directly, NOT the resolve_translate_provider_config
    # tuple shim — the shim narrows to 4 positional fields and drops
    # ``fallback_models``, which is exactly what the chain needs
    # (spec 2026-08-12-batch-fallback-rollout §1-F3, mirrors
    # caption_asset/classify_asset's resolve steps).
    from app.services.ai.providers.ai_provider_helpers import (
        DEFAULT_TRANSLATE_AGENT_SLUG,
        resolve_task_ai_config,
    )
    from app.services.ai.translate import TranslateService

    cfg = await resolve_task_ai_config(
        user_id, "translation", DEFAULT_TRANSLATE_AGENT_SLUG
    )
    svc = TranslateService(
        provider_key=cfg.provider_key,
        provider_config=cfg.provider_config or {},
        agent_slug=cfg.agent_slug,
    )
    patch: Dict[str, str] = {}
    for _source_field, target_field, source_text in plan:
        translated = await svc.translate(
            text=source_text,
            target_lang=target_lang,
            user_id=user_id,
            resource_id=resource_id,
            fallback_models=list(cfg.fallback_models),
        )
        if translated:
            patch[target_field] = translated
    return patch


async def caption_resource_for_caller(resource_id: str, user_id: str) -> Dict[str, str]:
    """Reverse-engineer a bilingual prompt from a resource's image, in-request.

    Same agent and same source ladder as the ``caption_asset`` workflow: an
    image captions its own bytes, a video captions its downloaded cover
    (``caption_source`` owns that ladder), an album has no single image and is
    refused. Returns ``{"en"?: str, "zh"?: str}`` — at least one is present.

    Reads the row through ``get_resource_by_id_for_caller``, NOT
    ``get_resource_by_id``: the latter needs an ambient ``request_scope`` the
    /assets router does not open, and the former carries the
    owner-or-teammate visibility predicate this path needs anyway.

    Raises :class:`CaptionSourceUnavailable` when there is nothing to look at,
    :class:`CaptionAgentPaused` when the agent is paused and
    :class:`CaptionAgentFailed` when it ran and produced nothing; provider
    failures propagate raw.
    """
    from app.repositories.resources_repository import ResourcesRepository
    from app.services.ai.caption import CaptionService
    from app.services.ai.caption_source import (
        caption_gate_reason,
        resolve_caption_source,
    )
    from app.services.ai.providers.ai_provider_helpers import (
        DEFAULT_CAPTION_AGENT_SLUG,
        resolve_task_ai_config,
    )
    from app.services.library.media_storage import materialize

    resource = await ResourcesRepository().get_resource_by_id_for_caller(
        str(resource_id), str(user_id)
    )
    if not resource:
        raise CaptionSourceUnavailable("File not found")

    source_path = await resolve_caption_source(resource)
    if not source_path:
        # caption_gate_reason says something DIFFERENT per branch (album /
        # unsupported type / video with no downloaded cover); collapsing them
        # into one message is what made the download surfaces look broken
        # rather than unsupported.
        raise CaptionSourceUnavailable(
            await caption_gate_reason(resource)
            or "This file has no image to reverse-engineer"
        )

    cfg = await resolve_task_ai_config(user_id, "caption", DEFAULT_CAPTION_AGENT_SLUG)
    service = CaptionService(
        provider_key=cfg.provider_key,
        provider_config=cfg.provider_config or {},
        agent_slug=cfg.agent_slug or "caption",
    )
    async with materialize(source_path) as local_path:
        result = await service.caption(
            file_path=str(local_path),
            user_id=user_id,
            resource_id=str(resource_id),
            fallback_models=list(cfg.fallback_models),
        )

    out: Dict[str, str] = {}
    for key in ("en", "zh"):
        value = ((result or {}).get(key) or "").strip()
        if value:
            out[key] = value
    if not out:
        # Empty is AMBIGUOUS at this layer: a paused agent and a model that
        # answered nothing usable both arrive as a falsy result. Ask the agent
        # row which one it was rather than reporting the more common guess.
        paused = await _agent_paused_reason(cfg.agent_slug or "caption")
        if paused:
            raise CaptionAgentPaused(
                f"The caption agent is paused ({paused}) — resume it in "
                f"Settings → AI"
            )
        logger.warning(
            f"[CaptionResource] agent returned no usable prompt for "
            f"resource {resource_id}"
        )
        raise CaptionAgentFailed(
            "The caption agent returned neither an EN nor a ZH prompt — check "
            "that the model assigned to Caption supports vision and is "
            "reachable (Settings → AI)"
        )
    return out


async def _agent_paused_reason(agent_slug: str) -> Optional[str]:
    """``ai_agents.paused_reason`` for the resolved agent, or None.

    Asked ONLY on the empty-result path. ``RunRecorder`` refuses a paused agent
    pre-flight (``AgentPausedError``), and both ``CaptionService.caption`` and
    ``TranslateService.translate`` catch that and return ``None`` — so at this
    layer "paused" is byte-identical to "the model produced nothing". Reading
    the reason back ('budget' / 'manual') is what separates them.

    A lookup failure is NOT a pause: ``get_by_slug`` already logs and returns
    None on error, and treating that as "not paused" keeps the caller on the
    generic 5xx rather than inventing a pause that may not exist.
    """
    from app.repositories.agent_repository import get_agent_repository

    agent = await get_agent_repository().get_by_slug(agent_slug)
    return (agent or {}).get("paused_reason") or None
