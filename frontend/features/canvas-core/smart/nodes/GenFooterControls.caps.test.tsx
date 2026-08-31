/**
 * The footer only offers what the model can honour.
 *
 * A pill for a knob the provider ignores is a fake switch — the user turns
 * it and nothing happens, silently. P1 made the backend drop-and-name those
 * knobs; this makes the UI stop offering them. Hiding, not disabling:
 * a greyed-out control still promises the capability exists.
 *
 * `caps === null` (unknown) must render EXACTLY today's full set — an old
 * backend serving a new frontend must not lose working controls.
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ModelCapabilities } from '../../services/canvasGenerationService';

/** What `useModelCapabilities` hands the footer for THIS case. */
let caps: ModelCapabilities | null = null;
vi.mock('./useModelCapabilities', () => ({
  useModelCapabilities: () => caps,
}));

import { GenFooterControls } from './GenFooterControls';

/** ArkProtocol.capabilities, verbatim: five ratios (ark_image's
 *  _ASPECT_TO_SIZE keys), no quality knob, no resolution ladder. */
const ARK: ModelCapabilities = {
  ratios: ['16:9', '9:16', '1:1', '4:3', '3:4'],
  quality: false,
  resolution: false,
  max_refs: 0,
  negative: false,
  video_modes: [],
};

/** CodexLocalProtocol.capabilities: all eight ratios, but `quality: false`
 *  — the daemon's argv never appends `--quality`, so P1 declared the honest
 *  value rather than the intended one. (Server-side CodexProtocol is
 *  `quality: True`; these are two different rows.) */
const CODEX_LOCAL: ModelCapabilities = {
  ratios: ['21:9', '16:9', '3:2', '4:3', '1:1', '3:4', '2:3', '9:16'],
  quality: false,
  resolution: false,
  max_refs: 9,
  negative: false,
  video_modes: [],
};

const MODELS = [
  { name: 'doubao-seedream', display_name: 'Seedream' },
  { name: 'codex-local-image', display_name: 'GPT Image 2 (local)' },
];

function renderBar(gen: Record<string, unknown> = {}) {
  const onChange = vi.fn();
  render(
    <GenFooterControls
      gen={{ kind: 'image', model: 'doubao-seedream', ratio: '1:1', count: 1, ...gen } as never}
      models={MODELS as never}
      onChange={onChange}
    />,
  );
  return onChange;
}

const ratioTexts = () =>
  screen.getAllByTestId('ratio-option').map((b) => b.textContent ?? '');

beforeEach(() => {
  caps = null;
});
afterEach(cleanup);

describe('GenFooterControls honours model capabilities', () => {
  it('ark: hides the two ratios it cannot produce, and the knobs it ignores', () => {
    caps = ARK;
    renderBar();
    // A knob ark drops server-side must not be offered client-side.
    expect(screen.queryByTestId('pill-quality')).toBeNull();

    fireEvent.click(screen.getByTestId('pill-size'));
    const texts = ratioTexts();
    expect(texts.some((t) => t.includes('21:9'))).toBe(false);
    expect(texts.some((t) => t.includes('3:2'))).toBe(false);
    expect(texts.some((t) => t.includes('16:9'))).toBe(true);
    // `auto` is a frontend concept the backend never lists — it survives the
    // filter, and stays first (a prompt follows its input by default).
    expect(texts[0]).toContain('Auto');
    expect(texts).toHaveLength(ARK.ratios.length + 1);
    // resolution: false — no ladder at all, not a greyed one.
    expect(screen.queryAllByTestId('resolution-option')).toHaveLength(0);
  });

  it('caps unknown (null): renders today\'s full set, losing nothing', () => {
    caps = null;
    renderBar();
    expect(screen.getByTestId('pill-quality')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('pill-size'));
    // Eight ratios + Auto, and the resolution ladder beside them.
    expect(ratioTexts()).toHaveLength(9);
    expect(ratioTexts().some((t) => t.includes('21:9'))).toBe(true);
    expect(screen.getAllByTestId('resolution-option')).toHaveLength(3);
  });

  it('codex-local: all eight ratios stay, but the fake quality pill goes', () => {
    caps = CODEX_LOCAL;
    renderBar({ model: 'codex-local-image' });
    expect(screen.queryByTestId('pill-quality')).toBeNull();

    fireEvent.click(screen.getByTestId('pill-size'));
    expect(ratioTexts()).toHaveLength(9);
    expect(ratioTexts().some((t) => t.includes('21:9'))).toBe(true);
  });

  it('the pill summary drops the resolution suffix a model has no knob for', () => {
    // The column is gone because ark's pixel size is the model's to pick —
    // then the summary must not keep asserting "1K" beside the ratio. Half a
    // fake switch is still a fake switch.
    caps = ARK;
    renderBar({ resolution: '2k' });
    expect(screen.getByTestId('pill-size').textContent).not.toContain('2K');
  });

  it('caps unknown: the resolution suffix stays exactly as today', () => {
    caps = null;
    renderBar({ resolution: '2k' });
    expect(screen.getByTestId('pill-size').textContent).toContain('2K');
  });

  it('marks a stored ratio the model no longer offers, without rewriting it', () => {
    // The node really does hold '21:9'; showing anything else would lie in
    // the other direction. So we keep the value and mark it — the user sees
    // that this pick will not be honoured before spending a run on it.
    caps = ARK;
    renderBar({ ratio: '21:9' });
    const shown = screen.getByTestId('pill-ratio');
    expect(shown.textContent).toBe('21:9');
    expect(shown.className).toContain('text-warn');
    expect(shown.getAttribute('title')).toBe('Not supported by this model');
  });

  it('leaves an offered ratio unmarked', () => {
    caps = ARK;
    renderBar({ ratio: '16:9' });
    const shown = screen.getByTestId('pill-ratio');
    expect(shown.className).not.toContain('text-warn');
    expect(shown.getAttribute('title')).toBeNull();
  });

  it('video: the aspect grid is the same grid, so it filters the same way', () => {
    // Image and video share ONE `ratio-option` block; only the source list
    // differs (FOOTER_RATIOS vs VIDEO_RATIOS), so one filter covers both.
    caps = ARK;
    renderBar({ kind: 'video', aspect: '16:9', ratio: undefined });

    fireEvent.click(screen.getByTestId('pill-size'));
    const texts = ratioTexts();
    expect(texts.some((t) => t.includes('21:9'))).toBe(false);
    expect(texts.some((t) => t.includes('16:9'))).toBe(true);
    // VIDEO_RATIOS' `auto` row is labelled Adaptive — still a frontend
    // concept, still kept.
    expect(texts.some((t) => t.includes('Adaptive'))).toBe(true);
  });
});
