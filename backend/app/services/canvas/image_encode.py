"""Make an image encodable in the format a derive transform writes.

``canvas_material_source.transform_mime`` hands every decodable-but-unwritable
source format (TIFF, BMP, …) to the transforms as ``image/png``, so a CMYK
scan or a LAB export arrives with a mode Pillow refuses to write there
(``OSError: cannot write mode CMYK as PNG``) — which the derive endpoints
surfaced as ``400 crop failed: cannot write mode CMYK as PNG``.

Converting BEFORE the encode turns that into a product. The mode is left
untouched whenever the target format accepts it, so an RGBA PNG keeps its
alpha and a palette GIF keeps its palette: this only rescues encodes that
would otherwise raise.
"""

from __future__ import annotations

from PIL import Image

#: Modes each format we write actually accepts, MEASURED against the pinned
#: Pillow rather than recalled: ``Image.new(mode).save(BytesIO(), format=fmt)``
#: for every mode, keeping the ones that do not raise. Pillow's per-plugin mode
#: tables are private, so measuring is the only honest way to state this.
#:
#: All four formats the transforms write are listed. WEBP swallows nearly
#: everything (it converts internally); GIF does NOT — it raises for CMYK /
#: YCbCr / LAB / HSV / PA / La — and an earlier version of this comment claimed
#: otherwise. Those GIF cases are unreachable today (a source sniffing as GIF
#: decodes to P/L/RGB), but a table asserting a false fact is worse than one
#: that is merely incomplete.
_SAVEABLE_MODES: dict[str, frozenset[str]] = {
    # "I" is deliberately ABSENT even though Pillow 12.1.1 still writes it:
    # it emits a DeprecationWarning saying mode-I PNG is removed in Pillow 13
    # (2026-10-15). Converting to RGB now keeps those images working across
    # that bump instead of turning the deprecation into a 400 on upgrade day.
    "PNG": frozenset({"1", "L", "LA", "P", "RGB", "RGBA", "I;16"}),
    "JPEG": frozenset({"1", "L", "RGB", "CMYK", "YCbCr"}),
    "WEBP": frozenset(
        {
            "1",
            "L",
            "LA",
            "P",
            "PA",
            "RGB",
            "RGBa",
            "RGBA",
            "CMYK",
            "YCbCr",
            "LAB",
            "HSV",
            "I",
            "F",
            "I;16",
        }
    ),
    "GIF": frozenset({"1", "L", "LA", "P", "RGB", "RGBA", "I", "F", "I;16"}),
}

#: Formats that can carry an alpha channel; for the rest, transparency is lost
#: on encode no matter what we convert to, so RGB is the honest target.
_ALPHA_FORMATS: frozenset[str] = frozenset({"PNG", "WEBP", "GIF"})

#: Modes with no direct conversion to RGB/RGBA, and the one Pillow does offer.
#: ``La`` (premultiplied greyscale+alpha) raises ``conversion from La to L not
#: supported`` on a direct ``convert("RGB")`` / ``convert("RGBA")``, so a
#: rescue that went straight for the target would replace one crash with
#: another. Via ``LA`` both directions work.
_VIA_MODE: dict[str, str] = {"La": "LA"}


def _has_alpha(image: Image.Image) -> bool:
    """Does anything get LOST by landing on RGB?

    Covers the premultiplied spellings (``La`` / ``RGBa``) as well as the plain
    ones — they carry exactly the same channel — plus a palette image whose
    transparency lives in ``info`` rather than in the mode.
    """
    return (
        image.mode in ("RGBA", "LA", "PA", "La", "RGBa") or "transparency" in image.info
    )


def to_encodable(image: Image.Image, out_format: str) -> Image.Image:
    """``image`` as-is when ``out_format`` can write its mode, else converted.

    Returns the SAME object when nothing is needed — callers may rely on that
    to avoid a copy, and it is what keeps existing RGB/RGBA/P/L behaviour
    byte-identical.
    """
    fmt = out_format.upper()
    saveable = _SAVEABLE_MODES.get(fmt)
    if saveable is None or image.mode in saveable:
        return image
    keep_alpha = _has_alpha(image) and fmt in _ALPHA_FORMATS
    via = _VIA_MODE.get(image.mode)
    if via is not None:
        image = image.convert(via)
        if image.mode in saveable and keep_alpha:
            # The detour already landed somewhere this format writes, with the
            # alpha intact. Converting further would only lose precision.
            return image
    return image.convert("RGBA" if keep_alpha else "RGB")
