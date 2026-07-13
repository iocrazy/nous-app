"""Contract: the registry and every dispatch surface agree. If someone adds
a protocol to factory/db_registry without the registry (or vice-versa), CI
goes red here — the guard the 2026-07-13 chat outage lacked."""

from __future__ import annotations

import pytest

from app.services.ai.adapters import factory
from app.services.ai.provider_protocols import (
    chat_provider_keys,
    default_chat_key,
)


@pytest.mark.unit
def test_factory_provider_keys_are_registry_derived():
    assert factory._PROVIDER_KEYS == chat_provider_keys()


@pytest.mark.unit
def test_registry_default_matches_resolve_provider_key_fallback():
    # Unknown label + unknown prefix must land on the registry's default.
    assert factory.resolve_provider_key("totally-unknown", "no-prefix-model") == (
        default_chat_key()
    )
