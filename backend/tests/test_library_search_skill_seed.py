"""The library-search skill seed is complete: the loader reads these five
frontmatter keys, and SKILL.md points the model at references/examples.md."""

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

SKILL_DIR = Path(__file__).resolve().parents[1] / "seeds" / "skills" / "library-search"


def test_frontmatter_has_the_five_keys() -> None:
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    fm = yaml.safe_load(text.split("---\n", 2)[1])
    for key in ("name", "description", "category", "icon", "is_public"):
        assert fm.get(key) not in (None, ""), key
    assert fm["is_public"] is True
    body = text.split("---\n", 2)[2]
    assert "LibrarySearch" in body and "references/examples.md" in body


def test_examples_reference_exists_and_is_not_empty() -> None:
    examples = SKILL_DIR / "references" / "examples.md"
    assert examples.is_file()
    assert examples.read_text(encoding="utf-8").strip()
