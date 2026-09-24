"""Shared plumbing for the ``/ai-library`` wire-parity tests (OpenAPI P4).

The routes read through ``read_scope()`` sessions and a handful of
repositories. :class:`ScriptedScope` stands in for ``read_scope`` and serves
one staged result per ``session.execute`` call, in order, across every
session the handler opens — so a test lists the rows each SELECT returns in
the order the handler issues them, and nothing else.

Wire parity is measured the way ``tests/api/wire_parity.py`` describes: the
handler is called directly (no response model in the way) to get the dict it
builds, then the same request goes over HTTP, and the body must equal
``jsonable_encoder`` of that dict.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Iterable

from app.core.deps import AuthContext

USER = "00000000-0000-0000-0000-000000000042"
AUTH = AuthContext(user_id=USER, auth_type="jwt")


async def fake_auth() -> AuthContext:
    return AUTH


class _Mappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def mappings(self) -> _Mappings:
        return _Mappings(self._value)

    def scalar(self) -> Any:
        return self._value


class ScriptedScope:
    """``read_scope`` replacement: each ``execute`` pops the next result.

    A result is a list of row dicts (read via ``.mappings()``) or a scalar
    (read via ``.scalar()``). Running out of results is a test bug, so it
    raises instead of quietly returning an empty page.
    """

    def __init__(self, results: Iterable[Any]) -> None:
        self._results = list(results)
        self.statements: list[Any] = []

    def __call__(self):
        return self._scope()

    @asynccontextmanager
    async def _scope(self):
        outer = self

        class _Session:
            async def execute(self, stmt: Any) -> _Result:
                outer.statements.append(stmt)
                assert outer._results, f"unexpected extra query: {stmt}"
                return _Result(outer._results.pop(0))

        yield _Session()
