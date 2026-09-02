"""The one request shape every generation path reads from.

Three dispatch branches used to hand-copy a dict each; they drifted (the
daemon branch sent `size` while the frontend only ever sends `ratio`, and
`params.actual_model` which nothing sets). Parse `params` ONCE into this
object at the workflow entrance; branches only read it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal, Optional, Protocol

from app.services.generation.aspect import (
    CODEX_DEFAULT_SIZE,
    CODEX_SIZES,
    aspect_instruction,
)

MAX_REFS = 9  # IC caps references at 9

VideoMode = Literal["frames", "multimodal"]


class CapabilitiesLike(Protocol):
    """Structural view of ProviderCapabilities (defined in provider_protocols.base)
    so this module never imports a concrete provider."""

    ratios: frozenset[str]
    quality: bool
    resolution: bool
    max_refs: int
    negative: bool
    video_modes: frozenset[str]


def _clean(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _positive_int(value: Any) -> Optional[int]:
    """A duration only exists if it parses AND is positive.

    Params reach us from JSON the frontend built, so this sees strings,
    numbers, None and the occasional typo. Absent, unparsable and
    non-positive all mean the same thing to every caller — no duration —
    so they collapse to None here rather than raising out of from_params
    and killing a workflow over one bad knob.
    """
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


@dataclass(frozen=True)
class GenerationRequest:
    kind: Literal["image", "video"]
    prompt: str
    model: str
    ratio: Optional[str]
    quality: Optional[str]
    resolution: Optional[str]
    refs: tuple[str, ...]
    negative: Optional[str]
    video_mode: Optional[VideoMode]
    duration: Optional[int]

    @classmethod
    def from_params(
        cls,
        *,
        kind: str,
        prompt: str,
        model: str,
        params: dict[str, Any],
        source_url: Optional[str],
    ) -> "GenerationRequest":
        k: Literal["image", "video"] = "video" if kind == "video" else "image"
        raw_refs = params.get("source_urls")
        refs = [
            u
            for u in (raw_refs if isinstance(raw_refs, list) else [])
            if isinstance(u, str) and u
        ][:MAX_REFS] or ([source_url] if source_url else [])
        mode = _clean(params.get("video_mode"))
        return cls(
            kind=k,
            prompt=prompt,
            model=model,
            # Video knobs arrive as `aspect`; images as `ratio`. One field here.
            ratio=_clean(params.get("aspect") if k == "video" else params.get("ratio")),
            quality=_clean(params.get("quality")),
            resolution=_clean(params.get("resolution")),
            refs=tuple(refs),
            negative=_clean(params.get("negative")),
            video_mode=mode if mode in ("frames", "multimodal") else None,  # type: ignore[arg-type]
            duration=_positive_int(params.get("duration")),
        )

    def reconcile(
        self, caps: CapabilitiesLike
    ) -> tuple["GenerationRequest", list[str]]:
        """Drop what this provider cannot honour and SAY which knobs went.

        The list is in a fixed order so metadata/tests read the same way.
        Never silent: an empty list means everything requested will be sent.
        """
        dropped: list[str] = []
        eff = self
        if eff.ratio and eff.ratio not in caps.ratios:
            eff = replace(eff, ratio=None)
            dropped.append("ratio")
        if eff.quality and not caps.quality:
            eff = replace(eff, quality=None)
            dropped.append("quality")
        if eff.resolution and not caps.resolution:
            eff = replace(eff, resolution=None)
            dropped.append("resolution")
        # Refs are governed by a DIFFERENT field per kind, and conflating the
        # two truncates real work: ``max_refs`` describes the image path (
        # jimeng-local declares 0 because its text2image CLI takes no --image),
        # while VIDEO refs ride on ``video_modes`` as first/last frame or
        # multimodal. Applying the image cap to a frames2video job emptied it.
        # The global 9 ceiling is enforced upstream in ``from_params``.
        if eff.kind == "video":
            if eff.refs and not caps.video_modes:
                eff = replace(eff, refs=())
                dropped.append("refs")
        elif len(eff.refs) > caps.max_refs:
            eff = replace(eff, refs=eff.refs[: caps.max_refs])
            dropped.append("refs")
        if eff.negative and not caps.negative:
            eff = replace(eff, negative=None)
            dropped.append("negative")
        if eff.video_mode and eff.video_mode not in caps.video_modes:
            eff = replace(eff, video_mode=None)
            dropped.append("video_mode")
        return eff, dropped

    def knobs_dict(self) -> dict[str, Any]:
        """The knobs, in the shape the outcome record stores them.

        Here because the workflow needs them as JSON-safe primitives to carry
        across a DBOS step boundary. Imported inside the method: ``outcome``
        already imports this module, and the record's shape belongs to it.
        """
        from app.services.generation.outcome import knobs_of

        return knobs_of(self)

    def to_codex_daemon_payload(
        self, *, engine_model: str, ref_urls: list[str]
    ) -> dict[str, Any]:
        """Payload for `tools/codex-daemon` image jobs.

        Sends both `size` and `ratio`, and keeps doing so. `size` is the
        canonical shape key — the daemon consumes it and nothing else for
        shape; deleting it would force a ratio→size table into JS, recreating
        the two-table drift this contract exists to end. `ratio` rides along
        for logging and attribution only — `index.mjs` says in so many words
        that this side ignores it. The aspect phrase is appended to the
        prompt here because codex only honours shape through language — the
        daemon must not have to know that.
        """
        return {
            "engine": "codex",
            "prompt": self.prompt + aspect_instruction(self.ratio or ""),
            "ratio": self.ratio,
            "size": CODEX_SIZES.get(self.ratio or "", CODEX_DEFAULT_SIZE),
            "quality": self.quality,
            "model": engine_model or "",
            "ref_urls": list(ref_urls),
        }
