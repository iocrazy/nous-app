"""Assembles the protocol instances and the query functions. The public
names re-exported by __init__ preserve Phase 1's API exactly."""

from __future__ import annotations

from typing import Optional

from app.services.ai.provider_protocols.ark import ArkProtocol
from app.services.ai.provider_protocols.base import ProviderProtocol
from app.services.ai.provider_protocols.claude import ClaudeProtocol
from app.services.ai.provider_protocols.codex import CodexProtocol
from app.services.ai.provider_protocols.deepseek import DeepSeekProtocol
from app.services.ai.provider_protocols.doubao import DoubaoProtocol
from app.services.ai.provider_protocols.jimeng import JimengProtocol
from app.services.ai.provider_protocols.modelscope import ModelScopeProtocol
from app.services.ai.provider_protocols.openai import OpenAIProtocol
from app.services.ai.provider_protocols.qwen import QwenProtocol

PROTOCOLS: tuple[ProviderProtocol, ...] = (
    QwenProtocol(),
    OpenAIProtocol(),
    ClaudeProtocol(),
    DeepSeekProtocol(),
    DoubaoProtocol(),
    ModelScopeProtocol(),
    ArkProtocol(),
    JimengProtocol(),
    CodexProtocol(),
)


def all_protocols() -> tuple[ProviderProtocol, ...]:
    return PROTOCOLS


def chat_provider_keys() -> frozenset[str]:
    return frozenset(p.key for p in PROTOCOLS if p.is_chat_key)


def generation_keys_for(family: str) -> frozenset[str]:
    out: set[str] = set()
    for p in PROTOCOLS:
        if p.generation_family == family:
            out.add(p.key)
            out.update(p.aliases)
    return frozenset(out)


def default_chat_key() -> str:
    return _default_chat_protocol().key


def _default_chat_protocol() -> ProviderProtocol:
    for p in PROTOCOLS:
        if p.is_chat_key and p.is_default:
            return p
    raise RuntimeError("no default chat protocol configured")


def get_chat_protocol(key: str) -> ProviderProtocol:
    """The chat protocol for a provider key. An unknown key falls back to the
    default (qwen) protocol — mirrors factory's historical ``else`` branch,
    which routed anything non-matching to the OpenAI-compatible QwenAdapter."""
    for p in PROTOCOLS:
        if p.is_chat_key and p.key == key:
            return p
    return _default_chat_protocol()


def resolve_generation_protocol(actual_provider: str) -> Optional[ProviderProtocol]:
    """The image/video protocol whose key OR alias matches ``actual_provider``
    (case-insensitive), or None."""
    label = (actual_provider or "").lower()
    for p in PROTOCOLS:
        if p.generation_family is None:
            continue
        if label == p.key or label in p.aliases:
            return p
    return None
