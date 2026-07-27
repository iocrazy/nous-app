"""Resources ORM model must map the negative-prompt columns (spec
2026-07-26-asset-prompt-management). Without this, the mapper-derived
_RESOURCES_NAME_TO_ATTR / _resources_row_to_dict in the repository
silently drop gen_prompt_negative / gen_prompt_negative_zh on every
read and write.
"""

from app.models.media import Resources


def test_negative_prompt_columns_are_mapped():
    cols = {c.key for c in Resources.__mapper__.column_attrs}
    assert "gen_prompt_negative" in cols
    assert "gen_prompt_negative_zh" in cols
