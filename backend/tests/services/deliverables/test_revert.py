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


# ── 并发回退：唯一索引仲裁 → 类型化 409（fix 轮 1） ────────────────────────


def _unique_violation() -> Exception:
    """Postgres 唯一索引冲突，照 asyncpg 的形状——``IntegrityError.orig`` 带
    ``sqlstate``。用 23505 以外的码会让这一条走 raise 分支（见下面的负向对照）。"""
    from sqlalchemy.exc import IntegrityError

    return IntegrityError(
        "INSERT INTO run_deliverables",
        {},
        SimpleNamespace(sqlstate="23505"),
    )


async def test_losing_the_version_race_is_a_409_not_a_500(wired, monkeypatch):
    """事务内那次 latest 复查不加锁，两个并发回退可以双双通过它——最后由唯一索引
    仲裁。输家拿到的必须是「你慢了一步」而不是「服务坏了」：内容已经随事务回滚，
    重试一次就能成功，而 500 会让前端把它当故障报给用户。"""
    seen = []

    async def _boom(**kw):
        seen.append(kw)
        raise _unique_violation()

    async def _latest(*, kind, ref_id, session):
        # 事务内读到的还是 3（所以放行），事务外重读才看见对方刚落的 4。
        return 3 if session is not None else 4

    monkeypatch.setattr(mod, "register_deliverable", _boom)
    monkeypatch.setattr(mod, "_latest_version", _latest)
    with pytest.raises(HTTPException) as err:
        await mod.revert_output(
            kind="script_shot", ref_id="9", to_version=1, expected_latest=3, auth=AUTH
        )
    assert err.value.status_code == 409
    assert err.value.detail["code"] == "version_conflict"
    # 交回去的是**重读到的**真相，不是进来时那个陈旧的 latest。
    assert err.value.detail["latest_version"] == 4
    assert len(seen) == 1


async def test_a_check_violation_is_not_dressed_up_as_a_race(wired, monkeypatch):
    """负向对照：CHECK / 外键违例是真缺陷。把它也说成 409 等于用一句安抚盖掉一个
    bug——它必须原样穿出去，带栈变成 500。"""
    from sqlalchemy.exc import IntegrityError

    async def _boom(**kw):
        raise IntegrityError(
            "INSERT INTO run_deliverables",
            {},
            SimpleNamespace(sqlstate="23514"),  # check_violation
        )

    monkeypatch.setattr(mod, "register_deliverable", _boom)
    with pytest.raises(IntegrityError):
        await mod.revert_output(
            kind="script_shot", ref_id="9", to_version=1, expected_latest=3, auth=AUTH
        )


# ── 场次臂：保留未登记的编辑（fix 轮 1） ──────────────────────────────────
#
# 场次的账本是全的（每次编辑都过 apply_element_ops），但**登记只开给 agent 产出与
# 回退**——编辑器里改一句台词写了 script_ops、不占版本号。所以「当前内容 ≠ 最新
# 登记版内容」在这条臂上一样会发生，而且正是用户刚写的那段。


def _scene_ledger(count: int):
    """真的用 ``apply_ops`` 造账本行，inverse 由它自己算出来——手写逆操作等于把
    被测的那条重放路径换成另一套说法。"""
    from app.services.script.scene_ops import apply_ops

    rows, elements = [], []
    anchors = [None, "el_1", "el_2"]
    for seq in range(1, count + 1):
        ops = [
            {
                "op": "insert",
                "element_id": f"el_{seq}",
                "after_id": anchors[seq - 1],
                "payload": {"type": "action", "text": "ABC"[seq - 1]},
            }
        ]
        elements, inverse = apply_ops(elements, ops)
        rows.append({"op_seq": seq, "op_json": {"ops": ops, "inverse": inverse}})
    return rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """只需要回答一次 ``SELECT op_seq, op_json`` —— 场次臂在事务里读的就这一条。"""

    def __init__(self, ledger):
        self._ledger = ledger

    async def execute(self, _stmt):
        return _FakeResult([(r["op_seq"], r["op_json"]) for r in self._ledger])


@pytest.fixture
def wired_scene(monkeypatch):
    from app.repositories import script_scene_repository as repo_mod
    from app.services.script.version_service import replay_to

    st = SimpleNamespace(
        ledger=_scene_ledger(3),
        # 登记过的两版看到的水位：v2 是最新的一版。
        watermarks={1: "1", 2: "2"},
        applied=[],
        registered=[],
    )

    def _chain():
        return [
            {
                "id": str(700000000000000 + v),
                "run_id": "913402881190401",
                "kind": "script_scene",
                "ref_id": "7",
                "version": v,
                "issue_id": "348087075560200",
                "ledger_ref": st.watermarks[v],
                "created_at": f"2026-09-1{v}T00:00:00+00:00",
                "title": f"Scene {v}",
            }
            for v in (2, 1)
        ]

    async def _chain_of(kind, ref_id, auth):
        return _chain()

    async def _ok(*a, **k):
        return None

    async def _rebuild(kind, ref_id, row):
        """与真 ``rebuild_content`` 的场次分支同一条路：按这一版的水位重放账本。"""
        return replay_to(st.ledger, int(row["ledger_ref"])), None

    async def _register(**kw):
        st.registered.append(kw)
        return SimpleNamespace(
            id="7100",
            version=2 + len(st.registered),
            run_id=None,
            kind=kw["kind"],
            ref_id=kw["ref_id"],
            parent_version=None,
            title=kw.get("title"),
            actor_user_id=kw.get("actor_user_id"),
            reverted_from_version=kw.get("reverted_from_version"),
        )

    async def _latest(*, kind, ref_id, session):
        return 2

    class _FakeUowWithSession:
        async def __aenter__(self):
            return _FakeSession(st.ledger)

        async def __aexit__(self, *exc):
            return False

    class _SceneRepo:
        async def apply_element_ops(self, scene_id, ops, expected_version, actor):
            st.applied.append((scene_id, ops, expected_version, actor))
            return {"content_version": expected_version + 1}

    monkeypatch.setattr(mod, "visible_chain", _chain_of)
    monkeypatch.setattr(mod, "verify_scene_access", _ok)
    monkeypatch.setattr(mod, "rebuild_content", _rebuild)
    monkeypatch.setattr(mod, "register_deliverable", _register)
    monkeypatch.setattr(mod, "_latest_version", _latest)
    monkeypatch.setattr(mod, "unit_of_work", _FakeUowWithSession)
    monkeypatch.setattr(repo_mod, "get_script_scene_repository", lambda: _SceneRepo())
    return st


async def test_a_scene_edit_made_after_the_last_registered_version_is_kept(wired_scene):
    """账本走到 op_seq 3，最新登记版只看到 2 —— 那第三笔是用户在编辑器里写的。
    不保留它，一次回退就把它冲掉且再也指认不出来。"""
    out = await mod.revert_output(
        kind="script_scene", ref_id="7", to_version=1, expected_latest=2, auth=AUTH
    )

    assert out.kept_version is not None and len(wired_scene.registered) == 2
    kept, reverted = wired_scene.registered
    # 保留版**不写新账本**：场次的账本本来就是全的，缺的只是「看到哪个水位」。
    assert kept["ledger_ref"] == "3" and kept["reverted_from_version"] is None
    assert kept["actor_user_id"] == ME
    assert reverted["reverted_from_version"] == 1
    # 保留版在回退版之前落号（先保留，后回退）。
    assert out.version["version"] > out.kept_version["version"]
    # 逆操作批把 3 和 2 撤掉，回到 v1 的内容。
    (_scene, ops, expected, actor) = wired_scene.applied[0]
    assert actor == f"revert:{ME}" and expected == 3
    assert [o["element_id"] for o in ops] == ["el_3", "el_2"]


async def test_a_scene_with_nothing_newer_than_its_latest_version_keeps_nothing(
    wired_scene,
):
    """没有水位之后的编辑 → 当前内容 == 最新登记版内容 → 只登记回退那一版。"""
    wired_scene.watermarks[2] = "3"

    out = await mod.revert_output(
        kind="script_scene", ref_id="7", to_version=1, expected_latest=2, auth=AUTH
    )

    assert out.kept_version is None and len(wired_scene.registered) == 1
    assert wired_scene.registered[0]["reverted_from_version"] == 1
