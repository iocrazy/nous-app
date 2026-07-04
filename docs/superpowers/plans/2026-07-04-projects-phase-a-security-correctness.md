# Projects Phase A：安全与正确性修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 Projects 模块的 IDOR 越权、评论 500、成员 no-op、假归档过滤器、N+1 查询，并清理 Tasks 死表面与 i18n 债——分 3 个 PR 合入 master。

**Architecture:** 权限走 FastAPI dependency 守卫（`scope_guards.py` 现有模式，成对的 read/write 守卫 + 结构化 wiring 测试防回归）。评论修复用独立新表 `project_file_comments`（保持前端契约，不碰 resources 侧 review 系统）。归档用 `projects.archived_at` 时间戳列 + SQL 过滤下推。

**Tech Stack:** FastAPI + SQLAlchemy Core（ORM 2.0 已收官，新代码一律 ORM 路径，不用 supabase REST）、Supabase PostgreSQL migration、React 19 + TypeScript、pytest。

**Spec:** `docs/superpowers/specs/2026-07-04-projects-module-security-and-redesign-design.md`

## Global Constraints

- 后端改动 .py 文件 push 前必须过 lint：`uv run black --check . && uv run isort --check-only . && uv run flake8`（对改动文件跑，CI 会卡）。
- loguru 禁 `%s` 风格插值，用 f-string。
- 新 DB 代码走 SQLAlchemy ORM（`read_scope`/`write_scope`），**不得**新增 supabase REST 调用。
- Migration 文件三位数递增编号；当前最新是 `334_rename_nous_models_to_mediahub_models.sql`，本计划用 `335_`。加列后 migration 末尾必须 `NOTIFY pgrst, 'reload schema';`。
- Migration 只进 `supabase/migrations/` 走 PR+CI apply，不 SSH psql 手跑；不得创建 `*_rollback.sql`（CI 会自动 apply）。
- UI 文本全英文 + i18n key（camelCase），`frontend/public/locales/en.json` + `zh.json` 成对补。
- 前端 Toast：`const { addToast } = useToast()`（`import { useToast } from '../Toast'` 或相对路径），`addToast(msg, 'error')`。
- 每个 PR 独立分支，从最新 `origin/master` 切出（先 `git fetch origin` 无 refspec），寿命 ≤1 天，用 `/ship` 提交。
- commit 信息 conventional commits 格式，无 attribution 尾注。
- Edit 工具 old_string 必须从 Read 输出逐字拷贝；改完 `git diff --stat` 确认无夹带。

---

# PR-A1：权限收口（branch `feature/projects-authz`）

## 背景（给零上下文工程师）

`backend/app/api/projects_router.py`（860 行，42 端点）只有 5 个端点挂了 `verify_project_write_access`。后端全程用 service-role Supabase client（绕过 RLS），所以任何登录用户拿到 project_id 就能读写他人项目。修法沿用仓库现有模式：FastAPI dependency 守卫（见 `backend/app/core/scope_guards.py` 顶部 docstring —— 守卫声明在路由签名里，作者忘不掉）。

权限语义（spec 已定）：
- **read** = project owner ∨ team 成员（team 项目）∨ project_members 任意角色
- **write** = project owner ∨ team 成员 ∨ project_members 角色 ∈ {manager, editor}
- `DELETE /projects/{id}` 额外保持仅 owner（service 层现有检查不动）
- `PUT /projects/{id}` 从「仅 owner」放宽为 write 守卫语义（删掉 service 层 owner 检查）
- 无 project_id 的端点豁免：`GET ""`（列表本就 owner-scoped）、`POST ""`（创建）、`GET /stages/catalog`（全局目录）

`project_members` 表结构（migration 047，070 因 IF NOT EXISTS 是 no-op）：复合主键 `(project_id, user_id)`，**无 id 列**，`role` ∈ manager/editor/viewer/external，`joined_at`。

### Task 1: 守卫函数 — read 守卫新增 + write 守卫扩展 project_members

**Files:**
- Modify: `backend/app/core/scope_guards.py`（54-93 行是现有 `verify_project_write_access`）
- Test: `backend/tests/test_scope_guards.py`

**Interfaces:**
- Produces: `verify_project_read_access(project_id: str, auth: AuthContext) -> None`、`verify_project_write_access(project_id: str, auth: AuthContext) -> None`（签名不变，行为扩展）。404 项目不存在 / 403 无权限 / 通过返回 None。

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_scope_guards.py` 末尾追加（fake client 范式沿用文件内现有 `_FakeClient`/`patch_admin`）：

```python
# ─── verify_project_read_access + project_members branch ──────────────


async def test_read_project_member_viewer_passes(patch_admin):
    from app.core.scope_guards import verify_project_read_access

    patch_admin(
        {
            "projects": [{"owner_id": "owner", "team_id": None}],
            "project_members": [{"role": "viewer"}],
        }
    )
    await verify_project_read_access(project_id="p1", auth=_auth("u1"))


async def test_read_non_member_403(patch_admin):
    from app.core.scope_guards import verify_project_read_access

    patch_admin(
        {
            "projects": [{"owner_id": "owner", "team_id": None}],
            "project_members": [],
        }
    )
    with pytest.raises(HTTPException) as ei:
        await verify_project_read_access(project_id="p1", auth=_auth("u1"))
    assert ei.value.status_code == 403


async def test_read_project_missing_404(patch_admin):
    from app.core.scope_guards import verify_project_read_access

    patch_admin({"projects": []})
    with pytest.raises(HTTPException) as ei:
        await verify_project_read_access(project_id="p1", auth=_auth("u1"))
    assert ei.value.status_code == 404


async def test_write_project_member_editor_passes(patch_admin):
    patch_admin(
        {
            "projects": [{"owner_id": "owner", "team_id": None}],
            "project_members": [{"role": "editor"}],
        }
    )
    await verify_project_write_access(project_id="p1", auth=_auth("u1"))


async def test_write_project_member_viewer_403(patch_admin):
    patch_admin(
        {
            "projects": [{"owner_id": "owner", "team_id": None}],
            "project_members": [{"role": "viewer"}],
        }
    )
    with pytest.raises(HTTPException) as ei:
        await verify_project_write_access(project_id="p1", auth=_auth("u1"))
    assert ei.value.status_code == 403
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_scope_guards.py -v`
Expected: 新增 5 个用例 FAIL（`ImportError: verify_project_read_access` / viewer 403 断言失败），原有 11 个 PASS。

- [ ] **Step 3: 实现守卫**

在 `scope_guards.py` 中把 `verify_project_write_access` 重构为共享 helper + 两个守卫（替换现有 54-93 行函数体，保留原 docstring 语义并更新）：

```python
_PROJECT_WRITE_ROLES = ("manager", "editor")


async def _check_project_access(
    project_id: str, auth: AuthContext, *, write: bool
) -> None:
    """Shared body for the project read/write guards.

    Access: owner, or member of the project's team, or a row in
    project_members — any role for read, manager/editor for write.
    project_members has a composite PK (project_id, user_id); no id column.
    """
    client = await get_async_supabase_admin()
    project_res = (
        await client.table("projects")
        .select("owner_id, team_id")
        .eq("id", project_id)
        .limit(1)
        .execute()
    )
    if not project_res.data:
        raise HTTPException(status_code=404, detail="Project not found")

    project = project_res.data[0]
    if project.get("owner_id") == auth.user_id:
        return

    team_id = project.get("team_id")
    if team_id:
        member_res = (
            await client.table("team_members")
            .select("team_id")
            .eq("team_id", team_id)
            .eq("user_id", auth.user_id)
            .limit(1)
            .execute()
        )
        if member_res.data:
            return

    pm_res = (
        await client.table("project_members")
        .select("role")
        .eq("project_id", project_id)
        .eq("user_id", auth.user_id)
        .limit(1)
        .execute()
    )
    if pm_res.data:
        role = pm_res.data[0].get("role")
        if not write or role in _PROJECT_WRITE_ROLES:
            return

    raise HTTPException(
        status_code=403, detail="You do not have access to this project"
    )


async def verify_project_write_access(
    project_id: str,
    auth: AuthContext = Depends(get_auth),
) -> None:
    """Guard for `/projects/{project_id}/...` write targets."""
    await _check_project_access(project_id, auth, write=True)


async def verify_project_read_access(
    project_id: str,
    auth: AuthContext = Depends(get_auth),
) -> None:
    """Guard for `/projects/{project_id}/...` read targets."""
    await _check_project_access(project_id, auth, write=False)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_scope_guards.py -v`
Expected: 全部 PASS（含原有 11 个——fake client 对缺省表返回 `[]`，原用例行为不变）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/scope_guards.py backend/tests/test_scope_guards.py
git commit -m "feat(projects): add verify_project_read_access + project_members branch in write guard"
```

### Task 2: 42 端点接线 + service 语义修正

**Files:**
- Modify: `backend/app/api/projects_router.py`
- Modify: `backend/app/services/library/projects_service.py`（94-136 行 update/delete_project；241 行 link_media）

**Interfaces:**
- Consumes: Task 1 的两个守卫。
- Produces: 所有 `/{project_id}` 端点在 handler 前强制归属校验；`link_media` 校验媒体归属，`PermissionError` → 403。

- [ ] **Step 1: router 导入两个守卫**

`projects_router.py` 第 17 行改为：

```python
from app.core.scope_guards import (
    verify_project_read_access,
    verify_project_write_access,
)
```

- [ ] **Step 2: 逐端点加守卫参数**

在每个端点签名的 `auth: AuthDep` 之后追加一个参数。**read 守卫**（`_project_guard: None = Depends(verify_project_read_access)`）加到：

- `GET /{project_id}` get_project (L96)
- `GET /{project_id}/style-profile` get_style_profile（L151——把现有 write 守卫**换成** read 守卫，读端点用读语义）
- `GET /{project_id}/current_stage` get_current_stage（L223——同上换成 read）
- `GET /{project_id}/stage_history` get_stage_history（L271——同上换成 read）
- `GET /{project_id}/files` list_files (L299)
- `GET /{project_id}/files/{file_id}` get_file_info (L375)
- `GET /{project_id}/shares` list_project_shares (L421)
- `GET /{project_id}/folders` list_folders (L454)
- `GET /{project_id}/files/{file_id}/versions` list_versions (L551)
- `GET /{project_id}/files/{file_id}/comments` list_comments (L606)
- `GET /{project_id}/tasks` list_tasks (L699)
- `GET /{project_id}/members` list_members (L761)
- `GET /{project_id}/collections` list_collections (L823)

**write 守卫**（`_project_guard: None = Depends(verify_project_write_access)`）加到：

- `PUT /{project_id}` update_project (L110)
- `DELETE /{project_id}` delete_project (L130)
- `POST /{project_id}/files/link-media` link_media (L357)
- `PUT /{project_id}/files/{file_id}` update_file (L389)
- `PUT /{project_id}/files/{file_id}/restore` restore_file (L407)
- `POST /{project_id}/shares` create_share (L433)
- `POST /{project_id}/folders` create_folder (L472)
- `PUT /{project_id}/folders/{folder_id}` rename_folder (L486)
- `DELETE /{project_id}/folders/{folder_id}` delete_folder (L502)
- `PUT /{project_id}/files/{file_id}/move` move_file (L516)
- `DELETE /{project_id}/files/{file_id}` delete_file (L532)
- `POST /{project_id}/files/{file_id}/comments` add_comment (L625)
- `DELETE /{project_id}/files/{file_id}/comments/{comment_id}` delete_comment (L652)
- `PUT /{project_id}/files/{file_id}/review-status` update_review_status (L673)
- `POST /{project_id}/tasks` create_task (L711)、`PUT .../tasks/{task_id}` (L725)、`DELETE .../tasks/{task_id}` (L744)（PR-A3 会整体删除，此处先机械接线保证结构测试全绿）
- `POST /{project_id}/members` add_member (L773)、`PUT .../members/{member_id}` (L790)、`DELETE .../members/{member_id}` (L806)
- `POST /{project_id}/collections` create_collection (L835)、`DELETE .../collections/{collection_id}` (L851)

已有 write 守卫且保持不变：upload_file (L318)、upload_version (L565)、put_style_profile (L175)、put_current_stage (L246)。

示例（get_project 改后）：

```python
@router.get("/{project_id}")
async def get_project(
    project_id: str,
    auth: AuthDep,
    _project_guard: None = Depends(verify_project_read_access),
):
```

- [ ] **Step 3: service 语义修正**

`projects_service.py` `update_project`（L94-114）：删除 owner 检查两行（L112-113 `if project["owner_id"] != user_id: raise PermissionError(...)`），docstring 改为 "Ownership/membership is enforced by the route-level write guard."。`delete_project`（L116-136）owner 检查**保留**（仅 owner 可删是额外收紧）。

`link_media`（L241 起）：Read 该方法现有实现，在取到 media 记录之后、创建 project_file 之前插入归属校验（parsed_media 的属主字段是 `user_id`；orphan 行 user_id 为 None 视为系统资源放行）：

```python
        media_owner = media.get("user_id")
        if media_owner and str(media_owner) != str(user_id):
            raise PermissionError("You do not have access to this media item")
```

router 的 link_media handler（L357-372）在 `except ValueError` 之后加：

```python
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
```

- [ ] **Step 4: 跑相关测试**

Run: `cd backend && uv run pytest tests/test_scope_guards.py tests/test_project_sop_stages.py tests/test_project_style_profile.py -v`
Expected: PASS（style-profile/stage 测试若 mock 了 write 守卫、现改为 read 守卫会失败——按新守卫名更新对应 dependency_overrides）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/projects_router.py backend/app/services/library/projects_service.py backend/tests/
git commit -m "feat(projects): enforce read/write access guards on all 42 project endpoints"
```

### Task 3: wiring 结构测试（防回归）

**Files:**
- Create: `backend/tests/test_projects_authz_wiring.py`

**Interfaces:**
- Consumes: `app.api.projects_router.router`、两个守卫函数。
- Produces: 结构测试——任何人未来加 project 端点忘挂守卫，CI 直接红。

- [ ] **Step 1: 写测试（先写、先看它抓住谁）**

```python
"""Structural test: every /projects/{project_id} route must declare a guard.

test_scope_guards.py tests the guard FUNCTIONS; this pins the WIRING —
a future endpoint added without Depends(verify_project_read|write_access)
fails here instead of shipping an IDOR (the 2026-07-04 audit found 37 of
42 endpoints unguarded)."""

from __future__ import annotations

import pytest

from app.api.projects_router import router
from app.core.scope_guards import (
    verify_project_read_access,
    verify_project_write_access,
)

pytestmark = pytest.mark.unit

# Routes with no {project_id} target: nothing to guard.
EXEMPT = {
    ("GET", "/projects"),
    ("POST", "/projects"),
    ("GET", "/projects/stages/catalog"),
}


def _flat_dependency_calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _flat_dependency_calls(dep)


@pytest.mark.parametrize(
    "route",
    [r for r in router.routes if hasattr(r, "dependant")],
    ids=lambda r: f"{','.join(sorted(r.methods))} {r.path}",
)
def test_project_route_declares_guard(route):
    key = (next(iter(route.methods)), route.path)
    if key in EXEMPT:
        pytest.skip("no project_id target")
    calls = set(_flat_dependency_calls(route.dependant))
    assert (
        verify_project_read_access in calls
        or verify_project_write_access in calls
    ), f"{key} has no project access guard"
```

- [ ] **Step 2: 跑测试**

Run: `cd backend && uv run pytest tests/test_projects_authz_wiring.py -v`
Expected: 全 PASS（Task 2 已接线）。故意注释掉任一端点的守卫再跑一次应 FAIL——验证测试真的在咬人，然后恢复。

- [ ] **Step 3: Commit + lint + ship**

```bash
cd backend && uv run black app/core/scope_guards.py app/api/projects_router.py app/services/library/projects_service.py tests/test_projects_authz_wiring.py tests/test_scope_guards.py && uv run isort --profile black app tests && uv run flake8 app/api/projects_router.py app/core/scope_guards.py tests/test_projects_authz_wiring.py
git add backend/tests/test_projects_authz_wiring.py
git commit -m "test(projects): structural authz wiring test for all project routes"
```

然后用 `/ship` 走 PR 流程（PR 标题：`fix(security): enforce project ownership guards on all /projects endpoints`）。

---

# PR-A2：数据正确性（branch `feature/projects-correctness`，基于 A1 合入后的 master）

## 背景

三个数据问题 + 一个性能问题。评论修复的关键约束（repo 683-682 行 CONSCIOUS-KEEP 注释已论证）：migration 062 重建的 `review_comments` 是 **resources 评审体系**的表（`resource_id BIGINT FK resources`、`version_id UUID FK resource_versions`），而 projects 评论面的 ID 是 `project_files.id` / `file_versions.id`（051 后均为 BIGINT snowflake）——不能混写。因此建**专用表 `project_file_comments`**，前端契约（`ReviewComment` in `frontend/types.ts:761-772`：`file_id/version_id/timestamp_seconds/drawing_data`）零改动。

### Task 4: migration 335 + ORM 模型

**Files:**
- Create: `supabase/migrations/335_project_file_comments_and_archive.sql`
- Modify: `backend/app/models/teams.py`（`Projects` 模型加列；新增 `ProjectFileComments` 模型，放在 `ProjectFiles` 类之后）

**Interfaces:**
- Produces: 表 `project_file_comments`（列见 SQL）；`projects.archived_at TIMESTAMPTZ NULL`；ORM 类 `ProjectFileComments`、`Projects.archived_at: Mapped[Optional[datetime.datetime]]`。

- [ ] **Step 1: 写 migration**

```sql
-- 335_project_file_comments_and_archive.sql
-- Phase A PR-A2 (spec 2026-07-04-projects-module-security-and-redesign):
-- 1) Dedicated comments table for the project-files review surface.
--    Migration 062 dropped the 043-era review_comments columns this surface
--    used (file_id / timestamp_seconds / drawing_data) and rebuilt the table
--    for the RESOURCES review system (resource_id FK). The projects comment
--    endpoints have been 500-ing since. project_files.id / file_versions.id
--    are BIGINT snowflakes (051), so they get their own table instead of
--    overloading review_comments.
-- 2) projects.archived_at — the UI has had Active/Archived filters with no
--    backing column; this makes Archive real.

CREATE TABLE IF NOT EXISTS project_file_comments (
  id                BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  file_id           BIGINT NOT NULL REFERENCES project_files(id) ON DELETE CASCADE,
  version_id        BIGINT REFERENCES file_versions(id) ON DELETE SET NULL,
  author_id         UUID NOT NULL REFERENCES auth.users(id),
  content           TEXT NOT NULL,
  timestamp_seconds FLOAT,
  drawing_data      JSONB,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_project_file_comments_file
  ON project_file_comments(file_id);
CREATE INDEX IF NOT EXISTS idx_project_file_comments_version
  ON project_file_comments(version_id);

ALTER TABLE project_file_comments ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access on project_file_comments"
  ON project_file_comments FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

ALTER TABLE projects ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_projects_archived_at
  ON projects(archived_at) WHERE archived_at IS NOT NULL;

NOTIFY pgrst, 'reload schema';
```

- [ ] **Step 2: 本地/dev 库验证 migration 可 apply**

对 dev 库（NAS `192.168.50.9:55434`，见 memory `reference_nas_dev_db`）或本地 Docker PG 跑一遍，然后核列：

```sql
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name='project_file_comments' ORDER BY ordinal_position;
SELECT column_name FROM information_schema.columns
WHERE table_name='projects' AND column_name='archived_at';
```

Expected: 9 列齐全；projects.archived_at 存在。（记住教训：migration CI 绿 ≠ 能 apply，合并后要盯 Run SQL Migration。）

- [ ] **Step 3: ORM 模型**

`backend/app/models/teams.py`：在 `Projects` 类中加（对齐现有 mapped_column 风格）：

```python
    archived_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
```

在 `ProjectFiles` 类后新增：

```python
class ProjectFileComments(Base):
    __tablename__ = "project_file_comments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["file_id"],
            ["public.project_files.id"],
            ondelete="CASCADE",
            name="project_file_comments_file_id_fkey",
        ),
        ForeignKeyConstraint(
            ["version_id"],
            ["public.file_versions.id"],
            ondelete="SET NULL",
            name="project_file_comments_version_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="project_file_comments_pkey"),
        Index("idx_project_file_comments_file", "file_id"),
        Index("idx_project_file_comments_version", "version_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    file_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    author_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp_seconds: Mapped[Optional[float]] = mapped_column(Float)
    drawing_data: Mapped[Optional[dict]] = mapped_column(JSONB)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
```

（`Float`/`JSONB`/`Text` 若未导入，从 `sqlalchemy` / `sqlalchemy.dialects.postgresql` 补导入，对齐文件头现有 import 块。）

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/335_project_file_comments_and_archive.sql backend/app/models/teams.py
git commit -m "feat(projects): project_file_comments table + projects.archived_at (migration 335)"
```

### Task 5: 评论 repo/service 重写到新表

**Files:**
- Modify: `backend/app/repositories/projects_repository.py`（666-744 行的 4 个方法 + 顶部 N2A 映射区）
- Modify: `backend/app/services/library/projects_service.py`（377-404 add_comment、482-503 get_file_comments/delete_comment——Read 后按实际行号）
- Test: `backend/tests/test_project_file_comments.py`（新建）

**Interfaces:**
- Consumes: `ProjectFileComments` ORM（Task 4）。
- Produces: `get_comments_for_file(file_id, version_id=None) -> List[dict]`、`create_comment(data: dict) -> dict`、`get_comment_by_id(comment_id) -> Optional[dict]`、`delete_comment(comment_id) -> bool`——返回 dict 的 `id/file_id/version_id` 为 str（对齐 `_row` 现有 N2A snowflake→str 惯例），键名与前端 `ReviewComment` 契约一致。

- [ ] **Step 1: 写失败测试**

沿用仓库 repo 单测风格（参考 `backend/tests/repositories/` 内现有文件的 session mock 方式；若该目录用真库 fixture 则对齐真库 fixture）。核心断言：

```python
"""Unit tests for the project_file_comments repository methods (PR-A2).

These four methods were 500-ing in production since migration 062 dropped
the 043-era review_comments columns. They now target the dedicated
project_file_comments table via ORM."""

from __future__ import annotations

import pytest

from app.repositories.projects_repository import ProjectsRepository

pytestmark = pytest.mark.unit


async def test_get_comments_orders_and_filters(fake_read_session):
    # fixture 按 tests/repositories 现有模式注入 fake session；
    # 断言 SQL 目标表为 project_file_comments 且含 version_id 过滤
    repo = ProjectsRepository()
    await repo.get_comments_for_file("123", version_id="456")
    stmt = fake_read_session.last_statement
    assert "project_file_comments" in str(stmt)
    assert "version_id" in str(stmt)


async def test_create_comment_returns_str_ids(fake_write_session):
    repo = ProjectsRepository()
    out = await repo.create_comment(
        {
            "file_id": "123",
            "author_id": "00000000-0000-0000-0000-000000000001",
            "content": "Looks good",
            "timestamp_seconds": 12.5,
        }
    )
    assert isinstance(out.get("id"), str)
    assert out.get("file_id") == "123"
```

（执行者注意：先 Read `backend/tests/repositories/` 里任一现有 repo 测试，把 fixture 命名/装配方式逐字对齐——上面两个用例的断言目标不变，装配代码按仓库现状写。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_project_file_comments.py -v`
Expected: FAIL（方法仍打旧表/旧 client）。

- [ ] **Step 3: 重写 4 个 repo 方法**

删除 666-682 行的 CONSCIOUS-KEEP 注释块（决策已做出：专用表），替换 4 个方法：

```python
    # ------------------------------------------------------------------ #
    # Project file comments (project_file_comments, migration 335).
    # The 043-era review_comments surface died with migration 062; the
    # resources review system owns review_comments now. This surface has
    # its own table keyed by project_files.id / file_versions.id.
    # ------------------------------------------------------------------ #

    async def get_comments_for_file(
        self, file_id: str, version_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Comments for a file, timestamp_seconds ASC nulls-last, created ASC."""
        try:
            stmt = select(ProjectFileComments).where(
                ProjectFileComments.file_id == int(file_id)
            )
            if version_id:
                stmt = stmt.where(ProjectFileComments.version_id == int(version_id))
            stmt = stmt.order_by(
                ProjectFileComments.timestamp_seconds.asc().nulls_last(),
                ProjectFileComments.created_at.asc(),
            )
            async with read_scope() as session:
                result = await session.execute(stmt)
                return [_row(r, _COMMENTS_N2A) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get comments for file {file_id}: {e}")
            raise

    async def create_comment(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new project-file comment."""
        try:
            values = _known_only(data, _COMMENTS_ATTRS)
            for key in ("file_id", "version_id"):
                if values.get(key) is not None:
                    values[key] = int(values[key])
            async with write_scope() as session:
                result = await session.execute(
                    insert(ProjectFileComments)
                    .values(**values)
                    .returning(ProjectFileComments)
                )
                row = result.scalars().first()
                out = _row(row, _COMMENTS_N2A) if row else {}
            logger.info(f"Created comment on file {data.get('file_id')}")
            return out
        except Exception as e:
            logger.error(f"Failed to create comment: {e}")
            raise

    async def get_comment_by_id(self, comment_id: str) -> Optional[Dict[str, Any]]:
        """Get a single comment by id."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ProjectFileComments)
                    .where(ProjectFileComments.id == int(comment_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _row(row, _COMMENTS_N2A) if row else None
        except Exception as e:
            logger.error(f"Failed to get comment {comment_id}: {e}")
            raise

    async def delete_comment(self, comment_id: str) -> bool:
        """Delete a comment by id."""
        try:
            async with write_scope() as session:
                await session.execute(
                    delete(ProjectFileComments).where(
                        ProjectFileComments.id == int(comment_id)
                    )
                )
            logger.info(f"Deleted comment {comment_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete comment {comment_id}: {e}")
            raise
```

文件顶部映射区（对照 `_FILES_N2A`/`_MEMBERS_N2A` 现有写法，通常在 L140-160 附近）加：

```python
_COMMENTS_ATTRS = {p.key for p in ProjectFileComments.__mapper__.column_attrs}
_COMMENTS_N2A = {"id": str, "file_id": str, "version_id": str}
```

（Read 现有 `_MEMBERS_N2A` 的实际形态——若它是「列名→转换函数」以外的结构，逐字对齐该结构。`ProjectFileComments` 加入模型 import 行。注意 `get_comments_for_file` 从「吞错返回 []」改为 raise：这是 spec 的静默吞错修复项，router 已有 500 兜底。）

- [ ] **Step 4: service 层核对**

Read `projects_service.py` 的 `add_comment`（L377-404）与 `get_file_comments`/`delete_comment`（L482-503）：它们组装 `file_id/author_id/content/timestamp_seconds/version_id/drawing_data` dict 后直接调 repo——键名与新表列名一致，无需改动；确认 `delete_comment` 的作者校验（`get_comment_by_id` → author_id 比对）字段仍为 `author_id`（str(UUID) 对比，注意两侧都 `str()`）。

- [ ] **Step 5: 跑测试 + 真库 smoke**

Run: `cd backend && uv run pytest tests/test_project_file_comments.py -v`
Expected: PASS。

真库 smoke（教训：mocked 测试全漏、live E2E 才抓到 asyncpg 类型 bug）：对 dev 环境起后端，用真实 JWT 走一遍 `POST → GET → DELETE /api/v1/projects/{id}/files/{fid}/comments`，确认 200 且读回内容一致。

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/projects_repository.py backend/app/services/library/projects_service.py backend/tests/test_project_file_comments.py
git commit -m "fix(projects): rewrite file comments onto project_file_comments — ends prod 500"
```

### Task 6: 成员 update/delete no-op 修复

**Files:**
- Modify: `backend/app/repositories/projects_repository.py`（1081-1122 行两个方法）
- Modify: `frontend/components/ProjectMembersPanel.tsx`（核对传参）
- Test: `backend/tests/test_project_members_repo.py`（新建）

**Interfaces:**
- Produces: `update_member(member_id, project_id, data)` / `delete_member(member_id, project_id)`——`member_id` 语义为成员的 **user_id**（UUID str），按复合主键 `(project_id, user_id)` 过滤；不存在时 update 返回 None（router 已映射 404）、delete 返回 False。

- [ ] **Step 1: 写失败测试**

`backend/tests/test_project_members_repo.py`（装配方式对齐 tests/repositories 现状，断言目标）：

```python
async def test_update_member_filters_by_composite_pk(fake_write_session):
    repo = ProjectsRepository()
    await repo.update_member(
        "00000000-0000-0000-0000-000000000001", "42", {"role": "editor"}
    )
    stmt = str(fake_write_session.last_statement)
    assert "project_members" in stmt
    assert "user_id" in stmt and "project_id" in stmt
    assert '"id"' not in stmt  # the phantom column that made this a no-op


async def test_delete_member_reports_missing_row(fake_write_session_rowcount_zero):
    repo = ProjectsRepository()
    assert await repo.delete_member(
        "00000000-0000-0000-0000-000000000001", "42"
    ) is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_project_members_repo.py -v` — Expected: FAIL。

- [ ] **Step 3: 重写两个方法（ORM，复合主键）**

```python
    async def update_member(
        self, member_id: str, project_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update a member's row. ``member_id`` is the member's user_id —
        project_members' PK is (project_id, user_id); there is no id column
        (the pre-2026-07 version filtered a phantom ``id`` and silently
        no-op'd)."""
        try:
            values = _known_only(data, _MEMBERS_ATTRS)
            async with write_scope() as session:
                result = await session.execute(
                    update(ProjectMembers)
                    .where(ProjectMembers.project_id == int(project_id))
                    .where(ProjectMembers.user_id == uuid.UUID(member_id))
                    .values(**values)
                    .returning(ProjectMembers)
                )
                row = result.scalars().first()
                return _row(row, _MEMBERS_N2A) if row else None
        except Exception as e:
            logger.error(f"Failed to update member {member_id}: {e}")
            raise

    async def delete_member(self, member_id: str, project_id: str) -> bool:
        """Remove a member. ``member_id`` is the member's user_id (see
        update_member). Returns False when no row matched."""
        try:
            async with write_scope() as session:
                result = await session.execute(
                    delete(ProjectMembers)
                    .where(ProjectMembers.project_id == int(project_id))
                    .where(ProjectMembers.user_id == uuid.UUID(member_id))
                )
                return bool(result.rowcount)
        except Exception as e:
            logger.error(f"Failed to delete member {member_id}: {e}")
            raise
```

（`import uuid` 若文件未导入则补。）router `remove_member`（L806-815）在 service 返回 False 时应 404：Read service `remove_member`（L650）——让它把 False 上抛为 `ValueError("Member not found")`，router 加 `except ValueError` → 404。

- [ ] **Step 4: 前端传参核对**

Read `frontend/components/ProjectMembersPanel.tsx` 和 `frontend/services/projectsService.ts` 的 `updateMemberRole`/`removeProjectMember` 调用点：确认传给 `{member_id}` 路径段的值是成员行的 `user_id`。若传的是 `member.id`（undefined），改为 `member.user_id`。

- [ ] **Step 5: 跑测试 + 真库 smoke**

Run: `cd backend && uv run pytest tests/test_project_members_repo.py -v` — Expected: PASS。
真库 smoke：dev 环境 `POST /members` 加一个成员 → `PUT .../members/{user_id}` 改 role → `GET /members` 确认 role 真的变了（写后读回）→ `DELETE` → 读回确认消失。

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/projects_repository.py backend/app/services/library/projects_service.py backend/app/api/projects_router.py frontend/components/ProjectMembersPanel.tsx frontend/services/projectsService.ts backend/tests/test_project_members_repo.py
git commit -m "fix(projects): member update/delete filter by composite PK — was silent no-op"
```

### Task 7: 归档功能 + 列表过滤下推 + N+1 修复

**Files:**
- Modify: `backend/app/repositories/projects_repository.py`（296-326 get_user_projects；433-454 file_count 区新增批量方法）
- Modify: `backend/app/services/library/projects_service.py`（39-61 get_projects_with_counts；94-114 update_project）
- Modify: `backend/app/api/projects_router.py`（49-78 list_projects）
- Modify: `backend/app/schemas/projects.py`（ProjectUpdate、ProjectResponse）
- Modify: `frontend/types.ts`（Project 接口）、`frontend/services/projectsService.ts`（fetchProjects/updateProject）、`frontend/pages/ProjectsPage.tsx`（107-141 过滤逻辑）、`frontend/components/ProjectContextMenu.tsx`（加菜单项）
- Test: `backend/tests/test_projects_list_filters.py`（新建）

**Interfaces:**
- Produces: `get_user_projects(user_id, team_id=None, project_type=None, starred=None, archived=None)`；`get_project_file_counts(project_ids: list) -> Dict[str, int]`；`GET /projects?archived=true|false`（router 缺省 `Query(None)` = 归档与否都返回，前端 VIEW 过滤器本地分流，见 Step 5 决策）；`ProjectUpdate.archived: Optional[bool]`（service 映射为 `archived_at` 时间戳/None）；前端 `Project.archived_at: string | null`。

- [ ] **Step 1: 写失败测试**

`backend/tests/test_projects_list_filters.py`：断言 repo 生成的 SQL 含 `archived_at IS NULL`（archived=False）、`is_starred`、`project_type` WHERE 子句；断言 `get_project_file_counts` 单条语句含 `GROUP BY`。装配对齐 tests/repositories 现状。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_projects_list_filters.py -v` — Expected: FAIL。

- [ ] **Step 3: repo 实现**

`get_user_projects` 签名与 WHERE 扩展：

```python
    async def get_user_projects(
        self,
        user_id: str,
        team_id: str | None = None,
        project_type: str | None = None,
        starred: bool | None = None,
        archived: bool | None = False,
    ) -> List[Dict[str, Any]]:
        """Projects owned by the user, updated_at desc. Filters push down
        to SQL (they were previously applied in-memory in the router).
        archived: False → active only (default), True → archived only,
        None → both."""
        try:
            stmt = (
                select(Projects)
                .where(Projects.owner_id == user_id)
                .order_by(Projects.updated_at.desc())
            )
            if team_id == "personal":
                stmt = stmt.where(Projects.team_id.is_(None))
            elif team_id:
                stmt = stmt.where(Projects.team_id == int(team_id))
            if project_type:
                stmt = stmt.where(Projects.project_type == project_type)
            if starred is not None:
                stmt = stmt.where(Projects.is_starred.is_(starred))
            if archived is True:
                stmt = stmt.where(Projects.archived_at.is_not(None))
            elif archived is False:
                stmt = stmt.where(Projects.archived_at.is_(None))
            async with read_scope() as session:
                result = await session.execute(stmt)
                return [_row(r, _PROJECTS_N2A) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get projects for user {user_id}: {e}")
            return []
```

批量 file count（放在 `get_project_file_count` 之后，保留旧单个方法供他处调用）：

```python
    async def get_project_file_counts(
        self, project_ids: List[str]
    ) -> Dict[str, int]:
        """Non-trashed file counts for many projects in ONE query
        (replaces the per-project N+1 in get_projects_with_counts)."""
        if not project_ids:
            return {}
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ProjectFiles.project_id, func.count())
                    .where(ProjectFiles.project_id.in_([int(p) for p in project_ids]))
                    .where(ProjectFiles.is_trashed.is_(False))
                    .group_by(ProjectFiles.project_id)
                )
                return {str(pid): count for pid, count in result.all()}
        except Exception as e:
            logger.error(f"Failed to get file counts: {e}")
            return {}
```

- [ ] **Step 4: service + router**

`get_projects_with_counts`：

```python
    async def get_projects_with_counts(
        self,
        user_id: str,
        team_id: str | None = None,
        project_type: str | None = None,
        starred: bool | None = None,
        archived: bool | None = False,
    ) -> list:
        projects = await self.repo.get_user_projects(
            user_id,
            team_id=team_id,
            project_type=project_type,
            starred=starred,
            archived=archived,
        )
        if not projects:
            return []
        counts = await self.repo.get_project_file_counts([p["id"] for p in projects])
        return [{**p, "file_count": counts.get(str(p["id"]), 0)} for p in projects]
```

（删除方法内 `import asyncio`。）`update_project` 在 repo 调用前映射 archived：

```python
        if "archived" in data:
            from datetime import datetime, timezone

            data["archived_at"] = (
                datetime.now(timezone.utc) if data.pop("archived") else None
            )
```

（注意 router 用 `exclude_none=True`，`archived=False` 不会被剔除——`ProjectUpdate.archived` 是 Optional[bool]，None 才剔除。unarchive 传 `archived: false` 可达。）

router `list_projects`（L49-78）：加 `archived: Optional[bool] = Query(False, description="true = archived only, false = active only")` 参数，删除 L70-73 的内存过滤，改为全部透传 service。

`backend/app/schemas/projects.py`：`ProjectUpdate` 加 `archived: Optional[bool] = None`；`ProjectResponse` 加 `archived_at: Optional[datetime] = None`（Read 该文件对齐现有 Optional/datetime 写法）。

- [ ] **Step 5: 前端**

- `frontend/types.ts` `Project`（L668-684）加 `archived_at: string | null;`，并删掉任何 `status` 残留引用。
- `frontend/pages/ProjectsPage.tsx` L107-141：Active 过滤 `!p.archived_at`，Archived 过滤 `!!p.archived_at`（替换 `p.status` 判断）；注意列表默认请求需带 `archived=null`（两者都要，供本地分组计数）——`fetchProjects` 透传 `archived: 'all'` 时后端 archived=None：router 侧用 `Optional[bool]`，前端全量场景传 `?archived=` 省略并在后端把「省略」当 None？**决策**：保持简单——前端列表页一次性拉全量（`fetchProjects({ archived: undefined })` → 不带参数），后端 `archived` Query 默认值改为 `None`（两者都返回），归档隐藏逻辑由前端 VIEW 过滤器承担（数据量在个人/团队规模可接受，100k 级是资源库不是项目列表）。router 参数于是为 `Query(None, ...)`，语义：省略 = 全部。
- `frontend/services/projectsService.ts` `updateProject` 类型放开 `{ archived?: boolean }`。
- `frontend/components/ProjectContextMenu.tsx`：菜单加一项 Archive/Unarchive（图标 `Archive` from lucide-react），点击调 `updateProject(project.id, { archived: !project.archived_at })` 后刷新列表；文案 `t('projects.menu.archive')` / `t('projects.menu.unarchive')`，en.json：`"archive": "Archive"`, `"unarchive": "Unarchive"`；zh.json：`"archive": "归档"`, `"unarchive": "取消归档"`（放在 `projects.menu` 命名空间，若无则新建）。

- [ ] **Step 6: 跑测试 + build**

Run: `cd backend && uv run pytest tests/test_projects_list_filters.py tests/test_project_file_comments.py tests/test_project_members_repo.py -v` — Expected: PASS。
Run: `cd frontend && npm run build` — Expected: 成功、无 TS 错误（`p.status` 引用清干净的证据）。

- [ ] **Step 7: Commit + lint + ship**

```bash
cd backend && uv run black app tests && uv run isort --profile black app tests && uv run flake8 app/repositories/projects_repository.py app/services/library/projects_service.py app/api/projects_router.py
git add -A backend/app backend/tests supabase/migrations frontend
git commit -m "feat(projects): real archive support, SQL filter pushdown, batched file counts"
```

`/ship`（PR 标题：`fix(projects): comments 500, member no-op, archive support, N+1 — data correctness batch`）。合并后**盯 Run SQL Migration workflow** 确认 335 apply 成功。

---

# PR-A3：死代码与体验债清理（branch `feature/projects-cleanup`，基于 A2 合入后的 master）

### Task 8: 删除 Tasks 死表面

**Files:**
- Delete: `frontend/components/KanbanBoard.tsx`、`frontend/services/projectTasksService.ts`
- Modify: `frontend/components/project/ProjectNavSidebar.tsx`（NAV_SECTIONS L42-52 删 Tasks 项）、`frontend/pages/ProjectsPage.tsx`（Tasks tab 渲染分支）、`frontend/features/projects/stageTools.ts`（若 TOOL_CATALOG 有指向 tasks tab 的条目则删）
- Modify: `backend/app/api/projects_router.py`（694-753 行 4 个 task 端点整段删除）、`backend/app/services/library/projects_service.py`（585-616 task 方法）、`backend/app/repositories/projects_repository.py`（task 相关方法）、`backend/app/schemas/projects.py`（TaskCreateRequest/TaskUpdateRequest 及 router import）
- Modify: `backend/app/models/teams.py`（`ProjectTasks` 模型删除——migration 176 已 DROP 该表）

**背景**：migration 175/176 把 project_tasks backfill 到 issues 系统后 DROP 了表。这些端点/组件自那以后全是死的（点开就 500）。

- [ ] **Step 1: 全仓引用清点**

```bash
grep -rn "KanbanBoard\|projectTasksService\|ProjectTasks\b\|TaskCreateRequest\|TaskUpdateRequest\|task_assets\|TaskAssets" frontend backend/app --include="*.ts" --include="*.tsx" --include="*.py" | grep -v test
```

按输出逐一处理：调用点删除、import 清理。若 `ProjectTasks`/`TaskAssets` 被 `models/__init__.py` 或其他仓库文件引用，一并删除引用（176 注释确认表已 DROP，任何存活引用都是死代码）。若发现**仍被活跃消费**的引用（如 issues 桥接），停下来在 PR 描述里说明并保留该处。

- [ ] **Step 2: 删除并验证**

删文件、删代码段后：

```bash
cd frontend && npm run build
cd ../backend && uv run pytest tests/ -x -q
```

Expected: build 成功；pytest 全绿（涉及 task 的旧测试文件一并删除）。

- [ ] **Step 3: 更新 wiring 测试豁免**

`backend/tests/test_projects_authz_wiring.py`：task 路由已不存在，参数化自动收缩，无需改动——跑一遍确认。

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(projects): remove dead Tasks surface (table dropped in migration 176)"
```

### Task 9: 死侧栏 + stage 重复请求 + 静默吞错

**Files:**
- Delete: `frontend/components/ProjectsSidebar.tsx`（201 行，全仓零引用）
- Modify: `frontend/pages/ProjectsPage.tsx`、`frontend/components/project/StageSelector.tsx`、`frontend/components/project/StageToolGrid.tsx`
- Modify: `frontend/components/ProjectCollectModal.tsx`（L34/51/59）、`frontend/components/ProjectShareModal.tsx`（L42）、`frontend/components/ProjectVersionModal.tsx`（L35）

- [ ] **Step 1: 删除 ProjectsSidebar.tsx**

先验证零引用：`grep -rn "ProjectsSidebar" frontend --include="*.tsx" --include="*.ts"` — 仅自身命中则删除。

- [ ] **Step 2: current_stage 状态提升**

`ProjectsPage.tsx`（详情视图渲染处，L213 附近）：新增一次 `fetchCurrentStage(projectId)`（`useEffect` on selectedProject 变化），state `currentStage: ProjectStage | null` + `handleStageChange(stage)` 回调（PUT 成功后 setState）。`StageSelector` 与 `StageToolGrid` 改为受控组件：删除各自内部的 `fetchCurrentStage` 调用（StageSelector L42、StageToolGrid L27），新增 props `currentStage`、（StageSelector 另加）`onStageChange`。StageSelector 的 PUT 逻辑保留（L61-77 handleStageClick），成功后调 `onStageChange(newStage)` 而非本地 setState。

- [ ] **Step 3: 3 个 modal 吞错修复**

每处空 catch 改为（以 CollectModal 为例，其余同型）：

```tsx
    } catch (err) {
      console.error('Failed to load project collections:', err);
      addToast(t('projects.errors.loadCollectionsFailed'), 'error');
    }
```

组件顶部 `const { addToast } = useToast();`（import 对齐同目录其他组件的 Toast import 路径）。i18n key 按动作命名（`loadCollectionsFailed` / `createCollectionFailed` / `deleteCollectionFailed` / `loadSharesFailed` / `loadVersionsFailed`），en/zh.json 成对补（en 例：`"loadCollectionsFailed": "Failed to load collection links"`；zh：`"loadCollectionsFailed": "加载收集链接失败"`）。

- [ ] **Step 4: 验证 + Commit**

`cd frontend && npm run build` — Expected: 成功。手动 smoke：详情页切阶段，Network 面板确认 `current_stage` 只请求一次。

```bash
git add -A frontend
git commit -m "refactor(projects): drop dead sidebar, dedupe stage fetch, surface swallowed modal errors"
```

### Task 10: i18n 收口（消灭中英混杂）

**Files:**
- Modify: `frontend/components/project/ProjectFilterSidebar.tsx`（L23-29 VIEW_FILTERS、L88/93/113）
- Modify: `frontend/components/project/ProjectNavSidebar.tsx`（L42-52 NAV_SECTIONS、L149/165/206/232/243/332/336/341）
- Modify: `frontend/pages/ProjectsPage.tsx`（L137-141 filterTitle）
- Modify: `frontend/components/ProjectCard.tsx`（L14-16 类型 fallback、L37-41 相对时间）、`frontend/components/ProjectsListView.tsx`（L89-93 formatRelativeTime、L202 sort title）
- Modify: `frontend/public/locales/en.json`、`frontend/public/locales/zh.json`

- [ ] **Step 1: 补翻译 key**

先 grep 现有命名空间避免撞 key：`grep -n '"projects"' -A 40 frontend/public/locales/en.json | head -60`，且查是否已有通用相对时间 key（`grep -n "justNow\|minutesAgo\|hoursAgo\|daysAgo" frontend/public/locales/en.json`）。在 `projects` 命名空间下补（已存在的跳过）：

en.json：
```json
"view": { "title": "View", "all": "All", "starred": "Starred", "recent": "Recent", "active": "Active", "archived": "Archived", "folders": "Folders" },
"nav": { "files": "Files", "scripts": "Scripts", "storyboard": "Storyboard", "output": "Output", "trash": "Trash", "settings": "Settings", "switchProject": "Switch project...", "noProjectsFound": "No projects found", "allProjects": "All Projects", "expandSidebar": "Expand sidebar", "collapseSidebar": "Collapse sidebar", "created": "Created" },
"filterTitle": { "starred": "Starred Projects", "recent": "Recent Projects", "active": "Active Projects", "archived": "Archived Projects" },
"sort": { "ascending": "Ascending", "descending": "Descending" }
```

zh.json（对应值）：
```json
"view": { "title": "视图", "all": "全部", "starred": "已收藏", "recent": "最近", "active": "活跃", "archived": "已归档", "folders": "文件夹" },
"nav": { "files": "文件", "scripts": "脚本", "storyboard": "分镜", "output": "输出", "trash": "回收站", "settings": "设置", "switchProject": "切换项目...", "noProjectsFound": "未找到项目", "allProjects": "全部项目", "expandSidebar": "展开侧栏", "collapseSidebar": "收起侧栏", "created": "创建于" },
"filterTitle": { "starred": "收藏的项目", "recent": "最近的项目", "active": "活跃项目", "archived": "已归档项目" },
"sort": { "ascending": "升序", "descending": "降序" }
```

相对时间：若已有通用 time key 则复用；否则在 `common` 下补 `"time": { "justNow": "just now", "minutesAgo": "{{count}}m ago", "hoursAgo": "{{count}}h ago", "daysAgo": "{{count}}d ago" }`（zh：`刚刚` / `{{count}} 分钟前` / `{{count}} 小时前` / `{{count}} 天前`）。

- [ ] **Step 2: 组件接线**

`ProjectFilterSidebar.tsx` / `ProjectNavSidebar.tsx`：文件加 `const { t } = useTranslation();`（import 对齐其他组件），把 VIEW_FILTERS/NAV_SECTIONS 的 `label` 字段改为 `labelKey`（如 `'projects.view.starred'`），渲染处 `t(item.labelKey)`。其余硬编码字符串逐处换 `t()`。`ProjectsPage.tsx` filterTitle 分支换 `t('projects.filterTitle.starred')` 等。`ProjectCard.tsx` 类型 fallback 与相对时间函数换 `t()`（时间函数移到共享 util 时带 t 参数，或改用现有 util——先 grep `formatRelativeTime` 是否有共享实现，有则收敛到一处）。

- [ ] **Step 3: 验证 + Commit + ship**

`cd frontend && npm run build`；切中文 locale 目检列表页与详情侧栏——不再出现英文残留（这是本 PR 的验收画面，对照用户 2026-07-04 的截图问题）。

```bash
git add -A frontend
git commit -m "fix(projects): wire ProjectFilterSidebar/ProjectNavSidebar/cards through i18n"
```

`/ship`（PR 标题：`refactor(projects): remove dead Tasks surface + sidebar, dedupe stage fetch, i18n pass`）。

---

## 收尾

- 三个 PR 全部合入后：跑一遍 memory 的发版错误漏斗 SQL（application_logs 7 天 ERROR 聚合）确认无新增 schema 漂移错误；确认生产 comments/members 端点 500 消失。
- 更新 memory：`project_orm2_migration.md` 的 parked 台账中「projects 评论 500 + 成员 no-op」标记为已修复（PR 号回填）。
- Phase B（UI 智能化重设计）另起 brainstorm/design-shotgun 流程，见 spec Phase B 节。
