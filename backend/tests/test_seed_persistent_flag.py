"""``ai_agents.persistent`` is declared by the seed, not by a migration.

Task 7a defect 4 (2026-09-10 real-stack acceptance, evidence H). Production
holds ZERO persistent rows, so ``FEATURE_WORKFORCE_DELEGATE`` — flipped on the
same day — is inert: every ``Delegate`` call is refused with "not configured as
a persistent worker". Migrations 162 (``summarize`` / ``analyze``) and 163
(``coordinator``) set the flag, but both sit BELOW the schema baseline
watermark, so they are baked into ``schema_baseline.sql`` and have never run
against this database. The seed loader — the one thing that DOES run on every
startup — omitted the column entirely, so nothing ever converged it.

The flag now rides in the seed's own ``AGENT.md`` frontmatter and is upserted
on every startup, which is why production converges on the next deploy with no
migration. Two things have to hold or it rots exactly the way the group map
would have: the declared set must match what 162/163 meant, and the flag must
participate in the seed hash — otherwise flipping it hits the unchanged-skip
branch and never lands.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.repositories.agent_repository import AgentRepository
from app.repositories.skill_repository import SkillRepository
from app.services.ai.runner.seed_loader import SeedLoader

pytestmark = pytest.mark.unit

SEEDS_AGENTS_DIR = Path(__file__).resolve().parents[1] / "seeds" / "agents"

#: Exactly what migrations 162 + 163 promoted. ``script_ai`` is deliberately
#: absent — neither migration touched it, and a pilot chat agent is not a
#: workforce worker.
EXPECTED_PERSISTENT = {"summarize", "analyze", "coordinator"}


def _agent_repo(existing: Any = None) -> AsyncMock:
    repo = AsyncMock(spec=AgentRepository)
    repo.get_by_slug.return_value = existing
    repo.update_fields.return_value = {}
    repo.update_skill_bindings.return_value = None
    repo.insert.return_value = {"id": str(uuid4())}
    return repo


def _loader(tmp_path: Path, repo: AsyncMock) -> SeedLoader:
    skills = AsyncMock(spec=SkillRepository)
    skills.get_by_slug.return_value = None
    skills.list_files.return_value = []
    skills.insert.return_value = {"id": 1}
    return SeedLoader(repo, skills, tmp_path)


def _seed(tmp_path: Path, slug: str, agent_md: str) -> Path:
    d = tmp_path / "agents" / slug
    d.mkdir(parents=True)
    (d / "IDENTITY.md").write_text(f"I am {slug}.")
    (d / "SOUL.md").write_text("Be brief.")
    (d / "AGENT.md").write_text(agent_md)
    return d


# ── the declaration ────────────────────────────────────────────────────


def test_a_seed_declaring_persistent_sets_the_column(tmp_path: Path) -> None:
    d = _seed(tmp_path, "summarize", "---\npersistent: true\n---\n\nDo the work.")
    fields = _loader(tmp_path, _agent_repo())._read_agent_fields(d, "summarize")
    assert fields["persistent"] is True


def test_persistent_defaults_to_false(tmp_path: Path) -> None:
    """A worker is opt-in. Everything the loader seeds is a chat agent until
    its own seed says otherwise."""
    d = _seed(tmp_path, "translate", "Translate the text.")
    fields = _loader(tmp_path, _agent_repo())._read_agent_fields(d, "translate")
    assert fields["persistent"] is False


def test_the_frontmatter_never_reaches_the_model(tmp_path: Path) -> None:
    """``agent_md`` is model-visible. A raw YAML block in it would be a
    prompt change dressed up as configuration."""
    d = _seed(tmp_path, "summarize", "---\npersistent: true\n---\n\nDo the work.")
    fields = _loader(tmp_path, _agent_repo())._read_agent_fields(d, "summarize")
    assert fields["agent_md"] == "Do the work."


# ── it has to land on an EXISTING row, which is the production case ────


async def test_flipping_the_flag_changes_the_seed_hash(tmp_path: Path) -> None:
    """Production rows already exist with the right prose. If ``persistent``
    is not hashed, the loader takes the unchanged-skip branch and the column
    stays false forever — the exact rot the agent_group comment warns about."""
    loader = _loader(tmp_path, _agent_repo())
    d = _seed(tmp_path, "summarize", "---\npersistent: true\n---\n\nBody.")
    worker = loader._read_agent_fields(d, "summarize")
    # the SAME seed with only the flag flipped — nothing else may differ, or
    # this passes for a reason that has nothing to do with the column
    plain = {**worker, "persistent": False}
    assert worker["persistent"] is True
    assert loader._agent_seed_hash(plain) != loader._agent_seed_hash(worker)


async def test_an_existing_row_is_updated_with_the_column(tmp_path: Path) -> None:
    """The production shape: the row is there, the prose matches, only the
    flag is wrong. It must be written anyway."""
    _seed(tmp_path, "summarize", "---\npersistent: true\n---\n\nBody.")
    repo = _agent_repo(
        existing={"id": str(uuid4()), "seed_hash": "stale", "persistent": False}
    )
    await _loader(tmp_path, repo).load_all()
    updates = repo.update_fields.await_args.args[1]
    assert updates["persistent"] is True


async def test_a_new_row_is_inserted_with_the_column(tmp_path: Path) -> None:
    _seed(tmp_path, "coordinator", "---\npersistent: true\n---\n\nRoute work.")
    repo = _agent_repo(existing=None)
    await _loader(tmp_path, repo).load_all()
    assert repo.insert.await_args.args[0]["persistent"] is True


# ── a leading `---` is not always frontmatter ──────────────────────────


HR_SEED = """---
You are an agent that keeps its own rules above the fold.

---

More prose after the rule.
"""


def test_a_horizontal_rule_does_not_eat_the_prose(tmp_path: Path) -> None:
    """Fix round 1, I4. ``frontmatter.load`` treats a leading ``---`` as an
    opening fence: on this file it returns ``metadata={}`` and a body starting
    at "More prose", silently dropping everything between the two rules — and
    that text is MODEL-VISIBLE (it becomes ``agent_md``). No exception, no log.

    A leading block is only frontmatter when it parses as a YAML mapping that
    declares something we know. Anything else is prose and stays.
    """
    d = _seed(tmp_path, "translate", HR_SEED)
    fields = _loader(tmp_path, _agent_repo())._read_agent_fields(d, "translate")
    assert fields["agent_md"] == HR_SEED.strip()
    assert fields["persistent"] is False


def test_a_yaml_block_with_no_known_key_is_left_alone(tmp_path: Path) -> None:
    """``---\nnot a mapping\n---\nBody`` also parses to ``metadata={}``. The
    rule is the same: nothing recognised, nothing stripped."""
    raw = "---\nnot a mapping\n---\nBody\n"
    d = _seed(tmp_path, "translate", raw)
    fields = _loader(tmp_path, _agent_repo())._read_agent_fields(d, "translate")
    assert fields["agent_md"] == raw.strip()


def test_every_shipped_seed_keeps_its_prose(tmp_path: Path) -> None:
    """Lockstep over the real files: a seed that declares nothing must come
    back byte-for-byte, and one that declares something must differ from its
    raw text ONLY by its frontmatter block."""
    import frontmatter

    loader = _loader(tmp_path, _agent_repo())
    for d in sorted(SEEDS_AGENTS_DIR.iterdir()):
        if not d.is_dir() or not (d / "AGENT.md").exists():
            continue
        raw = (d / "AGENT.md").read_text().strip()
        fields = loader._read_agent_fields(d, d.name)
        if d.name in EXPECTED_PERSISTENT:
            assert fields["agent_md"] == frontmatter.loads(raw).content.strip()
            assert "persistent:" not in (fields["agent_md"] or "")
        else:
            assert fields["agent_md"] == raw, f"{d.name}: prose changed"


# ── lockstep with what 162 / 163 actually promoted ─────────────────────


def test_exactly_the_migrated_slugs_declare_persistent(tmp_path: Path) -> None:
    loader = _loader(tmp_path, _agent_repo())
    declared = {
        d.name
        for d in sorted(SEEDS_AGENTS_DIR.iterdir())
        if d.is_dir() and loader._read_agent_fields(d, d.name)["persistent"]
    }
    assert declared == EXPECTED_PERSISTENT
