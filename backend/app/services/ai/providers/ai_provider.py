# backend/app/services/ai_provider.py

"""
AI Provider adapter system.

Provides a unified interface for multiple AI providers (OpenAI, DeepSeek, Doubao,
MiniMax, Kimi, Qwen, Ollama, LM Studio, Volcengine speech) using the factory
pattern. All OpenAI-compatible providers share a common base class.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

from loguru import logger
from openai import AsyncOpenAI

from app.services.ai.adapters import (  # noqa: F401 — backward-compat re-export
    QwenAdapter,
)
from app.services.ai.adapters.openai_compat import ensure_v1_base


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str
    # Speaker diarization label (e.g. "S01" / "S02"), an extension field the
    # self-hosted moss-asr server returns on each verbose_json segment. Absent
    # for OpenAI Whisper and the Volcengine ASR path, so it defaults to None —
    # downstream serialization omits the key entirely when None, keeping the
    # persisted segment shape identical for providers that don't diarize.
    speaker: Optional[str] = None


@dataclass
class TranscriptResult:
    text: str
    segments: List[TranscriptSegment]
    language: str
    duration: float


class AIProvider(ABC):
    """Unified interface for all AI providers."""

    # Config keys beyond (api_key, base_url, model) that this provider's
    # constructor needs, forwarded by ``AIProviderFactory.get_provider``.
    # Declared per class so a provider that needs one more credential field
    # (Volcengine speech: ``app_id``) says so here, instead of the factory
    # special-casing its key.
    extra_config_keys: tuple[str, ...] = ()

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
        # max_retries=0 on the SDK — Celery owns the retry policy. Previous
        # stack (SDK retries 2 × 120s + Celery retries 2 × the whole thing)
        # could keep a summary task "processing" for 30-90 minutes before
        # finally failing.
        client_kwargs = {"api_key": api_key, "timeout": 120.0, "max_retries": 0}
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

        # Transcription hotwords (人名 / 术语 domain hints). Two upstreams read
        # them under different names, so send BOTH when present, harmlessly:
        #   - moss-asr reads the `context` form field (extra_body).
        #   - OpenAI Whisper reads the first-class `prompt` param — pass it as a
        #     top-level SDK kwarg, not via extra_body.
        # Pop it here so it never leaks into `**kwargs` (an unknown `hotwords`
        # multipart part would confuse the upstream).
        hotwords = (kwargs.pop("hotwords", "") or "").strip()
        # merge_segments: nous-engine server-side segment merging — fragments
        # coalesce to sentence-final boundaries (speaker boundaries never
        # crossed), turning MOSS's native pause-level splits (~2s on fast
        # speech) into readable sentence segments. Unknown to other
        # OpenAI-compatible servers → harmlessly ignored.
        extra_body: dict = {"timestamps": True, "merge_segments": True}
        create_kwargs: dict = {}
        if hotwords:
            extra_body["context"] = hotwords
            create_kwargs["prompt"] = hotwords

        response = await self._client.audio.transcriptions.create(
            model=kwargs.pop("model", "whisper-1"),
            file=("audio.mp3", audio_bytes),
            response_format="verbose_json",
            timestamp_granularities=["segment"],
            # Self-hosted moss-asr ignores the two OpenAI params above and
            # gates segment timestamps behind its own `timestamps` form field
            # (verified against the live server: segments come back as
            # {start, end, speaker, text}). OpenAI-compatible servers that
            # don't know the field ignore unknown multipart parts.
            extra_body=extra_body,
            **create_kwargs,
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
                    # Optional diarization label from moss-asr; None (absent)
                    # for servers that don't return it.
                    speaker=(
                        seg.get("speaker")
                        if isinstance(seg, dict)
                        else getattr(seg, "speaker", None)
                    ),
                )
            )

        # Minimal OpenAI-compatible servers (e.g. the self-hosted moss-asr)
        # return `duration: null` / `language: null` with the real length in
        # `usage.seconds` — the SDK model keeps the attributes present-but-None,
        # so getattr defaults never engage. A None duration then crashed the
        # `:.1f` formatting downstream. Normalize both here.
        duration = getattr(response, "duration", None)
        if duration is None:
            usage = getattr(response, "usage", None)
            if isinstance(usage, dict):
                duration = usage.get("seconds")
            else:
                duration = getattr(usage, "seconds", None)
        return TranscriptResult(
            text=response.text or "",
            segments=segments,
            language=getattr(response, "language", None) or "unknown",
            duration=float(duration or 0.0),
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
        # Explicit timeout (OpenAI SDK default is 600s, too long for our
        # Celery retry loop) + max_retries=0 so Celery — not the SDK — owns
        # retry. Without these, a summary task could sit in 'processing'
        # for 30-90 min before Celery's own retry policy finally surfaces
        # the failure to the user.
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=120.0,
            max_retries=0,
        )

    async def chat(self, messages: list, model: str = None, **kwargs) -> str:
        response = await self._client.chat.completions.create(
            model=model or self.model,
            messages=messages,
            **kwargs,
        )
        return response.choices[0].message.content

    async def list_models(self) -> List[str]:
        # Let failures propagate — the only async caller is
        # AIProviderFactory.test_connection, which turns the exception
        # into {"success": False, error}. Swallowing to [] here made a
        # disabled/revoked key test as "Connected" (success + empty
        # models), and the stale catalog in settings kept rendering
        # "Detected N models from server".
        models = await self._client.models.list()
        return sorted([m.id for m in models.data])


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
            # Settings stores the root without /v1; the SDK appends /models etc.
            base_url=ensure_v1_base(base_url or "http://localhost:11434/v1"),
            model=model,
            **kwargs,
        )


class LMStudioProvider(OpenAICompatibleProvider):
    def __init__(
        self, api_key: str = "", base_url: str = "", model: str = "", **kwargs
    ):
        super().__init__(
            api_key=api_key or "lm-studio",
            # Settings stores the root without /v1; the SDK appends /models etc.
            base_url=ensure_v1_base(base_url or "http://localhost:1234/v1"),
            model=model,
            **kwargs,
        )


class MiniMaxProvider(OpenAICompatibleProvider):
    """MiniMax - M2.5 series models."""

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "MiniMax-M2.5",
        **kwargs,
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
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "qwen3.5-plus",
        **kwargs,
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1",
            model=model,
            **kwargs,
        )


# ModelScope daily-quota response headers → quota dict keys (docs:
# free-tier rate limits are surfaced on every API response).
_MODELSCOPE_QUOTA_HEADERS = {
    "modelscope-ratelimit-requests-limit": "requests_limit",
    "modelscope-ratelimit-requests-remaining": "requests_remaining",
    "modelscope-ratelimit-model-requests-limit": "model_requests_limit",
    "modelscope-ratelimit-model-requests-remaining": "model_requests_remaining",
}


def _quota_from_headers(headers) -> Optional[dict]:
    """Extract ModelScope daily-quota counters from response headers.

    Returns None when no quota headers are present (non-ModelScope
    upstreams, or the API stops sending them).
    """
    quota: dict = {}
    for header, key in _MODELSCOPE_QUOTA_HEADERS.items():
        value = headers.get(header) if headers is not None else None
        if value is None:
            continue
        try:
            quota[key] = int(value)
        except (TypeError, ValueError):
            continue
    return quota or None


class ModelScopeProvider(OpenAICompatibleProvider):
    """ModelScope (魔搭) — community inference, OpenAI-compatible.

    Model IDs are ``org/name`` (e.g. ``Qwen/Qwen3-235B-A22B``); a free
    tier is available with a ModelScope access token as the API key.
    """

    # Probe model for the auth-validating chat call. The empty-string
    # coalesce matters: test_connection passes model="" (frontend sends
    # no model), which would bypass a plain keyword default and make the
    # probe fail with "Invalid model id: ".
    DEFAULT_MODEL = "Qwen/Qwen3-235B-A22B"

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        **kwargs,
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url or "https://api-inference.modelscope.cn/v1",
            model=model or self.DEFAULT_MODEL,
            **kwargs,
        )
        # Daily-quota counters captured from the last auth-probe response
        # headers; surfaced by test_connection so the Settings card can
        # render "account: N/M left · this model: n/m left".
        self.last_quota: Optional[dict] = None

    async def list_models(self) -> List[str]:
        """Catalog + auth probe (+ quota capture).

        ModelScope's ``/v1/models`` is a PUBLIC catalog — it returns 200
        even with an invalid token, so listing alone would make Test
        Connection a false positive (the exact #659 bug class). A
        1-token chat call validates the key for real; its auth failure
        propagates and test_connection reports it. The raw response also
        carries the modelscope-ratelimit-* daily-quota headers, captured
        into ``last_quota``.
        """
        models = await super().list_models()
        raw = await self._client.chat.completions.with_raw_response.create(
            model=self.model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
        self.last_quota = _quota_from_headers(getattr(raw, "headers", None))
        return models


class VolcengineAsrProvider(AIProvider):
    """Volcengine SPEECH (openspeech bigasr / seed-asr) — the ``volcengine`` key.

    Not the doubao chat client. Until 2026-09-22 this key pointed at
    ``DoubaoProvider``, which cannot transcribe, so ``test_connection`` and
    ``ai_transcription`` both had to catch the key before that class was used.

    What lives here is what fits the ``AIProvider`` shape:

    * ``list_models`` IS the connection probe: it submits a dummy job to each
      resource and returns the models the key is granted. Auth-passed-bad-input
      (40xxxxxx) counts as granted; 45xxxxxx is "resource not granted".
    * ``transcribe`` refuses, by design. The openspeech API PULLS a public URL
      rather than accepting an upload, so building the request needs the
      resource's owner (for the signed /media token) or an object-store signed
      URL host-swapped to the public base — workflow concerns that live in
      ``app.workflows.ai_transcription._run_volcengine_asr`` (which calls
      ``VolcengineASRService``). Accepting a local ``audio_path`` here would
      promise something this layer cannot do.
    """

    extra_config_keys = ("app_id",)

    # (resource id, model name) — probe order is the result order.
    _PROBE_RESOURCES = (
        ("volc.seedasr.auc", "seed-asr"),
        ("volc.bigasr.auc", "bigasr"),
    )

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        app_id: str = "",
        **kwargs,
    ):
        super().__init__(api_key=api_key, base_url=base_url, model=model)
        self.app_id = app_id or ""

    async def chat(self, messages: list, model: str = None, **kwargs) -> str:
        raise NotImplementedError(
            "volcengine is the Volcengine speech (ASR) key, not a chat "
            "provider — the Doubao chat key is 'doubao'"
        )

    async def transcribe(self, audio_path: str, **kwargs) -> TranscriptResult:
        raise NotImplementedError(
            "Volcengine ASR pulls a public audio URL; transcription runs in "
            "app.workflows.ai_transcription._run_volcengine_asr, not through "
            "a provider-level upload"
        )

    def _auth_headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "X-Api-Request-Id": "test-connection",
            "X-Api-Sequence": "-1",
        }
        # Old console: app_id + access key. New console: api key alone.
        if self.app_id:
            headers["X-Api-App-Key"] = self.app_id
            headers["X-Api-Access-Key"] = self.api_key
        else:
            headers["X-Api-Key"] = self.api_key
        return headers

    async def list_models(self) -> List[str]:
        """The granted models, or raise with the provider's own reason."""
        import httpx

        from app.services.ai.transcribe.volcengine_asr_service import SUBMIT_URL

        if not self.api_key:
            raise ValueError("API Key is required")

        headers = self._auth_headers()
        granted: List[str] = []
        last_error = ""
        async with httpx.AsyncClient(timeout=10) as client:
            for resource_id, model_name in self._PROBE_RESOURCES:
                resp = await client.post(
                    SUBMIT_URL,
                    headers={**headers, "X-Api-Resource-Id": resource_id},
                    json={
                        "user": {"uid": "test"},
                        "audio": {
                            "format": "mp3",
                            "url": "https://example.com/test.mp3",
                        },
                        "request": {"model_name": "bigmodel"},
                    },
                )
                status_code = resp.headers.get("X-Api-Status-Code", "")
                # 20xxxxxx = success, 40xxxxxx = client error (auth passed, bad
                # input); 45xxxxxx = resource not granted — must reject.
                if status_code.startswith("20") or status_code.startswith("40"):
                    granted.append(model_name)
                else:
                    last_error = resp.headers.get(
                        "X-Api-Message", f"Status: {status_code}"
                    )
        if not granted:
            raise RuntimeError(last_error or "No model access granted")
        return granted


# ---------------------------------------------------------------------------
# The provider registry — ONE source, projected here.
# ---------------------------------------------------------------------------
# ``provider_protocols`` is the single source of truth: each protocol names its
# ``AIProvider`` subclass via ``ai_provider_name``. The one thing this module
# contributes is the explicit list of protocols that have NO provider, below.
#
# There used to be a second dict here too, ``BYOK_ONLY_PROVIDERS`` (volcengine,
# minimax, kimi, ollama, lmstudio). Those are protocols now (2026-09-22), and
# the dict is gone: test_provider_registry_is_derived fails if it comes back.

# Protocols that have no AIProvider at all, and why. Being listed here is a
# claim, and test_provider_registry_is_derived re-checks it against the
# protocol's own ``ai_provider_name`` rather than trusting the comment.
PROTOCOLS_WITHOUT_AI_PROVIDER = {
    "claude": "Anthropic Messages API — no AIProvider implementation exists",
    "codex-local": "runs on the user's device via the daemon; no HTTP endpoint",
    "openai-images": "image-only, CLI subprocess over the Images API",
    "jimeng-cli": "image/video-only, CLI subprocess",
    "jimeng-local": "image/video-only, on the user's device",
    "ark": "image/video-only, Volcengine task protocol",
}


def _protocol_providers() -> dict:
    """``{protocol key: AIProvider subclass}`` for every protocol that names one.

    The name is resolved against this module's globals rather than imported by
    the protocol, so ``provider_protocols`` stays free of any dependency on the
    provider layer — which is what lets the dependency point this way at all.
    """
    from app.services.ai.provider_protocols import all_protocols

    out = {}
    for protocol in all_protocols():
        name = getattr(protocol, "ai_provider_name", "")
        if not name:
            continue
        cls = globals().get(name)
        if cls is None:
            # Loud, not silent: a typo'd or deleted class name would otherwise
            # drop the protocol out of the registry exactly the way the
            # hand-written dict used to, which is the failure being removed.
            raise RuntimeError(
                f"protocol {protocol.key!r} names AIProvider {name!r}, which "
                f"does not exist in {__name__}"
            )
        out[protocol.key] = cls
    return out


def _build_registry() -> dict:
    """Every entry comes from a protocol — there is nothing else to add."""
    return _protocol_providers()


class AIProviderFactory:
    """Factory for creating AI provider instances."""

    # DERIVED, not hand-written — see ``_build_registry`` above. This used to be
    # a second list maintained by hand next to ``provider_protocols``; adding a
    # key to one did not add it to the other, and that is exactly how ``nous``
    # came to be missing here while being present there (PR #2375: every
    # transcription raised ``Unknown provider: nous``).
    #
    # Pinned key-for-key by tests/test_provider_registry_is_derived.py.
    _registry = _build_registry()

    @classmethod
    def get_provider(cls, provider_key: str, config: dict = None) -> AIProvider:
        """Get provider instance by key with config.

        Args:
            provider_key: A protocol key that names an AIProvider
                (see ``available_providers``).
            config: Dict with optional keys: api_key, base_url, model, plus
                whatever the provider class lists in ``extra_config_keys``.

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
        extra = {key: config.get(key) or "" for key in provider_cls.extra_config_keys}
        return provider_cls(
            api_key=config.get("api_key", ""),
            base_url=config.get("base_url", ""),
            model=config.get("model", ""),
            **extra,
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
            return {
                "success": True,
                "models": models,
                "error": None,
                # ModelScope surfaces daily-quota counters on response
                # headers; other providers simply don't set last_quota.
                "quota": getattr(provider, "last_quota", None),
            }
        except Exception as e:
            logger.warning(f"Connection test failed for {provider_key}: {e}")
            return {"success": False, "models": None, "error": str(e)}

    @classmethod
    def available_providers(cls) -> list:
        """Return list of registered provider keys."""
        return list(cls._registry.keys())
