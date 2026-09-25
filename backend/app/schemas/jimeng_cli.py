"""Response shapes of ``/api/v1/jimeng-cli/*`` (the server's shared dreamina login).

Both bodies are ``{"data": ...}`` with no ``success`` key, and each ``data`` is
one of two shapes told apart by which keys are present, so each is declared as
a union of two models rather than one model with optional fields (a default
would add a key the route never sent).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class JimengCliStatusLoggedIn(BaseModel):
    """``dreamina user_credit`` succeeded.

    ``total_credit`` is passed through from the CLI's JSON untouched (a number
    in every output seen so far; ``None`` when the CLI omits it).
    ``user_id`` is the dreamina account id, not a nous user. Numbers pass
    through as the CLI wrote them (``vip_level`` has only been seen as a
    string; an int would otherwise fail validation).
    """

    available: Literal[True]
    logged_in: Literal[True]
    total_credit: int | float | str | None
    user_id: str
    vip_level: str | int


class JimengCliStatusLoggedOut(BaseModel):
    """Not logged in, or the CLI could not be asked.

    ``reason`` is ``cli_missing`` / ``timeout`` or the first 200 characters of
    the CLI's own output.
    """

    available: bool
    logged_in: Literal[False]
    reason: str


class JimengCliStatusEnvelope(BaseModel):
    data: JimengCliStatusLoggedIn | JimengCliStatusLoggedOut


class JimengCliDeviceLink(BaseModel):
    """The device-flow link to approve in a browser (valid ~10 minutes)."""

    verification_uri: str
    user_code: str | None


class JimengCliAlreadyLoggedIn(BaseModel):
    """The CLI reused a stored token: nothing to approve."""

    already_logged_in: Literal[True]


class JimengCliLoginEnvelope(BaseModel):
    data: JimengCliDeviceLink | JimengCliAlreadyLoggedIn
