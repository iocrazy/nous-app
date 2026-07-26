/**
 * LibraryFeed windowing — only a slice of the loaded set may be mounted.
 *
 * Before windowing the feed rendered one FeedItem per loaded row. At ~53 DOM
 * nodes per item a fully scrolled library (1237 rows) produced ~65k nodes,
 * past the point where browsers start dropping frames. These tests pin the
 * three properties that make the window safe to ship:
 *
 *   1. mounted count stays bounded no matter how much data is loaded
 *   2. total scroll height is preserved (spacers stand in for unmounted rows)
 *   3. the window follows scrollTop, so swiping reveals real items
 */
import { describe, it, expect, vi, beforeAll } from 'vitest';
import { render, act } from '@testing-library/react';
import { LibraryFeed } from './LibraryFeed';
import type { Video } from '../types';

vi.mock('hls.js', () => ({ default: class { static isSupported() { return false; } } }));
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => ({ mediaToken: null }) }));

const ITEM_H = 600;

beforeAll(() => {
  // jsdom leaves these at 0; the window maths divides by clientHeight.
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
    configurable: true,
    get() {
      return this.classList?.contains('snap-y') ? ITEM_H : 0;
    },
  });
  // Autoplay/sentinel observers are irrelevant here — stub so they no-op.
  (globalThis as any).IntersectionObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

const makeData = (n: number): Video[] =>
  Array.from({ length: n }, (_, i) => ({
    platform_id: `id-${i}`,
    title: `Video ${i}`,
    aweme_type: 0,
  })) as unknown as Video[];

const scrollFeedTo = async (px: number) => {
  const scroller = document.querySelector('.snap-y') as HTMLElement;
  Object.defineProperty(scroller, 'scrollTop', { configurable: true, value: px });
  await act(async () => {
    scroller.dispatchEvent(new Event('scroll'));
    // the handler coalesces through requestAnimationFrame
    await new Promise((r) => setTimeout(r, 60));
  });
};

/** Ids of the currently mounted rows, in DOM order. */
const mountedIds = () =>
  Array.from(document.querySelectorAll('.feed-item')).map((el) =>
    el.getAttribute('data-id'),
  );

/**
 * How many rows each spacer stands in for.
 *
 * The component writes `calc(N * 100%)`, but jsdom normalises that to
 * `calc(N00%)` (e.g. 46 rows → `calc(4600%)`), so read the percentage and
 * divide rather than pattern-matching the authored form.
 */
const spacerRowCounts = () =>
  Array.from(document.querySelectorAll('.snap-none')).map((el) => {
    const m = /calc\((\d+)%\)/.exec((el as HTMLElement).style.height || '');
    return m ? Number(m[1]) / 100 : 0;
  });

describe('LibraryFeed windowing', () => {
  it('mounts a bounded slice regardless of how many rows are loaded', () => {
    const { unmount } = render(<LibraryFeed data={makeData(20)} />);
    const small = document.querySelectorAll('.feed-item').length;
    unmount();

    render(<LibraryFeed data={makeData(1237)} />);
    const large = document.querySelectorAll('.feed-item').length;

    // 1237 rows must not cost 60x what 20 rows cost.
    expect(large).toBeLessThanOrEqual(small);
    expect(large).toBeLessThan(20);
  });

  it('renders every item when the set is smaller than the window', () => {
    render(<LibraryFeed data={makeData(3)} />);
    expect(document.querySelectorAll('.feed-item').length).toBe(3);
    // No spacer should appear — nothing is unmounted.
    expect(document.querySelectorAll('.snap-none').length).toBe(0);
  });

  it('keeps total scroll height honest via spacers', async () => {
    render(<LibraryFeed data={makeData(100)} />);

    // Spacers stand in for the unmounted rows; together with what is mounted
    // they must still add up to the full set, or the scrollbar would lie and
    // the infinite-scroll sentinel would sit at the wrong place.
    const check = () => {
      const rows = spacerRowCounts().reduce((a, b) => a + b, 0);
      expect(rows + document.querySelectorAll('.feed-item').length).toBe(100);
    };
    check();
    await scrollFeedTo(ITEM_H * 50);
    check();
  });

  it('slides the window to follow scrollTop', async () => {
    render(<LibraryFeed data={makeData(100)} />);
    expect(mountedIds()).toContain('id-0');
    expect(mountedIds()).not.toContain('id-50');

    await scrollFeedTo(ITEM_H * 50);

    expect(mountedIds()).toContain('id-50');
    expect(mountedIds()).not.toContain('id-0');
  });

  it('recovers when the list shrinks past the current index', async () => {
    const { rerender } = render(<LibraryFeed data={makeData(100)} />);
    await scrollFeedTo(ITEM_H * 90);
    expect(mountedIds()).toContain('id-90');

    // A filter change drops the set to 5 rows — the window must not strand
    // itself past the end and render nothing.
    await act(async () => {
      rerender(<LibraryFeed data={makeData(5)} />);
    });
    expect(document.querySelectorAll('.feed-item').length).toBeGreaterThan(0);
  });
});
