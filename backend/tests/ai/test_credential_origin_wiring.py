"""``ResolvedAIConfig.origin`` 一直算得好好的，只是在进 ``RunRecorder`` 之前
被丢掉了（``ai_library_chat_wiring.py`` 只取 ``.provider_config``）。这一组钉住
它真的走完全程，以及五个 agent 栈调用点都把它填进 recorder。

同时钉住 ``RunRecorder`` 两个新字段的**默认值形状**：必须是简单默认值（→ 类属性），
不能是 ``default_factory``。全仓有大量 ``RunRecorder.__new__(...)`` 造的测试桩不
设这两个字段，它们靠类属性读到 None；换成 factory 会让那些测试整片 AttributeError。
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_BACKEND = Path(__file__).resolve().parents[2]

#: 五个 agent 栈调用点。前四个从 ``AgentRunnerStack.credential_origin`` 取，
#: 第五个（forced-declare）自己解析凭证，取法见该文件。
_WIRED = [
    "app/services/ai/chat/ai_library_chat_service.py",
    "app/services/chat/conversation_agent_turn.py",
    "app/services/workforce/agent_worker.py",
    "app/services/ai/runner/subagent_task_service.py",
    "app/services/ai/tools/forced_finish_declaration.py",
]


def _recorder_calls(path: Path) -> list[tuple[int, set[str]]]:
    """该文件里每一处 ``RunRecorder(...)`` 的 ``(行号, 关键字名集合)``。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "RunRecorder"
        ):
            out.append((node.lineno, {kw.arg for kw in node.keywords if kw.arg}))
    return out


def _recorder_kwargs(path: str) -> list[set[str]]:
    """该文件里每一处 ``RunRecorder(...)`` 的关键字名集合。"""
    return [kwargs for _lineno, kwargs in _recorder_calls(_BACKEND / path)]


def test_every_team_scoped_dispatch_anywhere_in_the_repo_stamps_the_origin():
    """上面那份清单是**手写**的，所以它只能证明清单里的五处接线了。这一条反过来
    扫全仓：凡是给 ``RunRecorder`` 传了 ``team_id`` 的调用点，都是会真的扣到某个
    团队头上的 run，必须同时说出这笔钱花的是谁的 key。

    没有 ``team_id`` 的站点（caption / classify / translate / summarize /
    visual / topic_scorer / agent_runner 的内部 recorder）不在此列 —— 它们不扣分，
    强求接线只会产出一堆为了过门禁而填的 ``None``。

    这条规则自己就抓到了 ``script_ai_service`` —— 它带 team_id、在清单之外、
    从未接过线，而它的 BYOK run 一直在被按平台价扣分。

    ⚠️ **覆盖边界**（照着它去信任这条守卫之前先读）：

    * 只匹配 ``ast.Name`` 形式的 ``RunRecorder(...)``。别名导入
      （``from ... import RunRecorder as R``）、属性调用
      （``rr.RunRecorder(...)``）、工厂函数里包一层、``**kwargs`` 展开 ——
      一律看不见。今天全仓是清一色的直接调用（上面的清单就是从这次扫描来
      的），所以够用；哪天有人换写法，这条守卫会**静默失效**而不是转红。
    * 只看 ``team_id`` / ``credential_origin`` 这两个关键字**在不在**，不看
      值。``credential_origin=None`` 照样过 —— 它挡的是「忘了接线」，不是
      「接了线但解析器返回空」。后者只有行为用例能证明（见本文件底下三条）。
    * 只扫 ``app/``。测试与脚本里的 ``RunRecorder(...)`` 不在此列。
    """
    offenders = []
    for path in sorted((_BACKEND / "app").rglob("*.py")):
        for lineno, kwargs in _recorder_calls(path):
            if "team_id" in kwargs and "credential_origin" not in kwargs:
                offenders.append(f"{path.relative_to(_BACKEND)}:{lineno}")
    assert not offenders, "带 team_id 却没有 credential_origin：" + ", ".join(offenders)


@pytest.mark.parametrize("path", _WIRED)
def test_every_agent_stack_dispatch_stamps_the_credential_origin(path):
    """漏一处的后果是静默的：那条链的 BYOK run 会被按平台价扣分，而所有测试照绿。"""
    calls = _recorder_kwargs(path)
    assert calls, f"{path} 里没有 RunRecorder(...)——调用点搬家了，更新本清单"
    for kwargs in calls:
        assert "credential_origin" in kwargs, f"{path}: {sorted(kwargs)}"


@pytest.mark.parametrize(
    "path",
    [
        "app/services/workforce/agent_worker.py",
        "app/services/ai/runner/subagent_task_service.py",
    ],
)
def test_the_two_child_dispatch_sites_hand_the_recorder_its_parent(path):
    """root 一次扣的判据是 ``self.parent_run_id is None``。这两处不填，
    每个子 run 都会自认 root 并按**整棵树**再扣一次。"""
    for kwargs in _recorder_kwargs(path):
        assert "parent_run_id" in kwargs, f"{path}: {sorted(kwargs)}"


def test_the_two_new_fields_are_plain_defaults_not_factories():
    from app.services.ai.runner.run_recorder import RunRecorder

    assert RunRecorder.credential_origin is None
    assert RunRecorder.parent_run_id is None


def test_the_chat_stack_carries_the_resolved_origin():
    """``AgentRunnerStack`` 是 wiring 与 recorder 之间唯一的传声筒。"""
    from app.services.ai.chat.ai_library_chat_wiring import AgentRunnerStack

    stack = AgentRunnerStack(
        runner=None,
        graph_facts=[],
        primary_model="m",
        fallback_chain_active=False,
        credential_origin="byok",
    )
    assert stack.credential_origin == "byok"


def _wire_forced_declare_resolver(monkeypatch, *, catalog_hit, chat_origin):
    """``_resolve_agent_and_adapter`` 把凭证解析全部 lazy import 进函数体，所以
    patch 的是**来源模块**上的名字，不是 ``fd`` 上的。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    import app.services.ai.adapters.factory as factory_mod
    import app.services.ai.chat.ai_library_chat_service as chat_mod
    import app.services.ai.chat.ai_library_chat_wiring as wiring_mod
    import app.services.ai.providers.ai_provider_helpers as helpers_mod
    from app.services.ai.tools import forced_finish_declaration as fd

    monkeypatch.setattr(
        chat_mod,
        "AILibraryChatService",
        lambda: SimpleNamespace(
            get_session=AsyncMock(return_value={"agent_slug": "issue_agent"})
        ),
    )
    monkeypatch.setattr(
        fd,
        "get_agent_repository",
        lambda: SimpleNamespace(
            get_by_slug=AsyncMock(
                return_value={"id": str(uuid4()), "slug": "issue_agent", "model": "m"}
            )
        ),
    )
    monkeypatch.setattr(
        helpers_mod,
        "resolve_nous_model",
        AsyncMock(
            return_value=(
                ("qwen", {"api_key": "k", "base_url": "u"}, "m")
                if catalog_hit
                else None
            )
        ),
    )
    monkeypatch.setattr(
        helpers_mod,
        "resolve_chat_config",
        AsyncMock(return_value=SimpleNamespace(provider_config={}, origin=chat_origin)),
    )
    monkeypatch.setattr(wiring_mod, "_load_user_provider_config", AsyncMock())
    monkeypatch.setattr(factory_mod, "resolve_provider_key", lambda *a: "qwen")
    monkeypatch.setattr(factory_mod, "get_adapter_for_key", lambda *a, **k: object())
    monkeypatch.setattr(factory_mod, "get_adapter_for_user", lambda *a, **k: object())
    return fd


async def test_a_catalog_hit_on_the_forced_declare_path_is_the_platform_paying(
    monkeypatch,
):
    """它不走 ``build_agent_runner_stack``，自己解析凭证。目录命中 = admin 凭证，
    所以这一条 run 的钱平台真付了，哪怕这个用户配了自己的 key。"""
    from uuid import uuid4

    fd = _wire_forced_declare_resolver(
        monkeypatch, catalog_hit=True, chat_origin="byok"
    )

    _agent, _adapter, _session, origin = await fd._resolve_agent_and_adapter(
        "s", str(uuid4())
    )

    assert origin == "platform"


async def test_a_catalog_miss_on_the_forced_declare_path_reports_the_resolved_origin(
    monkeypatch,
):
    """没命中目录才轮到用户的配置 —— origin 必须和 adapter 一起离开解析器，
    否则调用点只剩下一个无从追问来路的 adapter 对象，这条路的 BYOK run 就会
    被按平台价扣分。它带着 session 的 team_id，是真的会扣分的。"""
    from uuid import uuid4

    fd = _wire_forced_declare_resolver(
        monkeypatch, catalog_hit=False, chat_origin="byok"
    )

    _agent, _adapter, _session, origin = await fd._resolve_agent_and_adapter(
        "s", str(uuid4())
    )

    assert origin == "byok"


# ── C：真的跑一遍 build_agent_runner_stack ───────────────────────────────


async def test_the_real_stack_builder_hands_the_resolved_origin_to_the_stack(
    monkeypatch,
):
    """上面那条只造了一个 ``AgentRunnerStack``，它证明的是 dataclass 收得住这个
    字段，**不是** wiring 真的把 ``_chat_cfg.origin`` 填了进去 —— 把
    ``credential_origin=credential_origin`` 改成 ``None`` 它照样绿。

    这一条跑真的 ``build_agent_runner_stack``，只 mock 记忆召回、凭证解析与
    fallback 链三个外部边界，其余（hook 注册、Delegate/SubAgent 工具、
    AgentRunner）都真的构造。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock
    from uuid import uuid4

    import app.services.ai.chat.ai_library_chat_wiring as wiring_mod
    import app.services.ai.llm.fallback_wiring as fallback_mod
    import app.services.ai.providers.ai_provider_helpers as helpers_mod

    monkeypatch.setattr(
        wiring_mod, "_safe_recall_graph_facts", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        wiring_mod, "_safe_recall_honcho_context", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        wiring_mod, "_safe_recall_agent_memory", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        helpers_mod,
        "resolve_chat_config",
        AsyncMock(
            return_value=SimpleNamespace(
                provider_config={"qwen": {"api_key": "k"}}, origin="byok"
            )
        ),
    )
    monkeypatch.setattr(
        fallback_mod, "build_fallback_llm", AsyncMock(return_value=object())
    )

    user_id = uuid4()
    stack = await wiring_mod.build_agent_runner_stack(
        agent={"id": str(uuid4()), "slug": "script_ai", "model": "qwen-max"},
        skill_repo=MagicMock(),
        user_id=user_id,
        session_id=None,
        user_query="hi",
        settings=MagicMock(),
    )

    assert stack.credential_origin == "byok"


# ── A：script_ai 这条链（清单外唯一带 team_id 的站点）────────────────────


async def test_the_script_ai_dispatch_stamps_its_resolved_origin(monkeypatch):
    """``script_ai_service`` 带 ``team_id``（真的会扣分）却从未接过 origin ——
    根因是它的解析器曾是丢 origin 的 tuple shim（已删，换成
    ``resolve_script_ai_config``，七个生产调用点一起迁）。
    这一条钉住：调用方解析出的 origin 一路走到 ``RunRecorder``，且子 run 判据
    ``parent_run_id`` 显式为 None（这条链永远是树根）。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    import app.services.storyboard.script.script_ai_service as mod

    captured: dict = {}

    class _Rec:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def set_summaries(self, **kwargs):
            pass

    monkeypatch.setattr(mod, "RunRecorder", _Rec)
    monkeypatch.setattr(
        mod,
        "resolve_dispatch_scope",
        AsyncMock(
            return_value=SimpleNamespace(
                as_recorder_kwargs=lambda: {"project_id": None}
            )
        ),
    )
    monkeypatch.setattr(
        mod.ScriptAIService,
        "_build_composer",
        lambda self: SimpleNamespace(
            compose=AsyncMock(
                return_value=SimpleNamespace(model="qwen-max", agent_id=None)
            )
        ),
    )
    runner = MagicMock()
    runner.run_turn = AsyncMock(return_value={"content": "ok"})
    monkeypatch.setattr(
        mod.ScriptAIService, "_build_runner", AsyncMock(return_value=runner)
    )

    svc = mod.ScriptAIService(user_id=None, credential_origin="byok")
    out = await svc._run_agent(
        "do it",
        "content",
        user_id="11111111-1111-1111-1111-111111111111",
        team_id=7,
    )

    assert out == "ok"
    assert captured["credential_origin"] == "byok"
    assert captured["parent_run_id"] is None
