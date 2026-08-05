"""What each agent run has actually been SHOWN — the server's own record,
and the only trusted input to A5's edit precondition.

WHY THIS EXISTS (A5 review, Critical). Spec §5.2 rejected element hashing on
the grounds that ``content_version`` is already an operation-sequence
watermark and the optimistic-concurrency plumbing already speaks it. That
reasoning is correct **for the editor and not for an LLM**, and the
difference is a trust boundary rather than a mechanism:

    the editor NECESSARILY submits the version it read — the number is a
    by-product of having fetched the row. A model can emit any integer, and
    the first cut of this stage actively handed it the right one (ProposeEdit
    echoed the scene's CURRENT ``content_version``, and a conflict refusal
    returned ``current_content_version`` next to the words "re-read and
    rebase"). A model that copied that number into the write call made
    ``base == current`` true, which skipped the element check entirely and
    overwrote the author's typing in silence.

The same shape of bug is impossible once the precondition stops reading any
model-supplied value. So: ``ReadScene`` records, run-side, the exact element
array it handed the model; ``ApplyEdit`` compares the scene as it stands NOW
against THAT record. The model's ``base_content_version`` is demoted to a
cross-check — reported when it disagrees, never load-bearing.

Two properties fall out of this that the previous design could only assert:

  * **You cannot write what you never read.** No record, no write — which is
    what ``apply_edit``'s docstring already wanted and could not enforce.
  * **A refusal is not a retry oracle.** After a conflict the model must call
    ``ReadScene`` again (which refreshes the record) before a write can
    succeed. Handing it the current text to rebase on is therefore safe: the
    text alone does not unlock the write.

STORAGE IS DELIBERATELY IN-PROCESS, and fail-closed covers the gap. A record
lives for one run inside one worker. ``ReadScene`` and ``ApplyEdit`` in the
same turn run in the same tool loop, hence the same process, so the normal
path always finds it. Anything else — a different worker, an evicted entry, a
model that read in an earlier turn (a new ``run_id``) — finds nothing and is
refused with "read the scene first", which is both correct and
self-healing: the model re-reads and proceeds. A DB table would buy
durability this does not need, at the cost of a migration and a write on
every scene read.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Bounds. Scenes are small (production: mean 2.1 elements, max 10), so the
# memory here is negligible; these exist so a long-lived worker cannot grow
# the map without limit, not because the data is large. Eviction is LRU and
# an evicted entry degrades to "read the scene again", never to a bypass.
MAX_RUNS = 256
MAX_SCENES_PER_RUN = 64


@dataclass(frozen=True)
class SceneObservation:
    """One scene exactly as it was handed to a model.

    ``elements`` are the RAW ``content_json`` dicts, not the projected
    ``{element_id, type, text}`` view the tool returns: the comparison at
    write time runs through ``version_service.diff_scenes``, which classifies
    added/removed/changed/moved over whole elements, and projecting first
    would blind it to any key it had dropped."""

    content_version: int
    elements: tuple[dict[str, Any], ...]
    observed_at: float


# {run_id: {scene_id: SceneObservation}} — both levels LRU by insertion.
_OBSERVED: "OrderedDict[str, OrderedDict[str, SceneObservation]]" = OrderedDict()


def _key(value: Any) -> str:
    return str(value)


def record_scene_read(
    run_id: Any,
    scene_id: Any,
    content_version: int,
    elements: list[dict[str, Any]],
) -> None:
    """Record what a run was just shown. Called from the ONE place that hands
    scene content to a model (``scoped_script_gateway.read_scene_elements``),
    so the record cannot drift from what the model actually saw.

    A later read of the same scene REPLACES the earlier record: the tightest
    precondition is against the most recent thing the model was shown, and
    that is also what "re-read the scene and rebase" has to mean for the
    retry loop to terminate."""
    if run_id is None or scene_id is None:
        return
    run_key, scene_key = _key(run_id), _key(scene_id)

    per_run = _OBSERVED.get(run_key)
    if per_run is None:
        per_run = OrderedDict()
        _OBSERVED[run_key] = per_run
    _OBSERVED.move_to_end(run_key)

    per_run[scene_key] = SceneObservation(
        content_version=int(content_version or 0),
        elements=tuple(dict(el) for el in elements if isinstance(el, dict)),
        observed_at=time.time(),
    )
    per_run.move_to_end(scene_key)

    while len(per_run) > MAX_SCENES_PER_RUN:
        per_run.popitem(last=False)
    while len(_OBSERVED) > MAX_RUNS:
        _OBSERVED.popitem(last=False)


def observed_scene(run_id: Any, scene_id: Any) -> Optional[SceneObservation]:
    """What this run was last shown for this scene, or ``None``.

    ``None`` is a REFUSAL condition at every call site, never a reason to
    fall back to a model-supplied value — falling back is precisely the
    Critical this module exists to close."""
    if run_id is None or scene_id is None:
        return None
    per_run = _OBSERVED.get(_key(run_id))
    if per_run is None:
        return None
    return per_run.get(_key(scene_id))


def forget_run(run_id: Any) -> None:
    """Drop a finished run's records. Not required for correctness (the LRU
    bounds the map either way) — exposed so tests can isolate, and so a
    future run-completion hook can release early."""
    _OBSERVED.pop(_key(run_id), None)


def _reset_for_tests() -> None:
    _OBSERVED.clear()


__all__ = [
    "MAX_RUNS",
    "MAX_SCENES_PER_RUN",
    "SceneObservation",
    "forget_run",
    "observed_scene",
    "record_scene_read",
]
