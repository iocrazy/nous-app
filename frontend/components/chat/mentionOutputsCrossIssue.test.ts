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
    // 两个字符 —— 客户端的下限，所以请求真的会发出去。写一个字符的话这条断言
    // 测的是「短查询不搜」，不是「搜失败要说出来」。
    const { result } = mount({ query: 'ra' });
    act(() => result.current.outputs.onActivate?.());
    await waitFor(() => expect(result.current.outputs.error).toBeTruthy());
    expect(result.current.outputs.error).toContain('query_too_short');
    expect(result.current.outputs.rows).toHaveLength(0);
  });
});

/**
 * 短查询、错误清除与 loading 复位（3c Task 17 修复轮 1）。
 */
describe('@ 页签：查询太短、清空与在途取消', () => {
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

  const props = (over: Partial<Parameters<typeof useMentionOutputsTab>[0]> = {}) => ({
    pickerOpen: true,
    issueId: 42,
    query: '',
    projectId: '3' as string | number | null,
    onSelect: vi.fn(),
    ...over,
  });

  const mount = (over: Partial<Parameters<typeof useMentionOutputsTab>[0]> = {}) =>
    renderHook((p: Parameters<typeof useMentionOutputsTab>[0]) => useMentionOutputsTab(p), {
      initialProps: props(over),
    });

  it('第一个字符不发请求 —— 端点会拒，那次往返只换回一条注定的错误', () => {
    // 输入是逐字符到达的，所以「一个字符」不是边角情况，是**每一次**搜索的第一
    // 帧。让它打出去，等于每次用户开始打字界面都闪一次 query_too_short。
    const { result } = mount({ query: 'r' });
    act(() => result.current.outputs.onActivate?.());
    expect(searchFn).not.toHaveBeenCalled();
    // 也不该退化成「读本议题」：读者正在打字，本议题那份此刻没人看。
    expect(result.current.outputs.error).toBeNull();
  });

  it('删回空串后错误横幅消失 —— 即使本议题那份早就读过了', async () => {
    // 顺序很重要：**先**空查询读一次本议题（`requested` 从此为真），再搜、再
    // 失败、再删回空串。这时本议题那条路会被自己的守卫早退，于是没有任何人去
    // 清那个错误码 —— 一条关于「那次搜索」的横幅，留在一份「本议题产出」的列表
    // 上方，说的是一件此刻没有发生的失败。
    //
    // 反过来写（先搜后清）是测不出来的：那时列表路径会真的跑一次并顺手清掉错
    // 误，缺陷被另一条路的副作用盖住。
    const { result, rerender } = mount({ query: '' });
    act(() => result.current.outputs.onActivate?.());
    await waitFor(() => expect(listIssueOutputs).toHaveBeenCalledTimes(1));

    searchFn.mockRejectedValue(Object.assign(new Error('nope'), { code: 'boom' }));
    rerender(props({ query: 'rain' }));
    await waitFor(() => expect(result.current.outputs.error).toBeTruthy());

    rerender(props({ query: '' }));
    await waitFor(() => expect(result.current.outputs.error).toBeNull());
    // 本议题那份没有被重读 —— 守卫仍然成立，清错误不是靠再发一次请求。
    expect(listIssueOutputs).toHaveBeenCalledTimes(1);
  });

  it('搜索在途时关掉 mention，loading 不会永远卡住', async () => {
    // cleanup 把 `live` 置 false，于是那次请求的 `finally` 再也不会
    // `setLoading(false)`。清空查询时碰巧有另一条路接手并自己复位，所以那条路
    // 掩盖了缺陷；真正暴露它的是**没有接手者**的那次切换 —— 关掉 mention。
    // 后果是下次打开时一条永远转着的 Loading…，而没有任何请求在飞。
    let settle: ((v: unknown) => void) | null = null;
    searchFn.mockReturnValue(new Promise((res) => { settle = res; }));
    const { result } = mount({ query: 'rain' });
    act(() => result.current.outputs.onActivate?.());
    await waitFor(() => expect(result.current.outputs.loading).toBe(true));
    act(() => result.current.reset());
    await waitFor(() => expect(result.current.outputs.loading).toBe(false));
    act(() => settle?.({ groups: { issues: [], runs: [], outputs: [] }, totals: { issues: 0, runs: 0, outputs: 0 }, took_ms: 1 }));
    await waitFor(() => expect(result.current.outputs.loading).toBe(false));
  });
});

/**
 * 行带的是**事实**，比较是显示方的事（3c Task 17 修复轮 2）。
 *
 * `@` 的检索按**项目**作用域，所以本议题自己的产出必然也在结果里，而后端对每
 * 条命中都填 `issue_key`。于是「行只在来自别处时才带来源」这句话从一开始就是
 * 错的 —— 无条件抄下来是对的，判「不同」要发生在画它的地方。
 */
describe('搜来的行无条件记下产出在哪', () => {
  it('tab 把当前议题编号交给列表 —— 比较发生在画的地方', () => {
    // 行记录事实、列表判不同，这条链只有在 tab 真的把编号传下去时才成立。
    // 不传的后果是静默的：每一行都挂上读者正看着的那件议题，而所有关于「行」
    // 的断言照样全绿。
    const { result } = renderHook(
      (p: Parameters<typeof useMentionOutputsTab>[0]) => useMentionOutputsTab(p),
      { initialProps: { pickerOpen: true, issueId: 42, query: '', issueKey: 'MH-96', onSelect: vi.fn() } },
    );
    expect(result.current.outputs.currentIssueKey).toBe('MH-96');
  });

  it('没有议题编号时交出 null —— 不比较，全标', () => {
    const { result } = renderHook(
      (p: Parameters<typeof useMentionOutputsTab>[0]) => useMentionOutputsTab(p),
      { initialProps: { pickerOpen: true, issueId: 42, query: '', onSelect: vi.fn() } },
    );
    expect(result.current.outputs.currentIssueKey).toBeNull();
  });

  it('本议题的命中也带 issue_key —— 检索按项目作用域，它必然在结果里', () => {
    const rows = searchHitsToMentionRows([
      { ...hit({ kind: 'script_shot', ref_id: '9', version: 4 }), issue_key: 'MH-96' },
    ]);
    // 在这一层丢掉它，画的人就再也无从知道这一版产自哪里 —— 「和当前议题相同」
    // 与「后端没说」会长得一模一样。
    expect(rows[0].issue_key).toBe('MH-96');
  });
});
