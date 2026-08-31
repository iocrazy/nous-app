"""What a generation record says about itself.

Four keys, written on every generation by both the server and the daemon
upload path:

  requested — the knobs the user set
  effective — what survived reconciliation and actually went out
  measured  — real pixels/duration of the product, or null
  honored   — did `measured` match `effective`'s shape (null = no verdict)

`requested` vs `effective` is the distinction that makes the record worth
keeping: it separates "we never sent it" from "they ignored it", which is
exactly the pair that was indistinguishable before this contract.

Two entry points, one assembly. The server path runs inside DBOS, where
only JSON-safe primitives cross a step boundary, so it serialises the
knobs first and calls ``build_outcome_params_from_dicts``; callers holding
the objects call ``build_outcome_params``. Both funnel through ``_assemble``
so the two paths can never disagree about the same generation.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from app.services.generation.measure import Measured, compare_aspect
from app.services.generation.request import GenerationRequest

_KNOBS = ("ratio", "quality", "resolution", "negative", "video_mode", "duration")


def _set_only(values: Mapping[str, Any]) -> dict[str, Any]:
    """Set knobs only. A null in the record would read as "asked for nothing
    in particular", which is a different claim from "did not ask"."""
    return {k: v for k, v in values.items() if v is not None and v != ""}


def knobs_of(req: GenerationRequest) -> dict[str, Any]:
    """The knobs of a request, in the shape the record stores them.

    Public because the workflow serialises this across a DBOS step boundary
    (see ``GenerationRequest.knobs_dict``); a private name imported from
    another module is the kind of thing that gets tidied away by someone
    who cannot see that it is load-bearing.
    """
    out = _set_only({name: getattr(req, name, None) for name in _KNOBS})
    if req.refs:
        # A count, not the URLs: the record answers "how many references did
        # this go out with", and the URLs are already stored elsewhere.
        out["refs"] = len(req.refs)
    return out


def _measured_of(measured: Optional[Measured]) -> Optional[dict[str, Any]]:
    if measured is None:
        return None
    out: dict[str, Any] = {"width": measured.width, "height": measured.height}
    if measured.duration_s is not None:
        out["duration_s"] = measured.duration_s
    return out


def _assemble(
    requested: Mapping[str, Any],
    effective: Mapping[str, Any],
    dropped: list[str],
    measured: Optional[Measured],
) -> dict[str, Any]:
    honored: Optional[bool] = None
    if measured is not None and measured.width and measured.height:
        # Judge against what we SENT, not what was asked: a ratio we dropped
        # was never the provider's to honour.
        honored = compare_aspect(
            effective.get("ratio"), measured.width, measured.height
        )
    return {
        "requested": _set_only(requested),
        "effective": _set_only(effective),
        "dropped": list(dropped),
        "measured": _measured_of(measured),
        "honored": honored,
    }


def build_outcome_params(
    req: GenerationRequest,
    eff: GenerationRequest,
    dropped: list[str],
    measured: Optional[Measured],
) -> dict[str, Any]:
    """The outcome block merged into ``generated_media.params``."""
    return _assemble(knobs_of(req), knobs_of(eff), dropped, measured)


def build_outcome_params_from_dicts(
    *,
    requested: Mapping[str, Any],
    effective: Mapping[str, Any],
    dropped: list[str],
    measured: Optional[Measured],
) -> dict[str, Any]:
    """Same record, assembled from already-serialised knobs.

    ``effective`` is what was actually sent, so it — never ``requested`` —
    decides ``honored``.
    """
    return _assemble(requested, effective, dropped, measured)
