import { describe, it, expect } from 'vitest';

import {
  isPlatformModelAvailable,
  platformModelAvailability,
  platformOptionAttrs,
  platformModelLabel,
  platformModelText,
} from './platformModel';

describe('platformModelLabel — same string as the admin AI Models card', () => {
  it('leads with actual_model and keeps display_name as secondary text', () => {
    const m = {
      name: 'nous-doubao-embedding-vision',
      display_name: 'Doubao Embedding (Vision)',
      actual_model: 'doubao-embedding-vision-251215',
    };
    expect(platformModelLabel(m)).toEqual({
      primary: 'doubao-embedding-vision-251215',
      secondary: 'Doubao Embedding (Vision)',
    });
    expect(platformModelText(m)).toBe(
      'doubao-embedding-vision-251215 · Doubao Embedding (Vision)',
    );
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
      expect(platformModelLabel(m)).toEqual({
        primary: 'jimeng-local-image',
        secondary: 'Dreamina Image (Local)',
      });
    }
  });

  it('does not repeat display_name when it equals the primary label', () => {
    const m = { name: 'Codex (Local)', display_name: 'Codex (Local)', actual_model: '' };
    expect(platformModelLabel(m)).toEqual({ primary: 'Codex (Local)', secondary: null });
    expect(platformModelText(m)).toBe('Codex (Local)');
  });

  it('drops an empty display_name', () => {
    const m = { name: 'x', display_name: '', actual_model: 'model-x' };
    expect(platformModelLabel(m)).toEqual({ primary: 'model-x', secondary: null });
  });
});

describe('isPlatformModelAvailable', () => {
  it('is false only for a failed probe', () => {
    expect(isPlatformModelAvailable({ last_test_status: 'fail' })).toBe(false);
    expect(isPlatformModelAvailable({ last_test_status: 'ok' })).toBe(true);
    // Not probed is not a verdict in either direction.
    expect(isPlatformModelAvailable({ last_test_status: 'not_probed' })).toBe(true);
    expect(isPlatformModelAvailable({ last_test_status: null })).toBe(true);
    expect(isPlatformModelAvailable({})).toBe(true);
  });
});

describe('platformModelAvailability — idle rows stay visible but cannot be picked', () => {
  it('ok, not_probed and never-probed rows are selectable', () => {
    for (const status of ['ok', 'not_probed', null, undefined] as const) {
      expect(platformModelAvailability({ last_test_status: status })).toEqual({
        selectable: true,
      });
    }
    expect(platformModelAvailability({})).toEqual({ selectable: true });
  });

  it('idle (authorized on nous-engine, not loaded) is not selectable', () => {
    expect(platformModelAvailability({ last_test_status: 'idle' })).toEqual({
      selectable: false,
      reason: 'not_loaded',
    });
  });

  it('fail is left to isPlatformModelAvailable (hidden upstream), not greyed here', () => {
    expect(platformModelAvailability({ last_test_status: 'fail' })).toEqual({
      selectable: true,
    });
    expect(isPlatformModelAvailable({ last_test_status: 'fail' })).toBe(false);
  });
});

describe('platformOptionAttrs — the <option> props a UiSelect needs', () => {
  it('disables an idle row and carries the caller-localized reason', () => {
    expect(platformOptionAttrs({ last_test_status: 'idle' }, 'Not loaded on nous-engine')).toEqual({
      disabled: true,
      'data-availability': 'not_loaded',
      'data-description': 'Not loaded on nous-engine',
    });
  });

  it('leaves a selectable row untouched', () => {
    expect(platformOptionAttrs({ last_test_status: 'ok' }, 'x')).toEqual({ disabled: false });
  });
});
