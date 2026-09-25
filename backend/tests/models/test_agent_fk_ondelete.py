"""ORM declarations of the agent FKs that mig 505 (re)creates.

The schema-drift gate compares FK column pairs only, never ``ondelete``, so an
ORM that says CASCADE while the database says SET NULL would stay green. These
pins are the only thing that notices.
"""

from __future__ import annotations

import pytest

from app.models import AgentRuns, AgentSkills, ConversationAiMeta


def _ondelete(model, name: str) -> str | None:
    by_name = {fk.name: fk for fk in model.__table__.foreign_key_constraints}
    assert name in by_name, f"{model.__tablename__} lacks FK {name}"
    return by_name[name].ondelete


@pytest.mark.parametrize(
    ("model", "name", "expected", "target"),
    [
        (AgentSkills, "agent_skills_agent_id_fkey", "CASCADE", "ai_agents.id"),
        (
            ConversationAiMeta,
            "conversation_ai_meta_agent_id_fkey",
            "SET NULL",
            "ai_agents.id",
        ),
        (AgentRuns, "agent_runs_parent_run_id_fkey", "SET NULL", "agent_runs.id"),
        (AgentRuns, "agent_runs_root_run_id_fkey", "SET NULL", "agent_runs.id"),
    ],
)
def test_agent_fk_ondelete(model, name, expected, target):
    assert _ondelete(model, name) == expected
    fk = next(fk for fk in model.__table__.foreign_key_constraints if fk.name == name)
    assert [e.target_fullname for e in fk.elements] == [f"public.{target}"]
