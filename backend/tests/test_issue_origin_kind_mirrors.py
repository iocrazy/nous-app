"""Pin the origin_kind enum's mirrors to each other.

The enum lives in FOUR places: the DB CHECK (migration), the SQLAlchemy
model's CheckConstraint (drift-gate-pinned to the DB), the Pydantic schema
enum, and the TS union. The first two are covered by the schema-drift gate;
this test pins the third to the second — the exact gap that made
GET /issues/ 500 on 'project_stage' rows (2026-07-18). The TS union can't be
cross-pinned from here; both sites carry a four-mirrors comment instead.
"""

import re

from sqlalchemy import CheckConstraint

from app.models.reviews import Issues
from app.schemas.issue import IssueOriginKind


def _constraint_values() -> set[str]:
    for arg in Issues.__table_args__:
        if (
            isinstance(arg, CheckConstraint)
            and getattr(arg, "name", None) == "issues_origin_kind_check"
        ):
            return set(re.findall(r"'([a-z_]+)'", str(arg.sqltext)))
    raise AssertionError("issues_origin_kind_check not found on Issues model")


def test_pydantic_enum_matches_model_check_constraint():
    model_values = _constraint_values()
    schema_values = {m.value for m in IssueOriginKind}
    assert schema_values == model_values, (
        f"IssueOriginKind (schemas/issue.py) and issues_origin_kind_check "
        f"(models/reviews.py) drifted: schema-only={schema_values - model_values}, "
        f"model-only={model_values - schema_values}"
    )
