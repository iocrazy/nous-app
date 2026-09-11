/**
 * harness 3a §5 — the version dialog.
 *
 * The load-bearing rule: a side the backend could not reconstruct
 * (`available:false`) is drawn as a labelled placeholder. Drawing it as an
 * empty pane would tell the reader their version was blank, which is the
 * opposite of what happened.
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ChildRunContext, type ChildRunState } from './childRunContext';
import { OutputDiffDialog } from './OutputDiffDialog';
import type { OutputDiff, OutputLineage } from '../../services/outputsService';

vi.mock('../../utils/apiConfig', () => ({ getApiUrl: () => 'http://api.test' }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown, vars?: Record<string, unknown>) => {
      const tpl = typeof fallback === 'string' ? fallback : key;
      const v = (typeof fallback === 'object' && fallback ? fallback : vars) as Record<string, unknown> | undefined;
      return tpl.replace(/\{\{(\w+)\}\}/g, (_m, n: string) => String(v?.[n] ?? `{{${n}}}`));
    },
  }),
}));

const getOutputLineage = vi.fn();
const getOutputDiff = vi.fn();
vi.mock('../../services/outputsService', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../services/outputsService')>();
  return {
    ...mod,
    getOutputLineage: (...a: unknown[]) => getOutputLineage(...a),
    getOutputDiff: (...a: unknown[]) => getOutputDiff(...a),
  };
});

const v = (version: number, parent: number | null): OutputLineage['versions'][number] => ({
  id: `d${version}`, version, parent_version: parent, run_id: '347786145852700', issue_id: '5',
  issue_key: 'MH-91', deep_link: `/team/424242424242/todolist/MH-91?step=${version}`,
  seq: version, turn: 1, step: version, title: `Shot #1 v${version}`, model: 'qwen-max',
  cost_cents: 0.42, created_at: '2026-09-10T01:00:00Z',
});

const lineage: OutputLineage = { kind: 'script_shot', ref_id: '9', latest_version: 2, versions: [v(2, 1), v(1, null)] };

const side = (version: number, text: string | null, extra: Partial<OutputDiff['from']> = {}): OutputDiff['from'] => ({
  version, run_id: '347786145852739', issue_id: '5', created_at: '2026-09-10T01:00:00Z', model: 'qwen-max',
  cost_cents: 0.42, title: `Shot #1 v${version}`, text, media: null, available: true, unavailable_reason: null, ...extra,
});

afterEach(cleanup);
beforeEach(() => {
  getOutputLineage.mockReset().mockResolvedValue(lineage);
  getOutputDiff.mockReset().mockResolvedValue({
    kind: 'script_shot', ref_id: '9', content_type: 'text',
    from: side(1, 'the quick brown fox'), to: side(2, 'the quick red fox'),
  } satisfies OutputDiff);
});

describe('OutputDiffDialog', () => {
  it('opens on the latest change and colours what moved', async () => {
    render(<OutputDiffDialog kind="script_shot" refId="9" onClose={vi.fn()} />);
    await screen.findByTestId('output-diff');
    await waitFor(() => expect(getOutputDiff).toHaveBeenCalledWith('script_shot', '9', 1, 2));
    const from = screen.getByTestId('output-diff-from');
    const to = screen.getByTestId('output-diff-to');
    expect(from.textContent).toContain('brown');
    expect(from.textContent).not.toContain('red');
    expect(to.textContent).toContain('red');
    expect(from.querySelector('[data-diff="del"]')?.textContent).toBe('brown');
    expect(to.querySelector('[data-diff="add"]')?.textContent).toBe('red');
  });

  it('says a side could not be reconstructed instead of showing it empty', async () => {
    getOutputDiff.mockResolvedValueOnce({
      kind: 'script_shot', ref_id: '9', content_type: 'text',
      from: side(1, null, { available: false, unavailable_reason: 'no_snapshot' }),
      to: side(2, 'the quick red fox'),
    } satisfies OutputDiff);
    render(<OutputDiffDialog kind="script_shot" refId="9" onClose={vi.fn()} />);
    const note = await screen.findByTestId('output-diff-unavailable');
    expect(note.textContent).toContain('No snapshot');
    expect(screen.getByTestId('output-diff-to').textContent).toContain('red');
  });

  it('draws both media sides as thumbnails', async () => {
    getOutputDiff.mockResolvedValueOnce({
      kind: 'generated_media', ref_id: '77', content_type: 'media',
      from: { ...side(1, null), media: { id: '77', media_kind: 'image', mime: 'image/png', cover_url: '/api/v1/generated-media/500/cover', stream_url: null } },
      to: { ...side(2, null), media: { id: '78', media_kind: 'image', mime: 'image/png', cover_url: '/api/v1/generated-media/501/cover', stream_url: null } },
    } satisfies OutputDiff);
    render(<OutputDiffDialog kind="generated_media" refId="77" onClose={vi.fn()} />);
    const shots = await screen.findAllByTestId('output-diff-media');
    expect(shots).toHaveLength(2);
    // The wire is relative; a bare /api/... would hit the Pages origin and
    // come back as index.html, so the dialog resolves it against the API.
    expect(shots[0].getAttribute('src')).toBe('http://api.test/api/v1/generated-media/500/cover');
    expect(shots[1].getAttribute('src')).toBe('http://api.test/api/v1/generated-media/501/cover');
    expect(shots[0].getAttribute('src')?.startsWith('/')).toBe(false);
  });

  it('a single-version object shows that one version, and asks for no false pair', async () => {
    getOutputLineage.mockResolvedValueOnce({ kind: 'generated_media', ref_id: '77', latest_version: 1, versions: [v(1, null)] });
    getOutputDiff.mockResolvedValueOnce({
      kind: 'generated_media', ref_id: '77', content_type: 'text', from: side(1, 'only'), to: side(1, 'only'),
    } satisfies OutputDiff);
    render(<OutputDiffDialog kind="generated_media" refId="77" onClose={vi.fn()} />);
    await screen.findByTestId('output-diff-only');
    expect(getOutputDiff).toHaveBeenCalledWith('generated_media', '77', 1, 1);
    expect(screen.queryByTestId('output-diff-from')).toBeNull();
  });

  it('offers Revert disabled, and says when it arrives', async () => {
    render(<OutputDiffDialog kind="script_shot" refId="9" onClose={vi.fn()} />);
    const revert = await screen.findByTestId('output-diff-revert');
    expect((revert as HTMLButtonElement).disabled).toBe(true);
    expect(revert.getAttribute('title')).toContain('3b');
  });

  it('names a refusal by its typed code rather than showing an empty diff', async () => {
    const { OutputsError } = await import('../../services/outputsService');
    getOutputLineage.mockRejectedValueOnce(new OutputsError('not_registered', 404, 'not in the registry'));
    render(<OutputDiffDialog kind="script_shot" refId="9" onClose={vi.fn()} />);
    const err = await screen.findByTestId('output-diff-error');
    expect(err.textContent).toContain('not in the deliverable registry');
    expect(screen.queryByTestId('output-diff-from')).toBeNull();
  });

  it('closes on Escape and on the backdrop', async () => {
    const onClose = vi.fn();
    render(<OutputDiffDialog kind="script_shot" refId="9" onClose={onClose} />);
    await screen.findByTestId('output-diff');
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId('output-diff-backdrop'));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('switching version re-reads that pair', async () => {
    render(<OutputDiffDialog kind="script_shot" refId="9" onClose={vi.fn()} />);
    await screen.findByTestId('output-diff');
    await waitFor(() => expect(getOutputDiff).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByTestId('output-diff-version-1'));
    await waitFor(() => expect(getOutputDiff).toHaveBeenLastCalledWith('script_shot', '9', 1, 1));
  });
});

describe('OutputDiffDialog — one version on its own', () => {
  it('pinning both ends asks for that version alone, until another is picked', async () => {
    getOutputDiff.mockResolvedValueOnce({
      kind: 'script_shot', ref_id: '9', content_type: 'text', from: side(2, 'just this one'), to: side(2, 'just this one'),
    } satisfies OutputDiff);
    render(<OutputDiffDialog kind="script_shot" refId="9" initialTo={2} initialFrom={2} onClose={vi.fn()} />);
    await screen.findByTestId('output-diff-only');
    expect(getOutputDiff).toHaveBeenCalledWith('script_shot', '9', 2, 2);
    // picking a version drops the pin and goes back to comparing
    fireEvent.click(screen.getByTestId('output-diff-version-2'));
    await waitFor(() => expect(getOutputDiff).toHaveBeenLastCalledWith('script_shot', '9', 1, 2));
  });
});

describe('OutputDiffDialog — Open Run (修复轮 1, spec §5)', () => {
  const childRun = (open: () => void): ChildRunState => ({ current: null, open, close: vi.fn() });

  it('opens the run that produced the newer side, named by its last six digits', async () => {
    const open = vi.fn();
    render(
      <ChildRunContext.Provider value={childRun(open)}>
        <OutputDiffDialog kind="script_shot" refId="9" onClose={vi.fn()} />
      </ChildRunContext.Provider>,
    );
    const btn = await screen.findByTestId('output-open-run');
    expect((btn as HTMLButtonElement).disabled).toBe(false);
    expect(btn.textContent).toContain('852739');
    fireEvent.click(btn);
    expect(open).toHaveBeenCalledWith(expect.objectContaining({ childRunId: '347786145852739', step: 2 }));
  });

  it('is disabled with a reason when no run panel is mounted', async () => {
    render(<OutputDiffDialog kind="script_shot" refId="9" onClose={vi.fn()} />);
    const btn = await screen.findByTestId('output-open-run');
    expect((btn as HTMLButtonElement).disabled).toBe(true);
    expect(btn.getAttribute('title')).toBeTruthy();
  });
});

describe('OutputDiffDialog — media URLs (修复轮 2)', () => {
  it('passes an already-absolute cover through untouched, and falls back to the stream URL', async () => {
    getOutputDiff.mockResolvedValueOnce({
      kind: 'generated_media', ref_id: '77', content_type: 'media',
      from: { ...side(1, null), media: { id: '77', media_kind: 'image', mime: 'image/png', cover_url: 'https://cdn.example.com/a.png', stream_url: null } },
      to: { ...side(2, null), media: { id: '78', media_kind: 'video', mime: 'video/mp4', cover_url: null, stream_url: '/api/v1/generated-media/501/stream' } },
    } satisfies OutputDiff);
    render(<OutputDiffDialog kind="generated_media" refId="77" onClose={vi.fn()} />);
    const shots = await screen.findAllByTestId('output-diff-media');
    expect(shots[0].getAttribute('src')).toBe('https://cdn.example.com/a.png');
    expect(shots[1].getAttribute('src')).toBe('http://api.test/api/v1/generated-media/501/stream');
  });
});
