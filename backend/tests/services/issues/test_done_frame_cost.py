"""3c §4.2：done 帧带上这次回合的花费，刚结束的那条气泡不必再发一次 /runs/costs。

两个键**恒定存在**、读不到为 null——同 3b 的 ``seq`` / ``outputs``：有时缺席的字段会被
消费方读成 0，而 0 和「不知道」在钱上是两个答案。
"""

import pytest

pytestmark = pytest.mark.unit


async def _async(v):
    return v


class _Points:
    async def charged_points_for_references(self, *, reference_type, reference_ids):
        # 整棵树，不是 root 那一行（3c 终审 I2）：77 委派出去一条子 run 78，
        # 两条各有自己的 consume 流水，帧上的数是它们的合计。
        assert reference_type == "agent_run" and sorted(reference_ids) == ["77", "78"]
        return {"77": 0.62, "78": 0.20}


class _Trees:
    async def run_ids_in_trees(self, root_ids):
        assert root_ids == [77]
        return {"77": ["77", "78"]}


class _Repo:
    async def cost_rows_for_ids(self, ids):
        assert ids == [77]
        return [
            {
                "id": 77,
                "user_id": "u",
                "issue_id": 5,
                "cost_cents": 0.82,
                "model": "m",
                "status": "completed",
                "prompt_tokens": 1,
                "completion_tokens": 2,
            }
        ]


class _Boom:
    async def cost_rows_for_ids(self, ids):
        raise RuntimeError("db down")


def _patch(monkeypatch, repo, published):
    import app.repositories.agent_runs_repository as runs_mod
    import app.services.billing.run_tree_points as tree_mod
    from app.services.issues import issue_chat_stream as ics

    async def _fake_publish(issue_id, payload):
        published.append(payload)

    monkeypatch.setattr(ics, "_publish", _fake_publish)
    monkeypatch.setattr(ics, "_last_transcript_seq", lambda rid: _async(9))
    monkeypatch.setattr(ics, "_run_output_keys", lambda rid: _async([]))
    if repo is not None:
        monkeypatch.setattr(runs_mod, "get_agent_runs_repository", lambda: repo)
        # 积分那一侧现在走 ``charged_points_for_run_trees``，它在自己的模块里持有
        # 两个仓库工厂 —— 桩要打在那里，不是 ``app.repositories.*`` 的模块级名字上。
        monkeypatch.setattr(tree_mod, "get_agent_runs_repository", lambda: _Trees())
        monkeypatch.setattr(tree_mod, "get_points_repository", lambda: _Points())
    return ics


async def test_done_frame_carries_cost_and_points(monkeypatch):
    published: list[dict] = []
    ics = _patch(monkeypatch, _Repo(), published)

    await ics.publish_status(5, "done", run_id=77)

    assert published[-1]["cost_cents"] == 0.82
    # 0.62（root）+ 0.20（委派出去那条）—— 帧上的数回答的是「这次回合扣了多少」。
    assert published[-1]["charged_points"] == 0.82


async def test_done_frame_still_goes_out_when_both_reads_fail(monkeypatch):
    """一次遥测失败绝不该让状态帧发不出去（同 ``_last_transcript_seq`` 的规矩）。

    两个读**都**炸才两个键都是 null —— 只炸一个的情形由下面两条正交用例覆盖。
    """
    published: list[dict] = []

    class _BrokenPoints:
        async def charged_points_for_references(self, **_kw):
            raise RuntimeError("connection reset")

    import app.services.billing.run_tree_points as tree_mod

    ics = _patch(monkeypatch, _Boom(), published)
    monkeypatch.setattr(tree_mod, "get_points_repository", lambda: _BrokenPoints())

    await ics.publish_status(5, "done", run_id=77)

    assert published[-1]["phase"] == "done"
    assert published[-1]["cost_cents"] is None
    assert published[-1]["charged_points"] is None


async def test_running_frame_has_the_keys_as_null(monkeypatch):
    """``running`` 帧也带这两个键——消费方不必分两种形状去读。"""
    published: list[dict] = []
    ics = _patch(monkeypatch, None, published)

    await ics.publish_status(5, "running", run_id=None)

    assert published[-1]["cost_cents"] is None
    assert published[-1]["charged_points"] is None


async def test_a_run_nobody_billed_reports_cost_without_points(monkeypatch):
    """BYOK / 急停 / 零花费：花了钱但没有积分流水。``charged_points`` 是 None 而不是
    0——0 会把「没人收你的费」说成「收了你 0 分」，那是两个答案。"""
    published: list[dict] = []

    class _NoCharge:
        async def charged_points_for_references(self, **_kw):
            return {}

    import app.services.billing.run_tree_points as tree_mod

    ics = _patch(monkeypatch, _Repo(), published)
    monkeypatch.setattr(tree_mod, "get_points_repository", lambda: _NoCharge())

    await ics.publish_status(5, "done", run_id=77)

    assert published[-1]["cost_cents"] == 0.82
    assert published[-1]["charged_points"] is None


async def test_a_failed_points_read_keeps_the_cost_that_was_already_read(monkeypatch):
    """两个读是**正交**的：run 行读到了，积分行炸了，帧就该报出花费 + 「不知道扣没扣」。

    共用一个 ``try`` 会把已经读到的 ``cost_cents`` 一起丢掉 —— 一次积分故障把一个
    真花了钱的 run 画成「完全没有账」。口径同 ``issue_rollup._charged``：谁失败只
    空掉谁。
    """
    published: list[dict] = []

    class _BrokenPoints:
        async def charged_points_for_references(self, **_kw):
            raise RuntimeError("connection reset")

    import app.services.billing.run_tree_points as tree_mod

    ics = _patch(monkeypatch, _Repo(), published)
    monkeypatch.setattr(tree_mod, "get_points_repository", lambda: _BrokenPoints())

    await ics.publish_status(5, "done", run_id=77)

    assert published[-1]["cost_cents"] == 0.82
    assert published[-1]["charged_points"] is None


async def test_a_failed_run_row_read_still_reports_the_points(monkeypatch):
    """反向同理：积分账是另一张表，run 行读不到不该把它一起拖下水。"""
    published: list[dict] = []
    ics = _patch(monkeypatch, _Boom(), published)

    import app.services.billing.run_tree_points as tree_mod

    monkeypatch.setattr(tree_mod, "get_points_repository", lambda: _Points())

    await ics.publish_status(5, "done", run_id=77)

    assert published[-1]["cost_cents"] is None
    assert published[-1]["charged_points"] == 0.82
