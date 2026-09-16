"""Escape user text before it becomes an ILIKE pattern.

``rpc_user_media_text_search`` takes a ready-made pattern (``%foo%``) rather
than a bare term, so whoever builds the pattern owns the escaping. Nobody did.

The consequence is not injection — the pattern is a bound parameter — but a
user typing a single ``%`` got a match-everything scan across every searchable
column, transcript bodies included. ``_`` behaves the same way for one
character. Both are ordinary characters in a search box and must match
themselves.

The backslash IS Postgres' default ``LIKE`` / ``ILIKE`` escape character, so
this function is the *mechanism*: it is what makes a typed ``%`` match a literal
``%``. The explicit ``ESCAPE '\\'`` the RPC writes on every ILIKE is a
*declaration* — it pins that default down rather than depending on server
configuration, and saves the next reader a trip to the manual. Both belong
there, but a missing ``ESCAPE`` is not the same as escaping being switched off
(``ESCAPE ''`` would be that).
"""

from __future__ import annotations

#: The character the RPC declares in its ``ESCAPE`` clause.
LIKE_ESCAPE_CHAR = "\\"


def escape_like(value: str) -> str:
    """Return ``value`` with LIKE metacharacters neutralised.

    The backslash is escaped first; doing it last would double-escape the
    backslashes this function just introduced.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
