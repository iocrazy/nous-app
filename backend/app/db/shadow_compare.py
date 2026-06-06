"""Shadow-compare harness for the ORM 2.0 rollout.

Strategy-C built every ``<Repo>Orm`` to be **byte-identical** to its legacy
supabase-py ``<Repo>`` (uuid→str, timestamptz→ISO, bigint→int, …). Before we
flip a domain's ``USE_ORM_<DOMAIN>`` flag in production, we want to *prove* that
parity holds on real prod traffic — without risking the live response.

``ShadowRepo`` does exactly that: for **read** methods it returns the REST result
to the caller (REST stays the source of truth, always) and, fire-and-forget,
runs the ORM method in the background, deep-diffs the two outputs, and logs any
mismatch to ``application_logs`` (module ``orm_shadow``). **Write** methods pass
straight through to the REST repo only — never dual-run, or we would double-
mutate the database.

The harness is gated by ``SHADOW_ORM_DOMAINS`` (a comma-list env on
``settings``), completely independent of ``USE_ORM_<DOMAIN>``. Empty by default
→ ``shadow_enabled`` is always False → factories behave exactly as before.

Design guarantees:
  * **Read-only on the caller's path** — the caller always receives the REST
    value; the ORM call and the diff happen on a detached background task.
  * **No propagation** — any exception in the ORM call or in the diff is caught
    and logged as a shadow-failure; it never reaches the request.
  * **No double-mutation** — only ``get_/list_/find_/count_/search_/fetch_/
    exists_`` prefixed methods are shadowed; everything else (writes) is a plain
    passthrough to REST.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from functools import partial
from typing import Any, Awaitable, Callable

from loguru import logger as _base_logger

# Loguru sinks the record's ``name`` field into ``application_logs.module``
# (see ``app/services/infra/db_log_sink.py::_serialize``). We override it to a
# stable ``orm_shadow`` so the rollout monitoring SQL can filter on
# ``module='orm_shadow'`` regardless of which module emits the record.
logger = _base_logger.patch(lambda record: record.update(name="orm_shadow"))

# Method-name prefixes that identify a *read*. Any method whose name starts with
# one of these is dual-run + diffed; everything else passes straight through to
# REST (treated as a write / side-effecting call).
READ_PREFIXES: tuple[str, ...] = (
    "get_",
    "list_",
    "find_",
    "count_",
    "search_",
    "fetch_",
    "exists_",
)

# Hard cap on the serialized diff we persist, so a large list mismatch can't
# blow up the application_logs row / the loguru pipeline.
_MAX_DIFF_CHARS = 2048

# Strong references to the fire-and-forget shadow tasks. ``asyncio.create_task``
# only keeps a *weak* reference, so without this the GC can collect (and cancel)
# a shadow task mid-flight before its diff completes — a documented asyncio
# footgun. We hold each task until it finishes, then discard it.
_BG_TASKS: set[asyncio.Task[None]] = set()


def _shadow_domains() -> frozenset[str]:
    """Parse ``settings.SHADOW_ORM_DOMAINS`` (comma-list) into a lowercase set."""
    from app.core.config import settings

    raw = settings.SHADOW_ORM_DOMAINS or ""
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def shadow_enabled(domain: str) -> bool:
    """True iff ``domain`` is listed in ``SHADOW_ORM_DOMAINS`` (case-insensitive)."""
    return domain.strip().lower() in _shadow_domains()


def is_read_method(name: str) -> bool:
    """True iff ``name`` looks like a read (so it is safe to dual-run + diff)."""
    return name.startswith(READ_PREFIXES)


# ----------------------------------------------------------------------------
# Normalization + deep diff
# ----------------------------------------------------------------------------


def _canonical(value: Any) -> Any:
    """Make a value comparable + JSON-ish.

    Strategy-C guarantees REST and ORM already emit identical *value types*, so
    we normalize **structure only** (dataclass → dict), not values. We do NOT
    coerce types or massage timestamps — any remaining diff is a real finding.
    """
    if is_dataclass(value) and not isinstance(value, type):
        return _canonical(asdict(value))
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value


def diff_results(rest: Any, orm: Any) -> str | None:
    """Return a short human-readable diff, or ``None`` when the two are equal.

    Lists are compared **as-is** (order-sensitive): an ORM repo that returns the
    same rows in a different order IS a finding — REST's ordering is the
    contract the callers depend on. The returned string is capped at
    ``_MAX_DIFF_CHARS``.
    """
    a = _canonical(rest)
    b = _canonical(orm)
    if a == b:
        return None

    # Type mismatch is the most common + most informative case.
    if type(a) is not type(b):
        msg = f"type mismatch: rest={type(rest).__name__} orm={type(orm).__name__}"
        return msg[:_MAX_DIFF_CHARS]

    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"list length: rest={len(a)} orm={len(b)}"[:_MAX_DIFF_CHARS]
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                return f"list[{i}] differs: rest={x!r} orm={y!r}"[:_MAX_DIFF_CHARS]
        # Equal element-wise but != as a whole — shouldn't happen, be explicit.
        return "list differs (whole-object inequality)"[:_MAX_DIFF_CHARS]

    if isinstance(a, dict) and isinstance(b, dict):
        keys = sorted(set(a) | set(b))
        parts: list[str] = []
        for k in keys:
            if a.get(k) != b.get(k):
                parts.append(f"{k}: rest={a.get(k)!r} orm={b.get(k)!r}")
        if parts:
            return ("; ".join(parts))[:_MAX_DIFF_CHARS]

    # Scalar (or anything else) mismatch.
    return f"value: rest={rest!r} orm={orm!r}"[:_MAX_DIFF_CHARS]


def _summarize_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """Compact, capped repr of the call args for the diff log (no secrets dumps)."""
    summary = f"args={args!r} kwargs={kwargs!r}"
    return summary[:_MAX_DIFF_CHARS]


# ----------------------------------------------------------------------------
# ShadowRepo
# ----------------------------------------------------------------------------


class ShadowRepo:
    """Wrap a (rest, orm) repo pair and dual-run reads for parity validation.

    Method dispatch is by name prefix (see ``READ_PREFIXES``) via ``__getattr__``,
    so it works for *any* repo without a per-repo method list:

      * read method  → returns the REST result; schedules an ORM run + diff in
        the background; logs mismatches/failures to ``application_logs``.
      * other method → plain passthrough to the REST repo (writes are never
        dual-run).
    """

    __slots__ = ("_rest", "_orm", "_domain")

    def __init__(self, rest_repo: Any, orm_repo: Any, domain: str) -> None:
        self._rest = rest_repo
        self._orm = orm_repo
        self._domain = domain

    def __getattr__(self, name: str) -> Any:
        # __slots__ attrs are resolved before __getattr__, so anything here is a
        # repo method/attribute. Bind from the REST repo (the live truth).
        rest_attr = getattr(self._rest, name)

        # Non-callables and write methods: passthrough to REST unchanged.
        if not callable(rest_attr) or not is_read_method(name):
            return rest_attr

        return partial(self._shadowed_call, name, rest_attr)

    async def _shadowed_call(
        self,
        name: str,
        rest_method: Callable[..., Awaitable[Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Run REST (return it), then fire-and-forget the ORM diff."""
        result = await rest_method(*args, **kwargs)

        # Fire-and-forget: never await, never let it touch the response path.
        try:
            task = asyncio.create_task(self._shadow_compare(name, result, args, kwargs))
        except RuntimeError:
            # No running loop (shouldn't happen on the async request path) —
            # skip the shadow silently rather than crash the read.
            pass
        else:
            # Hold a strong ref until done so the GC can't cancel it mid-flight.
            _BG_TASKS.add(task)
            task.add_done_callback(_BG_TASKS.discard)

        return result

    async def _shadow_compare(
        self,
        name: str,
        rest_result: Any,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        """Background: run the ORM method, diff, and log any mismatch/failure.

        Catches everything — a shadow run must never affect the request that
        already returned, nor crash the event loop with an unretrieved task
        exception.
        """
        try:
            orm_method = getattr(self._orm, name)
            orm_result = await orm_method(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — shadow must swallow everything
            logger.bind(
                domain=self._domain,
                method=name,
                args_summary=_summarize_args(args, kwargs),
            ).warning(
                "orm_shadow failure [{}.{}]: ORM raised {}: {}",
                self._domain,
                name,
                type(exc).__name__,
                exc,
            )
            return

        try:
            diff = diff_results(rest_result, orm_result)
        except Exception as exc:  # noqa: BLE001
            logger.bind(domain=self._domain, method=name).warning(
                "orm_shadow failure [{}.{}]: diff raised {}: {}",
                self._domain,
                name,
                type(exc).__name__,
                exc,
            )
            return

        if diff is None:
            return  # parity holds — the happy path, logged nowhere.

        logger.bind(
            domain=self._domain,
            method=name,
            args_summary=_summarize_args(args, kwargs),
            diff=diff,
        ).warning(
            "orm_shadow mismatch [{}.{}]: {}",
            self._domain,
            name,
            diff,
        )
