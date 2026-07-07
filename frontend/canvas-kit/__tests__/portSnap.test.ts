/**
 * nearestPort — magnetic port snapping math (canvas-kit).
 */
import { describe, it, expect } from 'vitest';
import { nearestPort, type SnapPort } from '../portSnap';

const port = (id: string, x: number, y: number, nodeId = 'n'): SnapPort => ({
  id,
  nodeId,
  x,
  y,
});

describe('nearestPort', () => {
  it('returns null for an empty candidate list', () => {
    expect(nearestPort({ x: 0, y: 0 }, [])).toBeNull();
  });

  it('hits a port inside the radius', () => {
    const p = port('a', 10, 0);
    expect(nearestPort({ x: 0, y: 0 }, [p], 48)).toBe(p);
  });

  it('returns null when the only port is outside the radius', () => {
    const p = port('a', 100, 0);
    expect(nearestPort({ x: 0, y: 0 }, [p], 48)).toBeNull();
  });

  it('includes a port exactly on the radius boundary', () => {
    const p = port('edge', 48, 0);
    expect(nearestPort({ x: 0, y: 0 }, [p], 48)).toBe(p);
  });

  it('excludes a port just past the radius boundary', () => {
    const p = port('a', 49, 0);
    expect(nearestPort({ x: 0, y: 0 }, [p], 48)).toBeNull();
  });

  it('picks the nearest of several in-range candidates', () => {
    const near = port('near', 5, 5);
    const far = port('far', 30, 30);
    expect(nearestPort({ x: 0, y: 0 }, [far, near], 48)).toBe(near);
  });

  it('measures Euclidean distance (diagonal past radius is excluded)', () => {
    // (40,40) is ~56.6 away — outside a 48 radius even though each axis is < 48.
    const p = port('diag', 40, 40);
    expect(nearestPort({ x: 0, y: 0 }, [p], 48)).toBeNull();
  });

  it('breaks a distance tie in favour of the earlier port', () => {
    const first = port('first', 10, 0);
    const second = port('second', -10, 0);
    expect(nearestPort({ x: 0, y: 0 }, [first, second], 48)).toBe(first);
  });

  it('defaults to a 48px radius', () => {
    expect(nearestPort({ x: 0, y: 0 }, [port('a', 47, 0)])).not.toBeNull();
    expect(nearestPort({ x: 0, y: 0 }, [port('a', 49, 0)])).toBeNull();
  });
});
