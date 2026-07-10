# Projects Workspace Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement task-by-task. Spec: `docs/superpowers/specs/2026-07-10-projects-workspace-final.html`(G1–G14,唯一基准;artifact fd133ec7)。

**Goal:** 项目即工作区(0 跳)+ 主页工作队列——按定版终稿 G1–G14 重构 Projects 全模块。

**Architecture:** 4 个独立 PR 渐进迁移不断服务:PR-8 后端数据面(批量 suggestions/自动剧集/回填)→ PR-9 主页(队列+网格)→ PR-10 工作区壳(顶栏/侧栏/Overview/Episodes/Files 统一浏览器)→ PR-11 编辑器模块并入(下线 Scripts tab)。storage-unification 为独立 epic 不在本计划。

**Tech Stack:** FastAPI + SQLAlchemy-Core(`app.db.engine`)+ Supabase Postgres;React 19 + TS + Tailwind + i18next;pytest + vitest + Playwright。

## Global Constraints

- **Spec 决策 G1–G14 全文见终稿**;每任务隐含继承。
- **零数据迁移原则**(除 353 回填):episodes/sort_order/episode_id/canvases 全现成。
- UI 英文 + i18n;BIGINT str;immutability;task 系统路线 C;backend lint(black/isort/flake8);frontend lint(rules-of-hooks=error)。
- 共享工作树 git 纪律:subagent 禁 checkout/switch/reset/rebase;只 add 显式路径 + commit;**勿动 frontend/package.json(并行 session 在 bump)**。
- 渐进迁移:PR-10 期间旧编辑器路由与 Scripts tab 照常可用;PR-11 收尾才下线。

---

# PR-8 · 后端数据面(branch feature/projects-workspace-pr8)

### Task A: GET /projects/suggestions(批量队列数据源)

**Files:** Modify `backend/app/services/library/projects_service.py`、`backend/app/api/projects_router.py`、`backend/app/schemas/projects.py`;Test `backend/tests/test_projects_suggestions_batch.py`

**Interfaces:**
- Produces: `GET /api/v1/projects/suggestions`(auth 必须;**声明在一切 `/{project_id}` 动态路由之前**)→ `{items: [{project_id: str, name, stage_slug, kind, progress?, action?, stalled: bool, latest_activity?}]}`。
- 范围 = 调用者可见的**非归档**项目(复用 list 端点同一 scope 逻辑/repo 方法——grep `get_projects_with_counts` 的取数路径,读侧不重造权限)。
- 组装:复用已有 `_get_card_enrichment`(stage/activity/stalled 已齐)+ 对 `stage_slug=='storyboard'` 的项目并发跑 `storyboard_progress_for_project`(`asyncio.gather`,上限 8 并发,单项失败降级为导航 suggestion);非 storyboard 用 `build_stage_suggestion` 的决策表逻辑(抽公共 helper `_suggestion_from`,避免复制 kind 表)。
- Schema:`ProjectSuggestionItem` / `ProjectSuggestionsResponse`(pydantic,复用现有 `StoryboardProgress`/`SuggestionAction`)。
- 单测:fake repo(3 项目:storyboard 有空 shots/planning/review 停滞)断言 kind/action/stalled;单项目 progress 抛错→该行降级导航不 500;空项目列表→items=[]。

### Task B: 新建项目自动带 Ep 1 + 空脚本(G3)

**Files:** Modify `backend/app/services/library/projects_service.py`(create_project 内,现有 default-stage 块之后);Test `backend/tests/test_create_project_default_episode.py`

- Best-effort try/except(与 default-stage 同款纪律):建 `episodes` 行(project_id, title='Episode 1', sort_order=1)+ `script_projects` 行(episode_id=ep.id, status='active', 命名 'Episode 1' 或现有默认)。
- **先 grep 现有创建路径复用**:episodes 的建行(编辑器 EpisodePanel 走哪个 router/repo?)与 script_projects 的建行(`ScriptProjectRepository.create`?)——用真实 API,勿手写 INSERT(除非无现成方法,则加 repo 方法)。
- 单测:fake repos 断言 create 后调了 episode+script 创建且入参对;任一步骤炸→项目创建仍成功。

### Task C: migration 353 存量回填

**Files:** Create `supabase/migrations/353_projects_default_episode.sql`;本地 54322 实测。

```sql
-- 353_projects_default_episode.sql — G3:每项目必有 Ep 1;存量脚本挂回默认集。幂等。
INSERT INTO public.episodes (project_id, title, sort_order)
SELECT p.id, 'Episode 1', 1 FROM public.projects p
WHERE NOT EXISTS (SELECT 1 FROM public.episodes e WHERE e.project_id = p.id);

UPDATE public.script_projects sp
SET episode_id = (
  SELECT e.id FROM public.episodes e
  WHERE e.project_id = sp.project_id ORDER BY e.sort_order ASC LIMIT 1
)
WHERE sp.episode_id IS NULL AND sp.project_id IS NOT NULL;
```
(**实施前先对 information_schema 核 episodes 实际列名**——title/name?/sort_order;按实调整。本地先 `\d episodes`;若本地缺表,按迁移历史补前置。)运行两遍证幂等。

### Task D: PR-8 绿灯 + ship
- 全量相关测试 + lint;push;PR 标题 `feat(projects): workspace backend — batch suggestions + default episode (PR-8, G3/G7)`;CI 绿合并。

---

# PR-9 · 主页(outline,开工时细化 brief)
- 队列视图组件(默认):suggestions 端点驱动;排序 stalled>一键>导航>delivery;星标钉顶;行内一键(generate 直发+行内进度,导航进项目);☰/▦ 切换(localStorage 记忆);网格卡底加一键行(C 案);撤 Internal/External 标签页;视图栏 All/Starred/Archived。vitest+e2e。

# PR-10 · 工作区壳(outline)
- 顶栏项目条(名称/阶段步进/Advance/一键建议常驻);新侧栏(Overview/Canvas/Episodes 管理面/当前集块⇄紧凑切换/ASSETS 主库视图/Files 统一浏览器吸收 Output/Manage);Overview 落地页(Continue/建议/摘要/动态);Characters/Locations 主库=项目级派生聚合端点 `GET /projects/{id}/entities`(cue/场景头解析,Ep 徽标);Files 双源聚合(project_files+generated_media,类型 chips)。旧编辑器路由保持可用。

# PR-11 · 编辑器模块并入(outline)
- Script/Storyboard/Renders 挂载项目侧栏(编辑器 EditorShell 视图复用,Ep 上下文注入);@ 候选升项目级(entities 端点);下线 Scripts tab 与独立列表;Publish 子项占位(D2 后)。e2e 双主题全景 + bump 发版。
