import { describe, expect, it } from 'vitest';

import {
  CLICK_PX,
  FAB_GUTTER_PX,
  FAB_SIZE_PX,
  TOP_CHROME_PX,
  clampFabLeft,
  clampFabTop,
  defaultFabTop,
  dockedLeft,
  isClickGesture,
  snapSide,
} from './fabGeometry';

describe('fabGeometry · clamping', () => {
  it('keeps the top edge out of the TopBar band', () => {
    expect(clampFabTop(0, 768)).toBe(TOP_CHROME_PX);
    expect(clampFabTop(40, 768)).toBe(TOP_CHROME_PX);
  });

  it('keeps the bottom edge inside the viewport with the gutter', () => {
    expect(clampFabTop(10_000, 768)).toBe(768 - FAB_SIZE_PX - FAB_GUTTER_PX);
  });

  it('passes a top that is already in range through unchanged', () => {
    expect(clampFabTop(300, 768)).toBe(300);
  });

  it('never returns a top below the chrome band even on a tiny viewport', () => {
    // viewport shorter than chrome + fab: the chrome wins, the fab may clip at the bottom.
    expect(clampFabTop(0, 60)).toBe(TOP_CHROME_PX);
  });

  it('clamps left between the gutters', () => {
    expect(clampFabLeft(-50, 1024)).toBe(FAB_GUTTER_PX);
    expect(clampFabLeft(5_000, 1024)).toBe(1024 - FAB_SIZE_PX - FAB_GUTTER_PX);
    expect(clampFabLeft(400, 1024)).toBe(400);
  });

  it("defaults to today's bottom-20 offset (80px from the bottom)", () => {
    expect(defaultFabTop(768)).toBe(768 - FAB_SIZE_PX - 80);
  });
});

describe('fabGeometry · snapping', () => {
  it('snaps to the left when the centre is in the left half', () => {
    expect(snapSide(100, 1024)).toBe('left');
    expect(snapSide(511, 1024)).toBe('left');
  });

  it('snaps to the right when the centre is at or past the middle', () => {
    expect(snapSide(512, 1024)).toBe('right');
    expect(snapSide(900, 1024)).toBe('right');
  });

  it('places a docked fab one gutter from its edge', () => {
    expect(dockedLeft('left', 1024)).toBe(FAB_GUTTER_PX);
    expect(dockedLeft('right', 1024)).toBe(1024 - FAB_SIZE_PX - FAB_GUTTER_PX);
  });
});

describe('fabGeometry · click vs drag', () => {
  it('treats a displacement under the threshold as a click', () => {
    expect(isClickGesture(0, 0)).toBe(true);
    expect(isClickGesture(3, 0)).toBe(true);
    expect(isClickGesture(2, 2)).toBe(true); // hypot ≈ 2.83
  });

  it('treats the threshold itself and anything beyond as a drag', () => {
    expect(isClickGesture(CLICK_PX, 0)).toBe(false);
    expect(isClickGesture(0, -CLICK_PX)).toBe(false);
    expect(isClickGesture(3, 3)).toBe(false); // hypot ≈ 4.24
  });
});
