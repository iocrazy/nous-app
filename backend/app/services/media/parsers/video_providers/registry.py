import logging
from dataclasses import dataclass

from app.services.media.parsers.video_providers.base import (
    BaseImageProvider,
    BaseVideoProvider,
)

logger = logging.getLogger(__name__)


@dataclass
class ModelInfo:
    model_id: str
    provider: str
    type: str  # 'image' or 'video'
    display_name: str


class ProviderRegistry:
    def __init__(self) -> None:
        self._image_providers: dict[str, BaseImageProvider] = {}
        self._video_providers: dict[str, BaseVideoProvider] = {}

    def register_image_provider(self, name: str, provider: BaseImageProvider) -> None:
        if name in self._image_providers:
            logger.warning("Overwriting existing image provider: %s", name)
        self._image_providers = {**self._image_providers, name: provider}
        logger.info("Registered image provider: %s", name)

    def register_video_provider(self, name: str, provider: BaseVideoProvider) -> None:
        if name in self._video_providers:
            logger.warning("Overwriting existing video provider: %s", name)
        self._video_providers = {**self._video_providers, name: provider}
        logger.info("Registered video provider: %s", name)

    def get_image_provider(self, name: str) -> BaseImageProvider:
        if name not in self._image_providers:
            raise KeyError(f"Image provider not found: {name!r}")
        return self._image_providers[name]

    def get_video_provider(self, name: str) -> BaseVideoProvider:
        if name not in self._video_providers:
            raise KeyError(f"Video provider not found: {name!r}")
        return self._video_providers[name]

    def list_image_providers(self) -> list[str]:
        return list(self._image_providers.keys())

    def list_video_providers(self) -> list[str]:
        return list(self._video_providers.keys())

    def list_available_models(self) -> list[ModelInfo]:
        models: list[ModelInfo] = []

        for provider_name, provider in self._image_providers.items():
            try:
                for model_id in provider.list_models():
                    models.append(
                        ModelInfo(
                            model_id=model_id,
                            provider=provider_name,
                            type="image",
                            display_name=f"{provider_name}/{model_id}",
                        )
                    )
            except Exception:
                logger.exception(
                    "Failed to list models for image provider: %s", provider_name
                )

        for provider_name, provider in self._video_providers.items():
            try:
                for model_id in provider.list_models():
                    models.append(
                        ModelInfo(
                            model_id=model_id,
                            provider=provider_name,
                            type="video",
                            display_name=f"{provider_name}/{model_id}",
                        )
                    )
            except Exception:
                logger.exception(
                    "Failed to list models for video provider: %s", provider_name
                )

        return models


# Singleton
provider_registry = ProviderRegistry()
