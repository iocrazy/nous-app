import { describe, expect, it } from 'vitest';

import {
  INTERMEDIATE_OPTION,
  SINCE_PRESETS,
  SOURCE_OPTIONS,
  defaultGeneratedFilters,
  hasActiveFilters,
  listOptionsFor,
  parseFilters,
  serializeFilters,
  sinceIsoFor,
  toggleOriginKind,
  type GeneratedFilters,
} from './generatedFilters';

const NOW = new Date('2026-08-29T12:00:00.000Z');

describe('SOURCE_OPTIONS', () => {
  it('lists the six origin kinds the backend emits, each with an i18n key', () => {
    expect(SOURCE_OPTIONS.map((o) => o.kind)).toEqual([
      'canvas_run',
      'canvas_upload',
      'shot_generate',
      'shot_video',
      'agent_run',
      'chat_upload',
    ]);
    expect(SOURCE_OPTIONS.map((o) => o.labelKey)).toEqual([
      'generated.source.canvas_run',
      'generated.source.canvas_upload',
      'generated.source.shot_generate',
      'generated.source.shot_video',
      'generated.source.agent_run',
      'generated.source.chat_upload',
    ]);
  });
});

describe('parseFilters', () => {
  it('defaults to the unreviewed tab with no filters', () => {
    expect(parseFilters(new URLSearchParams())).toEqual(defaultGeneratedFilters());
    expect(parseFilters(new URLSearchParams()).state).toBe('unreviewed');
  });

  it('reads every supported param, repeated origin_kind included', () => {
    const sp = new URLSearchParams(
      'state=saved&origin_kind=canvas_run&origin_kind=agent_run' +
        '&project_id=325005725244722&media_kind=video&model=gpt-image-2&since=7d',
    );
    expect(parseFilters(sp)).toEqual({
      state: 'saved',
      originKinds: ['canvas_run', 'agent_run'],
      projectId: '325005725244722',
      mediaKind: 'video',
      model: 'gpt-image-2',
      since: '7d',
      includeIntermediate: false,
    });
  });

  it('rejects values outside each vocabulary instead of forwarding them', () => {
    // A hand-edited URL must not be able to send `state=deleted` (a real row
    // state, but NOT a tab) or an unknown origin kind to the router.
    const sp = new URLSearchParams(
      'state=deleted&origin_kind=canvas_run&origin_kind=bogus&media_kind=pdf&since=99y',
    );
    expect(parseFilters(sp)).toEqual({
      ...defaultGeneratedFilters(),
      originKinds: ['canvas_run'],
    });
  });

  it('treats blank strings as absent', () => {
    const sp = new URLSearchParams('project_id=&model=&origin_kind=');
    expect(parseFilters(sp)).toEqual(defaultGeneratedFilters());
  });

  // Both are real wire values: `audio` is what P6's My Uploads → As Asset
  // mints, `file` is a chat upload that is neither image nor video. Before
  // they were accepted here, a shared `?media_kind=audio` link silently
  // dropped the filter and showed everything.
  it.each(['audio', 'file'] as const)('accepts media_kind=%s from the URL', (kind) => {
    expect(parseFilters(new URLSearchParams(`media_kind=${kind}`)).mediaKind).toBe(kind);
  });
});

describe('serializeFilters', () => {
  it('omits defaults so the clean tab has a clean URL', () => {
    expect(serializeFilters(defaultGeneratedFilters()).toString()).toBe('');
  });

  it('round-trips every populated field', () => {
    const filters: GeneratedFilters = {
      state: 'in_assets',
      originKinds: ['shot_generate', 'chat_upload'],
      projectId: '325005725244722',
      mediaKind: 'image',
      model: 'seedream-4',
      since: '30d',
      includeIntermediate: true,
    };
    expect(parseFilters(serializeFilters(filters))).toEqual(filters);
  });

  it('repeats origin_kind rather than joining it with a comma', () => {
    const sp = serializeFilters({
      ...defaultGeneratedFilters(),
      originKinds: ['canvas_run', 'agent_run'],
    });
    expect(sp.getAll('origin_kind')).toEqual(['canvas_run', 'agent_run']);
    expect(sp.toString()).not.toContain(',');
  });

  it('keeps `state=all` in the URL — it is not the default', () => {
    expect(serializeFilters({ ...defaultGeneratedFilters(), state: 'all' }).get('state')).toBe('all');
  });
});

describe('sinceIsoFor', () => {
  it('computes each preset against the injected now', () => {
    expect(sinceIsoFor('24h', NOW)).toBe('2026-08-28T12:00:00.000Z');
    expect(sinceIsoFor('7d', NOW)).toBe('2026-08-22T12:00:00.000Z');
    expect(sinceIsoFor('30d', NOW)).toBe('2026-07-30T12:00:00.000Z');
  });

  it('is undefined when no preset is active', () => {
    expect(sinceIsoFor(null, NOW)).toBeUndefined();
  });

  it('covers every advertised preset', () => {
    for (const preset of SINCE_PRESETS) {
      expect(typeof sinceIsoFor(preset, NOW)).toBe('string');
    }
  });
});

describe('listOptionsFor', () => {
  it('maps filter state onto the service call, resolving `since` at call time', () => {
    expect(
      listOptionsFor(
        {
          state: 'all',
          originKinds: ['canvas_run'],
          projectId: '325005725244722',
          mediaKind: 'video',
          model: 'gpt-image-2',
          since: '24h',
          includeIntermediate: false,
        },
        NOW,
      ),
    ).toEqual({
      state: 'all',
      originKinds: ['canvas_run'],
      projectId: '325005725244722',
      mediaKind: 'video',
      model: 'gpt-image-2',
      since: '2026-08-28T12:00:00.000Z',
    });
  });

  it('omits absent filters entirely so the router keeps its own defaults', () => {
    expect(listOptionsFor(defaultGeneratedFilters(), NOW)).toEqual({ state: 'unreviewed' });
  });
});

describe('toggleOriginKind', () => {
  it('adds and removes without mutating the input', () => {
    const before = ['canvas_run'];
    expect(toggleOriginKind(before, 'agent_run')).toEqual(['canvas_run', 'agent_run']);
    expect(toggleOriginKind(before, 'canvas_run')).toEqual([]);
    expect(before).toEqual(['canvas_run']);
  });
});

describe('Intermediate Inputs (the canvas_upload sub-classification)', () => {
  it('is off by default and absent from a clean URL', () => {
    expect(defaultGeneratedFilters().includeIntermediate).toBe(false);
    expect(serializeFilters(defaultGeneratedFilters()).has('include_intermediate')).toBe(
      false,
    );
  });

  it('round-trips through the URL as the literal `true`', () => {
    const sp = serializeFilters({
      ...defaultGeneratedFilters(),
      includeIntermediate: true,
    });
    expect(sp.get('include_intermediate')).toBe('true');
    expect(parseFilters(sp).includeIntermediate).toBe(true);
  });

  it('reads anything other than `true` as off', () => {
    // A hand-edited `?include_intermediate=0` must not read as "yes" — the
    // whole point of the flag is that the inbox stays clean unless asked.
    for (const raw of ['0', 'false', 'yes', '1', '']) {
      const sp = new URLSearchParams(`include_intermediate=${raw}`);
      expect(parseFilters(sp).includeIntermediate).toBe(false);
    }
  });

  it('sets the request flag only when on', () => {
    expect(listOptionsFor(defaultGeneratedFilters(), NOW).includeIntermediate).toBeUndefined();
    expect(
      listOptionsFor(
        { ...defaultGeneratedFilters(), includeIntermediate: true },
        NOW,
      ).includeIntermediate,
    ).toBe(true);
  });

  it('counts as an active filter', () => {
    // It widens rather than narrows, but a chip that looks untouched while
    // the page is showing masks is the worse lie.
    expect(hasActiveFilters(defaultGeneratedFilters())).toBe(false);
    expect(
      hasActiveFilters({ ...defaultGeneratedFilters(), includeIntermediate: true }),
    ).toBe(true);
  });

  it('is not an origin kind', () => {
    // Adding it to `originKinds` would send the backend an origin_kind it has
    // never written, which matches nothing and looks like an empty inbox.
    expect(SOURCE_OPTIONS.map((o) => o.kind)).not.toContain('intermediate');
    expect(INTERMEDIATE_OPTION.labelKey).toBe('generated.source.intermediate');
  });
});
