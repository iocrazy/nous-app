# backend/app/services/ai_provider.py

"""
AI Provider adapter system.

Provides a unified interface for multiple AI providers (OpenAI, DeepSeek, Doubao,
MiniMax, Kimi, Qwen, Ollama, LM Studio) using the factory pattern. All
OpenAI-compatible providers share a common base class.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List

from loguru import logger
from openai import AsyncOpenAI


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptResult:
    text: str
    segments: List[TranscriptSegment]
    language: str
    duration: float


class AIProvider(ABC):
    """Unified interface for all AI providers."""

    def __init__(self, api_key: str = "", base_url: str = "", model: str = ""):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    @abstractmethod
    async def chat(self, messages: list, model: str = None, **kwargs) -> str:
        """Send chat completion request."""
        ...

    async def transcribe(self, audio_path: str, **kwargs) -> TranscriptResult:
        """Transcribe audio (only supported by some providers)."""
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support transcription"
        )

    async def list_models(self) -> List[str]:
        """List available models from the provider."""
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support listing models"
        )


class OpenAIProvider(AIProvider):
    """OpenAI - GPT models + Whisper."""

    def __init__(
        self, api_key: str = "", base_url: str = "", model: str = "gpt-4o", **kwargs
    ):
        super().__init__(api_key=api_key, base_url=base_url, model=model)
        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**client_kwargs)

    async def chat(self, messages: list, model: str = None, **kwargs) -> str:
        response = await self._client.chat.completions.create(
            model=model or self.model,
            messages=messages,
            **kwargs,
        )
        return response.choices[0].message.content

    async def transcribe(self, audio_path: str, **kwargs) -> TranscriptResult:
        import aiofiles
        async with aiofiles.open(audio_path, "rb") as f:
            audio_bytes = await f.read()
        response = await self._client.audio.transcriptions.create(
            model=kwargs.pop("model", "whisper-1"),
            file=("audio.mp3", audio_bytes),
            response_format="verbose_json",
            timestamp_granularities=["segment"],
            **kwargs,
        )

        segments = []
        for seg in getattr(response, "segments", []) or []:
            segments.append(
                TranscriptSegment(
                    start=(
                        seg.get("start", 0.0)
                        if isinstance(seg, dict)
                        else getattr(seg, "start", 0.0)
                    ),
                    end=(
                        seg.get("end", 0.0)
                        if isinstance(seg, dict)
                        else getattr(seg, "end", 0.0)
                    ),
                    text=(
                        seg.get("text", "")
                        if isinstance(seg, dict)
                        else getattr(seg, "text", "")
                    ),
                )
            )

        return TranscriptResult(
            text=response.text,
            segments=segments,
            language=getattr(response, "language", "unknown"),
            duration=getattr(response, "duration", 0.0),
        )

    async def list_models(self) -> List[str]:
        models = await self._client.models.list()
        return sorted([m.id for m in models.data])


class OpenAICompatibleProvider(AIProvider):
    """Base for OpenAI-compatible providers (DeepSeek, Doubao, Ollama, LM Studio)."""

    def __init__(
        self, api_key: str = "", base_url: str = "", model: str = "", **kwargs
    ):
        super().__init__(api_key=api_key, base_url=base_url, model=model)
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def chat(self, messages: list, model: str = None, **kwargs) -> str:
        response = await self._client.chat.completions.create(
            model=model or self.model,
            messages=messages,
            **kwargs,
        )
        return response.choices[0].message.content

    async def list_models(self) -> List[str]:
        try:
            models = await self._client.models.list()
            return sorted([m.id for m in models.data])
        except Exception as e:
            logger.warning(f"Failed to list models from {self.base_url}: {e}")
            return []


class DeepSeekProvider(OpenAICompatibleProvider):
    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "deepseek-chat",
        **kwargs,
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url or "https://api.deepseek.com/v1",
            model=model,
            **kwargs,
        )


class DoubaoProvider(OpenAICompatibleProvider):
    def __init__(
        self, api_key: str = "", base_url: str = "", model: str = "", **kwargs
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url or "https://ark.cn-beijing.volces.com/api/v3",
            model=model,
            **kwargs,
        )


class OllamaProvider(OpenAICompatibleProvider):
    def __init__(
        self, api_key: str = "", base_url: str = "", model: str = "", **kwargs
    ):
        super().__init__(
            api_key=api_key or "ollama",
            base_url=base_url or "http://localhost:11434/v1",
            model=model,
            **kwargs,
        )


class LMStudioProvider(OpenAICompatibleProvider):
    def __init__(
        self, api_key: str = "", base_url: str = "", model: str = "", **kwargs
    ):
        super().__init__(
            api_key=api_key or "lm-studio",
            base_url=base_url or "http://localhost:1234/v1",
            model=model,
            **kwargs,
        )


class MiniMaxProvider(OpenAICompatibleProvider):
    """MiniMax - M2.5 series models."""

    def __init__(
        self, api_key: str = "", base_url: str = "", model: str = "MiniMax-M2.5", **kwargs
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url or "https://api.minimax.chat/v1",
            model=model,
            **kwargs,
        )


class KimiProvider(OpenAICompatibleProvider):
    """Kimi (Moonshot AI) - K2 series models."""

    def __init__(
        self, api_key: str = "", base_url: str = "", model: str = "kimi-k2.5", **kwargs
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url or "https://api.moonshot.cn/v1",
            model=model,
            **kwargs,
        )


class QwenProvider(OpenAICompatibleProvider):
    """Qwen (Alibaba Cloud) - Qwen3 series models."""

    def __init__(
        self, api_key: str = "", base_url: str = "", model: str = "qwen3.5-plus", **kwargs
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1",
            model=model,
            **kwargs,
        )


class AIProviderFactory:
    """Factory for creating AI provider instances."""

    _registry = {
        "openai": OpenAIProvider,
        "deepseek": DeepSeekProvider,
        "doubao": DoubaoProvider,
        "minimax": MiniMaxProvider,
        "kimi": KimiProvider,
        "qwen": QwenProvider,
        "ollama": OllamaProvider,
        "lmstudio": LMStudioProvider,
    }

    @classmethod
    def get_provider(cls, provider_key: str, config: dict = None) -> AIProvider:
        """Get provider instance by key with config.

        Args:
            provider_key: One of 'openai', 'deepseek', 'doubao', 'minimax', 'kimi', 'qwen', 'ollama', 'lmstudio'.
            config: Dict with optional keys: api_key, base_url, model.

        Returns:
            An AIProvider instance.

        Raises:
            ValueError: If provider_key is not registered.
        """
        provider_cls = cls._registry.get(provider_key)
        if not provider_cls:
            raise ValueError(
                f"Unknown provider: {provider_key}. "
                f"Available: {', '.join(cls._registry.keys())}"
            )
        config = config or {}
        return provider_cls(
            api_key=config.get("api_key", ""),
            base_url=config.get("base_url", ""),
            model=config.get("model", ""),
        )

    @classmethod
    async def test_connection(cls, provider_key: str, config: dict) -> dict:
        """Test if provider is reachable.

        Returns:
            Dict with keys: success (bool), models (list[str] | None), error (str | None).
        """
        try:
            provider = cls.get_provider(provider_key, config)
            models = await provider.list_models()
            return {"success": True, "models": models, "error": None}
        except Exception as e:
            logger.warning(f"Connection test failed for {provider_key}: {e}")
            return {"success": False, "models": None, "error": str(e)}

    @classmethod
    def available_providers(cls) -> list:
        """Return list of registered provider keys."""
        return list(cls._registry.keys())
