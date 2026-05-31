"""Soda (汽水音乐 / Luna) audio decryption.

Soda music streams download as MP4 CENC (`cenc`) containers, AES-128-CTR
encrypted per-sample (optionally with clear/encrypted subsample ranges). The
decryption key is carried, obfuscated, inside the per-quality ``PlayAuth``
string (the "Spade" scheme).

This module is a clean Python port of the reference algorithm in
``music-lib/soda/crypto.go`` (the working Go blueprint), which is more complete
than the musicdl Python port — it handles subsample ranges and reads the IV
size from the ``tenc`` box. Two public entry points:

    extract_spade_key(play_auth) -> hex_key_str
    decrypt_audio(file_data, play_auth) -> decrypted_bytes

Pure algorithm, no I/O and no network — the unit tests synthesise inputs.
See ``docs/soda-music-integration.md`` §A.5.
"""

from __future__ import annotations

import base64
import struct
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

AES_BLOCK_SIZE = 16
DEFAULT_IV_SIZE = 8

# Container boxes whose children we may need to descend into when searching
# deeply (e.g. for the ``tenc`` box that declares the per-sample IV size).
_CONTAINER_CHILD_OFFSET = {
    b"moov": 8,
    b"trak": 8,
    b"mdia": 8,
    b"minf": 8,
    b"stbl": 8,
    b"sinf": 8,
    b"schi": 8,
    b"stsd": 16,  # 8 header + 8 (version/flags + entry_count)
    b"enca": 36,  # 8 header + 28 audio sample entry
    b"mp4a": 36,
    b"alac": 36,
    b"fLaC": 36,
}


class SodaDecryptError(Exception):
    """Raised when a PlayAuth or MP4 payload cannot be decrypted."""


@dataclass(frozen=True)
class _Box:
    offset: int  # absolute offset of the box header within the buffer
    size: int  # total box size including the 8-byte header
    data_start: int  # absolute offset where the payload begins (offset + 8)


# ---------------------------------------------------------------------------
# Spade key extraction (PlayAuth -> AES-128 hex key)
# ---------------------------------------------------------------------------


def _bitcount(n: int) -> int:
    return bin(n & 0xFFFFFFFF).count("1")


def _decode_base36(c: int) -> int:
    if 48 <= c <= 57:  # '0'-'9'
        return c - 48
    if 97 <= c <= 122:  # 'a'-'z'
        return c - 97 + 10
    return 0xFF


def _decrypt_spade_inner(inner: bytes) -> bytes:
    """Reverse the Spade byte obfuscation over ``inner``.

    ``buff`` is ``[0xFA, 0x55] + inner`` so each output byte depends on the
    input byte two positions back. Negative intermediate values wrap by +255
    (matching the reference's ``while v < 0: v += 255``).
    """
    buff = bytes([0xFA, 0x55]) + inner
    out = bytearray(len(inner))
    for i in range(len(inner)):
        v = (inner[i] ^ buff[i]) - _bitcount(i) - 21
        while v < 0:
            v += 255
        out[i] = v
    return bytes(out)


def extract_spade_key(play_auth: str) -> str:
    """Decode the AES-128 key (hex string) carried inside a PlayAuth token.

    Raises:
        SodaDecryptError: if the token is malformed or too short.
    """
    try:
        data = base64.b64decode(play_auth)
    except (
        Exception
    ) as exc:  # noqa: BLE001 — surface a domain error, not base64 internals
        raise SodaDecryptError(f"PlayAuth is not valid base64: {exc}") from exc

    if len(data) < 3:
        raise SodaDecryptError("PlayAuth too short (need ≥3 bytes)")

    padding_len = (data[0] ^ data[1] ^ data[2]) - 48
    if padding_len < 0 or len(data) < padding_len + 2:
        raise SodaDecryptError("PlayAuth has invalid padding length")

    inner = data[1 : len(data) - padding_len]
    tmp = _decrypt_spade_inner(inner)
    if not tmp:
        raise SodaDecryptError("PlayAuth inner decode produced no bytes")

    skip = _decode_base36(tmp[0])
    end_index = 1 + (len(data) - padding_len - 2) - skip
    if end_index < 1 or end_index > len(tmp):
        raise SodaDecryptError("PlayAuth key slice out of bounds")

    try:
        return tmp[1:end_index].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SodaDecryptError(f"PlayAuth key is not valid UTF-8 hex: {exc}") from exc


# ---------------------------------------------------------------------------
# MP4 box walking
# ---------------------------------------------------------------------------


def _read_u32(data: bytes, offset: int) -> int:
    return struct.unpack(">I", data[offset : offset + 4])[0]


def _find_box(data: bytes, box_type: bytes, start: int, end: int) -> _Box | None:
    """Flat sibling scan for a box of ``box_type`` within ``[start, end)``."""
    end = min(end, len(data))
    pos = start
    while pos + 8 <= end:
        size = _read_u32(data, pos)
        if size < 8:
            break
        if data[pos + 4 : pos + 8] == box_type:
            return _Box(offset=pos, size=size, data_start=pos + 8)
        pos += size
    return None


def _find_box_deep(data: bytes, box_type: bytes, start: int, end: int) -> _Box | None:
    """Recursive search that descends into known container boxes."""
    end = min(end, len(data))
    pos = start
    while pos + 8 <= end:
        size = _read_u32(data, pos)
        header = 8
        if size == 1:  # 64-bit largesize
            if pos + 16 > end:
                break
            size = struct.unpack(">Q", data[pos + 8 : pos + 16])[0]
            header = 16
        if size < header or pos + size > end:
            break
        current = bytes(data[pos + 4 : pos + 8])
        if current == box_type:
            return _Box(offset=pos, size=size, data_start=pos + header)
        child_off = _CONTAINER_CHILD_OFFSET.get(current)
        if child_off is not None and pos + child_off < pos + size:
            found = _find_box_deep(data, box_type, pos + child_off, pos + size)
            if found is not None:
                return found
        pos += size
    return None


def _locate_stbl(data: bytes, moov: _Box) -> _Box:
    trak = _find_box(data, b"trak", moov.data_start, moov.offset + moov.size)
    if trak is None:
        raise SodaDecryptError("trak box not found")
    mdia = _find_box(data, b"mdia", trak.data_start, trak.offset + trak.size)
    if mdia is None:
        raise SodaDecryptError("mdia box not found")
    minf = _find_box(data, b"minf", mdia.data_start, mdia.offset + mdia.size)
    if minf is None:
        raise SodaDecryptError("minf box not found")
    stbl = _find_box(data, b"stbl", minf.data_start, minf.offset + minf.size)
    if stbl is None:
        raise SodaDecryptError("stbl box not found")
    return stbl


def _parse_stsz(data: bytes, stsz: _Box) -> list[int]:
    body = data[stsz.data_start : stsz.offset + stsz.size]
    if len(body) < 12:
        raise SodaDecryptError("stsz box too short")
    fixed = _read_u32(body, 4)
    count = _read_u32(body, 8)
    if fixed != 0:
        return [fixed] * count
    sizes = []
    for i in range(count):
        off = 12 + i * 4
        if off + 4 > len(body):
            break
        sizes.append(_read_u32(body, off))
    return sizes


@dataclass(frozen=True)
class _SencSample:
    iv: bytes
    subsamples: tuple[tuple[int, int], ...]  # (clear, encrypted) pairs


def _default_iv_size(data: bytes, stbl: _Box) -> int:
    tenc = _find_box_deep(data, b"tenc", stbl.data_start, stbl.offset + stbl.size)
    if tenc is None:
        return DEFAULT_IV_SIZE
    body = data[tenc.data_start : tenc.offset + tenc.size]
    if len(body) < 8:
        return DEFAULT_IV_SIZE
    iv_size = body[7]
    return iv_size if iv_size in (8, 16) else DEFAULT_IV_SIZE


def _parse_senc(data: bytes, senc: _Box, iv_size: int) -> list[_SencSample]:
    body = data[senc.data_start : senc.offset + senc.size]
    if len(body) < 8:
        return []
    if iv_size not in (8, 16):
        iv_size = DEFAULT_IV_SIZE
    flags = _read_u32(body, 0) & 0x00FFFFFF
    count = _read_u32(body, 4)
    has_subsamples = bool(flags & 0x02)
    samples: list[_SencSample] = []
    ptr = 8
    for _ in range(count):
        if ptr + iv_size > len(body):
            break
        iv = body[ptr : ptr + iv_size]
        ptr += iv_size
        subs: list[tuple[int, int]] = []
        if has_subsamples:
            if ptr + 2 > len(body):
                break
            sub_count = struct.unpack(">H", body[ptr : ptr + 2])[0]
            ptr += 2
            if ptr + sub_count * 6 > len(body):
                break
            for _j in range(sub_count):
                clear = struct.unpack(">H", body[ptr : ptr + 2])[0]
                encrypted = _read_u32(body, ptr + 2)
                subs.append((clear, encrypted))
                ptr += 6
        samples.append(_SencSample(iv=iv, subsamples=tuple(subs)))
    return samples


# ---------------------------------------------------------------------------
# Per-sample AES-CTR
# ---------------------------------------------------------------------------


def _decrypt_sample(key: bytes, chunk: bytes, sample: _SencSample) -> bytes:
    iv = sample.iv
    if len(iv) < AES_BLOCK_SIZE:
        iv = iv + b"\x00" * (AES_BLOCK_SIZE - len(iv))
    decryptor = Cipher(algorithms.AES(key), modes.CTR(iv)).decryptor()

    if not sample.subsamples:
        return decryptor.update(chunk) + decryptor.finalize()

    out = bytearray()
    pos = 0
    for clear, encrypted in sample.subsamples:
        clear = min(clear, len(chunk) - pos)
        out += chunk[pos : pos + clear]
        pos += clear
        if pos >= len(chunk):
            break
        encrypted = min(encrypted, len(chunk) - pos)
        out += decryptor.update(chunk[pos : pos + encrypted])
        pos += encrypted
        if pos >= len(chunk):
            break
    if pos < len(chunk):
        out += chunk[pos:]
    return bytes(out)


def _restore_original_format(stsd_bytes: bytearray) -> None:
    """Replace the 'enca' sample-entry tag with the frma-indicated codec.

    Players reject 'enca' (encrypted) entries; CENC stores the real codec in a
    sibling 'frma' box. Falls back to 'mp4a' when frma is absent/unreadable.
    Mutates ``stsd_bytes`` in place.
    """
    enca = stsd_bytes.find(b"enca")
    if enca == -1:
        return
    fmt = b"mp4a"
    frma = stsd_bytes.find(b"frma")
    if frma >= 4 and frma + 8 <= len(stsd_bytes):
        size = struct.unpack(">I", stsd_bytes[frma - 4 : frma])[0]
        if size >= 12 and frma - 4 + size <= len(stsd_bytes):
            fmt = bytes(stsd_bytes[frma + 4 : frma + 8])
    stsd_bytes[enca : enca + 4] = fmt


def decrypt_audio(file_data: bytes, play_auth: str) -> bytes:
    """Decrypt a Soda CENC MP4 payload and return a playable copy.

    Does not mutate ``file_data``. Restores the 'enca' sample entry to its
    real codec so the output plays in standard players.

    Raises:
        SodaDecryptError: on a bad PlayAuth, missing MP4 boxes, or a decrypted
            size that does not match the original mdat (never silently wrong).
    """
    key = bytes.fromhex(extract_spade_key(play_auth))

    out = bytearray(file_data)

    moov = _find_box(out, b"moov", 0, len(out))
    if moov is None:
        raise SodaDecryptError("moov box not found")
    stbl = _locate_stbl(out, moov)

    stsz = _find_box(out, b"stsz", stbl.data_start, stbl.offset + stbl.size)
    if stsz is None:
        raise SodaDecryptError("stsz box not found")
    sample_sizes = _parse_stsz(out, stsz)

    senc = _find_box(out, b"senc", moov.data_start, moov.offset + moov.size)
    if senc is None:
        senc = _find_box(out, b"senc", stbl.data_start, stbl.offset + stbl.size)
    if senc is None:
        raise SodaDecryptError("senc box not found")
    senc_samples = _parse_senc(out, senc, _default_iv_size(out, stbl))

    mdat = _find_box(out, b"mdat", 0, len(out))
    if mdat is None:
        raise SodaDecryptError("mdat box not found")

    decrypted = bytearray()
    read_ptr = mdat.data_start
    for i, size in enumerate(sample_sizes):
        if read_ptr + size > len(out):
            break
        chunk = bytes(out[read_ptr : read_ptr + size])
        if i < len(senc_samples):
            decrypted += _decrypt_sample(key, chunk, senc_samples[i])
        else:
            decrypted += chunk
        read_ptr += size

    if len(decrypted) != mdat.size - 8:
        raise SodaDecryptError(
            f"decrypted size mismatch: {len(decrypted)} != {mdat.size - 8}"
        )
    out[mdat.data_start : mdat.offset + mdat.size] = decrypted

    stsd = _find_box(out, b"stsd", stbl.data_start, stbl.offset + stbl.size)
    if stsd is not None:
        stsd_slice = bytearray(out[stsd.offset : stsd.offset + stsd.size])
        _restore_original_format(stsd_slice)
        out[stsd.offset : stsd.offset + stsd.size] = stsd_slice

    return bytes(out)
