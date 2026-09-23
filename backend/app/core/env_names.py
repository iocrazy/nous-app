"""The single place that knows an env var has two names.

The project was renamed mediahub → nous. Process env that lives OUTSIDE
this repo (production ``secrets/backend.env``, the retired NAS stack's
compose) still says ``MEDIAHUB_*``, so every reader accepts both:

    env_alias("TOKEN_ENCRYPTION_KEY")
        → NOUS_TOKEN_ENCRYPTION_KEY, else MEDIAHUB_TOKEN_ENCRYPTION_KEY

Precedence
==========
* An empty value counts as unset (matches the old truthiness checks, and
  an empty ``NOUS_*=`` copied from ``.env.example`` must not shadow a real
  legacy value).
* If BOTH names are set and differ, the ``NOUS_*`` name wins and a
  WARNING is logged once per process per variable. Silently picking one
  would be the worst outcome for the encryption key: after a half-done
  rename, half the stored secrets would stop decrypting with no signal.
  The warning names the variables, never their values.

Once no environment sets ``MEDIAHUB_*`` any more, delete the fallback here.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

logger = logging.getLogger(__name__)

NEW_PREFIX = "NOUS_"
LEGACY_PREFIX = "MEDIAHUB_"

_warned: set[str] = set()


def env_names(suffix: str) -> tuple[str, str]:
    """``(new_name, legacy_name)`` for ``suffix`` — use in messages."""
    return f"{NEW_PREFIX}{suffix}", f"{LEGACY_PREFIX}{suffix}"


def env_alias(suffix: str, env: Mapping[str, str] | None = None) -> str | None:
    """Value of ``NOUS_<suffix>``, falling back to ``MEDIAHUB_<suffix>``.

    Returns ``None`` when neither is set to a non-empty value.
    """
    src = env if env is not None else os.environ
    new_name, legacy_name = env_names(suffix)
    new = src.get(new_name) or None
    legacy = src.get(legacy_name) or None
    if new is not None and legacy is not None and new != legacy:
        if suffix not in _warned:
            _warned.add(suffix)
            logger.warning(
                "%s and %s are both set and differ; using %s. "
                "Remove %s once the rename is complete.",
                new_name,
                legacy_name,
                new_name,
                legacy_name,
            )
    return new if new is not None else legacy


def _reset_warned_for_tests() -> None:
    _warned.clear()


__all__ = ["env_alias", "env_names"]
