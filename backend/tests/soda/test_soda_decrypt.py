"""Tests for soda_music.soda_decrypt — Spade key extraction + MP4 CENC AES-CTR.

These tests are hermetic: they synthesise a PlayAuth that extracts to a chosen
AES-128 key, build a minimal encrypted MP4 (moov>trak>mdia>minf>stbl>{stsd,stsz,
senc} + mdat) with that key, and assert the decryptor recovers the plaintext.
No network, no real sample files.
"""

from __future__ import annotations

import base64
import struct

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.services.media.parsers.soda_music.soda_decrypt import (
    SodaDecryptError,
    decrypt_audio,
    extract_spade_key,
)

# ---------------------------------------------------------------------------
# Test helpers — Spade encoder (inverse of extract_spade_key) + MP4 builder
# ---------------------------------------------------------------------------


def _bitcount(n: int) -> int:
    return bin(n & 0xFFFFFFFF).count("1")


def make_play_auth(hex_key: str) -> str:
    """Construct a PlayAuth string that extract_spade_key() decodes to hex_key.

    Uses paddingLen=0 and a leading '0' (base36 skip=0) so the recovered slice
    is exactly the hex key. Derived as the forward inverse of decryptSpadeInner.
    """
    # Desired decryptSpadeInner output: result[0]='0' (skip=0), result[1:]=hex_key
    result = bytearray([ord("0")]) + bytearray(hex_key.encode("ascii"))
    n = len(result)
    # Solve inner[] so decryptSpadeInner(inner) == result (non-negative branch):
    #   buff[0]=0xFA, buff[1]=0x55, buff[i]=inner[i-2] for i>=2
    #   result[i] = (inner[i] ^ buff[i]) - bitcount(i) - 21
    inner = bytearray(n)
    for i in range(n):
        buff_i = 0xFA if i == 0 else 0x55 if i == 1 else inner[i - 2]
        val = result[i] + _bitcount(i) + 21
        assert val <= 0xFF, f"encoder overflow at i={i}: {val}"
        inner[i] = val ^ buff_i
    # paddingLen=0 requires (b0 ^ inner[0] ^ inner[1]) == 48
    b0 = 48 ^ inner[0] ^ inner[1]
    return base64.b64encode(bytes([b0]) + bytes(inner)).decode("ascii")


def _box(box_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + box_type + payload


def _ctr(key: bytes, iv16: bytes, data: bytes) -> bytes:
    enc = Cipher(algorithms.AES(key), modes.CTR(iv16)).encryptor()
    return enc.update(data) + enc.finalize()


def _stsz(sample_sizes: list[int]) -> bytes:
    payload = (
        b"\x00\x00\x00\x00"
        + struct.pack(">I", 0)
        + struct.pack(">I", len(sample_sizes))
    )
    for s in sample_sizes:
        payload += struct.pack(">I", s)
    return _box(b"stsz", payload)


def _senc(
    ivs8: list[bytes], subsamples: list[list[tuple[int, int]]] | None = None
) -> bytes:
    flags = 0x02 if subsamples else 0x00
    payload = struct.pack(">I", flags) + struct.pack(">I", len(ivs8))
    for i, iv in enumerate(ivs8):
        payload += iv  # 8-byte IV
        if subsamples:
            subs = subsamples[i]
            payload += struct.pack(">H", len(subs))
            for clear, encrypted in subs:
                payload += struct.pack(">H", clear) + struct.pack(">I", encrypted)
    return _box(b"senc", payload)


def _stsd(original_format: bytes | None = None) -> bytes:
    """stsd carrying an 'enca' marker (+ optional frma box giving the real fmt)."""
    payload = b"\x00" * 8 + b"enca" + b"\x00" * 4
    if original_format is not None:
        payload += struct.pack(">I", 12) + b"frma" + original_format
    return _box(b"stsd", payload)


def _build_mp4(
    key: bytes,
    samples_plain: list[bytes],
    ivs8: list[bytes],
    subsamples: list[list[tuple[int, int]]] | None = None,
    original_format: bytes | None = None,
) -> bytes:
    """Assemble a minimal encrypted MP4 + return (mp4_bytes, encrypted_mdat_payload)."""
    enc_samples: list[bytes] = []
    for i, plain in enumerate(samples_plain):
        iv16 = ivs8[i] + b"\x00" * 8
        if subsamples:
            out = bytearray()
            pos = 0
            enc = Cipher(algorithms.AES(key), modes.CTR(iv16)).encryptor()
            for clear, encrypted in subsamples[i]:
                out += plain[pos : pos + clear]
                pos += clear
                out += enc.update(plain[pos : pos + encrypted])
                pos += encrypted
            out += plain[pos:]
            enc_samples.append(bytes(out))
        else:
            enc_samples.append(_ctr(key, iv16, plain))

    mdat = _box(b"mdat", b"".join(enc_samples))
    stbl = _box(
        b"stbl",
        _stsd(original_format)
        + _stsz([len(s) for s in samples_plain])
        + _senc(ivs8, subsamples),
    )
    minf = _box(b"minf", stbl)
    mdia = _box(b"mdia", minf)
    trak = _box(b"trak", mdia)
    moov = _box(b"moov", trak)
    return moov + mdat


# ---------------------------------------------------------------------------
# Spade key extraction
# ---------------------------------------------------------------------------


def test_extract_spade_key_recovers_known_key():
    hex_key = "0123456789abcdef0123456789abcdef"  # 16 bytes AES-128
    play_auth = make_play_auth(hex_key)
    assert extract_spade_key(play_auth) == hex_key


def test_extract_spade_key_rejects_too_short():
    with pytest.raises(SodaDecryptError):
        extract_spade_key(base64.b64encode(b"\x01").decode("ascii"))


# ---------------------------------------------------------------------------
# MP4 decryption
# ---------------------------------------------------------------------------


def _stsc_one_run(samples_per_chunk: int) -> bytes:
    # one run: from chunk 1, every chunk holds `samples_per_chunk` samples
    payload = (
        b"\x00\x00\x00\x00"
        + struct.pack(">I", 1)  # entry_count
        + struct.pack(">III", 1, samples_per_chunk, 1)
    )
    return _box(b"stsc", payload)


def _stco(offsets: list[int]) -> bytes:
    payload = b"\x00\x00\x00\x00" + struct.pack(">I", len(offsets))
    for o in offsets:
        payload += struct.pack(">I", o)
    return _box(b"stco", payload)


def _build_chunked_mp4(
    key: bytes,
    samples_plain: list[bytes],
    ivs8: list[bytes],
    *,
    data_blob: bytes,
) -> bytes:
    """Two audio chunks (2 samples each) with a foreign ``data_blob`` BETWEEN
    them inside mdat — mimics the real 2-track/chunked layout. Sequential reads
    from mdat start would run into data_blob; only stco/stsc offsets are right.

    mdat is placed FIRST so the absolute stco chunk offsets are computable.
    """
    assert len(samples_plain) == 4
    enc = [_ctr(key, ivs8[i] + b"\x00" * 8, samples_plain[i]) for i in range(4)]
    chunk1 = enc[0] + enc[1]
    chunk2 = enc[2] + enc[3]
    mdat_payload = chunk1 + data_blob + chunk2
    mdat = _box(b"mdat", mdat_payload)
    # mdat at file offset 0 → data_start = 8
    chunk1_off = 8
    chunk2_off = 8 + len(chunk1) + len(data_blob)
    stbl = _box(
        b"stbl",
        _stsd()
        + _stsz([len(s) for s in samples_plain])
        + _senc(ivs8)
        + _stco([chunk1_off, chunk2_off])
        + _stsc_one_run(2),
    )
    moov = _box(b"moov", _box(b"trak", _box(b"mdia", _box(b"minf", stbl))))
    return mdat + moov


def test_decrypt_audio_chunked_multitrack_decrypts_at_real_offsets():
    # The regression for '永不栖落的鸟': chunked audio + a foreign data track in
    # mdat. The decrypt must use stco/stsc offsets (not sequential), recover all
    # samples, AND leave the data blob byte-for-byte intact.
    hex_key = "00112233445566778899aabbccddeeff"
    key = bytes.fromhex(hex_key)
    play_auth = make_play_auth(hex_key)
    samples = [b"A" * 16, b"B" * 16, b"C" * 16, b"D" * 16]
    ivs = [bytes([i]) + b"\x00" * 7 for i in range(4)]
    data_blob = b"\xde\xad\xbe\xef" * 5  # the 2nd track's bytes — must survive

    mp4 = _build_chunked_mp4(key, samples, ivs, data_blob=data_blob)
    out = decrypt_audio(mp4, play_auth)

    # every audio sample recovered…
    for s in samples:
        assert s in out
    # …and the foreign data track is untouched (would be corrupted by a
    # sequential-from-mdat decrypt).
    assert data_blob in out
    # size preserved (in-place, no rebuild)
    assert len(out) == len(mp4)


def test_decrypt_audio_chunked_truncated_raises():
    # If a chunk offset + sample size runs past the buffer, fail loudly rather
    # than write a half-decrypted file.
    hex_key = "00112233445566778899aabbccddeeff"
    key = bytes.fromhex(hex_key)
    play_auth = make_play_auth(hex_key)
    samples = [b"A" * 16, b"B" * 16, b"C" * 16, b"D" * 16]
    ivs = [bytes([i]) + b"\x00" * 7 for i in range(4)]
    mp4 = bytearray(_build_chunked_mp4(key, samples, ivs, data_blob=b""))
    # chop the tail so the last chunk's samples no longer fit
    truncated = bytes(mp4[: len(mp4) - 20])
    with pytest.raises(SodaDecryptError):
        decrypt_audio(truncated, play_auth)


def test_decrypt_audio_no_subsamples_recovers_plaintext():
    hex_key = "00112233445566778899aabbccddeeff"
    key = bytes.fromhex(hex_key)
    play_auth = make_play_auth(hex_key)
    samples = [b"A" * 16, b"BCDE" * 8, b"x" * 24]
    ivs = [bytes([i]) + b"\x00" * 7 for i in range(len(samples))]

    mp4 = _build_mp4(key, samples, ivs)
    out = decrypt_audio(mp4, play_auth)

    # mdat payload must equal the concatenated plaintext samples
    assert b"".join(samples) in out


def test_decrypt_audio_with_subsamples_preserves_clear_and_decrypts_encrypted():
    hex_key = "aaaaaaaaaaaaaaaabbbbbbbbbbbbbbbb"
    key = bytes.fromhex(hex_key)
    play_auth = make_play_auth(hex_key)
    plain = b"CLEARpart" + b"SECRETpayload!!"  # 9 clear + 15 encrypted = 24
    samples = [plain]
    ivs = [b"\x07" + b"\x00" * 7]
    subs = [[(9, 15)]]

    mp4 = _build_mp4(key, samples, ivs, subsamples=subs)
    out = decrypt_audio(mp4, play_auth)

    assert plain in out  # both the clear prefix and decrypted secret are present


def test_decrypt_audio_restores_enca_to_frma_format():
    hex_key = "12341234123412341234123412341234"
    key = bytes.fromhex(hex_key)
    play_auth = make_play_auth(hex_key)
    samples = [b"Z" * 16]
    ivs = [b"\x00" * 8]

    mp4 = _build_mp4(key, samples, ivs, original_format=b"fLaC")
    out = decrypt_audio(mp4, play_auth)

    # 'enca' sample entry must be restored to the frma-indicated codec
    assert b"fLaC" in out


def test_decrypt_audio_raises_on_missing_moov():
    play_auth = make_play_auth("00112233445566778899aabbccddeeff")
    with pytest.raises(SodaDecryptError):
        decrypt_audio(b"not an mp4 at all", play_auth)


def test_decrypt_audio_does_not_mutate_input():
    hex_key = "00112233445566778899aabbccddeeff"
    key = bytes.fromhex(hex_key)
    play_auth = make_play_auth(hex_key)
    samples = [b"A" * 16]
    ivs = [b"\x00" * 8]
    mp4 = _build_mp4(key, samples, ivs)
    snapshot = bytes(mp4)

    decrypt_audio(mp4, play_auth)

    assert mp4 == snapshot  # input bytes untouched (immutability)
