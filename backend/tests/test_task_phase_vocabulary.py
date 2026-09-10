"""守卫：``task_tracking.phase`` 的词汇只能有一份，且必须跟 DB trigger 对得上。

病史（2026-08-13，小红书绑号第二次断在同一个形状上）
====================================================
``task_tracking.phase`` 有**两个写入方，两套词汇**：

* ``mirror_dbos_lifecycle_to_tracking`` trigger（DB 侧，真相在
  ``supabase/schema_baseline.sql``）：``RUNNING → 'in_progress'``
* ``UnifiedTaskManager``（应用侧）：``start() → 'processing'``

DBOS 把 workflow 翻成 RUNNING、trigger 写下 ``in_progress``，紧接着 workflow
体调 ``manager.start()`` 覆写成 ``processing``。**跑着的任务实际就停在
``processing``**。而"这任务还活着吗"的判断在五个调用点各手写了一份字面量，
其中三处写的是 ``("queued", "in_progress")`` —— 永远匹配不到活任务：

* SMS 登录提交手机号/验证码必 409「Login task is no longer active」（用户侧
  表现为"本次登录已结束"，而后端会话活得好好的）
* ``/flows`` 级联取消找不到任何子任务，flow 标 cancelled 而子任务继续跑
* storage audit 去重永不命中，管理员双击就是两次全库扫描

``_get_phase`` 把不认识的 phase 静默降级成 QUEUED（连日志都没有），所以这个
分歧从来没有炸过 —— 它只是让守卫静默失效。**同族问题**：
``test_capability_matches_browser.py``（后端不得声明浏览器没实现的能力）、
「探针必须可证伪」。

本文件把纪律变成红 CI
=====================
1. 真相来源是**机器可查**的：真的去 ``schema_baseline.sql`` 里解析 trigger
   的 ``mapped_phase`` CASE 块，不是在测试里再抄一份字面量。用哪个文件的规则
   与 ``.github/workflows/schema-drift.yml`` 完全一致（baseline 打底，再叠
   watermark 之上的 migration）—— 将来有人用 migration 改了 trigger，这里跟着
   走，不会停在旧真相上。
2. AST 扫 ``backend/app/``，拒绝任何**残缺的活集合**字面量。判据不是
   "``in_progress`` 非法"（它是 trigger 的真值），而是"一个包含活 phase 的
   集合，必须是完整的活集合" —— 手写的那三处正是因为残缺才永不命中。

⚠️ 本文件读 SQL 文本，不连数据库：CI 上没有 prod 库，而这个对账必须在**每个
PR** 上跑，不是只在改 schema 时跑。baseline 是 prod schema 的 checked-in dump，
2026-08-13 已用 ``pg_get_functiondef`` 对生产库逐字核过 mapped_phase 块一致。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.services.infra.unified_task_manager import (
    _TERMINAL_PHASES,
    ACTIVE_PHASES,
    KNOWN_PHASES,
    MIRROR_ONLY_PHASES,
    TaskPhase,
)

pytestmark = pytest.mark.unit

# backend/tests/ → backend/ → 仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE = REPO_ROOT / "supabase" / "schema_baseline.sql"
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"
BACKEND_APP = REPO_ROOT / "backend" / "app"

TRIGGER_FN = "mirror_dbos_lifecycle_to_tracking"


# ─────────────────────────────────────────────────────────────────
# 真相来源：从 SQL 里解析 trigger 真正会写出的 phase
# ─────────────────────────────────────────────────────────────────


def _baseline_watermark() -> int:
    """``--   BASELINE WATERMARK: 364`` —— 与 schema-drift.yml 同一个解析口径。"""
    m = re.search(
        r"^--\s+BASELINE WATERMARK:\s*(\d+)\s*$",
        BASELINE.read_text(),
        re.MULTILINE,
    )
    assert m, (
        f"{BASELINE} 头部解析不出 'BASELINE WATERMARK: <n>'。schema-drift.yml 也靠"
        "它决定叠哪些 migration —— 头部坏了，这个守卫无从知道该信哪份 SQL。"
    )
    return int(m.group(1))


def _sql_sources_in_apply_order() -> list[Path]:
    """baseline 打底，再叠 watermark 之上的 forward migration（数字序）。"""
    watermark = _baseline_watermark()
    later = [
        p
        for p in MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")
        if not p.name.endswith("_rollback.sql")
        and int(p.name.split("_", 1)[0]) > watermark
    ]
    later.sort(key=lambda p: int(p.name.split("_", 1)[0]))
    return [BASELINE, *later]


def _latest_trigger_body() -> tuple[Path, str]:
    """最后一次定义 trigger 函数的那份 SQL 正文（含其后所有文本，足够解析）。"""
    pattern = re.compile(
        rf"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+public\.{TRIGGER_FN}\s*\(",
        re.IGNORECASE,
    )
    found: tuple[Path, str] | None = None
    for path in _sql_sources_in_apply_order():
        text = path.read_text()
        matches = list(pattern.finditer(text))
        if matches:
            found = (path, text[matches[-1].start() :])
    assert found is not None, (
        f"在 baseline + watermark 之上的 migration 里都找不到 {TRIGGER_FN} 的定义。"
        "要么函数改名了、要么真相搬了家 —— 两种情况都必须同步改这个守卫，"
        "而不是让它静默失效。"
    )
    return found


def _parse_phase_case(body: str) -> dict[str, str]:
    """解析 ``mapped_phase := CASE NEW.status WHEN 'X' THEN 'y' ... END;``。"""
    block = re.search(
        r"mapped_phase\s*:=\s*CASE\s+NEW\.status(.*?)\bEND\s*;",
        body,
        re.DOTALL | re.IGNORECASE,
    )
    assert block, (
        f"{TRIGGER_FN} 的最新定义里解析不到 mapped_phase 的 CASE 块。"
        "如果 trigger 真的不再镜像 phase 列了，那 ACTIVE_PHASES 的组成前提就变了 —— "
        "请连同 unified_task_manager 里的两写入方说明一起更新，别只删这个断言。"
    )
    return {
        dbos.upper(): phase
        for dbos, phase in re.findall(
            r"WHEN\s+'([A-Za-z_]+)'\s+THEN\s+'([a-z_]+)'", block.group(1)
        )
    }


def _parse_terminal_dbos_states(body: str) -> set[str]:
    """哪些 DBOS 状态算终态 —— 从 trigger 自己写 ``completed_at`` 的那段推。

    刻意不在这里手写 ``{"SUCCESS", "ERROR", ...}``：那就又是一份抄来的字面量，
    正是本文件要消灭的东西。trigger 只在终态盖 ``completed_at``，所以那张表就是
    它对"终态"的定义。
    """
    block = re.search(
        r"completed_at\s*=\s*CASE\s+WHEN\s+NEW\.status\s+IN\s*\((.*?)\)",
        body,
        re.DOTALL | re.IGNORECASE,
    )
    assert block, (
        f"{TRIGGER_FN} 里解析不到 completed_at 的终态列表 —— 本守卫靠它区分"
        "「活着的 DBOS 状态」和「终态」。"
    )
    return {s.upper() for s in re.findall(r"'([A-Za-z_]+)'", block.group(1))}


# ─────────────────────────────────────────────────────────────────
# 1. 词汇对账：trigger 写得出的，代码必须认得
# ─────────────────────────────────────────────────────────────────


def test_the_sql_truth_source_is_still_parseable():
    """先证明这份守卫不是空转的。

    下面两条都是"⊆"断言 —— 如果解析出空集，它们会**恒真**地绿掉，守卫看着还
    在其实早死了。这条把"真相读到了"本身变成断言。
    """
    path, body = _latest_trigger_body()
    phase_map = _parse_phase_case(body)
    terminal = _parse_terminal_dbos_states(body)

    assert len(phase_map) >= 5, f"{path} 里只解析出 {phase_map} —— 太少，解析大概坏了"
    assert terminal, f"{path} 里没解析出任何终态 DBOS 状态"
    # 活状态必须真的存在，否则第 3 条也会恒真。
    assert set(phase_map) - terminal, (
        f"{path}: 所有 DBOS 状态都被判成终态了，"
        "「活着的任务停在哪个 phase」这条断言会退化成空转"
    )


def test_every_phase_the_trigger_writes_is_a_phase_the_code_knows():
    """trigger 写得出的每一个 phase，都必须在 ``KNOWN_PHASES`` 里。

    trigger 多一个新词而 Python 侧没跟上 → 这条红。修法是往 ``TaskPhase`` 或
    ``MIRROR_ONLY_PHASES`` 里补，而不是把这条注掉。
    """
    path, body = _latest_trigger_body()
    written = set(_parse_phase_case(body).values())

    unknown = written - KNOWN_PHASES
    assert not unknown, (
        f"{path} 里的 {TRIGGER_FN} 会往 task_tracking.phase 写 {sorted(unknown)}，"
        f"而 Python 侧的 KNOWN_PHASES 是 {sorted(KNOWN_PHASES)}。"
        "读的人会撞上一个自己不认识的值 —— 而 _get_phase 会把它静默降级成 QUEUED，"
        "所以这条不红的话，没有任何地方会报错。"
    )


def test_every_phase_a_live_task_can_sit_at_is_in_active_phases():
    """**这条是核心**：两个写入方的非终态词汇，一个都不能漏出 ACTIVE_PHASES。

    漏一个的后果就是 2026-08-13 那个 bug —— 守卫看着有，实际永不命中。
    """
    path, body = _latest_trigger_body()
    phase_map = _parse_phase_case(body)
    terminal_dbos = _parse_terminal_dbos_states(body)

    # (a) trigger 侧：DBOS 还没走到终态时，它写下的 phase
    live_from_trigger = {
        phase for dbos, phase in phase_map.items() if dbos not in terminal_dbos
    }
    missing = live_from_trigger - ACTIVE_PHASES
    assert not missing, (
        f"{path}: DBOS 尚未终结时 trigger 会把 phase 写成 {sorted(missing)}，"
        f"但 ACTIVE_PHASES={sorted(ACTIVE_PHASES)} 不含它。"
        "任何「这任务还活着吗」的判断都会漏掉这些行。"
    )

    # (b) 应用侧：TaskPhase 枚举里的非终态
    live_from_manager = {p.value for p in TaskPhase if p not in _TERMINAL_PHASES}
    assert live_from_manager <= ACTIVE_PHASES

    # (c) 反向：ACTIVE_PHASES 不许混进终态，否则"放行活任务"会变成"全放行"
    assert not (ACTIVE_PHASES & {p.value for p in _TERMINAL_PHASES}), (
        f"ACTIVE_PHASES={sorted(ACTIVE_PHASES)} 里混进了终态 phase。"
        "登录端点会把已经释放浏览器 context 的任务当成活的转发进去。"
    )


def test_mirror_only_phases_are_actually_only_written_by_the_trigger():
    """``MIRROR_ONLY_PHASES`` 名副其实：trigger 写得出、而 ``TaskPhase`` 没有。

    有人把 ``in_progress`` 加进 ``TaskPhase`` 枚举（"统一一下词汇"）而没删这里
    的话，同一个词就有了两个来源 —— 正是本文件要防的形状。
    """
    path, body = _latest_trigger_body()
    written_by_trigger = set(_parse_phase_case(body).values())
    enum_values = {p.value for p in TaskPhase}

    assert not (MIRROR_ONLY_PHASES & enum_values), (
        f"MIRROR_ONLY_PHASES={sorted(MIRROR_ONLY_PHASES)} 与 TaskPhase 枚举重叠。"
        "重叠的词该留在枚举里，从 MIRROR_ONLY_PHASES 删掉。"
    )
    orphan = MIRROR_ONLY_PHASES - written_by_trigger
    assert not orphan, (
        f"MIRROR_ONLY_PHASES 里的 {sorted(orphan)} 已经没有任何写入方了"
        f"（{path} 的 {TRIGGER_FN} 不再写它）。删掉它，别让死词汇继续挂在活集合上。"
    )


# ─────────────────────────────────────────────────────────────────
# 2. AST 扫描：不许再手写"活着的 phase 集合"
# ─────────────────────────────────────────────────────────────────

# 允许保留手写字面量的地方 —— key 是 (相对路径, 该字面量的值集合)，
# value 是理由。加条目前先确认它比的**不是** DBOS workflow 的 phase 词汇。
_LITERAL_ALLOWLIST: dict[tuple[str, frozenset[str]], str] = {
    (
        "repositories/agent_workforce_repository.py",
        frozenset({"assigned", "in_progress"}),
    ): (
        "agent_task 的 8 状态 lifecycle（LIFECYCLE_TO_STATUS 的键），不是 DBOS "
        "workflow 那套 phase。同一个查询里 task_kind == 'agent_task' 已把行限定死，"
        "这类行的 phase 全由该文件写、trigger 不参与（migration 200）。"
        "换成 ACTIVE_PHASES 会漏掉 assigned、并误伤 waiting_for_other / blocked。"
    ),
    (
        "repositories/agent_workforce_repository.py",
        frozenset({"queued", "assigned", "in_progress"}),
    ): (
        "同上，是 agent_task 的 8 状态 lifecycle —— ``count_inflight_agent_tasks`` "
        "的 WHERE 里 task_kind == 'agent_task' 已把行限定死，而这套词汇里根本没有 "
        "'processing'（那是 DBOS 侧的值，由 trigger 写给 workflow 行）。"
        "口径是「没走完的」：queued 等着跑、assigned 已被 claim、in_progress 正在跑。"
        "**'assigned' 必须在内** —— worker 崩在 claim 与完成之间时，任务就停在 "
        "assigned；漏掉它，这个 gauge 恰好对唯一需要被看见的状态失明，读数会是"
        "「队列空了」。不含 waiting_for_other / blocked：那两个在等外部输入，"
        "不是「排队等执行」。"
    ),
}


def _string_seq(node: ast.AST) -> list[str] | None:
    """字面量的 list/tuple/set of str → 值列表；否则 None。"""
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        vals = [
            e.value
            for e in node.elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
        if vals and len(vals) == len(node.elts):
            return vals
    return None


def _is_phase_operand(node: ast.AST) -> bool:
    """左操作数看着是不是一个 phase 值。

    覆盖 ``TaskTracking.phase`` / ``row["phase"]`` / ``row.get("phase")`` /
    裸变量 ``phase``。最后那种正是 ``issue_status_for_phase`` 里出过事的写法。
    """
    if isinstance(node, ast.Attribute) and node.attr == "phase":
        return True
    if isinstance(node, ast.Name) and node.id == "phase":
        return True
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Constant)
        and node.slice.value == "phase"
    ):
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and bool(node.args)
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "phase"
    )


def _phase_comparisons(tree: ast.AST) -> list[tuple[int, list[str], bool]]:
    """(行号, 字面量值, 是不是单值相等比较) 三元组。"""
    out: list[tuple[int, list[str], bool]] = []
    for node in ast.walk(tree):
        # X.phase.in_([...])
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "in_"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "phase"
            and len(node.args) == 1
        ):
            vals = _string_seq(node.args[0])
            if vals:
                out.append((node.lineno, vals, False))
        # phase in/not in (...) | phase == "x"
        if isinstance(node, ast.Compare) and _is_phase_operand(node.left):
            for op, comparator in zip(node.ops, node.comparators):
                if isinstance(op, (ast.In, ast.NotIn)):
                    vals = _string_seq(comparator)
                    if vals:
                        out.append((node.lineno, vals, False))
                elif isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(
                    comparator, ast.Constant
                ):
                    if isinstance(comparator.value, str):
                        out.append((node.lineno, [comparator.value], True))
    return out


def _iter_app_sources():
    for path in sorted(BACKEND_APP.rglob("*.py")):
        yield path, ast.parse(path.read_text())


def test_no_hand_written_live_phase_set_survives_in_backend_app():
    """任何**包含**活 phase 的字面量集合，必须就是完整的活集合。

    判据刻意不是"``in_progress`` 非法" —— 它是 trigger 的真值。真正的缺陷是
    **残缺**：``("queued", "in_progress")`` 漏了 ``processing``，于是永不命中。
    要表达"这任务还活着吗"就引用 ``ACTIVE_PHASES`` / ``ACTIVE_PHASES_SQL``；
    要表达别的（某个具体阶段、终态集合）就不会碰到活 phase，这条自然放行。
    """
    violations: list[str] = []
    for path, tree in _iter_app_sources():
        rel = str(path.relative_to(BACKEND_APP))
        for lineno, vals, is_eq in _phase_comparisons(tree):
            if is_eq or len(vals) < 2:
                continue  # 单值比较由下一条用例负责
            values = set(vals)
            if not (values & ACTIVE_PHASES):
                continue  # 纯终态集合（如 mirror 的 unmirrored 过滤），与本条无关
            if values == ACTIVE_PHASES:
                continue  # 手写但完整 —— 允许，虽然引用常量更好
            if (rel, frozenset(values)) in _LITERAL_ALLOWLIST:
                continue
            violations.append(
                f"  {rel}:{lineno} → {sorted(values)}\n"
                f"      缺 {sorted(ACTIVE_PHASES - values)}"
            )

    assert not violations, (
        "下面这些地方手写了一份**残缺**的「活着的 phase」集合，它们匹配不到"
        "真正活着的任务（跑着的 workflow 停在 'processing'）：\n"
        + "\n".join(violations)
        + "\n\n改用 app.services.infra.unified_task_manager 的 ACTIVE_PHASES"
        "（Python 判断）或 ACTIVE_PHASES_SQL（.in_() 查询）。"
        "\n如果这里比的根本不是 DBOS workflow 的 phase 词汇（例如 agent_task 的"
        " 8 状态 lifecycle），把它连同理由加进 _LITERAL_ALLOWLIST。"
    )


def test_no_equality_check_against_a_mirror_only_phase():
    """``phase == 'in_progress'`` 这类单值判等永远是 bug。

    ``MIRROR_ONLY_PHASES`` 里的值是 trigger 写下、随即被 ``manager.start()``
    覆写的过渡值 —— 没有任何业务逻辑可以稳定地"等于"它。它只能作为活集合的一
    员被**包含**。``publish_issue_mirror.issue_status_for_phase`` 就是这么写的，
    那个分支从上线起没进去过。
    """
    violations: list[str] = []
    for path, tree in _iter_app_sources():
        rel = str(path.relative_to(BACKEND_APP))
        for lineno, vals, is_eq in _phase_comparisons(tree):
            if is_eq and vals[0] in MIRROR_ONLY_PHASES:
                violations.append(f"  {rel}:{lineno} → phase == {vals[0]!r}")

    assert not violations, (
        "这些地方拿一个「只有 trigger 写、且立刻被覆写」的 phase 做判等：\n"
        + "\n".join(violations)
        + f"\n\n{sorted(MIRROR_ONLY_PHASES)} 只能出现在活集合里（用 ACTIVE_PHASES）。"
        "要判「任务在跑」，比的是 ACTIVE_PHASES，不是某一个过渡词。"
    )


def test_the_ast_scanner_can_actually_see_a_violation():
    """扫描器自证：拿一段已知有病的源码喂进去，必须被认出来。

    没有这条，上面两条扫描用例在"选择器写错了、什么都没匹配到"时会一样绿。
    这就是 bug 本体的同一种失效方式，不能在守卫里重演一遍。
    """
    bad = (
        "sel.where(TaskTracking.phase.in_(['queued', 'in_progress']))\n"
        "if task['phase'] not in ('queued', 'in_progress'):\n"
        "    pass\n"
        "if phase == 'in_progress':\n"
        "    pass\n"
    )
    found = _phase_comparisons(ast.parse(bad))

    sets = [set(v) for _, v, is_eq in found if not is_eq]
    assert {"queued", "in_progress"} in sets, f"没认出 .in_() 形式：{found}"
    assert sets.count({"queued", "in_progress"}) == 2, f"没认出 not-in 形式：{found}"
    assert any(
        is_eq and v == ["in_progress"] for _, v, is_eq in found
    ), f"没认出单值判等形式：{found}"
    # 而且这些确实会被上面的判据判成违规
    assert {"queued", "in_progress"} != ACTIVE_PHASES
    assert {"queued", "in_progress"} & ACTIVE_PHASES
