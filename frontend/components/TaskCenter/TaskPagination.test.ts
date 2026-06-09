import { describe, it, expect } from 'vitest';
import { pageWindow } from './TaskPagination';

describe('pageWindow', () => {
  it('returns [1] for a single page or empty', () => {
    expect(pageWindow(1, 1)).toEqual([1]);
    expect(pageWindow(1, 0)).toEqual([1]);
  });

  it('lists every page with no gaps when small', () => {
    expect(pageWindow(2, 4)).toEqual([1, 2, 3, 4]);
  });

  it('inserts gaps around a middle page', () => {
    expect(pageWindow(6, 50)).toEqual([1, '…', 5, 6, 7, '…', 50]);
  });

  it('no leading gap near the start', () => {
    expect(pageWindow(2, 50)).toEqual([1, 2, 3, '…', 50]);
  });

  it('no trailing gap near the end', () => {
    expect(pageWindow(49, 50)).toEqual([1, '…', 48, 49, 50]);
  });

  it('does not duplicate when first/last touch the window', () => {
    expect(pageWindow(1, 2)).toEqual([1, 2]);
    expect(pageWindow(3, 3)).toEqual([1, 2, 3]);
  });
});
