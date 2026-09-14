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
const revertOutput = vi.fn();
const invalidateOutputLineage = vi.fn();
vi.mock('../../services/outputsService', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../services/outputsService')>();
  return {
    ...mod,
    getOutputLineage: (...a: unknown[]) => getOutputLineage(...a),
    getOutputDiff: (...a: unknown[]) => getOutputDiff(...a),
    revertOutput: (...a: unknown[]) => revertOutput(...a),
    invalidateOutputLineage: (...a: unknown[]) => invalidateOutputLineage(...a),
  };
});
// The dialog also mounts from the CANVAS, where no ToastProvider is above it —
// hence `useOptionalToast`. Mocking it keeps the assertion on the sentence
// rather than on a DOM node some other provider owns.
const addToast = vi.fn();
vi.mock('../Toast', () => ({ useOptionalToast: () => ({ addToast }) }));

// Spied, not stubbed: the real store dedupes by watermark, and asserting
// through it would be re-asserting the store's own rules.
const notifyTurn = vi.fn();
vi.mock('./issueTurnSignal', async (importOriginal) => {
  const mod = await importOriginal<typeof import('./issueTurnSignal')>();
  return { ...mod, notifyTurn: (...a: unknown[]) => notifyTurn(...(a as [string, never])) };
});
const { __resetTurnSignals } = await import('./issueTurnSignal');

const v = (version: number, parent: number | null): OutputLineage['versions'][number] => ({
  id: `d${version}`, version, parent_version: parent, run_id: '347786145852700', issue_id: '5',
  actor_user_id: null, reverted_from_version: null, cost_kind: 'allocated',
  issue_key: 'MH-91', deep_link: `/team/424242424242/todolist/MH-91?step=${version}`,
  seq: version, turn: 1, step: version, title: `Shot #1 v${version}`, model: 'qwen-max',
  cost_cents: 0.42, created_at: '2026-09-10T01:00:00Z',
});

// `as_of_seq` is a Snowflake id and therefore a STRING on the wire — a number
// here would be a fixture describing a response the backend does not send.
const lineage: OutputLineage = { kind: 'script_shot', ref_id: '9', latest_version: 2, versions: [v(2, 1), v(1, null)], as_of_seq: '347786145852741' };

const side = (version: number, text: string | null, extra: Partial<OutputDiff['from']> = {}): OutputDiff['from'] => ({
  version, run_id: '347786145852739', issue_id: '5', created_at: '2026-09-10T01:00:00Z', model: 'qwen-max',
  cost_cents: 0.42, title: `Shot #1 v${version}`, text, media: null, available: true, unavailable_reason: null, ...extra,
});

afterEach(cleanup);
beforeEach(() => {
  revertOutput.mockReset();
  invalidateOutputLineage.mockReset();
  addToast.mockReset();
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
    getOutputLineage.mockResolvedValueOnce({ kind: 'generated_media', ref_id: '77', latest_version: 1, versions: [v(1, null)], as_of_seq: '347786145852741' });
    getOutputDiff.mockResolvedValueOnce({
      kind: 'generated_media', ref_id: '77', content_type: 'text', from: side(1, 'only'), to: side(1, 'only'),
    } satisfies OutputDiff);
    render(<OutputDiffDialog kind="generated_media" refId="77" onClose={vi.fn()} />);
    await screen.findByTestId('output-diff-only');
    expect(getOutputDiff).toHaveBeenCalledWith('generated_media', '77', 1, 1);
    expect(screen.queryByTestId('output-diff-from')).toBeNull();
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

/**
 * 3b §3.4 — Revert.
 *
 * Three facts this suite is here to hold:
 *  - the button is offered only where a revert can actually happen (a text
 *    kind, an older version): a button that refuses on click teaches nothing;
 *  - the confirm sentence promises a NEW version and says nothing about kept
 *    edits, because the dialog cannot know they exist until the server answers;
 *  - a refusal names the fact the reader needs — WHICH version appeared, WHY a
 *    version cannot be rebuilt — which is what `OutputsError.details` carries.
 */
describe('OutputDiffDialog — revert (3b §3.4)', () => {
  const ACTOR = '6f1c1b64-2b3f-4a5e-9a10-1f2c3d4e5f60';

  const openTextDiff = () => render(<OutputDiffDialog kind="script_shot" refId="9" onClose={vi.fn()} />);

  it('offers Revert only for a script kind on a non-latest version', async () => {
    openTextDiff();                                   // from=1, to=2, latest=2
    await waitFor(() => expect((screen.getByTestId('output-diff-revert') as HTMLButtonElement).disabled).toBe(false));
  });

  it('never offers Revert for media', async () => {
    // Media has no ledger and no inverse batch to replay — 3b §6 defers it.
    getOutputLineage.mockResolvedValueOnce({ kind: 'generated_media', ref_id: '77', latest_version: 2, versions: [v(2, 1), v(1, null)], as_of_seq: '347786145852741' });
    getOutputDiff.mockResolvedValueOnce({
      kind: 'generated_media', ref_id: '77', content_type: 'media',
      from: { ...side(1, null), media: { id: '77', media_kind: 'image', mime: 'image/png', cover_url: 'https://cdn.example.com/a.png', stream_url: null } },
      to: { ...side(2, null), media: { id: '78', media_kind: 'image', mime: 'image/png', cover_url: 'https://cdn.example.com/b.png', stream_url: null } },
    } satisfies OutputDiff);
    render(<OutputDiffDialog kind="generated_media" refId="77" onClose={vi.fn()} />);
    const btn = await screen.findByTestId('output-diff-revert');
    expect((btn as HTMLButtonElement).disabled).toBe(true);
  });

  it('never offers Revert on the latest version', async () => {
    getOutputDiff.mockResolvedValueOnce({
      kind: 'script_shot', ref_id: '9', content_type: 'text', from: side(2, 'just this one'), to: side(2, 'just this one'),
    } satisfies OutputDiff);
    render(<OutputDiffDialog kind="script_shot" refId="9" initialTo={2} initialFrom={2} onClose={vi.fn()} />);
    const btn = await screen.findByTestId('output-diff-revert');
    expect((btn as HTMLButtonElement).disabled).toBe(true);
  });

  it('confirms before reverting and says what it will create', async () => {
    openTextDiff();
    fireEvent.click(await screen.findByTestId('output-diff-revert'));
    // 调用前无从知道有没有未登记编辑，所以确认句里不提 kept。
    expect(screen.getByTestId('output-revert-confirm').textContent).toContain('Revert to v1? This creates v3.');
    expect(revertOutput).not.toHaveBeenCalled();
  });

  it('reverts, shows the new version and says the edits were kept', async () => {
    openTextDiff();
    revertOutput.mockResolvedValue({
      version: { ...v(4, 3), reverted_from_version: 1, run_id: null, actor_user_id: ACTOR },
      kept_version: { ...v(3, 2), run_id: null, actor_user_id: ACTOR },
    });
    fireEvent.click(await screen.findByTestId('output-diff-revert'));
    fireEvent.click(screen.getByTestId('output-revert-go'));
    await waitFor(() => expect(revertOutput).toHaveBeenCalledWith('script_shot', '9', { toVersion: 1, expectedLatest: 2 }));
    expect(addToast).toHaveBeenCalledWith('Reverted to v1 as v4', 'success');
    await waitFor(() => expect(screen.getByTestId('output-reverted-chip').textContent).toContain('v4 ↩ v1'));
    expect(screen.getByTestId('output-revert-kept').textContent)
      .toContain('Your edits before the revert were kept as v3.');
    // Elsewhere (the provenance block, the right rail) must ask again.
    expect(invalidateOutputLineage).toHaveBeenCalledWith('script_shot', '9');
  });

  it('a conflict names the version that appeared', async () => {
    const { OutputsError } = await import('../../services/outputsService');
    openTextDiff();
    revertOutput.mockRejectedValue(new OutputsError('version_conflict', 409, 'c', { latest_version: 4 }));
    fireEvent.click(await screen.findByTestId('output-diff-revert'));
    fireEvent.click(screen.getByTestId('output-revert-go'));
    await waitFor(() => expect(addToast).toHaveBeenCalledWith(
      'Someone registered v4 meanwhile — reopen to see it', 'error'));
  });

  it('an unrebuildable version says so', async () => {
    const { OutputsError } = await import('../../services/outputsService');
    openTextDiff();
    revertOutput.mockRejectedValue(new OutputsError('content_unavailable', 409, '', { reason: 'no_ledger' }));
    fireEvent.click(await screen.findByTestId('output-diff-revert'));
    fireEvent.click(screen.getByTestId('output-revert-go'));
    await waitFor(() => expect(addToast).toHaveBeenCalledWith("v1 can't be rebuilt (no ledger)", 'error'));
  });

  it('shows each side cost with the ≈ its kind earns', async () => {
    openTextDiff();   // v() 的 cost_kind='allocated'、cost_cents=0.42
    await waitFor(() => expect(screen.getByTestId('output-diff-cost-to').textContent).toBe('≈¢0.42'));
    expect(screen.getByTestId('output-diff-cost-from').textContent).toBe('≈¢0.42');
  });

  it('says where an allocated price came from', async () => {
    // The `≈` says "approximate"; the title says WHY, which is the half a
    // reader needs before deciding whether to reconcile anything against it.
    openTextDiff();
    await waitFor(() => expect(screen.getByTestId('output-diff-cost-to').getAttribute('title'))
      .toBe('Allocated from step cost'));
    expect(screen.getByTestId('output-diff-cost-from').getAttribute('title')).toBe('Allocated from step cost');
  });

  it('an unpriced media side says which model has no price row', async () => {
    const unpriced = (n: number, parent: number | null) => ({
      ...v(n, parent), model: 'gpt-6-astra', cost_cents: null, cost_kind: null,
    });
    getOutputLineage.mockResolvedValueOnce({
      kind: 'generated_media', ref_id: '77', latest_version: 2,
      versions: [unpriced(2, 1), unpriced(1, null)], as_of_seq: '347786145852741',
    });
    getOutputDiff.mockResolvedValueOnce({
      kind: 'generated_media', ref_id: '77', content_type: 'media',
      from: { ...side(1, null), media: { id: '77', media_kind: 'image', mime: 'image/png', cover_url: 'https://cdn.example.com/a.png', stream_url: null } },
      to: { ...side(2, null), media: { id: '78', media_kind: 'image', mime: 'image/png', cover_url: 'https://cdn.example.com/b.png', stream_url: null } },
    } satisfies OutputDiff);
    render(<OutputDiffDialog kind="generated_media" refId="77" onClose={vi.fn()} />);
    const cost = await screen.findByTestId('output-diff-cost-to');
    expect([cost.textContent, cost.getAttribute('title')])
      .toEqual(['—', 'No price configured for gpt-6-astra']);
  });

  it('hands focus to the confirm button, so Enter answers the question just asked', async () => {
    openTextDiff();
    fireEvent.click(await screen.findByTestId('output-diff-revert'));
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId('output-revert-go')));
  });

  it('drops the kept note when the reader moves to another version', async () => {
    // It answers "what happened to my edits during THAT revert" — carrying it
    // onto a version the revert never touched makes it a claim about the wrong
    // object.
    openTextDiff();
    revertOutput.mockResolvedValue({
      version: { ...v(4, 3), reverted_from_version: 1, run_id: null, actor_user_id: ACTOR },
      kept_version: { ...v(3, 2), run_id: null, actor_user_id: ACTOR },
    });
    fireEvent.click(await screen.findByTestId('output-diff-revert'));
    fireEvent.click(screen.getByTestId('output-revert-go'));
    await screen.findByTestId('output-revert-kept');
    fireEvent.click(screen.getByTestId('output-diff-version-1'));
    await waitFor(() => expect(screen.queryByTestId('output-revert-kept')).toBeNull());
  });
});

/**
 * harness 3b Task 6 — a revert is a turn as far as the page is concerned.
 *
 * The chain it belongs to names the issue; the dialog is also opened from the
 * canvas, where there is no issue at all and nothing to refresh.
 */
describe('OutputDiffDialog — a revert announces itself (3b Task 6)', () => {
  const ACTOR = '6f1c1b64-2b3f-4a5e-9a10-1f2c3d4e5f60';
  const openTextDiff = () => render(<OutputDiffDialog kind="script_shot" refId="9" onClose={vi.fn()} />);

  beforeEach(() => {
    __resetTurnSignals();
    notifyTurn.mockReset();
  });

  const revertOnce = async (over: Partial<OutputLineage['versions'][number]> = {}) => {
    openTextDiff();
    revertOutput.mockResolvedValue({
      version: { ...v(4, 3), id: '347786145852739099', reverted_from_version: 1, run_id: null, actor_user_id: ACTOR, ...over },
      kept_version: null,
    });
    fireEvent.click(await screen.findByTestId('output-diff-revert'));
    fireEvent.click(screen.getByTestId('output-revert-go'));
    await waitFor(() => expect(revertOutput).toHaveBeenCalled());
  };

  it('signals the issue the chain belongs to, on the local lane', async () => {
    // `runId: null` matters: a transcript seq and a local one are different
    // counters, and sharing a watermark would let either silence the other.
    await revertOnce();
    await waitFor(() => expect(notifyTurn).toHaveBeenCalledTimes(1));
    const [issue, signal] = notifyTurn.mock.calls[0] as [string, { runId: null; seq: number }];
    expect(issue).toBe('5');
    expect(signal.runId).toBeNull();
    expect(signal.seq).toBeGreaterThan(0);
  });

  it('two reverts in the same millisecond both get through', async () => {
    // The seq must NOT be the new version's id: that is a Snowflake, and
    // `Number()` rounds it past 2^53, so two reverts minted in one millisecond
    // collapse to the same value and the watermark drops the second as a
    // replay — the page would sit on the first revert's result.
    const { unmount } = render(<OutputDiffDialog kind="script_shot" refId="9" onClose={vi.fn()} />);
    unmount();
    await revertOnce();
    await waitFor(() => expect(notifyTurn).toHaveBeenCalledTimes(1));
    cleanup();
    await revertOnce();
    await waitFor(() => expect(notifyTurn).toHaveBeenCalledTimes(2));

    const seqs = notifyTurn.mock.calls.map((c) => (c[1] as { seq: number }).seq);
    expect(seqs[1]).toBeGreaterThan(seqs[0]);
  });

  it('says nothing when no version on the chain answers to an issue', async () => {
    // Opened from the canvas: nothing to refresh, so nothing is announced.
    getOutputLineage.mockResolvedValue({
      ...lineage,
      versions: [{ ...v(2, 1), issue_id: null }, { ...v(1, null), issue_id: null }],
    });
    await revertOnce({ issue_id: null });
    expect(notifyTurn).not.toHaveBeenCalled();
  });
});
