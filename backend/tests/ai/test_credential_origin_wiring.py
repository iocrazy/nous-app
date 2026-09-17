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


def _recorder_kwargs(path: str) -> list[set[str]]:
    """该文件里每一处 ``RunRecorder(...)`` 的关键字名集合。"""
    tree = ast.parse((_BACKEND / path).read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "RunRecorder"
        ):
            out.append({kw.arg for kw in node.keywords if kw.arg})
    return out


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
        "resolve_mediahub_model",
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
