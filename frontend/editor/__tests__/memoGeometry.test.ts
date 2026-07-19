/**
 * Memo-pin geometry (Beats M4) — pure math, no DOM.
 *
 * pinColorFor decides which beat colour a time-anchored memo inherits;
 * layoutMemoPins packs horizontally-crowded memo cards into non-overlapping
 * rows; memoPinPath draws the relaxed connector curve; formatAnchorSec is the
 * shared film-apostrophe offset label.
 */
import { describe, expect, it } from 'vitest';

import {
  formatAnchorSec,
  layoutMemoPins,
  memoPinPath,
  pinColorFor,
  type MemoPinBox,
  type PinColorBeat,
} from '../beats/memoGeometry';

describe('pinColorFor', () => {
  const beats: PinColorBeat[] = [
    { start_sec: 0, duration_sec: 60, color: '#aaa' },
    { start_sec: 60, duration_sec: 60, color: '#bbb' },
    { start_sec: 120, duration_sec: 60, color: null }, // colourless beat
    { start_sec: 200, duration_sec: null, color: '#ccc' }, // no duration
  ];

  it('returns the colour of the containing beat', () => {
    expect(pinColorFor(30, beats)).toBe('#aaa');
    expect(pinColorFor(90, beats)).toBe('#bbb');
  });

  it('is half-open: the end second belongs to the next beat', () => {
    expect(pinColorFor(60, beats)).toBe('#bbb'); // 60 is start of beat 2, not end of 1
  });

  it('returns null in a gap, over a colourless beat, or over a zero-length beat', () => {
    expect(pinColorFor(130, beats)).toBeNull(); // colourless beat
    expect(pinColorFor(200, beats)).toBeNull(); // null duration
    expect(pinColorFor(999, beats)).toBeNull(); // gap past the end
  });

  it('picks the earliest-starting beat when coloured beats overlap', () => {
    const overlap: PinColorBeat[] = [
      { start_sec: 0, duration_sec: 100, color: '#early' },
      { start_sec: 40, duration_sec: 100, color: '#late' },
    ];
    expect(pinColorFor(50, overlap)).toBe('#early');
  });

  it('ignores unarranged beats (null start)', () => {
    expect(pinColorFor(10, [{ start_sec: null, duration_sec: 60, color: '#x' }])).toBeNull();
  });
});

describe('layoutMemoPins', () => {
  it('keeps well-separated pins on the top row', () => {
    const pins: MemoPinBox[] = [
      { id: 'a', x: 0, width: 100 },
      { id: 'b', x: 300, width: 100 },
    ];
    const rows = layoutMemoPins(pins, 8);
    expect(rows.get('a')).toBe(0);
    expect(rows.get('b')).toBe(0);
  });

  it('pushes an overlapping pin to the next row', () => {
    const pins: MemoPinBox[] = [
      { id: 'a', x: 0, width: 100 },
      { id: 'b', x: 50, width: 100 }, // overlaps a
    ];
    const rows = layoutMemoPins(pins, 8);
    expect(rows.get('a')).toBe(0);
    expect(rows.get('b')).toBe(1);
  });

  it('reuses the top row once there is horizontal room again', () => {
    const pins: MemoPinBox[] = [
      { id: 'a', x: 0, width: 100 }, // ends at 108
      { id: 'b', x: 50, width: 100 }, // row 1
      { id: 'c', x: 200, width: 100 }, // clears a → back to row 0
    ];
    const rows = layoutMemoPins(pins, 8);
    expect(rows.get('a')).toBe(0);
    expect(rows.get('b')).toBe(1);
    expect(rows.get('c')).toBe(0);
  });

  it('orders by x regardless of input order (stable)', () => {
    const pins: MemoPinBox[] = [
      { id: 'later', x: 50, width: 100 },
      { id: 'first', x: 0, width: 100 },
    ];
    const rows = layoutMemoPins(pins, 8);
    expect(rows.get('first')).toBe(0);
    expect(rows.get('later')).toBe(1);
  });
});

describe('memoPinPath', () => {
  it('starts at the pin and ends at the card, with mid-line control points', () => {
    const d = memoPinPath(100, 0, 140, 40);
    expect(d).toBe('M 100 0 C 100 20 140 20 140 40');
  });

  it('drops straight when the card is directly below', () => {
    const d = memoPinPath(50, 0, 50, 60);
    expect(d).toBe('M 50 0 C 50 30 50 30 50 60');
  });
});

describe('formatAnchorSec', () => {
  it('reads seconds under a minute', () => {
    expect(formatAnchorSec(45)).toBe('45s');
    expect(formatAnchorSec(0)).toBe('0s');
  });

  it('uses the film apostrophe on and past the minute', () => {
    expect(formatAnchorSec(60)).toBe("1'");
    expect(formatAnchorSec(90)).toBe("1'30");
    expect(formatAnchorSec(125)).toBe("2'05");
  });

  it('clamps and rounds', () => {
    expect(formatAnchorSec(-5)).toBe('0s');
    expect(formatAnchorSec(30.6)).toBe('31s');
  });
});
