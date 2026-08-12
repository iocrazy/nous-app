# Undo Backlog 清扫 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收掉 Agent Run 撤销立项的五条终审 backlog：already_undone 独立文案、op 账本读错误上抛、撤销后刷新已开剧本纸、skipped.reason 加 internal_error 档（含权限立项点名的 `_undo_scenes` 泛异常误归因）。

**Architecture:** 后端两处小改（repository 错误传播 + undo service 归因）；前端沿 `shotFocusBus` 既有事件总线加第三通道，EditorShell 复用版本回滚的 `handleRolledBack` 先例（reload + nonce 重挂载）。零 migration。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async（后端）；React 19 + vitest + @testing-library（前端）；i18next（en/zh 双 locale）。

**Spec:** `docs/superpowers/specs/2026-08-12-undo-backlog-sweep-design.md`（已提交，用户已批准）。

## Global Constraints

- Worktree：`/media/heygo/program/projects-code/repos/nous-app/.worktrees/feat-undo-backlog-sweep`，分支 `feat/undo-backlog-sweep`。所有命令在该 worktree 内执行。
- UI 文案必须英文（en.json 值）；zh.json 提供中文翻译。代码内 fallback 字符串与 en.json 值一致。
- 后端测试：`cd backend && uv run pytest tests/<file> -v`；前端测试：`cd frontend && npx vitest run <file>`。
- 既有归因不得改变：CAS miss → `edited_after_run`、渲染物存在 → `rendered`、`VersionConflict` → `version_conflict`。只有**泛异常** handler 改成 `internal_error`。
- 每个 Task 单独 commit，commit message 末尾带：
  `Claude-Session: https://claude.ai/code/session_01PsmzBfabDy1JdoS82w26D9`

---

### Task 1: `list_ops_by_scene` DB 错误上抛（后端 repo）

**Files:**
- Modify: `backend/app/repositories/script_scene_repository.py:266-283`（`list_ops_by_scene`）
- Test: `backend/tests/test_script_scene_repository.py`（文件尾追加）

**Interfaces:**
- Consumes: 无（独立任务）。
- Produces: `list_ops_by_scene` 在 DB 错误时**抛出异常**（不再返回 `[]`）。Task 2 的 `_undo_scenes` 泛异常 handler 依赖这一行为把 DB 错误归为 `internal_error`。

- [ ] **Step 1: Write the failing test**

在 `backend/tests/test_script_scene_repository.py` 文件末尾追加（该文件已 import `pytest`、`patch`、`scene_mod`、`ScriptSceneRepository`，`_SCENE_ID` 常量在文件头）：

```python
@pytest.mark.asyncio
async def test_list_ops_by_scene_propagates_db_errors():
    """A ledger-read failure must PROPAGATE, not silently return [].

    Both consumers replay/invert this ledger: an empty list on error would
    reconstruct wrong content (version_service) or report "nothing to undo"
    (run_undo_service). House convention: primary reads fail loud, same as
    list_scene_rows_for_project.
    """

    class _BoomSession:
        async def execute(self, stmt):
            raise RuntimeError("db down")

    class _BoomCtx:
        async def __aenter__(self):
            return _BoomSession()

        async def __aexit__(self, *exc):
            return False

    with patch.object(scene_mod, "read_scope", lambda: _BoomCtx()):
        with pytest.raises(RuntimeError, match="db down"):
            await ScriptSceneRepository().list_ops_by_scene(str(_SCENE_ID))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_script_scene_repository.py::test_list_ops_by_scene_propagates_db_errors -v`
Expected: FAIL —— `DID NOT RAISE`（现有实现吞掉异常返回 `[]`）。

- [ ] **Step 3: Write minimal implementation**

`backend/app/repositories/script_scene_repository.py` 的 `list_ops_by_scene` 去掉 try/except（保留查询本体），docstring 补充传播语义：

```python
    async def list_ops_by_scene(self, scene_id: str) -> List[Dict[str, Any]]:
        """The immutable op ledger for a scene, ordered by ``op_seq`` ASC (replay
        order). Each row carries ``op_json`` = ``{"ops": [...], "inverse": [...]}``
        native (JSONB stays a dict). Read by the version service to replay a scene
        to a commit watermark and to build inverse batches for rollback.

        A query failure PROPAGATES (house convention for primary reads — same as
        ``list_scene_rows_for_project`` above): every consumer replays or inverts
        this ledger, so an empty list on error is not a safe default — it silently
        reconstructs wrong content (version_service) or reports "nothing to undo"
        (run_undo_service, which maps the raised error to ``internal_error``).
        """
        async with read_scope() as session:
            result = await session.execute(
                select(ScriptOps)
                .where(ScriptOps.scene_id == _bigint(scene_id))
                .order_by(ScriptOps.op_seq.asc())
            )
            return [
                _parity(_orm_obj_to_dict(r, _OPS_N2A))
                for r in result.scalars().all()
            ]
```

注意：删除原实现里的 `logger.error(...)` + `return []` 分支；不要动同文件其他方法。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_script_scene_repository.py -v`
Expected: 新测试 PASS，该文件全部既有测试 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/script_scene_repository.py backend/tests/test_script_scene_repository.py
git commit -m "fix(script): list_ops_by_scene DB 错误上抛 — 空账本会静默重建错误内容/静默无可撤销

Claude-Session: https://claude.ai/code/session_01PsmzBfabDy1JdoS82w26D9"
```

---

### Task 2: 泛异常归因 `internal_error`（后端 undo service）

**Files:**
- Modify: `backend/app/services/ai/undo/run_undo_service.py`（三处：docstring L43-45、`_undo_shots` L88-101、`_undo_scenes` L206-215）
- Modify: `backend/app/schemas/agent_runs.py:179`（注释）
- Test: `backend/tests/test_run_undo_service.py`（文件尾追加两个测试）

**Interfaces:**
- Consumes: Task 1 的传播行为（DB 错误现在会抵达 `_undo_scenes` 的泛异常 handler）。
- Produces: `execute_undo` 返回的 `skipped[].reason` 新增合法值 `"internal_error"`（仅泛异常路径）。Task 3 的前端 types/i18n 消费该值。

- [ ] **Step 1: Write the failing tests**

在 `backend/tests/test_run_undo_service.py` 文件末尾追加（文件已有 `_CaptureSession` / `_FakeResult` / `_ScopeCtx` / `_shot_ledger_row` / `_FULL` / `_RUN_ID` / `_SCENE_ID` 等 helper，以及 `AsyncMock, MagicMock, patch` imports）：

```python
@pytest.mark.asyncio
async def test_shot_unexpected_failure_reason_internal_error():
    """A generic exception inside a shot plan must NOT be blamed on the user
    ("edited_after_run" is a lie there) — it reports as internal_error."""
    read_session = _CaptureSession(
        [
            _FakeResult(
                all_rows=[_shot_ledger_row(1, 900, "create", None, _FULL)]
            ),
            _FakeResult(all_rows=[]),  # no scenes touched
        ]
    )
    with (
        patch.object(service_mod, "read_scope", lambda: _ScopeCtx(read_session)),
        patch.object(
            service_mod,
            "_undo_delete_shot",
            AsyncMock(side_effect=RuntimeError("boom")),
        ),
    ):
        report = await service_mod.execute_undo(_RUN_ID)

    assert report["shots_deleted"] == 0
    assert report["skipped"] == [
        {"kind": "shot", "id": "900", "reason": "internal_error"}
    ]


@pytest.mark.asyncio
async def test_scene_ledger_read_failure_reason_internal_error():
    """Task 1 makes list_ops_by_scene raise on DB errors; the per-scene
    handler must surface that as internal_error, not edited_after_run."""
    read_session = _CaptureSession(
        [
            _FakeResult(all_rows=[]),  # no shot ledger rows
            _FakeResult(all_rows=[(_SCENE_ID,)]),  # one scene touched
        ]
    )
    repo = MagicMock()
    repo.list_ops_by_scene = AsyncMock(side_effect=RuntimeError("db down"))
    with (
        patch.object(service_mod, "read_scope", lambda: _ScopeCtx(read_session)),
        patch.object(
            service_mod, "get_script_scene_repository", MagicMock(return_value=repo)
        ),
    ):
        report = await service_mod.execute_undo(_RUN_ID)

    assert report["scene_elements_reverted"] == 0
    assert report["skipped"] == [
        {"kind": "scene", "id": str(_SCENE_ID), "reason": "internal_error"}
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_run_undo_service.py -v -k internal_error`
Expected: 两个测试 FAIL —— skipped reason 目前是 `edited_after_run`。

- [ ] **Step 3: Write minimal implementation**

`backend/app/services/ai/undo/run_undo_service.py` 三处：

① `execute_undo` docstring（L43-45）reason 枚举行改为：

```python
    with ``reason`` in ``skipped`` one of ``edited_after_run`` /
    ``rendered`` / ``version_conflict`` / ``internal_error`` (the last is
    the honest label for unexpected server-side failures — never blamed on
    a user edit), and every id a ``str`` (snowflake precision discipline).
```

② `_undo_shots` 的泛异常 handler（原 L95-101）里 `"reason"` 值从
`"edited_after_run"` 改为 `"internal_error"`（logger.error 保留原样）。

③ `_undo_scenes` 的泛异常 handler（原 L213-215）同样改为
`"internal_error"`（`except VersionConflict` 分支不动）。

`backend/app/schemas/agent_runs.py:179` 注释同步：

```python
    reason: str  # 'edited_after_run' | 'rendered' | 'version_conflict' | 'internal_error'
```

- [ ] **Step 4: Run tests to verify they pass（含归因回归守卫）**

Run: `cd backend && uv run pytest tests/test_run_undo_service.py tests/test_run_undo_logic.py -v`
Expected: 全部 PASS。既有的 `test_delete_rowcount_zero_row_missing_edited_after_run` / `test_revert_rowcount_zero_edited_after_run_no_surface_sync` / `test_scene_version_conflict_skips_scene_no_element_counts` 就是"CAS miss / version_conflict 归因不变"的回归守卫，必须保持绿。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ai/undo/run_undo_service.py backend/app/schemas/agent_runs.py backend/tests/test_run_undo_service.py
git commit -m "fix(undo): 泛异常归因 internal_error — 不再把服务端内部错误谎报为 edited_after_run

Claude-Session: https://claude.ai/code/session_01PsmzBfabDy1JdoS82w26D9"
```

---

### Task 3: `already_undone` 独立文案 + `internal_error` 前端档位（前端 types / i18n / TurnWriteSummary）

**Files:**
- Modify: `frontend/types.ts:1972`（skipped reason union）
- Modify: `frontend/public/locales/en.json`（`agentActivity` 节）
- Modify: `frontend/public/locales/zh.json`（`agentActivity` 节）
- Modify: `frontend/components/agentActivity/TurnWriteSummary.tsx:175-199`（report 渲染块）
- Test: `frontend/components/agentActivity/TurnWriteSummary.test.tsx`

**Interfaces:**
- Consumes: Task 2 产出的 `internal_error` reason 值。
- Produces: i18n keys `agentActivity.undoAlreadyUndone`、`agentActivity.undoReason.internal_error`（Task 4/5 不依赖，但 e2e 验收会看到）。

- [ ] **Step 1: Write the failing tests**

先读 `frontend/components/agentActivity/TurnWriteSummary.test.tsx` 现有的
undo report 测试（约 L150-170，断言 `textContent` 含 `agentActivity.undoSummary`，
i18n mock 为 key-echo），照同一套 setup 模式追加两个用例：

```tsx
it('renders a distinct message for an already-undone run (no counts)', async () => {
  // 同现有 report 用例的 setup，但 undoRun resolve 为:
  // { status: 'already_undone', shots_deleted: 0, shots_reverted: 0,
  //   scene_elements_reverted: 0, skipped: [] }
  // 点击 undo 按钮后:
  const report = await screen.findByTestId('turn-undo-report');
  expect(report.textContent).toContain('agentActivity.undoAlreadyUndone');
  expect(report.textContent).not.toContain('agentActivity.undoSummary');
});

it('renders the internal_error skip reason via its i18n key', async () => {
  // 同现有 report 用例的 setup，undoRun resolve 为 status:'done' 且
  // skipped: [{ kind: 'scene', id: '700', reason: 'internal_error' }]
  const report = await screen.findByTestId('turn-undo-report');
  expect(report.textContent).toContain('agentActivity.undoReason.internal_error');
});
```

（具体 setup 代码以该文件现有 report 用例为准——mock 形状必须照抄现有约定，
不得自创；这是「边界 mock 用真实 JSON 形状」纪律。）

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run components/agentActivity/TurnWriteSummary.test.tsx`
Expected: 新增两用例 FAIL（第一个：已撤销时仍渲染 undoSummary；第二个视 types 而定可能是 TS 编译错，同样算 FAIL）。

- [ ] **Step 3: Write minimal implementation**

① `frontend/types.ts:1972` reason union 加档：

```ts
    reason: 'edited_after_run' | 'rendered' | 'version_conflict' | 'internal_error';
```

② `frontend/public/locales/en.json` `agentActivity` 节（挨着现有 `undoSummary`）：

```json
"undoAlreadyUndone": "Already undone earlier — nothing changed",
```

且 `undoReason` 对象内加：

```json
"internal_error": "internal error during undo — left untouched"
```

③ `frontend/public/locales/zh.json` 同位置：

```json
"undoAlreadyUndone": "此前已撤销过 — 本次未做任何更改",
```

`undoReason` 内：

```json
"internal_error": "撤销时发生内部错误 — 未改动"
```

④ `TurnWriteSummary.tsx` 的 report 渲染块改为按 status 分支：

```tsx
      {undoReport && (
        <div
          data-testid="turn-undo-report"
          className="mt-1 border-t border-agent-line px-2 pt-1 text-[10px] text-ink-500"
        >
          {undoReport.status === 'already_undone' ? (
            <p>
              {t(
                'agentActivity.undoAlreadyUndone',
                'Already undone earlier — nothing changed',
              )}
            </p>
          ) : (
            <>
              <p>
                {t('agentActivity.undoSummary', {
                  deleted: undoReport.shots_deleted,
                  reverted: undoReport.shots_reverted,
                  elements: undoReport.scene_elements_reverted,
                })}
              </p>
              {undoReport.skipped.map((item, i) => (
                <p key={`${item.kind}-${item.id}-${i}`}>
                  {t(`agentActivity.undoKind.${item.kind}`)} {item.id} ·{' '}
                  {t(`agentActivity.undoReason.${item.reason}`)}
                </p>
              ))}
            </>
          )}
        </div>
      )}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run components/agentActivity/TurnWriteSummary.test.tsx`
Expected: 全部 PASS（含既有用例）。

- [ ] **Step 5: Commit**

```bash
git add frontend/types.ts frontend/public/locales/en.json frontend/public/locales/zh.json frontend/components/agentActivity/TurnWriteSummary.tsx frontend/components/agentActivity/TurnWriteSummary.test.tsx
git commit -m "fix(agent-activity): already_undone 独立文案 + internal_error 归因档 — 不再渲染误导性零计数

Claude-Session: https://claude.ai/code/session_01PsmzBfabDy1JdoS82w26D9"
```

---

### Task 4: 事件总线第三通道 + useRunUndo 触发（前端）

**Files:**
- Modify: `frontend/components/agentActivity/shotFocusBus.ts`（文件尾追加通道）
- Modify: `frontend/components/agentActivity/useRunUndo.ts:91-104`（undo 回调）
- Test: `frontend/components/agentActivity/useRunUndo.test.ts`

**Interfaces:**
- Consumes: 无。
- Produces: `onSceneContentRefresh(listener: () => void): () => void` 与 `requestSceneContentRefresh(): void`（Task 5 的 EditorShell 订阅方消费；签名与 `onStoryboardRefresh` / `requestStoryboardRefresh` 完全同形）。

- [ ] **Step 1: Write the failing tests**

在 `frontend/components/agentActivity/useRunUndo.test.ts` 追加（该文件已有 `makeRun` / `makeReport` helper 与 `getRun`/`undoRun` service mock；照现有用例的 `renderHook` + `act` 模式）：

```ts
import { onSceneContentRefresh } from './shotFocusBus';

it('fires scene content refresh when the undo reverted scene elements', async () => {
  getRun.mockResolvedValue(makeRun());
  undoRun.mockResolvedValue(makeReport({ scene_elements_reverted: 2 }));
  const listener = vi.fn();
  const off = onSceneContentRefresh(listener);
  const { result } = renderHook(() => useRunUndo('run-1', true));
  await waitFor(() => expect(result.current.state).toBe('ready'));
  await act(() => result.current.undo());
  expect(listener).toHaveBeenCalledTimes(1);
  off();
});

it('does NOT fire scene content refresh when no scene elements changed', async () => {
  getRun.mockResolvedValue(makeRun());
  undoRun.mockResolvedValue(
    makeReport({
      scene_elements_reverted: 0,
      skipped: [{ kind: 'scene', id: '700', reason: 'internal_error' }],
    }),
  );
  const listener = vi.fn();
  const off = onSceneContentRefresh(listener);
  const { result } = renderHook(() => useRunUndo('run-2', true));
  await waitFor(() => expect(result.current.state).toBe('ready'));
  await act(() => result.current.undo());
  expect(listener).not.toHaveBeenCalled();
  off();
});
```

（import 语句合并进文件头现有的 `./shotFocusBus` import。）

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run components/agentActivity/useRunUndo.test.ts`
Expected: FAIL —— `onSceneContentRefresh` 尚不存在（import 报错即算 FAIL）。

- [ ] **Step 3: Write minimal implementation**

① `shotFocusBus.ts` 文件尾追加第三通道（与 storyboard refresh 通道同形）：

```ts
/**
 * Third channel: "an Undo reverted scene TEXT content — any open script
 * sheet should reload." EditorShell subscribes (reload + remount nonce,
 * the same recipe as a version rollback); useRunUndo publishes after an
 * undo that reverted at least one scene element. Fire-and-forget like the
 * channels above — no subscriber (script sheet not mounted) just means
 * the next mount seeds from a fresh fetch anyway.
 */
export type SceneContentRefreshListener = () => void;

const sceneContentListeners = new Set<SceneContentRefreshListener>();

/** Subscribe; returns the unsubscribe function (useEffect-shaped). */
export function onSceneContentRefresh(
  listener: SceneContentRefreshListener,
): () => void {
  sceneContentListeners.add(listener);
  return () => {
    sceneContentListeners.delete(listener);
  };
}

/** Ask any open script sheet to reload scene content. No-op when nothing listens. */
export function requestSceneContentRefresh(): void {
  for (const listener of [...sceneContentListeners]) {
    try {
      listener();
    } catch (err) {
      console.error('[shotFocusBus] scene content refresh listener failed:', err);
    }
  }
}
```

② `useRunUndo.ts`：import 行加 `requestSceneContentRefresh`，`undo` 回调在
`requestStoryboardRefresh()` 之后加条件触发：

```ts
      requestStoryboardRefresh();
      if (result.scene_elements_reverted > 0) {
        // Scene TEXT changed server-side — tell any open script sheet to
        // reload (skipped-only reports changed nothing, so stay quiet).
        requestSceneContentRefresh();
      }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run components/agentActivity/useRunUndo.test.ts components/agentActivity/shotFocusBus.test.ts`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/components/agentActivity/shotFocusBus.ts frontend/components/agentActivity/useRunUndo.ts frontend/components/agentActivity/useRunUndo.test.ts
git commit -m "feat(agent-activity): undo 改动 scene 正文时广播 sceneContentRefresh — 总线第三通道

Claude-Session: https://claude.ai/code/session_01PsmzBfabDy1JdoS82w26D9"
```

---

### Task 5: EditorShell 订阅刷新（前端剧本纸）

**Files:**
- Modify: `frontend/editor/components/EditorShell.tsx`（import + 一个 useEffect，紧邻 `handleRolledBack` L354-361 之后）
- Test: `frontend/editor/__tests__/EditorShell.test.tsx`

**Interfaces:**
- Consumes: Task 4 的 `onSceneContentRefresh`；本文件既有的 `handleRolledBack`（reload + bump rollbackNonce，L354-361）。
- Produces: 无对外接口——行为：收到 sceneContentRefresh 事件 → 剧本纸整卷 reload 并强制重挂载 SceneBlock。

- [ ] **Step 1: Write the failing test**

先读 `frontend/editor/__tests__/EditorShell.test.tsx` 的现有 mock 布局
（`sceneService` 的 `listScenes` 已是 `vi.fn()`），追加用例：

```tsx
import { requestSceneContentRefresh } from '../../components/agentActivity/shotFocusBus';

it('reloads scenes when an agent-run undo broadcasts scene content refresh', async () => {
  // 沿用该文件现有的最小 render 布局（listScenes resolve 一个场景）。
  render(<EditorShell scriptId="1" />);
  await waitFor(() => expect(svc.listScenes).toHaveBeenCalledTimes(1));

  act(() => {
    requestSceneContentRefresh();
  });

  await waitFor(() => expect(svc.listScenes).toHaveBeenCalledTimes(2));
});
```

（`svc` 为该测试文件既有的 hoisted sceneService mock 对象名，以实际文件为准；
若该文件的初始加载调用次数不同，以"事件后比事件前多一次"为断言口径。）

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run editor/__tests__/EditorShell.test.tsx`
Expected: 新用例 FAIL —— 第二次 `listScenes` 永远不来（超时）。

- [ ] **Step 3: Write minimal implementation**

`EditorShell.tsx`：

① import（放在现有 agentActivity 相关 import 附近，若无则新增一行）：

```tsx
import { onSceneContentRefresh } from '../../components/agentActivity/shotFocusBus';
```

② 在 `handleRolledBack` 定义之后紧邻处加订阅（复用同一恢复配方）：

```tsx
  // An agent-run Undo reverted scene TEXT server-side — identical stale-sheet
  // problem to a version rollback (same-id reseed guard in useSceneSync would
  // swallow a plain reload), so reuse the exact rollback recipe: re-fetch,
  // then bump the remount nonce. See handleRolledBack's comment above.
  useEffect(() => onSceneContentRefresh(handleRolledBack), [handleRolledBack]);
```

（`useEffect` 已在文件 import 内。）

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run editor/__tests__/EditorShell.test.tsx`
Expected: 全部 PASS（含既有用例）。

- [ ] **Step 5: Commit**

```bash
git add frontend/editor/components/EditorShell.tsx frontend/editor/__tests__/EditorShell.test.tsx
git commit -m "fix(editor): 剧本纸订阅 sceneContentRefresh — undo 后复用 rollback 配方刷新正文

Claude-Session: https://claude.ai/code/session_01PsmzBfabDy1JdoS82w26D9"
```

---

### Task 6: 全量回归 + 收尾

**Files:** 无新改动（只跑验证）。

**Interfaces:** 无。

- [ ] **Step 1: 后端全量相关测试**

Run: `cd backend && uv run pytest tests/test_run_undo_service.py tests/test_run_undo_logic.py tests/test_script_scene_repository.py -v`
Expected: 全 PASS。

- [ ] **Step 2: 前端全量相关测试 + 类型检查 + 构建**

Run:
```bash
cd frontend
npx vitest run components/agentActivity/ editor/__tests__/EditorShell.test.tsx
npm run build
```
Expected: 测试全 PASS；build 成功（tsc 无错）。

- [ ] **Step 3: 版本服务回归（Task 1 改了它的依赖）**

Run: `cd backend && uv run pytest tests/ -v -k "version_service or script_version"`
Expected: 全 PASS（若无匹配文件，`grep -rl list_ops_by_scene backend/tests/` 确认无其他消费者测试后即可）。

- [ ] **Step 4: 确认工作区干净、commit 数正确**

Run: `git status --porcelain && git log --oneline origin/master..HEAD`
Expected: 工作区干净；应有 6 个 commit（spec + 5 个 task；Task 6 无 commit）。
