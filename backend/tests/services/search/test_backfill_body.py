"""回填算出的正文，必须和实时写方登记时交的 ``search_text`` 是同一串。

**这个文件存在的理由**，和隔壁 ``test_search_text_wiring.py`` 是同一条缝的另一
半：那边钉住「四个生产者都交了正文」，这边钉住「补交的那一份和他们交的长得一
样」。两侧写进的是 ``search_docs.body`` 同一列 —— 一旦分叉，同一版产出的正文取
决于它是被回填写进去的还是新写进去的，而表里的两行长得**一模一样**，没有任何
东西会说出这件事。

判别法很简单：回填不许有自己的渲染器。它调的必须是 ``diff.render_shot`` /
``diff.render_elements`` 这**同一对**函数对象，而不是一个「差不多的」实现。下面
第一组用例就是按这个判的 —— 断言的右边直接调那两个函数，所以哪天回填改成自己
拼字符串，等号两边就会不相等。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.deliverables.diff import (
    NO_LEDGER,
    NO_SNAPSHOT,
    NOT_FOUND,
    render_elements,
    render_shot,
)
from app.services.search import backfill as bf

# asyncio_mode = auto（pyproject）—— async 用例不必逐条挂 asyncio 标记，
# 而挂上去会让本文件里那条同步用例收到一条 PytestWarning。
pytestmark = pytest.mark.unit

_APP = Path(__file__).resolve().parents[3] / "app"

SHOT = {
    "shot_type": "WS",
    "camera_angle": "eye_level",
    "camera_movement": "static",
    "focal_length": "35mm",
    "lighting": "golden hour",
    "description": "A wide shot of the cafe at dusk.",
}
ELEMENTS = [
    {"type": "action", "text": "The door swings open."},
    {"type": "dialogue", "text": "You came back."},
]


def _rebuild_returns(monkeypatch, content, reason=None):
    async def fake(kind, ref_id, row):
        return content, reason

    monkeypatch.setattr(bf, "rebuild_content", fake)


def _row(kind: str, **over):
    return {
        "entity_id": f"{kind}:1:1",
        "kind": kind,
        "ref_id": "1",
        "version": 1,
        "ledger_ref": None,
        "created_at": None,
        **over,
    }


# ── 与实时写方同一个渲染器 ───────────────────────────────────────────────


async def test_a_shot_renders_through_the_very_function_the_tools_hand_over(
    monkeypatch,
):
    """``screenwriting_tools`` 登记时传的是 ``search_text=render_shot(shot)``。
    等号右边在这里直接调同一个函数 —— 回填哪天改成自己拼 ``key: value``，这条
    就红。"""
    _rebuild_returns(monkeypatch, dict(SHOT))
    body, reason = await bf.output_body(_row("script_shot"))
    assert reason is None
    assert body == render_shot(SHOT)
    # 顺带钉死那串的形状本身，免得 render_shot 与本测试一起被改成另一种拼法。
    assert body.splitlines()[0] == "shot_type: WS"


async def test_a_scene_renders_through_the_element_renderer(monkeypatch):
    _rebuild_returns(monkeypatch, [dict(e) for e in ELEMENTS])
    body, reason = await bf.output_body(_row("script_scene"))
    assert reason is None
    assert body == render_elements(ELEMENTS)
    assert body.splitlines()[0] == "action: The door swings open."


async def test_the_backfill_owns_no_renderer_of_its_own():
    """源码级的同一条：正文的拼装只许**引用** diff 的渲染器。

    行为用例只在 ``rebuild_content`` 被替身接管时跑过两个 kind；一个新 kind 顺手
    在回填里手写一段拼接，行为用例不会碰到它。"""
    src = (_APP / "services" / "search" / "backfill.py").read_text(encoding="utf-8")
    assert "render_shot" in src and "render_elements" in src
    # 更强的一条：重建出来的内容要**原样**交给 diff 的渲染器。中间插一层
    # （``render_shot(_tidy(content))``、或者自己 join 一串行）就是第二个渲染器
    # 的开头，而它写进的是同一列。
    assert "render_shot(content)" in src and "render_elements(content)" in src


# ── 重建不出来是一种回答，不是失败 ───────────────────────────────────────


async def test_a_chapter_has_no_ledger_and_says_so():
    """四类里唯一没有账本、至今也没有生产者的一类。``(None, NO_LEDGER)`` 让
    调用方记 ``unavailable``；返回空串会被记成 ``filled``，那是在说谎。"""
    assert await bf.output_body(_row("script_chapter")) == (None, NO_LEDGER)


async def test_an_unknown_kind_falls_into_the_same_arm():
    assert await bf.output_body(_row("something_new")) == (None, NO_LEDGER)


async def test_a_rebuild_that_cannot_answer_passes_its_reason_through(monkeypatch):
    """``rebuild_content`` 的原因码要原样透出去，不能被压成一个笼统的失败 ——
    「分镜行被删了」(NOT_FOUND) 与「这一版之前没有账本」(NO_SNAPSHOT) 是两件事。"""
    _rebuild_returns(monkeypatch, None, NOT_FOUND)
    assert await bf.output_body(_row("script_shot")) == (None, NOT_FOUND)
    _rebuild_returns(monkeypatch, None, NO_SNAPSHOT)
    assert await bf.output_body(_row("script_scene")) == (None, NO_SNAPSHOT)


async def test_a_shot_whose_fields_are_all_blank_is_unavailable_not_filled(monkeypatch):
    """六个字段全空时 ``render_shot`` 给一个空串。写空串与不写在库里没有区别
    （``fill_empty_body`` 的谓词和 partial 索引都按「空」处理），但把它记成
    ``filled`` 会让「这批到底补上了没有」这个问题答错。"""
    _rebuild_returns(monkeypatch, {k: None for k in SHOT})
    assert await bf.output_body(_row("script_shot")) == (None, bf.EMPTY)


async def test_media_takes_the_whole_prompt_column_the_registry_hands_over():
    """登记口传的是 ``search_text=origin.prompt``（标题才取首行）。回填只能去读
    那一列本身 —— 去读 title、或者去读 params 里的某个别名，都会让同一版媒体的
    正文取决于它是哪条路写进去的。"""
    src = (_APP / "services" / "search" / "backfill.py").read_text(encoding="utf-8")
    assert "GeneratedMedia.prompt" in src


# ── 四个计数器互不嵌套 ───────────────────────────────────────────────────


def test_stats_addition_returns_a_new_object():
    """``BackfillStats`` 是 frozen 值对象：加法产出新对象，不改原件。"""
    start = bf.BackfillStats()
    later = start.plus(scanned=1, filled=1)
    assert (start.scanned, start.filled) == (0, 0)
    assert (later.scanned, later.filled) == (1, 1)


class _Repo:
    """``fill_empty_body`` 的替身。``accepts`` 决定它是写成功还是被谓词拦下。"""

    def __init__(self, accepts: bool = True):
        self.accepts = accepts
        self.calls: list[tuple[str, str, str]] = []

    async def fill_empty_body(self, *, entity_kind, entity_id, body):
        self.calls.append((entity_kind, entity_id, body))
        return self.accepts


def _drive(monkeypatch, *, outputs, runs, repo):
    async def fake_outputs():
        return outputs

    async def fake_runs():
        return runs

    monkeypatch.setattr(bf, "_empty_output_rows", fake_outputs)
    monkeypatch.setattr(bf, "_empty_run_rows", fake_runs)
    monkeypatch.setattr(bf, "get_search_docs_repository", lambda: repo)


async def test_every_outcome_is_counted_on_its_own_axis(monkeypatch):
    """一批里四种结局同时发生，四个数各自独立 —— CLAUDE.md「正交的结果各自独立
    上报」。把 ``unavailable`` 折进 ``filled`` 的分支，就会让一次「一条都没重建
    出来」的运行读成一次干净的成功。"""
    _rebuild_returns(monkeypatch, dict(SHOT))
    repo = _Repo(accepts=True)
    _drive(
        monkeypatch,
        outputs=[_row("script_shot"), _row("script_chapter")],
        runs=[{"entity_id": "77", "output_summary": "wrote three shots"}],
        repo=repo,
    )
    stats = await bf.backfill_search_docs_bodies()
    assert (stats.scanned, stats.filled, stats.unavailable, stats.failed) == (
        3,
        2,
        1,
        0,
    )
    assert repo.calls == [
        ("output", "script_shot:1:1", render_shot(SHOT)),
        ("run", "77", "wrote three shots"),
    ]


async def test_a_row_the_live_writer_won_is_raced_not_filled(monkeypatch):
    """``fill_empty_body`` 回 False 表示 WHERE 里那条「只补空的」谓词拦下了它。
    那是实时写方抢先写了更新的内容 —— 正常结局，但**不是**一次回填写入。"""
    _rebuild_returns(monkeypatch, dict(SHOT))
    repo = _Repo(accepts=False)
    _drive(monkeypatch, outputs=[_row("script_shot")], runs=[], repo=repo)
    stats = await bf.backfill_search_docs_bodies()
    assert (stats.scanned, stats.filled, stats.raced) == (1, 0, 1)


async def test_one_exploding_row_does_not_take_the_batch_with_it(monkeypatch):
    """一行抛异常记 ``failed`` 并继续。整批中断会让后面每一行都停在空正文上，
    而运维看到的是一条 traceback、不是「还剩多少没补」。"""
    seen: list[str] = []

    async def fake(kind, ref_id, row):
        seen.append(ref_id)
        if ref_id == "boom":
            raise RuntimeError("ledger is on fire")
        return dict(SHOT), None

    monkeypatch.setattr(bf, "rebuild_content", fake)
    repo = _Repo(accepts=True)
    _drive(
        monkeypatch,
        outputs=[
            _row("script_shot", ref_id="boom", entity_id="script_shot:boom:1"),
            _row("script_shot", ref_id="ok", entity_id="script_shot:ok:1"),
        ],
        runs=[],
        repo=repo,
    )
    stats = await bf.backfill_search_docs_bodies()
    assert (stats.scanned, stats.filled, stats.failed) == (2, 1, 1)
    assert seen == ["boom", "ok"]


async def test_a_dry_run_rebuilds_for_real_and_writes_nothing(monkeypatch):
    """预演跳过的只有写。它要是连重建也跳过，预演的就是另一个程序 —— 而运维正是
    拿它的计数来决定要不要真跑。"""
    rebuilt: list[str] = []

    async def fake(kind, ref_id, row):
        rebuilt.append(ref_id)
        return dict(SHOT), None

    monkeypatch.setattr(bf, "rebuild_content", fake)
    repo = _Repo(accepts=True)
    _drive(monkeypatch, outputs=[_row("script_shot")], runs=[], repo=repo)
    stats = await bf.backfill_search_docs_bodies(dry_run=True)
    assert rebuilt == ["1"]
    assert stats.filled == 1
    assert repo.calls == []


async def test_a_run_row_carries_the_output_summary_verbatim(monkeypatch):
    """run 行的正文就是 ``agent_runs.output_summary``，同
    ``project_run_best_effort``。任何加工（截断、加前缀）都会让同一次运行的正文
    取决于它是哪条路写进去的。"""
    repo = _Repo(accepts=True)
    summary = "Rewrote scene 4 and regenerated two shots."
    _drive(
        monkeypatch,
        outputs=[],
        runs=[{"entity_id": "42", "output_summary": summary}],
        repo=repo,
    )
    await bf.backfill_search_docs_bodies()
    assert repo.calls == [("run", "42", summary)]
