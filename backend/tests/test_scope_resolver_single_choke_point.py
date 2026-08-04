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
    "workflows/script_import.py": (
        "pre-A2 DBOS workflow (fountain/prose script import), triggered "
        "from the authenticated REST layer; constructs "
        "ScriptSceneRepository() directly for the same persist_scenes path "
        "as script_scene_convert.py above"
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
