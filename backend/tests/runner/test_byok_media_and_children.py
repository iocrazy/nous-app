"""BYOK 免扣的另两条道：生图与子 agent。

``by_child`` 与 ``by_child_byok`` 必须**同海拔**（都是整棵子树的合计）。
``_cost_cents_of`` 的 docstring 写明 ``by_child`` 存的是子树总额 —— 孙子的钱通过
子 run 自己的 ``spent_cents`` 已经含在里面，所以 BYOK 侧也必须报整棵子树，不能
只报「子 run 自身」的那部分。

⚠️ **``by_child_byok`` 当前没有任何消费方**（终审 I3）：扣费按**行**聚合，只读每条
run 自己的 ``own_cents`` / ``media_cents`` 减各自的 BYOK 道，``by_child*`` 两条都不
参与（见 ``ai/billing/tree_charge.py`` 模块 docstring）—— 子 run 的 BYOK 由它自己
那一行报。所以海拔对不上今天**不会多收钱**，只会让父行面板上的分解不自洽；两条道
仍要对齐，因为它们是同一个数的两半。本文件另一半（``media_*``）则**是**钱：那是
本行自己的分量，收口逐行读它。
"""

import pytest

pytestmark = pytest.mark.unit


def _views():
    from app.services.ai.runner.run_projection import empty_views

    return empty_views()


# ── deliverable（生图）────────────────────────────────────────────────


def test_a_byok_image_lands_in_the_media_byok_lane():
    from app.services.ai.runner.folds.deliverables import fold_deliverable

    views = _views()
    fold_deliverable(
        views,
        {
            "kind": "generated_media",
            "ref_id": "1",
            "version": 1,
            "cost_cents": 4.0,
            "byok_cents": 4.0,
        },
    )
    fold_deliverable(
        views,
        {"kind": "generated_media", "ref_id": "2", "version": 1, "cost_cents": 6.0},
    )
    cost = views["cost"]
    assert cost["media_cents"] == 10.0
    assert cost["media_byok_cents"] == 4.0
    # 真花了 10 分：BYOK 的钱用户真付了，预算门禁读的 spent_cents 不许缩水。
    assert cost["spent_cents"] == 10.0


def test_a_replayed_image_registration_does_not_double_the_byok_lane():
    """同一个 ``(kind, ref_id, version)`` 第二次到达：既不多计一件，也不多计一次钱，
    **两条道都不许多**（``media_cents`` 的去重键必须同时护住 ``media_byok_cents``）。"""
    from app.services.ai.runner.folds.deliverables import fold_deliverable

    views = _views()
    row = {
        "kind": "generated_media",
        "ref_id": "1",
        "version": 1,
        "cost_cents": 4.0,
        "byok_cents": 4.0,
    }
    fold_deliverable(views, row)
    fold_deliverable(views, dict(row))
    assert views["cost"]["media_cents"] == 4.0
    assert views["cost"]["media_byok_cents"] == 4.0


# ── subagent_done（子 agent）──────────────────────────────────────────


def test_a_childs_byok_total_is_set_not_added():
    """``subagent_done`` 可能到达不止一次（重放的 DBOS 步、views 重折）。
    与 ``by_child`` 同样是 SET 语义，否则父行的 BYOK 额随投递次数膨胀。"""
    from app.services.ai.runner.folds.subagents import fold_done

    views = _views()
    row = {"child_run_id": "c1", "mode": "async", "cost_cents": 9.0, "byok_cents": 6.0}
    fold_done(views, row)
    fold_done(views, dict(row))
    assert views["cost"]["by_child"] == {"c1": 9.0}
    assert views["cost"]["by_child_byok"] == {"c1": 6.0}
    assert views["cost"]["spent_cents"] == 9.0


def test_a_child_that_reports_no_byok_leaves_the_lane_empty():
    """键缺席 = 平台付的。不要写 0 —— 缺席与 0 在账上同值，但缺席还说明
    「这个子 run 的发射点根本没接线」，写 0 会把接线缺失伪装成结论。"""
    from app.services.ai.runner.folds.subagents import fold_done

    views = _views()
    fold_done(views, {"child_run_id": "c1", "mode": "sync", "cost_cents": 9.0})
    assert views["cost"]["by_child_byok"] == {}


# ── 两个发射点的取数函数同海拔 ─────────────────────────────────────────


class _Child:
    def __init__(self, cost):
        self.views = {"cost": cost}
        self.credential_origin = "byok"

    def compute_cost_cents(self):
        return 0.0


def test_the_two_emitters_report_the_same_altitude():
    """``_cost_cents_of`` 取整棵子树（``spent_cents``），所以 ``_byok_cents_of``
    也必须取整棵子树 = own_byok + media_byok + Σ by_child_byok。"""
    from app.services.ai.runner.subagent_task_service import (
        _byok_cents_of,
        _cost_cents_of,
    )

    child = _Child(
        {
            "spent_cents": 12.0,
            "own_cents": 5.0,
            "own_byok_cents": 5.0,
            "media_cents": 3.0,
            "media_byok_cents": 0.0,
            "by_child": {"g1": 4.0},
            "by_child_byok": {"g1": 4.0},
        }
    )
    assert _cost_cents_of(child) == 12.0
    assert _byok_cents_of(child) == 9.0  # 5 own + 0 media + 4 孙子


def test_a_child_with_no_step_folds_reports_byok_symmetrically():
    """``_cost_cents_of`` 在 ``spent_cents`` 为 0 时回落到 ``compute_cost_cents()``。
    BYOK 侧必须在**同一个**分支上回落，否则一条没有 step fold 的 BYOK 子 run 会
    报出「花了 7 分、BYOK 0 分」，父行照平台价收它。"""
    from app.services.ai.runner.subagent_task_service import (
        _byok_cents_of,
        _cost_cents_of,
    )

    class _NoFolds(_Child):
        def compute_cost_cents(self):
            return 7.0

    child = _NoFolds({"spent_cents": 0.0})
    assert _cost_cents_of(child) == 7.0
    assert _byok_cents_of(child) == 7.0

    platform = _NoFolds({"spent_cents": 0.0})
    platform.credential_origin = "platform"
    assert _byok_cents_of(platform) == 0.0


def test_a_recorder_that_cannot_answer_reports_zero_not_a_crash():
    from app.services.ai.runner.subagent_task_service import _byok_cents_of

    assert _byok_cents_of(None) == 0.0
    assert _byok_cents_of(object()) == 0.0


def test_a_non_string_credential_origin_is_not_byok():
    """MagicMock 造的 recorder 桩会让 ``credential_origin`` 变成一个非字符串对象。
    ``== "byok"`` 对它是 False，但先归一化才能保证任何 truthy 桩都不会被当成 BYOK。"""
    from app.services.ai.runner.subagent_task_service import _byok_cents_of

    class _Mocky(_Child):
        def compute_cost_cents(self):
            return 7.0

    child = _Mocky({"spent_cents": 0.0})
    child.credential_origin = object()
    assert _byok_cents_of(child) == 0.0


# ── 两个 envelope 的 byok_cents 键恒在 ────────────────────────────────


def test_the_failed_envelope_always_carries_the_key():
    """读方永远不必分辨「没花钱」与「没有这个字段」。"""
    from app.services.ai.runner.subagent_task_service import SubAgentTaskService

    env = SubAgentTaskService._failed("nope")
    assert env["cost_cents"] == 0.0
    assert env["byok_cents"] == 0.0


def test_the_success_envelope_reports_the_childs_byok_subtree():
    from app.services.ai.runner.subagent_task_service import SubAgentTaskService

    child = _Child(
        {
            "spent_cents": 12.0,
            "own_byok_cents": 5.0,
            "media_byok_cents": 0.0,
            "by_child_byok": {"g1": 4.0},
        }
    )
    env = SubAgentTaskService._build_envelope(
        result={"content": "ok"}, sub_run_id="9", recorder=child
    )
    assert env["cost_cents"] == 12.0
    assert env["byok_cents"] == 9.0


# ── 生图链把标记一路带到登记口 ──────────────────────────────────────────


def test_the_generation_origin_carries_the_byok_flag():
    from app.services.library.generated_media_service import GenerationOrigin

    assert GenerationOrigin(kind="agent_run").byok is False
    assert GenerationOrigin(kind="agent_run", byok=True).byok is True


def test_a_byok_row_stamps_the_flag_on_the_built_provider():
    """``resolve_image_provider`` 只返回 ``(provider, actual_model)``，所以 BYOK
    行的 ``source`` 从此贴在 provider 上 —— 与既有的 ``provider_key`` 同一个
    stamp 点。缺省仍是平台目录。"""
    from app.services.media.parsers.video_providers.db_registry import (
        _stamp_provider_key,
    )

    class _P:
        pass

    byok = _P()
    _stamp_provider_key(byok, "ark", source="byok")
    assert byok.provider_key == "ark"
    assert byok.is_byok is True

    catalog = _P()
    _stamp_provider_key(catalog, "ark")
    assert catalog.is_byok is False


@pytest.mark.asyncio
async def test_a_byok_image_provider_stamps_the_flag_on_the_result(monkeypatch):
    """``generate_image`` 的返回 dict 并列注入层标记，供 agent 工具读。

    ``model`` 是必填位置参数（真签名），``provider_name`` 走 in-proc registry 的
    KeyError 分支落到 DB 解析 —— 本仓 image registry 是空的，这条分支必走。"""
    from dataclasses import dataclass

    from app.services.ai.media import image_generation_service as svc

    @dataclass
    class _Result:
        image_url: str = "https://example.test/a.png"

    class _Provider:
        is_byok = True

        async def generate(self, *a, **k):
            return _Result()

    async def _resolve(name=None, *, user_id=None):
        return _Provider(), "doubao-seedream-4-0"

    monkeypatch.setattr(
        "app.services.media.parsers.video_providers.db_registry.resolve_image_provider",
        _resolve,
    )
    out = await svc.ImageGenerationService().generate_image(
        project_id="1",
        node_id="n1",
        prompt="a cat",
        model="dall-e-3",
        provider_name="ark",
        user_id="u1",
    )
    assert out["image_url"] == "https://example.test/a.png"
    assert out["byok"] is True


@pytest.mark.asyncio
async def test_a_catalog_image_provider_reports_no_byok(monkeypatch):
    """in-proc registry 那条分支的 provider 没有这个属性 → False。平台目录同理。"""
    from dataclasses import dataclass

    from app.services.ai.media import image_generation_service as svc

    @dataclass
    class _Result:
        image_url: str = "https://example.test/b.png"

    class _Provider:
        is_byok = False

        async def generate(self, *a, **k):
            return _Result()

    async def _resolve(name=None, *, user_id=None):
        return _Provider(), "doubao-seedream-4-0"

    monkeypatch.setattr(
        "app.services.media.parsers.video_providers.db_registry.resolve_image_provider",
        _resolve,
    )
    out = await svc.ImageGenerationService().generate_image(
        project_id="1",
        node_id="n1",
        prompt="a cat",
        model="dall-e-3",
        provider_name="ark",
        user_id="u1",
    )
    assert out["byok"] is False


def test_the_registration_lane_only_emits_the_key_when_it_is_byok():
    """``byok_cents`` 只进事件 payload。缺席 = 平台付的 —— 写 0 会把
    「这个登记口没接线」伪装成「平台付的」这个结论。"""
    import ast
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2] / "app/services/deliverables/registry.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)
    sig = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "register_deliverable"
    )
    assert "byok_cents" in {a.arg for a in sig.args.kwonlyargs}
    # 「``None`` 时整个键不出现」这条**行为**由下一条用例真跑着证 ——
    # 此处不再贴源码字面量：那种断言钉的是写法而不是结论，等价改写一次就红。


@pytest.mark.asyncio
async def test_a_platform_registration_puts_no_byok_key_in_the_event():
    """上一条是源码形状，这一条是真跑：不是 BYOK 时事件 payload 里**没有**
    这个键。缺席 = 平台付的。"""
    from unittest.mock import AsyncMock, patch

    from app.services.deliverables import registry as reg

    async def _run(byok_cents):
        seen = {}

        async def _emit(_rec, event_type, payload, **_k):
            seen["payload"] = payload
            return True

        row = reg.DeliverableRow(
            id="1",
            run_id="9",
            kind="generated_media",
            ref_id="4242",
            version=1,
            parent_version=None,
            title="t",
        )
        with (
            patch.object(reg, "_insert_next_version", AsyncMock(return_value=row)),
            patch.object(reg, "emit", _emit),
            patch.object(reg, "_stamp_seq", AsyncMock()),
            patch.object(reg, "_writer_for", AsyncMock(return_value=object())),
            patch(
                "app.services.search.projection.project_output_best_effort",
                AsyncMock(),
            ),
        ):
            await reg.register_deliverable(
                run_id="9",
                kind="generated_media",
                ref_id="4242",
                cost_cents=12.0,
                byok_cents=byok_cents,
            )
        return seen["payload"]

    platform = await _run(None)
    assert "byok_cents" not in platform
    assert platform["cost_cents"] == 12.0

    byok = await _run(12.0)
    assert byok["byok_cents"] == 12.0


@pytest.mark.asyncio
async def test_the_image_resolve_path_actually_passes_the_rows_tier(monkeypatch):
    """钉的是**调用点**，不是 ``_stamp_provider_key`` 自己。

    ``source`` 有默认值，所以把调用点改回 ``_stamp_provider_key(provider,
    actual_provider)`` 不会报错 —— 那条链的 ``is_byok`` 恒 False，BYOK 出的图
    静默按平台价收。上面那条 stamp 单测在这种改动下照绿，所以必须真的跑一遍
    ``resolve_image_provider``。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import app.services.ai.provider_protocols as protocols
    from app.services.media.parsers.video_providers import db_registry

    class _Built:
        pass

    built = _Built()
    monkeypatch.setattr(db_registry, "_enabled_rows", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        db_registry,
        "byok_image_rows",
        AsyncMock(
            return_value=[
                {
                    "name": "doubao-seedream-4-0",
                    "type": "image",
                    # BYOK 行**刻意**用协议名（与平台目录同键），所以目录价照样
                    # 命中 —— 层只能从 ``source`` 读，读不出别的地方。
                    "actual_provider": "ark",
                    "actual_model": "doubao-seedream-4-0",
                    "is_enabled": True,
                    "owner_user_id": "u1",
                    "source": "byok",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        protocols,
        "resolve_generation_protocol",
        lambda key: SimpleNamespace(
            generation_family="ark",
            build_image_provider=lambda row: (built, "doubao-seedream-4-0"),
        ),
    )

    provider, model = await db_registry.resolve_image_provider(
        "doubao-seedream-4-0", user_id="u1"
    )

    assert provider is built
    assert model == "doubao-seedream-4-0"
    assert provider.provider_key == "ark"
    assert provider.is_byok is True
