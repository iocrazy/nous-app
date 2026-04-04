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

import httpx
from loguru import logger

from app.core.config import settings
from app.repositories.storyboard_repository import StoryboardCharacterRepository
from app.services.storyboard_service import StoryboardService
from app.services.video_providers import (
    ImageGenResult,
    VideoGenResult,
    provider_registry,
)

# ---------------------------------------------------------------------------
# Scene schema description (used in LLM prompts)
# ---------------------------------------------------------------------------

_SCENE_SCHEMA_DESC = """\
[
  {
    "scene_number": 1,
    "description": "Brief scene description",
    "shot_type": "wide|medium|close-up|extreme-close-up|over-the-shoulder|pov",
    "camera_angle": "eye-level|low-angle|high-angle|dutch-angle|bird's-eye|worm's-eye",
    "camera_movement": "static|pan|tilt|dolly|zoom|handheld|tracking",
    "focal_length": "wide|standard|telephoto",
    "lighting": "natural|studio|dramatic|silhouette|golden-hour|night",
    "duration": 3.5,
    "characters": ["CharacterA", "CharacterB"],
    "dialogue": "Optional dialogue text for this scene",
    "suggested_prompt": "Detailed image generation prompt for this scene"
  }
]
"""


class StoryboardAIService:
    """AI orchestration layer for the Storyboard Workbench module."""

    def __init__(self) -> None:
        self.storyboard_service = StoryboardService()
        self.character_repo = StoryboardCharacterRepository()

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
            async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT_SECONDS) as client:
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
            logger.error("LLM API request failed: %s", exc)
            raise RuntimeError(f"LLM API request failed: {exc}") from exc

        data = response.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            logger.error("Unexpected LLM response shape: %s", data)
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
                stripped = stripped[len(fence):]
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
                    return json.loads(text[start: end + 1])
                except json.JSONDecodeError:
                    continue

        raise ValueError(f"No valid JSON found in LLM output: {text[:300]!r}")

    async def _build_character_prompt_fragments(
        self, character_ids: List[str]
    ) -> str:
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
            item if item is not None else {"success": False, "error": "unknown", "request_index": i}
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
        style_instruction = ""
        if style_guide:
            style_instruction = f"\nApply this visual style guide to all scenes: {style_guide}\n"

        system_prompt = (
            "You are a professional storyboard artist and cinematographer. "
            "Analyse the provided script and break it into individual scenes. "
            "Return ONLY a valid JSON array (no markdown, no extra text) "
            "where each element matches this schema:\n"
            f"{_SCENE_SCHEMA_DESC}"
            f"{style_instruction}"
            "Infer duration from pacing. Keep suggested_prompt vivid and "
            "suitable for an image generation model."
        )

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
            logger.error("Failed to parse split_script LLM response: %s", exc)
            raise

        if not isinstance(scenes, list):
            raise ValueError(
                f"Expected JSON array from LLM, got {type(scenes).__name__}"
            )

        logger.info("split_script: parsed %d scene(s) from script", len(scenes))
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
        from app.services.storyboard_image_service import StoryboardImageService

        image_service = StoryboardImageService()

        try:
            raw_keyframes: List[Dict[str, Any]] = image_service.detect_scenes(
                video_path
            )
        except Exception as exc:
            logger.error(
                "detect_scenes failed for video %s: %s", video_path, exc
            )
            raise

        if not raw_keyframes:
            logger.info("analyze_video: no keyframes detected in %s", video_path)
            return {"keyframes": []}

        async def _annotate_keyframe(kf: Dict[str, Any]) -> Dict[str, Any]:
            image_path = kf.get("image_path", "")
            timestamp = kf.get("time", 0.0)

            system_prompt = (
                "You are a cinematography expert. Given the description of a "
                "video frame at a specific timestamp, identify the shot type, "
                "camera angle, camera movement, and write a detailed "
                "image-generation prompt that would reproduce this frame. "
                "Return ONLY valid JSON (no markdown) matching:\n"
                '{"shot_type": "...", "camera_angle": "...", '
                '"movement": "...", "suggested_prompt": "..."}'
            )
            user_content = (
                f"Video frame at t={timestamp:.2f}s. "
                f"Image file: {image_path}"
            )

            messages = [
                {"role": "system", "content": system_prompt},
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

    # ------------------------------------------------------------------ #
    # 6. chat
    # ------------------------------------------------------------------ #

    async def chat(
        self,
        project_id: str,
        message: str,
        selected_frame_id: Optional[str] = None,
        skill_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Handle a conversational message in the context of a storyboard project.

        Builds LLM context from project characters and (optionally) the
        selected frame, then parses the response for structured actions.

        Args:
            project_id: UUID of the storyboard project.
            message: User's chat message.
            selected_frame_id: Optional UUID of the currently selected frame.

        Returns:
            Dict with keys:
            - ``response`` (str): Natural-language reply from the LLM.
            - ``actions`` (list[dict]): Structured actions parsed from the
              response (e.g. modify_frame, suggest_prompt).  Empty list if
              none found.
        """
        # Build project context
        context_parts: List[str] = []

        try:
            characters = await self.character_repo.list_by_project(project_id)
        except Exception as exc:
            logger.warning(
                "chat: could not fetch characters for project %s: %s",
                project_id,
                exc,
            )
            characters = []

        if characters:
            char_descriptions = [
                f"- {c.get('name', 'Unknown')}: {c.get('description', 'no description')}"
                for c in characters
            ]
            context_parts.append(
                "Project characters:\n" + "\n".join(char_descriptions)
            )

        # Optionally enrich with selected frame data
        if selected_frame_id:
            try:
                from app.db.supabase_client import get_async_supabase_admin
                from app.repositories.storyboard_repository import (
                    StoryboardFrameRepository,
                )

                client = await get_async_supabase_admin()
                frame_result = (
                    await client.table(StoryboardFrameRepository.TABLE_NAME)
                    .select("*")
                    .eq("id", selected_frame_id)
                    .limit(1)
                    .execute()
                )
                if frame_result.data:
                    frame = frame_result.data[0]
                    frame_info = (
                        f"Selected frame (id={selected_frame_id}):\n"
                        f"  prompt: {frame.get('prompt', '')}\n"
                        f"  shot_type: {frame.get('shot_type', '')}\n"
                        f"  camera_angle: {frame.get('camera_angle', '')}\n"
                        f"  notes: {frame.get('notes', '')}"
                    )
                    context_parts.append(frame_info)
            except Exception as exc:
                logger.warning(
                    "chat: could not fetch frame %s: %s", selected_frame_id, exc
                )

        context_block = "\n\n".join(context_parts) if context_parts else "No additional context."

        # Skill injection
        skill_prefix = ""
        if skill_id:
            try:
                from app.repositories.skill_repository import SkillRepository
                skill_repo = SkillRepository()
                skill = await skill_repo.get_by_id(skill_id)
                if skill and skill.get("status") == "active":
                    skill_prefix = (
                        f"<skill>\n{skill['content_md']}\n</skill>\n\n"
                    )
                    if skill.get("output_format"):
                        skill_prefix += (
                            f"Output format:\n{skill['output_format']}\n\n"
                        )
                    logger.info(
                        "chat: injected skill %s for project %s",
                        skill_id, project_id,
                    )
                else:
                    logger.warning(
                        "chat: skill %s not found or archived, proceeding without",
                        skill_id,
                    )
            except Exception as exc:
                logger.warning("chat: skill lookup failed: %s", exc)

        system_prompt = skill_prefix + (
            "You are a helpful storyboard assistant. "
            "You help filmmakers and animators develop their storyboard projects.\n\n"
            "When appropriate, you may suggest structured actions by including a "
            "JSON block at the END of your response like:\n"
            "```actions\n"
            '[{"type": "modify_frame", "frame_id": "...", "data": {...}}, '
            '{"type": "suggest_prompt", "prompt": "..."}]\n'
            "```\n\n"
            "Context about the current project:\n"
            f"{context_block}"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message},
        ]

        try:
            raw_response = await self._call_llm(
                messages, temperature=0.7, max_tokens=2048
            )
        except RuntimeError:
            logger.error("LLM call failed during chat for project %s", project_id)
            raise

        # Parse optional structured actions block
        actions: List[Dict[str, Any]] = []
        display_response = raw_response

        actions_fence_open = "```actions"
        if actions_fence_open in raw_response:
            pre, _, rest = raw_response.partition(actions_fence_open)
            actions_json, _, _ = rest.partition("```")
            display_response = pre.strip()
            try:
                parsed = json.loads(actions_json.strip())
                if isinstance(parsed, list):
                    actions = parsed
                else:
                    logger.warning(
                        "chat: actions block is not a JSON array – ignoring"
                    )
            except json.JSONDecodeError as exc:
                logger.warning(
                    "chat: could not parse actions JSON for project %s: %s",
                    project_id,
                    exc,
                )

        logger.info(
            "chat: responded to message for project=%s (actions=%d)",
            project_id,
            len(actions),
        )
        return {"response": display_response, "actions": actions}
