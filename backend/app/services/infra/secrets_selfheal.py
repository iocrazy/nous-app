"""Secret-at-rest self-heal — one-shot, idempotent re-encryption sweep.

Runs at startup (``app.startup.bootstrap``) and on demand via
``POST /admin/settings/encrypt-secrets``. ONLY when ``secret_box
.is_configured()`` — i.e. a REAL ``MEDIAHUB_TOKEN_ENCRYPTION_KEY`` env key
is present (never the public committed dev fallback). It rewrites secret
material that is either:

- **plaintext** (written before the conceal chokepoints shipped), or
- **dev-keyed** (encrypted under the public ``DEV_TOKEN_ENCRYPTION_KEY``
  committed in this repo — the pre-hardening state of
  ``user_mcp_servers.bearer_token`` when prod had no env key)

...to ciphertext under the real key. Four surfaces:

1. ``system_settings`` flat secret keys (``SECRET_SETTING_KEYS``)
2. ``system_settings['platform.ai_providers']`` per-provider api_key/app_id
3. ``mediahub_models.api_key``
4. ``user_mcp_servers.bearer_token`` (raw Fernet, no ``enc:v1:`` marker —
   that repo's own scheme; healed dev-keyed → real-keyed, and legacy
   plaintext → encrypted)
5. ``user_settings.settings_json['ai_settings']['ai_providers']`` per-provider
   ``api_key`` (secret-at-rest Phase 2, BYOK) — ``api_key`` may be a plain
   ``str`` OR a ``list[str]`` (Sprint 2 multi-key rotation); each element is
   healed independently. BYOK ciphertext is OWNER-BOUND (payload
   ``byok\\x00{user_id}\\x00{plaintext}``) — plaintext heals to the bound
   format; ciphertext bound to a DIFFERENT user or unbound (a possible
   replay) is NEVER rebound, only ERROR-logged. Written back with a TARGETED
   ``jsonb_set`` on the ``{ai_settings,ai_providers}`` path only — never a
   whole-column replace — so sibling ``settings_json`` keys (``parse_mode``,
   General settings, other ``ai_settings`` fields) are untouched (the #485
   clobber rule).

Idempotent: values already decryptable under the real key (marker check +
strict decrypt probe) are skipped, so a second pass rewrites 0 rows. Emits
ONE summary log line. Never raises — this is a background healer; failures
are logged per-item and surface in the summary's ``errors`` count.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet
from loguru import logger

from app.core import secret_box
from app.core.secure_settings import (
    JSONB_SECRET_KEYS,
    MARKER,
    SECRET_SETTING_KEYS,
    encrypt_byok,
    encrypt_marked,
    parse_byok_frame,
)

PLATFORM_PROVIDERS_KEY = "platform.ai_providers"


def _dev_decrypt(ciphertext: str) -> Optional[str]:
    """Decrypt with the PUBLIC dev key only. Returns None when the token was
    not produced under the dev key."""
    try:
        return (
            Fernet(secret_box.DEV_TOKEN_ENCRYPTION_KEY.encode())
            .decrypt(ciphertext.encode())
            .decode()
        )
    except Exception:  # noqa: BLE001 — probe (InvalidToken etc.), never raise
        return None


def _real_decrypt(ciphertext: str) -> Optional[str]:
    """Decrypt with the REAL env key(s) only (primary + _OLD, no dev
    fallback). Returns None when undecryptable under the real keys."""
    try:
        return secret_box.decrypt(ciphertext, allow_dev_fallback=False)
    except Exception:  # noqa: BLE001 — probe, never raise
        return None


def _heal_marked_value(value: Any) -> Optional[str]:
    """Compute the healed replacement for one system_settings/mediahub_models
    secret value (``enc:v1:`` marker scheme), or None when no rewrite is
    needed (already real-keyed / blank / non-string).

    - unmarked non-blank str (plaintext) → encrypt under the real key
    - marked + real-key decryptable → None (idempotent skip)
    - marked + dev-key decryptable   → re-encrypt under the real key
    - marked + neither key           → None + ERROR log (unhealable)
    """
    if not isinstance(value, str) or not value.strip():
        return None
    if not value.startswith(MARKER):
        return encrypt_marked(value)
    ciphertext = value[len(MARKER) :]
    if _real_decrypt(ciphertext) is not None:
        return None  # already healthy
    plaintext = _dev_decrypt(ciphertext)
    if plaintext is None:
        logger.error(
            "[secrets-selfheal] marked value undecryptable under real AND dev "
            "keys — cannot heal (was it encrypted under a rotated-away key?)"
        )
        return None
    return encrypt_marked(plaintext)


def _heal_bearer_token(value: Any) -> Optional[str]:
    """Healed replacement for one ``user_mcp_servers.bearer_token`` (raw
    Fernet scheme, 'gAAAAA' prefix = ciphertext), or None when no rewrite
    is needed.

    - 'gAAAAA' + real-key decryptable → None (skip)
    - 'gAAAAA' + dev-key decryptable  → re-encrypt under the real key
    - 'gAAAAA' + neither              → None + ERROR log
    - anything else (legacy plaintext) → encrypt under the real key
    """
    if not isinstance(value, str) or not value.strip():
        return None
    if not value.startswith("gAAAAA"):
        return secret_box.encrypt(value, allow_dev_fallback=False)
    if _real_decrypt(value) is not None:
        return None
    plaintext = _dev_decrypt(value)
    if plaintext is None:
        logger.error(
            "[secrets-selfheal] bearer_token undecryptable under real AND dev "
            "keys — cannot heal"
        )
        return None
    return secret_box.encrypt(plaintext, allow_dev_fallback=False)


async def _heal_system_settings_flat() -> int:
    """Rewrite plaintext/dev-keyed values of the flat secret keys."""
    from app.db import engine as db_engine

    rewritten = 0
    placeholders = ",".join(f":k{i}" for i in range(len(SECRET_SETTING_KEYS)))
    params = {f"k{i}": k for i, k in enumerate(sorted(SECRET_SETTING_KEYS))}
    rows = await db_engine.fetch_all(
        f"SELECT key, value FROM public.system_settings "
        f"WHERE key IN ({placeholders})",
        params,
    )
    for row in rows:
        healed = _heal_marked_value(row["value"])
        if healed is None:
            continue
        await db_engine.execute(
            "UPDATE public.system_settings SET value = CAST(:v AS jsonb) "
            "WHERE key = :k",
            {"v": json.dumps(healed), "k": row["key"]},
        )
        rewritten += 1
    return rewritten


async def _heal_platform_providers() -> int:
    """Rewrite plaintext/dev-keyed api_key/app_id fields inside
    platform.ai_providers. Counts rewritten FIELDS."""
    from app.db import engine as db_engine

    raw = await db_engine.fetch_val(
        "SELECT value FROM public.system_settings WHERE key = :k",
        {"k": PLATFORM_PROVIDERS_KEY},
    )
    if not isinstance(raw, dict):
        return 0
    secret_fields = JSONB_SECRET_KEYS[PLATFORM_PROVIDERS_KEY]
    rewritten = 0
    merged: Dict[str, Any] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            merged[name] = entry
            continue
        new_entry = dict(entry)
        for field_name in secret_fields:
            healed = _heal_marked_value(new_entry.get(field_name))
            if healed is not None:
                new_entry[field_name] = healed
                rewritten += 1
        merged[name] = new_entry
    if rewritten:
        await db_engine.execute(
            "UPDATE public.system_settings SET value = CAST(:v AS jsonb) "
            "WHERE key = :k",
            {"v": json.dumps(merged), "k": PLATFORM_PROVIDERS_KEY},
        )
    return rewritten


async def _heal_mediahub_models() -> int:
    from app.db import engine as db_engine

    rows = await db_engine.fetch_all(
        "SELECT id, api_key FROM public.mediahub_models "
        "WHERE api_key IS NOT NULL AND api_key <> ''"
    )
    rewritten = 0
    for row in rows:
        healed = _heal_marked_value(row["api_key"])
        if healed is None:
            continue
        await db_engine.execute(
            "UPDATE public.mediahub_models SET api_key = :v WHERE id = :id",
            {"v": healed, "id": row["id"]},
        )
        rewritten += 1
    return rewritten


def _heal_byok_scalar(value: Any, user_id: str) -> Optional[str]:
    """Healed replacement for one BYOK ``api_key`` scalar, or ``None`` when
    no rewrite is needed. BYOK ciphertext is OWNER-BOUND (payload
    ``byok\\x00{user_id}\\x00{plaintext}`` — see ``secure_settings``), so the
    heal must both produce the bound format and verify existing bindings:

    - unmarked non-blank str (plaintext) → encrypt BOUND to ``user_id``
    - marked + real-key decryptable + bound to THIS user → None (healthy)
    - marked + real-key decryptable + unbound or bound to ANOTHER user →
      None + ERROR log — a possible cross-surface/cross-user ciphertext
      replay. NEVER rebound to this user (that would launder a stolen
      ciphertext into a working key); left in place, where the reveal path
      already resolves it to ``""``.
    - marked + dev-key decryptable + bound to THIS user → re-encrypt bound
      under the real key
    - marked + neither key → None + ERROR (unhealable)
    """
    if not isinstance(value, str) or not value.strip():
        return None
    if not value.startswith(MARKER):
        return encrypt_byok(value, user_id)
    ciphertext = value[len(MARKER) :]

    payload = _real_decrypt(ciphertext)
    dev_keyed = False
    if payload is None:
        payload = _dev_decrypt(ciphertext)
        dev_keyed = payload is not None
    if payload is None:
        logger.error(
            "[secrets-selfheal] BYOK api_key undecryptable under real AND dev "
            "keys — cannot heal (was it encrypted under a rotated-away key?)"
        )
        return None

    parsed = parse_byok_frame(payload)
    if parsed is None or parsed[0] != str(user_id):
        logger.error(
            "[secrets-selfheal] BYOK api_key ownership mismatch (unbound or "
            "bound to a different user) — possible ciphertext replay; left "
            "untouched, NOT rebound"
        )
        return None
    if not dev_keyed:
        return None  # real-keyed + correctly bound — healthy
    return encrypt_byok(parsed[1], user_id)


def _heal_byok_field(value: Any, user_id: str) -> Optional[Any]:
    """Healed replacement for one BYOK ``api_key`` field (``str`` or
    ``list[str]`` — Sprint 2 multi-key rotation), or ``None`` when nothing in
    it needs rewriting.

    List elements are healed independently via ``_heal_byok_scalar``; a list
    with a mix of already-healthy and needs-healing elements returns a NEW
    list with only the needing elements replaced (the rest carried over
    byte-for-byte)."""
    if isinstance(value, list):
        healed_elems = [
            _heal_byok_scalar(v, user_id) if isinstance(v, str) else None for v in value
        ]
        if not any(h is not None for h in healed_elems):
            return None
        return [
            healed if healed is not None else original
            for healed, original in zip(healed_elems, value)
        ]
    return _heal_byok_scalar(value, user_id)


async def _heal_user_settings_ai_providers() -> int:
    """Rewrite plaintext/dev-keyed BYOK ``api_key`` fields inside
    ``user_settings.settings_json.ai_settings.ai_providers``, one row at a
    time. Counts rewritten FIELDS (matching ``_heal_platform_providers``).

    Uses a TARGETED ``jsonb_set`` on the ``{ai_settings,ai_providers}`` path
    — never a whole-``settings_json`` replace — so every other top-level key
    (``parse_mode``, General settings) and every other ``ai_settings`` field
    (``whisper_provider``, ``task_assignment``, ...) survives untouched (the
    #485 clobber rule)."""
    from app.db import engine as db_engine

    rows = await db_engine.fetch_all(
        "SELECT user_id, settings_json -> 'ai_settings' -> 'ai_providers' "
        "AS ai_providers FROM public.user_settings "
        "WHERE settings_json -> 'ai_settings' -> 'ai_providers' IS NOT NULL"
    )
    total_rewritten = 0
    for row in rows:
        raw = row["ai_providers"]
        if not isinstance(raw, dict):
            continue
        merged: Dict[str, Any] = {}
        row_rewritten = 0
        for name, entry in raw.items():
            if not isinstance(entry, dict):
                merged[name] = entry
                continue
            new_entry = dict(entry)
            if "api_key" in new_entry:
                healed = _heal_byok_field(new_entry["api_key"], str(row["user_id"]))
                if healed is not None:
                    new_entry["api_key"] = healed
                    row_rewritten += 1
            merged[name] = new_entry
        if row_rewritten:
            await db_engine.execute(
                "UPDATE public.user_settings SET settings_json = jsonb_set("
                "settings_json, '{ai_settings,ai_providers}', CAST(:v AS jsonb)"
                ") WHERE user_id = :uid",
                {"v": json.dumps(merged), "uid": row["user_id"]},
            )
            total_rewritten += row_rewritten
    return total_rewritten


async def _heal_user_mcp_servers() -> int:
    from app.db import engine as db_engine

    rows = await db_engine.fetch_all(
        "SELECT id, bearer_token FROM public.user_mcp_servers "
        "WHERE bearer_token IS NOT NULL AND bearer_token <> ''"
    )
    rewritten = 0
    for row in rows:
        healed = _heal_bearer_token(row["bearer_token"])
        if healed is None:
            continue
        await db_engine.execute(
            "UPDATE public.user_mcp_servers SET bearer_token = :v WHERE id = :id",
            {"v": healed, "id": row["id"]},
        )
        rewritten += 1
    return rewritten


async def run_secrets_selfheal() -> Dict[str, Any]:
    """Run the full sweep. Returns a summary dict (also logged as ONE line).

    No-ops (with a WARNING) when the real encryption key is not configured —
    healing under the public dev key would be worse than useless — or when
    the DB engine is not configured (DB-less local/CI boot).
    """
    if not secret_box.is_configured():
        logger.warning(
            "[secrets-selfheal] skipped — MEDIAHUB_TOKEN_ENCRYPTION_KEY not "
            "set; secrets remain unprotected"
        )
        return {"ok": False, "reason": "encryption_key_not_configured"}

    from app.db import engine as db_engine

    if not db_engine.is_configured():
        logger.warning("[secrets-selfheal] skipped — DB engine not configured")
        return {"ok": False, "reason": "db_not_configured"}

    summary: Dict[str, Any] = {"ok": True, "errors": 0}
    for label, fn in (
        ("system_settings", _heal_system_settings_flat),
        ("platform_ai_providers", _heal_platform_providers),
        ("mediahub_models", _heal_mediahub_models),
        ("user_mcp_servers", _heal_user_mcp_servers),
        ("user_settings_ai_providers", _heal_user_settings_ai_providers),
    ):
        try:
            summary[label] = await fn()
        except Exception as exc:  # noqa: BLE001 — healer must never raise
            logger.error(f"[secrets-selfheal] {label} sweep failed: {exc!r}")
            summary[label] = 0
            summary["errors"] += 1
    logger.info(
        "[secrets-selfheal] done — system_settings={} platform_ai_providers={} "
        "mediahub_models={} user_mcp_servers={} user_settings_ai_providers={} "
        "errors={}",
        summary["system_settings"],
        summary["platform_ai_providers"],
        summary["mediahub_models"],
        summary["user_mcp_servers"],
        summary["user_settings_ai_providers"],
        summary["errors"],
    )
    return summary


__all__ = ["run_secrets_selfheal"]
