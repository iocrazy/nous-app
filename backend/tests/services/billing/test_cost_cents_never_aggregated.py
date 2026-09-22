"""四条源码扫描守卫，钉住 mig 479 之后的四条口径。

1. **``agent_runs.cost_cents`` 是展示列，不许进聚合。** 它是「自身 + 已报到的
   后代」，同一棵树上父行与子行各自都含着同一笔钱 —— 按它 SUM 就是把委派链上
   每一层重复计一遍。聚合读面（议题预算 ``prior``、效率账、树总额）一律读
   ``own_cost_cents``（自身 + media，无后代）。Task 3–5 已经把读面全换完，这里
   只负责让它换不回去。
2. **「自身花费 = own + media」这条公式只许有一份实现。**
   ``tree_charge.spend_of_run`` 是那一份；别处再写一遍加法，就是第二个会漂的
   定义（钳位、非数字读作 0 这些约定都在 ``spend_of_run`` 里，抄一遍的人不会
   连约定一起抄）。
3. **Python 侧把行上的 ``cost_cents`` 加起来的地方，必须逐个登记。** 求和从 SQL
   挪到 Python 不会改变它的错法 —— ``for r in rows: total += r["cost_cents"]``
   与 ``SUM(cost_cents)`` 是同一个双计。见下面 ``PY_ROLLUP_ALLOWLIST`` 的长注释：
   这条为什么只能是登记制，而不能像守卫 1 那样直接拦。
4. **把老列放进 select 参数位（裸投影）的地方，也必须逐个登记。** 裸投影本身合法
   —— 那一列就是行上的展示值。但它一旦流出仓库，行里那个 ``cost_cents`` 键与
   ``own_cost_cents.label("cost_cents")`` 送出去的键**逐字一样**，于是守卫 3 在消
   费端分不清自己看的是哪一列，而守卫 1 只认聚合形态、别名禁令只认 ``.label()``
   —— 三条都看不见这一族写法。见 ``BARE_PROJECTION_ALLOWLIST``。

四条都是**源码扫描**，所以它们不看行为、只看写法 —— 能绕过（换个变量名、拼
字符串）。它们要拦的不是恶意，是「照着旁边那行抄一个聚合」这种复发。

⚠️ **它们扫的文本不一样，这是故意的：**

* 守卫 1 扫**原样源码**。它要拦的写法之一是裸 SQL 的 ``SUM(cost_cents)``，而裸
  SQL 只存在于字符串字面量里 —— 把字符串抹掉，那条模式就永远不会命中，守卫
  等于只剩三分之二。代价是注释里写 ``SUM(cost_cents)`` 也会被拦；那种注释本身
  就该改，不算误报。
* 守卫 2 扫**抹掉注释与字符串之后**的源码（``_code_only``）。描述这条公式的散文
  到处都是（``models/agents.py`` 的列注释、``budget_hook`` 的模块 docstring、
  ``recompute_spent`` 的 docstring），拦它们等于逼着大家不许把公式写进文档。
* 守卫 3 同守卫 1 扫**原样源码**，理由一样：它找的是字典键 ``r["cost_cents"]``，
  而键名就是字符串字面量。
* 守卫 4 同守卫 2 扫 ``_code_only``：它找的是一次 ORM 属性引用
  （``AgentRuns.cost_cents`` 出现在代码里），散文里提到这个名字不该被要求登记 ——
  ``app/`` 下的注释与 docstring 正在这么提它（比如 ``cost_rows_for_ids`` 那段
  「别把这一列当成树总额加起来」的警告）。
"""

from __future__ import annotations

import io
import pathlib
import re
import tokenize
from typing import Iterator

import pytest

pytestmark = pytest.mark.unit

# tests/services/billing/<this file> → backend/app
APP = pathlib.Path(__file__).resolve().parents[3] / "app"

# 老列进聚合的三种写法：ORM 的 func.sum、裸 SQL 的 SUM(...)、以及套了 coalesce
# 的 ORM 写法。三条都不匹配 ``own_cost_cents`` —— 名字里 ``own_`` 挡在
# ``cost_cents`` 前面，而正则要求点号后面紧跟 ``cost_cents``。
AGGREGATION_PATTERNS = (
    r"func\.sum\(\s*AgentRuns\.cost_cents",
    r"SUM\(\s*(?:a\.|agent_runs\.)?cost_cents\b",
    r"sum\(\s*func\.coalesce\(\s*AgentRuns\.cost_cents",
)

# own 与 media 相加。两个方向都写，顺序不该成为绕过的方法。
OWN_PLUS_MEDIA_PATTERNS = (
    r"own_cents[^\n]{0,40}\+[^\n]{0,40}media_cents",
    r"media_cents[^\n]{0,40}\+[^\n]{0,40}own_cents",
)

# ``cost_cents = own + children + media``（``run_recorder._finish`` 的**老列**
# 公式）落在上面那个窗口里 —— 中间只隔着 ``children_cents + ``。它是另一件事：
# 树总额，不是自身花费，``spend_of_run`` 也不提供它。所以按「匹配段里出现
# children 就不算」排除，而不是去改那行代码迎合正则（窗口收窄到 20 也能把它
# 排掉，但那是个凑出来的数字；"有 children 参与的就不是自身花费" 是条能讲清楚
# 的理由）。
#
# 代价：把 ``own_cents + children_cents + media_cents`` 写进真正想拦的场景也能
# 溜过去。守卫拦的是复发，不是绕过。
TREE_TOTAL_MARKER = "children"

# 自身花费公式的唯一合法住所。
SPEND_OF_RUN_HOME = "tree_charge.py"

# ── 守卫 3 的材料 ──────────────────────────────────────────────────────

# Python 侧对**一行的** ``cost_cents`` 做累加的写法。``+=`` 与 ``sum(...)`` 各一条；
# 两条都要求同一行里既有聚合动作、又有从行上取 ``cost_cents`` 这个键/属性的读法。
#
# ``own_`` 前缀不会命中：正则要求 ``cost_cents`` 前面紧跟的是引号或点号。
#
# ``(?<!func\.)`` 把 ORM 的 ``func.sum(Table.cost_cents)`` 排除在外：那是 SQL 侧，
# 归守卫 1 管（而 ``ai_usage_hourly`` 有它自己的、与 agent_runs 无关的 cost_cents 列）。
_ROW_READ = r"(?:\[\"cost_cents\"\]|\.get\(\"cost_cents\"|\.cost_cents\b)"
PY_ROLLUP_PATTERNS = (
    r"\+=[^\n]*" + _ROW_READ,
    r"(?<!func\.)\bsum\([^\n]*" + _ROW_READ,
)

# 登记表：``app/`` 下的相对路径 → 那批行的 ``cost_cents`` 键里装的到底是什么。
#
# ⚠️ **为什么是登记制而不是直接拦**：``cost_cents`` 在 Python 这一侧是个**标签**，
# 不是列名。两个读面刻意把自身列贴上老标签送出去 ——
# ``agent_runs_repository._USAGE_COST_COL`` 与 ``ai_library_router`` 的
# ``AgentRuns.own_cost_cents.label("cost_cents")`` —— 这样 wire 形状不变、前端不用
# 跟着改。于是 ``r["cost_cents"]`` 这一句在源码上**无法**区分「读的是自身列的别名」
# 与「读的是折叠列」：两者逐字一样，差别在几百行外的那个 select 里。
#
# 所以这条守卫不回答「这次求和对不对」，它回答「**有没有人新开了一个求和点而没说
# 清它在加什么**」。新加一处就转红，作者必须在这里写下那批行的来源；而写这一行的
# 时候，人正好被迫去看那个 select 到底 project 了哪一列 —— 这就是它买到的东西。
#
# 已知缺口，写下来免得被误当成全覆盖：**这条守卫拦的是新文件，不是已登记文件里的
# 新求和点。** 在 ``ai_library_router`` / ``agent_runs_sweeper`` 内部再写一个对着
# **裸投影**求和的地方，登记表照过 —— Ruling 6 之前那个
# ``sum_cost_cents += float(cost)`` 正是在 ``ai_library_router`` 里。
#
# 而「裸投影」是常态，不是边角：``AgentRuns.cost_cents`` 这一列的**自己的名字就是**
# ``cost_cents``，所以 ``select(..., AgentRuns.cost_cents)`` + ``.mappings()`` 不需要
# 任何 ``.label()`` 就能产出 ``row["cost_cents"]``。下面那条别名禁令**管不到它们**：
# 它们压根没有 ``.label()``。那一族现在由**守卫 4**（``BARE_PROJECTION_ALLOWLIST``）
# 按文件登记 —— 它不替这条守卫回答「这次求和对不对」，它让投影那一侧也有人签字。
#
# 要根治得让标签不再说谎 —— 把两处 ``own_cost_cents.label("cost_cents")`` 改成
# ``own_cost_cents``、消费方跟着改读法，届时「读到 ``cost_cents`` 键」就等价于
# 「读到折叠列」，这条守卫才能从登记制升级成直接拦。那是一次独立的改动。
PY_ROLLUP_ALLOWLIST = {
    "api/ai_library_router.py": (
        "行来自 monthly_usage_by_agent 与本文件 3447 行那个 "
        "own_cost_cents.label('cost_cents') 的 select —— 键里是自身列"
    ),
    "workflows/agent_runs_sweeper.py": (
        "行来自 agent_runs_repository.monthly_usage_by_agent（_USAGE_COST_COL "
        "= own_cost_cents.label('cost_cents')）—— 键里是自身列"
    ),
    "api/admin/ai_usage_router.py": (
        "行来自 ai_usage_hourly，不是 agent_runs —— 那张表的 cost_cents 是它自己的列"
    ),
}

#: 折叠列**不许改名**：把它贴上别的标签送出去，读的人就再也看不出手里是哪一列。
#:
#: ⚠️ 这条**不是**「`r["cost_cents"]` 一族安全」的依据，别那么读它。折叠列不加
#: `.label()` 也照样落在 `cost_cents` 这个键上（列名本来就叫这个），所以这条禁令
#: 覆盖不到裸投影 —— 那一族归守卫 4。它只堵住「改名」这一种额外的混淆，是补充，
#: 不是那道门。
COST_CENTS_ALIAS_PATTERN = r"AgentRuns\.cost_cents\.label\("

# ── 守卫 4 的材料 ──────────────────────────────────────────────────────

#: 一次**裸投影** = 源码里对 ``AgentRuns.cost_cents`` 的一次引用，既没被聚合包住
#: （那归守卫 1，见 ``_AGGREGATE_WRAP_RE``），也没被 ``.label()`` 改名（那归上面的别名
#: 禁令）。现存写法有两类：直接摆进 select 的参数位，或先攒进一个 ``cols`` 元组再
#: ``select(*cols)``（``list_for_issue`` 就是后者）—— 所以这条守卫**不**去匹配
#: ``select(`` 这个词，只认那次属性引用。
BARE_PROJECTION_PATTERN = r"AgentRuns\.cost_cents\b(?!\s*\.label\()"

#: 命中点**紧邻其前**是这个形状 → 聚合形态，归守卫 1，这条不重复点亮（同一处写法
#: 同时红两条只会让报错更难读）。
#:
#: ⚠️ 刻意只放过 **coalesce 套在 sum 里**的那一种，不是所有 coalesce。裸的
#: ``select(func.coalesce(AgentRuns.cost_cents, 0))`` **不是聚合** —— 它是给这一列配
#: 个逐行的 NULL 默认值，也就是一次裸投影，守卫 1 的第三条模式
#: （``sum\(\s*func\.coalesce\(…``）也不认它。把 ``func.coalesce(`` 整个放过去，等于
#: 开一个四条守卫全都看不见的口子；所以这里写成一条正则，把「coalesce 前面得有 sum」
#: 钉死。用正则而不是 ``endswith`` 是因为 Python 的 lookbehind 定宽，跨行的
#: ``func.sum(\n    AgentRuns.cost_cents`` 会漏。
_AGGREGATE_WRAP_RE = re.compile(r"(?:func\.)?sum\(\s*(?:func\.coalesce\(\s*)?\Z")

#: 登记表：``app/`` 下的相对路径 → 那几处投影把这一列送去干什么。
#:
#: ⚠️ **为什么裸投影需要登记**：老列是展示列，摆进 select 本身完全合法 —— 单行上
#: 「这次 run 花了多少（含已报到的后代）」就是它的正确用法。危险在它**流出仓库之后**：
#: 行里那个键叫 ``cost_cents``，而 ``monthly_usage_by_agent`` / ``ai_library_router``
#: 刻意用 ``own_cost_cents.label("cost_cents")`` 送出的键**一模一样**。于是守卫 3 在
#: 消费端根本分不清手里是哪一列（它的长注释里写的就是这件事），而守卫 1 只认聚合形态、
#: 别名禁令只认 ``.label()`` —— 这一族对三条守卫全部隐形，Ruling 6 之前
#: ``ai_library_router`` 里那个 ``sum_cost_cents += float(cost)`` 正是这么长出来的。
#:
#: 所以这条守卫问的是：**有没有人新开了一个「把折叠列发出去」的口子而没说清它去哪儿**。
#: 新增一处就转红，作者必须在这里写下用途；写这一行的时候，人正好被迫回答「下游会不会
#: 把它加起来」。
#:
#: 已知缺口，写下来免得被误当成全覆盖：**登记是按文件的，不是按处的。** 在一个已登记
#: 的文件里再加一处裸投影，这张表照过。要收紧就得把值改成 (处数, 理由) 并接受它随无关
#: 编辑而 churn —— 那是一次独立的权衡，不是这一票顺手能定的。
BARE_PROJECTION_ALLOWLIST: dict[str, str] = {
    "repositories/agent_runs_repository.py": (
        "list_for_issue 的议题 run 列表 + cost_rows_for_ids 的按 id 取行 —— "
        "两处都是单行展示列，跨行的钱走 own_cost_cents（tree_cost_cents / "
        "spent_cents_for_issue）"
    ),
    "api/workforce_router.py": (
        "get_workforce_board 的每 agent 最近 5 条 + get_agent_detail 的最近 20 条 —— "
        "两处都是逐行渲染，本文件不对它求和"
    ),
    "api/ai_library_router.py": (
        "get_agent_dashboard 三处（run 行 / 最近一条 run 横幅 / recent runs 表）"
        "+ list_live_runs 一处 —— 四处都是逐行展示；同一批响应里的 costs_14d 合计"
        "读的是 own_cost_cents"
    ),
}


def _code_only(src: str) -> str:
    """把注释、字符串字面量抹成空格，行号与列号原样保留。

    抹而不是删：报错里要能直接给出 ``文件:行号``。
    """
    lines = src.splitlines(keepends=True)
    blanked = [list(line) for line in lines]
    drop = {tokenize.COMMENT, tokenize.STRING}
    # f-string 在 3.12+ 拆成 FSTRING_START / MIDDLE / END，中间那段才是字面量；
    # 花括号里的表达式是真代码，照常参与扫描。
    fstring_middle = getattr(tokenize, "FSTRING_MIDDLE", None)
    if fstring_middle is not None:
        drop.add(fstring_middle)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # 扫不动的文件不能当成"干净"—— 原样交回去，宁可误报也不漏报。
        return src
    for tok in tokens:
        if tok.type not in drop:
            continue
        (srow, scol), (erow, ecol) = tok.start, tok.end
        for row in range(srow, erow + 1):
            line = blanked[row - 1]
            lo = scol if row == srow else 0
            hi = ecol if row == erow else len(line)
            for col in range(lo, min(hi, len(line))):
                if line[col] != "\n":
                    line[col] = " "
    return "".join("".join(line) for line in blanked)


def _app_sources() -> Iterator[tuple[pathlib.Path, str]]:
    """``(路径, 原样源码)``。抹注释/字符串是逐守卫的选择，不在这里做。"""
    for path in sorted(APP.rglob("*.py")):
        yield path, path.read_text(encoding="utf-8")


def _hits(src: str, patterns: tuple[str, ...], *, flags: int = 0) -> list[str]:
    found = []
    for pattern in patterns:
        for match in re.finditer(pattern, src, flags):
            found.append((src[: match.start()].count("\n") + 1, match.group(0)))
    return [f"L{line}: {text.strip()}" for line, text in found]


# ── 守卫 1：老列不进聚合 ────────────────────────────────────────────────


def test_cost_cents_is_never_summed_in_app():
    hits: list[str] = []
    for path, src in _app_sources():
        for hit in _hits(src, AGGREGATION_PATTERNS, flags=re.I):
            hits.append(f"{path.relative_to(APP)} {hit}")
    assert not hits, (
        "cost_cents 是展示列（自身 + 已报到的后代），按它聚合会把委派链上每一层"
        f"重复计一遍；改读 own_cost_cents（mig 479）：{hits}"
    )


#: 裸 SQL 只活在字符串字面量里。守卫 1 扫原样源码就是为了它们。
RAW_SQL_SNIPPETS = (
    "sql = 'SELECT SUM(cost_cents) FROM agent_runs'",
    "sql = 'SELECT SUM(a.cost_cents) FROM agent_runs a'",
)


def test_the_aggregation_guard_bites():
    """正例：四种写法各自都会被抓。守卫自己失效时必须有人喊。"""
    for snippet in (
        "total = func.sum(AgentRuns.cost_cents)",
        "total = sum(func.coalesce(AgentRuns.cost_cents, 0))",
        *RAW_SQL_SNIPPETS,
    ):
        assert _hits(snippet, AGGREGATION_PATTERNS, flags=re.I), snippet


def test_the_aggregation_guard_must_not_blank_strings():
    """钉住「别给守卫 1 也套上 ``_code_only``」：套了就看不见裸 SQL。"""
    for snippet in RAW_SQL_SNIPPETS:
        assert not _hits(_code_only(snippet), AGGREGATION_PATTERNS, flags=re.I)


def test_the_aggregation_guard_lets_the_new_column_through():
    """反例：读面换成 own_cost_cents 之后的真实写法一条都不许命中。"""
    for snippet in (
        "total = func.sum(AgentRuns.own_cost_cents)",
        "total = func.sum(func.coalesce(AgentRuns.own_cost_cents, 0))",
        "sql = 'SELECT SUM(COALESCE(own_cost_cents, 0)) FROM agent_runs'",
        "sql = 'SELECT SUM(a.own_cost_cents) FROM agent_runs a'",
    ):
        assert not _hits(snippet, AGGREGATION_PATTERNS, flags=re.I), snippet


# ── 守卫 2：自身花费公式只有一份 ────────────────────────────────────────


def test_own_media_sum_has_exactly_one_home():
    hits: list[str] = []
    for path, src in _app_sources():
        if path.name == SPEND_OF_RUN_HOME:
            continue
        for hit in _hits(_code_only(src), OWN_PLUS_MEDIA_PATTERNS):
            if TREE_TOTAL_MARKER in hit:
                continue  # 树总额公式，不是自身花费
            hits.append(f"{path.relative_to(APP)} {hit}")
    assert not hits, (
        "自身花费（own + media）只许在 tree_charge.spend_of_run 算一次，"
        f"别处一律调它：{hits}"
    )


def test_the_formula_guard_bites():
    for snippet in (
        "own_media_cents = round((own_cents or 0.0) + media_cents, 4)",
        "total = media_cents + own_cents",
    ):
        assert _hits(snippet, OWN_PLUS_MEDIA_PATTERNS), snippet


def test_the_formula_guard_leaves_the_tree_total_alone():
    """``cost_cents = own + children + media`` 是树总额，另一件事。"""
    snippet = "cost_cents = round((own_cents or 0.0) + children_cents + media_cents, 4)"
    assert all(
        TREE_TOTAL_MARKER in hit for hit in _hits(snippet, OWN_PLUS_MEDIA_PATTERNS)
    )


# ── 守卫 3：Python 侧的求和点逐个登记 ──────────────────────────────────


def _rollup_hits() -> dict[str, list[str]]:
    """扫**原样源码**，理由同守卫 1：要找的写法里 ``"cost_cents"`` 是个字符串字面量
    （字典键），``_code_only`` 一抹就什么都看不见了。代价是注释里写出这一族写法也
    会被登记表要求解释 —— 那种注释本身就该指明它在说哪一批行，不算误报。"""
    out: dict[str, list[str]] = {}
    for path, src in _app_sources():
        hits = _hits(src, PY_ROLLUP_PATTERNS)
        if hits:
            out[str(path.relative_to(APP))] = hits
    return out


def test_every_python_side_cost_rollup_is_registered():
    unregistered = {
        k: v for k, v in _rollup_hits().items() if k not in PY_ROLLUP_ALLOWLIST
    }
    assert not unregistered, (
        "新的 Python 侧 cost_cents 求和点。先确认那批行的 cost_cents 键里装的是"
        "自身列（own_cost_cents 的别名）还是折叠列，再把文件连同理由写进 "
        f"PY_ROLLUP_ALLOWLIST：{unregistered}"
    )


def test_the_allowlist_has_no_stale_entries():
    """登记表跟着代码走：求和点删了，条目也要删 —— 否则它会替一个新出现的、
    没人看过的求和点背书。"""
    hits = _rollup_hits()
    stale = sorted(set(PY_ROLLUP_ALLOWLIST) - set(hits))
    assert not stale, f"这些文件已经不做 cost_cents 求和了，条目该删：{stale}"


def test_every_registered_file_says_what_the_key_holds():
    """条目的价值全在那句理由上。空着等于「我登记过了」，那是签到不是判断。"""
    for path, why in PY_ROLLUP_ALLOWLIST.items():
        assert "own_cost_cents" in why or "ai_usage_hourly" in why, path


def test_the_rollup_guard_bites():
    """正例：四种写法各自都会被抓（登记表之外的文件里出现就是红）。"""
    for snippet in (
        'bucket["cost_cents"] += float(r["cost_cents"])',
        'total += float(r.get("cost_cents") or 0.0)',
        'total = sum(d["cost_cents"] for d in daily)',
        "total = sum(d.cost_cents for d in daily)",
    ):
        assert _hits(snippet, PY_ROLLUP_PATTERNS), snippet


def test_the_rollup_guard_lets_the_new_column_through():
    """反例：读自身列的写法一条都不许命中 —— ``own_`` 挡在 ``cost_cents`` 前面。"""
    for snippet in (
        'total += float(r["own_cost_cents"])',
        'total += float(r.get("own_cost_cents") or 0.0)',
        'total = sum(d["own_cost_cents"] for d in daily)',
        "total = sum(d.own_cost_cents for d in daily)",
        # 累加器自己叫 cost_cents 不算 —— 那是输出键名，不是从行上读的那一下。
        "sum_cost_cents += float(cost)",
    ):
        assert not _hits(snippet, PY_ROLLUP_PATTERNS), snippet


def test_the_folded_column_never_travels_under_an_alias():
    """``own_cost_cents.label("cost_cents")`` 是刻意的（wire 形状不变）；反过来给
    折叠列改名，读的人就再也看不出手里是哪一列。

    ⚠️ 这条不覆盖**裸投影** —— 折叠列的列名本来就是 ``cost_cents``，不加 ``.label()``
    也落在同一个键上。那一族归守卫 4（``BARE_PROJECTION_ALLOWLIST``，按文件登记，现存
    处数以它扫出来的为准）。这一条只堵「改名」这一种额外的混淆。"""
    hits = []
    for path, src in _app_sources():
        for hit in _hits(_code_only(src), (COST_CENTS_ALIAS_PATTERN,)):
            hits.append(f"{path.relative_to(APP)} {hit}")
    assert not hits, f"折叠列被贴了标签：{hits}"
    # 守卫自己咬得动。
    assert _hits(
        'AgentRuns.cost_cents.label("cost_cents")', (COST_CENTS_ALIAS_PATTERN,)
    )
    assert not _hits(
        'AgentRuns.own_cost_cents.label("cost_cents")', (COST_CENTS_ALIAS_PATTERN,)
    )


# ── 守卫 4：裸投影逐个登记 ─────────────────────────────────────────────


def _bare_projections_in(code: str) -> list[str]:
    """``_code_only`` 之后的源码里，那些没被聚合包住的 ``AgentRuns.cost_cents``。"""
    out: list[str] = []
    for match in re.finditer(BARE_PROJECTION_PATTERN, code):
        before = code[: match.start()]
        if _AGGREGATE_WRAP_RE.search(before):
            continue  # 聚合形态归守卫 1
        line = before.count("\n") + 1
        out.append(f"L{line}: {match.group(0)}")
    return out


def _bare_projection_hits() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for path, src in _app_sources():
        hits = _bare_projections_in(_code_only(src))
        if hits:
            out[str(path.relative_to(APP))] = hits
    return out


def test_bare_projections_of_cost_cents_are_registered():
    """两向：没登记的新投影要红，登记了却已经不存在的条目也要红。

    后者不是洁癖 —— 一个失效条目会替将来某个新出现的、没人看过的投影背书，而那正是
    这条守卫唯一要买的东西。
    """
    hits = _bare_projection_hits()
    unregistered = {k: v for k, v in hits.items() if k not in BARE_PROJECTION_ALLOWLIST}
    stale = sorted(set(BARE_PROJECTION_ALLOWLIST) - set(hits))
    assert set(hits) == set(BARE_PROJECTION_ALLOWLIST), (
        "老列被摆进了新的 select 参数位。它本身合法（展示列），但送出去之后那个 "
        "cost_cents 键与 own_cost_cents.label('cost_cents') 逐字一样，下游分不清 —— "
        "把文件连同用途写进 BARE_PROJECTION_ALLOWLIST，或者改投 own_cost_cents。"
        f"未登记：{unregistered}；失效登记：{stale}"
    )


def test_the_bare_projection_guard_bites():
    """正例：select 参数位、先攒 ``cols`` 元组、以及**没有 sum 包着的** coalesce。"""
    for snippet in (
        "stmt = select(AgentRuns.id, AgentRuns.cost_cents)",
        "cols = (\n    AgentRuns.id,\n    AgentRuns.cost_cents,\n)",
        "stmt = select(func.count(), AgentRuns.cost_cents)",
        # 逐行的 NULL 默认值，不是聚合 —— 守卫 1 的 sum(coalesce(…)) 模式不认它，
        # 所以它必须落在这条守卫上，否则四条守卫全都看不见。
        "stmt = select(func.coalesce(AgentRuns.cost_cents, 0))",
    ):
        assert _bare_projections_in(snippet), snippet


def test_the_bare_projection_guard_leaves_the_other_guards_forms_alone():
    """反例：自身列、聚合形态、别名 —— 三者各有各的守卫，这条一个都不许重复点亮。"""
    for snippet in (
        "stmt = select(AgentRuns.id, AgentRuns.own_cost_cents)",
        'AgentRuns.own_cost_cents.label("cost_cents")',
        "total = func.sum(AgentRuns.cost_cents)",
        "total = func.sum(\n    AgentRuns.cost_cents\n)",  # 跨行的 sum 也算聚合
        "total = func.sum(func.coalesce(AgentRuns.own_cost_cents, 0))",
        "total = sum(func.coalesce(AgentRuns.cost_cents, 0))",
        "total = func.sum(func.coalesce(AgentRuns.cost_cents, 0))",
        'stmt = select(AgentRuns.cost_cents.label("spend"))',
    ):
        assert not _bare_projections_in(snippet), snippet


def test_every_registered_bare_projection_says_where_it_goes():
    """条目的价值全在那句用途上。空着等于签到，不是判断。"""
    for path, why in BARE_PROJECTION_ALLOWLIST.items():
        assert len(why) > 20 and ("展示" in why or "渲染" in why), path


# ── _code_only ─────────────────────────────────────────────────────────


def test_prose_about_the_formula_is_not_a_second_implementation():
    """列注释与 docstring 里描述公式不该被当成第二份实现。"""
    src = '"""spent_cents = own_cents + media_cents."""\n# own_cents + media_cents\n'
    assert not _hits(_code_only(src), OWN_PLUS_MEDIA_PATTERNS)


def test_code_only_keeps_line_numbers():
    src = "# comment\nown_media = own_cents + media_cents\n"
    assert _hits(_code_only(src), OWN_PLUS_MEDIA_PATTERNS) == [
        "L2: own_cents + media_cents"
    ]
