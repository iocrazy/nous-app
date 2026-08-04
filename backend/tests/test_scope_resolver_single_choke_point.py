"""Architecture guard: script_scenes / script_shots / episodes may be joined
against script_projects for authorization in EXACTLY ONE place —
``app/services/ai/scope/scope_resolver.py`` (A2 — screenwriting agent layer,
spec §3.2: "唯一解析器... 禁止各工具自行实现可见性判断").

Mirrors ``tests/test_run_recorder_coverage.py``'s shape: grep the backend for
patterns that indicate a NEW site doing this join (or reaching for the
underlying ORM models / repository getters directly) and compare against a
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

# Files allowed to call the scene/shot/episode/script-project repository
# GETTER FUNCTIONS directly (a subtler bypass: skip the ORM classes but
# still skip the resolver by calling get_script_scene_repository().get_by_id
# straight from new tool code — get_by_id applies NO scope filter of its own,
# see script_scene_repository.py's docstring). Everything here predates A2
# and sits behind the same authenticated REST/workflow layer as above.
REPO_GETTER_ALLOWED_PATHS: dict[str, str] = {
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
}

_ORM_ATTR_PATTERN = re.compile(r"\b(?:" + "|".join(_ORM_CLASS_NAMES) + r")\.")
_ORM_IMPORT_PATTERN = re.compile(
    r"^\s*(?:from|import).*\b(?:" + "|".join(_ORM_CLASS_NAMES) + r")\b"
)
_RAW_SQL_PATTERN = re.compile(
    r"(?:FROM|JOIN)\s+(?:public\.)?(?:script_scenes|script_shots|script_projects|episodes)\b",
    re.IGNORECASE,
)
_REPO_GETTER_PATTERN = re.compile(
    r"\bget_script_scene_repository\(\)|\bget_script_shot_repository\(\)|"
    r"\bget_episode_repository\(\)|\bget_script_project_repository\(\)"
)


def _is_allowed(rel_path: str, allowed: dict[str, str]) -> bool:
    for allowed_path in allowed:
        if rel_path == allowed_path or rel_path.startswith(
            allowed_path.rstrip("/") + "/"
        ):
            return True
    return False


def _scan(pattern: re.Pattern, allowed: dict[str, str]) -> list[tuple[str, int, str]]:
    offenders: list[tuple[str, int, str]] = []
    for py_file in BACKEND_APP.rglob("*.py"):
        rel = py_file.relative_to(BACKEND_APP).as_posix()
        if _is_allowed(rel, allowed):
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(content.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if pattern.search(line):
                offenders.append((rel, line_no, line.strip()))
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


def test_no_new_direct_repository_getter_calls_outside_resolver_and_rest_layer():
    offenders = _scan(_REPO_GETTER_PATTERN, REPO_GETTER_ALLOWED_PATHS)
    if offenders:
        raise AssertionError(
            _format(
                offenders,
                "Direct call to get_script_scene_repository() / "
                "get_script_shot_repository() / get_episode_repository() / "
                "get_script_project_repository() outside the pre-A2 "
                "authenticated REST/workflow layer: these getters apply NO "
                "scope filter of their own (see e.g. "
                "script_scene_repository.py's get_by_id docstring). Route "
                "through resolve_scene / resolve_shot / resolve_episode "
                "instead, or add a justified entry to "
                "REPO_GETTER_ALLOWED_PATHS in this test.",
            )
        )


# ---------------------------------------------------------------------- #
# Prove the guard isn't decorative: plant a deliberately-bad snippet in a
# TEMP file under app/ (not committed — created + removed within the test)
# and assert the scanner actually flags it. Uses tmp_path-free direct
# creation/cleanup so it exercises the SAME BACKEND_APP.rglob walk the real
# tests use, not a copy of the scanning logic.
# ---------------------------------------------------------------------- #


def test_guard_fires_on_a_planted_bypass():
    planted_dir = BACKEND_APP / "services" / "ai" / "tools"
    planted_dir.mkdir(parents=True, exist_ok=True)
    planted = planted_dir / "_scope_guard_fixture_do_not_commit.py"
    planted.write_text(
        "from app.models import ScriptScenes\n"
        "\n"
        "async def sneaky_lookup(scene_id):\n"
        "    # bypasses resolve_scene entirely\n"
        "    return ScriptScenes.id\n",
        encoding="utf-8",
    )
    try:
        offenders = _scan(_ORM_ATTR_PATTERN, ORM_ALLOWED_PATHS) + _scan(
            _ORM_IMPORT_PATTERN, ORM_ALLOWED_PATHS
        )
        offending_files = {path for path, _, _ in offenders}
        assert (
            "services/ai/tools/_scope_guard_fixture_do_not_commit.py" in offending_files
        ), "The guard failed to catch a deliberately-planted direct ORM bypass"
    finally:
        planted.unlink(missing_ok=True)
