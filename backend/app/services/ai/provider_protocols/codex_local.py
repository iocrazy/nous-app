from __future__ import annotations

from typing import Any

from app.services.ai.provider_protocols.base import (
    ALL_RATIOS,
    ProviderCapabilities,
    ProviderProtocol,
)


class CodexLocalProtocol(ProviderProtocol):
    """LLM text over the user's OWN machine via the paired nous-codex daemon
    (spec 2026-08-27), AND image generation over that same daemon. No
    credentials on the row: the credential is the user's local
    ``~/.codex/auth.json``, which nous never sees.

    ``generation_family`` is its own value rather than ``"codex"``: the
    capabilities are the server protocol's, but the two must never be
    interchangeable at the registry level. Sharing the family would let
    ``db_registry`` build a SERVER-side CodexCliProvider for a local catalog
    row — running the model on nous' OAuth session instead of the user's
    machine, silently. There is no build hook here on purpose: generation
    happens on the paired device via ``canvas_generation``'s daemon branch,
    so asking this protocol to build one raises ProtocolCapabilityError."""

    key = "codex-local"
    label = "Codex (Local daemon)"
    description = "codex exec on the user's paired device — text (no tools) and images."
    model_types = ("llm", "image")
    is_chat_key = True
    generation_family = "codex-local"
    # Same knobs as the server-side codex protocol: it is the same CLI, just
    # executed on the user's machine (spec §3.2 groups them on one row).
    capabilities = ProviderCapabilities(
        ratios=ALL_RATIOS,
        quality=True,
        resolution=False,  # the model picks the pixel size; --size is ignored
        max_refs=9,
        negative=False,
        video_modes=frozenset(),
        honours_ratio="prompt_hint",
    )

    def build_chat_adapter(
        self, model: str, creds: dict[str, Any], **context: Any
    ) -> Any:
        from app.services.ai.adapters.codex_daemon import CodexDaemonAdapter
        from app.services.ai.provider_protocols.base import ProviderNotConfiguredError

        user_id = str(context.get("user_id") or "").strip()
        if not user_id:
            # Routing is per-user; without a user there is no daemon to dial.
            raise ProviderNotConfiguredError("codex-local", model)
        return CodexDaemonAdapter(user_id=user_id, model=model or "")
