# app/core/scope_dep.py

"""FastAPI dependency that establishes the ambient tenant ``Scope`` for a request.

Entry-ambient scope threading (plan D1): one generator dependency sets the
``_scope`` ContextVar for the whole request and resets it afterwards, so the
app-layer choke point (``app/db/scope.py``) reads the request's identity while
repo methods keep using plain ``read_scope()`` / ``write_scope()`` — no explicit
``Scope`` argument threading through every call.

Resources is single-axis ``UserScoped(creator_id)`` (per decisions §7.2 — NOT
team/project scoped), so the ``Scope`` only needs ``user_id`` with empty team/
project sets; there is NO membership query here.

IMPORTANT — ``creator_id`` is a UUID, not a bigint. ``resources.creator_id`` (the
owner column ``UserScoped`` names) is a Postgres ``uuid`` holding the Supabase
auth ``sub``. ``AuthContext.user_id`` is that UUID *string*. We therefore pass it
THROUGH UNCHANGED — we do NOT apply the ``_bigint`` snowflake-string→int coercion
used for bigint id columns; SQLAlchemy/asyncpg binds the UUID string against the
``uuid`` column directly. (The ``Scope.user_id: int | str`` annotation is nominal — the
frozen dataclass does no runtime validation — and the tenant axis here is a UUID.)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends

from app.core.deps import AuthContext, get_auth
from app.db.scope import Scope, request_scope


async def scoped_request(
    auth: AuthContext = Depends(get_auth),
) -> AsyncIterator[None]:
    """Set the ambient ``Scope(user_id=<auth user>)`` for the request duration.

    Builds a single-axis user scope from ``AuthContext.user_id`` (the Supabase
    auth ``sub`` — a UUID string, passed through unchanged; see module docstring)
    and binds it via ``request_scope`` so the choke point enforces
    ``creator_id == scope.user_id`` on every scoped query in the request, then
    resets the ambient scope on exit.

    Inert until ``SCOPE_ENFORCE_RESOURCES`` is on: with the flag off the choke
    point does not enforce ``resources``, so adding this dependency to a router is
    safe to land ahead of the flip.
    """
    async with request_scope(Scope(user_id=auth.user_id)):
        yield


# Annotated alias for routers to add: ``deps: ScopedRequestDep`` (or as a
# path-operation ``dependencies=[Depends(scoped_request)]``).
ScopedRequestDep = Annotated[None, Depends(scoped_request)]
