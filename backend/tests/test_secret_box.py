"""P7 — Fernet-based at-rest encryption for stored secrets."""
from __future__ import annotations

import pytest

from app.core import secret_box


@pytest.mark.unit
def test_encrypt_decrypt_roundtrip():
    ct = secret_box.encrypt("sk-mysecret-12345")
    assert ct is not None
    assert ct != "sk-mysecret-12345"   # actually encrypted
    assert ct.startswith("gAAAAA")     # Fernet token prefix
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
    monkeypatch.setattr(secret_box, "_resolve_keys",
                        lambda *, allow_dev_fallback=True: [])
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
    monkeypatch.setenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY",
                       Fernet.generate_key().decode())
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
