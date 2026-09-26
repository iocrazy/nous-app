import { describe, it, expect } from 'vitest';

import { ENGINE_DOWN, baseAISettings, withPlatform } from '../tests/fixtures/platform';

import {
  platformModelAvailability,
  platformModelRows,
  platformOptionAttrs,
  platformModelLabel,
  platformModelText,
} from './platformModel';

describe('platformModelLabel — same string as the admin AI Models card', () => {
  it('is exactly actual_model; display_name is never shown on the user side', () => {
    const m = {
      name: 'nous-doubao-embedding-vision',
      display_name: 'Doubao Embedding (Vision)',
      actual_model: 'doubao-embedding-vision-251215',
    };
    expect(platformModelLabel(m)).toEqual({ primary: 'doubao-embedding-vision-251215' });
    expect(platformModelText(m)).toBe('doubao-embedding-vision-251215');
  });

  it('keeps a display_name that differs from the admin identifier out of the text entirely', () => {
    // The display names were authored by migrations, not by the admin, and the
    // admin page does not show them — so they must not leak into any label.
    const m = {
      name: 'nous-qwen3-asr',
      display_name: 'Qwen3 ASR (Flash)',
      actual_model: 'qwen3-asr-flash',
    };
    const text = platformModelText(m);
    expect(text).toBe('qwen3-asr-flash');
    expect(text).not.toContain('Qwen3 ASR (Flash)');
    expect(text).not.toContain(' · ');
    expect(Object.keys(platformModelLabel(m))).toEqual(['primary']);
  });

  it('falls back to the row name when actual_model is empty, as admin does', () => {
    // jimeng-local rows carry actual_model '' in production: the dreamina CLI
    // has no model selector, and admin prints the row name in that case.
    for (const actual of ['', '   ', null, undefined]) {
      const m = {
        name: 'jimeng-local-image',
        display_name: 'Dreamina Image (Local)',
        actual_model: actual,
      };
      expect(platformModelLabel(m)).toEqual({ primary: 'jimeng-local-image' });
      expect(platformModelText(m)).toBe('jimeng-local-image');
    }
  });

  it('is the row name even when display_name equals it', () => {
    const m = { name: 'Codex (Local)', display_name: 'Codex (Local)', actual_model: '' };
    expect(platformModelLabel(m)).toEqual({ primary: 'Codex (Local)' });
    expect(platformModelText(m)).toBe('Codex (Local)');
  });

  it('trims actual_model and ignores an empty display_name', () => {
    const m = { name: 'x', display_name: '', actual_model: '  model-x ' };
    expect(platformModelLabel(m)).toEqual({ primary: 'model-x' });
    expect(platformModelText(m)).toBe('model-x');
  });
});

describe('platformModelAvailability — idle rows stay visible but cannot be picked', () => {
  it('ok, not_probed and unknown are selectable — no signal is not a verdict', () => {
    for (const status of ['ok', 'not_probed', null, undefined] as const) {
      expect(platformModelAvailability(status)).toEqual({ selectable: true });
    }
  });

  it('idle (authorized on nous-engine, not loaded) is not selectable', () => {
    expect(platformModelAvailability('idle')).toEqual({
      selectable: false,
      reason: 'not_loaded',
    });
  });
});

describe('platformOptionAttrs — the <option> props a UiSelect needs', () => {
  it('disables an idle row and carries the caller-localized reason', () => {
    expect(platformOptionAttrs('idle', 'Not loaded on nous-engine')).toEqual({
      disabled: true,
      'data-availability': 'not_loaded',
      'data-description': 'Not loaded on nous-engine',
    });
  });

  it('leaves a selectable row untouched', () => {
    expect(platformOptionAttrs('ok', 'x')).toEqual({ disabled: false });
  });
});

describe('platformModelRows — the one mapping every picker uses', () => {
  const ROWS = [
    { name: 'nous-doubao', actual_model: 'doubao-seed-2-0-pro', disabled: true },
    { name: 'nous-qwen3-8b', actual_model: 'qwen3-8b' },
    { name: 'nous-wemm-2b', actual_model: 'wemm-2b', type: 'embedding' as const, status: 'idle' as const },
    { name: 'codex-local-image', actual_model: 'gpt-image-2', type: 'image' as const, is_local: true },
  ];

  it('enabled scope = enabled_models in server order, each with its mapping', () => {
    const rows = platformModelRows(withPlatform(baseAISettings(), ROWS));
    expect(rows.map((r) => r.name)).toEqual(['nous-qwen3-8b', 'nous-wemm-2b', 'codex-local-image']);
    expect(rows[1]).toMatchObject({ name: 'nous-wemm-2b', type: 'embedding', status: 'idle' });
  });

  it('listed scope keeps the rows the user switched off (the card re-enables them)', () => {
    const rows = platformModelRows(withPlatform(baseAISettings(), ROWS), { scope: 'listed' });
    expect(rows.map((r) => r.name)).toEqual([
      'nous-doubao',
      'nous-qwen3-8b',
      'nous-wemm-2b',
      'codex-local-image',
    ]);
  });

  it('filters by type', () => {
    const rows = platformModelRows(withPlatform(baseAISettings(), ROWS), { types: ['image'] });
    expect(rows.map((r) => r.name)).toEqual(['codex-local-image']);
  });

  it('offers nothing when the card is switched off, but still lists for the card', () => {
    const settings = withPlatform(baseAISettings(), ROWS, { enabled: false });
    expect(platformModelRows(settings)).toEqual([]);
    expect(platformModelRows(settings, { scope: 'listed' })).toHaveLength(4);
  });

  it('an unreachable engine changes nothing about the list (could not reach ≠ revoked)', () => {
    const settings = withPlatform(baseAISettings(), ROWS, { engine: ENGINE_DOWN });
    expect(platformModelRows(settings)).toHaveLength(3);
  });

  it('platform_models null (unknown) or absent (not loaded) → no rows, nothing guessed', () => {
    const settings = withPlatform(baseAISettings(), ROWS);
    expect(platformModelRows({ ...settings, platform_models: null })).toEqual([]);
    expect(platformModelRows({ ...settings, platform_models: undefined })).toEqual([]);
    expect(platformModelRows(null)).toEqual([]);
  });

  it('skips a name the mapping does not carry rather than showing a bare id', () => {
    const settings = withPlatform(baseAISettings(), ROWS);
    const nous = { ...settings.providers.nous!, enabled_models: ['ghost', 'nous-qwen3-8b'] };
    const rows = platformModelRows({ ...settings, providers: { nous } });
    expect(rows.map((r) => r.name)).toEqual(['nous-qwen3-8b']);
  });
});
