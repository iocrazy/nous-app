/**
 * harness 3a §5 (修复轮 1) — hovering an «Outputs» row in the rail rings the
 * matching card in the thread.
 *
 * The two live in different React trees (a page block vs. deep inside the
 * trajectory renderer), so this test renders BOTH and drives the real store —
 * a test of either half alone would pass while the pair stayed silent.
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { StepNode } from '../agentActivity/TrajectoryRenderer/foldEvents';
import type { OutputObject } from '../../services/outputsService';
import type { IssueBlockContext } from './issueBlocks';
import { setHighlightedOutput } from './outputHighlight';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown, opts?: unknown) => {
      const tpl = typeof fallback === 'string' ? fallback : key;
      const vars = (typeof fallback === 'object' && fallback ? fallback : opts) as Record<string, unknown> | undefined;
      return vars ? tpl.replace(/\{\{(\w+)\}\}/g, (_m, k: string) => String(vars[k] ?? `{{${k}}}`)) : tpl;
    },
  }),
}));
vi.mock('../../utils/apiConfig', () => ({ getApiUrl: () => 'http://api.test' }));
const listIssueOutputs = vi.fn();
vi.mock('../../services/outputsService', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../services/outputsService')>();
  return { ...mod, listIssueOutputs: (...a: unknown[]) => listIssueOutputs(...a) };
});
vi.mock('./OutputDiffDialog', () => ({ OutputDiffDialog: () => <div data-testid="output-diff" /> }));

const { OutputsBlockView } = await import('./blocks/OutputsBlock');
const { OutputCards } = await import('../agentActivity/TrajectoryRenderer/nodes/builtins');

const version = (v: number, parent: number | null) => ({
  id: `d${v}`, version: v, parent_version: parent, run_id: '347786145852700', issue_id: '5',
  issue_key: 'MH-91', deep_link: `/team/424242424242/todolist/MH-91?step=${v}`,
  seq: v, turn: 1, step: v, title: `S3 · Shot #1`, model: 'qwen-image', cost_cents: null,
  created_at: '2026-09-10T01:00:00Z',
});
const media: OutputObject = { kind: 'generated_media', ref_id: '77', title: 'S3 · Shot #1', latest_version: 1, versions: [version(1, null)] };
const shot: OutputObject = { kind: 'script_shot', ref_id: '9', title: 'Shot 4', latest_version: 2, versions: [version(2, 1), version(1, null)] };

const step: StepNode = {
  kind: 'step', key: 'step:1:1', turn: 1, step: 1, live: false, model: 'm', startedAt: null, lines: [],
  summary: { tools: 0, retries: 0, compactions: 0, outputs: 0, todo: null, durationMs: null, costCents: null, finishReason: null },
  children: [],
  outputs: [
    { key: 'generated_media:77:1', kind: 'generated_media', refId: '77', version: 1, parentVersion: null, title: 'S3 · Shot #1', model: 'qwen-image', costCents: null },
    { key: 'script_shot:9:2', kind: 'script_shot', refId: '9', version: 2, parentVersion: 1, title: 'Shot 4', model: 'qwen-max', costCents: 0.42 },
  ],
};

const ctx = (): IssueBlockContext => ({
  issue: { id: 347474243723822 }, rollup: { issue_id: '5' } as never, originKind: null, phase: 'running', env: {},
});

afterEach(() => {
  setHighlightedOutput(null);
  cleanup();
});
beforeEach(() => listIssueOutputs.mockReset().mockResolvedValue([media, shot]));

describe('rail hover → thread card', () => {
  it('rings the card the hovered row points at, and only while pointing', async () => {
    render(
      <>
        <OutputsBlockView ctx={ctx()} />
        <OutputCards node={step} />
      </>,
    );
    const rows = await screen.findAllByTestId('outputs-row');
    const cards = screen.getAllByTestId('output-card');
    expect(cards.map((c) => c.getAttribute('data-highlighted'))).toEqual(['false', 'false']);

    fireEvent.mouseEnter(rows[0]);
    await waitFor(() => expect(screen.getAllByTestId('output-card')[0].getAttribute('data-highlighted')).toBe('true'));
    expect(screen.getAllByTestId('output-card')[1].getAttribute('data-highlighted')).toBe('false');

    fireEvent.mouseLeave(rows[0]);
    await waitFor(() => expect(screen.getAllByTestId('output-card')[0].getAttribute('data-highlighted')).toBe('false'));
  });

  it('a revised object points at its LATEST version, which is the card the thread shows last', async () => {
    render(
      <>
        <OutputsBlockView ctx={ctx()} />
        <OutputCards node={step} />
      </>,
    );
    const rows = await screen.findAllByTestId('outputs-row');
    fireEvent.mouseEnter(rows[1]);
    await waitFor(() => expect(screen.getAllByTestId('output-card')[1].getAttribute('data-highlighted')).toBe('true'));
  });

});
