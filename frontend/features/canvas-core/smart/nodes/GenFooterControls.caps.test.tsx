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
import en from '../../../../public/locales/en.json';

// Resolve `t` against the REAL shipped English copy rather than a hand-written
// table, so the title assertion below still fails if the key is renamed or
// dropped from en.json instead of silently passing on a bare key.
vi.mock('react-i18next', () => {
  const t = (key: string, vars?: Record<string, unknown>): string => {
    const template = key
      .split('.')
      .reduce<unknown>((node, part) => (node as Record<string, unknown>)?.[part], en);
    if (typeof template !== 'string') return key;
    return template.replace(/\{\{(\w+)\}\}/g, (_m, name) => String(vars?.[name] ?? ''));
  };
  return { useTranslation: () => ({ t }) };
});

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
  // The router emits the ordered list beside the boolean; a provider that
  // honours no tier sends `[]`, never a missing key.
  quality_tiers: [],
  resolution: false,
  max_refs: 0,
  negative: false,
  video_modes: [],
};

/** CodexLocalProtocol.capabilities, verbatim: all eight ratios and
 *  `quality: true`. P1 declared `false` because the daemon's argv never
 *  appended `--quality`; P3 flipped it back once daemon 0.4.0 forwards the
 *  knob and `MIN_IMAGE_DAEMON_VERSION` refuses older daemons outright, so
 *  the pill is no longer a fake switch. `quality: false` still hides the
 *  pill — ARK covers that rule below. */
const CODEX_LOCAL: ModelCapabilities = {
  ratios: ['21:9', '16:9', '3:2', '4:3', '1:1', '3:4', '2:3', '9:16'],
  quality: true,
  // LEGACY_QUALITY_TIERS, in the router's low→max order.
  quality_tiers: ['low', 'medium', 'high'],
  resolution: false,
  max_refs: 9,
  negative: false,
  video_modes: [],
};

/** OpenAIImagesProtocol.capabilities, verbatim: the gpt-image-2.5 rows are
 *  the only ones that reach `xhigh` / `max`, and the only ones that honour
 *  an exact --size. IMAGE_25_QUALITY_TIERS in the router's low→max order. */
const OPENAI_25: ModelCapabilities = {
  ratios: ['21:9', '16:9', '3:2', '4:3', '1:1', '3:4', '2:3', '9:16'],
  quality: true,
  quality_tiers: ['low', 'medium', 'high', 'xhigh', 'max'],
  resolution: true,
  max_refs: 9,
  negative: false,
  video_modes: [],
};

const MODELS = [
  { name: 'doubao-seedream', display_name: 'Seedream' },
  { name: 'codex-local-image', display_name: 'GPT Image 2 (local)' },
  { name: 'openai-image-flare', display_name: 'GPT Image 2.5 Flare (OpenAI API)' },
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

const qualityTexts = () =>
  screen.getAllByTestId('quality-option').map((b) => b.textContent ?? '');

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

  it('codex-local: all eight ratios stay, and so does the quality pill', () => {
    caps = CODEX_LOCAL;
    renderBar({ model: 'codex-local-image' });
    expect(screen.getByTestId('pill-quality')).toBeInTheDocument();

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

/**
 * The quality ramp is per-model, not per-app.
 *
 * `quality: true` only says the pill exists; WHICH rungs it offers is
 * `quality_tiers`. Offering `Max` on a codex row is the same fake switch as
 * offering a ratio the provider drops — the backend refuses the value and
 * the run comes back at some other tier without saying so.
 */
describe('GenFooterControls quality tiers follow the model', () => {
  it('offers only the tiers the model declares', () => {
    caps = CODEX_LOCAL;
    renderBar({ model: 'codex-local-image' });
    fireEvent.click(screen.getByTestId('pill-quality'));
    // Auto always leads: "let the provider pick" is a frontend concept the
    // backend never lists, exactly like the ratio grid's `auto` row.
    expect(qualityTexts()).toEqual(['Auto', 'Low', 'Medium', 'High']);
  });

  it('offers xhigh and max on a gpt-image-2.5 row', () => {
    caps = OPENAI_25;
    renderBar({ model: 'openai-image-flare' });
    fireEvent.click(screen.getByTestId('pill-quality'));
    expect(qualityTexts()).toEqual([
      'Auto',
      'Low',
      'Medium',
      'High',
      'Extra High',
      'Max',
    ]);
  });

  it('caps unknown (null): every tier stays', () => {
    caps = null;
    renderBar();
    fireEvent.click(screen.getByTestId('pill-quality'));
    expect(qualityTexts()).toContain('Max');
    expect(qualityTexts()).toContain('Extra High');
  });

  it('a backend too old to send quality_tiers keeps every tier', () => {
    // The two halves deploy independently (Cloudflare Pages vs gpupc, no
    // ordering guarantee), so a caps row WITHOUT the key is a real wire
    // shape for as long as that window lasts. Reading it as "supports no
    // tier" would strip working rungs; worse, a bare `.includes` on the
    // missing field throws and takes the whole footer down.
    const { quality_tiers: _dropped, ...legacy } = CODEX_LOCAL;
    caps = legacy as ModelCapabilities;
    renderBar({ model: 'codex-local-image' });
    fireEvent.click(screen.getByTestId('pill-quality'));
    expect(qualityTexts()).toContain('Max');
  });

  it('a stored quality the model no longer offers reads as Auto', () => {
    // Unlike the ratio, this one is NOT marked and kept: `request.py` drops a
    // quality outside `caps.quality_tiers` before dispatch, so the run really
    // will be Auto. Showing "Max" would promise a tier nobody will honour.
    caps = CODEX_LOCAL;
    renderBar({ model: 'codex-local-image', quality: 'max' });
    expect(screen.getByTestId('pill-quality').textContent).toBe('Auto');
  });
});
