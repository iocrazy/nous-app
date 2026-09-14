"""回退（spec §2.3）。四条纪律：只回可回的（媒体恒 v1、章节无账本 → 400，不假装
成功）；expected_latest 是乐观锁（409 并把 latest 交回去）；永不销毁内容（当前内容
≠ 最新登记版重建内容 → 先把当前登记成一版）；三步一个事务（内容 / 账本 / 登记一起
成立或一起不成立，登记在事务外会留下「内容回了、版本没记」）。"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services.deliverables import revert as mod

pytestmark = pytest.mark.unit
ME = "11111111-1111-1111-1111-111111111111"
AUTH = SimpleNamespace(user_id=ME)


class _FakeUow:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc):
        return False


def _chain(*versions):
    return [
        {
            "id": str(700000000000000 + v),
            "run_id": "913402881190401",
            "kind": "script_shot",
            "ref_id": "9",
            "version": v,
            "issue_id": "348087075560200",
            "ledger_ref": str(100 + v),
            "created_at": f"2026-09-1{v}T00:00:00+00:00",
            "title": f"S1 · Shot {v}",
        }
        for v in versions
    ]


@pytest.fixture
def wired(monkeypatch):
    """把 revert 的六个外部面全换成桩：可见性、写权限、内容重建、读/写分镜、登记口、
    事务。断言的是「我们发出去的东西」，真执行留给 Task 8 的真栈验收。"""
    st = SimpleNamespace(
        chain=_chain(3, 2, 1),
        current={"shot_type": "WS", "description": "v3 text"},
        rebuilt={
            3: {"shot_type": "WS", "description": "v3 text"},
            1: {"shot_type": "WS", "description": "v1 text"},
        },
        applied=[],
        registered=[],
    )

    async def _chain_of(kind, ref_id, auth):
        return st.chain

    async def _ok(*a, **k):
        return None

    async def _rebuild(kind, ref_id, row):
        return st.rebuilt.get(row["version"]), None

    async def _read(ref_id, session):
        return st.current

    async def _write(ref_id, fields, *, actor, session, before):
        st.applied.append((fields, actor))
        return "9999"

    async def _register(**kw):
        st.registered.append(kw)
        return SimpleNamespace(
            id="7000",
            version=3 + len(st.registered),
            run_id=None,
            kind=kw["kind"],
            ref_id=kw["ref_id"],
            parent_version=None,
            title=kw.get("title"),
            actor_user_id=kw.get("actor_user_id"),
            reverted_from_version=kw.get("reverted_from_version"),
        )

    async def _latest(*, kind, ref_id, session):
        return 3

    monkeypatch.setattr(mod, "visible_chain", _chain_of)
    monkeypatch.setattr(mod, "verify_shot_access", _ok)
    monkeypatch.setattr(mod, "rebuild_content", _rebuild)
    monkeypatch.setattr(mod, "_read_shot_fields", _read)
    monkeypatch.setattr(mod, "_write_shot_fields", _write)
    monkeypatch.setattr(mod, "register_deliverable", _register)
    monkeypatch.setattr(mod, "_latest_version", _latest)
    monkeypatch.setattr(mod, "unit_of_work", _FakeUow)
    return st


async def test_media_is_not_revertible(wired):
    with pytest.raises(HTTPException) as err:
        await mod.revert_output(
            kind="generated_media",
            ref_id="5",
            to_version=1,
            expected_latest=1,
            auth=AUTH,
        )
    assert err.value.status_code == 400
    assert err.value.detail["code"] == "kind_not_revertible"


async def test_a_stale_expected_latest_is_a_conflict_carrying_the_truth(wired):
    with pytest.raises(HTTPException) as err:
        await mod.revert_output(
            kind="script_shot", ref_id="9", to_version=1, expected_latest=2, auth=AUTH
        )
    assert err.value.status_code == 409
    assert err.value.detail["code"] == "version_conflict"
    assert err.value.detail["latest_version"] == 3


async def test_an_unknown_target_version_is_a_404(wired):
    with pytest.raises(HTTPException) as err:
        await mod.revert_output(
            kind="script_shot", ref_id="9", to_version=9, expected_latest=3, auth=AUTH
        )
    assert err.value.detail["code"] == "version_not_found"


async def test_a_clean_revert_writes_six_fields_and_registers_one_version(wired):
    out = await mod.revert_output(
        kind="script_shot", ref_id="9", to_version=1, expected_latest=3, auth=AUTH
    )
    assert wired.applied == [
        ({"shot_type": "WS", "description": "v1 text"}, f"revert:{ME}")
    ]
    assert out.kept_version is None and len(wired.registered) == 1
    reg = wired.registered[0]
    assert reg["run_id"] is None and reg["actor_user_id"] == ME
    assert reg["reverted_from_version"] == 1
    assert reg["ledger_ref"] == "9999"  # 刚写的那行账本，不是目标版的
    assert reg["session"] is not None  # 与内容同一个事务


async def test_unregistered_manual_edits_are_kept_as_their_own_version(wired):
    """当前内容 ≠ 最新登记版重建内容 → 先登记当前，再回退。回退前的人手状态永远可回。"""
    wired.current = {"shot_type": "WS", "description": "hand-typed"}
    out = await mod.revert_output(
        kind="script_shot", ref_id="9", to_version=1, expected_latest=3, auth=AUTH
    )
    assert out.kept_version is not None and len(wired.registered) == 2
    kept, reverted = wired.registered
    assert kept["reverted_from_version"] is None and kept["actor_user_id"] == ME
    assert reverted["reverted_from_version"] == 1
    # 保留版也要有自己的账本行，否则它以后重建不出来。
    assert wired.applied[0][1] == f"keep:{ME}"


async def test_content_that_cannot_be_rebuilt_refuses_instead_of_blanking(
    wired, monkeypatch
):
    async def _unavailable(kind, ref_id, row):
        return None, "no_ledger"

    monkeypatch.setattr(mod, "rebuild_content", _unavailable)
    with pytest.raises(HTTPException) as err:
        await mod.revert_output(
            kind="script_shot", ref_id="9", to_version=1, expected_latest=3, auth=AUTH
        )
    assert err.value.status_code == 409
    assert err.value.detail["code"] == "content_unavailable"
    assert err.value.detail["reason"] == "no_ledger"
    assert wired.applied == [] and wired.registered == []
