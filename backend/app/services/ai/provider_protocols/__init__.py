"""Provider-protocol registry (Phase 2 package). Single source of truth for
provider protocols — metadata AND per-protocol adapter/provider construction.

Public API preserved from the Phase 1 flat module so every existing
``from app.services.ai.provider_protocols import ...`` keeps working."""

from __future__ import annotations

from app.services.ai.provider_protocols._registry import (
    PROTOCOLS,
    all_protocols,
    chat_provider_keys,
    default_chat_key,
    generation_keys_for,
    get_chat_protocol,
    resolve_generation_protocol,
)
from app.services.ai.provider_protocols.base import (
    ProtocolCapabilityError,
    ProviderProtocol,
)

__all__ = [
    "PROTOCOLS",
    "ProviderProtocol",
    "ProtocolCapabilityError",
    "all_protocols",
    "chat_provider_keys",
    "generation_keys_for",
    "default_chat_key",
    "get_chat_protocol",
    "resolve_generation_protocol",
]
