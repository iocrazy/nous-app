// Regression: the empty star must use a THEME-FOLLOWING token, not a raw
// `ink` hue. K1's palette remap inverted that ramp in the light theme
// (`--ink-600` = #52525b dark but #D9D4C8 light), so `text-ink-600` painted
// near-white stars on the warm-paper card — 1.43:1 contrast, invisible. The
// bug shipped unnoticed because every RatingStars caller that had a non-zero
// rating still showed its filled (ochre) stars; only all-empty rows, like a
// freshly captured inspiration note, went completely blank.
//
// This asserts the class name rather than a computed colour on purpose:
// jsdom applies no stylesheet, so `getComputedStyle` would report the same
// empty string for every candidate and the test would pass for ANY class.
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { RatingStars } from './DetailCardKit';

const starsOf = () => screen.getAllByRole('button').map((b) => b.querySelector('svg'));

describe('RatingStars theme tokens', () => {
  it('paints empty stars with a theme-following token, never the inverted ink ramp', () => {
    render(<RatingStars value={0} onChange={vi.fn()} />);
    const stars = starsOf();
    expect(stars).toHaveLength(5);
    for (const svg of stars) {
      expect(svg?.getAttribute('class')).toContain('text-content-3');
      expect(svg?.getAttribute('class')).not.toContain('ink-600');
    }
  });

  it('keeps the filled star on the K1-remapped ochre hue, which has a generated fill- utility', () => {
    render(<RatingStars value={3} onChange={vi.fn()} />);
    const stars = starsOf();
    for (const svg of stars.slice(0, 3)) {
      expect(svg?.getAttribute('class')).toContain('fill-amber-400');
    }
    // `fill-warn` does not exist in the built stylesheet — swapping to it
    // would silently drop the fill and leave hollow outlines.
    for (const svg of stars) expect(svg?.getAttribute('class')).not.toContain('fill-warn');
  });
});
