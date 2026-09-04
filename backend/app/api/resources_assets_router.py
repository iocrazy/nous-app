"""My Uploads → Assets — ``POST /resources/{id}/save-as-asset`` (P6 ruling E).

The Generated inbox already has ``POST /generated/{id}/save-as-asset``, but its
path parameter is a ``generated_media`` row and a library resource is not one:
only chat uploads and promoted generations ever get an inbox row, so a plain
upload in My Uploads has nothing for that endpoint to take. Answering that with
"no inbox row" would fold a supported action into "there is nothing here" —
the failure mode this repo keeps re-learning.

So this route takes the RESOURCE id and the service finds-or-mints the one
inbox row that registers it, inside the same transaction as the attach. See
``GeneratedInboxService.save_resource_as_asset`` for why one transaction is
both possible and necessary here.

**Same contract as the generation route on purpose.** Request body is literally
``SaveAsAssetRequest``; the response is the same three keys (``generation`` /
``asset_id`` / ``resource_id``) plus ``generated_id``, so
``SaveAsAssetDialog`` needs no second result shape. The gate, envelopes and id
validators are IMPORTED from :mod:`app.api.assets_router` — the same reason
:mod:`app.api.generated_router` imports them: two routers must not answer the
same failure in two shapes.
"""

from __future__ import annotations

from typing import Any, Dict, Union

from fastapi import APIRouter

from app.api.assets_router import (
    IdPath,
    ScopeIdQuery,
    _err,
    _gate,
    _ok,
)
from app.core.deps import AuthDep
from app.schemas.assets import ErrorEnvelope
from app.schemas.generated import GeneratedItem, SaveAsAssetRequest
from app.services.assets.assets_service import AssetError
from app.services.library.generated_inbox_service import GeneratedInboxService

router = APIRouter(prefix="/resources", tags=["resources", "assets"])

# Every refusal this route can produce, declared so the OpenAPI contract says
# so too. 403 = not a member of the target scope (``_gate``) or the asset is a
# read-only preset; 404 = resource not accessible / asset not found; 409 = a
# mid-flight conflict from the attach; 422 = a non-image resource, a resource
# with no single file, or a slot the asset type does not have.
_ERRORS: Dict[Union[int, str], Dict[str, Any]] = {
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope},
}


def _service() -> GeneratedInboxService:
    return GeneratedInboxService()


@router.post("/{resource_id}/save-as-asset", status_code=201, responses=_ERRORS)
async def save_resource_as_asset(
    resource_id: IdPath,
    payload: SaveAsAssetRequest,
    auth: AuthDep,
    scope_id: ScopeIdQuery,
):
    """Attach a My Uploads resource to an asset slot, minting its inbox row.

    TWO independent authorisations, and both are load-bearing:

    * ``_gate`` — membership of ``scope_id``, the team the asset lives in.
    * the resource read inside the service — owner or teammate. A caller who
      belongs to the target scope still cannot reach a resource that is not
      theirs, and a caller who owns the resource still cannot write it into a
      team they do not belong to.

    Failures are typed (``resource_not_accessible`` / ``resource_not_image`` /
    ``materialize_failed`` / the asset codes ``attach_file`` already emits), so
    the context menu can say WHY rather than fall silent.

    **Images only, and that is narrower than ``/generated/{id}/save-as-asset``
    on purpose.** The generation route imposes no media-kind rule because the
    inbox only ever holds what a model produced for a slot. My Uploads holds
    everything a user ever dragged in, and every FILE slot in the slot table is
    a visual reference (``sheet`` / ``establishing`` / ``turnaround`` /
    ``flat``); a PDF or a screen recording attached to one is a broken card
    rather than a saved asset. The one type this rules out that has a real slot
    is ``audio`` (``primary`` / ``variants``) — widening to ``audio/*`` is a
    one-line change here plus a ``media_kind`` in
    ``GeneratedInboxService._mint_args_for_resource``, and is deliberately NOT
    done blind: audio assets have no UI entry point in My Uploads yet.
    """
    try:
        sid = await _gate(scope_id, auth)
        out = await _service().save_resource_as_asset(
            resource_id, sid, auth.user_id, payload
        )
    except AssetError as e:
        return _err(e)
    return _ok(
        {
            "generation": GeneratedItem.model_validate(out["generation"]).model_dump(
                mode="json"
            ),
            "asset_id": out["asset_id"],
            "resource_id": out["resource_id"],
            "generated_id": out["generated_id"],
        }
    )
