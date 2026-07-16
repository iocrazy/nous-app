/**
 * The writer (content menus) and the reader (Related tab) must agree on the
 * origin_id format forever — these pin the round-trip and the cases that must
 * NOT render a back-link.
 */

import { describe, it, expect } from 'vitest';
import { buildOriginId, originLabel, originPath, parseOriginId } from './issueOrigin';

describe('issueOrigin', () => {
  it('round-trips what a content surface writes', () => {
    const originId = buildOriginId('canvas', '99887766554433');
    expect(originId).toBe('canvas:99887766554433');
    expect(parseOriginId(originId)).toEqual({ kind: 'canvas', id: '99887766554433' });
  });

  it('keeps snowflake ids intact (no numeric coercion)', () => {
    const big = '9007199254740993'; // > 2^53
    expect(parseOriginId(buildOriginId('canvas', big))?.id).toBe(big);
  });

  it('renders no back-link for non-content origins', () => {
    // A routine stores a bare schedule id — not a content reference.
    expect(parseOriginId('12345')).toBeNull();
    expect(parseOriginId(null)).toBeNull();
    expect(parseOriginId(undefined)).toBeNull();
    expect(parseOriginId('')).toBeNull();
    // Kinds we don't link yet must degrade quietly, not crash.
    expect(parseOriginId('script:42')).toBeNull();
  });

  it('ignores malformed values', () => {
    expect(parseOriginId(':42')).toBeNull();
    expect(parseOriginId('canvas:')).toBeNull();
  });

  it('builds a team-scoped canvas path, with a fallback when there is no team', () => {
    const origin = { kind: 'canvas' as const, id: 'c1' };
    expect(originPath(origin, '8')).toBe('/team/8/canvas/c1');
    expect(originPath(origin, undefined)).toBe('/canvas/c1');
    expect(originLabel(origin)).toBe('From a canvas');
  });
});
