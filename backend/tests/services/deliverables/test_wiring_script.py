"""接线三之章节：写一次 ⇒ 一行一事件。

分镜与场次的登记已经搬去 ``screenwriting_tools``（必须在 ``caller_scope``
之外，见 ``test_registration_outside_caller_scope.py`` 的 Critical 说明），
所以它们的用例也在那个文件里。这里只剩章节——它不走 ``caller_scope``。
"""

from types import SimpleNamespace  # noqa: F401  (helpers below)

# --------------------------------------------------------------------------
# 章节
# --------------------------------------------------------------------------


async def test_expand_chapter_registers_with_the_run_it_was_given(monkeypatch):
    import app.workflows.script_ai_workflows as wf

    calls: list[dict] = []
    _stub_chapter_service(monkeypatch, calls)

    await _call_step(
        wf.script_ai_expand_persist,
        chapter_id="5",
        html="<p>x</p>",
        run_id=777,
        turn=1,
        step=2,
    )

    assert calls[-1]["attributed_to_run_id"] == 777


async def test_create_branches_registers_each_new_chapter(monkeypatch):
    import app.workflows.script_ai_workflows as wf

    calls: list[dict] = []
    _stub_chapter_service(monkeypatch, calls)

    await _call_step(
        wf.script_ai_branches_persist,
        script_id="1",
        chapter_id="5",
        branch_type="choice",
        branches=[
            {"title": "A", "summary": "s", "branch_label": "1"},
            {"title": "B", "summary": "s", "branch_label": "2"},
        ],
        run_id=777,
        turn=1,
        step=2,
    )

    assert [c["attributed_to_run_id"] for c in calls] == [777, 777]


async def test_a_chapter_write_with_no_run_attributes_to_nobody(monkeypatch):
    """人手编辑章节走同样的 service 方法。默认 None ⇒ 登记口 no-op。"""
    import app.workflows.script_ai_workflows as wf

    calls: list[dict] = []
    _stub_chapter_service(monkeypatch, calls)

    await _call_step(wf.script_ai_expand_persist, chapter_id="5", html="<p>x</p>")

    assert calls[-1]["attributed_to_run_id"] is None


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


async def _call_step(step_fn, **kwargs):
    fn = getattr(step_fn, "__wrapped__", step_fn)
    return await fn(**kwargs)


def _stub_chapter_service(monkeypatch, calls):
    import app.services.storyboard.script.script_service as svc

    class _Repo:
        async def get_by_id(self, _cid):
            return {"position_x": 400, "position_y": 100}

    class _Svc:
        chapter_repo = _Repo()

        async def update_chapter(
            self, chapter_id, data, *, attributed_to_run_id=None, turn=None, step=None
        ):
            calls.append(
                {
                    "chapter_id": chapter_id,
                    "attributed_to_run_id": attributed_to_run_id,
                    "turn": turn,
                    "step": step,
                }
            )
            return {"id": chapter_id}

        async def create_chapter(
            self, script_id, data, *, attributed_to_run_id=None, turn=None, step=None
        ):
            calls.append(
                {
                    "script_id": script_id,
                    "attributed_to_run_id": attributed_to_run_id,
                    "turn": turn,
                    "step": step,
                }
            )
            return {"id": "new"}

    monkeypatch.setattr(svc, "ScriptService", _Svc)


async def test_the_chapter_service_actually_uses_the_attribution_it_is_given(
    monkeypatch,
):
    """跑真的 ``ScriptService``，不是桩。

    上面那两条只证明 workflow **传了**署名；把 service 里的
    ``attributed_to_run_id`` 换成 ``None``，它们照样绿——真正把它用出去的
    这一步没人看着。这条盯的就是那一段。
    """
    import app.services.deliverables.registry as registry
    from app.services.storyboard.script.script_service import ScriptService

    calls: list[dict] = []

    async def _spy(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(registry, "register_deliverable_best_effort", _spy)

    class _Repo:
        async def update(self, chapter_id, _data):
            return {"id": chapter_id, "title": "Act I"}

        async def create(self, data):
            return {"id": "new-1", "title": data.get("title")}

    svc = ScriptService()
    monkeypatch.setattr(svc, "chapter_repo", _Repo(), raising=False)

    await svc.update_chapter("5", {"content": "x"}, attributed_to_run_id=777, step=2)
    await svc.create_chapter("1", {"title": "B"}, attributed_to_run_id=777, step=3)

    assert [c["run_id"] for c in calls] == [777, 777]
    assert [c["kind"] for c in calls] == ["script_chapter", "script_chapter"]
    assert [c["ref_id"] for c in calls] == ["5", "new-1"]
    assert [c["step"] for c in calls] == [2, 3]


async def test_a_human_chapter_edit_registers_nothing(monkeypatch):
    """编辑器走同一个方法、不传署名 ⇒ 登记口拿到 None ⇒ no-op。"""
    import app.services.deliverables.registry as registry
    from app.services.storyboard.script.script_service import ScriptService

    calls: list[dict] = []

    async def _spy(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(registry, "register_deliverable_best_effort", _spy)

    class _Repo:
        async def update(self, chapter_id, _data):
            return {"id": chapter_id, "title": "Act I"}

    svc = ScriptService()
    monkeypatch.setattr(svc, "chapter_repo", _Repo(), raising=False)

    await svc.update_chapter("5", {"content": "x"})

    assert [c["run_id"] for c in calls] == [None]
