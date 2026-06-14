"""Canvas-mode prompt runner (Phase 2 Day 6-8).

Executes one prompt-node Run by calling the configured LLM adapter
directly. Stays out of the AgentRunner pipeline so we don't drag
skill/tool/history machinery into what is conceptually a single-turn
creative generation.

Provider mapping:
    None or ""         → DEFAULT_MODEL          (qwen-plus)
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

import logging
from typing import Any, List, Mapping, Optional
from uuid import UUID

from app.schemas.canvas_run import CanvasPromptRunResult
from app.services.canvas.classic_dispatch import (
    OP_IMAGE_GEN,
    OP_VIDEO_GEN,
    ClassicDispatchError,
    resolve_classic_dispatch,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen-plus"
# nous-routed runs are always workflow nodes (comfy/image/video) that can run
# minutes — well past the default chat-style NOUS_CENTER_MAX_WAIT_S. Lift the
# poll ceiling for this path; override via NOUS_CENTER_MAX_WAIT_S_WORKFLOW.
DEFAULT_WORKFLOW_MAX_WAIT_S = 600.0
SYSTEM_MESSAGE = (
    "You are a smart-canvas prompt runner. The user gave you a single "
    "creative prompt; reply with the rendered content directly, no "
    "preamble, no scaffolding. Do not call tools."
)
TEMPERATURE = 0.7
MAX_TOKENS = 2048


def _resolve_model(provider_slug: Optional[str]) -> str:
    """Pick the model id the adapter factory should dispatch on."""
    if not provider_slug:
        return DEFAULT_MODEL
    if "/" in provider_slug:
        _, _, model = provider_slug.partition("/")
        return model or DEFAULT_MODEL
    return provider_slug


def _compose_system_message(agent_id: Optional[str]) -> str:
    if agent_id:
        return f"{SYSTEM_MESSAGE}\n\n[Acting under agent {agent_id}]"
    return SYSTEM_MESSAGE


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


class CanvasRunService:
    """Single entrypoint: ``await svc.run_prompt(...)`` returns a result."""

    def __init__(self, settings: Any = None) -> None:
        # Defer the real settings import — keeps unit tests cheap.
        self._settings = settings

    async def _get_settings(self) -> Any:
        if self._settings is not None:
            return self._settings
        from app.core.config import get_settings  # local import

        self._settings = get_settings()
        return self._settings

    async def _get_adapter(self, model: str):
        from app.services.ai.adapters import get_adapter

        settings = await self._get_settings()
        return get_adapter(model=model, settings=settings)

    async def run_prompt(
        self,
        *,
        body: str,
        provider_slug: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> CanvasPromptRunResult:
        body = (body or "").strip()

        # Route to nous-center for workflow-style providers FIRST. Many
        # ComfyUI/nous workflows (comfy/image/video nodes) need no text prompt,
        # so the empty-body guard must NOT apply to this path — a node with a
        # valid workflow_slug but empty prompt is legitimate.
        if provider_slug and provider_slug.startswith("nous/"):
            return await self._run_via_nous(provider_slug, body, agent_id)

        # Non-nous (text-adapter / llm) path requires a prompt body.
        if not body:
            return CanvasPromptRunResult(
                ok=False, text="", error="prompt body is empty"
            )

        model = _resolve_model(provider_slug)
        system_message = _compose_system_message(agent_id)

        try:
            adapter = await self._get_adapter(model)
        except Exception as exc:
            logger.exception("canvas run: adapter init failed for %s", model)
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
            logger.exception("canvas run: adapter call failed for %s", model)
            return CanvasPromptRunResult(
                ok=False, text="", error=f"adapter call failed: {exc}"
            )

        text = self._extract_text(response)
        return CanvasPromptRunResult(ok=True, text=text, error=None)

    # ------------------------------------------------------------------
    # ClassicMode node dispatch (Phase 5a C2)
    # ------------------------------------------------------------------

    async def run_classic_node(
        self,
        *,
        node_type: Optional[str],
        node: Optional[Mapping[str, Any]] = None,
        body: str,
        agent_id: Optional[str] = None,
        node_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> CanvasPromptRunResult:
        """Resolve a classic node to its route (provider vs op) and run it
        synchronously, returning the in-band ok/text/result/error envelope.

        Routes:
          - ``llm``       → bare-model adapter path (``run_prompt``), unchanged.
          - ``comfy``     → ``nous/<workflow_slug>`` route (block-polls
            ``run_nous_workflow`` to terminal), unchanged.
          - ``image_gen`` → direct ``StoryboardAIService.generate_image`` call
            (Phase 5a path B). ``result.image_url`` carries the output.
          - ``video_gen`` → direct ``StoryboardAIService.generate_video`` call
            (Phase 5a path B). ``result.video_url`` carries the output.
          - unknown / literal-sink / missing → ok=False in-band, no dispatch
            (never a silent wrong route).

        ``node_id`` / ``project_id`` are only consumed by op handlers
        (image_gen / video_gen pass them to the gen service for logging + style
        lookup); the provider paths ignore them. Single-synchronous throughout
        — no DBOS workflow is created here.

        NEXT TASK: split adds one more ``op`` branch below.
        """
        try:
            dispatch = resolve_classic_dispatch(node_type, node)
        except ClassicDispatchError as exc:
            logger.info("canvas classic dispatch rejected: %s", exc)
            return CanvasPromptRunResult(ok=False, text="", error=str(exc))

        if dispatch.kind == "op":
            if dispatch.op == OP_IMAGE_GEN:
                return await self._run_image_gen(
                    node=node,
                    body=body,
                    node_id=node_id,
                    project_id=project_id,
                )
            if dispatch.op == OP_VIDEO_GEN:
                return await self._run_video_gen(
                    node=node,
                    node_id=node_id,
                    project_id=project_id,
                )
            # split op slots in here (next task).
            return CanvasPromptRunResult(
                ok=False,
                text="",
                error=f"classic op '{dispatch.op}' is not implemented yet",
            )

        return await self.run_prompt(
            body=body,
            provider_slug=dispatch.provider_slug,
            agent_id=agent_id,
        )

    # ------------------------------------------------------------------
    # image_gen op (Phase 5a path B)
    # ------------------------------------------------------------------

    def _storyboard_ai_service(self):
        """Construct the storyboard AI service (seam for tests to patch).

        Lazy import keeps unit tests that never touch image_gen cheap and
        avoids dragging the storyboard repo/provider stack into module load.
        """
        from app.services.storyboard.storyboard_ai_service import StoryboardAIService

        return StoryboardAIService()

    async def _run_image_gen(
        self,
        *,
        node: Optional[Mapping[str, Any]],
        body: str,
        node_id: Optional[str],
        project_id: Optional[str],
    ) -> CanvasPromptRunResult:
        """Run an image_gen node: prompt → image via ``generate_image``.

        Params come from the node's ``data`` (prompt falls back to the run
        ``body`` since in a real graph the prompt arrives from an upstream
        node). Reuses the run service's context — there is no DB session to
        thread; ``generate_image`` looks up its own project style fragment by
        ``project_id`` (best-effort, returns empty when absent). The dataclass
        result is normalised into the shared envelope with ``result.image_url``.

        Any failure (missing prompt, unregistered provider, provider raises) is
        returned in-band as ok=False with a clear ``error`` — never silent.
        """
        params = _extract_image_gen_params(node)
        prompt = params["prompt"] or (body or "").strip()
        if not prompt:
            return CanvasPromptRunResult(
                ok=False,
                text="",
                error="image_gen node is missing a prompt",
            )

        try:
            service = self._storyboard_ai_service()
            raw = await service.generate_image(
                project_id=str(project_id) if project_id is not None else "",
                node_id=str(node_id) if node_id is not None else "",
                prompt=prompt,
                model=params["model"],
                provider_name=params["provider_name"],
                character_ids=params["character_ids"],
                reference_image_url=params["reference_image_url"],
                aspect_ratio=params["aspect_ratio"],
            )
        except Exception as exc:
            logger.exception("canvas image_gen failed for node %s", node_id)
            return CanvasPromptRunResult(
                ok=False, text="", error=f"image generation failed: {exc}"
            )

        result = dict(raw) if isinstance(raw, Mapping) else {}
        image_url = result.get("image_url")
        if not image_url:
            return CanvasPromptRunResult(
                ok=False,
                text="",
                error="image generation returned no image_url",
            )

        return CanvasPromptRunResult(
            ok=True,
            text=str(image_url),
            error=None,
            result=result,
        )

    # ------------------------------------------------------------------
    # video_gen op (Phase 5a path B)
    # ------------------------------------------------------------------

    async def _run_video_gen(
        self,
        *,
        node: Optional[Mapping[str, Any]],
        node_id: Optional[str],
        project_id: Optional[str],
    ) -> CanvasPromptRunResult:
        """Run a video_gen node: source image + motion prompt → video via
        ``generate_video`` (the distinct video provider registry).

        Params come from the node's ``data``. ``source_image_url`` is required —
        in a real graph it arrives from an upstream image node, but for now it
        is read straight from the node data, so a missing one is a hard,
        in-band failure (the service is NOT called). Reuses the run service's
        context exactly like ``_run_image_gen`` (project_id from the gate,
        node_id from the node; no DB session to thread). The dataclass result
        is normalised into the shared envelope with ``result.video_url`` (and
        ``thumbnail_url``).

        Any failure (missing source image, unregistered video provider,
        provider raises, empty video_url) is returned in-band as ok=False with
        a clear ``error`` — never silent.
        """
        params = _extract_video_gen_params(node)
        source_image_url = params["source_image_url"]
        if not source_image_url:
            return CanvasPromptRunResult(
                ok=False,
                text="",
                error="video_gen node is missing a source image",
            )

        try:
            service = self._storyboard_ai_service()
            raw = await service.generate_video(
                project_id=str(project_id) if project_id is not None else "",
                node_id=str(node_id) if node_id is not None else "",
                source_image_url=source_image_url,
                prompt=params["prompt"],
                provider_name=params["provider_name"],
                model=params["model"],
                duration_seconds=params["duration_seconds"],
                motion_intensity=params["motion_intensity"],
            )
        except Exception as exc:
            logger.exception("canvas video_gen failed for node %s", node_id)
            return CanvasPromptRunResult(
                ok=False, text="", error=f"video generation failed: {exc}"
            )

        result = dict(raw) if isinstance(raw, Mapping) else {}
        video_url = result.get("video_url")
        if not video_url:
            return CanvasPromptRunResult(
                ok=False,
                text="",
                error="video generation returned no video_url",
            )

        return CanvasPromptRunResult(
            ok=True,
            text=str(video_url),
            error=None,
            result=result,
        )

    @staticmethod
    def _extract_text(response: dict) -> str:
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return ""
        if not isinstance(content, str):
            return ""
        return content

    # ------------------------------------------------------------------
    # nous-center workflow routing
    # ------------------------------------------------------------------

    async def _run_via_nous(
        self,
        provider_slug: str,
        body: str,
        agent_id: Optional[str],
    ) -> CanvasPromptRunResult:
        """provider_slug shape: ``nous/<workflow_slug>``. Calls nous-center,
        polls for completion, returns the workflow's text output (or a
        JSON encoding when the workflow output is non-textual)."""
        from app.services.canvas.nous_center_runner import (
            NousCenterNotConfigured,
            run_nous_workflow,
        )

        _, _, workflow_slug = provider_slug.partition("/")
        if not workflow_slug:
            return CanvasPromptRunResult(
                ok=False, text="", error="nous provider_slug missing workflow"
            )

        try:
            settings = await self._get_settings()
            raw_ceiling = getattr(settings, "NOUS_CENTER_MAX_WAIT_S_WORKFLOW", None)
            try:
                workflow_ceiling = (
                    float(raw_ceiling)
                    if raw_ceiling is not None
                    else DEFAULT_WORKFLOW_MAX_WAIT_S
                )
            except (TypeError, ValueError):
                workflow_ceiling = DEFAULT_WORKFLOW_MAX_WAIT_S
            return await run_nous_workflow(
                settings=settings,
                workflow_slug=workflow_slug,
                prompt=body,
                agent_id=agent_id,
                max_wait_s_override=workflow_ceiling,
            )
        except NousCenterNotConfigured as exc:
            return CanvasPromptRunResult(ok=False, text="", error=str(exc))
        except Exception as exc:
            logger.exception("nous-center workflow %s failed", workflow_slug)
            return CanvasPromptRunResult(
                ok=False, text="", error=f"nous-center call failed: {exc}"
            )

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
