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

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional, Tuple

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
    from app.services.ai.provider_protocols import resolve_generation_protocol

    catalog = await _enabled_rows("image")
    byok = await byok_image_rows(user_id)
    visible = _visible_rows([*catalog, *byok], name, user_id, "image")
    explicit = _explicit_match(visible, name) if name else None
    # Upscale-only rows (``text_to_image = False``) never win a DEFAULT pick —
    # they need an input image a prompt request does not have. Named
    # explicitly they stay in, so the refusal below names the row instead of
    # silently substituting some other model.
    image_rows = [r for r in visible if r is explicit or _is_text_to_image(r)]
    if not image_rows:
        raise RuntimeError(
            "no image model configured (nous_models catalog or user " "BYOK providers)"
        )

    row = _pick_row(image_rows, name)
    actual_provider = (row.get("actual_provider") or "").lower()

    protocol = resolve_generation_protocol(actual_provider)
    if protocol is None:
        raise RuntimeError(
            f"No image provider implementation for actual_provider="
            f"{actual_provider!r} (catalog row name={row.get('name')!r})"
        )
    if not getattr(protocol, "text_to_image", True):
        raise RuntimeError(
            f"image model {row.get('name')!r} is an upscale-only service "
            f"(actual_provider={actual_provider!r}); it cannot generate from a "
            "prompt"
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


def _is_text_to_image(row: dict) -> bool:
    """Whether ``row`` may serve a prompt → picture request.

    A row whose protocol does not resolve counts as text-to-image here on
    purpose: ``resolve_image_provider`` then raises its "no implementation"
    error naming the row, which is the existing, louder behaviour."""
    from app.services.ai.provider_protocols import resolve_generation_protocol

    protocol = resolve_generation_protocol((row.get("actual_provider") or "").lower())
    return protocol is None or getattr(protocol, "text_to_image", True)


# Upscale preference: the self-hosted engine first (our GPU, no per-call
# vendor quota), dreamina as the fallback. Families not listed sort last and
# only appear here if they declare ``upscale_capable``.
_UPSCALE_FAMILY_ORDER: tuple[str, ...] = ("nous", "jimeng-cli")


async def resolve_upscale_provider(*, user_id: Optional[str]) -> Tuple[Any, str, str]:
    """Pick the canvas 放大 backend: ``(provider, actual_model, row_name)``.

    Candidates are the enabled image rows visible to ``user_id`` (catalog +
    BYOK, the same owner scoping as ``resolve_image_provider``) whose protocol
    declares ``upscale_capable``. nous-engine rows win over jimeng-cli rows;
    within a family the catalog ``sort_order`` decides.

    Deliberately NOT a fallback chain at call time: if the nous row is picked
    and the engine refuses (key not authorised, busy), the route surfaces that
    error rather than quietly re-running on dreamina — a silent second vendor
    would hide the misconfiguration and bill a different account.

    Raises:
        RuntimeError: no upscale-capable image model is enabled.
    """
    from app.services.ai.provider_protocols import resolve_generation_protocol

    catalog = await _enabled_rows("image")
    byok = await byok_image_rows(user_id)
    candidates: list[tuple[int, int, dict, Any]] = []
    for index, row in enumerate(
        _visible_rows([*catalog, *byok], None, user_id, "image")
    ):
        protocol = resolve_generation_protocol(
            (row.get("actual_provider") or "").lower()
        )
        if protocol is None or not getattr(protocol, "upscale_capable", False):
            continue
        family = protocol.generation_family or ""
        rank = (
            _UPSCALE_FAMILY_ORDER.index(family)
            if family in _UPSCALE_FAMILY_ORDER
            else len(_UPSCALE_FAMILY_ORDER)
        )
        candidates.append((rank, index, row, protocol))
    if not candidates:
        raise RuntimeError(
            "no upscale-capable image model enabled (need an enabled nous or "
            "jimeng-cli image row in Admin > AI Models)"
        )
    _, _, row, protocol = min(candidates, key=lambda c: (c[0], c[1]))
    provider, actual_model = protocol.build_upscale_provider(row)
    actual_provider = (row.get("actual_provider") or "").lower()
    _stamp_provider_key(
        provider, actual_provider, source=str(row.get("source") or "catalog")
    )
    row_name = str(row.get("name") or "")
    logger.info(
        "Resolved upscale provider: {} (row={}, model={})",
        actual_provider,
        row_name,
        actual_model,
    )
    return provider, actual_model, row_name


async def _pick_video_row(name: Optional[str], user_id: Optional[str]) -> dict:
    """The ONE video-row pick: owner scoping, then ``_pick_row``.

    Shared by ``resolve_video_provider`` (server-only) and
    ``resolve_video_route`` (either machine) so the two can never disagree on
    which row a request lands on.
    """
    video_rows = _visible_rows(await _enabled_rows("video"), name, user_id, "video")
    if not video_rows:
        raise RuntimeError("no video model configured in nous_models catalog")
    return _pick_row(video_rows, name)


def _build_server_video_provider(row: dict) -> Tuple[JimengCliProvider, str]:
    """Build (and stamp) the server-side provider for an already-picked row.

    Only the ``jimeng-cli`` family has a server-side video build. A local row
    (``jimeng-local``) is refused here on purpose: building a server-side
    JimengCliProvider for it would run the user's local model on nous' own
    OAuth session (see ``JimengLocalProtocol``).
    """
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


async def resolve_video_provider(
    name: Optional[str] = None,
    *,
    user_id: Optional[str] = None,
) -> Tuple[JimengCliProvider, str]:
    """Resolve a SERVER-SIDE video provider from the ``nous_models`` catalog.

    Returns ``(provider, actual_model)``. Only the jimeng-cli CLI has a wired
    server-side video path; callers use ``provider.generate_video(...)``
    directly. Owner-scoped rows resolve only for their owner (see
    ``resolve_image_provider``). Callers that can also run on the user's own
    machine use ``resolve_video_route`` instead.

    Raises:
        RuntimeError: no enabled video model, the requested row is private to
            another user, or the resolved row's ``actual_provider`` has no
            wired server-side video implementation (a local row included).
    """
    return _build_server_video_provider(await _pick_video_row(name, user_id))


@dataclass(frozen=True)
class LocalVideoRoute:
    """The picked row runs on the user's OWN machine via the paired daemon."""

    engine: str
    engine_model: str
    row_name: str


@dataclass(frozen=True)
class ServerVideoRoute:
    """The picked row runs on our servers; ``provider`` is already built."""

    provider: Any
    actual_model: str
    row_name: str


VideoRoute = LocalVideoRoute | ServerVideoRoute


async def resolve_video_route(
    name: Optional[str] = None,
    *,
    user_id: Optional[str] = None,
) -> VideoRoute:
    """Pick the video row ONCE, then branch on whose machine runs it.

    ``name`` may be empty: most video callers (shot video, timeline, the agent
    tool) name no model, so the local question has to be asked of the row the
    default pick lands on - asking it only of an explicit name is how a default
    pick onto a ``jimeng-local`` row used to raise inside
    ``resolve_video_provider``.

    Default pick (unchanged, pinned by ``tests/test_video_route_resolution``):
    an explicit name match; else the first row, in catalog ``sort_order``, of
    EITHER Dreamina family (``jimeng-cli`` / ``jimeng-local``); else the first
    row. So with both a local and a server video row visible, whichever sorts
    first wins.

    Local rows map through ``local_dispatch.LOCAL_ENGINES``; only the
    ``dreamina`` engine has a video runner on the daemon, so any other local
    engine is refused here rather than after a dispatch.
    """
    from app.services.generation.local_dispatch import LOCAL_ENGINES

    row = await _pick_video_row(name, user_id)
    actual_provider = (row.get("actual_provider") or "").lower()
    row_name = str(row.get("name") or "")
    engine = LOCAL_ENGINES.get(actual_provider)
    if engine is not None:
        if engine != "dreamina":
            raise RuntimeError(
                f"No video provider implementation for actual_provider="
                f"{actual_provider!r} (catalog row name={row_name!r}): the "
                f"local {engine!r} engine has no video runner"
            )
        logger.info("Resolved video route: local daemon {} (row={})", engine, row_name)
        return LocalVideoRoute(
            engine=engine,
            engine_model=str(row.get("actual_model") or ""),
            row_name=row_name,
        )
    provider, actual_model = _build_server_video_provider(row)
    return ServerVideoRoute(
        provider=provider, actual_model=actual_model, row_name=row_name
    )
