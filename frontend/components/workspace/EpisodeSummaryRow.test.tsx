/**
 * EpisodeSummaryRow (B5 T-B5.5) — per-episode rollup row. Pins: the mono
 * read-out (SC · SHOTS · CUTS) mirrors the Continue card; the segmented stage
 * indicator lights `stageFill(status)` of 5 segments (status-derived
 * approximation of the spec's node-segmented bar); clicking deep-links.
 */
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { describe, expect, it, vi, afterEach } from 'vitest';

import { EpisodeSummaryRow, stageFill } from './EpisodeSummaryRow';
import type { EpisodeProgress } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown) =>
      key.startsWith('projects.workspace.episodeStatus.')
        ? key.split('.').pop()!
        : typeof fallback === 'string'
          ? fallback
          : key,
  }),
}));

const EP: EpisodeProgress = {
  episode_id: '7',
  title: 'Ep 7 — Finale',
  sort_order: 70,
  script_count: 1,
  scene_count: 5,
  shots_total: 20,
  shots_done: 12,
  renders_count: 3,
  status: 'boarding',
};

afterEach(() => cleanup());

describe('stageFill', () => {
  it('maps each pipeline stage to 1..5 lit segments', () => {
    expect(stageFill('planned')).toBe(1);
    expect(stageFill('drafting')).toBe(2);
    expect(stageFill('boarding')).toBe(3);
    expect(stageFill('boarded')).toBe(4);
    expect(stageFill('rendered')).toBe(5);
  });

  it('degrades unknown/future statuses to 0 lit segments', () => {
    expect(stageFill('archived')).toBe(0);
    expect(stageFill('')).toBe(0);
  });
});

describe('EpisodeSummaryRow', () => {
  it('renders the EP number, title and the SC/SHOTS/CUTS read-out', () => {
    render(<EpisodeSummaryRow episode={EP} epNumber={7} isCurrent={false} onSelect={() => {}} />);
    const row = screen.getByTestId('ws-rollup-row-7');
    expect(row).toHaveTextContent('EP7');
    expect(row).toHaveTextContent('Ep 7 — Finale');
    expect(row).toHaveTextContent('5 SC · SHOTS 12/20 · CUTS 3');
  });

  it('lights stageFill(status) segments in the stage indicator', () => {
    render(<EpisodeSummaryRow episode={EP} epNumber={7} isCurrent={false} onSelect={() => {}} />);
    // boarding → 3 of 5 lit.
    expect(screen.getByTestId('ws-rollup-stages-7')).toHaveAttribute('data-fill', '3');
  });

  it('deep-links to the episode on click', () => {
    const onSelect = vi.fn();
    render(<EpisodeSummaryRow episode={EP} epNumber={7} isCurrent={false} onSelect={onSelect} />);
    fireEvent.click(screen.getByTestId('ws-rollup-row-7'));
    expect(onSelect).toHaveBeenCalledWith('7');
  });

  it('marks the current episode and disables clicking when no handler is given', () => {
    render(<EpisodeSummaryRow episode={EP} epNumber={7} isCurrent />);
    const row = screen.getByTestId('ws-rollup-row-7');
    expect(row).toHaveAttribute('data-current', 'true');
    expect(row).toBeDisabled();
  });
});
