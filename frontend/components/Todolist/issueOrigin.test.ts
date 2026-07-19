/**
 * The writer (content menus) and the reader (Related tab) must agree on the
 * origin_id format forever — these pin the round-trip and the cases that must
 * NOT render a back-link.
 */

import { describe, it, expect } from 'vitest';
import { buildOriginId, originLabel, originModule, originPath, parseOriginId } from './issueOrigin';

describe('issueOrigin', () => {
  it('round-trips what a content surface writes', () => {
    const originId = buildOriginId('canvas', '99887766554433');
    expect(originId).toBe('canvas:99887766554433');
    expect(parseOriginId(originId)).toEqual({ kind: 'canvas', id: '99887766554433' });
  });

  it('round-trips a script scene the same way', () => {
    const originId = buildOriginId('scene', '77665544332211');
    expect(originId).toBe('scene:77665544332211');
    expect(parseOriginId(originId)).toEqual({ kind: 'scene', id: '77665544332211' });
  });

  it('keeps snowflake ids intact (no numeric coercion)', () => {
    const big = '9007199254740993'; // > 2^53
    expect(parseOriginId(buildOriginId('canvas', big))?.id).toBe(big);
    // Scene ids are Snowflake bigints too — must never be Number()-coerced.
    expect(parseOriginId(buildOriginId('scene', big))?.id).toBe(big);
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
    expect(parseOriginId('scene:')).toBeNull();
  });

  it('builds a team-scoped canvas path, with a fallback when there is no team', () => {
    const origin = { kind: 'canvas' as const, id: 'c1' };
    expect(originPath(origin, '8')).toBe('/team/8/canvas/c1');
    expect(originPath(origin, undefined)).toBe('/canvas/c1');
    expect(originLabel(origin)).toBe('From a canvas');
  });

  it('routes a scene origin to the projects area (bare scene id cannot deep-link)', () => {
    const origin = { kind: 'scene' as const, id: '77665544332211' };
    expect(originPath(origin, '8')).toBe('/team/8/projects');
    expect(originPath(origin, undefined)).toBe('/projects');
    expect(originLabel(origin)).toBe('From a script scene');
  });

  it('round-trips a project_stage origin, keeping the composite id a string', () => {
    // origin_id = project_stage:{projectId}:{stageId} — the id half itself
    // carries a colon and must NOT be split into numbers.
    const originId = buildOriginId('project_stage', '9007199254740993:123456789012345');
    expect(originId).toBe('project_stage:9007199254740993:123456789012345');
    expect(parseOriginId(originId)).toEqual({
      kind: 'project_stage',
      id: '9007199254740993:123456789012345',
    });
  });

  it('routes a project_stage origin to the project detail page (projectId only)', () => {
    const origin = { kind: 'project_stage' as const, id: '5001:9002' };
    expect(originPath(origin, '8')).toBe('/team/8/projects/5001');
    // No team in scope → team-agnostic projects list fallback.
    expect(originPath(origin, undefined)).toBe('/projects');
    expect(originLabel(origin)).toBe('From a project stage');
  });

  it('degrades a malformed project_stage id without crashing', () => {
    // Empty id after the kind prefix is not a content reference.
    expect(parseOriginId('project_stage:')).toBeNull();
    // Missing projectId (leading colon in the composite id) → list fallback.
    const origin = { kind: 'project_stage' as const, id: ':9002' };
    expect(originPath(origin, '8')).toBe('/projects');
  });
});

describe('originModule', () => {
  it('maps each content origin to its module chip', () => {
    expect(originModule('canvas:123')).toEqual({ label: 'Canvas', dotClass: 'bg-sky-400' });
    expect(originModule('scene:456')).toEqual({ label: 'Script', dotClass: 'bg-violet-400' });
    expect(originModule('project_stage:1:2')).toEqual({ label: 'Project', dotClass: 'bg-emerald-400' });
  });

  it('returns null for non-content origins (no chip, like the mockup)', () => {
    expect(originModule(null)).toBeNull();
    expect(originModule(undefined)).toBeNull();
    expect(originModule('')).toBeNull();
    expect(originModule('12345')).toBeNull(); // routine schedule id
  });

  it('chips a routine-created issue as Autopilot from origin_kind alone', () => {
    // A routine stores a bare schedule id in origin_id (not a content ref), so
    // the kind is what surfaces the trace — regardless of origin_id shape.
    expect(originModule('12345', 'routine')).toEqual({
      label: 'Autopilot',
      dotClass: 'bg-amber-400',
    });
    expect(originModule(null, 'routine')).toEqual({
      label: 'Autopilot',
      dotClass: 'bg-amber-400',
    });
    // A non-routine kind still defers to the content origin_id.
    expect(originModule('canvas:123', 'manual')).toEqual({
      label: 'Canvas',
      dotClass: 'bg-sky-400',
    });
  });
});

describe('publish origin', () => {
  it('round-trips, routes to the distribution surface, and chips as Publish', () => {
    const big = '9007199254740993';
    const parsed = parseOriginId(buildOriginId('publish', big));
    expect(parsed).toEqual({ kind: 'publish', id: big });
    expect(originPath(parsed!, 'team-1')).toBe('/team/team-1/distribution');
    expect(originPath(parsed!, undefined)).toBe('/distribution');
    expect(originLabel(parsed!)).toBe('From a publish batch');
    expect(originModule(`publish:${big}`)).toEqual({ label: 'Publish', dotClass: 'bg-rose-400' });
  });
});
