from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.services.ai.provider_protocols.base import ProviderProtocol
from app.services.media.parsers.video_providers.base import (
    BaseImageProvider,
    ImageGenResult,
    TaskStatus,
)

if TYPE_CHECKING:
    from app.services.media.parsers.video_providers.jimeng_cli import (
        JimengCliProvider,
    )


class _JimengImageAdapter(BaseImageProvider):
    """Adapts JimengCliProvider onto the BaseImageProvider ``generate`` contract.

    ``generate`` returns an ``ImageGenResult`` whose ``image_path`` is the local
    file the CLI produced (``image_url`` stays empty — there is no URL). The
    downstream persist step ingests the local file via ``source_path``.
    """

    def __init__(self, provider: "JimengCliProvider") -> None:
        self._provider = provider

    async def generate(self, prompt: str, model: str, **kwargs) -> ImageGenResult:
        result = await self._provider.generate_image(
            prompt=prompt,
            aspect=kwargs.get("aspect_ratio") or "",
            model_version=model or None,
            resolution_type=kwargs.get("resolution") or None,
        )
        return ImageGenResult(
            image_url="",
            image_path=result.local_path,
            provider="jimeng-cli",
            model=model or "",
            metadata={"mime": result.mime, **(result.raw or {})},
        )

    async def check_status(self, task_id: str) -> TaskStatus:
        raise NotImplementedError(
            "JimengCliProvider generation is synchronous; check_status is n/a"
        )

    def list_models(self) -> list[str]:
        return []


class JimengProtocol(ProviderProtocol):
    key = "jimeng-cli"
    label = "Jimeng CLI (即梦)"
    description = (
        "Subprocess dreamina CLI (OAuth session is the credential; no "
        "api_key). Primary image/video generator."
    )
    model_types = ("image", "video")
    aliases = ("jimeng",)
    generation_family = "jimeng-cli"

    def build_image_provider(self, row: dict[str, Any]) -> Any:
        from app.services.media.parsers.video_providers.jimeng_cli import (
            JimengCliProvider,
        )

        actual_model = row.get("actual_model") or ""
        return _JimengImageAdapter(JimengCliProvider()), actual_model

    def build_video_provider(self, row: dict[str, Any]) -> Any:
        from app.services.media.parsers.video_providers.jimeng_cli import (
            JimengCliProvider,
        )

        actual_model = row.get("actual_model") or ""
        return JimengCliProvider(), actual_model
