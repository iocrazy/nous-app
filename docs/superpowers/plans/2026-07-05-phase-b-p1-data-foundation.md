# Phase B / Phase 1 数据地基 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 spec v3 的数据地基：episodes / script_scenes / script_ops 三表 + anchor-op 元素协议（content_version 乐观并发、幂等、op 落账、派生文本）+ 全端点 authz + Convert-to-scenes AI 端点。**不含编辑器前端**（UI 选稿后另出 plan）。

**Architecture:** 三个迁移（episodes → scenes+ops → 回填）；ORM 模型进 `backend/app/models/scripts.py`；新 repo `script_scene_repository.py`（纯函数 `apply_ops` 分离出 `scene_ops.py` 便于穷举单测）；新 router `script_scenes_router.py` + `episodes_router.py`；共享守卫 `verify_script_access` 提升到 `app/core/scope_guards.py`（顺带修 script_canvas_router 既有 IDOR）；Convert-to-scenes 走 DBOS workflow（wf_id 串联 + resolve_script_provider_config——本仓刚踩过的两个坑）。

**Tech Stack:** FastAPI + SQLAlchemy 2.0（read_scope/write_scope）、Supabase PG（BIGINT snowflake）、DBOS workflow、pytest。

**Spec:** `docs/superpowers/specs/2026-07-05-phase-b-script-storyboard-design.md`（v3）

## Global Constraints

- 迁移号 claim **338/339/340**（337 已被 chat_media_object_store 占用；合并时若再被占取下一空号并同步改 plan 引用）；每个迁移末尾 `NOTIFY pgrst, 'reload schema';`；不建 `*_rollback.sql`。
- 后端新代码一律 ORM 路径（read_scope/write_scope），禁 supabase REST；loguru 用 f-string。
- **bigint 写路径必 coerce**（`_bigint()`）；**path param 与 row 字段比较必双侧 str coerce**（#1006/#1011 坑）。
- **所有新端点挂 authz 守卫** + 结构化 wiring 测试（Phase A 范式）。
- DBOS 派发端点必须 `wf_id = str(uuid4())` 串联 `mgr.create(dbos_workflow_id=wf_id)` 与 `start_workflow_routed(workflow_id=wf_id)`（#1017 坑）；AI 调用必须经 `resolve_script_provider_config(user_id)` 传 provider config（#1025/#1030 坑）。
- 异步派发端点返回**扁平** `{"success": True, "task_id": ...}`（#1019 契约）。
- lint gate：`uv run black <files> && uv run isort --profile black <files> && uv run flake8 <files>`；commit 无 attribution 尾注；每 PR ≤1 天，从最新 origin/master 切 worktree（`bash scripts/worktree-manager.sh create <branch>`）。
- 真库验证用 NAS dev 库：`source /private/tmp/claude-501/-Volumes-program-project-code-repos-nous/7ba3e65d-49b1-47af-83b8-981d0cd018a1/scratchpad/dev_db.env` → `psql "$DEV_DSN"`（dev 有漂移史，用前先 information_schema 核表）。
- **DB 端点 done 前必跑一次真库 round-trip**（mocked 测试漏 asyncpg 类型坑——本仓血泪律）。

## File Structure

```
supabase/migrations/338_episodes.sql                       (new)
supabase/migrations/339_script_scenes_and_ops.sql          (new)
supabase/migrations/340_backfill_episodes.sql              (new)
backend/app/models/scripts.py                              (modify: +Episodes, +ScriptScenes, +ScriptOps; ScriptProjects.episode_id)
backend/app/models/__init__.py                             (modify: exports)
backend/app/services/script/scene_ops.py                   (new: 纯函数 apply_ops / extract_text / OpError)
backend/app/repositories/script_scene_repository.py        (new: scenes CRUD + apply_element_ops + reorder + ops log)
backend/app/repositories/episode_repository.py             (new)
backend/app/core/scope_guards.py                           (modify: +verify_script_access)
backend/app/api/episodes_router.py                         (new)
backend/app/api/script_scenes_router.py                    (new)
backend/app/api/script_canvas_router.py                    (modify: 补 update/delete_chapter 访问校验——既有 IDOR 顺带修)
backend/app/api/script_ai_router.py                        (modify: +convert-to-scenes 端点)
backend/app/workflows/script_scene_convert.py              (new: chapter prose→scenes workflow)
backend/app/api/__init__.py 或 main.py                     (modify: 注册两个新 router)
backend/tests/test_scene_ops.py                            (new: 纯函数穷举)
backend/tests/test_script_scene_repository.py              (new)
backend/tests/test_episodes_scenes_authz_wiring.py         (new)
backend/tests/test_scene_convert_dispatch.py               (new: wf_id+provider pin)
backend/tests/integration/test_script_scenes_orm.py        (new: 真库 round-trip)
```

**PR 切分**：PR-B1 = Task 1-3（迁移+模型+纯函数）；PR-B2 = Task 4-6（repo+守卫+router）；PR-B3 = Task 7-8（Convert-to-scenes + 真库 E2E 收口）。每 PR 走 SDD（实现→任务审→ship 前终审）。

---

### Task 1: 迁移 338/339/340 + dev 库验证

**Files:** Create 三个迁移文件。

**Interfaces:** Produces 表结构（下述 SQL 即契约，后续所有任务依赖列名）。

- [ ] **Step 1: 写 338_episodes.sql**

```sql
-- 338_episodes.sql — Phase B P1: episodes 维度（spec v3 §2.1）
CREATE TABLE IF NOT EXISTS episodes (
  id          BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  project_id  BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  title       VARCHAR(200) NOT NULL DEFAULT 'Ep 1',
  sort_order  INTEGER NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_episodes_project ON episodes(project_id, sort_order);
ALTER TABLE episodes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on episodes" ON episodes;
CREATE POLICY "Service role full access on episodes" ON episodes FOR ALL
  USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

ALTER TABLE script_projects ADD COLUMN IF NOT EXISTS episode_id BIGINT REFERENCES episodes(id) ON DELETE RESTRICT;
CREATE INDEX IF NOT EXISTS idx_script_projects_episode ON script_projects(episode_id);
NOTIFY pgrst, 'reload schema';
```

- [ ] **Step 2: 写 339_script_scenes_and_ops.sql**

```sql
-- 339_script_scenes_and_ops.sql — Phase B P1: scene 层 + 操作日志（spec v3 §2.1/§2.2）
CREATE TABLE IF NOT EXISTS script_scenes (
  id               BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  script_id        BIGINT NOT NULL REFERENCES script_projects(id) ON DELETE CASCADE,
  chapter_id       BIGINT REFERENCES script_chapters(id) ON DELETE SET NULL,
  heading_int_ext  VARCHAR(10),
  location_text    TEXT,
  location_id      BIGINT,            -- 实体软引用，无 FK（spec §2.4）
  time_of_day      VARCHAR(20),
  content_json     JSONB NOT NULL DEFAULT '[]'::jsonb,
  content          TEXT NOT NULL DEFAULT '',
  content_version  INTEGER NOT NULL DEFAULT 0,
  position_x       DOUBLE PRECISION, position_y DOUBLE PRECISION,
  width            DOUBLE PRECISION, height DOUBLE PRECISION,
  sort_order       INTEGER NOT NULL DEFAULT 0,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_script_scenes_script ON script_scenes(script_id, chapter_id, sort_order);

CREATE TABLE IF NOT EXISTS script_ops (
  id         BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
  scene_id   BIGINT NOT NULL REFERENCES script_scenes(id) ON DELETE CASCADE,
  op_seq     INTEGER NOT NULL,          -- = 应用后的 content_version
  op_json    JSONB NOT NULL,            -- {"ops":[...], "inverse":[...]}
  actor      VARCHAR(64) NOT NULL,      -- user uuid 或 'copilot'
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_script_ops_scene ON script_ops(scene_id, op_seq);

ALTER TABLE script_scenes ENABLE ROW LEVEL SECURITY;
ALTER TABLE script_ops ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access on script_scenes" ON script_scenes;
CREATE POLICY "Service role full access on script_scenes" ON script_scenes FOR ALL
  USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
DROP POLICY IF EXISTS "Service role full access on script_ops" ON script_ops;
CREATE POLICY "Service role full access on script_ops" ON script_ops FOR ALL
  USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
NOTIFY pgrst, 'reload schema';
```

- [ ] **Step 3: 写 340_backfill_episodes.sql**

```sql
-- 340_backfill_episodes.sql — 存量项目回填 Ep 1 并挂接 script_projects（spec v3 §2.1 / audit #23）
INSERT INTO episodes (project_id, title, sort_order)
SELECT DISTINCT sp.project_id, 'Ep 1', 0
FROM script_projects sp
WHERE sp.project_id IS NOT NULL AND sp.episode_id IS NULL
  AND NOT EXISTS (SELECT 1 FROM episodes e WHERE e.project_id = sp.project_id);

UPDATE script_projects sp
SET episode_id = e.id
FROM episodes e
WHERE sp.episode_id IS NULL AND sp.project_id = e.project_id;
NOTIFY pgrst, 'reload schema';
```

- [ ] **Step 4: dev 库 apply + 验证（含幂等重跑）**

```bash
source /private/tmp/claude-501/.../scratchpad/dev_db.env   # 完整路径见 Global Constraints
for f in 338_episodes 339_script_scenes_and_ops 340_backfill_episodes; do psql "$DEV_DSN" -f supabase/migrations/$f.sql; done
# 幂等：三个文件各重跑一遍，必须 0 error
psql "$DEV_DSN" -tAc "SELECT string_agg(column_name,',') FROM information_schema.columns WHERE table_name='script_scenes';"
psql "$DEV_DSN" -tAc "SELECT COUNT(*) FROM script_projects WHERE project_id IS NOT NULL AND episode_id IS NULL;"  -- 期望 0
```

- [ ] **Step 5: Commit** `feat(script): migrations 338-340 — episodes, script_scenes, script_ops, backfill`

---

### Task 2: ORM 模型

**Files:** Modify `backend/app/models/scripts.py`（在 ScriptChapters 后加三个类；ScriptProjects 加 episode_id）、`backend/app/models/__init__.py`（导出）。

**Interfaces:** Produces 类名 `Episodes` / `ScriptScenes` / `ScriptOps`，列名与 Task 1 SQL 完全一致；`ScriptProjects.episode_id: Mapped[Optional[int]]`。

- [ ] **Step 1: 失败测试**（新文件 `backend/tests/test_scene_models_import.py`）

```python
import pytest
pytestmark = pytest.mark.unit

def test_scene_models_importable_and_shaped():
    from app.models import Episodes, ScriptOps, ScriptScenes
    from app.models.scripts import ScriptProjects
    assert ScriptScenes.__tablename__ == "script_scenes"
    assert Episodes.__tablename__ == "episodes"
    assert ScriptOps.__tablename__ == "script_ops"
    cols = {c.key for c in ScriptScenes.__mapper__.column_attrs}
    assert {"content_json", "content", "content_version", "chapter_id", "sort_order"} <= cols
    assert "episode_id" in {c.key for c in ScriptProjects.__mapper__.column_attrs}
```

- [ ] **Step 2: RED** `uv run pytest tests/test_scene_models_import.py -q` → ImportError
- [ ] **Step 3: 实现**——对照 scripts.py 现有类的 mapped_column 风格逐列声明（BigInteger/Text/JSONB/DateTime(True)/Double(53)/Integer；server_default 对齐 SQL）。ScriptProjects 加 `episode_id: Mapped[Optional[int]] = mapped_column(BigInteger)`。__init__.py 按字母序导出三类。
- [ ] **Step 4: GREEN**；import 冒烟 `uv run python -c "from app.models import ScriptScenes"`；lint。
- [ ] **Step 5: Commit** `feat(script): ORM models Episodes/ScriptScenes/ScriptOps`

---

### Task 3: 纯函数层 `scene_ops.py`（协议核心）

**Files:** Create `backend/app/services/script/scene_ops.py`、`backend/tests/test_scene_ops.py`。

**Interfaces:** Produces：

```python
class OpError(Exception):
    def __init__(self, code: str, message: str): ...   # code ∈ missing_anchor|unknown_element|duplicate_id|invalid_op|invalid_payload

ELEMENT_TYPES = {"action","dialogue","character","paren","transition","comment","subtitle"}

def apply_ops(elements: list[dict], ops: list[dict]) -> tuple[list[dict], list[dict]]:
    """返回 (new_elements, inverse_ops)。anchor-based；insert=upsert-by-id 幂等；
    绝不原地改传入 list（不可变约定）。"""

def extract_text(elements: list[dict]) -> str:
    """派生纯文本：按序拼 text，character 后跟冒号，空元素跳过。"""
```

op 形状（spec v3 §2.2，逐字实现）：`{"op": "insert|update|delete|move", "element_id": "el_x", "before_id": str|None, "after_id": str|None, "payload": {"type":..., "text":..., "character_id":...}}`。语义：
- insert：element_id 已存在 → 原位更新 payload（幂等 upsert，**不移动**）；不存在 → 按 before_id/after_id 锚插入；两锚都缺 → 追加尾部；锚不存在 → OpError("missing_anchor")。payload.type 不在 ELEMENT_TYPES → OpError("invalid_payload")。
- update：不存在 → OpError("unknown_element")；merge payload（只覆盖给出的键）。
- delete：不存在 → **幂等成功**（重放安全），inverse 记原元素+原位置锚。
- move：element_id 不存在 → OpError("unknown_element")；锚缺/不存在 → OpError("missing_anchor")；before_id/after_id 指向自身 → no-op。
- inverse_ops：每个 op 的逆操作（insert→delete；update→update 回原值；delete→insert 回原锚位；move→move 回原锚位），供 undo/Phase 4 回滚。

- [ ] **Step 1: 失败测试**——穷举以上每条语义（≥14 个用例：插入头/中/尾/锚缺失/幂等重放同 op 两次结果一致/update merge/delete 幂等/move 自身/invalid type/inverse 往返 `apply_ops(apply_ops(e,ops)[0], inverse)[0] == e`/传入 list 未被 mutate）。测试代码逐条写出（实现者不许偷懒合并断言）。
- [ ] **Step 2: RED** → **Step 3: 实现**（纯 Python，无 IO）→ **Step 4: GREEN + lint**
- [ ] **Step 5: Commit** `feat(script): scene_ops pure functions — anchor-based element ops with inverse`

---

### Task 4: `script_scene_repository.py`

**Files:** Create repo + `backend/tests/test_script_scene_repository.py`（capture-emitted-SQL 风格，照 `tests/test_projects_member_update_delete.py` 的 fixture 范式）。

**Interfaces:** Produces（全部走 read_scope/write_scope；bigint 参数 `_bigint()` coerce；返回 dict 中 uuid/datetime 走既有 `_parity` 惯例、bigint 保 native）：

```python
class ScriptSceneRepository:
    async def list_by_script(self, script_id) -> list[dict]                    # ORDER BY chapter_id NULLS LAST, sort_order
    async def get_by_id(self, scene_id) -> dict | None
    async def create(self, data: dict) -> dict                                 # sort_order 自动 = chapter 内 MAX+1000
    async def update_meta(self, scene_id, data) -> dict                        # 头字段/坐标——不触 content_version
    async def delete(self, scene_id) -> bool
    async def apply_element_ops(self, scene_id, ops, expected_version: int, actor: str) -> dict
        # 事务内：SELECT content_json,content_version WHERE id=?
        # expected_version 不符 → raise VersionConflict(current_version, elements)
        # apply_ops() → UPDATE ... SET content_json=?, content=extract_text(?),
        #   content_version=content_version+1 WHERE id=? AND content_version=?  (双保险)
        # rowcount==0 → 重读 raise VersionConflict
        # 同事务 INSERT script_ops(scene_id, op_seq=new_version, op_json={"ops":..,"inverse":..}, actor)
        # 返回 {"content_version": new, "elements": new_elements}
    async def move_scene(self, scene_id, *, chapter_id=UNSET, before_scene_id=None, after_scene_id=None) -> dict
        # reparent（可选）+ step-1000 稀疏插入；间隙不足 → chapter 内 renumber（×1000 重排）
```

`VersionConflict(Exception)`：携带 `current_version: int` 与 `elements: list`。

- [ ] Steps：失败测试（含：apply 的 SQL 含 `content_version = :v` 双保险 WHERE；op 落账同事务；move_scene 重排触发条件；`_bigint` coerce 断言）→ RED → 实现 → GREEN → lint → Commit `feat(script): scene repository — versioned element ops + op ledger + sparse reorder`

---

### Task 5: 守卫 + Episodes/Scenes routers + canvas 既有 IDOR 修复

**Files:** Modify `backend/app/core/scope_guards.py`；Create `episodes_router.py` / `script_scenes_router.py`；Modify `script_canvas_router.py`；注册 router；Create `backend/tests/test_episodes_scenes_authz_wiring.py`。

**Interfaces:** Produces：

```python
# scope_guards.py 新增（收编 script_ai/script_canvas 各自的 _verify_script_access 私有副本）
async def verify_script_access(script_id: str, auth: AuthContext = Depends(get_auth)) -> None:
    """script_projects.team_id 与调用者 team 比对；str 双侧 coerce（#1006）。404 先于 403。"""
```

端点（全部挂守卫；scene 端点先由 scene_id 反查 script_id 再走同一校验——提供 `verify_scene_access` 薄封装）：

| 端点 | 方法 | 守卫 |
|------|------|------|
| `/projects/{project_id}/episodes` | GET/POST | verify_project_read/write_access（复用 Phase A 守卫） |
| `/episodes/{episode_id}` | PATCH/DELETE | 反查 project → verify_project_write_access 语义 |
| `/scripts/{script_id}/scenes` | GET/POST | verify_script_access |
| `/scenes/{scene_id}` | GET/PATCH/DELETE | verify_scene_access |
| `/scenes/{scene_id}/elements/ops` | POST | verify_scene_access |
| `/scenes/{scene_id}/move` | POST | verify_scene_access |

elements/ops 端点契约（逐字）：请求头 `If-Match: <content_version>`（缺失 → 428 Precondition Required）；体 `{"ops":[...]}`；200 → `{"success":true,"data":{"content_version":N,"elements":[...]}}`；VersionConflict → **409** `{"success":false,"code":"version_conflict","current_version":M,"elements":[...]}`；OpError → **422** `{"success":false,"code":"<op_error_code>","detail":...}`。actor = `auth.user_id`（copilot 路径后续接入时传 'copilot'）。

script_canvas_router 修复：`update_chapter`（L46）与 `delete_chapter`（L61）当前**不校验访问**——由 chapter_id 反查 script_id 后调 `verify_script_access` 同款校验（写成依赖或行内均可，与本文件既有 create_chapter 风格一致）。

- [ ] Steps：wiring 失败测试（结构化：两 router 全部路由必须声明守卫，参数化断言——照 `tests/test_projects_authz_wiring.py`；外加 canvas update/delete_chapter 的 403 行为测试）→ RED → 实现 → GREEN → 全量 pytest → lint → Commit `feat(script): episodes + scenes routers with authz; fix script_canvas chapter IDOR`

---

### Task 6: Convert-to-scenes（AI 拆场）

**Files:** Modify `script_ai_router.py`（+端点）；Create `backend/app/workflows/script_scene_convert.py`、`backend/tests/test_scene_convert_dispatch.py`。

**Interfaces:** `POST /scripts/{script_id}/chapters/{chapter_id}/convert-to-scenes` → 扁平 `{"success":true,"task_id":...}`。

- [ ] **Step 1: pin 失败测试**（照 `tests/test_script_ai_endpoint_dispatch.py` 范式）：mgr.create 收到非空 dbos_workflow_id；start_workflow_routed 收到相同 workflow_id；`resolve_script_provider_config` 被调用且 ScriptAIService 构造收到 provider_key/provider_config（照 `tests/test_script_ai_workflow_provider_config.py` 范式）。
- [ ] **Step 2: RED** → **Step 3: 实现**：

端点体（对照 sb_export_router 范本）：`_verify_script_access` → `wf_id = str(uuid4())` → `mgr.create(user_id, task_type="script_scene_convert", title=f"Convert chapter to scenes", dbos_workflow_id=wf_id)` → `start_workflow_routed("script_scene_convert", dbos_workflow_callable=script_scene_convert_workflow, dbos_workflow_kwargs={"script_id":..., "chapter_id":..., "user_id": auth.user_id}, workflow_id=wf_id)`。

workflow（照 script_outline.py 结构，@DBOS.step 拆两步）：step1 `convert_chapter_to_scenes(chapter_id, user_id)` —— 读 chapter 的 content（纯文本，mig120 派生列），`resolve_script_provider_config(user_id)` → `ScriptAIService(user_id=..., agent_slug=..., provider_key=..., provider_config=...)`，request_instructions 要求返回 JSON 数组 `[{"heading_int_ext","location_text","time_of_day","elements":[{type,text}]}]`（提示词全文写入实现，不许 TBD——基调：把章节散文拆成拍摄场景，保留原文语句归入 action/dialogue，识别角色名转 character+dialogue 对）；step2 `persist_scenes(script_id, chapter_id, scenes)` —— 逐场景 `ScriptSceneRepository.create` + 首次 content 写入（生成 el_ id，直接落 content_json + content + content_version=1 + op_seq=1 落账 actor='copilot'）。失败路径 **raise**（不 return failed dict——CLAUDE.md 路线 C 纪律）。

- [ ] **Step 4: GREEN + 全量 + lint** → **Step 5: Commit** `feat(script): convert-to-scenes AI endpoint + workflow (wf_id + DB provider threaded)`

---

### Task 7: 真库集成 round-trip

**Files:** Create `backend/tests/integration/test_script_scenes_orm.py`（照 `tests/integration/test_projects_repository_orm.py` 的 INTEGRATION_DATABASE_URL 守卫模式）。

- [ ] 用例（对 dev 库）：建 episode→script(挂 episode)→scene → `apply_element_ops` 插入 3 元素（含幂等重放同批 ops 第二次 → 版本不再前进或 409，断言无重复元素）→ 错误 expected_version → VersionConflict 携带 current → `move_scene` 换序读回 → script_ops 行数与 op_seq 校验 → 派生 content 含元素文本 → 清理。RED（表在 dev 已有，测试首跑即应 GREEN——此任务价值在真 asyncpg 类型面）→ 跑通 → Commit `test(script): live-DB round-trip for scenes/ops`

---

### Task 8: E2E 收口 + 收尾

- [ ] 扩 `scratchpad` E2E 脚本式验证（прod 部署后）：signup→建项目→（自动 Ep 1 校验：POST script 后 episode_id 非空）→POST scene→ops 写两元素→If-Match 错值拿 409→convert-to-scenes 对一个有 prose 的 chapter → 轮询 task_tracking completed + script_scenes 增行→清理。写成 `scratchpad/e2e_scenes.py` 备部署后跑。
- [ ] 全量 pytest + 前端 build（不应受影响）+ 三迁移在 dev 幂等重跑记录。
- [ ] 更新 memory：project_phase_b 进度 + plan 完成情况。

## Self-Review 已做

- Spec 覆盖：v3 §2 全部数据项（episodes/scenes/ops/回填/级联/索引/一致性校验/派生 content/content_version）→ Task 1-4；§2.2 协议全语义 → Task 3/4/5；authz 不变量+canvas 顺带修 → Task 5；Convert-to-scenes → Task 6；§8 测试清单中数据层部分 → Task 3/4/5/7（编辑器状态机/渲染快照/10x 性能属前端 plan）。**不含**：编辑器前端全部、scene 懒加载 API 分页（P1 前端 plan 一起定形状——列为前端 plan 输入）。
- 类型一致性：apply_ops 返回 tuple 在 Task 3/4 一致；VersionConflict 字段在 Task 4/5 一致；扁平 task_id 契约在 Task 6 与 Global Constraints 一致。
- 无占位符。
