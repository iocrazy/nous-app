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

vi.mock('./apiClient', () => ({
  apiClient: { get: vi.fn() },
  ApiError: class ApiError extends Error {
    status: number;
    code?: string;
    constructor(m: string, s: number, o: { code?: string } = {}) {
      super(m);
      this.status = s;
      this.code = o.code;
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
    vi.mocked(apiClient.get).mockRejectedValueOnce(new ApiError('nope', 404, { code: 'not_registered' }));
    await expect(getResourceProvenance('1')).resolves.toBeNull();
  });

  it('any other failure propagates', async () => {
    // 500 静默成 null 会让「读不到」长得跟「人传的」一模一样。
    vi.mocked(apiClient.get).mockRejectedValueOnce(new ApiError('boom', 500, {}));
    await expect(getResourceProvenance('1')).rejects.toThrow('boom');
  });
});
