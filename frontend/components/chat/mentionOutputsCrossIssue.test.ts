/**
 * @ 页签 Outputs 跨议题（harness 三期 3c §2.4）。契约从「读一次 + 内存过滤」
 * 翻成 assets 页签那一侧：空查询 = 本议题产出，有查询 = 一次 `/search`。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown, vars?: Record<string, unknown>) => {
      const tpl = typeof fallback === 'string' ? fallback : key;
      const v = (typeof fallback === 'object' && fallback ? fallback : vars) as
        | Record<string, unknown>
        | undefined;
      return tpl.replace(/\{\{(\w+)\}\}/g, (_m, n: string) => String(v?.[n] ?? `{{${n}}}`));
    },
  }),
}));

import { searchHitsToMentionRows } from './outputMentionRows';
import { useMentionOutputsTab } from './useMentionOutputsTab';
import type { SearchHit } from '../../services/unifiedSearchService';

// `id` 是后端的不透明身份键 `kind:ref_id:version`（契约补充），不是登记行 id。
// 行的坐标从 `meta` 来——切 id 等于把后端的拼法复制到前端。
const hit = (meta: Record<string, unknown>): SearchHit => ({
  kind: 'output',
  id: 'script_shot:9:4',
  title: 'S3 · Shot 1 · MS',
  snippet: null,
  deep_link: '/team/7/todolist/MH-98',
  issue_key: 'MH-98',
  issue_id: '98',
  meta,
});

describe('跨议题命中 → mention 行', () => {
  it('carries the issue key so the row can say where it came from', () => {
    const rows = searchHitsToMentionRows([hit({ kind: 'script_shot', ref_id: '9', version: 4 })]);
    expect(rows[0]).toMatchObject({
      key: 'script_shot:9:4',
      ref_kind: 'script_shot',
      ref_id: '9',
      version: 4,
      issue_key: 'MH-98',
      title: 'S3 · Shot 1 · MS',
    });
    // 检索给的是一版，不是一条链——标成「最新」等于对一个我们没读过链的对象
    // 下结论。
    expect(rows[0].latest).toBe(false);
    expect(rows[0].startsOlderGroup).toBe(false);
  });

  it('drops a hit whose meta lacks the three coordinates', () => {
    // 引用是三坐标的。缺一个就拼不出 chip，塞进去只会在发帖时 400。
    expect(searchHitsToMentionRows([hit({ kind: 'script_shot', ref_id: '9' })])).toEqual([]);
    expect(searchHitsToMentionRows([hit({ ref_id: '9', version: 4 })])).toEqual([]);
    // `ref_id` 是 Snowflake，wire 上一律 string。number 不是「差不多的同一个
    // 东西」——它已经丢过精度了，认下来等于把一个错的 id 拼进 key。
    expect(searchHitsToMentionRows([hit({ kind: 'script_shot', ref_id: 9, version: 4 })])).toEqual([]);
  });

  it('keeps a blank title as null so the row prints its kind instead', () => {
    const rows = searchHitsToMentionRows([
      { ...hit({ kind: 'script_shot', ref_id: '9', version: 4 }), title: '' },
    ]);
    expect(rows[0].title).toBeNull();
  });
});

/**
 * 接线：options 的 `projectId` + 有没有查询 → 打哪个端点。
 *
 * 纯函数那部分（上面）证明的是「一条命中怎么变成一行」；这一段证明的是「什么
 * 时候真的去搜」。两者分开，是因为一个只在其中一侧成立的实现——行拼得对但从来
 * 没人调用它——在只有上面那组断言时是全绿的。
 */
const { listIssueOutputs } = vi.hoisted(() => ({ listIssueOutputs: vi.fn() }));
const { searchFn } = vi.hoisted(() => ({ searchFn: vi.fn() }));

vi.mock('../../services/outputsService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/outputsService')>();
  return { ...actual, listIssueOutputs };
});
vi.mock('../../services/unifiedSearchService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/unifiedSearchService')>();
  return { ...actual, unifiedSearch: (...a: unknown[]) => searchFn(...a) };
});

const OUT_HIT = {
  kind: 'output' as const,
  id: 'script_shot:727145299382534999:2',
  title: 'S3 · Shot #1',
  snippet: null,
  deep_link: '/team/7/todolist/MH-98',
  issue_key: 'MH-98',
  issue_id: '98',
  meta: { kind: 'script_shot', ref_id: '727145299382534999', version: 2 },
};

describe('@ 页签：两套契约按有没有查询切换', () => {
  beforeEach(() => {
    listIssueOutputs.mockReset();
    listIssueOutputs.mockResolvedValue([]);
    searchFn.mockReset();
    searchFn.mockResolvedValue({
      groups: { issues: [], runs: [], outputs: [OUT_HIT] },
      totals: { issues: 0, runs: 0, outputs: 1 },
      took_ms: 5,
    });
  });

  const mount = (over: Partial<Parameters<typeof useMentionOutputsTab>[0]> = {}) =>
    renderHook((props: Parameters<typeof useMentionOutputsTab>[0]) => useMentionOutputsTab(props), {
      initialProps: {
        pickerOpen: true,
        issueId: 42,
        query: '',
        projectId: '3',
        onSelect: vi.fn(),
        ...over,
      },
    });

  it('空查询读本议题，一次也不搜', async () => {
    const { result } = mount();
    act(() => result.current.outputs.onActivate?.());
    await waitFor(() => expect(listIssueOutputs).toHaveBeenCalledTimes(1));
    expect(searchFn).not.toHaveBeenCalled();
  });

  it('有查询时按项目搜，每次击键重查', async () => {
    const { result, rerender } = mount({ query: 'rain' });
    act(() => result.current.outputs.onActivate?.());
    await waitFor(() => expect(searchFn).toHaveBeenCalledTimes(1));
    expect(searchFn.mock.calls[0][0]).toMatchObject({
      q: 'rain',
      kinds: ['output'],
      projectId: '3',
    });
    // 命中经三坐标守卫变成可引用的行，并带上来源议题。
    await waitFor(() => expect(result.current.outputs.rows).toHaveLength(1));
    expect(result.current.outputs.rows[0]).toMatchObject({
      ref_kind: 'script_shot',
      version: 2,
      issue_key: 'MH-98',
      latest: false,
    });
    // 本议题那条路没被走过——两套契约是互斥的，不是叠加的。
    expect(listIssueOutputs).not.toHaveBeenCalled();

    rerender({ pickerOpen: true, issueId: 42, query: 'rainy', projectId: '3', onSelect: vi.fn() });
    await waitFor(() => expect(searchFn).toHaveBeenCalledTimes(2));
  });

  it('没有 project 的议题退回本议题——范围为空的检索是范围为全部的检索', async () => {
    const { result } = mount({ query: 'rain', projectId: null });
    act(() => result.current.outputs.onActivate?.());
    await waitFor(() => expect(listIssueOutputs).toHaveBeenCalledTimes(1));
    expect(searchFn).not.toHaveBeenCalled();
  });

  it('检索失败说出来，而不是显示成一份空货架', async () => {
    searchFn.mockRejectedValue(Object.assign(new Error('nope'), { code: 'query_too_short' }));
    const { result } = mount({ query: 'r' });
    act(() => result.current.outputs.onActivate?.());
    await waitFor(() => expect(result.current.outputs.error).toBeTruthy());
    expect(result.current.outputs.error).toContain('query_too_short');
    expect(result.current.outputs.rows).toHaveLength(0);
  });
});
