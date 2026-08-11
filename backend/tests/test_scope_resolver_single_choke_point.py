"""Architecture guard: script_scenes / script_shots / episodes may be joined
against script_projects for authorization in EXACTLY ONE place —
``app/services/ai/scope/scope_resolver.py`` (A2 — screenwriting agent layer,
spec §3.2: "唯一解析器... 禁止各工具自行实现可见性判断").

Mirrors ``tests/test_run_recorder_coverage.py``'s shape: grep the backend for
patterns that indicate a NEW site doing this join (or reaching for the
underlying ORM models / repository layer directly) and compare against a
documented allow-list of pre-A2 files that legitimately do this today (the
existing, human-request-authenticated REST layer — routers, ``scope_guards.py``,
the repositories themselves, and the DBOS workflows). A new bypass fails the
test and forces a human to either route the new code through
``resolve_scene`` / ``resolve_shot`` / ``resolve_episode``, or explicitly
justify a new allow-list entry.

Update ALLOWED_* when:
- You intentionally add a new legitimate direct-access path (and document why)
- A file gets renamed (update the path)

Do NOT update it just to make the test pass without addressing the bypass —
same discipline as test_run_recorder_coverage.py.

A2 REVIEW FIX (Important): the first cut of this guard had three real
blind spots, each verified by a reviewer planting a file and running the
scanner against it:
  1. Direct repository CLASS construction (``ScriptSceneRepository().get_by_id(...)``)
     matched neither the ORM-attribute pattern nor the getter-FUNCTION
     pattern — the getter pattern only recognised the module-level
     ``get_script_scene_repository()`` factory, not the class itself.
  2. The raw-SQL pattern only matched ``FROM``/``JOIN`` — an ``UPDATE`` /
     ``INSERT INTO`` / ``DELETE FROM`` against these tables sailed straight
     through, and A4's ``CreateShot``/``UpdateShot`` are precisely writes.
  3. The scanner matched line-by-line (``content.splitlines()``), so a
     pattern split across two lines (e.g. ``FROM\\n    public.script_scenes``)
     was invisible even though each individual line looks innocuous.
All three are fixed below; ``test_guard_fires_on_a_planted_bypass`` now
plants and detects all three shapes, not just the easiest one.
"""

from __future__ import annotations

import re
from pathlib import Path

BACKEND_APP = Path(__file__).resolve().parent.parent / "app"

# The ORM models this guard protects. Direct attribute access
# (``ScriptScenes.foo``) or an import of one of these names is the first
# bypass shape: a new file querying the model directly instead of going
# through the resolver.
_ORM_CLASS_NAMES = ("ScriptScenes", "ScriptShots", "ScriptProjects", "Episodes")

# The repository CLASS names backing these tables (as opposed to the
# module-level get_*_repository() factory functions below) — constructing
# one directly (``ScriptSceneRepository().get_by_id(...)``) is the "skip the
# one-line getter, go straight to the class" bypass a hurried tool author
# would reach for, and it is JUST as unscoped as the factory (see e.g.
# script_scene_repository.py's ``get_by_id`` docstring — no tenant filter at
# all).
_REPO_CLASS_NAMES = (
    "ScriptSceneRepository",
    "ScriptShotRepository",
    "EpisodeRepository",
    "ScriptProjectRepository",
)

_TABLE_NAMES = ("script_scenes", "script_shots", "script_projects", "episodes")

# Files allowed to touch the ORM classes directly. These predate A2 and are
# either (a) the model definitions themselves, (b) the repository layer that
# backs the existing human-request-authenticated REST endpoints (scoped by
# FastAPI auth + app/core/scope_guards.py, an entirely different — and
# already-correct — authorization path from the agent-tool one this guard
# protects), or (c) the resolver module this guard exists to keep unique.
ORM_ALLOWED_PATHS: dict[str, str] = {
    "models/scripts.py": "ORM model definitions themselves",
    "models/__init__.py": "re-export module for ORM model definitions",
    "repositories/episode_repository.py": (
        "pre-A2 repository backing the human-request-authenticated "
        "/episodes REST endpoints (app/core/scope_guards.py verifies "
        "project access before these are called)"
    ),
    "repositories/generated_media_repository.py": (
        "pre-A2 repository; project-scoped media listing behind the "
        "authenticated project REST layer"
    ),
    "repositories/project_stage_nodes_repository.py": (
        "pre-A2 workflow repository backing the human-request-authenticated "
        "/projects/{id}/workflow + /advance REST endpoints. Reads "
        "Episodes.sort_order (id-only, project-scoped) in add_cross_episode_dep "
        "to enforce the strictly-earlier-episode rule for a cross-episode "
        "dependency edge — NOT the agent-tool scope path this guard protects; "
        "callers are gated by project access before reaching here"
    ),
    "repositories/script_repository.py": (
        "pre-A2 repository backing /script_projects REST endpoints "
        "(app/core/scope_guards.py verify_script_access gates callers)"
    ),
    "repositories/script_scene_repository.py": (
        "pre-A2 repository backing /script_scenes REST endpoints "
        "(app/core/scope_guards.py verify_scene_access gates callers)"
    ),
    "repositories/script_shot_repository.py": (
        "pre-A2 repository backing /script_shots REST endpoints "
        "(app/core/scope_guards.py verify_shot_access gates callers)"
    ),
    "services/ai/scope/scope_resolver.py": (
        "THE single choke point this guard exists to keep unique — "
        "resolve_scene/resolve_shot/resolve_episode"
    ),
    "services/ai/scope/scoped_script_gateway.py": (
        "A4. The resolver answers 'may this run touch id X' for ONE id; it "
        "deliberately does not enumerate (ListScenes has no id to resolve) "
        "and does not write (CreateShot/UpdateShot). Those need the same "
        "tables. This entry is paid for by a MECHANICAL invariant, not a "
        "promise: every public function in that module takes the run's "
        "AgentRunScope plus — where it operates on an entity — an "
        "already-resolved ResolvedScene/ResolvedShot/ResolvedEpisode handle, "
        "never a raw model-supplied id. Those frozen dataclasses are "
        "constructed in exactly one place (inside the resolvers, AFTER the "
        "scope check), so the authorization proof is carried by the type. "
        "test_scoped_script_gateway_takes_only_resolved_handles below "
        "enforces it by signature introspection — a future function taking a "
        "bare scene_id fails the build."
    ),
    "services/workflow/surface_completion.py": (
        "B4 回流 hook 服务（产物写入回流点触发自动完成）。在 _scope_for_* 私有函数中"
        "通过 ORM JOIN 解析 script_id/scene_id/shot_id 的项目与集数归属（为了入队 tick）。"
        "这些函数的 id 参数来自系统发起的回流点（ScriptProjects.id / ScriptScenes.id / "
        "ScriptShots.id），不是 agent-tool 用户供给的，与 scope_resolver 的单回流点约束"
        "同精神——回流 hook 层只负责解析归属，不做 scope 判定（判定已在上游产物写入时完成）。"
    ),
    "workflows/autopilot_sweep.py": (
        "5 分钟兜底 sweep 的 eligible-project 扫描（B4 fast-follow, 2026-08-08）:"
        "outerjoin Episodes 只为识别「某集游标仍指着已 done 节点 = cascade 欠账」的"
        "项目 id 集合。纯基础设施只读扫描,无任何用户/agent 供给的 id,不触碰场景/"
        "镜头数据,不做 scope 判定——判定与推进都在被入队的 autopilot_tick 里走"
        "既有闸门。"
    ),
    "services/ai/undo/run_undo_service.py": (
        "Run 撤销执行器（mig 415 立项）。不是 agent-tool 路径：入口是"
        "人触发的 /runs/{run_id}/undo REST 端点，router 已按 agent_runs."
        "user_id 校验归属 + undone_at CAS 幂等后才调用。它操作的每个 id 都"
        "来自服务端自己的账本（script_shot_ops / script_ops），从不接受"
        "模型或用户供给的 scene/shot id；写入全部带 CAS WHERE（与并发编辑"
        "互斥），scene 正文只走 apply_element_ops 这一条 ops 通道。"
    ),
}

# Files allowed to reach the scene/shot/episode/script-project repository
# layer directly — either via the module-level get_*_repository() factory
# OR by constructing the repository class itself. Everything here predates
# A2 and sits behind the same authenticated REST/workflow layer as above.
REPO_LAYER_ALLOWED_PATHS: dict[str, str] = {
    "api/episodes_router.py": "REST endpoint, authenticated + scope_guards-gated",
    "api/script_projects_router.py": "REST endpoint, authenticated + scope_guards-gated",
    "api/script_scenes_router.py": "REST endpoint, authenticated + scope_guards-gated",
    "api/script_shots_router.py": "REST endpoint, authenticated + scope_guards-gated",
    "core/scope_guards.py": (
        "the FastAPI dependency guards themselves — resolve child id -> "
        "script_id -> team check, for the human-request path (NOT the "
        "agent-tool path; a live 'is this user a team member' check is the "
        "WRONG authorization model for a dispatched agent run's scope, "
        "see scope_resolver.py's module docstring)"
    ),
    "repositories/episode_repository.py": "repository implementation itself",
    "repositories/script_repository.py": "repository implementation itself",
    "repositories/script_scene_repository.py": "repository implementation itself",
    "repositories/script_shot_repository.py": "repository implementation itself",
    "services/ai/scope/scoped_script_gateway.py": (
        "A5, and the ONLY agent-path entry here. Narrow on purpose: the "
        "module calls exactly ONE scene-repository method, "
        "``apply_element_ops`` — THE ops channel (one transaction that "
        "version-guards content_json, writes the script_ops row plus its "
        "inverse, and raises the same VersionConflict the editor's own "
        "optimistic-concurrency path speaks). Re-implementing it to keep "
        "this list short would create the second, drifting write path "
        "agent-layer spec §5.2 exists to forbid. "
        "THIS ENTRY IS A MECHANISM, NOT A PROMISE (A5 review, Important 3): "
        "the path-level scan below would let ANY repo method through once "
        "this file is listed — including get_by_id(<model-supplied id>) — so "
        "test_scoped_script_gateway_calls_only_the_ops_channel AST-scans the "
        "file and fails if the called-method set is anything but "
        "{apply_element_ops}. The authorization argument is unchanged from "
        "this module's ORM_ALLOWED_PATHS entry: that call takes ``scene.id`` "
        "off an already-resolved ResolvedScene, never a model-supplied id."
    ),
    "services/library/projects_service.py": "pre-A2 project service, authenticated REST path",
    "services/script/version_service.py": "pre-A2 script version service, authenticated REST path",
    "services/storyboard/script/script_service.py": "pre-A2 script service, authenticated REST path",
    "workflows/script_shot_breakdown.py": (
        "pre-A2 DBOS workflow triggered from the authenticated REST layer, "
        "not (yet) reachable from an agent tool call"
    ),
    "workflows/script_shot_generate.py": (
        "pre-A2 DBOS workflow (the one A6 will wire GenerateShotImage into "
        "later) — when A6 lands, its tool entry point must resolve ids via "
        "the resolver BEFORE invoking this workflow, not rely on the "
        "workflow's own internal lookups; flagged in the A2 report as a "
        "follow-up for whoever implements A6"
    ),
    "workflows/script_shot_video.py": (
        "pre-A2 DBOS workflow triggered from the authenticated REST layer"
    ),
    "workflows/script_scene_convert.py": (
        "pre-A2 DBOS workflow (chapter-prose -> scenes AI conversion), "
        "triggered from the authenticated REST layer; constructs "
        "ScriptSceneRepository() directly to persist_scenes — found by the "
        "A2 review widening the repo-class-construction pattern"
    ),
    "workflows/autopilot.py": (
        "scheduled/system DBOS workflow (not an agent-tool scope path). B2 T4 "
        "made the per-project tick loop over the project's episodes: it reads "
        "``get_episode_repository().list_by_project(project_id)`` to enumerate "
        "them, then advances each. The identity is the project the tick already "
        "owns — no user-supplied scope to resolve — so this is a system read, "
        "not the agent-tool id-resolution this guard protects."
    ),
    "api/projects_router.py": (
        "authenticated REST layer — the /workflow + /advance + start-early "
        "endpoints. B2 T3 reads get_episode_repository().get_by_id to verify a "
        "client-supplied episode_id belongs to the path project (_require_"
        "project_episode → 404) before scoping the workflow to it; the caller "
        "is already project-access-gated, id is validated, not model-supplied."
    ),
    "services/workflow/advance_service.py": (
        "pre-A2 workflow advance engine, reached from the authenticated REST "
        "/advance + autopilot. B2 T1 sank the cursor to episode level: reads "
        "get_episode_repository().get_by_id(episode_id).current_node_id and "
        "writes episode_repository.set_current_node_id — episode_id comes from "
        "the server-bound dispatch scope / a project-gated REST arg, never a "
        "model-supplied id."
    ),
    "services/workflow/instantiation.py": (
        "pre-A2 workflow instantiation service, reached only from the "
        "authenticated, project-access-gated REST layer (attach / create-"
        "project / create-episode / reinstantiate-per-episode). B3 fans a "
        "template out per episode: reads get_episode_repository().list_by_"
        "project to enumerate the project's OWN episodes and calls "
        "episode_repository.set_current_node_id to seat each episode's cursor. "
        "The identity is the project the caller already owns — every episode id "
        "is the project's own or a "
        "project-gated REST arg, never a model-supplied id — a system write "
        "like autopilot.py's tick, not the agent-tool id-resolution this guard "
        "protects."
    ),
    "services/workflow/node_folders.py": (
        "pre-A2 deliverable-folder helper called from advance_service. B2 T2 "
        "reads get_episode_repository().get_by_id to prefix a node's folder "
        "name per episode (breaks the cross-episode shared-folder P0 trap); "
        "episode_id is the node's own frozen surface scope, not model-supplied."
    ),
    "services/workflow/node_authz.py": (
        "Task 7, workspace IA redesign spec §5. can_edit_node_config reads "
        "get_episode_repository().get_by_id(episode_id) to compare its "
        "owner_id against the caller — episode_id comes from an already "
        "project-scoped node row (project_stage_nodes.episode_id, fetched by "
        "the authenticated /workflow/nodes/{node_id} PATCH handler after its "
        "own project guard), never a bare model/user-supplied id; same "
        "pattern as node_folders.py and advance_service.py above, not the "
        "agent-tool scope path this guard protects."
    ),
    "services/workflow/surface_completion.py": (
        "B4 回流 hook 服务（产物写入回流点触发自动完成）。读判据/节点/镜像走 "
        "repo 公开接口（surface_criteria_for_episode / list_nodes_by_episode / "
        "list_by_origin / transition_status / set_node_status）。所有 id 来自 "
        "系统层（节点.id / issue.id），不涉及 scope 解析，无 agent-tool 用户供给 id 的路径。"
    ),
    "workflows/script_import.py": (
        "pre-A2 DBOS workflow (fountain/prose script import), triggered "
        "from the authenticated REST layer; constructs "
        "ScriptSceneRepository() directly for the same persist_scenes path "
        "as script_scene_convert.py above"
    ),
    "services/ai/undo/run_undo_service.py": (
        "Run 撤销执行器（mig 415 立项）。不是 agent-tool 路径：入口是"
        "人触发的 /runs/{run_id}/undo REST 端点，router 已按 agent_runs."
        "user_id 校验归属 + undone_at CAS 幂等后才调用。它操作的每个 id 都"
        "来自服务端自己的账本（script_shot_ops / script_ops），从不接受"
        "模型或用户供给的 scene/shot id；写入全部带 CAS WHERE（与并发编辑"
        "互斥），scene 正文只调 get_script_scene_repository() 的"
        "list_ops_by_scene + apply_element_ops 两个方法。"
    ),
}

_ORM_ATTR_PATTERN = re.compile(r"\b(?:" + "|".join(_ORM_CLASS_NAMES) + r")\.")
_ORM_IMPORT_PATTERN = re.compile(
    r"^\s*(?:from|import).*\b(?:" + "|".join(_ORM_CLASS_NAMES) + r")\b",
    re.MULTILINE,
)
# Gap #2 fix: was FROM|JOIN only. UPDATE/INSERT INTO/DELETE FROM are exactly
# the shapes a write tool (A4's CreateShot/UpdateShot) would reach for.
_RAW_SQL_PATTERN = re.compile(
    r"(?:FROM|JOIN|UPDATE|INSERT\s+INTO|DELETE\s+FROM)\s+(?:public\.)?"
    r"(?:" + "|".join(_TABLE_NAMES) + r")\b",
    re.IGNORECASE,
)
_REPO_GETTER_PATTERN = re.compile(
    r"\bget_script_scene_repository\(\)|\bget_script_shot_repository\(\)|"
    r"\bget_episode_repository\(\)|\bget_script_project_repository\(\)"
)
# Gap #1 fix: direct construction of the repository CLASS, bypassing the
# module-level factory the getter pattern above catches.
_REPO_CLASS_CONSTRUCTION_PATTERN = re.compile(
    r"\b(?:" + "|".join(_REPO_CLASS_NAMES) + r")\("
)
_REPO_LAYER_PATTERN = re.compile(
    _REPO_GETTER_PATTERN.pattern + "|" + _REPO_CLASS_CONSTRUCTION_PATTERN.pattern
)


def _is_allowed(rel_path: str, allowed: dict[str, str]) -> bool:
    for allowed_path in allowed:
        if rel_path == allowed_path or rel_path.startswith(
            allowed_path.rstrip("/") + "/"
        ):
            return True
    return False


def _strip_comment_lines(content: str) -> str:
    """Blank out lines that are ENTIRELY a comment (same skip semantics the
    original scanner had), preserving line count/positions so a match's
    line number still maps back to the real file. Does not attempt to strip
    trailing inline comments (``x = 1  # note``) — a pattern match inside an
    inline comment is a rare enough false positive that a human triaging a
    hit will spot it immediately, same tradeoff test_run_recorder_coverage.py
    already accepts."""
    lines = content.splitlines()
    return "\n".join("" if line.lstrip().startswith("#") else line for line in lines)


def _scan(pattern: re.Pattern, allowed: dict[str, str]) -> list[tuple[str, int, str]]:
    """Gap #3 fix: search the WHOLE file content (comment-lines blanked)
    rather than one ``content.splitlines()`` entry at a time — a pattern
    split across two physical lines (``FROM\\n    public.script_scenes``)
    is invisible to a line-by-line search no matter how the pattern itself
    is written, since the two halves never coexist in the same string being
    tested. Whitespace in these patterns already matches embedded newlines
    (``\\s`` includes ``\\n``), so no DOTALL flag is needed once the search
    unit is the whole file rather than one line."""
    offenders: list[tuple[str, int, str]] = []
    for py_file in BACKEND_APP.rglob("*.py"):
        rel = py_file.relative_to(BACKEND_APP).as_posix()
        if _is_allowed(rel, allowed):
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        searchable = _strip_comment_lines(content)
        original_lines = content.splitlines()
        for match in pattern.finditer(searchable):
            line_no = searchable.count("\n", 0, match.start()) + 1
            snippet = (
                original_lines[line_no - 1].strip()
                if 0 < line_no <= len(original_lines)
                else match.group(0)
            )
            offenders.append((rel, line_no, snippet))
    return offenders


def _format(offenders: list[tuple[str, int, str]], guidance: str) -> str:
    lines = [f"  {path}:{lineno}  ->  {text}" for path, lineno, text in offenders]
    return "\n".join(lines) + "\n\n" + guidance


def test_no_new_direct_orm_access_to_scene_shot_episode_tables():
    offenders = (
        _scan(_ORM_ATTR_PATTERN, ORM_ALLOWED_PATHS)
        + _scan(_ORM_IMPORT_PATTERN, ORM_ALLOWED_PATHS)
        + _scan(_RAW_SQL_PATTERN, ORM_ALLOWED_PATHS)
    )
    if offenders:
        raise AssertionError(
            _format(
                offenders,
                "Direct ORM/raw-SQL access to script_scenes/script_shots/"
                "script_projects/episodes outside scope_resolver.py:\n"
                "Route model-supplied ids through resolve_scene / "
                "resolve_shot / resolve_episode (app/services/ai/scope/"
                "scope_resolver.py), or add a justified entry to "
                "ORM_ALLOWED_PATHS in this test.",
            )
        )


def test_no_new_direct_repository_layer_access_outside_resolver_and_rest_layer():
    offenders = _scan(_REPO_LAYER_PATTERN, REPO_LAYER_ALLOWED_PATHS)
    if offenders:
        raise AssertionError(
            _format(
                offenders,
                "Direct access to the scene/shot/episode/script-project "
                "repository layer (either get_script_scene_repository() "
                "style factory calls or ScriptSceneRepository()-style direct "
                "construction) outside the pre-A2 authenticated REST/"
                "workflow layer: these apply NO scope filter of their own "
                "(see e.g. script_scene_repository.py's get_by_id "
                "docstring). Route through resolve_scene / resolve_shot / "
                "resolve_episode instead, or add a justified entry to "
                "REPO_LAYER_ALLOWED_PATHS in this test.",
            )
        )


# ---------------------------------------------------------------------- #
# Prove the guard isn't decorative: plant deliberately-bad snippets in a
# TEMP file under app/ (not committed — created + removed within the test)
# and assert the scanner actually flags EACH of the three shapes it was
# previously blind to (repo class construction, a write statement, and a
# match split across two lines) — not just the easiest ORM-import case.
# ---------------------------------------------------------------------- #


def test_guard_fires_on_a_planted_bypass():
    planted_dir = BACKEND_APP / "services" / "ai" / "tools"
    planted_dir.mkdir(parents=True, exist_ok=True)
    planted = planted_dir / "_scope_guard_fixture_do_not_commit.py"
    planted.write_text(
        "from app.models import ScriptScenes\n"
        "from app.repositories.script_scene_repository import ScriptSceneRepository\n"
        "\n"
        "async def sneaky_lookup(scene_id):\n"
        "    # bypasses resolve_scene entirely\n"
        "    return ScriptScenes.id\n"
        "\n"
        "async def sneaky_repo_construction(scene_id):\n"
        "    repo = ScriptSceneRepository()\n"
        "    return await repo.get_by_id(scene_id)\n"
        "\n"
        "async def sneaky_write(scene_id):\n"
        "    query = (\n"
        '        "UPDATE public.script_scenes "\n'
        "        \"SET content = 'x' WHERE id = %s\"\n"
        "    )\n"
        "    return query\n"
        "\n"
        "async def sneaky_multiline_join(project_id):\n"
        "    query = (\n"
        '        "SELECT * FROM\\n"\n'
        '        "    public.script_scenes sc\\n"\n'
        '        "JOIN public.script_projects sp ON sp.id = sc.script_id"\n'
        "    )\n"
        "    return query\n",
        encoding="utf-8",
    )
    try:
        orm_offenders = _scan(_ORM_ATTR_PATTERN, ORM_ALLOWED_PATHS) + _scan(
            _ORM_IMPORT_PATTERN, ORM_ALLOWED_PATHS
        )
        raw_sql_offenders = _scan(_RAW_SQL_PATTERN, ORM_ALLOWED_PATHS)
        repo_layer_offenders = _scan(_REPO_LAYER_PATTERN, REPO_LAYER_ALLOWED_PATHS)

        rel = "services/ai/tools/_scope_guard_fixture_do_not_commit.py"

        assert rel in {
            p for p, _, _ in orm_offenders
        }, "The guard failed to catch a direct ORM import/attribute bypass"
        assert rel in {p for p, _, _ in repo_layer_offenders}, (
            "The guard failed to catch direct repository CLASS construction "
            "(the gap a reviewer found: ScriptSceneRepository() bypasses "
            "the getter-function-only pattern)"
        )
        assert rel in {p for p, _, _ in raw_sql_offenders}, (
            "The guard failed to catch a raw UPDATE against script_scenes "
            "(the gap a reviewer found: the pattern only matched FROM/JOIN, "
            "so A4's CreateShot/UpdateShot writes would sail through)"
        )
    finally:
        planted.unlink(missing_ok=True)


def test_scoped_script_gateway_takes_only_resolved_handles():
    """The invariant that BUYS scoped_script_gateway.py its ORM_ALLOWED_PATHS
    entry (A4).

    Every public function there must take the run's ``AgentRunScope`` first,
    and every remaining parameter must be either a plain value the model
    cannot use to select a row (``fields``, ``limit``) or an
    already-``Resolved*`` handle. A parameter annotated as a bare id is the
    bypass shape this checks for: it would let a caller hand the gateway a
    model-supplied ``scene_id`` and write to a scene nobody authorized.

    One documented exemption, asserted to stay exactly one: dispatch-time
    ``episode_id_for_script`` runs BEFORE any run exists, on a script id the
    server itself read out of ``conversation_ai_meta`` — there is no scope to
    take, and its result can only narrow the scope about to be created.
    """
    import inspect

    from app.services.ai.scope import scoped_script_gateway as gw

    dispatch_time_exempt = {"episode_id_for_script"}
    assert dispatch_time_exempt == {"episode_id_for_script"}, (
        "Adding a second scope-free function to the gateway needs its own "
        "justification — see this test's docstring."
    )

    resolved_types = {"ResolvedScene", "ResolvedShot", "ResolvedEpisode"}
    offenders: list[str] = []

    for name in gw.__all__:
        obj = getattr(gw, name)
        if not inspect.isfunction(obj) or name in dispatch_time_exempt:
            continue
        params = list(inspect.signature(obj).parameters.values())
        if not params or params[0].name != "scope":
            offenders.append(f"{name}: first parameter is not `scope`")
            continue
        for param in params[1:]:
            annotation = str(param.annotation)
            if param.name.endswith("_id") and not any(
                t in annotation for t in resolved_types
            ):
                offenders.append(
                    f"{name}({param.name}): takes a raw id instead of a "
                    "Resolved* handle"
                )

    assert not offenders, (
        "scoped_script_gateway broke the invariant that justifies its "
        "ORM_ALLOWED_PATHS entry:\n  " + "\n  ".join(offenders)
    )


def test_screenwriting_tools_do_not_bypass_the_resolver():
    """The A4 tool handlers themselves must stay clean under BOTH scanners.

    Belt-and-suspenders next to the two repo-wide tests above: those would
    catch this too, but a targeted assertion names the file a future A5/A6
    author is most likely to reach into the ORM from, and fails with a
    message that says what to do instead."""
    tool_files = {
        "services/ai/tools/screenwriting_tools.py",
        "services/ai/tools/screenwriting_specs.py",
    }
    hits = {
        path
        for path, _, _ in (
            _scan(_ORM_ATTR_PATTERN, ORM_ALLOWED_PATHS)
            + _scan(_ORM_IMPORT_PATTERN, ORM_ALLOWED_PATHS)
            + _scan(_RAW_SQL_PATTERN, ORM_ALLOWED_PATHS)
            + _scan(_REPO_LAYER_PATTERN, REPO_LAYER_ALLOWED_PATHS)
        )
    }
    assert not (hits & tool_files), (
        "A screenwriting tool handler is reaching the scene/shot tables "
        "directly. Route it through resolve_scene/resolve_shot/"
        "resolve_episode and then scoped_script_gateway."
    )


def test_guard_catches_a_join_split_across_two_physical_lines():
    """Isolated regression pin for gap #3 — a JOIN whose keyword and table
    name are on different source lines used to be invisible because the
    scanner tested one ``splitlines()`` entry at a time."""
    planted_dir = BACKEND_APP / "services" / "ai" / "tools"
    planted_dir.mkdir(parents=True, exist_ok=True)
    planted = planted_dir / "_scope_guard_multiline_fixture_do_not_commit.py"
    planted.write_text(
        "SQL = '''\n"
        "SELECT sc.id\n"
        "FROM\n"
        "    public.script_scenes sc\n"
        "JOIN public.script_projects sp ON sp.id = sc.script_id\n"
        "'''\n",
        encoding="utf-8",
    )
    try:
        offenders = _scan(_RAW_SQL_PATTERN, ORM_ALLOWED_PATHS)
        rel = "services/ai/tools/_scope_guard_multiline_fixture_do_not_commit.py"
        assert rel in {p for p, _, _ in offenders}, (
            "The guard still misses a FROM/table pair split across two "
            "physical lines"
        )
    finally:
        planted.unlink(missing_ok=True)


# The repository methods scoped_script_gateway.py is allowed to call. ONE
# entry: the ops channel. See REPO_LAYER_ALLOWED_PATHS' entry for that file.
_GATEWAY_ALLOWED_REPO_METHODS = {"apply_element_ops"}

# Everything the scene repository exposes. A call to any of these on an object
# the gateway got from get_script_scene_repository() is what the scan looks
# for; naming them explicitly (rather than "any attribute call") keeps the
# check from tripping over ordinary local-object method calls.
_SCENE_REPO_METHODS = {
    "apply_element_ops",
    "create",
    "delete",
    "get_by_id",
    "list_by_script",
    "list_ops_by_scene",
    "move_scene",
    "persist_scenes",
    "update_meta",
}


def test_scoped_script_gateway_calls_only_the_ops_channel():
    """A5 review (Important 3): the allow-list entry above is path-level, so
    once ``scoped_script_gateway.py`` is listed, ``repo.update_meta(...)`` or
    ``repo.get_by_id(<model-supplied id>)`` inside it trips nothing — the same
    gap the ORM rule closes for that file via signature introspection.

    This closes it for the repository layer: walk the file's AST, collect every
    ``<something>.<scene-repo-method>(...)`` call, and require the set to be
    exactly ``{apply_element_ops}``. Widening it is then a deliberate edit to
    ``_GATEWAY_ALLOWED_REPO_METHODS`` with a reviewer looking, rather than a
    line that slips in under an entry whose prose still claims otherwise."""
    import ast

    path = BACKEND_APP / "services" / "ai" / "scope" / "scoped_script_gateway.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _SCENE_REPO_METHODS
    }

    assert called == _GATEWAY_ALLOWED_REPO_METHODS, (
        f"scoped_script_gateway.py calls scene-repository methods {sorted(called)}, "
        f"expected exactly {sorted(_GATEWAY_ALLOWED_REPO_METHODS)}.\n\n"
        "That file is allow-listed in REPO_LAYER_ALLOWED_PATHS for ONE reason: "
        "apply_element_ops is the ops channel and re-implementing it would fork "
        "the write path. Every other repository method reachable from there is "
        "unscoped (see script_scene_repository's own docstrings) and would be "
        "callable with a model-supplied id. Route the new need through the "
        "resolver, or justify widening _GATEWAY_ALLOWED_REPO_METHODS here."
    )


def test_the_ops_channel_guard_fires_on_a_planted_second_call():
    """The guard above must actually detect a widened call set — a scan that
    silently matches nothing would pass forever."""
    import ast

    tree = ast.parse(
        "async def f(repo, scene_id):\n"
        "    await repo.apply_element_ops(scene_id, [], 1, 'a')\n"
        "    return await repo.get_by_id(scene_id)\n"
    )
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _SCENE_REPO_METHODS
    }
    assert called == {"apply_element_ops", "get_by_id"}
    assert called != _GATEWAY_ALLOWED_REPO_METHODS
