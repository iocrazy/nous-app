/**
 * harness 3a T8c 缺陷 4 — 「引用 N 件」那一行。
 *
 * spec §5 稿二要求它注明**按版本锁定，不随后续修订漂移**。那句话是引用语义
 * 在整个界面上**唯一**的说明：没有它，读者无从知道引用钉在某一版上，而不是
 * 跟着对象走。真机验收实测这一行从未实现（`outputs` 命名空间 45 个 key 里
 * 一个都不表达它）。
 *
 * ⚠️ 别与 `STEP N · 1 outputs` 混淆：那是既有的 `summary.outputs`（助手输出
 * 块数），与产出登记无关，纯属标签撞名。
 */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import en from '../../../../public/locales/en.json';
import zh from '../../../../public/locales/zh.json';
import type { OutputCitation, StepNode } from '../foldEvents';
import { OutputCitations } from './builtins';
import { TrajectoryIssueKeyContext } from '../trajectoryRunContext';

// Resolved against the REAL shipped English copy, with i18next's plural-suffix
// lookup, because the plural forms are the thing under test: a `{{count}}` key
// with no `_one` / `_other` renders "References 1 outputs" for the commonest
// case of all (真机那一轮引用的就是 1 件). A hand-written template in the mock
// would hide exactly that — the same reason `AISettings.governance.test` reads
// this file instead of its own table.
vi.mock('react-i18next', () => {
  const lookup = (key: string): unknown =>
    key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown>)?.[part], en);
  const t = (key: string, fallback?: unknown, opts?: unknown) => {
    const vars = (typeof fallback === 'object' && fallback ? fallback : opts) as
      | Record<string, unknown>
      | undefined;
    const count = vars?.count;
    const suffixed =
      typeof count === 'number' ? lookup(`${key}_${count === 1 ? 'one' : 'other'}`) : undefined;
    const resolved = suffixed ?? lookup(key);
    const template = typeof resolved === 'string' ? resolved : typeof fallback === 'string' ? fallback : key;
    return vars ? template.replace(/\{\{(\w+)\}\}/g, (_m, k: string) => String(vars[k] ?? `{{${k}}}`)) : template;
  };
  return { useTranslation: () => ({ t }) };
});

const CITED: OutputCitation[] = [
  // ⚠️ **每条可解析的引用都带 `issueKey`**。`output_ref_resolver._stamped` 不与
  // 任何「当前议题」比较 —— 它填的是这一版产出在哪，本议题产出的那条也照填。
  // 把同议题那条写成 null 等于给一个后端不会发的响应写测试，而「只在不同时才
  // 画」这条规则就永远不会被这组断言碰到。
  { key: 'script_shot:337650953731886:2', kind: 'script_shot', refId: '337650953731886', version: 2, title: 'MEDIUM', issueKey: 'MH-96' },
  { key: 'generated_media:77:1', kind: 'generated_media', refId: '77', version: 1, title: 'S3 · Shot #1', issueKey: 'MH-98' },
];

const step = (citations?: OutputCitation[]): StepNode => ({
  kind: 'step', key: 'step:1:1', turn: 1, step: 1, live: false, model: 'm', startedAt: null,
  lines: [], summary: { tools: 0, retries: 0, compactions: 0, outputs: 0, todo: null, durationMs: null, costCents: null, finishReason: null },
  children: [], outputs: [], citations,
});

afterEach(cleanup);

describe('OutputCitations', () => {
  it('says how many were cited and that they are pinned to a version', () => {
    render(<OutputCitations node={step(CITED)} />);
    const el = screen.getByTestId('output-citations');
    expect(el.textContent).toContain('References 2 outputs');
    expect(el.textContent).toContain('pinned to version');
  });

  it('says "1 output", not "1 outputs" — the commonest case of all', () => {
    render(<OutputCitations node={step([CITED[0]])} />);
    const el = screen.getByTestId('output-citations');
    expect(el.textContent).toContain('References 1 output ');
    expect(el.textContent).not.toContain('1 outputs');
  });

  it('ships both plural forms in both locales, and no bare key', () => {
    // zh has no plural category so its two forms read the same — but the key
    // SETS must match (`i18n-rendering` 的结构守卫), which is how the
    // neighbouring mentionCount / revisions / provenanceVersions pairs are
    // written too. The bare `cited` must be gone: with `{{count}}` passed,
    // i18next looks up the suffixed key first and only falls back to the bare
    // one — leaving it would hide a missing plural form.
    expect(en.outputs.cited_one).toContain('{{count}} output ');
    expect(en.outputs.cited_other).toContain('{{count}} outputs ');
    expect(zh.outputs.cited_one).toBe(zh.outputs.cited_other);
    expect('cited' in en.outputs).toBe(false);
    expect('cited' in zh.outputs).toBe(false);
  });

  it('names each cited version the way the composer chip did', () => {
    // 在「就是这件议题」的 provider 里渲染，来源标签才不会混进被断言的名字里。
    render(
      <TrajectoryIssueKeyContext.Provider value="MH-96">
        <OutputCitations node={step(CITED)} />
      </TrajectoryIssueKeyContext.Provider>,
    );
    const chips = screen.getAllByTestId('output-citation-chip');
    expect(chips).toHaveLength(2);
    expect(chips[0].textContent).toBe('@MEDIUM v2');
    expect(chips[0].getAttribute('data-ref')).toBe('337650953731886');
    expect(chips[0].getAttribute('data-version')).toBe('2');
    expect(chips[1].textContent).toContain('@S3 · Shot #1 v1');
  });

  it('只在来源议题与正在读的这件不同时才画', () => {
    render(
      <TrajectoryIssueKeyContext.Provider value="MH-96">
        <OutputCitations node={step(CITED)} />
      </TrajectoryIssueKeyContext.Provider>,
    );
    // 两条都带 issueKey（后端对每条都填），但只有 MH-98 与当前议题不同。
    // 判「有没有」而不是「同不同」会让每条引用都挂一个指着你正看着的议题的
    // 标签 —— 整片都画，唯一真的来自别处的那一条就不再显眼了。
    const marks = screen.getAllByTestId('output-citation-issue');
    expect(marks).toHaveLength(1);
    expect(marks[0].textContent).toBe('MH-98');
  });

  it('不知道自己在哪时全画 —— 聊天面板的轨迹背后没有议题', () => {
    // Provider 之外（chat 的 Trajectory 页签、Task Center 详情）默认 null。
    // 那时「不比较」是诚实答案：每条引用都该说出自己来自哪。
    render(<OutputCitations node={step(CITED)} />);
    expect(screen.getAllByTestId('output-citation-issue')).toHaveLength(2);
  });

  it('falls back to the coordinates when the citation kept no title', () => {
    render(
      <TrajectoryIssueKeyContext.Provider value="MH-96">
        <OutputCitations node={step([{ ...CITED[0], title: null }])} />
      </TrajectoryIssueKeyContext.Provider>,
    );
    expect(screen.getByTestId('output-citation-chip').textContent).toBe('@script shot #337650953731886 v2');
  });

  it('draws nothing for a step that cited nothing', () => {
    const { container } = render(<OutputCitations node={step()} />);
    expect(container.innerHTML).toBe('');
  });

  it('draws nothing for an empty list either', () => {
    const { container } = render(<OutputCitations node={step([])} />);
    expect(container.innerHTML).toBe('');
  });
});
