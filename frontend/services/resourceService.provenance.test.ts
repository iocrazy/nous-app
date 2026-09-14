/**
 * 资源反查产出它的 run（harness 三期 3b Task 7b，spec §5 稿四）。
 *
 * 一条规则贯穿全文件：**404 是答案，不是故障**。资源库里绝大多数行是人传的，
 * 后端在没有 generated_media 促成它时答 404 `not_registered`——把常态渲染成
 * 错误，等于在每一个资源面板上挂一个永久假警报。反过来，其它失败必须外抛：
 * 静默成 `null` 会让「读不到」长得跟「人传的」一模一样，而这两件事读者要采取
 * 的行动完全不同。
 *
 * mock 用真实 wire 形状：血缘响应里每个 id 都是 string（`backend/app/schemas/
 * outputs.py` 的 `id: str` / `run_id: str` / `issue_id: Optional[str]`），
 * 与 CLAUDE.md「边界 mock 必须用真实 JSON 形状」同一条纪律。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

// 真实 `ApiError` 的形状：生产的 ErrorResponse 外壳让 `code` 是 `http_404`，
// 类型码落在 `details` 里（`apiClient.ts` 的 `code: body?.code ?? …` /
// `details: body?.details ?? …`）。桩少一个 `details`，被测代码就只能去读
// `code`，而那正是真栈上判错的那条路。
vi.mock('./apiClient', () => ({
  apiClient: { get: vi.fn() },
  ApiError: class ApiError extends Error {
    status: number;
    code?: string;
    details?: unknown;
    constructor(m: string, s: number, o: { code?: string; details?: unknown } = {}) {
      super(m);
      this.status = s;
      this.code = o.code;
      this.details = o.details;
    }
  },
}));

const { apiClient, ApiError } = await import('./apiClient');
const { getResourceProvenance } = await import('./resourceService');

const body = {
  kind: 'generated_media',
  ref_id: '347786145852739',
  latest_version: 1,
  // Snowflake 水位，字符串（过 2^53 在浏览器里掉精度）。
  as_of_seq: '347786145852739011',
  versions: [
    {
      id: '347786145852739011',
      version: 1,
      parent_version: null,
      run_id: '727145299382534100',
      issue_id: '727145299382534000',
      issue_key: 'MH-94',
      deep_link: '/team/424242424242/todolist/MH-94?step=3',
      seq: 31,
      turn: 1,
      step: 3,
      title: 'Cover',
      model: 'gpt-6-astra',
      cost_cents: 12,
      cost_kind: 'exact',
      actor_user_id: null,
      reverted_from_version: null,
      created_at: '2026-09-12T02:00:00Z',
    },
  ],
};

describe('getResourceProvenance', () => {
  beforeEach(() => vi.mocked(apiClient.get).mockReset());

  it('reads the chain for a promoted 资源', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce(body);
    const out = await getResourceProvenance('347786145852739000');
    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/resources/347786145852739000/provenance');
    expect(out?.versions[0].run_id).toBe('727145299382534100');
  });

  it('a human upload is null, not an error', async () => {
    // 404 not_registered 是答案不是故障：库里大多数资源都是人传的。
    // 原样的生产错误体：外壳的 code 是 http_404，类型码在 details 里。
    vi.mocked(apiClient.get).mockRejectedValueOnce(
      new ApiError('no generation was promoted into this resource', 404, {
        code: 'http_404',
        details: { code: 'not_registered', message: 'no generation was promoted into this resource' },
      }),
    );
    await expect(getResourceProvenance('1')).resolves.toBeNull();
  });

  it('a resource that is not there is also null', async () => {
    // 这条路的另一个 404（`not_found`）对这块 UI 是同一个答案：没有来源可画。
    vi.mocked(apiClient.get).mockRejectedValueOnce(
      new ApiError('resource not found', 404, {
        code: 'http_404',
        details: { code: 'not_found', message: 'resource not found' },
      }),
    );
    await expect(getResourceProvenance('1')).resolves.toBeNull();
  });

  it('a 404 that is not one of ours propagates', async () => {
    // 路由还没部署时 FastAPI 答的是裸 `{"detail":"Not Found"}` —— 没有类型码。
    // 把它收成 null 会让整块来源在全站静默消失，而没有一处会说出来。
    vi.mocked(apiClient.get).mockRejectedValueOnce(new ApiError('Not Found', 404, {}));
    await expect(getResourceProvenance('1')).rejects.toThrow('Not Found');
  });

  it('any other failure propagates', async () => {
    // 500 静默成 null 会让「读不到」长得跟「人传的」一模一样。
    vi.mocked(apiClient.get).mockRejectedValueOnce(new ApiError('boom', 500, {}));
    await expect(getResourceProvenance('1')).rejects.toThrow('boom');
  });
});
