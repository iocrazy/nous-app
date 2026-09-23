"""P7 — Fernet-based at-rest encryption for stored secrets."""

from __future__ import annotations

import pytest

from app.core import secret_box


@pytest.mark.unit
def test_encrypt_decrypt_roundtrip():
    ct = secret_box.encrypt("sk-mysecret-12345")
    assert ct is not None
    assert ct != "sk-mysecret-12345"  # actually encrypted
    assert ct.startswith("gAAAAA")  # Fernet token prefix
    assert secret_box.decrypt(ct) == "sk-mysecret-12345"


@pytest.mark.unit
def test_encrypt_none_passes_through():
    assert secret_box.encrypt(None) is None
    assert secret_box.decrypt(None) is None


@pytest.mark.unit
def test_decrypt_legacy_plaintext_passes_through():
    """Backward-compat — rows that were stored before P7 mig still
    return as-is so we don't lose access during the encryption rollout."""
    legacy = "raw-plaintext-no-fernet-prefix"
    assert secret_box.decrypt(legacy) == legacy


@pytest.mark.unit
def test_encrypt_each_call_produces_different_ciphertext():
    """Fernet uses a random IV — ciphertexts of the same plaintext differ."""
    ct1 = secret_box.encrypt("samevalue")
    ct2 = secret_box.encrypt("samevalue")
    assert ct1 != ct2
    assert secret_box.decrypt(ct1) == "samevalue"
    assert secret_box.decrypt(ct2) == "samevalue"


@pytest.mark.unit
def test_encrypt_raises_when_no_key_and_no_fallback(monkeypatch):
    """If neither env nor dev fallback yields a key, encrypt raises."""
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD", raising=False)
    # Patch _resolve_keys to return [] (simulates no fallback either)
    monkeypatch.setattr(
        secret_box, "_resolve_keys", lambda *, allow_dev_fallback=True: []
    )
    with pytest.raises(secret_box.SecretBoxNotConfigured):
        secret_box.encrypt("anything")


@pytest.mark.unit
def test_decrypt_with_wrong_key_raises_value_error(monkeypatch):
    """Tampered/foreign ciphertext → ValueError, not crash."""
    from cryptography.fernet import Fernet

    # Encrypt with one key, attempt to decrypt with another
    other_key = Fernet.generate_key().decode()
    foreign_ct = Fernet(other_key.encode()).encrypt(b"hello").decode()
    # Force the test process to use a different key
    monkeypatch.setenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD", raising=False)
    with pytest.raises(ValueError, match="decrypt failed"):
        secret_box.decrypt(foreign_ct)


@pytest.mark.unit
def test_key_rotation_via_old_key_env(monkeypatch):
    """OLD env var enables transparent rotation: decrypt tries new
    key first, then OLD."""
    from cryptography.fernet import Fernet

    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()

    # Encrypt with old key (simulates pre-rotation row)
    old_ct = Fernet(old_key.encode()).encrypt(b"sekret123").decode()

    # Set up env for post-rotation app
    monkeypatch.setenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", new_key)
    monkeypatch.setenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD", old_key)

    # Should still decrypt the old ciphertext via fallback
    assert secret_box.decrypt(old_ct) == "sekret123"

    # And new encrypt uses new key (verify by removing old + decrypting)
    new_ct = secret_box.encrypt("fresh")
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD")
    assert secret_box.decrypt(new_ct) == "fresh"


@pytest.mark.unit
def test_is_configured_reports_real_env_only(monkeypatch):
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", raising=False)
    assert secret_box.is_configured() is False
    monkeypatch.setenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", "anything-truthy")
    assert secret_box.is_configured() is True


# ── NOUS_* / MEDIAHUB_* alias (rename without touching prod secrets) ──


def _clear_key_env(monkeypatch):
    for name in (
        "NOUS_TOKEN_ENCRYPTION_KEY",
        "NOUS_TOKEN_ENCRYPTION_KEY_OLD",
        "MEDIAHUB_TOKEN_ENCRYPTION_KEY",
        "MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.unit
def test_alias_neither_set_uses_dev_fallback_only(monkeypatch):
    _clear_key_env(monkeypatch)
    assert secret_box.is_configured() is False
    assert secret_box._resolve_keys(allow_dev_fallback=False) == []
    assert secret_box._resolve_keys() == [secret_box.DEV_TOKEN_ENCRYPTION_KEY.encode()]


@pytest.mark.unit
def test_alias_legacy_name_only(monkeypatch):
    from cryptography.fernet import Fernet

    _clear_key_env(monkeypatch)
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", key)
    assert secret_box.is_configured() is True
    ct = Fernet(key.encode()).encrypt(b"legacy").decode()
    assert secret_box.decrypt(ct, allow_dev_fallback=False) == "legacy"


@pytest.mark.unit
def test_alias_new_name_only(monkeypatch):
    from cryptography.fernet import Fernet

    _clear_key_env(monkeypatch)
    key = Fernet.generate_key().decode()
    old = Fernet.generate_key().decode()
    monkeypatch.setenv("NOUS_TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("NOUS_TOKEN_ENCRYPTION_KEY_OLD", old)
    assert secret_box.is_configured() is True
    ct = Fernet(key.encode()).encrypt(b"new").decode()
    old_ct = Fernet(old.encode()).encrypt(b"rotated").decode()
    assert secret_box.decrypt(ct, allow_dev_fallback=False) == "new"
    assert secret_box.decrypt(old_ct, allow_dev_fallback=False) == "rotated"


@pytest.mark.unit
def test_alias_both_differ_new_wins_and_warns(monkeypatch, caplog):
    import logging

    from cryptography.fernet import Fernet

    from app.core import env_names

    env_names._reset_warned_for_tests()
    _clear_key_env(monkeypatch)
    new = Fernet.generate_key().decode()
    legacy = Fernet.generate_key().decode()
    monkeypatch.setenv("NOUS_TOKEN_ENCRYPTION_KEY", new)
    monkeypatch.setenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", legacy)
    try:
        with caplog.at_level(logging.WARNING, logger="app.core.env_names"):
            keys = secret_box._resolve_keys(allow_dev_fallback=False)
        assert keys == [new.encode()]
        assert any(
            "NOUS_TOKEN_ENCRYPTION_KEY" in r.getMessage()
            and "MEDIAHUB_TOKEN_ENCRYPTION_KEY" in r.getMessage()
            for r in caplog.records
            if r.levelno == logging.WARNING
        )
        # A fresh encrypt lands under the NOUS key, not the legacy one.
        ct = secret_box.encrypt("x", allow_dev_fallback=False)
        assert Fernet(new.encode()).decrypt(ct.encode()) == b"x"
    finally:
        env_names._reset_warned_for_tests()
