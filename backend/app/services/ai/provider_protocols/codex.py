from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from loguru import logger

from app.services.ai.provider_protocols.base import (
    ALL_RATIOS,
    LEGACY_QUALITY_TIERS,
    ProviderCapabilities,
    ProviderProtocol,
)
from app.services.media.parsers.video_providers.base import (
    BaseImageProvider,
    ImageGenResult,
    TaskStatus,
)

if TYPE_CHECKING:
    from app.services.media.parsers.video_providers.codex_cli import (
        CodexCliProvider,
    )


class _CodexImageAdapter(BaseImageProvider):
    """Adapts CodexCliProvider onto the BaseImageProvider ``generate`` contract.

    ``generate`` returns an ``ImageGenResult`` whose ``image_path`` is the local
    PNG the CLI produced (``image_url`` stays empty — there is no URL). The
    downstream persist step ingests the local file via ``source_path``.

    ``provider_name`` is what the result CALLS itself. Two protocols drive the
    same binary (``codex`` on the subscription session, ``openai-images`` on
    the API key), so a hardcoded "codex" would mislabel half the generations —
    ``_stamp_provider_key`` overwrites ``provider_key`` downstream, but the
    result's own ``provider`` string is read on its way there.
    """

    def __init__(
        self, provider: "CodexCliProvider", provider_name: str = "codex"
    ) -> None:
        self._provider = provider
        self._provider_name = provider_name

    async def generate(self, prompt: str, model: str, **kwargs) -> ImageGenResult:
        # Preferred channel: LOCAL reference paths the workflow already
        # materialized (multi-ref, IC 图1/图2 semantics). Fallback: a single
        # reference_image_url that happens to be a local file; remote urls
        # degrade to plain generate — loudly, not silently.
        paths = [
            str(p)
            for p in (kwargs.get("reference_image_paths") or [])
            if p and os.path.isfile(str(p))
        ]
        if not paths:
            ref = kwargs.get("reference_image_url") or None
            if ref and os.path.isfile(str(ref)):
                paths = [str(ref)]
            elif ref:
                logger.warning(
                    "[codex] reference image {} is not a local file — "
                    "generating without it",
                    str(ref)[:200],
                )
        result = await self._provider.generate_image(
            prompt=prompt,
            aspect=kwargs.get("aspect_ratio") or "",
            model_version=model or None,
            quality=kwargs.get("quality") or None,
            ref_image_paths=paths or None,
            resolution=kwargs.get("resolution") or None,
        )
        return ImageGenResult(
            image_url="",
            image_path=result.local_path,
            provider=self._provider_name,
            model=model or "",
            metadata={"mime": result.mime, **(result.raw or {})},
        )

    async def check_status(self, task_id: str) -> TaskStatus:
        raise NotImplementedError(
            "CodexCliProvider generation is synchronous; check_status is n/a"
        )

    def list_models(self) -> list[str]:
        return []


class CodexProtocol(ProviderProtocol):
    key = "codex"
    label = "Codex CLI (GPT Image)"
    description = (
        "Subprocess gpt-image-2-skill CLI over the local Codex OAuth session "
        "(no api_key; ChatGPT subscription quota). Image generation only."
    )
    model_types = ("image",)
    credential_kind = "server_session"
    aliases = ()
    generation_family = "codex"
    capabilities = ProviderCapabilities(
        ratios=ALL_RATIOS,
        quality=True,
        # The subscription CLI path: low/medium/high only. Measured
        # 2026-09-09 — asking gpt-image-2.5 for ``xhigh`` here comes back
        # rewritten to ``medium`` with no word said, so the tiers stop at
        # what this transport can actually deliver.
        quality_tiers=LEGACY_QUALITY_TIERS,
        resolution=False,  # the model picks the pixel size; --size is ignored
        max_refs=9,
        negative=False,
        video_modes=frozenset(),
        honours_ratio="prompt_hint",
    )

    def build_image_provider(self, row: dict[str, Any]) -> Any:
        from app.services.media.parsers.video_providers.codex_cli import (
            CodexCliProvider,
        )

        actual_model = row.get("actual_model") or ""
        return _CodexImageAdapter(CodexCliProvider()), actual_model
