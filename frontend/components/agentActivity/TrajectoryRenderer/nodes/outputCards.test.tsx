/**
 * harness 3a §5 — the output cards inside a step.
 *
 * A first registration and a revision have to read differently at a glance:
 * "the agent made something" vs "the agent replaced what it made", and the
 * second one has to say what it replaced. Spend of 0 reads `—`, never
 * `¢0.000` — no generated-media call site prices its rows, and a fabricated
 * zero says the work was free (the same rule fmtChildCents writes down).
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { OutputCard, StepNode } from '../foldEvents';
import { OutputCards } from './builtins';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown, opts?: unknown) => {
      const template = typeof fallback === 'string' ? fallback : key;
      const vars = (typeof fallback === 'object' ? fallback : opts) as Record<string, unknown> | undefined;
      return vars ? template.replace(/\{\{(\w+)\}\}/g, (_m, k: string) => String(vars[k] ?? `{{${k}}}`)) : template;
    },
  }),
}));
vi.mock('../../../Todolist/OutputDiffDialog', () => ({
  OutputDiffDialog: ({ kind, refId, initialTo, initialFrom }: { kind: string; refId: string; initialTo?: number; initialFrom?: number }) => (
    <div data-testid="output-diff" data-kind={kind} data-ref={refId} data-to={String(initialTo ?? '')} data-from={String(initialFrom ?? '')} />
  ),
}));

const card = (over: Partial<OutputCard> = {}): OutputCard => ({
  key: 'generated_media:77:1',
  kind: 'generated_media',
  refId: '77',
  version: 1,
  parentVersion: null,
  title: 'S3 · Shot #1',
  model: 'qwen-image',
  costCents: null,
  ...over,
});

const step = (outputs: OutputCard[]): StepNode => ({
  kind: 'step', key: 'step:1:1', turn: 1, step: 1, live: false, model: 'm', startedAt: null,
  lines: [], summary: { tools: 0, retries: 0, compactions: 0, outputs: 0, todo: null, durationMs: null, costCents: null, finishReason: null },
  children: [], outputs,
});

afterEach(cleanup);

describe('OutputCards', () => {
  it('draws nothing when the step registered nothing', () => {
    const { container } = render(<OutputCards node={step([])} />);
    expect(container.innerHTML).toBe('');
  });

  it('a first registration reads as new, with its title and model', () => {
    render(<OutputCards node={step([card()])} />);
    const el = screen.getByTestId('output-card');
    expect(el.getAttribute('data-state')).toBe('new');
    expect(el.className).toContain('border-ok-line');
    expect(el.textContent).toContain('S3 · Shot #1');
    expect(el.textContent).toContain('qwen-image');
  });

  it('a revision says which version it replaced and is toned as a change', () => {
    render(<OutputCards node={step([card({ key: 'script_shot:9:3', kind: 'script_shot', refId: '9', version: 3, parentVersion: 2, title: 'Shot 4', costCents: 0.42 })])} />);
    const el = screen.getByTestId('output-card');
    expect(el.getAttribute('data-state')).toBe('revised');
    expect(el.className).toContain('border-warn-line');
    expect(el.textContent).toContain('v3 ← v2');
    expect(el.textContent).toContain('¢0.420');
  });

  it('unpriced work reads — rather than a fabricated zero', () => {
    render(<OutputCards node={step([card({ costCents: 0 }), card({ key: 'generated_media:78:1', refId: '78', costCents: null })])} />);
    const cards = screen.getAllByTestId('output-card');
    expect(cards[0].textContent).toContain('—');
    expect(cards[0].textContent).not.toContain('¢0');
    expect(cards[1].textContent).toContain('—');
  });

  it('Open shows that version on its own; only a revision offers Diff', () => {
    render(<OutputCards node={step([card(), card({ key: 'script_shot:9:2', kind: 'script_shot', refId: '9', version: 2, parentVersion: 1 })])} />);
    expect(screen.getAllByTestId('output-open')).toHaveLength(2);
    const diffs = screen.getAllByTestId('output-diff-open');
    expect(diffs).toHaveLength(1);

    fireEvent.click(screen.getAllByTestId('output-open')[0]);
    const single = screen.getByTestId('output-diff');
    expect(single.getAttribute('data-ref')).toBe('77');
    expect(single.getAttribute('data-to')).toBe('1');
    expect(single.getAttribute('data-from')).toBe('1');
  });

  it('Diff opens the revision against the version it replaced', () => {
    render(<OutputCards node={step([card({ key: 'script_shot:9:2', kind: 'script_shot', refId: '9', version: 2, parentVersion: 1 })])} />);
    fireEvent.click(screen.getByTestId('output-diff-open'));
    const dialog = screen.getByTestId('output-diff');
    expect(dialog.getAttribute('data-kind')).toBe('script_shot');
    expect(dialog.getAttribute('data-to')).toBe('2');
    expect(dialog.getAttribute('data-from')).toBe('');
  });
});
