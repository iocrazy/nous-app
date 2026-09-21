"""两条源码扫描守卫，钉住 mig 479 之后的两条口径。

1. **``agent_runs.cost_cents`` 是展示列，不许进聚合。** 它是「自身 + 已报到的
   后代」，同一棵树上父行与子行各自都含着同一笔钱 —— 按它 SUM 就是把委派链上
   每一层重复计一遍。聚合读面（议题预算 ``prior``、效率账、树总额）一律读
   ``own_cost_cents``（自身 + media，无后代）。Task 3–5 已经把读面全换完，这里
   只负责让它换不回去。
2. **「自身花费 = own + media」这条公式只许有一份实现。**
   ``tree_charge.spend_of_run`` 是那一份；别处再写一遍加法，就是第二个会漂的
   定义（钳位、非数字读作 0 这些约定都在 ``spend_of_run`` 里，抄一遍的人不会
   连约定一起抄）。

两条都是**源码扫描**，所以它们不看行为、只看写法 —— 能绕过（换个变量名、拼
字符串）。它们要拦的不是恶意，是「照着旁边那行抄一个聚合」这种复发。

⚠️ **两条扫的文本不一样，这是故意的：**

* 守卫 1 扫**原样源码**。它要拦的写法之一是裸 SQL 的 ``SUM(cost_cents)``，而裸
  SQL 只存在于字符串字面量里 —— 把字符串抹掉，那条模式就永远不会命中，守卫
  等于只剩三分之二。代价是注释里写 ``SUM(cost_cents)`` 也会被拦；那种注释本身
  就该改，不算误报。
* 守卫 2 扫**抹掉注释与字符串之后**的源码（``_code_only``）。描述这条公式的散文
  到处都是（``models/agents.py`` 的列注释、``budget_hook`` 的模块 docstring、
  ``recompute_spent`` 的 docstring），拦它们等于逼着大家不许把公式写进文档。
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
