"""Shared read-boundary helpers for the SQLAlchemy 2.0 ORM repositories.

Extracted from ``media_repository_orm.py`` (Task 5.1) and
``resources_repository_orm.py`` (Task 5.2) so the upcoming agent_runs /
user_settings ORM repos reuse them instead of growing a 3rd/4th copy.

These three functions encode two P0-class parity fixes caught in the 5.1
quality review — both about making an ORM read produce EXACTLY the plain
dict the retired asyncpg / legacy supabase-py impls produced:

  1. Enum → ``.value`` unwrap (``_plain``): ORM ``Enum(...)`` columns read
     back as Enum MEMBERS, but the prior impls returned bare ``str``. Enum
     members ARE str subclasses so ``==`` / ``json.dumps`` look fine, yet
     ``str(x)`` / f-strings yield ``"DownloadStatus.COMPLETED"`` instead of
     ``"completed"`` — silently breaking parity for every caller.

  2. Renamed-column attribute resolution (``_name_to_attr`` /
     ``_orm_obj_to_dict``): a column declared with a Python attribute name
     different from its DB column name must be read via the *attribute*,
     not the column name. The canonical trap is a JSONB ``metadata`` column
     mapped to ``metadata_`` (SQLAlchemy reserves ``metadata`` on
     declarative classes for the MetaData registry) — reading
     ``getattr(obj, "metadata")`` would hand back the MetaData object, not
     the row value.
"""

from __future__ import annotations

import enum
from typing import Any, Dict

from sqlalchemy import inspect

__all__ = ["_plain", "_name_to_attr", "_orm_obj_to_dict"]


def _plain(value: Any) -> Any:
    """Coerce a value to its plain-Python form at the read dict boundary.

    The ORM types enum-backed columns (e.g. the parsed_media
    ``*_download_status`` columns as ``Enum(DownloadStatus)``, or the
    resources status columns as ``Enum(AiTaskStatus)``) so reads return
    Enum MEMBERS, whereas the retired asyncpg / legacy supabase-py impls
    returned bare ``str``. Enum members ARE str subclasses, so ``==`` and
    ``json.dumps`` look fine — but ``str(x)`` / f-strings yield
    ``"DownloadStatus.COMPLETED"`` instead of ``"completed"`` (or
    ``"AiTaskStatus.NONE"`` instead of ``"none"``), silently breaking
    parity for callers. Unwrap any Enum to ``.value`` so every status field
    returns exactly the bare string the prior impls did."""
    if isinstance(value, enum.Enum):
        return value.value
    return value


def _name_to_attr(model: Any) -> Dict[str, str]:
    """Build a DB-column-name → mapped-attribute-name map from the mapper.

    Works for ANY mapped model. For most columns ``name == key``, but a
    column declared ``mapped_column("dbname")`` with a different Python
    attribute (e.g. a JSONB ``metadata`` column mapped to ``metadata_``
    because SQLAlchemy reserves ``metadata`` on declarative classes for the
    MetaData registry) has ``name != key``. Reading ``getattr(obj, name)``
    for such a column would hand back the wrong object (the MetaData
    registry), so we always resolve the value via the mapped attribute
    name."""
    return {prop.columns[0].name: prop.key for prop in inspect(model).column_attrs}


def _orm_obj_to_dict(obj: Any, name_to_attr: Dict[str, str]) -> Dict[str, Any]:
    """Convert a full ORM row object to a plain dict keyed by DB column NAME
    (SELECT * parity).

    Values are read via the mapped attribute (NOT the column name — see
    ``_name_to_attr``) and enum-typed columns are unwrapped to bare strings
    (see ``_plain``) so the dict matches the legacy supabase-py / asyncpg
    SELECT * shape exactly. ``name_to_attr`` is the precomputed map for the
    model — pass ``_name_to_attr(Model)`` (build it once at module level)."""
    return {name: _plain(getattr(obj, attr)) for name, attr in name_to_attr.items()}
