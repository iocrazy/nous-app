/**
 * 重试点的是哪个接口 —— 用户 2026-09-15 报的就是这件事。
 *
 * 截图：一个失败的抖音解析点了四次重试，任务中心多出四条 `(recovered)` 行，原任务
 * 纹丝不动 ——「不还是新建任务？和之前的任务是剥离的？」
 *
 * 生产的 `api_request_logs` 给出了决定性证据：那四次点击打的全是
 *   POST /api/v1/workflows/parse-8e1584e3…/restart   202  ×4
 * 也就是 DBOS 的 fork。fork 按定义就是「另起一个 workflow，原行留在终态」，而且
 * 没有任何人给 fork 出来的 id 建 task_tracking 行 —— 于是跑起来的 workflow 掉进
 * `TaskManager.start()` 的自愈分支，兜底建了一条标题写死 `(recovered)`、没有
 * dedup_key / flow_id / metadata 的孤儿行。
 *
 * 所以这个文件钉的不是「重试能不能成功」，而是**顺序**：原地重试优先，fork 只在
 * 后端明确说「这个类型我重试不了」时才轮得到。顺序反了，上面那一幕就会重演，而且
 * 每一层单测都会照样绿 —— 因为每一层自己都没错。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const restartWorkflow = vi.fn();
vi.mock('./dbosWorkflowService', () => ({
  restartWorkflow: (...a: unknown[]) => restartWorkflow(...a),
  cancelWorkflow: vi.fn(),
}));
vi.mock('./parserService', () => ({
  getAuthHeaders: async () => ({ Authorization: 'Bearer t' }),
}));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

/** 生产的错误体外壳：类型化理由在 details.code 里（CLAUDE.md 2026-09-09）。 */
const envelope = (status: number, code: string) =>
  new Response(
    JSON.stringify({
      success: false,
      error: 'Conflict',
      code: `http_${status}`,
      request_id: 'req_1',
      details: { code, message: 'nope' },
    }),
    { status, headers: { 'Content-Type': 'application/json' } },
  );

async function retry(taskId: string) {
  const { retryTaskInPlace } = await import('./taskRetry');
  return retryTaskInPlace(taskId);
}

describe('重试的路由顺序', () => {
  beforeEach(() => {
    vi.resetModules();
    restartWorkflow.mockReset().mockResolvedValue(undefined);
    vi.stubGlobal('fetch', vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('先打原地重试，成功就到此为止 —— 绝不再 fork 一个新 workflow', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('{}', { status: 200 }));

    await retry('parse-8e');

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toContain('/api/v1/task-manager/tasks/parse-8e/retry');
    expect(init?.method).toBe('POST');
    // 这一条就是用户报的缺陷：多出来的那条 `(recovered)` 行正是 fork 建的。
    expect(restartWorkflow).not.toHaveBeenCalled();
  });

  it('后端说这个类型原地重试不了，才回落到 fork', async () => {
    // 409 retry_not_supported 的契约是「行一个字都没动」，所以回落是安全的。
    vi.mocked(fetch).mockResolvedValue(envelope(409, 'retry_not_supported'));

    await retry('agent-run-1');

    expect(restartWorkflow).toHaveBeenCalledWith('agent-run-1');
  });

  it('根本不是 task_tracking 行（404）也回落到 fork', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('', { status: 404 }));

    await retry('not-a-task');

    expect(restartWorkflow).toHaveBeenCalledWith('not-a-task');
  });

  it('别的失败就是失败，不拿 fork 去盖 —— 那只会盖出一条孤儿行', async () => {
    // 503（媒体解析模块关着）是要让用户看见的，不是偷偷换条路把任务跑起来。
    vi.mocked(fetch).mockResolvedValue(envelope(503, 'MODULE_DISABLED'));

    await expect(retry('parse-8e')).rejects.toThrow();
    expect(restartWorkflow).not.toHaveBeenCalled();
  });

  it('同一个 409 下的别的理由也不回落', async () => {
    // 外层 code 永远是 http_409，两种拒绝区分不开 —— 理由必须读 details.code。
    vi.mocked(fetch).mockResolvedValue(envelope(409, 'something_else'));

    await expect(retry('parse-8e')).rejects.toThrow();
    expect(restartWorkflow).not.toHaveBeenCalled();
  });
});
