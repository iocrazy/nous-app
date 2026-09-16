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
        assert reference_type == "agent_run" and reference_ids == ["77"]
        return {"77": 0.82}


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
    import app.repositories.points_repository as points_mod
    from app.services.issues import issue_chat_stream as ics

    async def _fake_publish(issue_id, payload):
        published.append(payload)

    monkeypatch.setattr(ics, "_publish", _fake_publish)
    monkeypatch.setattr(ics, "_last_transcript_seq", lambda rid: _async(9))
    monkeypatch.setattr(ics, "_run_output_keys", lambda rid: _async([]))
    if repo is not None:
        monkeypatch.setattr(runs_mod, "get_agent_runs_repository", lambda: repo)
        monkeypatch.setattr(points_mod, "get_points_repository", lambda: _Points())
    return ics


async def test_done_frame_carries_cost_and_points(monkeypatch):
    published: list[dict] = []
    ics = _patch(monkeypatch, _Repo(), published)

    await ics.publish_status(5, "done", run_id=77)

    assert published[-1]["cost_cents"] == 0.82
    assert published[-1]["charged_points"] == 0.82


async def test_done_frame_still_goes_out_when_the_cost_read_fails(monkeypatch):
    """一次遥测失败绝不该让状态帧发不出去（同 ``_last_transcript_seq`` 的规矩）。"""
    published: list[dict] = []
    ics = _patch(monkeypatch, _Boom(), published)

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

    import app.repositories.points_repository as points_mod

    ics = _patch(monkeypatch, _Repo(), published)
    monkeypatch.setattr(points_mod, "get_points_repository", lambda: _NoCharge())

    await ics.publish_status(5, "done", run_id=77)

    assert published[-1]["cost_cents"] == 0.82
    assert published[-1]["charged_points"] is None
