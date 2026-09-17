"""The ``from`` / ``to`` window parser every usage-style endpoint shares.

One implementation because two would drift: the Usage panel
(``usage_router.usage_summary``) and the AI Library efficiency endpoint
(``ai_library_router.get_usage_efficiency``) must agree on what ``2026-09-15``
means (UTC midnight) and on what an unparseable value means.

An unparseable value falls back to the caller's default rather than 400: the
window only narrows a read-only aggregate, so a stray character should not
blank the page. Anything that is not a string counts as absent — FastAPI hands
this a ``str | None``, and the only way a non-string arrives is an endpoint
function called directly with its ``Query(...)`` default object still in place,
which is not a date and must not raise ``TypeError`` out of a parser whose
whole contract is "never fail".
"""

from __future__ import annotations

import datetime
from typing import Any, Optional

#: The widest window any usage-style endpoint will aggregate over. Shared, not
#: copied: both readers scan a table that grows forever, and a second copy of
#: this number only ever drifts in the direction nobody notices — upward, on the
#: endpoint with no index to save it.
MAX_RANGE_DAYS = 366


def window_error(
    frm: datetime.datetime,
    to: datetime.datetime,
    *,
    max_days: int = MAX_RANGE_DAYS,
) -> Optional[str]:
    """``None`` if the window is usable, else a stable reason code.

    Returns a code rather than raising so each caller can dress it in its own
    error shape — the Usage panel answers in prose it has always answered in,
    the AI-library endpoints answer with ``details.code`` — without either one
    owning the rule itself.
    """
    if frm >= to:
        return "invalid_range"
    if (to - frm).days > max_days:
        return "range_too_long"
    return None


def parse_window_dt(value: Any, *, default: datetime.datetime) -> datetime.datetime:
    """Parse a 'YYYY-MM-DD' or full-ISO string into a UTC-aware datetime.
    Blank/invalid/non-string → ``default``."""
    if not isinstance(value, str) or not value:
        return default
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed = datetime.datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return default
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed
