"""Generic AI media-generation service (image + video).

Provider-agnostic single-image and single-video generation via the platform
provider registry / ``mediahub_models`` DB catalog. Extracted from
``StoryboardAIService`` so the two LIVE consumers of that class's generic
generation capability — ``workflows/script_shot_generate`` (script editor shot
imagery) and ``services/ai/tools/generate_media_tools`` (the agent
GenerateImage / GenerateVideo tools) — no longer transitively import the
retired Storyboard Workbench stack (storyboard_service / storyboard_repository /
the tombstone models). The storyboard-specific code path is being retired
separately; this class carries only the parts those live callers actually use.

Deliberate deletions vs the storyboard original: the ``character_ids`` argument
and the ``build_project_style_fragment`` style-injection step are both dropped.
Each reached the retired storyboard stack (the character store / the
storyboard-project → canonical-project style bridge) and neither was ever
exercised by the two live consumers — they pass ``project_id=""`` and no
character ids — so no live behaviour changes.

Credentials are DB-only (铁律 2026-07-07): the image/video registries ship
EMPTY, so every call resolves its provider against the platform
``mediahub_models`` catalog rather than env.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, Optional

from loguru import logger

from app.services.media.parsers.video_providers import (
    ImageGenResult,
    VideoGenResult,
    provider_registry,
)

# The legacy default image model. When the image registry misses and we resolve
# a provider from the DB catalog, this sentinel yields to the catalog row's
# actual_model — a caller that passed an explicit non-default model keeps it.
# Kept equal to script_shot_generate._DEFAULT_MODEL by contract.
_DEFAULT_IMAGE_MODEL = "dall-e-3"


class ImageGenerationService:
    """Provider-agnostic image + video generation orchestration."""

    async def generate_image(
        self,
        project_id: str,
        node_id: str,
        prompt: str,
        model: str,
        provider_name: Optional[str],
        reference_image_url: Optional[str] = None,
        aspect_ratio: str = "16:9",
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate a single image via the named provider.

        Note: In production this method is called from DBOS steps / agent tools,
        not directly from request handlers.

        Args:
            project_id: Owning-project id, for logging only (both live callers
                pass "").
            node_id: Canvas/shot id (for logging / provider provenance).
            prompt: Base image generation prompt.
            model: Model identifier understood by the provider.
            provider_name: Registered image provider name, or None to resolve
                from the DB catalog.
            reference_image_url: Optional URL of a reference image passed to the
                provider.
            aspect_ratio: Output aspect ratio string (e.g. "16:9", "1:1").
            user_id: Requesting user, threaded to the catalog resolver so
                owner-scoped rows (migration 431) resolve only for their owner.

        Returns:
            ImageGenResult serialised as a dict.

        Raises:
            RuntimeError: If no image model is configured in the catalog
                (registry miss + empty catalog), or the provider raises during
                generation.
        """
        try:
            effective_prompt = prompt

            # Provider precedence: the in-process registry wins when it has the
            # named provider (reserved for future in-proc providers). Today the
            # image registry ships EMPTY, so every call KeyErrors here and
            # resolves against the DB mediahub_models catalog (house rule:
            # provider config lives in the DB, not env).
            #
            # Model precedence on the DB path: an explicit non-default caller
            # model wins; the 'dall-e-3' default sentinel (or an empty model)
            # yields to the catalog row's actual_model — so a shot dispatched
            # with the legacy default lands on whatever image model the admin
            # enabled (e.g. a doubao-seedream id).
            try:
                image_provider = provider_registry.get_image_provider(provider_name)
                gen_model = model
            except KeyError:
                from app.services.media.parsers.video_providers.db_registry import (
                    resolve_image_provider,
                )

                image_provider, actual_model = await resolve_image_provider(
                    provider_name, user_id=user_id
                )
                gen_model = (
                    model if (model and model != _DEFAULT_IMAGE_MODEL) else actual_model
                )

            result: ImageGenResult = await image_provider.generate(
                effective_prompt,
                gen_model,
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

        except Exception as exc:
            logger.error(
                "Image generation failed for project=%s node=%s: %s",
                project_id,
                node_id,
                exc,
            )
            raise

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
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate a video clip from a source image via the named provider.

        Args:
            project_id: Owning-project id (for logging).
            node_id: Canvas/shot id (for logging).
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
            # In practice this in-proc registry ships EMPTY for video (same as
            # images), so every call KeyErrors and resolves against the DB
            # mediahub_models catalog below (config env→DB house rule). The
            # registry hit is kept for tests / future in-proc providers.
            try:
                video_provider = provider_registry.get_video_provider(provider_name)
            except KeyError:
                return await self._generate_video_via_catalog(
                    project_id=project_id,
                    node_id=node_id,
                    source_image_url=source_image_url,
                    prompt=prompt,
                    provider_name=provider_name,
                    model=model,
                    user_id=user_id,
                )

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

        except Exception as exc:
            logger.error(
                "Video generation failed for project=%s node=%s: %s",
                project_id,
                node_id,
                exc,
            )
            raise

    async def _generate_video_via_catalog(
        self,
        *,
        project_id: str,
        node_id: str,
        source_image_url: str,
        prompt: str,
        provider_name: str,
        model: str,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """DB-catalog video fallback (G4-B0), mirroring the image path.

        Resolves the provider from ``mediahub_models`` (only jimeng-cli has a
        wired video path today) and bridges the durable ``/cover`` source URL
        back to its local file for image2video — both filesystem AND
        object-store rows resolve now (Task 2: ``generated_media_local_path``
        materializes sb:// rows to a temp file); only a raw/unbridgeable
        source URL degrades to text2video. The CLI product is a LOCAL FILE,
        so the result carries ``video_path`` and an empty ``video_url``;
        callers must persist through the generated-media store to mint a
        servable URL.
        """
        from app.services.library.generated_media_service import (
            generated_media_local_path,
        )
        from app.services.media.parsers.video_providers import db_registry

        video_provider, actual_model = await db_registry.resolve_video_provider(
            provider_name or None, user_id=user_id
        )
        gen_model = model or actual_model

        async with generated_media_local_path(
            source_image_url, media_kind="image"
        ) as image_path:
            cli_result = await video_provider.generate_video(
                prompt=prompt,
                aspect="",
                model_version=gen_model or None,
                image_path=image_path,
            )
            logger.info(
                "Video generated via catalog for project=%s node=%s model=%s i2v=%s",
                project_id,
                node_id,
                gen_model,
                bool(image_path),
            )
        result = VideoGenResult(
            video_url="",
            video_path=cli_result.local_path,
            provider="jimeng-cli",
            model=gen_model or "",
            metadata={
                "mime": getattr(cli_result, "mime", None),
                **(getattr(cli_result, "raw", None) or {}),
            },
        )
        return asdict(result)


__all__ = ["ImageGenerationService"]
