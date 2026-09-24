"""Building blocks for declaring the response shape a route ALREADY emits.

Adding ``response_model`` to a route that returned a bare dict must not change
a byte of its JSON. Two places where Pydantic's serializer differs from the
``jsonable_encoder`` FastAPI used on the bare dict:

- **datetimes.** ``jsonable_encoder`` writes ``datetime.isoformat()``
  (``2026-09-24T01:02:03+00:00``); Pydantic writes ``…Z`` for UTC. A field
  that holds a native ``datetime`` is declared :data:`WireDatetime`, which
  serializes through ``isoformat()`` and so keeps the old string. A field the
  repository already turned into an ISO string is declared ``str``.
- **missing keys.** A declared field with a default is always emitted, so an
  optional default adds a key the bare dict did not have. Columns that every
  row carries are declared required; defaults are for keys that really are
  sometimes absent.

And one thing Pydantic does that the bare dict did not: it drops undeclared
keys. That is the reason every typed route has a wire-parity test comparing
its response with ``jsonable_encoder`` of the dict the handler builds (see
``tests/api/wire_parity.py``).

Spec: docs/superpowers/specs/2026-09-24-openapi-typed-frontend-design.md §5
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel, PlainSerializer, WithJsonSchema

T = TypeVar("T")

WireDatetime = Annotated[
    datetime,
    PlainSerializer(lambda value: value.isoformat(), return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "format": "date-time"}),
]
"""A native ``datetime`` that serializes exactly as ``jsonable_encoder`` does."""


class DataEnvelope(BaseModel, Generic[T]):
    """``{"data": ...}`` with no ``success`` key.

    Some routers (generated-media, generated) never sent ``success``; wrapping
    them in :class:`app.schemas.envelope.Envelope` would add it.
    """

    data: T


def binary_response(description: str, *media_types: str) -> dict[int | str, Any]:
    """``responses=`` for a route that returns bytes, not JSON.

    Pair it with ``response_class=Response`` so FastAPI does not also
    advertise an ``application/json`` body the route never sends.
    """
    binary = {"schema": {"type": "string", "format": "binary"}}
    return {
        200: {
            "description": description,
            "content": {media_type: binary for media_type in media_types},
        }
    }
