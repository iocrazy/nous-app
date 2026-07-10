import { describe, expect, it } from 'vitest';
import { buildPrefillContent, hotspotToRef } from './hotspotToRef';
import type { Hotspot } from '../../services/topicService';

const base = (over: Partial<Hotspot> = {}): Hotspot => ({
  id: '42',
  title: 'Silent vlog cooking passes 2.1B',
  tags: [],
  source_label: 'DOUYIN',
  heat: 98.4,
  origin_url: 'https://douyin.com/x',
  url: 'https://fallback',
  captured_at: '2026-07-07T00:00:00+00:00',
  ...over,
});

describe('hotspotToRef', () => {
  it('maps the core fields into a RefHotspot snapshot', () => {
    expect(hotspotToRef(base())).toEqual({
      hotspot_id: '42',
      title: 'Silent vlog cooking passes 2.1B',
      source: 'DOUYIN',
      heat: 98.4,
      url: 'https://douyin.com/x',
      captured_at: '2026-07-07T00:00:00+00:00',
    });
  });

  it('prefers origin_url, falls back to url', () => {
    expect(hotspotToRef(base({ origin_url: null })).url).toBe('https://fallback');
  });

  it('drops null/undefined optionals rather than emitting nulls', () => {
    const ref = hotspotToRef(base({ source_label: null, heat: null, origin_url: null, url: null, captured_at: null }));
    expect(ref).toEqual({ hotspot_id: '42', title: 'Silent vlog cooking passes 2.1B' });
    expect('source' in ref).toBe(false);
    expect('url' in ref).toBe(false);
  });
});

describe('buildPrefillContent', () => {
  it('merges category + tags, lowercased/deduped/hyphenated, category first', () => {
    expect(
      buildPrefillContent(base({ category: 'Short Form', tags: ['Trend', 'trend', 'AI Video'] })),
    ).toBe('\n\n#short-form #trend #ai-video');
  });

  it('returns empty string when there is no category or tags', () => {
    expect(buildPrefillContent(base({ category: null, tags: [] }))).toBe('');
  });
});
