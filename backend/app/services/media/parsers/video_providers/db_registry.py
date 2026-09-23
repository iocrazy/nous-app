"""DB-catalog image/video-provider resolution.

House rule (config env→DB): image/video-provider credentials live in the
``nous_models`` catalog, NOT env. The in-process ``provider_registry`` ships
EMPTY for images, so generation resolves its provider dynamically from the DB
here, dispatching on the row's ``actual_provider``:

  - ``jimeng-cli`` → the subprocess-driven dreamina CLI (no api_key — the CLI's
    OAuth session is the credential). Preferred when multiple rows are enabled
    (user decision 2026-07-07: CLI is the primary; Ark rows stay enabled as a
    lower-priority fallback).
  - ``doubao`` / ``ark`` → the OpenAI-compatible Ark image endpoint.

Row SELECTION lives here (``_enabled_rows`` / ``_pick_row``); CONSTRUCTION of
the actual provider/adapter is delegated to the matching provider protocol
(``resolve_generation_protocol(actual_provider).build_image_provider(row)`` /
``.build_video_provider(row)`` — see ``app.services.ai.provider_protocols``).
Image callers use the ``BaseImageProvider.generate`` contract, so the CLI
provider is wrapped in ``_JimengImageAdapter`` (returns an ``ImageGenResult``
with ``image_path`` set — a local file, not a URL; the adapter class now lives
in ``provider_protocols.jimeng``, next to the protocol that builds it). Video
callers use ``JimengCliProvider.generate_video`` directly (only the CLI has a
wired video path today), so ``resolve_video_provider`` returns the provider
as-is.

BYOK tier (images only)
-----------------------
``resolve_image_provider`` resolves over TWO tiers: the admin catalog above,
plus the calling user's own Settings → AI providers projected into rows of the
same shape by ``byok_rows.byok_image_rows`` (house rule: the providers page is
the only model-management entry, and agents consume whatever it makes
available). BYOK rows carry the canonical PROTOCOL key in ``actual_provider``
(``"ark"``, never the settings key ``"doubao"``) so dispatch below and
``ai_model_prices`` lookups — keyed ``(model_id, "ark")`` — both land; they are
owner-stamped, so ``_visible_to`` admits them for their own user only; and they
sort after every catalog row, so a call that names no model resolves exactly as
it did before the tier existed. CLI/daemon families (codex, codex-local,
jimeng-cli, openai-images) are excluded from that tier on purpose — their
credential is a local session or a paired device, not an api key. Video
resolution is catalog-only and unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Tuple

from loguru import logger

from app.core.catalog_names import catalog_name_alias, find_row_by_catalog_name
from app.services.media.parsers.video_providers.byok_rows import byok_image_rows

if TYPE_CHECKING:
    from app.services.media.parsers.video_providers.base import BaseImageProvider
    from app.services.media.parsers.video_providers.jimeng_cli import (
        JimengCliProvider,
    )


# The Dreamina families. Two of them because dispatch must never confuse a row
# that runs on our servers with one that runs on the user's device — see
# JimengLocalProtocol — but that split is about HOW the row is executed, and
# the default-provider preference below is about WHICH PRODUCT to reach for.
# Matching only "jimeng-cli" made the preference silently stop applying the
# moment Dreamina moved to the daemon: nothing raised, the lookup just found
# nothing and fell through to sort_order.
_JIMENG_FAMILIES = frozenset({"jimeng-cli", "jimeng-local"})


def _pick_row(rows: list[dict], name: Optional[str], jimeng_first: bool = True) -> dict:
    """Choose a catalog row: an explicit name match wins; else the preferred
    Dreamina row (either family); else the first row (already ordered by
    sort_order).

    The name match is ``_explicit_match`` — the SAME matcher ``_visible_rows``
    uses to decide whether a requested row was withheld. Two matchers would let
    a name pass the visibility check and then select something else.
    """
    if name:
        match = _explicit_match(rows, name)
        if match is not None:
            return match
    if jimeng_first:
        from app.services.ai.provider_protocols import resolve_generation_protocol

        jimeng = next(
            (
                r
                for r in rows
                if (
                    protocol := resolve_generation_protocol(
                        r.get("actual_provider") or ""
                    )
                )
                is not None
                and protocol.generation_family in _JIMENG_FAMILIES
            ),
            None,
        )
        if jimeng is not None:
            return jimeng
    return rows[0]


async def _enabled_rows(media_type: str) -> list[dict]:
    from app.repositories.nous_model_repository import (
        get_nous_model_repository,
    )

    repo = get_nous_model_repository()
    # list_all reveals api_key on every full row and is ordered by sort_order;
    # list_enabled returns public columns only (no api_key/base_url).
    rows = await repo.list_all()
    return [r for r in rows if r.get("type") == media_type and r.get("is_enabled")]


def _visible_to(row: dict, user_id: Optional[str]) -> bool:
    """Owner scoping (migration 431): a NULL owner is platform-wide; a set
    owner admits only that user. No user in scope → fail-closed."""
    owner = row.get("owner_user_id")
    if not owner:
        return True
    return user_id is not None and str(owner) == str(user_id)


def _explicit_match(rows: list[dict], name: str) -> Optional[dict]:
    """The row a caller asked for by ``name``.

    ``byok_provider`` is matched too, so ``provider="doubao"`` — the provider
    CARD's key, which is what an agent naturally passes and which no catalog
    row is ever named — selects that user's first enabled image model on that
    card. Catalog rows have no such field, so this widens nothing for them.

    Only when nothing matches exactly does the ``mediahub-`` ↔ ``nous-``
    rename alias of ``name`` get a try, against ``name`` alone
    (``app.core.catalog_names``) — an exact hit on any field always wins.
    """
    exact = next(
        (
            r
            for r in rows
            if r.get("name") == name
            or r.get("actual_model") == name
            or r.get("byok_provider") == name
        ),
        None,
    )
    if exact is not None:
        return exact
    alias = catalog_name_alias(name)
    return find_row_by_catalog_name(rows, alias) if alias else None


def _visible_rows(
    rows: list[dict], name: Optional[str], user_id: Optional[str], kind: str
) -> list[dict]:
    """Apply owner scoping; an explicitly-requested private row must RAISE for
    a non-owner, never silently fall back to some other public row."""
    visible = [r for r in rows if _visible_to(r, user_id)]
    if (
        name
        and _explicit_match(visible, name) is None
        and _explicit_match(rows, name) is not None
    ):
        raise RuntimeError(
            f"{kind} model {name!r} is private to another user "
            "(nous_models.owner_user_id)"
        )
    return visible


def _stamp_provider_key(
    provider: object, actual_provider: str, *, source: str = "catalog"
) -> None:
    """Record which catalog row's ``actual_provider`` built this provider,
    and WHOSE credentials it runs on.

    A built provider does NOT otherwise remember its own catalog key — the
    protocol constructs a bare adapter (``ArkImageProvider``,
    ``_CodexImageAdapter``, ...) and drops the row on the floor. Callers that
    need the provider's declared capabilities (``canvas_generation``'s
    ``_capabilities_for``) would then have nothing to resolve the protocol
    back from, silently get ``ProviderCapabilities.none()``, and drop every
    knob the user asked for. This function is the choke point: the one place
    that both builds the provider and still holds the row.

    ``source`` 是目录行的**层**（``"catalog"`` / ``"byok"``）。BYOK 行的
    ``actual_provider`` **刻意**用协议名（如 ``ark``，与平台目录同键），所以
    ``media_price_cents()`` 照样命中平台价 —— 这正是「BYOK 出的图被按平台价扣分」
    的确切机理。价钱该算（血缘要真），但积分不该收，所以判据必须跟着 provider
    对象走到登记口（用户裁定 2）。

    ⚠️ ``source`` 有默认值，所以漏改一个调用点**不会报错** —— 那条链的
    ``is_byok`` 恒 False，静默按平台价收。加新的 resolve 路径时必须显式传它。
    """
    provider.provider_key = actual_provider  # type: ignore[attr-defined]
    provider.is_byok = source == "byok"  # type: ignore[attr-defined]


async def resolve_image_provider(
    name: Optional[str] = None,
    *,
    user_id: Optional[str] = None,
) -> Tuple[BaseImageProvider, str]:
    """Resolve an image provider from the ``nous_models`` catalog or from
    the calling user's own BYOK providers.

    Returns ``(provider, actual_model)``. Dispatches on ``actual_provider``;
    when several image rows are enabled and no explicit ``name`` matches, the
    jimeng-cli row is preferred (CLI is the primary generator). Owner-scoped
    rows — every BYOK row included — resolve only for their owner
    (``user_id``); call paths that don't thread a user fail closed, and the
    BYOK tier then contributes nothing without reading any settings.

    Priority: an explicit ``name`` match (catalog rows first, then BYOK, in
    list order) → the jimeng-cli row → the first row. Catalog rows precede
    BYOK rows, so unspecified calls resolve exactly as they did before the
    BYOK tier existed.

    Raises:
        RuntimeError: no enabled image model in either tier, the requested row
            is private to another user, or the resolved row's
            ``actual_provider`` has no wired implementation.
    """
    catalog = await _enabled_rows("image")
    byok = await byok_image_rows(user_id)
    image_rows = _visible_rows([*catalog, *byok], name, user_id, "image")
    if not image_rows:
        raise RuntimeError(
            "no image model configured (nous_models catalog or user " "BYOK providers)"
        )

    row = _pick_row(image_rows, name)
    actual_provider = (row.get("actual_provider") or "").lower()

    from app.services.ai.provider_protocols import resolve_generation_protocol

    protocol = resolve_generation_protocol(actual_provider)
    if protocol is None:
        raise RuntimeError(
            f"No image provider implementation for actual_provider="
            f"{actual_provider!r} (catalog row name={row.get('name')!r})"
        )
    provider, actual_model = protocol.build_image_provider(row)
    _stamp_provider_key(
        provider, actual_provider, source=str(row.get("source") or "catalog")
    )
    # Names the TIER, not just the family: "catalog" on a BYOK row would make
    # every log read as if the admin had enabled the model, and the two tiers
    # bill and fail for different reasons.
    logger.info(
        "Resolved image provider from {}: {} (model={})",
        row.get("source") or "catalog",
        actual_provider,
        actual_model,
    )
    return provider, actual_model


async def resolve_video_provider(
    name: Optional[str] = None,
    *,
    user_id: Optional[str] = None,
) -> Tuple[JimengCliProvider, str]:
    """Resolve a video provider from the ``nous_models`` catalog.

    Returns ``(provider, actual_model)``. Only the jimeng-cli CLI has a wired
    video path today; callers use ``provider.generate_video(...)`` directly.
    Owner-scoped rows resolve only for their owner (see
    ``resolve_image_provider``).

    Raises:
        RuntimeError: no enabled video model, the requested row is private to
            another user, or the resolved row's ``actual_provider`` has no
            wired video implementation.
    """
    video_rows = _visible_rows(await _enabled_rows("video"), name, user_id, "video")
    if not video_rows:
        raise RuntimeError("no video model configured in nous_models catalog")

    row = _pick_row(video_rows, name)
    actual_provider = (row.get("actual_provider") or "").lower()

    from app.services.ai.provider_protocols import resolve_generation_protocol

    protocol = resolve_generation_protocol(actual_provider)
    if protocol is None or protocol.generation_family != "jimeng-cli":
        raise RuntimeError(
            f"No video provider implementation for actual_provider="
            f"{actual_provider!r} (catalog row name={row.get('name')!r})"
        )
    provider, actual_model = protocol.build_video_provider(row)
    # 视频侧今天只有平台目录（``_enabled_rows("video")``，没有 BYOK 层），所以
    # 这里恒是 "catalog"。仍然显式传行上的值：BYOK 视频行一旦出现，这条链就已经
    # 说得出层，而不是等着谁想起来补一个默认值。
    _stamp_provider_key(
        provider, actual_provider, source=str(row.get("source") or "catalog")
    )
    logger.info(
        "Resolved video provider from catalog: jimeng-cli (model={})", actual_model
    )
    return provider, actual_model
