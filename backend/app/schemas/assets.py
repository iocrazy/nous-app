"""Pydantic schemas for the asset library (mig 445/446).

Snowflake ids are strings at this boundary (bigIntSafeFetch discipline).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Dict, Generic, List, Literal, Optional, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

# Snowflake ids ride as strings here (bigIntSafeFetch discipline), but the
# service int()s them. Without this the first non-numeric body value is a
# ValueError deep in the service — a 500 where the caller should have got a 422.
SNOWFLAKE_PATTERN = r"^[0-9]{1,20}$"

# ``assets.id`` and every id it references are PostgreSQL BIGINT. The pattern
# alone admits 20 digits, i.e. up to 10^20-1, which is ~10x past int64: the
# value parses, reaches the driver, and fails at BIND — a 500 handed out for
# hostile input. Bound it where the string is validated instead.
_INT64_EXCLUSIVE_MAX = 2**63


def within_int64(value: Optional[str]) -> Optional[str]:
    """Reject digit strings the BIGINT columns cannot hold (``>= 2**63``)."""
    if value is not None and int(value) >= _INT64_EXCLUSIVE_MAX:
        raise ValueError(f"id must be < {_INT64_EXCLUSIVE_MAX} (BIGINT range)")
    return value


SnowflakeId = Annotated[
    str, Field(pattern=SNOWFLAKE_PATTERN), AfterValidator(within_int64)
]

AssetType = Literal["character", "location", "prop", "costume", "prompt", "audio"]
AssetSource = Literal[
    "manual", "script_import", "generated", "migrated", "duplicated", "system_preset"
]
"""Every provenance the ``assets_source_check`` constraint allows. This is the
RESPONSE vocabulary — a row legitimately comes back as ``duplicated`` /
``migrated`` / ``system_preset`` / ``script_import``."""

AssetCreateSource = Literal["manual", "generated"]
"""The subset a CLIENT may claim on POST /assets.

The other four are assertions only the server can honestly make: ``duplicated``
is set by ``AssetsService.duplicate``, ``migrated`` by the migration workflow,
``system_preset`` by the seeder, ``script_import`` by the script importer —
each alongside the row (``duplicated_from``, the preset flag) that makes the
claim true. Accepting them from the request body let any client forge
provenance on a hand-made asset, and provenance is written once at creation
and never corrected afterwards, so nothing downstream could tell.

Those server-side paths do NOT go through ``AssetCreate``: ``duplicate`` hands
``create_raw`` a full column dict, and the seeder writes rows directly. So the
narrowing costs them nothing — verified by grep, ``AssetCreate(...)`` has TWO
non-test construction sites, and neither lets a client near this field:
``generated_inbox_service`` (server-built, passes ``generated``) and
``AssetsService._import_one`` (leaves the default and asserts ``script_import``
through ``create_asset``'s keyword-only ``source=`` instead — a parameter no
route passes, so the request body cannot reach it).

⚠️ That count is the check this note makes falsifiable, so re-run the grep when
you add a caller rather than trusting the number:
``grep -rn "AssetCreate(" app/``."""
LinkRelation = Literal["wears", "holds", "ambience_of", "voice_of"]
ReadinessState = Literal["ready", "draft"]

LibraryFilter = Literal["in", "out", "all"]
"""``GET /assets?library=`` — which side of ``assets.in_library`` to return.

Three values, not a boolean, because "both" is a real answer this surface
needs: the shelf asks ``in`` (the library IS the members), the project panel
asks ``all`` (a project's page is the home of its own entities whether or not
anyone adopted them), and ``out`` is what makes the shelf's "Not In Library"
chip able to show the user what is waiting to be adopted.

A boolean with a null default would have collapsed "all" and "unspecified" into
the same value, which is the ambiguity that makes a default impossible to
change later."""


class AssetCreate(BaseModel):
    asset_type: AssetType
    name: str = Field(..., min_length=1, max_length=200)
    subtype: Optional[str] = Field(default=None, max_length=40)
    role_tag: str = Field(default="", max_length=40)
    description: str = Field(default="", max_length=20000)
    attrs: Dict[str, Any] = Field(default_factory=dict)
    prompt_positive: Optional[str] = Field(default=None, max_length=20000)
    prompt_negative: Optional[str] = Field(default=None, max_length=20000)
    prompt_positive_zh: Optional[str] = Field(default=None, max_length=20000)
    prompt_negative_zh: Optional[str] = Field(default=None, max_length=20000)
    platform_params: Dict[str, Any] = Field(default_factory=dict)
    tags: Dict[str, Any] = Field(default_factory=dict)
    # Narrower than the AssetSource the response carries — see AssetCreateSource.
    source: AssetCreateSource = "manual"


class AssetUpdate(BaseModel):
    """PATCH payload — **omit = unchanged, explicit null = clear**.

    The two are told apart by ``model_fields_set`` (the service dumps with
    ``exclude_unset=True``), not by the value: an omitted key never reaches the
    UPDATE, while ``{"cover_file_id": null}`` writes NULL. Before that, both
    read as ``None`` and "remove this asset's cover / subtype / prompt" was
    unreachable through the API — the request answered 200 and changed nothing.

    Only the nullable columns can be cleared —
    ``subtype / cover_file_id / prompt_positive / prompt_negative /
    prompt_positive_zh / prompt_negative_zh``. A null aimed at a NOT NULL
    column (``name``, ``attrs``, ``tags``, …) is a typed 422
    (``field_not_nullable``), never a dropped key.

    ``extra="forbid"``: without it ``{"promptpositive": "..."}`` answered 200
    with the asset untouched, so a typo'd field was indistinguishable from a
    successful edit — the silent-no-op class this module refuses everywhere else.
    """

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    subtype: Optional[str] = Field(default=None, max_length=40)
    role_tag: Optional[str] = Field(default=None, max_length=40)
    description: Optional[str] = Field(default=None, max_length=20000)
    attrs: Optional[Dict[str, Any]] = None
    prompt_positive: Optional[str] = Field(default=None, max_length=20000)
    prompt_negative: Optional[str] = Field(default=None, max_length=20000)
    prompt_positive_zh: Optional[str] = Field(default=None, max_length=20000)
    prompt_negative_zh: Optional[str] = Field(default=None, max_length=20000)
    platform_params: Optional[Dict[str, Any]] = None
    cover_file_id: Optional[SnowflakeId] = None
    tags: Optional[Dict[str, Any]] = None
    sort_order: Optional[int] = None

    # ⚠️ ``in_library`` (mig 449) is DELIBERATELY ABSENT, and it is the one
    # writable column this model does not mirror.
    #
    # Library membership has exactly ONE write path:
    # ``POST``/``DELETE /assets/{id}/library`` →
    # ``AssetsService.set_library_membership``. Declaring the field here would
    # add a second one, and the two would converge only at the repository —
    # so a rule later added to ``set_library_membership`` (an audit row, a
    # refusal, a side effect) would be silently bypassed by anything PATCHing
    # the column. That is the drift seam this exclusion removes rather than
    # documents.
    #
    # ``extra="forbid"`` makes the exclusion a TYPED refusal rather than a
    # silent drop: ``PATCH {"in_library": true}`` is a 422 naming the field, so
    # a client that guesses wrong is told where the real action is instead of
    # getting a 200 that changed nothing.
    # Pinned by ``tests/services/assets/test_schemas.py`` and by the
    # single-write-path guard in ``test_assets_library_membership.py``.


class AssetReadiness(BaseModel):
    state: ReadinessState
    missing: List[str] = Field(default_factory=list)


class AssetResponse(BaseModel):
    id: str
    # NULL for global system presets (assets_scope_or_preset CHECK).
    scope_id: Optional[str] = None
    asset_type: AssetType
    subtype: Optional[str] = None
    name: str
    role_tag: str = ""
    description: str = ""
    attrs: Dict[str, Any] = Field(default_factory=dict)
    prompt_positive: Optional[str] = None
    prompt_negative: Optional[str] = None
    prompt_positive_zh: Optional[str] = None
    prompt_negative_zh: Optional[str] = None
    platform_params: Dict[str, Any] = Field(default_factory=dict)
    cover_file_id: Optional[str] = None
    source: AssetSource = "manual"
    duplicated_from: Optional[str] = None
    is_system_preset: bool = False

    @field_validator("attrs", "platform_params", "tags", mode="before")
    @classmethod
    def _json_null_is_empty(cls, value: Any) -> Any:
        """A jsonb column can hold the JSON value ``null`` while satisfying
        ``NOT NULL DEFAULT '{}'`` — 2026-09-06 one hand-seeded system preset
        did exactly that, and because presets are unioned into EVERY scope,
        the asset shelf's All / Prompts tabs and the chat @-picker answered 500
        for every user until the row was repaired. mig 458 now refuses the
        value at the table; this validator keeps one bad row from ever taking
        a whole listing down again on the way OUT. ``None`` → ``{}``; anything
        else still has to be a dict."""
        return {} if value is None else value

    # mig 449 — see ``Assets.in_library``. REQUIRED, no default, unlike its
    # neighbours: this model is what FastAPI validates on the way out, and a
    # default would let a service that stopped emitting the key ship a row the
    # client reads as "in library". Membership decides whether the shelf shows
    # the asset at all, so guessing it is worse than failing loudly — the same
    # argument ``GenerateSlotPreview.aspect_ratio`` makes for its own frame.
    in_library: bool
    tags: Dict[str, Any] = Field(default_factory=dict)
    sort_order: int = 0
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    # derived
    readiness: AssetReadiness
    file_counts_by_slot: Dict[str, int] = Field(default_factory=dict)
    project_ids: List[str] = Field(default_factory=list)
    loadout_count: int = 0


class AssetCountsResponse(BaseModel):
    """GET /assets/counts — one non-deleted, non-preset count per asset type.

    Six explicit fields rather than ``Dict[str, int]``: this is the payload
    FastAPI validates on the way out, and a free-form dict would let a service
    that stopped emitting ``costume`` ship a body the sidebar then renders as a
    missing badge. Every field defaults to 0 so "no rows of this type" and "the
    key was omitted" cannot look the same to the client.

    The field set is pinned against ``models.assets.ASSET_TYPES`` by
    ``tests/services/assets/test_schemas.py`` — adding a seventh type to the
    slot table fails that test until this model carries it too.
    """

    character: int = 0
    location: int = 0
    prop: int = 0
    costume: int = 0
    prompt: int = 0
    audio: int = 0


class AssetFileResponse(BaseModel):
    asset_id: str
    resource_id: str
    slot: str
    loadout_id: Optional[str] = None
    sort_order: int = 0
    note: Optional[str] = None
    # Declared because ``_serialize_file`` emits it: once the routes carry a
    # response_model, any key missing from the model is silently dropped from
    # the wire — a removal no test or log would report.
    attached_by: Optional[str] = None
    attached_at: datetime


class AssetLinkResponse(BaseModel):
    from_asset_id: str
    to_asset_id: str
    relation: LinkRelation
    # Same reason as AssetFileResponse.attached_by — ``_serialize_link`` emits it.
    created_at: Optional[datetime] = None


class LoadoutCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    costume_ids: List[SnowflakeId] = Field(default_factory=list)
    prop_ids: List[SnowflakeId] = Field(default_factory=list)
    prompt_extra: Optional[str] = Field(default=None, max_length=20000)


class LoadoutUpdate(BaseModel):
    """PATCH payload — None means "leave unchanged".

    ``is_default=False`` is NOT an operation: ``uq_loadout_default`` means an
    asset has exactly one default, so there is no "unset the default", only
    "make a different one default". A False is therefore dropped, deliberately.

    ``extra="forbid"`` for the same reason as :class:`AssetUpdate`.
    """

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    costume_ids: Optional[List[SnowflakeId]] = None
    prop_ids: Optional[List[SnowflakeId]] = None
    prompt_extra: Optional[str] = Field(default=None, max_length=20000)
    is_default: Optional[bool] = None
    sort_order: Optional[int] = None


class LoadoutResponse(BaseModel):
    id: str
    asset_id: str
    name: str
    is_default: bool
    costume_ids: List[str] = Field(default_factory=list)
    prop_ids: List[str] = Field(default_factory=list)
    prompt_extra: Optional[str] = None
    sort_order: int = 0
    created_at: datetime


class UsedInCanvasRef(BaseModel):
    """One canvas that references this asset (P4).

    ``node_ids`` is a LIST because a canvas may place the same asset on several
    nodes; collapsing to a count would lose the ability to jump to one. Ids are
    strings for the usual snowflake-precision reason.
    """

    canvas_id: str
    canvas_name: str
    kind: str
    project_id: str
    node_ids: List[str] = Field(default_factory=list)
    loadout_ids: List[str] = Field(default_factory=list)


class UsedInResponse(BaseModel):
    """Where an asset is in use (spec §5.1 ``used_in``).

    ``storyboards`` is declared and always EMPTY today — the storyboard side has
    no ref mirror yet (a later phase). It is here rather than absent so the
    client renders "no storyboard usage" instead of branching on a missing key,
    and so the day it starts filling nothing on the wire has to change shape.
    """

    canvases: List[UsedInCanvasRef] = Field(default_factory=list)
    storyboards: List[Dict[str, Any]] = Field(default_factory=list)


class AssetDetailResponse(AssetResponse):
    files: List[AssetFileResponse] = Field(default_factory=list)
    links: List[AssetLinkResponse] = Field(default_factory=list)  # outgoing
    linked_by: List[AssetLinkResponse] = Field(default_factory=list)  # incoming
    loadouts: List[LoadoutResponse] = Field(default_factory=list)
    # OPT-IN (``?include_used_in=true``), and NULL rather than empty when it
    # was not asked for. An empty ``used_in`` is the claim "this asset is used
    # nowhere"; a caller that did not pay for the five-table aggregate has no
    # basis for making it, and a client that cannot tell the two apart renders
    # "Used nowhere" for an answer nobody computed. Three states on the wire —
    # null (not asked), empty lists (asked, none), populated — and the client
    # keeps all three.
    used_in: Optional[UsedInResponse] = None


class AttachFileRequest(BaseModel):
    resource_id: SnowflakeId
    slot: str = Field(default="unsorted", min_length=1)
    loadout_id: Optional[SnowflakeId] = None
    note: Optional[str] = Field(default=None, max_length=2000)


class AttachFilesBatchRequest(BaseModel):
    items: List[AttachFileRequest] = Field(..., min_length=1, max_length=200)


class LinkRequest(BaseModel):
    to_asset_id: SnowflakeId
    relation: LinkRelation


class ProjectRefRequest(BaseModel):
    project_id: SnowflakeId


class DuplicateRequest(BaseModel):
    """POST /assets/{id}/duplicate body — the copy's name, and nothing else.

    Everything else about the copy is derived from the source (that is the
    point), so this is the whole knob. Omitting ``name`` means "{source}
    (copy)"; an EMPTY string does not, and is refused like every other blank
    name on this surface rather than producing an unnamed asset.

    ``extra="forbid"`` for the same reason as :class:`AssetUpdate`: a typo'd
    ``{"naem": "..."}`` would otherwise answer 201 having quietly used the
    default name — a request that did something other than what was asked and
    reported success.
    """

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)


class PromptTranslateRequest(BaseModel):
    """POST /assets/{id}/prompt/translate body.

    ``target_lang='zh'`` reads ``prompt_positive`` / ``prompt_negative`` and
    writes the ``_zh`` columns; ``'en'`` reads the ``_zh`` columns and writes
    the EN side. The source side is never modified.

    ``force`` is the ONLY way to overwrite a target that already holds text.
    Without it a translate run would silently replace a hand-written Chinese
    prompt with a machine one, with no undo and a 200 — so the default skips
    filled targets, and when that leaves nothing the answer is a typed 422
    (``nothing_to_translate``) rather than a cheerful no-op.

    ``extra="forbid"`` for the same reason as :class:`AssetUpdate`: a typo'd
    ``{"targetlang": "zh"}`` must not fall through to a default and report
    success for a direction nobody asked for.
    """

    model_config = ConfigDict(extra="forbid")

    target_lang: Literal["zh", "en"]
    force: bool = False


class GenerateSlotRequest(BaseModel):
    """POST /assets/{id}/generate-slot body.

    ``count`` is capped at 4 because every unit is a paid provider call made
    IN-REQUEST; a larger fan-out belongs in a workflow, not a request handler.

    ``model`` omitted means "the catalog default" — the image provider chain
    resolves whatever model the admin enabled, so a caller does NOT have to
    know a provider's model id to generate.

    ``extra="forbid"`` for the same reason as :class:`AssetUpdate`: a typo'd
    ``{"loadout": "..."}`` must not answer 202 having generated with no
    loadout at all — a run that cost money and did something other than what
    was asked.
    """

    model_config = ConfigDict(extra="forbid")

    slot: str = Field(..., min_length=1, max_length=40)
    loadout_id: Optional[SnowflakeId] = None
    model: Optional[str] = Field(default=None, max_length=100)
    count: int = Field(1, ge=1, le=4)


class GenerateSlotPreview(BaseModel):
    """What a generate-slot run would compose — the dry run of the paid call.

    ``model`` is null unless the caller pinned one: the effective model is
    resolved from the ``mediahub_models`` catalog at generation time, and
    echoing the legacy ``dall-e-3`` sentinel here would name a model that is
    not what runs.
    """

    positive: str = Field(
        ...,
        description=(
            "The prompt sent to the image provider — asset prompt, loadout "
            "extra, linked costume/prop prompts, then the slot template."
        ),
    )
    negative: str = Field(
        ...,
        description=(
            "RECORDED PROVENANCE, NOT A PROVIDER INPUT. No image adapter in "
            "this repo accepts a negative prompt, so this text is stored on "
            "the generation as params.negative for the user to read and "
            "re-use; it does not shape the image."
        ),
    )
    reference_resource_ids: List[str] = Field(
        default_factory=list,
        description=(
            "The asset's own files that ride along as references, in priority "
            "order (primary slot, worn, stills, the rest), capped at the "
            "provider ceiling. Each is materialized to a local file and sent "
            "as reference_image_paths — honored by the codex adapter; the "
            "ark and jimeng adapters ignore references entirely."
        ),
    )
    aspect_ratio: str = Field(
        # Required, no default: this model exists so FastAPI VALIDATES the
        # payload on the way out. With a default, a service that stopped
        # emitting the frame would silently ship "16:9" — the half-row failure
        # the envelope was introduced to make loud.
        ...,
        description=(
            "Frame the slot template asks for (grids 1:1, costume flat lay "
            "3:2, otherwise 16:9). Sent to the provider."
        ),
    )
    model: Optional[str] = None


class GenerateSlotFailure(BaseModel):
    """One failed unit of a generate-slot run.

    ``index`` is the position in the requested ``count``, so a partially
    successful run says WHICH units failed rather than "2 of 3 worked".
    """

    index: int
    code: str
    detail: str


class SkippedReference(BaseModel):
    """A reference file that was chosen but could not be handed to the provider.

    Its own list rather than an entry in ``failed``: the run SUCCEEDED, it just
    generated with fewer references than the preview promised. Silently
    dropping it is the "选了也生成了但图里没有" failure this repo has already
    recorded once — the user sees an image that ignored the reference and has
    no way to learn why.
    """

    resource_id: str
    reason: str


class BundlePrompt(BaseModel):
    """The two prompt halves of a bundle.

    Nested rather than flattened to ``positive``/``negative`` so a caller that
    forwards "the prompt" forwards BOTH — ``negative`` is provenance the
    generation records, and a flat shape is how it gets left behind.
    """

    positive: str
    negative: str


DroppedReason = Literal["no_image_file", "over_limit", "provider_no_refs"]
"""Why a reference the asset owns is not in the delivered list.

A closed vocabulary, declared here so FastAPI VALIDATES it on the way out: an
invented reason fails loudly rather than reaching a UI that has no string for
it and renders nothing — which is the silent drop this whole field exists to
end.

``provider_no_refs`` is deliberately NOT folded into ``over_limit``: a ceiling
of zero is not a contest this file lost, and the user's remedy is a different
model rather than fewer picks."""


class DroppedReference(BaseModel):
    """One reference that will NOT be sent, and why."""

    resource_id: str
    reason: DroppedReason


class BundleResponse(BaseModel):
    """``GET /assets/{id}/bundle`` — the delivery payload for one model.

    Every field is REQUIRED, ``dropped`` included. A defaulted empty list would
    let a service that stopped reporting drops answer "nothing was dropped",
    which is the exact failure the field was added to prevent.
    """

    prompt: BundlePrompt
    reference_resource_ids: List[str] = Field(
        ...,
        description=(
            "The asset's files that fit this provider, in priority order "
            "(primary slot, worn, stills, the rest), trimmed to max_refs."
        ),
    )
    dropped: List[DroppedReference] = Field(
        ...,
        description=(
            "Every reference the asset owns that is NOT in the list above, "
            "each with its reason. Present and empty when nothing was dropped."
        ),
    )
    max_refs: int = Field(
        ...,
        description=(
            "How many references this provider accepts (0 = none at all). "
            "Echoed so a caller can say '3 of 5 sent' without inferring the "
            "ceiling from the two list lengths."
        ),
    )


class GenerateSlotResponse(BaseModel):
    """202 body. ``failed`` is present even when empty — a caller must not
    have to infer "did any of them fail?" from ``len(generation_ids)``."""

    generation_ids: List[str] = Field(default_factory=list)
    failed: List[GenerateSlotFailure] = Field(default_factory=list)
    skipped_references: List[SkippedReference] = Field(default_factory=list)
    inbox_state: Literal["unreviewed"] = "unreviewed"


# ── response envelopes ─────────────────────────────────────────────────────
# Every /assets route answers ``{success, data}`` on the way out and
# ``{success:false, error:{code, detail, ...}}`` on refusal. Declaring both as
# models is what makes FastAPI VALIDATE the payload (a service that stops
# emitting ``readiness`` now fails loudly instead of shipping a half row) and
# what puts a real schema in the OpenAPI document the frontend types read off.

T = TypeVar("T")


class ResolveLegacyResponse(BaseModel):
    """GET /assets/resolve-legacy — the asset a pre-P3 canvas card became.

    ``asset_id`` is explicitly nullable and always present. "Nothing migrated
    with that provenance" is a real answer the caller has to act on (it keeps
    the legacy card as-is), and it must not arrive as a missing key that reads
    the same as a truncated body.

    A string, like every other id on this router — ``assets.id`` is a Snowflake
    BIGINT and a JSON number would lose precision in the browser.
    """

    asset_id: Optional[str] = None


class Envelope(BaseModel, Generic[T]):
    """Success wrapper. ``success`` is always True here — a failure is an
    :class:`ErrorEnvelope`, returned as a raw JSONResponse so it bypasses this
    model rather than being coerced into it."""

    success: bool = True
    data: T


class ErrorEnvelope(BaseModel):
    """Refusal wrapper. ``error`` stays an open dict on purpose: each
    ``AssetError`` carries ``code`` + ``detail`` plus its own extras
    (``existing_asset_id``, ``costume_ids``, …), and narrowing the model would
    drop exactly the field a client needs to act on."""

    success: bool = False
    error: Dict[str, Any]


class DeletedResponse(BaseModel):
    deleted: bool = True


class DetachedResponse(BaseModel):
    detached: bool = True


class RemovedResponse(BaseModel):
    removed: bool = True


class LinkedResponse(BaseModel):
    linked: bool = True


class UnlinkedResponse(BaseModel):
    unlinked: bool = True


# ── POST /projects/{id}/assets/import-from-script ───────────────────────────


class ImportedAssetItem(BaseModel):
    """One name's outcome. Every name the script yielded gets a row here —
    including the ones nothing happened to (CLAUDE.md 触发路径必须类型化失败回显:
    a batch that silently drops what it could not do reports success for work
    it did not perform).

    ``action`` and ``linked`` are reported INDEPENDENTLY on purpose. They answer
    different questions — "did this call create the asset row" vs "does a
    project ref for it exist now" — and folding the second into the first is
    exactly the "正交的结果各自独立上报" failure: an asset that was created but
    whose ref write then failed would read as a clean ``created`` to a caller
    branching on ``action`` alone.

    - ``created`` — this call inserted the asset. ``linked`` says whether its
      project ref landed too; if it did not, ``code``/``detail`` say why.
    - ``linked`` (action) — the asset already existed under this name+type in
      this scope and this call added the missing project ref.
    - ``skipped`` — nothing was written. ``code`` says which: ``already_linked``
      (asset and ref both already there — the idempotent re-run), ``empty_name``
      / ``name_too_long`` (unusable name), or the ``AssetError`` code that
      refused it.
    """

    name: str
    asset_type: AssetType
    action: Literal["created", "linked", "skipped"]
    asset_id: Optional[str] = None
    linked: bool = False
    code: Optional[str] = None
    detail: Optional[str] = None


class ImportFromScriptResponse(BaseModel):
    """The batch outcome. The three tallies count ``items`` by ``action``, so
    ``created + linked + skipped == len(items)`` always holds — a caller can
    render the summary line without walking the list, and a mismatch is a bug
    in this endpoint rather than an ambiguity the client has to resolve."""

    items: List[ImportedAssetItem]
    created: int
    linked: int
    skipped: int
