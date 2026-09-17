/**
 * The `@` picker's THIRD population: this issue's registered OUTPUTS.
 *
 * Its own file for the reason the assets one has its own: the tab is OPTIONAL.
 * The canvas renders this popover with neither extra tab, the chat panel
 * renders it with Assets only (citations are issue-scoped, so the chat
 * composer REFUSES them in 3a), and the issue reply box is the one host that
 * passes all three. "Still works without the tab" has to be a case somebody
 * can read.
 *
 * What is under test is the SEAM — which tab is lit, which body draws, and
 * above all that adding a third body did not leave two of them on screen at
 * once. The rows themselves are covered in `outputMentionRows.test.ts`.
 *
 * Row shapes come from `toMentionRows`, i.e. from the real endpoint's wire
 * shape: ids are strings, `title` may be null.
 */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';

import { ResourcePickerSuggestion, type OutputsTabProps } from './ResourcePickerSuggestion';
import type { OutputMentionRow } from './outputMentionRows';
import { OutputMentionList, type OutputMentionListHandle } from './OutputMentionList';
import type { ResourceSearchResult } from '../../types';

afterEach(cleanup);

const ROWS: ResourceSearchResult[] = [
  {
    id: '1', name: 'story.md', kind: 'doc', mime: 'text/markdown', size: 100,
    scope: { type: 'personal', id: 'u' }, updated_at: '2026-05-24T00:00:00Z',
    thumbnail_url: null, transcript_status: null, summary_status: null,
  },
];
const COUNTS = { all: 1, video: 0, image: 0, doc: 1, audio: 0, pdf: 0 };

const row = (over: Partial<OutputMentionRow> = {}): OutputMentionRow => ({
  key: 'script_shot:727145299382534999:3',
  ref_kind: 'script_shot',
  ref_id: '727145299382534999',
  version: 3,
  title: 'S3 · Shot #1',
  // 本议题读来的行不带来源议题（3c §2.4）——chip 只画在真从别处搜来的那些上。
  issue_key: null,
  latest: true,
  startsOlderGroup: false,
  ...over,
});

const OLDER = row({ key: 'script_shot:727145299382534999:2', version: 2, latest: false, startsOlderGroup: true });

function makeOutputs(over: Partial<OutputsTabProps> = {}): OutputsTabProps {
  return {
    active: false,
    onActivate: vi.fn(),
    rows: [row(), OLDER],
    loading: false,
    error: null,
    onSelect: vi.fn(),
    ...over,
  };
}

function renderPicker(props: Partial<React.ComponentProps<typeof ResourcePickerSuggestion>> = {}) {
  return render(
    <ResourcePickerSuggestion
      items={ROWS}
      query=""
      loading={false}
      counts={COUNTS}
      activeKind=""
      onKindChange={vi.fn()}
      onSelect={vi.fn()}
      {...props}
    />,
  );
}

describe('the Outputs tab', () => {
  it('is absent when the host passes no outputs prop', () => {
    renderPicker();
    expect(screen.queryByTestId('resource-picker-tab-outputs')).toBeNull();
    // ...and the popover it shares with five resource tabs still works.
    expect(screen.getByTestId('resource-picker-list')).toBeTruthy();
  });

  it('draws a tab that reports itself unpressed until activated', () => {
    renderPicker({ outputs: makeOutputs() });
    const tab = screen.getByTestId('resource-picker-tab-outputs');
    expect(tab.getAttribute('aria-pressed')).toBe('false');
    expect(tab.getAttribute('data-kind')).toBe('outputs');
  });

  it('asks the host to activate on click rather than deciding for itself', () => {
    const onActivate = vi.fn();
    renderPicker({ outputs: makeOutputs({ onActivate }) });
    fireEvent.click(screen.getByTestId('resource-picker-tab-outputs'));
    expect(onActivate).toHaveBeenCalled();
  });

  it('replaces the resource list when active — never two bodies at once', () => {
    // The regression this pins: the body swap was written as
    // `assetsActive ? null : <list>`. A third tab added to the strip WITHOUT
    // extending that condition lights its own tab, draws its own rows, and
    // leaves the five-kind resource list underneath — two lists, one set of
    // arrow keys, and Enter picking from whichever the code reached first.
    renderPicker({ outputs: makeOutputs({ active: true }) });
    expect(screen.queryByTestId('resource-picker-list')).toBeNull();
    expect(screen.getByTestId('output-picker-list')).toBeTruthy();
  });

  it('leaves the resource list alone when the tab is merely offered', () => {
    renderPicker({ outputs: makeOutputs() });
    expect(screen.getByTestId('resource-picker-list')).toBeTruthy();
    expect(screen.queryByTestId('output-picker-list')).toBeNull();
  });

  it('does not draw the Assets body while Outputs is the active tab', () => {
    const assets = {
      active: false,
      onActivate: vi.fn(),
      count: 3,
      onCountChange: vi.fn(),
      onSelect: vi.fn(),
      fetch: vi.fn(async () => []),
    };
    renderPicker({ outputs: makeOutputs({ active: true }), assets });
    expect(screen.queryByTestId('resource-picker-list')).toBeNull();
    expect(screen.getByTestId('output-picker-list')).toBeTruthy();
    expect(screen.getByTestId('resource-picker-tab-assets').getAttribute('aria-pressed')).toBe('false');
  });

  it('renders one row per citable version, carrying its version', () => {
    renderPicker({ outputs: makeOutputs({ active: true }) });
    const rendered = screen.getAllByTestId('output-picker-row');
    expect(rendered).toHaveLength(2);
    expect(rendered[0].getAttribute('data-version')).toBe('3');
    expect(rendered[0].getAttribute('data-ref')).toBe('727145299382534999');
  });

  it('draws the Older header exactly where the fold starts', () => {
    renderPicker({ outputs: makeOutputs({ active: true }) });
    expect(screen.getAllByTestId('output-picker-older')).toHaveLength(1);
  });

  it('draws no Older header when nothing has been revised', () => {
    renderPicker({ outputs: makeOutputs({ active: true, rows: [row()] }) });
    expect(screen.queryByTestId('output-picker-older')).toBeNull();
  });

  it('hands the picked row back whole, version included', () => {
    const onSelect = vi.fn();
    renderPicker({ outputs: makeOutputs({ active: true, onSelect }) });
    fireEvent.click(screen.getAllByTestId('output-picker-row')[1]);
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ version: 2, ref_id: '727145299382534999' }));
  });

  it('says the read failed instead of showing an empty shelf', () => {
    // "produced nothing" and "I could not find out" are answers a reader acts
    // on differently — the same rule the Outputs block follows.
    renderPicker({ outputs: makeOutputs({ active: true, rows: [], error: 'Could not read this issue’s outputs' }) });
    expect(screen.getByTestId('output-picker-error').textContent).toContain('Could not read');
    expect(screen.queryByTestId('output-picker-empty')).toBeNull();
  });

  it('says the issue has produced nothing when the read succeeded and was empty', () => {
    renderPicker({ outputs: makeOutputs({ active: true, rows: [], error: null }) });
    expect(screen.getByTestId('output-picker-empty')).toBeTruthy();
    expect(screen.queryByTestId('output-picker-error')).toBeNull();
  });

  it('shows loading rather than "nothing produced" while the read is in flight', () => {
    renderPicker({ outputs: makeOutputs({ active: true, rows: [], loading: true }) });
    expect(screen.queryByTestId('output-picker-empty')).toBeNull();
    expect(screen.getByTestId('output-picker-loading')).toBeTruthy();
  });
});

/**
 * The list's own half of the keyboard contract. The hook decides WHETHER the
 * tab claims a key; the list decides what the key does and, crucially, when
 * there is nothing to do — `commitActive()` answering false is what lets Enter
 * reach the text instead of vanishing into a pick that never happened.
 */
describe('OutputMentionList — the handle the host routes keys into', () => {
  const rows = [
    row(),
    row({ key: 'script_shot:727145299382534999:2', version: 2, latest: false, startsOlderGroup: true }),
  ];

  function renderList(props: Partial<React.ComponentProps<typeof OutputMentionList>> = {}) {
    const ref = React.createRef<OutputMentionListHandle>();
    const onPick = vi.fn();
    render(
      <OutputMentionList ref={ref} active rows={rows} loading={false} error={null} onPick={onPick} {...props} />,
    );
    return { ref, onPick };
  }

  it('starts on the first row, which is the latest version', () => {
    const { ref, onPick } = renderList();
    act(() => { ref.current!.commitActive(); });
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ version: 3, latest: true }));
  });

  it('moves the highlight down', () => {
    const { ref, onPick } = renderList();
    act(() => { ref.current!.move(1); });
    act(() => { ref.current!.commitActive(); });
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ version: 2 }));
  });

  it('wraps rather than running off either end', () => {
    const { ref, onPick } = renderList();
    act(() => { ref.current!.move(-1); });
    act(() => { ref.current!.commitActive(); });
    // Up from the first row lands on the last, so the reader never reaches a
    // dead end where the arrow key does nothing and nothing says why.
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ version: 2 }));
  });

  it('answers false with an empty list instead of swallowing the keystroke', () => {
    const { ref, onPick } = renderList({ rows: [] });
    let claimed = true;
    act(() => { claimed = ref.current!.commitActive(); });
    expect(claimed).toBe(false);
    expect(onPick).not.toHaveBeenCalled();
  });

  it('claims nothing while its tab is not showing', () => {
    const { ref, onPick } = renderList({ active: false });
    let claimed = true;
    act(() => { claimed = ref.current!.commitActive(); });
    expect(claimed).toBe(false);
    act(() => { ref.current!.move(1); });
    expect(onPick).not.toHaveBeenCalled();
  });
});

/**
 * 来源议题 chip 的判定（3c Task 17 修复轮 2）。
 *
 * `@` 的检索按**项目**作用域，所以本议题自己的产出必然也在结果里，而后端对每条
 * 命中都填 `issue_key`。行无条件记下它（那是事实），这里判「不同」才画 —— 与
 * 线程里已发出的引用卡、轨迹里的引用条同一条规则。
 */
describe('Outputs 行的来源议题', () => {
  const crossIssue = row({ key: 'script_shot:9:4', version: 4, issue_key: 'MH-98' });
  const sameIssue = row({ key: 'script_shot:8:1', version: 1, issue_key: 'MH-96' });

  it('只标注来自别的议题的那一行', () => {
    renderPicker({
      outputs: makeOutputs({ active: true, rows: [sameIssue, crossIssue], currentIssueKey: 'MH-96' }),
    });
    const marks = screen.getAllByTestId('output-picker-issue');
    expect(marks).toHaveLength(1);
    expect(marks[0].textContent).toBe('MH-98');
  });

  it('不知道自己在哪件议题上时全标 —— 那时「来自别处」无从判断', () => {
    renderPicker({
      outputs: makeOutputs({ active: true, rows: [sameIssue, crossIssue], currentIssueKey: null }),
    });
    expect(screen.getAllByTestId('output-picker-issue')).toHaveLength(2);
  });
});
