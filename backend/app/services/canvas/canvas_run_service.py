"""Canvas-mode prompt runner (Phase 2 Day 6-8).

Executes one prompt-node Run by calling the configured LLM adapter
directly. Stays out of the AgentRunner pipeline so we don't drag
skill/tool/history machinery into what is conceptually a single-turn
creative generation.

Provider mapping:
    None or ""         → DB default text model  (_default_text_model — the
                                                 first enabled ``llm`` row in
                                                 the ``mediahub_models`` catalog,
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

from typing import Any, List, Mapping, Optional
from uuid import UUID

from loguru import logger

from app.schemas.canvas_run import CanvasPromptRunResult
from app.services.canvas.classic_dispatch import (
    OP_IMAGE_GEN,
    OP_VIDEO_GEN,
    ClassicDispatchError,
    resolve_classic_dispatch,
)
from app.services.library.generated_media_service import (
    GenerationOrigin,
    register_generated_media,
)
from app.services.library.resources_service import _resolve_personal_team_id

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
        # DB-only credential resolution (铁律 2026-07-07): platform
        # ``mediahub_models`` catalog → ProviderNotConfiguredError. No env.
        from app.services.ai.providers.ai_provider_helpers import resolve_db_adapter

        return await resolve_db_adapter(model, "canvas")

    async def _default_text_model(self) -> str:
        """The catalog model an empty ``provider_slug`` resolves to.

        First enabled ``llm`` row in the platform ``mediahub_models`` catalog,
        falling back to the governed maintenance model (itself a catalog
        entry). DB-only, so the default text Run always names a model the
        platform actually has configured — the root cause of the 2026-07-12
        "default prompt won't run" report was a hardcoded ``qwen-plus`` that
        the catalog no longer carries.
        """
        from app.repositories.mediahub_model_repository import (
            get_mediahub_model_repository,
        )
        from app.services.ai.providers.ai_provider_helpers import (
            get_maintenance_model,
        )

        try:
            rows = await get_mediahub_model_repository().list_enabled("llm")
        except Exception:  # noqa: BLE001 — degrade to the governed default
            logger.opt(exception=True).warning(
                "canvas: enabled-llm catalog read failed; using maintenance model"
            )
            rows = []
        for row in rows:
            name = row.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
        return await get_maintenance_model()

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
        user_id: Optional[str] = None,
        canvas_id: Optional[int] = None,
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
            logger.info("canvas classic dispatch rejected: {}", exc)
            return CanvasPromptRunResult(ok=False, text="", error=str(exc))

        if dispatch.kind == "op":
            if dispatch.op == OP_IMAGE_GEN:
                return await self._run_image_gen(
                    node=node,
                    body=body,
                    node_id=node_id,
                    project_id=project_id,
                    user_id=user_id,
                    canvas_id=canvas_id,
                )
            if dispatch.op == OP_VIDEO_GEN:
                return await self._run_video_gen(
                    node=node,
                    node_id=node_id,
                    project_id=project_id,
                    user_id=user_id,
                    canvas_id=canvas_id,
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
        user_id: Optional[str] = None,
        canvas_id: Optional[int] = None,
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
            logger.exception("canvas image_gen failed for node {}", node_id)
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

        # Best-effort capture into Tier-1 generated_media store.
        # Failures here must NEVER affect the canvas run result.
        if image_url and user_id:
            try:
                scope_id = int(await _resolve_personal_team_id(str(user_id)))
                await register_generated_media(
                    user_id=str(user_id),
                    scope_id=scope_id,
                    source_url=str(image_url),
                    mime="image/png",
                    origin=GenerationOrigin(
                        kind="canvas_run",
                        run_id=None,
                        canvas_id=canvas_id,
                        node_id=node_id,
                        prompt=prompt,
                        model=params.get("model"),
                        provider=params.get("provider_name"),
                        params=params,
                        derivation_kind="image_gen",
                    ),
                )
            except Exception:  # noqa: BLE001
                logger.opt(exception=True).warning(
                    "[genmedia] canvas image capture failed (non-fatal)"
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
        user_id: Optional[str] = None,
        canvas_id: Optional[int] = None,
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
            logger.exception("canvas video_gen failed for node {}", node_id)
            return CanvasPromptRunResult(
                ok=False, text="", error=f"video generation failed: {exc}"
            )

        result = dict(raw) if isinstance(raw, Mapping) else {}
        video_url = result.get("video_url")
        video_path = result.get("video_path")

        # DB-catalog (jimeng-cli) products are LOCAL FILES — no URL exists
        # until the generated-media store ingests them, so registration is
        # REQUIRED here (unlike the remote-URL branch below where it stays
        # best-effort). The durable /stream URL is token-free by design so a
        # bare <video src> can load it.
        if not video_url and video_path:
            if not user_id:
                return CanvasPromptRunResult(
                    ok=False,
                    text="",
                    error="video generation produced a local file but the run "
                    "has no user context to persist it under",
                )
            try:
                scope_id = int(await _resolve_personal_team_id(str(user_id)))
                row = await register_generated_media(
                    user_id=str(user_id),
                    scope_id=scope_id,
                    source_path=str(video_path),
                    mime="video/mp4",
                    origin=GenerationOrigin(
                        kind="canvas_run",
                        run_id=None,
                        canvas_id=canvas_id,
                        node_id=node_id,
                        prompt=params.get("prompt"),
                        model=params.get("model"),
                        provider=params.get("provider_name"),
                        params=params,
                        derivation_kind="video_gen",
                    ),
                )
                gen_id = row.get("id")
                if gen_id is None:
                    raise RuntimeError("register_generated_media returned no id")
                video_url = f"/api/v1/generated-media/{gen_id}/stream"
                result["video_url"] = video_url
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "canvas video_gen local persist failed for node {}", node_id
                )
                return CanvasPromptRunResult(
                    ok=False,
                    text="",
                    error=f"video generated but persist/registration failed: {exc}",
                )
            return CanvasPromptRunResult(
                ok=True,
                text=str(video_url),
                error=None,
                result=result,
            )

        if not video_url:
            return CanvasPromptRunResult(
                ok=False,
                text="",
                error="video generation returned no video_url",
            )

        # Best-effort capture into Tier-1 generated_media store.
        # Failures here must NEVER affect the canvas run result.
        if video_url and user_id:
            try:
                scope_id = int(await _resolve_personal_team_id(str(user_id)))
                await register_generated_media(
                    user_id=str(user_id),
                    scope_id=scope_id,
                    source_url=str(video_url),
                    mime="video/mp4",
                    origin=GenerationOrigin(
                        kind="canvas_run",
                        run_id=None,
                        canvas_id=canvas_id,
                        node_id=node_id,
                        prompt=params.get("prompt"),
                        model=params.get("model"),
                        provider=params.get("provider_name"),
                        params=params,
                        derivation_kind="video_gen",
                    ),
                )
            except Exception:  # noqa: BLE001
                logger.opt(exception=True).warning(
                    "[genmedia] canvas video capture failed (non-fatal)"
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
            logger.exception("nous-center workflow {} failed", workflow_slug)
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
