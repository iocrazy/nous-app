"""The Generated inbox card's ``agent_run`` provenance line (3a).

Before this, every chat-generated image printed the same four words — "Chat
generation" — with no link. "Which run made this picture?" was a question the
inbox could not answer even though ``run_deliverables`` had held the answer
since the registry shipped.

The reverse lookup is best-effort by design: rows generated before the registry
existed are simply not in it, and a card with no provenance is normal history,
not a fault. It falls back to today's flat label and never raises.
"""

from __future__ import annotations

import importlib

import pytest

from app.services.library.generated_source import describe_source

service_mod = importlib.import_module("app.services.library.generated_inbox_service")

pytestmark = pytest.mark.unit

PROV = {
    "issue_id": "348087075560200",
    "issue_key": "MH-91",
    "run_id": "913402881190401",
    "step": 3,
    "agent_name": "Script Ai",
}


def test_agent_run_prints_the_agent_and_the_issue():
    s = describe_source(
        {"origin_kind": "agent_run", "id": "500"},
        canvas_names={},
        team_id="42",
        provenance=PROV,
    )
    assert s["label"] == "Script Ai · MH-91"
    assert s["deep_link"] == "/team/42/todolist/MH-91?step=3"
    assert s["issue_id"] == "348087075560200"
    assert s["run_id"] == "913402881190401"
    assert s["step"] == 3


def test_a_run_without_a_step_links_to_the_issue_without_an_anchor():
    s = describe_source(
        {"origin_kind": "agent_run"},
        canvas_names={},
        team_id="42",
        provenance={**PROV, "step": None},
    )
    assert s["deep_link"] == "/team/42/todolist/MH-91"


def test_a_run_with_no_issue_keeps_the_name_but_invents_no_url():
    s = describe_source(
        {"origin_kind": "agent_run"},
        canvas_names={},
        team_id="42",
        provenance={**PROV, "issue_id": None, "issue_key": None},
    )
    assert s["label"] == "Script Ai · Run"
    assert s["deep_link"] is None
    assert s["run_id"] == "913402881190401"


def test_an_unregistered_row_falls_back_to_todays_flat_label():
    s = describe_source({"origin_kind": "agent_run"}, canvas_names={}, team_id="42")
    assert s["label"] == "Chat generation"
    assert s["deep_link"] is None
    assert s["issue_id"] is None and s["run_id"] is None and s["step"] is None


def test_other_kinds_never_grow_a_provenance_line():
    """A canvas card's source is the canvas, not the run — handing it the same
    lookup would overwrite a working deep link with a worse one."""
    s = describe_source(
        {"origin_kind": "canvas_run", "canvas_id": "501"},
        canvas_names={"501": "EP1"},
        team_id="42",
        provenance=PROV,
    )
    assert s["label"] == "EP1 · Canvas"
    assert s["deep_link"] == "/team/42/canvas/501"


# ── service wiring ───────────────────────────────────────────────────────


class _Canvases:
    async def names_by_ids(self, ids):
        return {}


class _ProvRepo:
    def __init__(self, out=None, boom=False):
        self.out = out or {}
        self.boom = boom
        self.asked: list = []

    async def provenance_for(self, *, kind, ref_ids):
        self.asked.append((kind, list(ref_ids)))
        if self.boom:
            raise RuntimeError("registry unavailable")
        return self.out


def _row(**over):
    row = {
        # Strings, because that is what the repository really returns for
        # these two Snowflake BIGINT columns.
        "id": "500",
        "scope_id": "42",
        "media_kind": "image",
        "origin_kind": "agent_run",
        "prompt": "a wide shot",
        "created_at": "2026-09-11T00:00:00+00:00",
        "review_state": "unreviewed",
    }
    row.update(over)
    return row


@pytest.mark.asyncio
async def test_decorate_looks_provenance_up_once_for_the_whole_page(monkeypatch):
    repo = _ProvRepo({"500": PROV})
    monkeypatch.setattr(service_mod, "get_run_deliverables_repository", lambda: repo)
    svc = service_mod.GeneratedInboxService.__new__(service_mod.GeneratedInboxService)
    svc.canvases = _Canvases()

    items = await svc._decorate([_row(), _row(id="501")], "42")

    assert repo.asked == [("generated_media", ["500", "501"])]
    assert items[0]["source"]["label"] == "Script Ai · MH-91"
    # 501 is not in the registry — it keeps today's label rather than
    # borrowing 500's.
    assert items[1]["source"]["label"] == "Chat generation"


@pytest.mark.asyncio
async def test_decorate_does_not_ask_when_no_row_came_from_an_agent(monkeypatch):
    repo = _ProvRepo()
    monkeypatch.setattr(service_mod, "get_run_deliverables_repository", lambda: repo)
    svc = service_mod.GeneratedInboxService.__new__(service_mod.GeneratedInboxService)
    svc.canvases = _Canvases()

    await svc._decorate([_row(origin_kind="canvas_upload")], "42")
    assert repo.asked == []


@pytest.mark.asyncio
async def test_a_failing_lookup_still_returns_the_page(monkeypatch):
    """The registry is decoration on this path. A card with a flat label beats
    a 500 on the whole inbox."""
    repo = _ProvRepo(boom=True)
    monkeypatch.setattr(service_mod, "get_run_deliverables_repository", lambda: repo)
    svc = service_mod.GeneratedInboxService.__new__(service_mod.GeneratedInboxService)
    svc.canvases = _Canvases()

    items = await svc._decorate([_row()], "42")
    assert items[0]["source"]["label"] == "Chat generation"
