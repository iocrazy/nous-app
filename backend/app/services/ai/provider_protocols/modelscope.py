from __future__ import annotations

from app.services.ai.provider_protocols.base import ProviderProtocol


class ModelScopeProtocol(ProviderProtocol):
    key = "modelscope"
    label = "ModelScope"
    description = "ModelScope org/name models (BYO key)."
    model_types = ("llm",)
    is_chat_key = True
