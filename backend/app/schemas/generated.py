"""Pydantic schemas for the Generated inbox (`/api/v1/generated`).

Snowflake ids ride as strings here, same as :mod:`app.schemas.assets` — the
repository already ``_normalize``s rows into string ids and ISO datetimes.

Every request model is ``extra="forbid"``: a typo'd field on a batch body would
otherwise answer 200 having done something other than what the caller asked
(``{"action": "delete", "idz": [...]}`` is an empty batch, not an error).
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.assets import AssetType, SnowflakeId

# ``deleted`` exists as a state but is not offered as a filter tab; it is here
# because a row can be read back after a delete (e.g. in a cleanup sample).
ReviewState = Literal["unreviewed", "saved", "in_assets", "deleted"]

BatchAction = Literal["save", "save_as_asset", "delete"]

# Cards are one line; anything longer is cut. Kept as a constant so the test
# that pins the cap and the code that applies it cannot drift apart.
TITLE_MAX_CHARS = 80

# A sentence ends at the first of these. ``。`` matters because prompts in this
# product are routinely Chinese, and a Chinese prompt has no ASCII period at
# all — without it the whole prompt would only ever be cut by length.
_SENTENCE_ENDS = (".", "。", "\n")


def _cap(text: str) -> str:
    """Hard cap at :data:`TITLE_MAX_CHARS`, ``…`` included in the count."""
    return text if len(text) <= TITLE_MAX_CHARS else text[: TITLE_MAX_CHARS - 1] + "…"


def derive_title(
    prompt: Optional[str],
    media_kind: str,
    model: Optional[str],
    provider: Optional[str],
    origin_kind: str,
    *,
    filename: Optional[str] = None,
) -> str:
    """Card title: the prompt's first sentence, else the file's name, else a
    descriptive fallback.

    Cut at the first ``.``/``。``/newline (delimiter excluded), then hard-capped
    at :data:`TITLE_MAX_CHARS` *including* the appended ``…`` — the return value
    is never longer than the cap, so callers can size a column to it.

    A prompt that yields an empty first sentence (blank, whitespace, or leading
    delimiter) falls back rather than returning ``""``: an empty title renders
    as a blank card, which is indistinguishable from a broken row.

    ``filename`` is a SEPARATE source on purpose, and it is used **verbatim**
    (capped, never cut). It exists because a registered My Uploads row has no
    prompt at all, and routing a file's name through the prompt argument would
    put it through the sentence cut above: ``interview.v2.mp3`` would be
    truncated at its first dot and titled ``interview``, and a dotfile would cut
    to nothing and silently take the fallback. A file's name is not prose and
    has no sentences — the two sources are different kinds of text, so they get
    different handling. The prompt still wins when both are present; nothing
    that has a real prompt should be re-titled after a file.
    """
    text = (prompt or "").strip()
    if text:
        cuts = [i for i in (text.find(c) for c in _SENTENCE_ENDS) if i != -1]
        if cuts:
            text = text[: min(cuts)]
        text = text.strip()
    if text:
        return _cap(text)
    name = (filename or "").strip()
    if name:
        return _cap(name)
    return f"{media_kind} · {model or provider or origin_kind}"


class GeneratedSource(BaseModel):
    """Mirror of ``app.services.library.generated_source.describe_source``.

    ``deep_link`` is ``None`` for kinds that have no route today (shot, chat) —
    never an invented URL.

    ``issue_id`` / ``run_id`` / ``step`` are the 3a run provenance, filled only
    for ``agent_run`` rows that are actually in ``run_deliverables``. They stay
    ``None`` for everything else, historical rows included — a card with no
    provenance is normal history, not a fault. Ids are strings: they are
    Snowflake BIGINTs.
    """

    kind: str
    label: str
    canvas_id: Optional[str] = None
    node_id: Optional[str] = None
    shot_id: Optional[str] = None
    conversation_id: Optional[str] = None
    deep_link: Optional[str] = None
    issue_id: Optional[str] = None
    run_id: Optional[str] = None
    step: Optional[int] = None


class GeneratedItem(BaseModel):
    id: str
    scope_id: str
    media_kind: str
    mime: Optional[str] = None
    prompt: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    origin_kind: str
    canvas_id: Optional[str] = None
    node_id: Optional[str] = None
    created_at: datetime
    promoted_resource_id: Optional[str] = None
    review_state: ReviewState
    source_asset_id: Optional[str] = None
    # derived
    source: GeneratedSource
    title: str


class GeneratedPage(BaseModel):
    items: List[GeneratedItem] = Field(default_factory=list)
    next_cursor: Optional[str] = None


class CountsResponse(BaseModel):
    """Tab counters. ``deleted`` is deliberately not on the wire — there is no
    deleted tab, and shipping a count for a tab that does not exist invites a
    UI that reads it."""

    unreviewed: int
    saved: int
    in_assets: int


class NewAssetSpec(BaseModel):
    """Create-and-attach in one call, for "save into a brand-new asset"."""

    model_config = ConfigDict(extra="forbid")

    asset_type: AssetType
    name: str = Field(..., min_length=1, max_length=200)


class SaveAsAssetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: Optional[SnowflakeId] = None
    new_asset: Optional[NewAssetSpec] = None
    slot: str = Field(default="unsorted", min_length=1, max_length=40)
    loadout_id: Optional[SnowflakeId] = None

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "SaveAsAssetRequest":
        if (self.asset_id is None) == (self.new_asset is None):
            raise ValueError("exactly one of asset_id / new_asset is required")
        return self


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: List[SnowflakeId] = Field(..., min_length=1, max_length=200)
    action: BatchAction
    save_as_asset: Optional[SaveAsAssetRequest] = None

    @model_validator(mode="after")
    def _payload_matches_action(self) -> "BatchRequest":
        wants_payload = self.action == "save_as_asset"
        if wants_payload and self.save_as_asset is None:
            raise ValueError("save_as_asset payload is required for this action")
        if not wants_payload and self.save_as_asset is not None:
            raise ValueError(f"save_as_asset payload is not valid for {self.action!r}")
        return self


class CleanupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    older_than_days: int = Field(default=30, ge=1, le=3650)
    # Destructive default is the safe one: a caller that forgets the flag gets
    # a preview, not a purge.
    dry_run: bool = True


class CleanupResponse(BaseModel):
    dry_run: bool
    count: int
    sample: List[GeneratedItem] = Field(default_factory=list)
    deleted: int = 0
    # One pass scans a bounded window, so ``count`` is "matched in this pass",
    # not "matched ever". Without this flag a capped pass and an exhaustive one
    # are indistinguishable on the wire, and a UI would report the cleanup as
    # finished while rows remain.
    truncated: bool = False


class BatchFailure(BaseModel):
    id: str
    code: str
    detail: str


class BatchResult(BaseModel):
    """Per-id outcome. Partial failure is reported, not collapsed into a 500 —
    "12 of 20 saved" has to survive the response boundary."""

    ok: List[str] = Field(default_factory=list)
    failed: List[BatchFailure] = Field(default_factory=list)
