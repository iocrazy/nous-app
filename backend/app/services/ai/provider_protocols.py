"""Single source of truth for AI provider *protocols* (dispatch surfaces).

Before this module, two code-internal lists drifted independently:
  - chat/embedding/asr: ``factory._PROVIDER_KEYS`` (adapter keys)
  - image/video: ``db_registry._ARK_PROVIDERS`` / ``_JIMENG_PROVIDERS``

Admins typed ``actual_provider`` as free text against those hidden lists —
the exact drift that took AI Chat down for a week (#1313). This registry
makes the set explicit; the two lists are now DERIVED from it (see
``chat_provider_keys`` / ``generation_keys_for``), and the admin UI renders
its dropdown from ``all_protocols``.

Adding a protocol = one row here (+ its adapter). The contract tests in
``tests/test_provider_protocols.py`` fail if a dispatch surface and this
registry disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ProviderProtocol:
    """One provider protocol the platform can dispatch a catalog row to.

    ``key`` is the canonical ``actual_provider`` value; ``aliases`` are
    accepted equivalents (image/video dispatch matches key OR alias).
    ``is_chat_key`` marks a buildable chat adapter key (factory). A non-None
    ``generation_family`` marks an image/video dispatch family
    (``db_registry``). ``model_types`` drives the admin dropdown hint only.
    """

    key: str
    label: str
    description: str
    model_types: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    is_chat_key: bool = False
    generation_family: Optional[str] = None
    is_default: bool = False


PROTOCOLS: tuple[ProviderProtocol, ...] = (
    ProviderProtocol(
        key="qwen",
        label="OpenAI-Compatible (generic)",
        description=(
            "Standard OpenAI /chat/completions contract. The fail-open "
            "default: any self-hosted or aggregated endpoint (vLLM, Nous, "
            "etc.) works here — the base_url + key is the whole credential."
        ),
        model_types=("llm", "embedding", "asr"),
        is_chat_key=True,
        is_default=True,
    ),
    ProviderProtocol(
        key="openai",
        label="OpenAI (native)",
        description="Native OpenAI API (multimodal gpt-*/o1/o3).",
        model_types=("llm", "embedding", "asr"),
        is_chat_key=True,
    ),
    ProviderProtocol(
        key="claude",
        label="Claude (Anthropic)",
        description="Native Anthropic Messages API (claude-*).",
        model_types=("llm",),
        is_chat_key=True,
    ),
    ProviderProtocol(
        key="deepseek",
        label="DeepSeek",
        description="DeepSeek chat-completions endpoint.",
        model_types=("llm",),
        is_chat_key=True,
    ),
    ProviderProtocol(
        key="doubao",
        label="Doubao (chat)",
        description="Volcengine Doubao chat-completions (doubao-*/ep-*).",
        model_types=("llm", "embedding", "asr"),
        is_chat_key=True,
    ),
    ProviderProtocol(
        key="modelscope",
        label="ModelScope",
        description="ModelScope org/name models (BYO key).",
        model_types=("llm",),
        is_chat_key=True,
    ),
    ProviderProtocol(
        key="ark",
        label="Ark image/video (方舟)",
        description="Volcengine Ark task protocol for image/video generation.",
        model_types=("image", "video"),
        aliases=("doubao",),
        generation_family="ark",
    ),
    ProviderProtocol(
        key="jimeng-cli",
        label="Jimeng CLI (即梦)",
        description=(
            "Subprocess dreamina CLI (OAuth session is the credential; no "
            "api_key). Primary image/video generator."
        ),
        model_types=("image", "video"),
        aliases=("jimeng",),
        generation_family="jimeng-cli",
    ),
)


def all_protocols() -> tuple[ProviderProtocol, ...]:
    return PROTOCOLS


def chat_provider_keys() -> frozenset[str]:
    """Buildable chat adapter keys — the derived ``factory._PROVIDER_KEYS``."""
    return frozenset(p.key for p in PROTOCOLS if p.is_chat_key)


def generation_keys_for(family: str) -> frozenset[str]:
    """All accepted ``actual_provider`` strings (key + aliases) for an
    image/video dispatch family (``ark`` / ``jimeng-cli``)."""
    out: set[str] = set()
    for p in PROTOCOLS:
        if p.generation_family == family:
            out.add(p.key)
            out.update(p.aliases)
    return frozenset(out)


def default_chat_key() -> str:
    """The fail-open chat protocol key (must equal resolve_provider_key's
    final fallback)."""
    for p in PROTOCOLS:
        if p.is_chat_key and p.is_default:
            return p.key
    raise RuntimeError("no default chat protocol configured")
