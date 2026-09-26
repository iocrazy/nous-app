"""Shot-index automation policy (spec 2026-09-26 §3).

Four admin governance keys decide whether ``index_shots`` runs without a
click — for a fresh download (``auto_index``) and for the backlog
(``backfill``) — plus the sweeper's own bookkeeping row. Reading is
tolerant (an unreadable or malformed value is the default); writing goes
through :func:`validate_policy_update`, the same rules the generic
``PATCH /admin/settings/{key}`` path applies (``settings_validation``).

"Local provider" = the catalog row the VISUAL layer embeds with has
``actual_provider == NOUS_ENGINE_PROVIDER`` (nous-engine, no token cost).
``local_only`` on a network provider (doubao) is therefore ``off`` — the
panel says so instead of leaving the admin to wonder why nothing runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from loguru import logger

from app.repositories.admin.system_settings_repository import (
    get_system_settings_repository,
)
from app.services.library.embedding_spaces import (
    ActiveSpaceUnknown,
    VisualSpaceError,
    resolve_visual_space_and_embedder,
    visual_catalog_name,
)

AUTO_INDEX_SETTING = "ai_module.shots.auto_index"
BACKFILL_SETTING = "ai_module.shots.backfill"
BACKFILL_BATCH_SETTING = "ai_module.shots.backfill_batch"
BACKFILL_DAILY_CAP_SETTING = "ai_module.shots.backfill_daily_cap"
#: The sweeper's state: ``{day, dispatched_today, last_tick, last_error,
#: last_skip}`` — read by the Vectors panel, written only by the sweeper.
BACKFILL_STATE_SETTING = "ai_module.shots.backfill_state"

PolicyMode = Literal["off", "local_only", "always"]
POLICY_MODES: tuple[str, ...] = ("off", "local_only", "always")

DEFAULT_AUTO_INDEX = "local_only"
DEFAULT_BACKFILL = "off"
DEFAULT_BATCH = 5
DEFAULT_DAILY_CAP = 200
BATCH_RANGE = (1, 50)
#: 0 = no cap. Only applied while the visual provider is a network one.
DAILY_CAP_RANGE = (0, 10_000)

#: Why a dispatch did or did not happen — stable strings for logs / status.
DecisionReason = Literal[
    "tag_shots",
    "policy_always",
    "provider_local",
    "policy_off",
    "provider_not_local",
    "visual_space_unresolved",
]


@dataclass(frozen=True)
class ShotsPolicy:
    auto_index: str
    backfill: str
    batch: int
    daily_cap: int

    def mode(self, which: Literal["auto_index", "backfill"]) -> str:
        return self.auto_index if which == "auto_index" else self.backfill


@dataclass(frozen=True)
class DispatchDecision:
    """Whether the policy lets ``index_shots`` run now. ``space_id`` is set
    only when ``dispatch`` is True (the visual space it would index into)."""

    dispatch: bool
    reason: str
    policy: ShotsPolicy
    provider_local: Optional[bool]
    space_id: Optional[int] = None
    detail: str = ""


@dataclass(frozen=True)
class BackfillState:
    day: str
    dispatched_today: int
    last_tick: Optional[str] = None
    last_error: Optional[str] = None
    last_skip: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "day": self.day,
            "dispatched_today": self.dispatched_today,
            "last_tick": self.last_tick,
            "last_error": self.last_error,
            "last_skip": self.last_skip,
        }


class PolicyValueError(ValueError):
    """A policy field got a value outside its whitelist / range."""

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field}: {reason}")


# ---------------------------------------------------------------- parsing ----
def _unquote(raw: Any) -> str:
    s = str(raw if raw is not None else "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return s


def parse_mode(raw: Any, default: str) -> str:
    """One of :data:`POLICY_MODES`; anything else is ``default``."""
    s = _unquote(raw).lower()
    return s if s in POLICY_MODES else default


def parse_bounded_int(raw: Any, default: int, lo: int, hi: int) -> int:
    """An int clamped to ``[lo, hi]``; unparsable is ``default``."""
    try:
        value = int(float(_unquote(raw)))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, value))


def check_mode(raw: Any) -> str:
    s = _unquote(raw).lower()
    if s not in POLICY_MODES:
        raise PolicyValueError("mode", f"must be one of {', '.join(POLICY_MODES)}")
    return s


def check_bounded_int(raw: Any, lo: int, hi: int) -> int:
    if isinstance(raw, bool):
        raise PolicyValueError("int", f"an integer in [{lo}, {hi}]")
    try:
        value = int(float(_unquote(raw)))
    except (TypeError, ValueError):
        raise PolicyValueError("int", f"an integer in [{lo}, {hi}]")
    if not lo <= value <= hi:
        raise PolicyValueError("int", f"an integer in [{lo}, {hi}]")
    return value


def validate_policy_update(fields: Dict[str, Any]) -> Dict[str, Any]:
    """``{governance key: normalised value}`` for the fields given (any of
    ``auto_index`` / ``backfill`` / ``batch`` / ``daily_cap``). Raises
    :class:`PolicyValueError` naming the offending field."""
    out: Dict[str, Any] = {}
    for field, value in fields.items():
        if value is None:
            continue
        try:
            if field == "auto_index":
                out[AUTO_INDEX_SETTING] = check_mode(value)
            elif field == "backfill":
                out[BACKFILL_SETTING] = check_mode(value)
            elif field == "batch":
                out[BACKFILL_BATCH_SETTING] = check_bounded_int(value, *BATCH_RANGE)
            elif field == "daily_cap":
                out[BACKFILL_DAILY_CAP_SETTING] = check_bounded_int(
                    value, *DAILY_CAP_RANGE
                )
            else:
                raise PolicyValueError(field, "unknown policy field")
        except PolicyValueError as e:
            raise PolicyValueError(field, e.reason) from e
    return out


# ---------------------------------------------------------------- reading ----
async def read_shots_policy() -> ShotsPolicy:
    """The four keys, each falling back to its default when absent or
    malformed. A settings read failure is logged and reads as the defaults
    too: ``auto_index=local_only`` only ever dispatches free local work, and
    ``backfill=off`` dispatches nothing."""
    repo = get_system_settings_repository()
    values: Dict[str, Any] = {}
    for key in (
        AUTO_INDEX_SETTING,
        BACKFILL_SETTING,
        BACKFILL_BATCH_SETTING,
        BACKFILL_DAILY_CAP_SETTING,
    ):
        try:
            values[key] = await repo.get_value(key)
        except Exception as e:  # noqa: BLE001 — tolerant read, defaults below
            logger.error(f"[shot_policy] reading {key}: {e}")
            values[key] = None
    return ShotsPolicy(
        auto_index=parse_mode(values[AUTO_INDEX_SETTING], DEFAULT_AUTO_INDEX),
        backfill=parse_mode(values[BACKFILL_SETTING], DEFAULT_BACKFILL),
        batch=parse_bounded_int(
            values[BACKFILL_BATCH_SETTING], DEFAULT_BATCH, *BATCH_RANGE
        ),
        daily_cap=parse_bounded_int(
            values[BACKFILL_DAILY_CAP_SETTING], DEFAULT_DAILY_CAP, *DAILY_CAP_RANGE
        ),
    )


async def write_shots_policy(fields: Dict[str, Any], admin_id: str) -> ShotsPolicy:
    """Validate and upsert the given fields; answers with the policy as
    it now reads."""
    repo = get_system_settings_repository()
    for key, value in validate_policy_update(fields).items():
        await repo.upsert_setting(key, value, admin_id)
    return await read_shots_policy()


async def visual_provider_is_local() -> Optional[bool]:
    """True when the visual layer's catalog row is served by nous-engine,
    False for a network provider, None when it cannot be told (no catalog
    row: a manual embedder config, nothing configured, or a read failure).
    None counts as "not local" for ``local_only``."""
    from app.repositories.nous_model_repository import (
        NOUS_ENGINE_PROVIDER,
        get_nous_model_repository,
    )

    try:
        name = await visual_catalog_name()
        if not name:
            return None
        row = await get_nous_model_repository().get_by_name(name)
    except ActiveSpaceUnknown as e:
        logger.warning(f"[shot_policy] visual provider unknown: {e}")
        return None
    if not row:
        return None
    return (row.get("actual_provider") or "") == NOUS_ENGINE_PROVIDER


def mode_allows(mode: str, provider_local: Optional[bool]) -> bool:
    if mode == "always":
        return True
    if mode == "local_only":
        return provider_local is True
    return False


async def dispatch_decision(
    which: Literal["auto_index", "backfill"], *, force: bool = False
) -> DispatchDecision:
    """Apply the policy (or ``force`` — the ``shots`` intent tag) and, when
    it allows, resolve the visual space so the caller never creates a task
    that would fail on ``embedder_unconfigured`` / ``provider_no_image``."""
    policy = await read_shots_policy()
    provider_local = await visual_provider_is_local()
    mode = policy.mode(which)
    if force:
        reason = "tag_shots"
    elif mode == "always":
        reason = "policy_always"
    elif mode == "local_only" and provider_local is True:
        reason = "provider_local"
    elif mode == "local_only":
        return DispatchDecision(False, "provider_not_local", policy, provider_local)
    else:
        return DispatchDecision(False, "policy_off", policy, provider_local)
    try:
        space, _ = await resolve_visual_space_and_embedder()
    except VisualSpaceError as e:
        return DispatchDecision(
            False, "visual_space_unresolved", policy, provider_local, detail=e.code
        )
    return DispatchDecision(
        True, reason, policy, provider_local, space_id=int(space["id"])
    )


# ------------------------------------------------------------ sweeper state ----
def today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_backfill_state(raw: Any) -> BackfillState:
    """From the stored JSON (dict or JSON string); anything else is a fresh
    state for today. A state from another day starts today at 0."""
    data: Any = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
    if not isinstance(data, dict):
        return BackfillState(day=today_utc(), dispatched_today=0)
    day = str(data.get("day") or "")
    dispatched = parse_bounded_int(data.get("dispatched_today"), 0, 0, 10**9)
    if day != today_utc():
        day, dispatched = today_utc(), 0
    return BackfillState(
        day=day,
        dispatched_today=dispatched,
        last_tick=data.get("last_tick") or None,
        last_error=data.get("last_error") or None,
        last_skip=data.get("last_skip") or None,
    )


async def read_backfill_state() -> BackfillState:
    try:
        raw = await get_system_settings_repository().get_value(BACKFILL_STATE_SETTING)
    except Exception as e:  # noqa: BLE001 — a display row; fresh state
        logger.error(f"[shot_policy] reading backfill state: {e}")
        raw = None
    return parse_backfill_state(raw)


#: ``system_settings.updated_by`` is a nullable ``uuid`` (the admin who last
#: wrote the row). The sweeper is not a user: it writes NULL. A string there
#: is an asyncpg DataError on every tick — which is exactly how the first
#: production tick failed (2026-09-26).
SYSTEM_WRITER = None


async def write_backfill_state(state: BackfillState) -> None:
    await get_system_settings_repository().upsert_setting(
        BACKFILL_STATE_SETTING, state.as_dict(), SYSTEM_WRITER
    )


__all__ = [
    "AUTO_INDEX_SETTING",
    "BACKFILL_BATCH_SETTING",
    "BACKFILL_DAILY_CAP_SETTING",
    "BACKFILL_SETTING",
    "BACKFILL_STATE_SETTING",
    "BATCH_RANGE",
    "DAILY_CAP_RANGE",
    "DEFAULT_AUTO_INDEX",
    "DEFAULT_BACKFILL",
    "DEFAULT_BATCH",
    "DEFAULT_DAILY_CAP",
    "POLICY_MODES",
    "BackfillState",
    "DispatchDecision",
    "PolicyValueError",
    "ShotsPolicy",
    "dispatch_decision",
    "mode_allows",
    "parse_backfill_state",
    "read_backfill_state",
    "read_shots_policy",
    "validate_policy_update",
    "visual_provider_is_local",
    "write_backfill_state",
    "write_shots_policy",
]
