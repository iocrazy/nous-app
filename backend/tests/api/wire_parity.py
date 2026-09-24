"""Wire-parity helpers for routes that gain a ``response_model``.

Declaring a response model on a route that used to return a bare dict makes
Pydantic filter the body through the model: an undeclared key disappears,
and a value can be re-serialized (datetimes, decimals). Both are wire changes
the frontend never agreed to. Each typed route gets a test that:

1. builds the dict the handler returns from a row carrying EVERY column with
   its real native type (:func:`sample_row`, driven by the ORM model, which
   the schema-drift gate keeps equal to the live table), and
2. asserts the HTTP response equals ``jsonable_encoder`` of that dict — what
   FastAPI sent before the model existed (:func:`assert_wire_unchanged`).

A fixture that omits a column would prove nothing about that column, which is
why rows come from the ORM mapper and not from hand-written dicts.

Spec: docs/superpowers/specs/2026-09-24-openapi-typed-frontend-design.md §5
"""

from __future__ import annotations

import datetime as dt
import decimal
import uuid
from typing import Any, Iterable

from fastapi.encoders import jsonable_encoder
from sqlalchemy import (
    ARRAY,
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Uuid,
    inspect,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

# Not midnight, not whole seconds: a serializer that drops microseconds or
# rewrites the offset shows up as a diff.
SAMPLE_TS = dt.datetime(2026, 9, 24, 1, 2, 3, 456789, tzinfo=dt.timezone.utc)
# Above 2**53, so a float round-trip of a Snowflake id cannot pass unnoticed.
SAMPLE_BIGINT = 7_300_000_000_000_000_123


def _sample_value(column: Any, index: int) -> Any:
    kind = column.type
    if isinstance(kind, ARRAY):
        return [f"{column.name}-a", f"{column.name}-b"]
    if isinstance(kind, (JSONB, JSON)):
        return {"k": column.name, "n": index}
    if isinstance(kind, (Uuid, PG_UUID)):
        return uuid.UUID(int=index + 1)
    if isinstance(kind, DateTime):
        return SAMPLE_TS + dt.timedelta(seconds=index)
    if isinstance(kind, Date):
        return dt.date(2026, 9, 24)
    if isinstance(kind, Boolean):
        return index % 2 == 0
    if isinstance(kind, BigInteger):
        return SAMPLE_BIGINT + index
    if isinstance(kind, (Integer, SmallInteger)):
        return 100 + index
    if isinstance(kind, Numeric) and not isinstance(kind, Float):
        return decimal.Decimal("12.5") + index
    if isinstance(kind, Float):
        return 12.5 + index
    if isinstance(kind, (String, Text)):
        return f"{column.name}-value"
    try:
        python_type = kind.python_type
    except NotImplementedError:
        return f"{column.name}-value"
    if python_type is str:
        return f"{column.name}-value"
    raise TypeError(f"no sample for {column.name}: {kind!r}")


def sample_row(model: Any, *, only: Iterable[str] | None = None) -> dict[str, Any]:
    """Every mapped column of ``model`` (or the ``only`` subset, by DB column
    name) with a non-null value of its native Python type, keyed by DB column
    name — the shape of ``SELECT *`` / ``_orm_obj_to_dict``."""
    wanted = set(only) if only is not None else None
    out: dict[str, Any] = {}
    for index, prop in enumerate(inspect(model).column_attrs):
        column = prop.columns[0]
        if wanted is None or column.name in wanted:
            out[column.name] = _sample_value(column, index)
    if wanted is not None:
        missing = wanted - set(out)
        assert not missing, f"{model.__name__} has no columns {sorted(missing)}"
    return out


def sample_orm(model: Any, **overrides: Any) -> Any:
    """A transient ORM instance with every column set (for repos that
    convert ORM objects themselves, e.g. ``_row(obj, N2A)``)."""
    values = {
        prop.key: _sample_value(prop.columns[0], index)
        for index, prop in enumerate(inspect(model).column_attrs)
    }
    values.update(overrides)
    return model(**values)


def column_names(model: Any) -> set[str]:
    return {prop.columns[0].name for prop in inspect(model).column_attrs}


def assert_wire_unchanged(response: Any, raw: Any, *, status: int = 200) -> None:
    """The HTTP body equals what FastAPI emitted for ``raw`` with no model."""
    assert response.status_code == status, response.text
    assert response.json() == jsonable_encoder(raw)
