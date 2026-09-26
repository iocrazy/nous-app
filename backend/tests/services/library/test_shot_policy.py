"""``services.library.shot_policy`` — the four automation keys, the
"is the visual provider local" question and the dispatch decision
(spec 2026-09-26 §3.1)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.library import shot_policy as mod
from app.services.library.embedding_spaces import ActiveSpaceUnknown, VisualSpaceError


class _Settings:
    def __init__(self, values=None, fail=None):
        self.values, self.fail = dict(values or {}), fail
        self.writes: list = []

    async def get_value(self, key):
        if self.fail is not None:
            raise self.fail
        return self.values.get(key)

    async def upsert_setting(self, key, value, updated_by):
        self.writes.append((key, value, updated_by))
        self.values[key] = value
        return {}


def _wire(monkeypatch, settings: _Settings):
    monkeypatch.setattr(mod, "get_system_settings_repository", lambda: settings)
    return settings


# ------------------------------------------------------------- parsing ----
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("always", "always"),
        ('"local_only"', "local_only"),
        (" OFF ", "off"),
        ("sometimes", "local_only"),
        (None, "local_only"),
        (3, "local_only"),
    ],
)
def test_parse_mode_whitelists_and_defaults(raw, expected):
    assert mod.parse_mode(raw, "local_only") == expected


@pytest.mark.parametrize(
    "raw,expected", [("7", 7), (7.9, 7), ("0", 1), ("500", 50), ("x", 5), (None, 5)]
)
def test_parse_bounded_int_clamps_and_defaults(raw, expected):
    assert mod.parse_bounded_int(raw, 5, 1, 50) == expected


def test_validate_policy_update_normalises_and_names_the_field():
    out = mod.validate_policy_update(
        {"auto_index": "Always", "backfill": None, "batch": "10", "daily_cap": 0}
    )
    assert out == {
        mod.AUTO_INDEX_SETTING: "always",
        mod.BACKFILL_BATCH_SETTING: 10,
        mod.BACKFILL_DAILY_CAP_SETTING: 0,
    }
    for field, bad in [
        ("auto_index", "maybe"),
        ("batch", 0),
        ("batch", 51),
        ("batch", True),
        ("daily_cap", -1),
        ("daily_cap", "lots"),
        ("colour", "red"),
    ]:
        with pytest.raises(mod.PolicyValueError) as exc:
            mod.validate_policy_update({field: bad})
        assert exc.value.field == field


# ------------------------------------------------------------- reading ----
@pytest.mark.asyncio
async def test_read_policy_defaults_when_absent_or_unreadable(monkeypatch):
    _wire(monkeypatch, _Settings())
    assert await mod.read_shots_policy() == mod.ShotsPolicy("local_only", "off", 5, 200)
    _wire(monkeypatch, _Settings(fail=RuntimeError("db down")))
    assert (await mod.read_shots_policy()).backfill == "off"


@pytest.mark.asyncio
async def test_read_policy_reads_the_four_keys(monkeypatch):
    _wire(
        monkeypatch,
        _Settings(
            {
                mod.AUTO_INDEX_SETTING: "always",
                mod.BACKFILL_SETTING: "local_only",
                mod.BACKFILL_BATCH_SETTING: "8",
                mod.BACKFILL_DAILY_CAP_SETTING: 0,
            }
        ),
    )
    assert await mod.read_shots_policy() == mod.ShotsPolicy(
        "always", "local_only", 8, 0
    )


@pytest.mark.asyncio
async def test_write_policy_validates_then_upserts_only_the_given_fields(
    monkeypatch,
):
    s = _wire(monkeypatch, _Settings())
    policy = await mod.write_shots_policy({"backfill": "always", "batch": 3}, "admin")
    assert s.writes == [
        (mod.BACKFILL_SETTING, "always", "admin"),
        (mod.BACKFILL_BATCH_SETTING, 3, "admin"),
    ]
    assert policy.backfill == "always" and policy.batch == 3
    with pytest.raises(mod.PolicyValueError):
        await mod.write_shots_policy({"batch": 99}, "admin")
    assert len(s.writes) == 2


# ------------------------------------------------------ provider locality ----
def _catalog(monkeypatch, row):
    repo = SimpleNamespace(get_by_name=AsyncMock(return_value=row))
    monkeypatch.setattr(
        "app.repositories.nous_model_repository.get_nous_model_repository",
        lambda: repo,
    )
    return repo


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,row,expected",
    [
        ("nous-wemm-embedding-2b", {"actual_provider": "nous"}, True),
        ("nous-doubao-embedding-vision", {"actual_provider": "ark"}, False),
        ("text-embedding-3", None, None),
        (None, None, None),
    ],
)
async def test_visual_provider_is_local(monkeypatch, name, row, expected):
    monkeypatch.setattr(mod, "visual_catalog_name", AsyncMock(return_value=name))
    _catalog(monkeypatch, row)
    assert await mod.visual_provider_is_local() is expected


@pytest.mark.asyncio
async def test_visual_provider_unknown_is_none(monkeypatch):
    monkeypatch.setattr(
        mod, "visual_catalog_name", AsyncMock(side_effect=ActiveSpaceUnknown("x"))
    )
    assert await mod.visual_provider_is_local() is None


@pytest.mark.parametrize(
    "mode,local,expected",
    [
        ("always", None, True),
        ("always", False, True),
        ("local_only", True, True),
        ("local_only", False, False),
        ("local_only", None, False),
        ("off", True, False),
    ],
)
def test_mode_allows(mode, local, expected):
    assert mod.mode_allows(mode, local) is expected


# ---------------------------------------------------------- the decision ----
def _decide(monkeypatch, *, policy, local, resolve=None):
    monkeypatch.setattr(mod, "read_shots_policy", AsyncMock(return_value=policy))
    monkeypatch.setattr(mod, "visual_provider_is_local", AsyncMock(return_value=local))
    monkeypatch.setattr(
        mod,
        "resolve_visual_space_and_embedder",
        AsyncMock(
            return_value=({"id": 42}, None) if resolve is None else None,
            side_effect=resolve,
        ),
    )


_P = mod.ShotsPolicy("local_only", "off", 5, 200)


@pytest.mark.asyncio
async def test_decision_local_only_dispatches_only_on_a_local_provider(monkeypatch):
    _decide(monkeypatch, policy=_P, local=True)
    d = await mod.dispatch_decision("auto_index")
    assert d.dispatch and d.reason == "provider_local" and d.space_id == 42
    _decide(monkeypatch, policy=_P, local=False)
    d = await mod.dispatch_decision("auto_index")
    assert not d.dispatch and d.reason == "provider_not_local" and d.space_id is None


@pytest.mark.asyncio
async def test_decision_off_and_always(monkeypatch):
    _decide(monkeypatch, policy=_P, local=True)
    assert (await mod.dispatch_decision("backfill")).reason == "policy_off"
    _decide(monkeypatch, policy=mod.ShotsPolicy("off", "always", 5, 0), local=False)
    d = await mod.dispatch_decision("backfill")
    assert d.dispatch and d.reason == "policy_always"


@pytest.mark.asyncio
async def test_the_shots_tag_overrides_the_policy(monkeypatch):
    _decide(monkeypatch, policy=mod.ShotsPolicy("off", "off", 5, 0), local=False)
    d = await mod.dispatch_decision("auto_index", force=True)
    assert d.dispatch and d.reason == "tag_shots"


@pytest.mark.asyncio
async def test_decision_never_dispatches_into_an_unresolved_space(monkeypatch):
    _decide(
        monkeypatch,
        policy=mod.ShotsPolicy("always", "always", 5, 0),
        local=True,
        resolve=VisualSpaceError("provider_no_image", "text only"),
    )
    d = await mod.dispatch_decision("auto_index")
    assert not d.dispatch
    assert d.reason == "visual_space_unresolved" and d.detail == "provider_no_image"


# ---------------------------------------------------------- sweeper state ----
def test_backfill_state_rolls_the_day(monkeypatch):
    monkeypatch.setattr(mod, "today_utc", lambda: "2026-09-26")
    same = mod.parse_backfill_state(
        {
            "day": "2026-09-26",
            "dispatched_today": 37,
            "last_tick": "t",
            "last_error": "",
        }
    )
    assert same.dispatched_today == 37 and same.last_tick == "t"
    assert same.last_error is None
    other = mod.parse_backfill_state('{"day": "2026-09-25", "dispatched_today": 37}')
    assert other.day == "2026-09-26" and other.dispatched_today == 0
    assert mod.parse_backfill_state("garbage").dispatched_today == 0
    assert mod.parse_backfill_state(None).day == "2026-09-26"


@pytest.mark.asyncio
async def test_backfill_state_round_trips_through_the_setting(monkeypatch):
    s = _wire(monkeypatch, _Settings())
    monkeypatch.setattr(mod, "today_utc", lambda: "2026-09-26")
    await mod.write_backfill_state(
        mod.BackfillState("2026-09-26", 3, last_tick="now", last_skip="backpressure")
    )
    assert s.writes[0][0] == mod.BACKFILL_STATE_SETTING
    assert s.writes[0][1]["dispatched_today"] == 3
    # updated_by is a uuid column; the sweeper is not a user (2026-09-26: a
    # string here made every production tick fail before it stamped anything).
    assert s.writes[0][2] is None
    got = await mod.read_backfill_state()
    assert got.dispatched_today == 3 and got.last_skip == "backpressure"
