from __future__ import annotations

from typing import Any

from app.services.ai.provider_protocols.base import ProviderProtocol


class CodexLocalProtocol(ProviderProtocol):
    """LLM text over the user's OWN machine via the paired nous-codex daemon
    (spec 2026-08-27). No credentials on the row: the credential is the
    user's local ``~/.codex/auth.json``, which nous never sees."""

    key = "codex-local"
    label = "Codex (Local daemon)"
    description = "codex exec on the user's paired device — text only, no tools."
    model_types = ("llm",)
    is_chat_key = True

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
