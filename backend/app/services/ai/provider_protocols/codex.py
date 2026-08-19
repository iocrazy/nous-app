from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from loguru import logger

from app.services.ai.provider_protocols.base import ProviderProtocol
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
    """

    def __init__(self, provider: "CodexCliProvider") -> None:
        self._provider = provider

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
        )
        return ImageGenResult(
            image_url="",
            image_path=result.local_path,
            provider="codex",
            model=model or "",
            metadata={"mime": result.mime},
        )

    async def check_status(self, task_id: str) -> TaskStatus:
        raise NotImplementedError(
            "CodexCliProvider generation is synchronous; check_status is n/a"
        )

    def list_models(self) -> list[str]:
        return []


class CodexProtocol(ProviderProtocol):
    key = "codex"
    label = "Codex CLI (GPT Image 2)"
    description = (
        "Subprocess gpt-image-2-skill CLI over the local Codex OAuth session "
        "(no api_key; ChatGPT subscription quota). Image generation only."
    )
    model_types = ("image",)
    aliases = ()
    generation_family = "codex"

    def build_image_provider(self, row: dict[str, Any]) -> Any:
        from app.services.media.parsers.video_providers.codex_cli import (
            CodexCliProvider,
        )

        actual_model = row.get("actual_model") or ""
        return _CodexImageAdapter(CodexCliProvider()), actual_model
