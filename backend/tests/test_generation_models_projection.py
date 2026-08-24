"""The canvas model picker needs actual_provider to mark codex-local rows —
pin it into the public projection so it cannot silently drop out again
(the '后端返回了前端从没读' family, inverted: frontend reads what the
backend never sent)."""

from __future__ import annotations

from app.repositories.mediahub_model_repository import _PUBLIC_COLS


def test_public_projection_carries_actual_provider():
    names = {c.key for c in _PUBLIC_COLS}
    assert "actual_provider" in names
    # secrets must stay out
    assert "api_key" not in names
    assert "base_url" not in names
