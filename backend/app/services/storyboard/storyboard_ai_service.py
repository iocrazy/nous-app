# app/services/storyboard_ai_service.py

"""
Storyboard AI Service

Orchestrates AI-powered operations for the Storyboard Workbench:
- Image generation (single and batch) via provider registry
- Video generation via provider registry
- Script splitting into structured scene dicts via LLM
- Video analysis: scene detection + per-keyframe LLM annotation
- Conversational chat with project context

LLM calls use httpx.AsyncClient against an OpenAI-compatible endpoint
configured via environment variables (LLM_API_URL, LLM_API_KEY).
"""

import asyncio
import json
from dataclasses import asdict
from typing import Any, Dict, List, Optional
from uuid import UUID

import httpx
from loguru import logger

from app.core.config import settings
from app.repositories.agent_repository import AgentRepository
from app.repositories.skill_repository import SkillRepository
from app.repositories.storyboard_repository import StoryboardCharacterRepository
from app.services.ai.runner.agent_runner import AgentRunner
from app.services.ai.adapters import get_adapter
from app.services.ai.adapters.factory import provider_key_for_model
from app.services.ai.prompts.prompt_composer import ComposerInput, PromptComposer
from app.services.ai.runner.run_recorder import AgentPausedError, RunRecorder
from app.services.ai.skills.skill_tool_service import SkillToolService
from app.services.storyboard.storyboard_service import StoryboardService
from app.services.media.parsers.video_providers import (
    ImageGenResult,
    VideoGenResult,
    provider_registry,
)

# Agent slug in the ai_agents table (seeded from backend/seeds/agents/storyboard/).
AGENT_SLUG = "storyboard"


class StoryboardAIService:
    """AI orchestration layer for the Storyboard Workbench module."""

    AGENT_SLUG: str = AGENT_SLUG

    def __init__(self) -> None:
        self.storyboard_service = StoryboardService()
        self.character_repo = StoryboardCharacterRepository()

    # ------------------------------------------------------------------ #
    # Agent prompt composition (Phase 2 PR 2.6 — DB-driven prompts)
    # ------------------------------------------------------------------ #

    async def _run_via_agent_runner(
        self,
        *,
        instruction: str,
        user_content: str,
        user_id: Optional[UUID] = None,
        team_id: Optional[int] = None,
        project_id: Optional[int] = None,
        trigger: str = "storyboard_ai",
    ) -> str:
        """Compose + run the storyboard agent through AgentRunner +
        RunRecorder. Returns raw assistant content.

        K migration: replaces the sync httpx path for telemetry-worthy
        sites (currently only ``chat()``). Brings:
            * agent_runs row with cost / tokens / model / trigger
            * paused_reason gate (admin pause works)
            * Skill / Delegate tools available to the LLM
            * fallback chain (when wired)

        Telemetry is best-effort: if user_id is None or RunRecorder.start
        fails, the run still goes through with a no-op recorder.
        """
        composer = PromptComposer(AgentRepository(), SkillRepository())
        composed = await composer.compose(
            ComposerInput(
                agent_slug=self.AGENT_SLUG,
                request_instructions=instruction,
            )
        )
        adapter = get_adapter(composed.model or "", settings)
        runner = AgentRunner(
            adapter=adapter,
            skill_tool=SkillToolService(SkillRepository()),
        )
        user_messages = [{"role": "user", "content": user_content}]

        if user_id is None:
            # No-telemetry path. Mirrors script_ai_service for parity.
            result = await runner.run_turn(composed, user_messages=user_messages)
            return result.get("content") or ""

        model = composed.model or ""
        try:
            provider = provider_key_for_model(model) if model else None
        except ValueError:
            provider = None

        try:
            async with RunRecorder(
                agent_id=composed.agent_id,
                user_id=user_id,
                trigger=trigger,
                team_id=team_id,
                project_id=project_id,
                model=model or None,
                provider=provider,
                input_summary=user_content,
                metadata={"full_input": user_content[:5000]},
            ) as recorder:
                result = await runner.run_turn(
                    composed,
                    user_messages=user_messages,
                    recorder=recorder,
                )
                content = result.get("content") or ""
                recorder.set_summaries(output_summary=content)
                return content
        except AgentPausedError:
            # Surface as runtime so callers translate it; matches script_ai.
            raise

    async def _compose_system_prompt(self, instruction: str) -> str:
        """Fetch the ``storyboard`` agent's composed system message from DB.

        The agent's AGENT.md documents 3 modes (split_script / annotate_keyframe /
        chat). The per-call ``instruction`` tells the model which mode to use
        and carries any dynamic context (style guide, project characters,
        selected frame details, skill injection, etc.).
        """
        composer = PromptComposer(AgentRepository(), SkillRepository())
        composed = await composer.compose(
            ComposerInput(
                agent_slug=self.AGENT_SLUG,
                request_instructions=instruction,
            )
        )
        return composed.system_message

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    async def _call_llm(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """
        Send a chat-completion request to the configured LLM endpoint.

        Args:
            messages: List of {"role": ..., "content": ...} dicts.
            temperature: Sampling temperature.
            max_tokens: Maximum response tokens.

        Returns:
            The raw text content of the first choice.

        Raises:
            RuntimeError: If the HTTP request fails or returns an error status.
        """
        headers = {"Content-Type": "application/json"}
        if settings.LLM_API_KEY:
            headers["Authorization"] = f"Bearer {settings.LLM_API_KEY}"

        payload = {
            "model": settings.LLM_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        try:
            async with httpx.AsyncClient(
                timeout=settings.LLM_TIMEOUT_SECONDS
            ) as client:
                response = await client.post(
                    f"{settings.LLM_API_URL}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "LLM API returned %s: %s",
                exc.response.status_code,
                exc.response.text[:500],
            )
            raise RuntimeError(
                f"LLM API error {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except httpx.RequestError as exc:
            logger.error(f"LLM API request failed: {exc}")
            raise RuntimeError(f"LLM API request failed: {exc}") from exc

        data = response.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            logger.error(f"Unexpected LLM response shape: {data}")
            raise RuntimeError("Unexpected LLM response structure") from exc

    @staticmethod
    def _extract_json_from_text(text: str) -> Any:
        """
        Extract and parse the first JSON value found in *text*.

        Handles responses that wrap JSON in markdown code fences.

        Args:
            text: Raw LLM output that should contain a JSON value.

        Returns:
            Parsed Python object.

        Raises:
            ValueError: If no valid JSON can be found.
        """
        # Strip markdown code fences if present
        stripped = text.strip()
        for fence in ("```json", "```"):
            if stripped.startswith(fence):
                stripped = stripped[len(fence) :]
                if stripped.endswith("```"):
                    stripped = stripped[:-3]
                stripped = stripped.strip()
                break

        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

        # Last resort: find the first '[' or '{' and try from there
        for start_char, end_char in (("[", "]"), ("{", "}")):
            start = text.find(start_char)
            end = text.rfind(end_char)
            if start != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    continue

        raise ValueError(f"No valid JSON found in LLM output: {text[:300]!r}")

    async def _build_character_prompt_fragments(self, character_ids: List[str]) -> str:
        """
        Fetch each character and return a joined description string.

        Args:
            character_ids: List of character UUIDs.

        Returns:
            Comma-separated character descriptions, or empty string.
        """
        if not character_ids:
            return ""

        tasks = [
            self.storyboard_service.get_character_prompt_fragment(cid)
            for cid in character_ids
        ]
        fragments: List[str] = []
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for cid, result in zip(character_ids, results):
            if isinstance(result, Exception):
                logger.warning(
                    "Could not fetch prompt fragment for character %s: %s", cid, result
                )
            else:
                fragments.append(result)

        return ", ".join(fragments)

    # ------------------------------------------------------------------ #
    # 1. generate_image
    # ------------------------------------------------------------------ #

    async def generate_image(
        self,
        project_id: str,
        node_id: str,
        prompt: str,
        model: str,
        provider_name: str,
        character_ids: Optional[List[str]] = None,
        reference_image_url: Optional[str] = None,
        aspect_ratio: str = "16:9",
    ) -> Dict[str, Any]:
        """
        Generate a single image via the named provider.

        If *character_ids* are supplied the character descriptions are
        prepended to *prompt* before the request is sent.

        Note: In production this method is called from Celery tasks, not
        directly from request handlers.

        Args:
            project_id: UUID of the owning project (for logging / future use).
            node_id: UUID of the canvas node (for logging / future use).
            prompt: Base image generation prompt.
            model: Model identifier understood by the provider.
            provider_name: Registered image provider name.
            character_ids: Optional list of character UUIDs whose visual
                           descriptions are prepended to the prompt.
            reference_image_url: Optional URL of a reference image passed to
                                 the provider.
            aspect_ratio: Output aspect ratio string (e.g. "16:9", "1:1").

        Returns:
            ImageGenResult serialised as a dict.

        Raises:
            KeyError: If *provider_name* is not registered.
            RuntimeError: If the provider raises during generation.
        """
        try:
            effective_prompt = prompt

            if character_ids:
                character_fragment = await self._build_character_prompt_fragments(
                    character_ids
                )
                if character_fragment:
                    effective_prompt = f"{character_fragment}, {prompt}"

            image_provider = provider_registry.get_image_provider(provider_name)

            result: ImageGenResult = await image_provider.generate(
                effective_prompt,
                model,
                aspect_ratio=aspect_ratio,
                reference_image_url=reference_image_url,
            )

            logger.info(
                "Image generated for project=%s node=%s provider=%s model=%s",
                project_id,
                node_id,
                provider_name,
                model,
            )
            return asdict(result)

        except KeyError:
            logger.error(
                "Image provider not found: %s (project=%s node=%s)",
                provider_name,
                project_id,
                node_id,
            )
            raise
        except Exception as exc:
            logger.error(
                "Image generation failed for project=%s node=%s: %s",
                project_id,
                node_id,
                exc,
            )
            raise

    # ------------------------------------------------------------------ #
    # 2. generate_image_batch
    # ------------------------------------------------------------------ #

    async def generate_image_batch(
        self, requests: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Generate multiple images, grouping by provider for concurrency.

        Requests within the same provider group are run concurrently via
        asyncio.gather.  Partial failures are captured per-item; a failed
        item returns a dict with ``success=False`` and an ``error`` key.

        Args:
            requests: List of request dicts, each containing at minimum:
                      project_id, node_id, prompt, model, provider_name.
                      Optional keys: character_ids, reference_image_url,
                      aspect_ratio.

        Returns:
            List of result dicts in the same order as *requests*.  Each
            successful entry is an ImageGenResult dict extended with
            ``success=True`` and the original ``request_index``.  Each
            failed entry has ``success=False``, ``error`` (str), and
            ``request_index``.
        """
        if not requests:
            return []

        # Group requests by provider while preserving original indices
        by_provider: Dict[str, List[tuple[int, Dict[str, Any]]]] = {}
        for idx, req in enumerate(requests):
            provider_name = req.get("provider_name", "")
            if provider_name not in by_provider:
                by_provider[provider_name] = []
            by_provider[provider_name] = [*by_provider[provider_name], (idx, req)]

        # Placeholder list – keeps None until we fill in results
        output: List[Optional[Dict[str, Any]]] = [None] * len(requests)

        async def _generate_one(idx: int, req: Dict[str, Any]) -> None:
            try:
                result = await self.generate_image(
                    project_id=req.get("project_id", ""),
                    node_id=req.get("node_id", ""),
                    prompt=req.get("prompt", ""),
                    model=req.get("model", ""),
                    provider_name=req.get("provider_name", ""),
                    character_ids=req.get("character_ids"),
                    reference_image_url=req.get("reference_image_url"),
                    aspect_ratio=req.get("aspect_ratio", "16:9"),
                )
                output[idx] = {**result, "success": True, "request_index": idx}
            except Exception as exc:
                logger.warning(
                    "Batch image generation failed for request index %d: %s", idx, exc
                )
                output[idx] = {
                    "success": False,
                    "error": str(exc),
                    "request_index": idx,
                }

        # Run each provider group concurrently, groups are sequential (no
        # strict need to interleave across providers)
        for provider_name, indexed_reqs in by_provider.items():
            group_tasks = [_generate_one(idx, req) for idx, req in indexed_reqs]
            await asyncio.gather(*group_tasks)

        # Fill any remaining None slots (defensive)
        filled: List[Dict[str, Any]] = [
            (
                item
                if item is not None
                else {"success": False, "error": "unknown", "request_index": i}
            )
            for i, item in enumerate(output)
        ]
        return filled

    # ------------------------------------------------------------------ #
    # 3. generate_video
    # ------------------------------------------------------------------ #

    async def generate_video(
        self,
        project_id: str,
        node_id: str,
        source_image_url: str,
        prompt: str,
        provider_name: str,
        model: str = "",
        duration_seconds: float = 5.0,
        motion_intensity: str = "medium",
    ) -> Dict[str, Any]:
        """
        Generate a video clip from a source image via the named provider.

        Args:
            project_id: UUID of the owning project (for logging).
            node_id: UUID of the canvas node (for logging).
            source_image_url: URL of the reference image to animate.
            prompt: Motion / style prompt for the video.
            provider_name: Registered video provider name.
            model: Model identifier understood by the provider.
            duration_seconds: Desired clip length in seconds.
            motion_intensity: Hint for motion intensity
                              (``"low"``, ``"medium"``, ``"high"``).

        Returns:
            VideoGenResult serialised as a dict.

        Raises:
            KeyError: If *provider_name* is not registered.
            RuntimeError: If the provider raises during generation.
        """
        try:
            video_provider = provider_registry.get_video_provider(provider_name)

            result: VideoGenResult = await video_provider.generate(
                source_image_url,
                prompt,
                model,
                duration_seconds=duration_seconds,
                motion_intensity=motion_intensity,
            )

            logger.info(
                "Video generated for project=%s node=%s provider=%s",
                project_id,
                node_id,
                provider_name,
            )
            return asdict(result)

        except KeyError:
            logger.error(
                "Video provider not found: %s (project=%s node=%s)",
                provider_name,
                project_id,
                node_id,
            )
            raise
        except Exception as exc:
            logger.error(
                "Video generation failed for project=%s node=%s: %s",
                project_id,
                node_id,
                exc,
            )
            raise

    # ------------------------------------------------------------------ #
    # 4. split_script
    # ------------------------------------------------------------------ #

    async def split_script(
        self,
        script_text: str,
        style_guide: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Break a script into structured scene dicts using an LLM.

        Builds a system prompt that instructs the model to return a JSON
        array of scene objects.  A style guide can optionally be injected
        to bias shot types, lighting choices, etc.

        Args:
            script_text: Full script or treatment text to split.
            style_guide: Optional stylistic guidance (e.g. "noir", "anime").

        Returns:
            List of scene dicts, each containing:
            scene_number, description, shot_type, camera_angle,
            camera_movement, focal_length, lighting, duration,
            characters, dialogue, suggested_prompt.

        Raises:
            ValueError: If the LLM response cannot be parsed as a JSON array.
            RuntimeError: If the LLM API call fails.
        """
        instruction_parts = ["Mode: split_script. Follow Mode A of your AGENT spec."]
        if style_guide:
            instruction_parts.append(
                f"Apply this visual style guide to all scenes: {style_guide}"
            )
        instruction = "\n\n".join(instruction_parts)

        system_prompt = await self._compose_system_prompt(instruction)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Script:\n\n{script_text}"},
        ]

        try:
            raw = await self._call_llm(messages, temperature=0.5, max_tokens=8192)
        except RuntimeError:
            logger.error("LLM call failed during split_script")
            raise

        try:
            scenes = self._extract_json_from_text(raw)
        except ValueError as exc:
            logger.error(f"Failed to parse split_script LLM response: {exc}")
            raise

        if not isinstance(scenes, list):
            raise ValueError(
                f"Expected JSON array from LLM, got {type(scenes).__name__}"
            )

        logger.info(f"split_script: parsed {len(scenes)} scene(s) from script")
        return scenes

    # ------------------------------------------------------------------ #
    # 5. analyze_video
    # ------------------------------------------------------------------ #

    async def analyze_video(self, video_path: str) -> Dict[str, Any]:
        """
        Detect scene keyframes in a video and annotate each with LLM analysis.

        Uses StoryboardImageService.detect_scenes() for frame extraction,
        then calls the LLM concurrently on each keyframe image (described by
        path) to annotate cinematographic properties.

        Args:
            video_path: Absolute path to the video file to analyse.

        Returns:
            Dict with key ``keyframes`` containing a list of dicts:
            [{time, image_path, shot_type, camera_angle, movement,
              suggested_prompt}, ...]
        """
        from app.services.storyboard.storyboard_image_service import StoryboardImageService

        image_service = StoryboardImageService()

        try:
            raw_keyframes: List[Dict[str, Any]] = image_service.detect_scenes(
                video_path
            )
        except Exception as exc:
            logger.error(f"detect_scenes failed for video {video_path}: {exc}")
            raise

        if not raw_keyframes:
            logger.info(f"analyze_video: no keyframes detected in {video_path}")
            return {"keyframes": []}

        # Compose Mode B system prompt ONCE — every keyframe uses the same
        # prompt, only the user content varies, so avoid N DB round-trips.
        annotate_system_prompt = await self._compose_system_prompt(
            "Mode: annotate_keyframe. Follow Mode B of your AGENT spec."
        )

        async def _annotate_keyframe(kf: Dict[str, Any]) -> Dict[str, Any]:
            image_path = kf.get("image_path", "")
            timestamp = kf.get("time", 0.0)
            user_content = (
                f"Video frame at t={timestamp:.2f}s. Image file: {image_path}"
            )
            messages = [
                {"role": "system", "content": annotate_system_prompt},
                {"role": "user", "content": user_content},
            ]

            try:
                raw = await self._call_llm(messages, temperature=0.3, max_tokens=512)
                annotation = self._extract_json_from_text(raw)
                if not isinstance(annotation, dict):
                    raise ValueError("LLM returned non-dict annotation")
            except Exception as exc:
                logger.warning(
                    "Keyframe annotation failed for %s at t=%.2f: %s",
                    image_path,
                    timestamp,
                    exc,
                )
                annotation = {
                    "shot_type": "unknown",
                    "camera_angle": "unknown",
                    "movement": "unknown",
                    "suggested_prompt": "",
                }

            return {
                "time": timestamp,
                "image_path": image_path,
                "shot_type": annotation.get("shot_type", "unknown"),
                "camera_angle": annotation.get("camera_angle", "unknown"),
                "movement": annotation.get("movement", "unknown"),
                "suggested_prompt": annotation.get("suggested_prompt", ""),
            }

        annotated = await asyncio.gather(
            *[_annotate_keyframe(kf) for kf in raw_keyframes]
        )

        logger.info(
            "analyze_video: annotated %d keyframe(s) in %s",
            len(annotated),
            video_path,
        )
        return {"keyframes": list(annotated)}

    # Storyboard chat moved to /api/v1/ai-library/sessions/* (AgentRunner
    # pipeline) in the AIChatDrawer migration. The legacy chat() method
    # was deleted. AILibraryChatService handles per-turn context, skill
    # selection, and runs through the same Skill / Delegate tool loop as
    # every other agent — the bespoke instruction wiring here was the
    # last thing keeping two parallel code paths alive.
