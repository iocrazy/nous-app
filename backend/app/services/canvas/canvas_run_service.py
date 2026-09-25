"""Canvas-mode prompt runner (Phase 2 Day 6-8).

Executes one prompt-node Run by calling the configured LLM adapter
directly. Stays out of the AgentRunner pipeline so we don't drag
skill/tool/history machinery into what is conceptually a single-turn
creative generation.

Provider mapping:
    None or ""         → DB default text model  (_default_text_model — the
                                                 first enabled ``llm`` row in
                                                 the ``nous_models`` catalog,
                                                 never a hardcoded slug)
    "qwen/<model>"     → <model>
    "claude/<model>"   → <model>                (e.g. claude-sonnet-4-6)
    "deepseek/<model>" → <model>
    "doubao/<model>"   → <model>
    "<model>"          → <model>                (direct; matches adapter
                                                 factory's prefix dispatch)

`agent_id` is plumbed through for future per-agent persona injection
(Phase 4.5). For now it just flows into the system_message via a
non-binding "Run under agent X" hint.

Failures are returned in-band via `ok=False` + `error`; we never raise
to the router so the route always returns 200 (the frontend
distinguishes ok vs failed by the body).
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional
from uuid import UUID

from loguru import logger

from app.schemas.canvas_run import CanvasPromptRunResult

# ``nous/<workflow>`` slugs used to route to the legacy nous-center workflow
# bridge. That bridge was retired on 2026-09-24 (never configured in
# production); an old canvas node still carrying such a slug gets a typed
# in-band failure instead of being misread as a model id.
RETIRED_NOUS_PREFIX = "nous/"
RETIRED_NOUS_ERROR = (
    "nous-center workflows were retired; pick a text model from the catalog"
)
SYSTEM_MESSAGE = (
    "You are a smart-canvas prompt runner. The user gave you a single "
    "creative prompt; reply with the rendered content directly, no "
    "preamble, no scaffolding. Do not call tools."
)
TEMPERATURE = 0.7
MAX_TOKENS = 2048


def _resolve_model(provider_slug: Optional[str]) -> str:
    """Parse an explicit model id out of a provider slug.

    Returns ``""`` when the slug names no explicit model (``None``/empty, or a
    bare ``"<provider>/"`` with no tail). An empty return is a signal — the
    caller falls back to :meth:`CanvasRunService._default_text_model`, the
    DB-backed catalog default. We deliberately never bake a hardcoded model
    slug in here (config→DB 铁律): a constant like the old ``qwen-plus`` drifts
    out of the platform catalog and makes the default Run path raise
    ProviderNotConfiguredError.
    """
    if not provider_slug:
        return ""
    if "/" in provider_slug:
        _, _, model = provider_slug.partition("/")
        return model  # "" when tail empty → caller resolves the DB default
    return provider_slug


def _compose_system_message(agent_id: Optional[str]) -> str:
    if agent_id:
        return f"{SYSTEM_MESSAGE}\n\n[Acting under agent {agent_id}]"
    return SYSTEM_MESSAGE


async def _compose_system_message_with_agent(agent_id: Optional[str]) -> str:
    """Real per-agent persona injection (character canvas CC3 — the Phase 4.5
    slot the placeholder always pointed at).

    When ``agent_id`` resolves to an ai_agents row, its IDENTITY + SOUL are
    prepended so the run actually speaks as that agent (AGENT.md is the
    task-protocol doc — the canvas prompt body carries the task here, so it
    is deliberately not injected). Any miss (non-UUID id, unknown agent, repo
    error) degrades to the legacy placeholder — never fails the run.
    """
    if not agent_id:
        return SYSTEM_MESSAGE
    try:
        from uuid import UUID

        from app.repositories.agent_repository import AgentRepository

        agent = await AgentRepository().get_by_id(UUID(str(agent_id)))
    except Exception:  # noqa: BLE001 — resolution is best-effort
        agent = None
    if not agent:
        return _compose_system_message(agent_id)
    parts = [
        str(agent.get("identity_md") or "").strip(),
        str(agent.get("soul_md") or "").strip(),
    ]
    persona = "\n\n".join(p for p in parts if p)
    if not persona:
        return _compose_system_message(agent_id)
    return f"{persona}\n\n{SYSTEM_MESSAGE}"


# Data-payload keys (snake + camel) for image_gen params. The node's domain
# payload nests under ``data`` in a React Flow node; we also accept a flat dict.
_IMAGE_GEN_PROMPT_KEYS = ("prompt",)
_IMAGE_GEN_MODEL_KEYS = ("model",)
_IMAGE_GEN_PROVIDER_KEYS = ("provider_name", "providerName", "provider")
_IMAGE_GEN_ASPECT_KEYS = ("aspect_ratio", "aspectRatio")
_IMAGE_GEN_REFERENCE_KEYS = ("reference_image_url", "referenceImageUrl")
_IMAGE_GEN_CHARACTER_KEYS = ("character_ids", "characterIds")
_DEFAULT_ASPECT_RATIO = "16:9"


def _image_gen_data(node: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Pull the domain payload off a node — nested ``data`` or flat dict."""
    if not isinstance(node, Mapping):
        return {}
    data = node.get("data")
    if isinstance(data, Mapping):
        return data
    return node


def _first_str(data: Mapping[str, Any], keys: tuple[str, ...]) -> Optional[str]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_image_gen_params(node: Optional[Mapping[str, Any]]) -> dict:
    """Normalise image_gen params out of a node payload.

    Returns a dict with: prompt (str), model (str), provider_name (str),
    aspect_ratio (str), reference_image_url (Optional[str]),
    character_ids (Optional[list[str]]). Missing string params come back as
    "" so the caller's clear-error checks (and generate_image's own provider
    lookup) fire predictably.
    """
    data = _image_gen_data(node)

    character_ids: Optional[List[str]] = None
    for key in _IMAGE_GEN_CHARACTER_KEYS:
        value = data.get(key)
        if isinstance(value, (list, tuple)):
            character_ids = [str(c) for c in value if c]
            break

    return {
        "prompt": _first_str(data, _IMAGE_GEN_PROMPT_KEYS) or "",
        "model": _first_str(data, _IMAGE_GEN_MODEL_KEYS) or "",
        "provider_name": _first_str(data, _IMAGE_GEN_PROVIDER_KEYS) or "",
        "aspect_ratio": _first_str(data, _IMAGE_GEN_ASPECT_KEYS)
        or _DEFAULT_ASPECT_RATIO,
        "reference_image_url": _first_str(data, _IMAGE_GEN_REFERENCE_KEYS),
        "character_ids": character_ids,
    }


# Data-payload keys (snake + camel) for video_gen params. In a real graph the
# source image arrives from an upstream image node; for now it is read from the
# node's own ``data``. The video provider registry is distinct from image.
_VIDEO_GEN_SOURCE_KEYS = ("source_image_url", "sourceImageUrl")
_VIDEO_GEN_PROMPT_KEYS = ("prompt",)
_VIDEO_GEN_MODEL_KEYS = ("model",)
_VIDEO_GEN_PROVIDER_KEYS = ("provider_name", "providerName", "provider")
_VIDEO_GEN_DURATION_KEYS = ("duration_seconds", "durationSeconds")
_VIDEO_GEN_MOTION_KEYS = ("motion_intensity", "motionIntensity")
_DEFAULT_DURATION_SECONDS = 5.0
_DEFAULT_MOTION_INTENSITY = "medium"


def _first_float(
    data: Mapping[str, Any], keys: tuple[str, ...], default: float
) -> float:
    """Pull the first numeric value for ``keys``, coercing str/int → float.

    Falls back to ``default`` when no key holds a parseable number, so a
    malformed payload never blows up the run (it just uses the default clip
    length).
    """
    for key in keys:
        value = data.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str) and value.strip():
            try:
                return float(value.strip())
            except ValueError:
                continue
    return default


def _extract_video_gen_params(node: Optional[Mapping[str, Any]]) -> dict:
    """Normalise video_gen params out of a node payload.

    Returns a dict with: source_image_url (Optional[str] — None when absent so
    the caller's clear-error check fires), prompt (str), model (str),
    provider_name (str), duration_seconds (float, default 5.0),
    motion_intensity (str, default "medium"). Reuses the image_gen node-data
    accessor (nested ``data`` or flat dict).
    """
    data = _image_gen_data(node)

    return {
        "source_image_url": _first_str(data, _VIDEO_GEN_SOURCE_KEYS),
        "prompt": _first_str(data, _VIDEO_GEN_PROMPT_KEYS) or "",
        "model": _first_str(data, _VIDEO_GEN_MODEL_KEYS) or "",
        "provider_name": _first_str(data, _VIDEO_GEN_PROVIDER_KEYS) or "",
        "duration_seconds": _first_float(
            data, _VIDEO_GEN_DURATION_KEYS, _DEFAULT_DURATION_SECONDS
        ),
        "motion_intensity": _first_str(data, _VIDEO_GEN_MOTION_KEYS)
        or _DEFAULT_MOTION_INTENSITY,
    }


# Probe statuses a "Catalog default" may land on, best first. ``idle`` (local
# nous-engine row authorized but not loaded — a real chat got 503 "not loaded"
# on 2026-09-24) and ``fail`` are skipped: the user pickers grey / hide those
# rows, so the implicit default must not quietly pick one either.
_DEFAULT_STATUS_RANK = {"ok": 0, None: 1, "not_probed": 1}


def _row_name(row: Dict[str, Any]) -> Optional[str]:
    name = row.get("name")
    return name.strip() if isinstance(name, str) and name.strip() else None


def _pick_default_row(rows: List[Dict[str, Any]]) -> Optional[str]:
    """``ok`` row first, then never-probed / ``not_probed``, catalog order
    within a rank. When every named row is ``idle``/``fail``, keep the old
    first-row behaviour (a guess beats no model) and say so in a WARN."""
    named = [(row, n) for row in rows if (n := _row_name(row))]
    ranked = [
        (_DEFAULT_STATUS_RANK[row.get("last_test_status")], i, n)
        for i, (row, n) in enumerate(named)
        if row.get("last_test_status") in _DEFAULT_STATUS_RANK
    ]
    if ranked:
        return min(ranked)[2]
    if not named:
        return None
    row, name = named[0]
    logger.warning(
        "canvas: no enabled llm row is ok or unprobed; default falls back to "
        f"{name!r} (last_test_status={row.get('last_test_status')!r})"
    )
    return name


class CanvasRunService:
    """Single entrypoint: ``await svc.run_prompt(...)`` returns a result."""

    async def _get_adapter(self, model: str):
        # DB-only credential resolution (铁律 2026-07-07): platform
        # ``nous_models`` catalog → ProviderNotConfiguredError. No env.
        from app.services.ai.providers.ai_provider_helpers import resolve_db_adapter

        return await resolve_db_adapter(model, "canvas")

    async def _default_text_model(self) -> str:
        """The catalog model an empty ``provider_slug`` resolves to.

        Best enabled ``llm`` row in the platform ``nous_models`` catalog (see
        ``_pick_default_row`` for the probe-status ranking), falling back to
        the governed maintenance model (itself a catalog entry). DB-only, so
        the default text Run always names a model the platform actually has
        configured — the root cause of the 2026-07-12
        "default prompt won't run" report was a hardcoded ``qwen-plus`` that
        the catalog no longer carries.
        """
        from app.repositories.nous_model_repository import (
            get_nous_model_repository,
        )
        from app.services.ai.providers.ai_provider_helpers import (
            get_maintenance_model,
        )

        try:
            rows = await get_nous_model_repository().list_enabled("llm")
        except Exception:  # noqa: BLE001 — degrade to the governed default
            logger.opt(exception=True).warning(
                "canvas: enabled-llm catalog read failed; using maintenance model"
            )
            rows = []
        picked = _pick_default_row(rows)
        if picked is not None:
            return picked
        return await get_maintenance_model()

    async def run_prompt(
        self,
        *,
        body: str,
        provider_slug: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> CanvasPromptRunResult:
        body = (body or "").strip()

        # Retired nous-center slugs fail loudly rather than being parsed as a
        # bare model id (which would surface as a confusing adapter error).
        if provider_slug and provider_slug.startswith(RETIRED_NOUS_PREFIX):
            return CanvasPromptRunResult(ok=False, text="", error=RETIRED_NOUS_ERROR)

        if not body:
            return CanvasPromptRunResult(
                ok=False, text="", error="prompt body is empty"
            )

        # Empty parse → DB catalog default (never a hardcoded slug).
        model = _resolve_model(provider_slug) or await self._default_text_model()
        system_message = await _compose_system_message_with_agent(agent_id)

        try:
            adapter = await self._get_adapter(model)
        except Exception as exc:
            logger.exception("canvas run: adapter init failed for {}", model)
            return CanvasPromptRunResult(
                ok=False,
                text="",
                error=f"adapter init failed: {exc}",
            )

        composed = self._minimal_composed(model=model, system_message=system_message)
        messages: List[dict] = [{"role": "user", "content": body}]

        try:
            response = await adapter.call(composed, messages)
        except Exception as exc:
            logger.exception("canvas run: adapter call failed for {}", model)
            return CanvasPromptRunResult(
                ok=False, text="", error=f"adapter call failed: {exc}"
            )

        text = self._extract_text(response)
        return CanvasPromptRunResult(ok=True, text=text, error=None)

    @staticmethod
    def _extract_text(response: dict) -> str:
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return ""
        if not isinstance(content, str):
            return ""
        return content

    @staticmethod
    def _minimal_composed(*, model: str, system_message: str):
        """Build a ComposedSystemPrompt with the bare minimum the
        adapter call needs. Skips skill_manifest / tools entirely."""
        from app.schemas.ai_library import ComposedSystemPrompt

        return ComposedSystemPrompt(
            agent_id=UUID(int=0),  # sentinel — distinguishes from real agents
            agent_slug="canvas_prompt_run",
            model=model,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            system_message=system_message,
            tools=[],
            skill_manifest=[],
            cache_fingerprint=f"canvas_prompt_run_{model}",
            prefix_fingerprint=f"canvas_prompt_run_{model}",
            dynamic_fingerprint=f"canvas_prompt_run_{model}",
            recalled_memory_ids=[],
        )
