"""Real-shaped ``/scripts/projects`` rows for route tests.

Built from the ORM mapper and passed through the repository's own
``_to_dict``, so a stub returns exactly what the live repository would:
every column, bigint ids as ints, timestamps as ISO strings. Tests that stub
a repository write use these instead of hand-written partial dicts, which the
routes' response models would (rightly) reject.
"""

from __future__ import annotations

from typing import Any

from app.models import ScriptAssets, ScriptChapters, ScriptProjects
from app.repositories.script_repository import (
    _ASSET_N2A,
    _CHAPTER_N2A,
    _PROJECT_N2A,
    _to_dict,
)
from tests.api.wire_parity import sample_orm


def script_project_row(**over: Any) -> dict:
    return _to_dict(sample_orm(ScriptProjects, **over), _PROJECT_N2A)


def script_chapter_row(**over: Any) -> dict:
    return _to_dict(sample_orm(ScriptChapters, **over), _CHAPTER_N2A)


def script_asset_row(**over: Any) -> dict:
    return _to_dict(sample_orm(ScriptAssets, **over), _ASSET_N2A)
