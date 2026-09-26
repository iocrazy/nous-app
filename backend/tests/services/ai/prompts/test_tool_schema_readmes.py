"""Tool-schema README guard, per (module, tool) — fh5 C2.

``test_model_experience_readmes.py`` registers model-visible surfaces per
MODULE. That left a hole: once a module had one registered block, every tool
schema in it passed. ``Delegate`` (next to ``Skill`` in prompt_composer) and the
1:1 chat ``ResourceFetch`` slipped through exactly that way.

Here every tool-schema literal in ``app/`` is discovered with ``ast`` and must
be registered by ``(module, tool name)``. Its README block's code fence must
paste ``"name": "<Tool>"`` and THAT module's description verbatim, so two
same-name schemas with different text (ResourceFetch 1:1 vs team channel) each
need their own block. A schema whose name the scanner cannot resolve is a hard
failure unless its module is listed in ``_DYNAMIC_TOOL_SITES`` with a reason.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.services.ai.prompts.test_model_experience_readmes import (
    _APP_ROOT,
    _CHAT,
    _FRAMEWORK,
    _H_ASK_USER,
    _H_FINISH,
    _H_LIBRARY,
    _H_MCP,
    _H_MEDIA,
    _H_SCREENWRITING,
    _H_SET_CRITERIA,
    _H_SKILL_SCHEMA,
    _H_WAKEUP,
    _PROMPTS,
    _TEAM_CHAT,
    _block_problems,
    _const,
    _heading_lines,
)

# ── per-tool registry (fh5 C2) ───────────────────────────────────────────
#
# 上面的模块级登记有一个洞：一个模块登记了一个块，就替它里面**每一个**工具
# schema 过关。`Delegate`（prompt_composer 里 Skill 的邻居）和 1:1 聊天的
# `ResourceFetch` 就是这样漏掉的。所以工具 schema 另按 (模块, 工具名) 登记，
# 块的 fence 里必须贴出 `"name": "<Tool>"` 和**该模块**的 description 原文——
# 同名不同描述的两份 ResourceFetch 各自要一个块，谁也不能借对方的。

_H_DELEGATE = "### 工具 schema：`Delegate`（仅当 `FEATURE_WORKFORCE_DELEGATE` 开启）"
_H_RESOURCE_FETCH_1TO1 = "### 工具 schema：`ResourceFetch`（1:1 聊天）"
_H_TEAM_TURN = "### 团队频道 @agent 轮次"
_H_OUTBOUND_MCP = "### 出站 MCP 工具（agent 连接的第三方 server）"


@dataclass(frozen=True)
class DynamicToolSite:
    """A module whose tool schema's name the scanner cannot resolve to a literal."""

    reason: str
    # The README block that documents what the model sees instead, or None when
    # the module only reshapes schemas other modules authored (``transport:``).
    block: tuple[str, str] | None


TOOL_SURFACES: dict[tuple[str, str], tuple[str, str]] = {
    ("services/ai/prompts/prompt_composer.py", "Skill"): (_PROMPTS, _H_SKILL_SCHEMA),
    ("services/ai/prompts/prompt_composer.py", "Delegate"): (_PROMPTS, _H_DELEGATE),
    ("services/ai/chat/ai_library_chat_service.py", "ResourceFetch"): (
        _CHAT,
        _H_RESOURCE_FETCH_1TO1,
    ),
    ("services/chat/conversation_agent_turn.py", "ResourceFetch"): (
        _TEAM_CHAT,
        _H_TEAM_TURN,
    ),
    ("services/ai/tools/ask_user_tool.py", "AskUser"): (_PROMPTS, _H_ASK_USER),
    ("services/ai/tools/finish_issue_tool.py", "FinishIssue"): (_PROMPTS, _H_FINISH),
    ("services/ai/tools/generate_media_specs.py", "GenerateImage"): (
        _PROMPTS,
        _H_MEDIA,
    ),
    ("services/ai/tools/generate_media_specs.py", "GenerateVideo"): (
        _PROMPTS,
        _H_MEDIA,
    ),
    ("services/ai/tools/library_search_tool.py", "LibrarySearch"): (
        _PROMPTS,
        _H_LIBRARY,
    ),
    ("services/ai/tools/schedule_wakeup_tool.py", "ScheduleWakeup"): (
        _PROMPTS,
        _H_WAKEUP,
    ),
    ("services/ai/tools/set_acceptance_criteria_tool.py", "SetAcceptanceCriteria"): (
        _PROMPTS,
        _H_SET_CRITERIA,
    ),
    **{
        ("services/ai/tools/screenwriting_specs.py", tool): (_PROMPTS, _H_SCREENWRITING)
        for tool in (
            "ListScenes",
            "ReadScene",
            "CreateShot",
            "UpdateShot",
            "ProposeEdit",
            "GenerateShotImage",
            "ApplyEdit",
        )
    },
}

_DYNAMIC_TOOL_SITES: dict[str, DynamicToolSite] = {
    "services/ai/runner/agent_runner.py": DynamicToolSite(
        "outbound MCP: _mcp_tools_to_openai_format copies each connected server's "
        "tool name and description into our tools list — third-party text the "
        "model reads, documented as its own block",
        (_FRAMEWORK, _H_OUTBOUND_MCP),
    ),
    "agent_framework/mcp_descriptor.py": DynamicToolSite(
        "MCP tools/list for external clients: names are built per skill / agent "
        "slug (skill.{slug}); the shapes are pasted in the MCP block",
        (_FRAMEWORK, _H_MCP),
    ),
    "services/ai/adapters/claude.py": DynamicToolSite(
        "transport: re-shapes OpenAI tool schemas other modules authored into "
        "Anthropic's input_schema form; authors no tool of its own",
        None,
    ),
}


# ── tool-schema discovery (fh5 C2) ───────────────────────────────────────

_SCHEMA_KEYS = ("parameters", "input_schema", "inputSchema")


@dataclass(frozen=True)
class ToolSchema:
    """One tool-schema literal found in ``app/``."""

    module: str
    line: int
    name: str | None  # None: the scanner cannot resolve it to a literal
    description: str | None


_Consts = dict[str, "str | int | float"]


def _module_consts(tree: ast.Module) -> _Consts:
    """Module-level ``NAME = <str|int|float literal or f-string of those>``.

    Scalars count because descriptions interpolate them: ScheduleWakeup's
    text is ``f"At most {MAX_WAKEUPS_PER_RUN} per run …"``. Assignments are
    read in order, so a later f-string sees the numbers bound above it.
    """
    out: _Consts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        resolved = _scalar_value(value, out)
        if resolved is not None:
            out.update({t.id: resolved for t in targets if isinstance(t, ast.Name)})
    return out


def _scalar_value(node: ast.AST | None, consts: _Consts) -> str | int | float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float)):
        return None if isinstance(node.value, bool) else node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for v in node.values:
            if isinstance(v, ast.FormattedValue):
                if v.format_spec is not None or v.conversion != -1:
                    return None
                inner = _scalar_value(v.value, consts)
                if inner is None:
                    return None
                parts.append(str(inner))
            elif isinstance(v, ast.Constant):
                parts.append(str(v.value))
        return "".join(parts)
    return None


def _resolvable_consts(tree: ast.Module) -> _Consts:
    """Module constants plus ``from app.x import NAME`` constants of other modules."""
    out = _module_consts(tree)
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or not (node.module or "").startswith(
            "app."
        ):
            continue
        src = _APP_ROOT.joinpath(*node.module.split(".")[1:]).with_suffix(".py")
        if not src.is_file():
            continue
        theirs = _module_consts(ast.parse(src.read_text(encoding="utf-8")))
        for alias in node.names:
            if alias.name in theirs:
                out[alias.asname or alias.name] = theirs[alias.name]
    return out


def _str_value(node: ast.AST | None, consts: _Consts) -> str | None:
    value = _scalar_value(node, consts)
    return value if isinstance(value, str) else None


def _keys(node: ast.Dict) -> dict[object, ast.AST]:
    return {_const(k): v for k, v in zip(node.keys, node.values) if k is not None}


def _schema_dicts(tree: ast.AST) -> list[ast.Dict]:
    """OpenAI ``{"type": "function", "function": {name, parameters}}`` inner
    dicts, plus flat ``{name, parameters|input_schema|inputSchema}`` dicts;
    an OpenAI inner dict is not counted twice as a flat one."""
    found: list[ast.Dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = _keys(node)
        inner = keys.get("function")
        if _const(keys.get("type")) == "function" and isinstance(inner, ast.Dict):
            if "parameters" in _keys(inner):
                found.append(inner)
        elif "name" in keys and any(k in keys for k in _SCHEMA_KEYS):
            found.append(node)
    seen: set[int] = set()
    return [d for d in found if not (id(d) in seen or seen.add(id(d)))]


def _tool_schemas_in(src: Path) -> list[ToolSchema]:
    tree = ast.parse(src.read_text(encoding="utf-8"))
    consts = _resolvable_consts(tree)
    rel = src.relative_to(_APP_ROOT).as_posix()
    out: list[ToolSchema] = []
    for d in _schema_dicts(tree):
        keys = _keys(d)
        name = _str_value(keys.get("name"), consts) or None
        desc = _str_value(keys.get("description"), consts)
        out.append(ToolSchema(rel, d.lineno, name, desc))
    return out


def _discovered_tools() -> list[ToolSchema]:
    return [t for src in sorted(_APP_ROOT.rglob("*.py")) for t in _tool_schemas_in(src)]


def _fenced_text(readme: str, heading: str) -> str | None:
    """Everything inside code fences of the ``###`` block, or None if no block."""
    lines = (_APP_ROOT / readme).read_text(encoding="utf-8").splitlines()
    stripped = [ln.rstrip() for ln in lines]
    if heading not in stripped:
        return None
    fenced: list[str] = []
    fence: str | None = None
    for raw in stripped[stripped.index(heading) + 1 :]:
        m = re.match(r"^(`{3,}|~{3,})", raw)
        if fence is None and re.match(r"^#{1,3} ", raw):
            break
        if fence is None:
            fence = m.group(1) if m else None
        elif m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
            fence = None
        else:
            fenced.append(raw)
    return "\n".join(fenced)


def _pasted(fenced: str, text: str) -> bool:
    """``text`` appears verbatim, or JSON-string-escaped (how READMEs paste it)."""
    return text in fenced or json.dumps(text, ensure_ascii=False)[1:-1] in fenced


def _tool_block_problems(schema: ToolSchema, readme: str, heading: str) -> list[str]:
    where = f"{schema.module}:{schema.line} {schema.name} → {readme} :: {heading}"
    fenced = _fenced_text(readme, heading)
    if fenced is None:
        return [f"{where}: block missing"]
    lines = _heading_lines((_APP_ROOT / readme).read_text(encoding="utf-8"))
    problems = [f"{where}: {p}" for p in _block_problems(lines, heading)]
    if f'"name": "{schema.name}"' not in fenced:
        problems.append(f'{where}: no fence pastes "name": "{schema.name}"')
    if schema.description is None:
        problems.append(f"{where}: description is not a literal the guard can check")
    elif not _pasted(fenced, schema.description):
        problems.append(f"{where}: no fence carries this module's description verbatim")
    return problems


def _tool_surface_problems(tools: list[ToolSchema]) -> list[str]:
    problems: list[str] = []
    discovered = {(t.module, t.name) for t in tools if t.name}
    for t in tools:
        if t.name is None:
            if t.module not in _DYNAMIC_TOOL_SITES:
                problems.append(
                    f"{t.module}:{t.line}: tool name unresolvable and the module is "
                    "not in _DYNAMIC_TOOL_SITES"
                )
            continue
        target = TOOL_SURFACES.get((t.module, t.name))
        if target is None:
            problems.append(f"{t.module}:{t.line} {t.name}: not in TOOL_SURFACES")
        else:
            problems += _tool_block_problems(t, *target)
    problems += [
        f"stale TOOL_SURFACES entry (no such schema any more): {key}"
        for key in sorted(TOOL_SURFACES.keys() - discovered)
    ]
    return sorted(problems)


def _dynamic_site_problems(tools: list[ToolSchema]) -> list[str]:
    problems: list[str] = []
    dynamic = {t.module for t in tools if t.name is None}
    for module, site in sorted(_DYNAMIC_TOOL_SITES.items()):
        if module not in dynamic:
            problems.append(f"stale _DYNAMIC_TOOL_SITES entry: {module}")
        if not site.reason.strip():
            problems.append(f"{module}: dynamic tool site without a reason")
        if site.block is None:
            if not site.reason.startswith("transport:"):
                problems.append(f"{module}: only a transport: site may have no block")
            continue
        readme, heading = site.block
        lines = _heading_lines((_APP_ROOT / readme).read_text(encoding="utf-8"))
        problems += [
            f"{module} → {readme} :: {heading}: {p}"
            for p in _block_problems(lines, heading)
        ]
    return problems


# ── per-tool tests (fh5 C2) ──────────────────────────────────────────────


@pytest.mark.unit
def test_every_tool_schema_has_its_own_readme_block():
    """每个工具 schema 按 (模块, 工具名) 有块，fence 贴着它的名字和该模块的描述原文。"""
    problems = _tool_surface_problems(_discovered_tools())
    assert not problems, "tool-schema README gaps:\n" + "\n".join(problems)


@pytest.mark.unit
def test_every_dynamic_tool_site_has_a_reason_and_a_block():
    problems = _dynamic_site_problems(_discovered_tools())
    assert not problems, "dynamic tool-schema sites:\n" + "\n".join(problems)


@pytest.mark.unit
def test_tool_discovery_sees_the_schemas_that_slipped_through():
    """Positive control: the two schemas the module-level registry let through."""
    found = {(t.module, t.name): t for t in _discovered_tools()}
    delegate = ("services/ai/prompts/prompt_composer.py", "Delegate")
    one_to_one = ("services/ai/chat/ai_library_chat_service.py", "ResourceFetch")
    team = ("services/chat/conversation_agent_turn.py", "ResourceFetch")
    for key in (delegate, one_to_one, team):
        assert key in found, f"tool discovery missed {key} — the scanner is blind"
    # Names bound to module constants resolve (LibrarySearch's is a constant),
    # and so do f-string descriptions over module numbers (ScheduleWakeup's).
    assert ("services/ai/tools/library_search_tool.py", "LibrarySearch") in found
    wakeup = found[("services/ai/tools/schedule_wakeup_tool.py", "ScheduleWakeup")]
    assert wakeup.description and "At most 3 per run" in wakeup.description
    # Same name, different text: neither block can stand in for the other.
    assert found[one_to_one].description != found[team].description
    dynamic = {t.module for t in _discovered_tools() if t.name is None}
    assert "services/ai/runner/agent_runner.py" in dynamic


@pytest.mark.unit
def test_tool_guard_rejects_a_block_that_lacks_the_modules_description():
    """Positive control: the team ResourceFetch block must not satisfy the 1:1 schema."""
    one_to_one = next(
        t
        for t in _discovered_tools()
        if (t.module, t.name)
        == ("services/ai/chat/ai_library_chat_service.py", "ResourceFetch")
    )
    problems = _tool_block_problems(one_to_one, _TEAM_CHAT, _H_TEAM_TURN)
    assert any("description verbatim" in p for p in problems), problems


@pytest.mark.unit
def test_tool_guard_reports_stale_entries():
    ghost = ToolSchema("services/ai/prompts/prompt_composer.py", 1, "Ghost", "x")
    problems = _tool_surface_problems([])
    assert any("stale TOOL_SURFACES entry" in p for p in problems)
    assert any("not in TOOL_SURFACES" in p for p in _tool_surface_problems([ghost]))
    assert any(
        "stale _DYNAMIC_TOOL_SITES entry" in p for p in _dynamic_site_problems([])
    )


@pytest.mark.unit
def test_tool_name_resolves_through_an_imported_constant():
    """``from app.x import NAME`` resolves to the other module's literal, so a
    schema that imports its tool name is checked instead of read as dynamic."""
    tree = ast.parse(
        "from app.services.ai.tools.finish_issue_tool import "
        "FINISH_ISSUE_TOOL_NAME as N\n"
        'SPEC = {"type": "function", "function": {"name": N, "parameters": {}}}\n'
    )
    assert _resolvable_consts(tree)["N"] == "FinishIssue"
