import { describe, expect, it } from 'vitest';

import {
  SINCE_PRESETS,
  SOURCE_OPTIONS,
  defaultGeneratedFilters,
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
