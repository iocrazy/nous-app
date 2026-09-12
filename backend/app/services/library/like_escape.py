"""Escape user text before it becomes an ILIKE pattern.

``rpc_user_media_text_search`` takes a ready-made pattern (``%foo%``) rather
than a bare term, so whoever builds the pattern owns the escaping. Nobody did.

The consequence is not injection — the pattern is a bound parameter — but a
user typing a single ``%`` got a match-everything scan across every searchable
column, transcript bodies included. ``_`` behaves the same way for one
character. Both are ordinary characters in a search box and must match
themselves.

Postgres has no default LIKE escape character, so the pattern has to be used
with an explicit ``ESCAPE '\\'`` for these to mean anything. The RPC declares
that on every ILIKE it applies.
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
