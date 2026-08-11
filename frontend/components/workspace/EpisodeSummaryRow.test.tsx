/**
 * EpisodeSummaryRow (IA redesign Task 4 rewrite) — the accordion row shell.
 * Pins: the mono read-out (SC · SHOTS · CUTS) and segmented stage indicator
 * are unchanged from the pre-rewrite rollup row; `isOpen`/`onToggle` replace
 * `isCurrent`/`onSelect` (the row toggles its own body in place instead of
 * deep-linking away), and the body only mounts (with the caller's children)
 * while open.
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
    render(<EpisodeSummaryRow episode={EP} epNumber={7} isOpen={false} onToggle={() => {}} />);
    const row = screen.getByTestId('ep-accordion-row-7');
    expect(row).toHaveTextContent('EP7');
    expect(row).toHaveTextContent('Ep 7 — Finale');
    expect(row).toHaveTextContent('5 SC · SHOTS 12/20 · CUTS 3');
  });

  it('lights stageFill(status) segments in the stage indicator', () => {
    render(<EpisodeSummaryRow episode={EP} epNumber={7} isOpen={false} onToggle={() => {}} />);
    // boarding → 3 of 5 lit.
    expect(screen.getByTestId('ws-rollup-stages-7')).toHaveAttribute('data-fill', '3');
  });

  // ── node-segmented bar (B4 真数据: EpisodeProgress.workflow) ──────────

  const WF = { nodes_total: 8, nodes_done: 3, current_node_id: '336', needs_input_count: 0 };

  it('segments by real workflow nodes when the rollup field is present', () => {
    render(
      <EpisodeSummaryRow
        episode={{ ...EP, workflow: WF }}
        epNumber={7}
        isOpen={false}
        onToggle={() => {}}
      />,
    );
    const bar = screen.getByTestId('ws-rollup-stages-7');
    expect(bar).toHaveAttribute('data-fill', '3');
    expect(bar).toHaveAttribute('data-total', '8');
    expect(bar.childElementCount).toBe(8);
  });

  it('falls back to the status ladder when the rollup is absent or has no nodes', () => {
    render(<EpisodeSummaryRow episode={EP} epNumber={7} isOpen={false} onToggle={() => {}} />);
    const legacy = screen.getByTestId('ws-rollup-stages-7');
    expect(legacy).toHaveAttribute('data-fill', '3'); // boarding
    expect(legacy).toHaveAttribute('data-total', '5');
    cleanup();
    render(
      <EpisodeSummaryRow
        episode={{ ...EP, workflow: { ...WF, nodes_total: 0, nodes_done: 0 } }}
        epNumber={7}
        isOpen={false}
        onToggle={() => {}}
      />,
    );
    expect(screen.getByTestId('ws-rollup-stages-7')).toHaveAttribute('data-total', '5');
  });

  it('toggles: passes the episode id when closed, null when already open', () => {
    const onToggle = vi.fn();
    const { rerender } = render(
      <EpisodeSummaryRow episode={EP} epNumber={7} isOpen={false} onToggle={onToggle} />,
    );
    fireEvent.click(screen.getByTestId('ep-accordion-row-7'));
    expect(onToggle).toHaveBeenCalledWith('7');

    onToggle.mockClear();
    rerender(<EpisodeSummaryRow episode={EP} epNumber={7} isOpen onToggle={onToggle} />);
    fireEvent.click(screen.getByTestId('ep-accordion-row-7'));
    expect(onToggle).toHaveBeenCalledWith(null);
  });

  it('mounts the body (and its children) only while open', () => {
    const { rerender } = render(
      <EpisodeSummaryRow episode={EP} epNumber={7} isOpen={false} onToggle={() => {}}>
        <div data-testid="strip-slot">strip</div>
      </EpisodeSummaryRow>,
    );
    expect(screen.queryByTestId('ep-accordion-body-7')).toBeNull();
    expect(screen.queryByTestId('strip-slot')).toBeNull();

    rerender(
      <EpisodeSummaryRow episode={EP} epNumber={7} isOpen onToggle={() => {}}>
        <div data-testid="strip-slot">strip</div>
      </EpisodeSummaryRow>,
    );
    expect(screen.getByTestId('ep-accordion-body-7')).toBeTruthy();
    expect(screen.getByTestId('strip-slot')).toBeTruthy();
  });
});
