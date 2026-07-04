"""Field-level "encryption at rest" for admin-managed secret material stored
in ``system_settings`` JSONB values — and, by extension, ``mediahub_models
.api_key`` (see ``MediahubModelRepository``), which reuses the same
``enc:v1:`` marker scheme via ``encrypt_marked`` / ``reveal`` below.

This sits on top of ``app.core.secret_box`` (the P7 Fernet box already used
for ``user_mcp_servers.bearer_token``) and adds two things secret_box alone
doesn't give us:

1. A stable marker prefix (``enc:v1:``) so a stored JSONB value is
   unambiguously "ciphertext" vs "plaintext" — lets ``conceal_for_key`` be
   idempotent (writing the same key twice never double-encrypts) and lets
   ``reveal`` safely no-op on anything unmarked, including legacy plaintext
   rows written before this PR shipped.
2. A registry of WHICH ``system_settings`` keys carry secret material —
   ``SECRET_SETTING_KEYS`` for a flat string value, ``JSONB_SECRET_KEYS``
   for a nested per-provider dict (currently only ``platform.ai_providers``,
   where each provider entry's ``api_key`` / ``app_id`` are secret but
   ``base_url`` is not).

Write side — ``conceal_for_key(key, value)``
=============================================
Called from ``SystemSettingsRepository.update`` / ``upsert_setting`` for
EVERY key (cheap passthrough for the ~95% of keys that aren't secret). NO
dev-key fallback: if ``MEDIAHUB_TOKEN_ENCRYPTION_KEY`` isn't configured,
this RAISES (``secret_box.SecretBoxNotConfigured``) so a write of secret
material fails LOUD instead of silently landing in the DB encrypted under
the public, committed ``DEV_TOKEN_ENCRYPTION_KEY`` — the exact bug this PR
fixes for ``user_mcp_servers`` (which still defaults to
``allow_dev_fallback=True`` and is intentionally NOT touched here).

Read side — ``reveal(value)``
==============================
Called from every internal settings reader (``ai_governance._read_raw``,
``graph_memory._default_settings_reader``,
``langfuse_exporter._langfuse_settings_reader``,
``embedding_config._read_settings``). Fail-SOFT by design — those readers
must never raise (see each module's own docstring): a marked value that
fails to decrypt (no key configured, wrong key, tampered ciphertext) logs
an ERROR and resolves to ``""`` rather than propagating. Non-string /
unmarked values pass through byte-identical, so calling ``reveal``
unconditionally on every settings read is cheap and safe — it recurses into
dicts/lists so the ``platform.ai_providers`` nested shape round-trips
without the reader needing to know which keys are secret.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from loguru import logger

from app.core import secret_box
from app.services.ai.governance.ai_governance import TASK_MODULES

MARKER = "enc:v1:"

# Flat system_settings keys whose VALUE (a string) is secret in its entirety.
SECRET_SETTING_KEYS: frozenset[str] = frozenset(
    {f"ai_module.{m}.api_key" for m in TASK_MODULES}
    | {
        "telemetry.langfuse.secret_key",
        "telemetry.langfuse.public_key",
        "graph_extractor_api_key",
        "graph_embedder_api_key",
    }
)

# system_settings keys whose VALUE is a JSONB dict-of-dicts (per-provider);
# only the named fields inside each inner dict are secret — sibling fields
# (base_url, model, ...) stay plaintext so the admin UI can still show them.
JSONB_SECRET_KEYS: Dict[str, Tuple[str, ...]] = {
    "platform.ai_providers": ("api_key", "app_id"),
}


def is_secret_key(key: str) -> bool:
    """True when ``key`` is a registered flat or JSONB-nested secret key."""
    return key in SECRET_SETTING_KEYS or key in JSONB_SECRET_KEYS


def _encrypt_marked(value: str) -> str:
    """Encrypt ``value`` with the REAL configured key only (no dev-key
    fallback) and prefix with ``MARKER``. Raises
    ``secret_box.SecretBoxNotConfigured`` when no real key is set."""
    ciphertext = secret_box.encrypt(value, allow_dev_fallback=False)
    return f"{MARKER}{ciphertext}"


def encrypt_marked(value: str) -> str:
    """Public strict-encrypt for callers outside system_settings that want
    the same ``enc:v1:`` marker scheme (currently:
    ``MediahubModelRepository.api_key`` and the secrets self-heal
    migration). Raises ``secret_box.SecretBoxNotConfigured`` when no real
    key is configured — fail closed, matching ``conceal_for_key``."""
    return _encrypt_marked(value)


def _conceal_flat(value: Any) -> Any:
    """Encrypt a flat secret value. ``None`` / blank strings pass through
    unchanged (nothing to protect, and it lets a caller omit a field to mean
    "no change" without accidentally encrypting an empty string). Already
    marked ⇒ idempotent no-op — a re-save of the same value must not
    double-encrypt."""
    if value is None:
        return value
    text = value if isinstance(value, str) else str(value)
    if not text.strip():
        return value
    if text.startswith(MARKER):
        return text
    return _encrypt_marked(text)


def _conceal_jsonb_fields(entry: Any, secret_fields: Tuple[str, ...]) -> Any:
    """Encrypt the named secret fields inside one provider dict; every other
    field (base_url, model, ...) passes through untouched."""
    if not isinstance(entry, dict):
        return entry
    out = dict(entry)
    for field_name in secret_fields:
        if field_name in out:
            out[field_name] = _conceal_flat(out[field_name])
    return out


def conceal_for_key(key: str, value: Any) -> Any:
    """WRITE-side chokepoint — call for every system_settings key before it
    hits the DB. Passthrough (identity) for the vast majority of
    (non-secret) keys — cheap membership tests only."""
    if key in SECRET_SETTING_KEYS:
        return _conceal_flat(value)
    if key in JSONB_SECRET_KEYS and isinstance(value, dict):
        secret_fields = JSONB_SECRET_KEYS[key]
        return {
            provider: _conceal_jsonb_fields(entry, secret_fields)
            for provider, entry in value.items()
        }
    return value


def _reveal_str(value: str) -> str:
    if not value.startswith(MARKER):
        return value
    ciphertext = value[len(MARKER) :]
    try:
        plaintext = secret_box.decrypt(ciphertext)
    except Exception as exc:  # noqa: BLE001 — fail-soft read, never raise
        logger.error(f"[secure_settings] decrypt failed for marked value: {exc!r}")
        return ""
    return plaintext if plaintext is not None else ""


def reveal(value: Any) -> Any:
    """READ-side counterpart — recursively decrypts any ``MARKER``-prefixed
    string found in ``value`` (a flat string, or nested inside a dict/list of
    arbitrary depth — covers both a flat secret key and the
    ``platform.ai_providers`` per-provider shape without needing to know
    which keys are secret at read time). Anything else (bool / int / None /
    unmarked str) passes through byte-identical, so callers can call this
    unconditionally on every settings read."""
    if isinstance(value, str):
        return _reveal_str(value)
    if isinstance(value, dict):
        return {k: reveal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [reveal(v) for v in value]
    return value


# ── User BYOK provider keys (Phase 2) ───────────────────────────────────
#
# ``user_settings.settings_json.ai_settings.ai_providers.<provider>.api_key``
# is a SEPARATE table/column from ``system_settings`` — it needs its own
# write/read chokepoints rather than a ``JSONB_SECRET_KEYS`` registry entry
# (that registry is keyed by ``system_settings.key``, which this data never
# has). Reuses the same ``enc:v1:`` marker + ``secret_box`` crypto so a value
# is unambiguously ciphertext vs plaintext regardless of which table it came
# from.
#
# REAL SHAPE: ``api_key`` may be a plain ``str`` OR a ``list[str]`` (Sprint 2
# multi-key rotation — see ``app.services.ai.adapters.factory
# .get_adapter_for_user``). Every element of the list is encrypted /
# decrypted independently so ``RotatingAdapter`` keeps working unchanged.
BYOK_SECRET_FIELDS: Tuple[str, ...] = ("api_key",)


def _conceal_byok_scalar(value: Any) -> Any:
    """Encrypt one secret scalar. Non-string values (should not normally
    occur) pass through untouched rather than being coerced/encrypted, to
    avoid corrupting an unexpected shape."""
    if not isinstance(value, str):
        return value
    return _conceal_flat(value)


def _conceal_byok_field(value: Any) -> Any:
    """Encrypt a BYOK secret field that may be ``str`` or ``list[str]``."""
    if isinstance(value, list):
        return [_conceal_byok_scalar(v) for v in value]
    return _conceal_byok_scalar(value)


def conceal_byok_providers(providers: Any) -> Any:
    """WRITE-side chokepoint for user BYOK ``ai_providers`` — call after
    ``merge_ai_providers`` has merged the incoming payload, right before the
    result is persisted to ``user_settings.settings_json``.

    Encrypts each provider entry's ``api_key`` (``str`` or ``list[str]``);
    every other field (``base_url``, ``model``, ``enabled``, ``app_id``, …)
    and any unknown/non-dict entry passes through untouched. Fail-CLOSED,
    like ``conceal_for_key``: raises ``secret_box.SecretBoxNotConfigured``
    when no real encryption key is configured, so a write of a plaintext
    BYOK key never silently lands in the DB unencrypted.
    """
    if not isinstance(providers, dict):
        return providers
    out: Dict[str, Any] = {}
    for provider, entry in providers.items():
        if not isinstance(entry, dict):
            out[provider] = entry
            continue
        new_entry = dict(entry)
        for field_name in BYOK_SECRET_FIELDS:
            if field_name in new_entry:
                new_entry[field_name] = _conceal_byok_field(new_entry[field_name])
        out[provider] = new_entry
    return out


def _reveal_byok_scalar(value: Any) -> Any:
    return _reveal_str(value) if isinstance(value, str) else value


def _reveal_byok_field(value: Any) -> Any:
    """Decrypt a BYOK secret field that may be ``str`` or ``list[str]``."""
    if isinstance(value, list):
        return [_reveal_byok_scalar(v) for v in value]
    return _reveal_byok_scalar(value)


def reveal_byok_providers(providers: Any) -> Any:
    """READ-side counterpart of :func:`conceal_byok_providers` — call from
    every internal reader of user BYOK ``ai_providers`` so every downstream
    consumer (adapter factory, task resolvers, chat wiring) sees plaintext.

    Fail-SOFT, like ``reveal``: a marked value that fails to decrypt logs an
    ERROR and resolves to ``""`` rather than raising. Non-dict entries and
    unmarked/non-secret fields pass through untouched.
    """
    if not isinstance(providers, dict):
        return providers
    out: Dict[str, Any] = {}
    for provider, entry in providers.items():
        if not isinstance(entry, dict):
            out[provider] = entry
            continue
        new_entry = dict(entry)
        for field_name in BYOK_SECRET_FIELDS:
            if field_name in new_entry:
                new_entry[field_name] = _reveal_byok_field(new_entry[field_name])
        out[provider] = new_entry
    return out


__all__ = [
    "MARKER",
    "SECRET_SETTING_KEYS",
    "JSONB_SECRET_KEYS",
    "BYOK_SECRET_FIELDS",
    "is_secret_key",
    "conceal_for_key",
    "encrypt_marked",
    "reveal",
    "conceal_byok_providers",
    "reveal_byok_providers",
]
