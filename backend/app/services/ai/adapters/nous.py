"""NousAdapter — the self-hosted nous-engine gateway.

A thin :class:`OpenAICompatibleAdapter` subclass, like every other member of
that family. What makes it its own class is that it has NO default endpoint:
nous-engine runs on a private address that differs per deployment, so the
catalog row's ``base_url`` is a required part of the credential rather than an
override. ``api_url`` is therefore positional and unconditional here, where
``OpenAIAdapter`` and ``QwenAdapter`` both fall back to a vendor URL.

Model weights, vLLM flags and GPU allocation live in nous-engine; this side
only speaks the protocol (2026-09-16 用户原则 — nous-app 是业务层).
"""

from __future__ import annotations

from app.services.ai.adapters.openai_compat import OpenAICompatibleAdapter


class NousAdapter(OpenAICompatibleAdapter):
    """Self-hosted nous-engine OpenAI-compatible endpoint."""

    def __init__(
        self,
        api_url: str,
        api_key: str = "",
        default_model: str = "",
        timeout_seconds: float = 60.0,
    ) -> None:
        super().__init__(
            api_url=api_url,
            api_key=api_key,
            default_model=default_model,
            timeout_seconds=timeout_seconds,
        )
