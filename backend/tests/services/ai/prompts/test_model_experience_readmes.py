"""Model Experience 三问 README 的注册表守卫 + 发现棘轮（CLAUDE.md「提示词与模型可见面纪律」）。

任何会改变**模型能看到什么**的模块，其 README 必须有一个 ``###`` 块，块内依次是
``#### What the model sees`` / ``#### Token effect`` / ``#### KV Cache effect``，
README 级再有 ``## Known Limitations and Deferred Work``。

这里有两张表，模块只能落在其中一张，**没有第三种「没分类」状态**：

- ``MODEL_SURFACES``：模块 → README → 精确的 ``###`` 标题行。测试 1 检查块存在、
  三个 ``####`` 各出现一次且顺序固定。
- ``NOT_MODEL_VISIBLE``：不归本纪律管的模块，reason 必填。它有三类，reason 的前缀
  写明是哪一类：模型根本看不到（``not seen:``）、只是搬运别人写好的消息
  （``transport:``）、单次 tasklet（``tasklet:``——模型看得到，但它是独立请求、
  没有共享前缀；三问另开票补，见计划留票）。

测试 2 是发现棘轮：AST 扫 ``app/`` 下每个 ``.py``，凡是写出 ``{"role": <常量>,
"content": ...}`` 字面量、``{"type": "function"}`` 字面量、或渲染一个框
（``test_frame_escape_wiring._frame_renderers``）的模块，都必须在两张表之一。
测试 3 是正向对照：扫描器必须看得见两个已知模块，否则上一条会在扫描器瞎掉时照样全绿。

登记在测试文件里而不是运行时代码里：这是文档纪律，不是运行时行为。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.services.ai.prompts.test_frame_escape_wiring import _frame_renderers

_APP_ROOT = Path(__file__).resolve().parents[4] / "app"

_BLOCK_HEADINGS = (
    "#### What the model sees",
    "#### Token effect",
    "#### KV Cache effect",
)
_MODEL_EXPERIENCE = "## Model Experience"
_LIMITATIONS = "## Known Limitations and Deferred Work"


@dataclass(frozen=True)
class Surface:
    """One model-visible surface: the module that produces it, and where it is documented."""

    module: str  # app-relative path of the producing module; must exist
    readme: str  # app-relative README path
    heading: str  # the exact ``### ...`` line in that README


_PROMPTS = "services/ai/prompts/README.md"
_SKILLS = "services/ai/skills/README.md"
_BOUNDARY = "boundary/README.md"
_FRAMEWORK = "agent_framework/README.md"
_CHAT = "services/ai/chat/README.md"
_RUNNER = "services/ai/runner/README.md"
_SCRIPT = "services/storyboard/script/README.md"
_TEAM_CHAT = "services/chat/README.md"

_H_SYSTEM = "### 系统消息（每次请求）"
_H_SKILL_SCHEMA = "### 工具 schema：`Skill`（请求的 `tools` 参数，不在系统消息文本里）"
_H_TODO = '### 工具结果：内建 todo 的 `<todo_list>`（`Skill(skill="todo")` 的返回值）'
_H_INBOX = "### 收件箱消息框 `<inbox_message>`（步骤边界注入的 user 消息）"
_H_ASK_USER = "### 工具 schema：`AskUser`（请求的 `tools` 参数，两条路都有）"
_H_WAKEUP = "### 工具 schema：`ScheduleWakeup`（仅 issue 根 run）"
_H_LIBRARY = "### 工具 schema：`LibrarySearch`（请求的 `tools` 参数，两条路都有）"
_H_TIMEOUT = "### 工具结果：超时（任何工具，两条路都有）"
_H_RESOURCES = "### `<available_resources>`（仅当本轮有 @-mention）"
_H_OUTPUTS = "### `<referenced_outputs>`（仅当本轮有被引用的产出版本）"
_H_LINKS = "### `[link-summary]` 链接摘要块（仅当本轮用户消息含 URL）"
_H_FINISH = "### 工具 schema 与指令：`FinishIssue`（仅 issue 触发）"
_H_MEDIA = "### 工具 schema：`GenerateImage` / `GenerateVideo`（按能力授予）"
_H_SCREENWRITING = "### 工具 schema：写剧本工具组（按 write_level 授予）"
_H_COMPACTION = "### 压缩摘要 `<conversation_summary>` 与四档阈值"
_H_MCP = "### MCP `tools/list` 与 `tools/call`（外部客户端）"
_H_HISTORY = "### 聊天历史组装（每轮从库重建）"
_H_RUNNER_SUB = "### 子 agent / workforce：请求指令、回给父 run 的信封、续跑历史"

MODEL_SURFACES: tuple[Surface, ...] = (
    # ── already documented before fh4 ─────────────────────────────────
    Surface("services/ai/prompts/prompt_composer.py", _PROMPTS, _H_SYSTEM),
    Surface("services/ai/prompts/prompt_composer.py", _PROMPTS, _H_SKILL_SCHEMA),
    Surface("agent_framework/agent_todo.py", _PROMPTS, _H_TODO),
    Surface("services/ai/runner/inbox.py", _PROMPTS, _H_INBOX),
    Surface("services/ai/runner/inbox_hook.py", _PROMPTS, _H_INBOX),
    Surface("services/ai/tools/ask_user_tool.py", _PROMPTS, _H_ASK_USER),
    Surface("services/ai/tools/schedule_wakeup_tool.py", _PROMPTS, _H_WAKEUP),
    Surface("services/ai/tools/library_search_tool.py", _PROMPTS, _H_LIBRARY),
    Surface("services/ai/runner/tool_exec.py", _PROMPTS, _H_TIMEOUT),
    Surface("services/ai/runner/tool_timeouts.py", _PROMPTS, _H_TIMEOUT),
    Surface("services/ai/prompts/prompt_composer.py", _PROMPTS, _H_RESOURCES),
    Surface("services/assets/chat_ref.py", _PROMPTS, _H_RESOURCES),
    Surface("services/ai/prompts/prompt_composer.py", _PROMPTS, _H_OUTPUTS),
    Surface("services/ai/chat/output_ref_resolver.py", _PROMPTS, _H_OUTPUTS),
    Surface(
        "services/ai/skills/skill_tool_service.py",
        _SKILLS,
        "### `Skill()` 工具的返回体",
    ),
    Surface(
        "boundary/external_text.py",
        _BOUNDARY,
        "### `neutralize_external_text()` 的输出",
    ),
    Surface(
        "boundary/frame_markers.py",
        _BOUNDARY,
        "### `escape_frame_attr()` / `escape_frame_body()` / `escape_frame_prose()` 的输出",
    ),
    # ── fh4 T4: prompts README gains four blocks ──────────────────────
    Surface("services/ai/prompts/link_injection.py", _PROMPTS, _H_LINKS),
    Surface("services/ai/prompts/link_understanding.py", _PROMPTS, _H_LINKS),
    Surface("services/ai/tools/finish_issue_tool.py", _PROMPTS, _H_FINISH),
    Surface("services/ai/tools/forced_finish_declaration.py", _PROMPTS, _H_FINISH),
    Surface("services/ai/tools/generate_media_specs.py", _PROMPTS, _H_MEDIA),
    Surface("services/ai/tools/screenwriting_specs.py", _PROMPTS, _H_SCREENWRITING),
    # ── fh4 T4: new agent_framework README ────────────────────────────
    Surface("agent_framework/context_compactor.py", _FRAMEWORK, _H_COMPACTION),
    Surface("boundary/summary_frame.py", _FRAMEWORK, _H_COMPACTION),
    Surface(
        "agent_framework/summarizer.py",
        _FRAMEWORK,
        "### 摘要请求：暖前缀与维护模型两条路",
    ),
    Surface(
        "agent_framework/tool_result_pruner.py",
        _FRAMEWORK,
        "### 工具结果去重与老化（yellow 档）",
    ),
    Surface(
        "agent_framework/message_truncation.py",
        _FRAMEWORK,
        "### 单条消息上限标记",
    ),
    Surface("agent_framework/loop_guard.py", _FRAMEWORK, "### 循环守卫系统警告"),
    Surface("agent_framework/plan_mode.py", _FRAMEWORK, "### 计划模式提示词"),
    Surface("agent_framework/multimodal.py", _FRAMEWORK, "### 附件占位与多段内容"),
    Surface("agent_framework/mcp_descriptor.py", _FRAMEWORK, _H_MCP),
    Surface("services/ai/skills/mcp_tool_registration.py", _FRAMEWORK, _H_MCP),
    # ── fh4 T4: new chat README ───────────────────────────────────────
    Surface("services/ai/chat/conversations_ai_store.py", _CHAT, _H_HISTORY),
    Surface("services/ai/chat/history_image_replay.py", _CHAT, _H_HISTORY),
    Surface(
        "services/ai/chat/ai_library_chat_service.py",
        _CHAT,
        "### 聊天 request_instructions 组装（缓存边界之后）",
    ),
    # ── fh4 T4: new runner README ─────────────────────────────────────
    Surface(
        "services/ai/runner/agent_runner.py",
        _RUNNER,
        "### runner 合成的工具结果与注记",
    ),
    Surface("services/ai/runner/subagent_task_service.py", _RUNNER, _H_RUNNER_SUB),
    Surface("services/ai/runner/replay.py", _RUNNER, _H_RUNNER_SUB),
    Surface("services/workforce/agent_worker.py", _RUNNER, _H_RUNNER_SUB),
    # ── fh4 T4: new screenplay-copilot README ─────────────────────────
    Surface(
        "services/storyboard/script/script_ai_service.py",
        _SCRIPT,
        "### 剧本助手的七个单次请求",
    ),
    # ── fh4 T4: new team-channel README ───────────────────────────────
    Surface(
        "services/chat/conversation_agent_turn.py",
        _TEAM_CHAT,
        "### 团队频道 @agent 轮次",
    ),
    Surface(
        "services/chat/conversation_memory_service.py",
        _TEAM_CHAT,
        "### 团队频道 @agent 轮次",
    ),
)

_TASKLET = (
    "tasklet: single-shot request with its own prompt and no shared prefix; "
    "model-visible, three-question coverage is a separate ticket (fh4 plan 留票)"
)
_TRANSPORT = (
    "transport: reshapes messages other modules authored into a provider's wire "
    "format; authors no model-visible text of its own"
)

NOT_MODEL_VISIBLE: dict[str, str] = {
    # transport / plumbing
    "services/ai/adapters/claude.py": _TRANSPORT,
    "services/ai/adapters/codex_daemon.py": _TRANSPORT,
    "services/ai/adapters/openai_compat.py": _TRANSPORT,
    "services/ai/providers/ai_provider.py": _TRANSPORT,
    "services/ai/providers/embedding_items.py": (
        "not seen: builds embedding-model inputs (vectors out, no chat model reads them)"
    ),
    "services/ai/nous_model_health.py": (
        "not seen: a liveness probe request; its answer is discarded, no agent sees it"
    ),
    "agent_framework/tokenizer.py": (
        "not seen: builds throwaway message dicts to count tokens; never sent"
    ),
    "agent_framework/catalog_windows.py": (
        "not seen: supplies the window denominator (TTL 300s, smaller window wins); "
        "decides WHEN compaction fires — documented in the compaction block's Token effect"
    ),
    "agent_framework/context_window.py": (
        "not seen: ContextWindowError goes to the caller, not the model; "
        "REJECT_RATIO=0.90 is the compactor's red line — cited in the compaction block"
    ),
    "agent_framework/output_budget.py": "not seen: sets max_tokens only",
    "agent_framework/tool_result_cache.py": (
        "not seen: returns the identical cached result; the model cannot tell a hit"
    ),
    "agent_framework/context_engine.py": (
        "not seen: pass-through to PromptComposer (covered by the system-message block)"
    ),
    "services/ai/chat/chat_context_engine.py": (
        "not seen: pass-through to PromptComposer (covered by the system-message block)"
    ),
    "agent_framework/hooks_protocol.py": (
        "not seen: HookResult.note is audit-only; the one step-hook injection is "
        "InboxClaimHook, covered by the <inbox_message> block"
    ),
    "services/ai/runner/pause_hook.py": "not seen: stops the turn, injects nothing",
    "services/ai/runner/budget_hook.py": "not seen: stops the turn, injects nothing",
    "services/ai/runner/interrupted_turn.py": "not seen: writes the transcript only",
    "services/ai/runner/reasoning.py": (
        "not seen: strips <think> before persistence — one sentence in the chat-history block"
    ),
    # single-shot tasklets
    "services/ai/caption/caption_service.py": _TASKLET,
    "services/ai/classify/classify_service.py": _TASKLET,
    "services/ai/translate/translate_service.py": _TASKLET,
    "services/ai/summarize/summarize_service.py": _TASKLET,
    "services/ai/visual/visual_analysis_service.py": _TASKLET,
    "services/ai/tasklets/base.py": _TASKLET,
    "services/ai/memory/agent_memory_consolidator.py": _TASKLET,
    "services/ai/memory/promotion_evaluator.py": _TASKLET,
    "services/ai/runner/commitment_harvester.py": _TASKLET,
    "services/canvas/canvas_run_service.py": _TASKLET,
    "services/topics/topic_scorer.py": _TASKLET,
}

_ROLES = frozenset({"system", "user", "tool", "assistant"})


# ── README parsing ───────────────────────────────────────────────────────


def _heading_lines(text: str) -> list[str]:
    """Every markdown line OUTSIDE fenced code, right-stripped.

    Fences matter: the prompts README pastes the system message verbatim, and
    that paste contains ``# Identity`` / ``## Available Skills`` lines which
    are model text, not README structure.
    """
    out: list[str] = []
    fence: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        m = re.match(r"^(`{3,}|~{3,})", line)
        if fence is None:
            if m:
                fence = m.group(1)
                continue
            out.append(line)
        elif m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
            fence = None
    return out


def _block(lines: list[str], heading: str) -> list[str] | None:
    """Lines after ``heading`` up to the next ``###``/``##``/``#`` heading."""
    if heading not in lines:
        return None
    start = lines.index(heading) + 1
    body: list[str] = []
    for line in lines[start:]:
        if re.match(r"^#{1,3} ", line):
            break
        body.append(line)
    return body


def _block_problems(lines: list[str], heading: str) -> list[str]:
    count = lines.count(heading)
    if count == 0:
        return ["block missing"]
    problems = [] if count == 1 else [f"heading appears {count} times"]
    body = _block(lines, heading) or []
    positions: list[int] = []
    for sub in _BLOCK_HEADINGS:
        n = body.count(sub)
        if n != 1:
            problems.append(f"{sub!r} appears {n} times (want exactly 1)")
        else:
            positions.append(body.index(sub))
    if len(positions) == len(_BLOCK_HEADINGS) and positions != sorted(positions):
        problems.append("the three #### headings are out of order")
    return problems


def _readme_problems(lines: list[str]) -> list[str]:
    if _MODEL_EXPERIENCE not in lines:
        return [f"no {_MODEL_EXPERIENCE!r}"]
    if _LIMITATIONS not in lines:
        return [f"no {_LIMITATIONS!r}"]
    if lines.index(_MODEL_EXPERIENCE) > lines.index(_LIMITATIONS):
        return ["Known Limitations comes before Model Experience"]
    return []


def _coverage_problems() -> list[str]:
    problems: list[str] = []
    missing_readmes: set[str] = set()
    checked_readmes: set[str] = set()
    seen: set[tuple[str, str]] = set()
    for s in MODEL_SURFACES:
        if not (_APP_ROOT / s.module).is_file():
            problems.append(f"module does not exist: {s.module}")
        path = _APP_ROOT / s.readme
        if not path.is_file():
            missing_readmes.add(s.readme)
            continue
        lines = _heading_lines(path.read_text(encoding="utf-8"))
        if s.readme not in checked_readmes:
            checked_readmes.add(s.readme)
            problems += [f"{s.readme}: {p}" for p in _readme_problems(lines)]
        if (s.readme, s.heading) in seen:
            continue
        seen.add((s.readme, s.heading))
        problems += [
            f"{s.readme} :: {s.heading}: {p}" for p in _block_problems(lines, s.heading)
        ]
    problems += [f"README missing: {r}" for r in sorted(missing_readmes)]
    return sorted(problems)


# ── discovery ────────────────────────────────────────────────────────────


def _const(node: ast.AST | None) -> object:
    return node.value if isinstance(node, ast.Constant) else None


def _writes_model_message(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = {_const(k): v for k, v in zip(node.keys, node.values) if k is not None}
        if _const(keys.get("role")) in _ROLES and "content" in keys:
            return True
        if _const(keys.get("type")) == "function":
            return True
    return False


def _discovered_modules() -> set[str]:
    found = set(_frame_renderers())
    for src in _APP_ROOT.rglob("*.py"):
        if _writes_model_message(ast.parse(src.read_text(encoding="utf-8"))):
            found.add(src.relative_to(_APP_ROOT).as_posix())
    return found


# ── tests ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_every_registered_surface_has_its_three_question_block():
    problems = _coverage_problems()
    assert not problems, "Model Experience coverage gaps:\n" + "\n".join(problems)


@pytest.mark.unit
def test_every_discovered_model_visible_module_is_classified():
    """No third state: a module that writes model-visible text is either documented or exempt with a reason."""
    covered = {s.module for s in MODEL_SURFACES}
    unclassified = sorted(_discovered_modules() - covered - NOT_MODEL_VISIBLE.keys())
    assert not unclassified, (
        "modules that write model-visible messages / tool schemas / frames but are in "
        "neither MODEL_SURFACES nor NOT_MODEL_VISIBLE:\n" + "\n".join(unclassified)
    )


@pytest.mark.unit
def test_registries_are_consistent():
    covered = {s.module for s in MODEL_SURFACES}
    both = sorted(covered & NOT_MODEL_VISIBLE.keys())
    assert not both, f"in both tables: {both}"
    blank = sorted(k for k, v in NOT_MODEL_VISIBLE.items() if not v.strip())
    assert not blank, f"exemptions without a reason: {blank}"
    gone = sorted(k for k in NOT_MODEL_VISIBLE if not (_APP_ROOT / k).is_file())
    assert not gone, f"exempt modules that no longer exist (delete the entry): {gone}"
    bad_prefix = sorted(
        k
        for k, v in NOT_MODEL_VISIBLE.items()
        if not v.startswith(("not seen:", "transport:", "tasklet:"))
    )
    assert (
        not bad_prefix
    ), f"reason must say which kind of exemption it is: {bad_prefix}"


@pytest.mark.unit
def test_discovery_sees_known_model_visible_modules():
    """Positive control: if the scanner goes blind, the ratchet above goes green for nothing."""
    found = _discovered_modules()
    for known in ("services/ai/runner/inbox_hook.py", "boundary/summary_frame.py"):
        assert known in found, f"discovery missed {known} — the scanner is blind"


@pytest.mark.unit
def test_block_parser_rejects_swapped_headings():
    """Positive control for test 1: the parser must notice a reordered block."""
    good = ["### X", *_BLOCK_HEADINGS, "## Next"]
    assert _block_problems(good, "### X") == []
    swapped = ["### X", _BLOCK_HEADINGS[1], _BLOCK_HEADINGS[0], _BLOCK_HEADINGS[2]]
    assert _block_problems(swapped, "### X") == [
        "the three #### headings are out of order"
    ]
    fenced = _heading_lines("### X\n```\n## not a heading\n```\n#### Token effect\n")
    assert "## not a heading" not in fenced
