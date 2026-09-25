"""The ``{"success": true, "data": ...}`` success envelope, as a typed model.

Most JSON routes wrap their payload as ``{"success": True, "data": <payload>}``.
Declaring ``response_model=Envelope[Payload]`` puts that shape into the
OpenAPI contract (and from there into ``frontend/types/api.generated.d.ts``)
without changing a byte on the wire.

Failures are not this model's job. An ``HTTPException`` is rendered by
``app/core/exceptions.py`` as the ``ErrorResponse`` shell
(``{"success": false, "error", "code", "request_id", "details"}``), which
bypasses the route's ``response_model`` entirely. So ``Envelope`` carries no
``error`` field: the success body never has one.

A route whose envelope carries siblings next to ``data`` (``total``,
``message`` …) subclasses this model and declares them, rather than widening
this one: every key on the wire is a declared key.

Spec: docs/superpowers/specs/2026-09-24-openapi-typed-frontend-design.md §3.3
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    """Success wrapper: ``success`` is always True on this path."""

    success: bool = True
    data: T
