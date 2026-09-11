/**
 * harness 3a §5 — the right-rail "Outputs" block.
 *
 * Its unit is the OBJECT, not the row: three revisions of one shot are one
 * line that says v3, not three lines. A read that FAILED says so rather than
 * drawing an empty card — "produced nothing" and "I could not find out" are
 * answers a person acts on differently (same rule as the Schedules block).
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { OutputObject } from '../../../services/outputsService';
import type { IssueBlockContext } from '../issueBlocks';
import { OutputsBlockView, outputsBlock } from './OutputsBlock';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown, vars?: Record<string, unknown>) => {
      const tpl = typeof fallback === 'string' ? fallback : key;
      const v = (typeof fallback === 'object' && fallback ? fallback : vars) as Record<string, unknown> | undefined;
      return tpl.replace(/\{\{(\w+)\}\}/g, (_m, n: string) => String(v?.[n] ?? `{{${n}}}`));
    },
  }),
}));

const listIssueOutputs = vi.fn();
vi.mock('../../../services/outputsService', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../../services/outputsService')>();
  return { ...mod, listIssueOutputs: (...a: unknown[]) => listIssueOutputs(...a) };
});
vi.mock('../OutputDiffDialog', () => ({
  OutputDiffDialog: ({ kind, refId, initialTo }: { kind: string; refId: string; initialTo?: number }) => (
    <div data-testid="output-diff" data-kind={kind} data-ref={refId} data-to={String(initialTo ?? '')} />
  ),
}));

const ISSUE_ID = 347474243723822;

const version = (version: number, parent: number | null) => ({
  id: `d${version}`, version, parent_version: parent, run_id: '347786145852700', issue_id: String(ISSUE_ID),
  seq: version, turn: 1, step: version, title: `Shot #1 v${version}`, model: 'qwen-max',
  cost_cents: version === 1 ? null : 0.42, created_at: '2026-09-10T01:00:00Z',
});

const shot: OutputObject = { kind: 'script_shot', ref_id: '9', title: 'Shot #1 v3', latest_version: 3, versions: [version(3, 2), version(2, 1), version(1, null)] };
const image: OutputObject = { kind: 'generated_media', ref_id: '77', title: 'S3 · Shot #1', latest_version: 1, versions: [version(1, null)] };

function ctx(): IssueBlockContext {
  return { issue: { id: ISSUE_ID }, rollup: { issue_id: String(ISSUE_ID) } as never, originKind: null, phase: 'running', env: {} };
}

afterEach(cleanup);
beforeEach(() => listIssueOutputs.mockReset().mockResolvedValue([shot, image]));

describe('OutputsBlockView', () => {
  it('shows one row per object with its latest version', async () => {
    render(<OutputsBlockView ctx={ctx()} />);
    const rows = await screen.findAllByTestId('outputs-row');
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain('Shot #1 v3');
    expect(rows[0].textContent).toContain('v3');
    expect(rows[0].getAttribute('data-kind')).toBe('script_shot');
    // 3 registrations of one object are ONE row, not three
    expect(rows[0].textContent).toContain('2 revisions');
    expect(rows[1].textContent).not.toContain('revision');
  });

  it('draws nothing at all when the issue produced nothing', async () => {
    listIssueOutputs.mockResolvedValueOnce([]);
    const { container } = render(<OutputsBlockView ctx={ctx()} />);
    await waitFor(() => expect(listIssueOutputs).toHaveBeenCalled());
    await waitFor(() => expect(container.querySelector('[data-testid="outputs-block"]')).toBeNull());
  });

  it('says the read failed rather than showing an empty card', async () => {
    listIssueOutputs.mockRejectedValueOnce(new Error('boom'));
    render(<OutputsBlockView ctx={ctx()} />);
    const err = await screen.findByTestId('outputs-error');
    expect(err.textContent).toContain('Could not read');
    expect(screen.queryByTestId('outputs-row')).toBeNull();
  });

  it('opens the version dialog on the object that was clicked', async () => {
    render(<OutputsBlockView ctx={ctx()} />);
    const rows = await screen.findAllByTestId('outputs-row');
    fireEvent.click(rows[0]);
    const dialog = await screen.findByTestId('output-diff');
    expect(dialog.getAttribute('data-kind')).toBe('script_shot');
    expect(dialog.getAttribute('data-ref')).toBe('9');
    expect(dialog.getAttribute('data-to')).toBe('3');
  });

  it('reads the issue id straight from the issue, not from the rollup', async () => {
    render(<OutputsBlockView ctx={ctx()} />);
    await waitFor(() => expect(listIssueOutputs).toHaveBeenCalledWith(ISSUE_ID));
  });
});

describe('outputsBlock registration', () => {
  it('sits in the context rail under an id of its own', () => {
    // NOT `deliverables`: that id belongs to the project-stage folder block and
    // `registerIssueBlock` throws on a duplicate.
    expect(outputsBlock.id).toBe('outputs');
    expect(outputsBlock.zone).toBe('context');
    expect(outputsBlock.order).toBe(25);
    expect(outputsBlock.match(ctx())).toBe(true);
  });

  it('is registered alongside the existing deliverables block, both ids intact', async () => {
    const { BUILTIN_ISSUE_BLOCKS } = await import('./index');
    const ids = BUILTIN_ISSUE_BLOCKS.map((b) => b.id);
    expect(ids).toContain('outputs');
    expect(ids).toContain('deliverables');
    expect(new Set(ids).size).toBe(ids.length);
  });
});
