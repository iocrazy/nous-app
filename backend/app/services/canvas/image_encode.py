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

#: Modes each format we write actually accepts, measured against the pinned
#: Pillow rather than recalled (``Image.new(mode).save(format)`` for every
#: mode). Pillow's per-plugin mode tables are private, so this is the only
#: honest way to state it — and a format we do not write is not listed, which
#: leaves its image untouched rather than guessed at.
_SAVEABLE_MODES: dict[str, frozenset[str]] = {
    "PNG": frozenset({"1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16"}),
    "JPEG": frozenset({"1", "L", "RGB", "CMYK", "YCbCr"}),
    # WEBP and GIF convert internally for every mode we can arrive with, so
    # they are absent on purpose: listing a partial set would convert images
    # Pillow handles better itself.
}

#: Formats that can carry an alpha channel; for the rest, transparency is lost
#: on encode no matter what we convert to, so RGB is the honest target.
_ALPHA_FORMATS: frozenset[str] = frozenset({"PNG", "WEBP", "GIF"})


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in ("RGBA", "LA", "PA", "La") or "transparency" in image.info


def to_encodable(image: Image.Image, out_format: str) -> Image.Image:
    """``image`` as-is when ``out_format`` can write its mode, else converted.

    Returns the SAME object when nothing is needed — callers may rely on that
    to avoid a copy, and it is what keeps existing RGB/RGBA/P/L behaviour
    byte-identical.
    """
    saveable = _SAVEABLE_MODES.get(out_format.upper())
    if saveable is None or image.mode in saveable:
        return image
    target = (
        "RGBA" if _has_alpha(image) and out_format.upper() in _ALPHA_FORMATS else "RGB"
    )
    return image.convert(target)
