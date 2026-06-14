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
    ClassicDispatchError,
    resolve_provider_slug,
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
        if not body:
            return CanvasPromptRunResult(
                ok=False, text="", error="prompt body is empty"
            )

        # Route to nous-center for workflow-style providers.
        if provider_slug and provider_slug.startswith("nous/"):
            return await self._run_via_nous(provider_slug, body, agent_id)

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
    ) -> CanvasPromptRunResult:
        """Resolve a classic node's provider_slug from its type + data, then
        dispatch through the existing synchronous run path.

        A ``comfy`` node resolves to ``nous/<workflow_slug>`` and reuses the
        ``nous/`` route (which block-polls ``run_nous_workflow`` to a terminal
        state) — single-synchronous, no DBOS workflow. An ``llm`` node resolves
        to its model slug and reuses the bare-model adapter path. Unknown /
        non-runnable node types fail in-band (ok=False) without dispatching, so
        we never silently route to the wrong provider.
        """
        try:
            provider_slug = resolve_provider_slug(node_type, node)
        except ClassicDispatchError as exc:
            logger.info("canvas classic dispatch rejected: %s", exc)
            return CanvasPromptRunResult(ok=False, text="", error=str(exc))

        return await self.run_prompt(
            body=body,
            provider_slug=provider_slug,
            agent_id=agent_id,
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
