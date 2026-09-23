/**
 * Shots tab — the "not indexed yet" placeholder.
 *
 * Shot indexing arrives with PR 3; until then the tab shows what indexing
 * this video WOULD cost and a deliberately disabled trigger (the same
 * pattern as the asset library's `Send To Canvas`).
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_k: string, def?: unknown, opts?: Record<string, unknown>) => {
      const text = typeof def === 'string' ? def : _k;
      const vars = typeof def === 'object' && def ? (def as Record<string, unknown>) : opts;
      return text.replace(/{{(\w+)}}/g, (_m, name) => String(vars?.[name] ?? ''));
    },
  }),
}));

import { ShotsTabPlaceholder, estimateShots } from './ShotsTabPlaceholder';

describe('estimateShots', () => {
  it('is one shot per 4.5 s, rounded up', () => {
    expect(estimateShots(445)).toBe(99);
    expect(estimateShots(446)).toBe(100);
    expect(estimateShots(4)).toBe(1);
  });
  it('is null without a usable duration', () => {
    expect(estimateShots(undefined)).toBeNull();
    expect(estimateShots(0)).toBeNull();
    expect(estimateShots(Number.NaN)).toBeNull();
  });
});

describe('ShotsTabPlaceholder', () => {
  it('shows the duration, the shot estimate and twice as many embeddings', () => {
    render(<ShotsTabPlaceholder durationSeconds={446} />);
    expect(screen.getByTestId('shots-estimate')).toHaveTextContent(
      '7:26 · ≈ 100 shots · ≈ 200 embeddings',
    );
  });

  it('shows a dash when the duration is unknown', () => {
    render(<ShotsTabPlaceholder />);
    expect(screen.getByTestId('shots-estimate')).toHaveTextContent('—');
    expect(screen.getByTestId('shots-estimate')).not.toHaveTextContent('shots');
  });

  it('renders Index This Video as a disabled placeholder that says when it arrives', () => {
    render(<ShotsTabPlaceholder durationSeconds={60} />);
    const button = screen.getByRole('button', { name: 'Index This Video' });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('title', 'Arrives with PR 3');
    expect(screen.getByText('Runs as a Task Center task · you can keep browsing')).toBeInTheDocument();
  });
});
