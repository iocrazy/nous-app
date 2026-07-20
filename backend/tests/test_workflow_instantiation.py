"""Project workflow instantiation — pure-unit boundary coverage (M1 PR-B B1).

``ProjectStageNodesRepository.instantiate_from_template`` is ORM-session-backed
(``write_scope``/``read_scope`` against the SQLAlchemy engine) — this worktree
has no ``SUPAVISOR_DATABASE_URL`` configured, so a live-DB harness is deferred,
matching the house convention already documented in
``tests/repositories/test_workflow_templates_repository.py`` ("a full
INTEGRATION harness that points write_scope at a test DB is deferred").

What's fully unit-testable without a database is the pure decision logic the
plan calls the "method matrix" (spec §2/§3, team-lead B1 note in
``project_stage_nodes_repository.py``): the live/ai/hybrid shortcut that flips
Canvas/Shooting's skip state over a template's ``skip_default``, plus the
date/uuid/serialization boundary helpers that guard the isoformat-string and
UUID-coercion footguns documented in CLAUDE.md.
"""

from __future__ import annotations

import datetime
import uuid

import pytest

from app.repositories.project_stage_nodes_repository import (
    _SLUG_CANVAS,
    _SLUG_SHOOTING,
    _as_uuid,
    _require_date,
    _resolve_skip,
    _s,
)


# ── method matrix: _resolve_skip (spec §2 — Canvas 常驻不可关, Shooting 按 method) ──


def test_canvas_never_skipped_regardless_of_method_or_default():
    for method in (None, "live", "ai", "hybrid"):
        for skip_default in (True, False):
            assert _resolve_skip(_SLUG_CANVAS, skip_default, method) is False


@pytest.mark.parametrize("method", ["live", "hybrid"])
def test_shooting_on_for_live_and_hybrid(method):
    # Regardless of the template's own skip_default, live/hybrid force it on.
    assert _resolve_skip(_SLUG_SHOOTING, True, method) is False
    assert _resolve_skip(_SLUG_SHOOTING, False, method) is False


def test_shooting_off_for_ai():
    assert _resolve_skip(_SLUG_SHOOTING, False, "ai") is True
    assert _resolve_skip(_SLUG_SHOOTING, True, "ai") is True


def test_shooting_falls_back_to_template_default_with_no_method():
    assert _resolve_skip(_SLUG_SHOOTING, True, None) is True
    assert _resolve_skip(_SLUG_SHOOTING, False, None) is False


@pytest.mark.parametrize("method", [None, "live", "ai", "hybrid"])
def test_other_nodes_always_keep_template_default(method):
    for slug in ("script", "storyboard", "voiceover", "editing", None):
        assert _resolve_skip(slug, True, method) is True
        assert _resolve_skip(slug, False, method) is False


# ── _as_uuid: owner/member id coercion ───────────────────────────────────────


def test_as_uuid_passes_through_uuid():
    u = uuid.uuid4()
    assert _as_uuid(u) is u


def test_as_uuid_coerces_str():
    u = uuid.uuid4()
    assert _as_uuid(str(u)) == u


def test_as_uuid_none_is_none():
    assert _as_uuid(None) is None


def test_as_uuid_rejects_garbage():
    with pytest.raises(ValueError):
        _as_uuid("not-a-uuid")


# ── _s: id/date stringification for the API boundary ─────────────────────────


def test_s_stringifies_uuid():
    u = uuid.uuid4()
    assert _s(u) == str(u)


def test_s_isoformats_date():
    d = datetime.date(2026, 7, 20)
    assert _s(d) == "2026-07-20"


def test_s_isoformats_datetime():
    dt = datetime.datetime(2026, 7, 20, 12, 0, tzinfo=datetime.timezone.utc)
    assert _s(dt) == dt.isoformat()


def test_s_passes_through_plain_values():
    assert _s("plain") == "plain"
    assert _s(7) == 7
    assert _s(None) is None


# ── _require_date: the isoformat-string DATE-bind guard ─────────────────────


def test_require_date_passes_through_date_object():
    d = datetime.date(2026, 7, 20)
    assert _require_date(d, "planned_due") is d


def test_require_date_none_is_none():
    assert _require_date(None, "planned_due") is None


def test_require_date_rejects_iso_string():
    with pytest.raises(TypeError, match="ISO string"):
        _require_date("2026-07-20", "planned_due")


def test_require_date_rejects_other_types():
    with pytest.raises(TypeError):
        _require_date(1721433600, "planned_due")
