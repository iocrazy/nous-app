/**
 * `/api/v1/search` 的 client（harness 三期 3c Task 17 / 契约 §1）。
 *
 * 与 `searchService.test.ts` 是两个模块两份测试：那一个测的是资源库的语义/混合
 * 检索（同名的 `SearchResponse`，完全不同的字段），本文件测的是三组统一检索。
 *
 * fixture 用**真实 wire 形状**：命中的 `id` / `issue_id` 是 string（后端刻意
 * stringify 的 Snowflake），`version` 是 number；拒绝体是生产的 ErrorResponse
 * 外壳，类型化码在 `details.code` 不在 `detail`（CLAUDE.md 2026-09-09）。
 *
 * 议题命中的 `meta` 照 `backend/app/schemas/unified_search.py` 抄：那里给的是
 * `assignee_user_id` / `assignee_agent_id` 两个 id，**没有** `assignee_name`
 * ——契约 §1 写的是名字，实现刻意不造一个永远为 null 的字段。fixture 写成名字
 * 就等于给一个后端不发的响应写测试。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'http://api.test' }));
vi.mock('./parserService', () => ({ getAuthHeaders: async () => ({ Authorization: 'Bearer t' }) }));

const { unifiedSearch, UnifiedSearchError } = await import('./unifiedSearchService');

const fetchMock = vi.fn();
beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

const WIRE = {
  groups: {
    issues: [
      {
        kind: 'issue',
        id: '347786145852739',
        title: 'MH-96 · Alpha rain on glass',
        snippet: null,
        deep_link: '/team/7/todolist/MH-96',
        issue_key: 'MH-96',
        issue_id: '347786145852739',
        meta: { status: 'in_progress', assignee_user_id: 'u-1', assignee_agent_id: null },
      },
    ],
    runs: [],
    outputs: [],
  },
  totals: { issues: 1, runs: 0, outputs: 0 },
  took_ms: 12,
};

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

describe('unifiedSearchService', () => {
  it('sends q, kinds and the scope as query params, keeping ids strings', async () => {
    fetchMock.mockResolvedValueOnce(json(200, WIRE));
    const res = await unifiedSearch({ q: 'rain', kinds: ['output'], projectId: '3' });
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain('q=rain');
    expect(url).toContain('kinds=output');
    expect(url).toContain('project_id=3');
    expect(typeof res.groups.issues[0].id).toBe('string');
    expect(res.groups.issues[0].id).toBe('347786145852739');
    expect(res.totals.issues).toBe(1);
  });

  it('joins several kinds with a comma and omits the scopes the caller left out', async () => {
    fetchMock.mockResolvedValueOnce(json(200, WIRE));
    await unifiedSearch({ q: 'rain', kinds: ['issue', 'run'], limitPerGroup: 5 });
    const url = String(fetchMock.mock.calls[0][0]);
    expect(decodeURIComponent(url)).toContain('kinds=issue,run');
    expect(url).toContain('limit_per_group=5');
    expect(url).not.toContain('project_id');
    expect(url).not.toContain('team_id');
    expect(url).not.toContain('issue_id');
  });

  it('reads a typed refusal from details.code', async () => {
    fetchMock.mockResolvedValueOnce(
      json(400, {
        success: false,
        error: 'a search needs at least 2 characters',
        code: 'http_400',
        request_id: 'r-1',
        details: { code: 'query_too_short', message: 'a search needs at least 2 characters' },
      }),
    );
    await expect(unifiedSearch({ q: 'r' })).rejects.toMatchObject({
      code: 'query_too_short',
      status: 400,
    });
  });

  it('keeps the status line when the refusal typed nothing', async () => {
    fetchMock.mockResolvedValueOnce(new Response('<html>502</html>', { status: 502 }));
    await expect(unifiedSearch({ q: 'rain' })).rejects.toBeInstanceOf(UnifiedSearchError);
    fetchMock.mockResolvedValueOnce(new Response('<html>502</html>', { status: 502 }));
    await expect(unifiedSearch({ q: 'rain' })).rejects.toMatchObject({ code: 'http_502' });
  });
});
