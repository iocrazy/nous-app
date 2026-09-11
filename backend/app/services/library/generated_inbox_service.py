"""Generated inbox orchestration (spec §6.1) — list, save, batch, cleanup.

Everything Tier-1 (``generated_media``) needs a decision on lives behind this
service. Three properties are load-bearing:

1. **The review-state machine is closed here.** No public method accepts a
   state string. ``saved`` is written only by ``mark_promoted`` (inside
   ``PromoteGeneratedMediaService.promote``) and by
   ``GeneratedMediaRepository.insert_registered_resource`` (a row that
   registers an EXISTING resource is born ``saved`` — Tier-2 already holds the
   bytes); ``in_assets`` is written by :meth:`_attach_and_mark` — and only
   after the attach has succeeded — which is the single writer both
   :meth:`save_as_asset` and :meth:`save_resource_as_asset` go through, and by
   ``workflows/backfill_generated_inbox._apply``, an admin-only reconciliation
   that labels rows whose attachment already exists. Two writers, both
   guarded; there is no generic setter.
   ``GeneratedMediaRepository.set_review_state`` is a repo primitive — it is
   deliberately NOT re-exported, because a caller that can write any state can
   silently un-promote a row.
2. **Save-as-asset is TWO segments, not one transaction — from a
   GENERATION.** ``promote`` runs first and OUTSIDE ``unit_of_work()`` (it does
   ``SET LOCAL ROLE``, storage I/O, and carries a "non-fatal" backlink guard
   that a shared transaction would make fatal — see
   :meth:`_save_as_asset_core` for the full reasoning); attach + ``in_assets``
   then share one transaction. A failed attach leaves the row ``saved`` with a
   promoted resource behind it — a legitimate state, identical to a plain
   :meth:`save`. What can never happen is the harmful direction, ``in_assets``
   without an attachment, because that write is last. Pure validation failures
   do not even get that far: :meth:`_validate_target` refuses before
   ``promote``.

   From a LIBRARY RESOURCE (:meth:`save_resource_as_asset`) the boundary is
   different, and deliberately so: there is nothing to promote — the resource
   already exists — so none of the three reasons above applies, and the mint of
   the inbox row shares the attach's transaction. A failure there leaves NO
   row, because an orphan inbox card for a file the user never generated is
   the harmful direction on that path.
3. **Errors are typed, never swallowed.** Everything a caller can be expected
   to act on becomes an :class:`AssetError` with a code; anything else (a
   storage backend refusing, a bug) propagates, because reporting a genuine
   fault as a per-item "failure" hides it.

``team_id`` only feeds ``describe_source``'s deep link. :meth:`list` takes it
explicitly (the router knows the caller's team); the single-row paths derive it
from ``scope_id``, which for ``generated_media`` IS the team id.
"""

from __future__ import annotations

import datetime
from pathlib import PurePosixPath
from typing import Any, Optional

from loguru import logger

from app.db.session import unit_of_work
from app.repositories.canvas_repository import CanvasRepository
from app.repositories.generated_media_repository import (
    CLEANUP_SCAN_LIMIT,
    GeneratedMediaRepository,
)
from app.repositories.resources_repository import ResourcesRepository
from app.repositories.run_deliverables_repository import (
    get_run_deliverables_repository,
)
from app.schemas.assets import AssetCreate, AttachFileRequest
from app.schemas.generated import (
    BatchRequest,
    BatchResult,
    CleanupRequest,
    CleanupResponse,
    GeneratedItem,
    SaveAsAssetRequest,
    derive_title,
)
from app.services.assets.assets_service import AssetError, AssetsService
from app.services.assets.slots import is_valid_slot
from app.services.library.generated_source import (
    LIBRARY_UPLOAD_ORIGIN,
    describe_source,
)
from app.services.library.promote_generated_media_service import (
    PromoteGeneratedMediaService,
)
from app.services.library.resource_file_path import resolve_resource_file_path

# One cleanup pass looks at at most this many rows. The number is the repo's
# own SQL cap (``CLEANUP_SCAN_LIMIT``), imported rather than repeated so the
# request and the cap cannot drift — see the comment at its definition.
# ``count`` is therefore "matched in this pass", not "matched ever" — a second
# run picks up the rest.
# The preview a dry run shows. Enough to recognise what is about to go.
_CLEANUP_SAMPLE = 12
# An origin_kind that is NULL/blank in the database would render as an empty
# card label ("" reads as a broken card, not as "we don't know").
_UNKNOWN_ORIGIN = "unknown"
# ``params`` key holding the name a registered My Uploads row should be titled
# after. In ``params`` and not in ``prompt``: the row has no prompt, and
# ``derive_title`` treats a prompt as prose (it cuts at the first ``.``), which
# would truncate ``interview.v2.mp3`` to ``interview``. ``params`` never
# reaches the wire — ``GeneratedItem`` drops it — so this is internal
# provenance, not a new public field.
SOURCE_FILENAME_KEY = "source_filename"
# The media kinds a LIBRARY resource may seed an asset file with, in the order
# the refusal detail lists them. Both are ``mime`` top-level types AND the
# ``generated_media.media_kind`` value written for them, which is why one tuple
# can drive the check and the column. Derived from the slot table, not from
# what ``mime_type`` happens to hold: every non-audio asset type's slots take a
# visual reference, ``audio``'s ``primary``/``variants`` take an audio file,
# and nothing in ``app/services/assets/slots.py`` takes a video or a document.
ACCEPTED_ASSET_FILE_KINDS = ("image", "audio")


def _title_source_from_filename(filename: Any) -> Optional[str]:
    """A resource's filename, without its extension, as a card title source.

    A registered My Uploads row has no generation prompt, so before this
    ``derive_title`` fell all the way through to
    ``f"{media_kind} · {origin_kind}"`` and an mp3 a user saved through "As
    Asset" read **audio · library_upload** in the inbox — a label that names
    neither the file nor anything the user typed.

    Only the LAST suffix goes, and only when it leaves something behind:
    ``song.mp3`` → ``song``, ``archive.tar.gz`` → ``archive.tar`` (guessing at
    double extensions would eat real name parts), ``.gitignore`` → ``.gitignore``
    (a leading-dot name is all name, no extension).

    Returns ``None`` — not ``""`` — when there is no usable name, so the caller
    writes no ``params`` key at all rather than an empty one; ``derive_title``
    then still reaches its descriptive fallback, which is the right answer when
    there is nothing to name the row after.
    """
    name = str(filename or "").strip()
    if not name:
        return None
    stem = PurePosixPath(name).stem.strip()
    return stem or None


async def _provenance_for(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Which run produced each ``agent_run`` row on this page (3a).

    One query per page, and only when the page actually contains an agent-made
    row — the other origins never read it.

    Best-effort on purpose: rows generated before the registry existed are
    simply not in it, and a lookup that fails must not take the whole inbox
    down with it. The failure is LOGGED, not swallowed — the card quietly
    losing its provenance line is a visible-enough symptom, but only if there
    is a log line to correlate it with.
    """
    wanted = [
        str(r["id"])
        for r in rows
        if r.get("origin_kind") == "agent_run" and r.get("id") is not None
    ]
    if not wanted:
        return {}
    try:
        return await get_run_deliverables_repository().provenance_for(
            kind="generated_media", ref_ids=wanted
        )
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.opt(exception=True).warning(
            f"[generated] deliverable provenance lookup failed for "
            f"{len(wanted)} row(s): {exc!r}"
        )
        return {}


def _build_item(
    row: dict[str, Any],
    *,
    canvas_names: dict[str, str],
    team_id: str,
    provenance: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Repo row → validated ``GeneratedItem`` dict (source + title attached).

    Validation is not decoration: the repo projection carries columns the wire
    model must never expose (``file_path``, ``params``, ``cost_cents``), and
    ``model_validate`` is what drops them.
    """
    kind = str(row.get("origin_kind") or "").strip() or _UNKNOWN_ORIGIN
    row = {**row, "origin_kind": kind}
    return GeneratedItem.model_validate(
        {
            **row,
            "source": describe_source(
                row,
                canvas_names=canvas_names,
                team_id=team_id,
                provenance=provenance,
            ),
            "title": derive_title(
                row.get("prompt"),
                str(row.get("media_kind") or ""),
                row.get("model"),
                row.get("provider"),
                kind,
                filename=(row.get("params") or {}).get(SOURCE_FILENAME_KEY),
            ),
        }
    ).model_dump()


class GeneratedInboxService:
    def __init__(
        self,
        gen_repo: Optional[GeneratedMediaRepository] = None,
        promote: Optional[PromoteGeneratedMediaService] = None,
        assets: Optional[AssetsService] = None,
        canvases: Optional[CanvasRepository] = None,
        resources: Optional[ResourcesRepository] = None,
    ):
        self.gen_repo = gen_repo or GeneratedMediaRepository()
        self.promote = promote or PromoteGeneratedMediaService()
        self.assets = assets or AssetsService()
        self.canvases = canvases or CanvasRepository()
        self.resources = resources or ResourcesRepository()

    # ── helpers ────────────────────────────────────────────────────────────

    async def _decorate(
        self, rows: list[dict[str, Any]], team_id: str
    ) -> list[dict[str, Any]]:
        """Attach source/title to a page of rows with ONE canvas-name query and
        ONE deliverable-registry lookup."""
        ids = sorted(
            {int(r["canvas_id"]) for r in rows if r.get("canvas_id") is not None}
        )
        names = await self.canvases.names_by_ids(ids)
        provenance = await _provenance_for(rows)
        return [
            _build_item(
                r,
                canvas_names=names,
                team_id=team_id,
                provenance=provenance.get(str(r.get("id"))),
            )
            for r in rows
        ]

    async def _promoted_resource(
        self, gen_id: int | str, scope_id: int, user_id: str
    ) -> dict[str, Any]:
        """``promote`` with its exceptions mapped onto the HTTP contract.

        An unmapped ``ValueError`` is re-raised untouched: inventing a code for
        it would turn an unknown fault into a tidy 4xx the caller cannot act on.
        """
        try:
            return await self.promote.promote(
                gen_id=int(gen_id),
                user_id=user_id,
                target_scope_id=int(scope_id),
            )
        except PermissionError as exc:
            raise AssetError(403, "not_authorised", str(exc)) from exc
        except ValueError as exc:
            message = str(exc)
            if "not found" in message:
                raise AssetError(404, "generation_not_found", message) from exc
            if "file missing" in message:
                raise AssetError(409, "file_missing", message) from exc
            raise

    @staticmethod
    def _not_in_scope() -> AssetError:
        return AssetError(
            404, "generation_not_found", "Generation not found in this scope"
        )

    # ── reads ──────────────────────────────────────────────────────────────

    async def list(
        self,
        scope_id: int,
        team_id: str,
        *,
        state: Optional[str] = None,
        origin_kinds: Optional[list[str]] = None,
        project_id: Optional[int] = None,
        canvas_id: Optional[int] = None,
        media_kind: Optional[str] = None,
        model: Optional[str] = None,
        since: Optional[datetime.datetime] = None,
        source_asset_id: Optional[int] = None,
        include_intermediate: bool = False,
        cursor: Optional[str] = None,
        limit: int = 60,
    ) -> dict[str, Any]:
        page = await self.gen_repo.list_inbox(
            scope_id,
            state=state,
            origin_kinds=origin_kinds,
            project_id=project_id,
            canvas_id=canvas_id,
            media_kind=media_kind,
            model=model,
            since=since,
            source_asset_id=source_asset_id,
            include_intermediate=include_intermediate,
            cursor=cursor,
            limit=limit,
        )
        return {
            "items": await self._decorate(page["items"], team_id),
            "next_cursor": page.get("next_cursor"),
        }

    async def get_item(self, gen_id: int | str, scope_id: int) -> dict[str, Any]:
        """One card by id, in the EXACT shape :meth:`list` ships.

        Same ``_decorate`` — therefore the same ``source``/``title`` derivation
        and the same ``GeneratedItem`` projection — because a by-id read that
        built its own body would be a second wire shape for one row, and the
        client would have to know which endpoint it came from. Pinned by
        ``tests/services/library/test_generated_inbox_service.py``.

        Refuses with the same 404 the other single-row paths use when the row is
        not in this scope. Note this reads the row through
        ``GeneratedMediaRepository.get``, which does NOT filter ``review_state``
        — a row the user deleted from the inbox is still resolvable here, as it
        already is for :meth:`save` and :meth:`delete`. Making the by-id read
        stricter than the actions available on the same row would mean "you can
        promote it but you cannot look at it".

        It applies no ``include_intermediate`` filter either, for the same
        reason and one more. ``generated_roles`` marks machine-made rows
        (``canvas_upload`` and friends) intermediate so :meth:`list` can keep
        them off the landing tab; a caller who NAMES a row has already got past
        the browsing problem that filter solves, and hiding it here would 404 a
        row whose id the client is holding. The two knobs are therefore
        deliberately absent rather than defaulted — this method answers "give
        me THAT row", and neither ``review_state`` nor intermediacy changes
        which row that is.
        """
        row = await self.gen_repo.get(int(gen_id), int(scope_id))
        if row is None:
            raise self._not_in_scope()
        return (await self._decorate([row], str(scope_id)))[0]

    async def counts(self, scope_id: int) -> dict[str, int]:
        """Tab counters. ``deleted`` is dropped — there is no deleted tab, and
        a count for a tab that does not exist invites a UI that reads it."""
        by_state = await self.gen_repo.count_by_state(int(scope_id))
        return {
            key: int(by_state.get(key, 0))
            for key in ("unreviewed", "saved", "in_assets")
        }

    # ── single-item actions ────────────────────────────────────────────────

    async def _save_core(
        self, gen_id: int | str, scope_id: int, user_id: str
    ) -> dict[str, Any]:
        """Promote and re-read. Returns the RAW repo row, undecorated.

        Split from :meth:`save` so ``batch`` can run it N times without paying
        for a card it throws away (each decoration is a ``names_by_ids``).

        The scope read comes FIRST. ``promote`` resolves the generation by id
        alone (it has its own source-scope grant check), so a gen_id that lives
        in another scope the caller belongs to used to be copied into this
        library and only THEN 404'd — a refusal that had already half-applied.
        A cheap read turns it back into a real no-op.
        """
        if await self.gen_repo.get(int(gen_id), int(scope_id)) is None:
            raise self._not_in_scope()
        await self._promoted_resource(gen_id, scope_id, user_id)
        row = await self.gen_repo.get(int(gen_id), int(scope_id))
        if row is None:
            raise self._not_in_scope()
        return row

    async def save(self, gen_id: int | str, scope_id: int, user_id: str) -> dict:
        """Promote into the resource library. State advances to ``saved``
        inside ``mark_promoted`` — never written here."""
        row = await self._save_core(gen_id, scope_id, user_id)
        return (await self._decorate([row], str(scope_id)))[0]

    async def _validate_target(self, scope_id: int, req: SaveAsAssetRequest) -> None:
        """Everything about the DESTINATION that can be known before promoting.

        Deliberately the same two checks ``attach_file`` runs (asset resolvable
        + writable in this scope, slot valid for its type) — run early rather
        than reimplemented, so the two cannot answer differently. What is NOT
        pre-checked is the resource-in-scope check: that resource does not
        exist yet, it is what ``promote`` is about to create.
        """
        if req.new_asset is not None:
            asset_type = req.new_asset.asset_type
        else:
            row = await self.assets._require_writable(int(req.asset_id), int(scope_id))
            asset_type = row["asset_type"]
        if not is_valid_slot(asset_type, req.slot):
            raise AssetError(
                422,
                "invalid_slot",
                f"Slot '{req.slot}' is not valid for {asset_type}",
            )

    async def _save_as_asset_core(
        self,
        gen_id: int | str,
        scope_id: int,
        user_id: str,
        req: SaveAsAssetRequest,
    ) -> dict[str, Any]:
        """Promote, attach to an asset slot, mark ``in_assets``. Raw row out.

        **``promote`` runs OUTSIDE the unit of work, deliberately.** Three
        reasons, each of which bit us the moment it was inside:

        1. For canvas-origin rows ``promote`` issues ``SET LOCAL ROLE
           service_role`` (promote_generated_media_service.py). ``write_scope()``
           joins an ambient UoW and ``SET LOCAL`` is TRANSACTION-scoped, so the
           escalation would outlive the promote and cover ``create_asset`` /
           ``attach_file`` / ``set_review_state`` / the COMMIT.
        2. Its internal "canvas backlink failed — non-fatal" guard stops being
           non-fatal in a shared transaction: the failed statement poisons the
           whole transaction (``InFailedSqlTransaction``), killing a save that
           was supposed to survive it — and, being no ``AssetError``, aborting a
           whole batch with it.
        3. It does storage I/O (hash, download/copy, upload). Holding a write
           transaction open across a network round-trip is how a slow object
           store becomes lock contention.

        The accepted cost is a weaker guarantee: if the attach fails, the row
        stays ``saved`` with a promoted resource behind it. That is a legitimate
        state — exactly what a plain :meth:`save` produces — and the user can
        retry (``promote`` is idempotent via its ``promoted_resource_id``
        short-circuit). What can never happen is the harmful direction:
        ``in_assets`` without an attachment, because that write is the last
        thing in the transaction the attach shares.

        That accepted cost covers a genuine ATTACH failure — a mid-flight
        conflict nothing could have foreseen. It does not extend to pure
        validation: an unknown ``asset_id``, a read-only preset or a slot the
        asset type does not have are all knowable from a cheap read, so they
        are checked BEFORE ``promote`` and refuse cleanly instead of leaving a
        promoted resource in My Uploads behind a 404/422.
        """
        await self._validate_target(scope_id, req)
        resource = await self._promoted_resource(gen_id, scope_id, user_id)
        resource_id = str(resource["id"])
        async with unit_of_work():
            return await self._attach_and_mark(
                gen_id, scope_id, user_id, req, resource_id
            )

    async def _attach_and_mark(
        self,
        gen_id: int | str,
        scope_id: int,
        user_id: str,
        req: SaveAsAssetRequest,
        resource_id: str,
    ) -> dict[str, Any]:
        """The TRANSACTIONAL segment of save-as-asset. The caller owns the
        transaction — this method opens none.

        Extracted so ``POST /resources/{id}/save-as-asset`` can run it in a
        transaction that ALSO contains the mint of the inbox row (see
        :meth:`save_resource_as_asset`), instead of a second copy that could
        drift from this one. ``unit_of_work()`` is REQUIRES_NEW here — nesting
        it would open a separate transaction that commits independently, which
        is exactly the rollback guarantee the resource path is buying — so the
        boundary has to be the caller's, not this method's.

        ``set_review_state`` is last on purpose: ``in_assets`` without an
        attachment is the one direction that must never be reachable.
        """
        if req.new_asset is not None:
            created = await self.assets.create_asset(
                int(scope_id),
                AssetCreate(
                    asset_type=req.new_asset.asset_type,
                    name=req.new_asset.name,
                    source="generated",
                ),
                user_id,
            )
            asset_id = str(created["id"])
        else:
            asset_id = str(req.asset_id)
        await self.assets.attach_file(
            int(asset_id),
            int(scope_id),
            AttachFileRequest(
                resource_id=resource_id,
                slot=req.slot,
                loadout_id=req.loadout_id,
            ),
            user_id,
        )
        updated = await self.gen_repo.set_review_state(
            int(gen_id), int(scope_id), "in_assets"
        )
        if updated is None:
            raise self._not_in_scope()
        return {"row": updated, "asset_id": asset_id, "resource_id": resource_id}

    async def save_as_asset(
        self,
        gen_id: int | str,
        scope_id: int,
        user_id: str,
        req: SaveAsAssetRequest,
    ) -> dict:
        """:meth:`_save_as_asset_core` plus the card. See the core's docstring
        for the transaction boundary and the guarantee it buys."""
        out = await self._save_as_asset_core(gen_id, scope_id, user_id, req)
        return {
            "generation": (await self._decorate([out["row"]], str(scope_id)))[0],
            "asset_id": out["asset_id"],
            "resource_id": out["resource_id"],
        }

    # ── save a LIBRARY RESOURCE as an asset (ruling E) ─────────────────────

    async def _require_library_resource(
        self, resource_id: int | str, user_id: str
    ) -> dict[str, Any]:
        """The resource row, or a typed refusal. Never ``None`` to the caller.

        ``get_resource_by_id_for_caller`` answers ``None`` for BOTH "no such
        row" and "exists but not visible to you", and opens its own scope when
        ``SCOPE_ENFORCE_RESOURCES`` is on — so this call site needs no ambient
        request scope and leaks no existence. One code for both is deliberate:
        "not yours" and "not there" are the same answer to a caller who may
        not learn which.
        """
        row = await self.resources.get_resource_by_id_for_caller(
            str(resource_id), str(user_id)
        )
        if not row:
            raise AssetError(
                404,
                "resource_not_accessible",
                "Resource not found or not accessible",
            )
        return row

    async def _mint_args_for_resource(
        self, resource: dict[str, Any], scope_id: int, user_id: str
    ) -> dict[str, Any]:
        """Everything ``insert_registered_resource`` needs, or a typed refusal.

        Two refusals, both knowable before any write:

        * ``resource_kind_unsupported`` — the slot table has exactly two file
          shapes: every non-``audio`` type's slots take a visual reference
          (``sheet`` / ``establishing`` / ``turnaround`` / ``flat`` …) and
          ``audio``'s ``primary`` / ``variants`` take an audio file. A video or
          a document has no slot to land in, so it refuses here rather than
          becoming an attachment that renders as a broken card. The detail
          names the accepted kinds — a refusal the user cannot act on is half
          a refusal.
        * ``resource_file_unresolved`` — the PR-B ladder
          (``resources.file_path`` → ``parsed_media.download_path``) resolved
          no SINGLE file. That covers a row whose bytes were never downloaded
          AND an image album, whose path is a directory prefix: handing a
          directory downstream is worse than refusing, not better.

          NOT ``materialize_failed``, which this endpoint originally borrowed:
          ``workflows/canvas_generation`` already owns that vocabulary and
          draws the line the other way — ``materialize_failed`` there means
          "the bytes exist and READING them failed", which the frontend
          renders as "读取失败". Nothing is materialized on this path (no
          bytes are copied), and "never downloaded" is the opposite diagnosis
          from "read failed"; reusing the code would have told the user to
          retry something that cannot succeed.
        """
        mime = str(resource.get("mime_type") or "").lower()
        media_kind = next(
            (k for k in ACCEPTED_ASSET_FILE_KINDS if mime.startswith(f"{k}/")), None
        )
        if media_kind is None:
            raise AssetError(
                422,
                "resource_kind_unsupported",
                "Only "
                + " and ".join(ACCEPTED_ASSET_FILE_KINDS)
                + " resources can be saved as an asset file",
            )
        file_path = await resolve_resource_file_path(resource)
        if not file_path:
            raise AssetError(
                422,
                "resource_file_unresolved",
                "Resource has no single local file to attach",
            )
        return {
            "scope_id": int(scope_id),
            # The row describes who OWNS the file, not who clicked "As Asset"
            # — same rule ``backfill_generated_inbox`` states for its own
            # ``creator_id``. Falls back to the caller only for a legacy row
            # with no creator.
            "creator_id": str(resource.get("creator_id") or user_id),
            "resource_id": int(resource["id"]),
            "file_path": str(file_path),
            "mime": mime or None,
            "media_kind": media_kind,
            "conversation_id": None,
            "origin_kind": LIBRARY_UPLOAD_ORIGIN,
            # What the card is titled after. Without it ``derive_title`` has
            # nothing to work with and the row reads "audio · library_upload",
            # naming neither the file nor anything the user wrote.
            "params": (
                {SOURCE_FILENAME_KEY: stem}
                if (stem := _title_source_from_filename(resource.get("filename")))
                else {}
            ),
        }

    async def save_resource_as_asset(
        self,
        resource_id: int | str,
        scope_id: int,
        user_id: str,
        req: SaveAsAssetRequest,
    ) -> dict:
        """Save a LIBRARY resource as an asset — find-or-mint + attach, ONE
        transaction (ruling E).

        A My Uploads file is not a generation, so there may be no inbox row to
        save. Step ① reuses the one that exists (``promoted_resource_id`` is
        the key — a resource has at most one inbox row, whoever wrote it);
        step ② mints one when it does not, registering the EXISTING resource
        (no blob copy: the row carries the resource's own ``file_path`` and
        points ``promoted_resource_id`` at it); step ③ is the same
        :meth:`_attach_and_mark` the generation path runs.

        **Why one transaction, unlike :meth:`_save_as_asset_core`.** That path
        keeps ``promote`` outside the unit of work because it escalates roles,
        does storage I/O and carries a non-fatal backlink guard. None of the
        three applies here: nothing is promoted, because the resource already
        exists — the mint is a SELECT and an INSERT on ``generated_media`` and
        nothing else. So the mint joins the attach's transaction, and a failure
        at ③ (a slot conflict, a mid-flight permission change) leaves NO minted
        row behind. That is the whole point: a user who cancels or whose save
        fails must not find an orphan card in the Generated inbox.

        Idempotent. A second call for the same resource hits ① and re-attaches
        the same row; ``attach`` itself is the upsert that makes the repeat
        harmless.

        Validation runs BEFORE the transaction so an unknown asset, a read-only
        preset, a bad slot, a resource of a kind no slot accepts or a resource
        with no single file all refuse cleanly with nothing written.

        A pre-existing inbox row that lives in ANOTHER scope surfaces as the
        usual typed ``generation_not_found`` from ``set_review_state`` — inside
        the transaction, so the attach rolls back with it rather than half
        applying.
        """
        resource = await self._require_library_resource(resource_id, user_id)
        mint_args = await self._mint_args_for_resource(resource, scope_id, user_id)
        await self._validate_target(scope_id, req)
        async with unit_of_work():
            # Serialise same-resource mints. ``insert_registered_resource`` is
            # idempotent by a SELECT, not by a DB constraint — there is no
            # unique index on ``promoted_resource_id`` — and unlike its other
            # two writers (a one-shot upload, an admin backfill) THIS one is
            # user-triggered and repeatable: a double-submitted dialog can put
            # two requests in flight for the same resource, both read "no row"
            # under READ COMMITTED, and both insert. The second row would then
            # be unreachable forever (the lookup is ``id asc limit 1``) — a
            # permanent orphan ``saved`` card, which is exactly the harm this
            # transaction exists to prevent, entering by another door.
            # Transaction-level, so the winner's COMMIT releases it and the
            # loser re-reads and finds the winner's row.
            await self.gen_repo.lock_resource_registration(int(resource["id"]))
            gen = await self.gen_repo.insert_registered_resource(**mint_args)
            out = await self._attach_and_mark(
                gen["id"], scope_id, user_id, req, str(resource["id"])
            )
        return {
            "generation": (await self._decorate([out["row"]], str(scope_id)))[0],
            "asset_id": out["asset_id"],
            "resource_id": out["resource_id"],
            "generated_id": str(gen["id"]),
        }

    async def delete(self, gen_id: int | str, scope_id: int) -> None:
        """Hard delete (the repo also drops the backing object when nothing
        else references it). A no-op delete is a 404, not a silent success."""
        if not await self.gen_repo.delete(int(gen_id), int(scope_id)):
            raise AssetError(404, "generation_not_found", "Generation not found")

    # ── bulk ───────────────────────────────────────────────────────────────

    async def batch(
        self, req: BatchRequest, scope_id: int, user_id: str
    ) -> dict[str, Any]:
        """Run one action over many ids, reporting each id's outcome.

        NOT atomic across items — each id is its own operation (``save_as_asset``
        keeps its own per-item transaction). Every id is attempted: a failure
        never stops the ones behind it, because "12 of 20 saved" has to reach
        the caller. Only :class:`AssetError` lands in ``failed``; an unexpected
        exception aborts the batch, since dressing a bug up as a per-item code
        would hide it.

        Runs the UNDECORATED cores: only ids are reported, so building a card
        per item — a ``names_by_ids`` round-trip each — would be work thrown
        away, up to 200 times over.
        """
        ok: list[str] = []
        failed: list[dict[str, str]] = []
        for raw_id in req.ids:
            gen_id = str(raw_id)
            try:
                if req.action == "save":
                    await self._save_core(gen_id, scope_id, user_id)
                elif req.action == "save_as_asset":
                    await self._save_as_asset_core(
                        gen_id, scope_id, user_id, req.save_as_asset
                    )
                else:
                    await self.delete(gen_id, scope_id)
            except AssetError as exc:
                failed.append({"id": gen_id, "code": exc.code, "detail": exc.detail})
            else:
                ok.append(gen_id)
        return BatchResult.model_validate({"ok": ok, "failed": failed}).model_dump()

    async def cleanup(self, req: CleanupRequest, scope_id: int) -> dict[str, Any]:
        """Purge old unreviewed generations. Dry run by default (the schema's
        default), so a caller that forgets the flag gets a preview.

        ``count`` is what this pass matched (capped at ``CLEANUP_SCAN_LIMIT``),
        and ``deleted`` counts rows the delete actually removed — the two are
        reported separately rather than assumed equal, so a row that vanished
        underneath us is visible instead of inflating the total. ``truncated``
        says the cap was reached, so the caller can tell "that was all of them"
        apart from "that was the first ``CLEANUP_SCAN_LIMIT``".
        """
        older_than = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=int(req.older_than_days)
        )
        rows = await self.gen_repo.list_older_unreviewed(
            int(scope_id), older_than, limit=CLEANUP_SCAN_LIMIT
        )
        # A full page means the scan hit its cap, so there may be more behind
        # it. ``>=`` rather than ``==``: a repo that ever over-returns should
        # still flag the pass as partial, not silently claim it saw everything.
        truncated = len(rows) >= CLEANUP_SCAN_LIMIT
        if req.dry_run:
            sample = await self._decorate(rows[:_CLEANUP_SAMPLE], str(scope_id))
            return CleanupResponse(
                dry_run=True,
                count=len(rows),
                sample=sample,
                deleted=0,
                truncated=truncated,
            ).model_dump()
        deleted = 0
        for row in rows:
            if await self.gen_repo.delete(int(row["id"]), int(scope_id)):
                deleted += 1
        return CleanupResponse(
            dry_run=False,
            count=len(rows),
            sample=[],
            deleted=deleted,
            truncated=truncated,
        ).model_dump()
