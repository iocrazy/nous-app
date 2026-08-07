/**
 * EpisodeViewTabs — the surface segmented control (B5 T-B5.4).
 *   1. Renders one segment per view, labelled by its i18n key.
 *   2. Clicking an inactive segment fires onChange with that view key.
 *   3. Clicking the already-active segment is a no-op (no redundant onChange).
 *   4. An empty view set (deliverable-only node) renders nothing.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { EpisodeViewTabs } from './EpisodeViewTabs';
import { SURFACE_VIEWS } from './nodeSurface';

// Deterministic labels: echo the i18n key so assertions never depend on the
// translation bundle being loaded.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, fb?: string) => fb ?? k }),
}));

const storyboardViews = SURFACE_VIEWS.storyboard;

describe('EpisodeViewTabs', () => {
  it('renders one tab per view in the set', () => {
    render(
      <EpisodeViewTabs views={storyboardViews} active="storyboard" onChange={() => {}} />,
    );
    const tabs = screen.getAllByRole('tab');
    expect(tabs).toHaveLength(storyboardViews.length); // storyboard | canvas | shotlist
    expect(screen.getByRole('tab', { name: 'projects.episodeViews.canvas' })).toBeTruthy();
  });

  it('marks the active view with aria-selected', () => {
    render(
      <EpisodeViewTabs views={storyboardViews} active="canvas" onChange={() => {}} />,
    );
    const canvas = screen.getByRole('tab', { name: 'projects.episodeViews.canvas' });
    expect(canvas.getAttribute('aria-selected')).toBe('true');
    const shotlist = screen.getByRole('tab', { name: 'projects.episodeViews.shotlist' });
    expect(shotlist.getAttribute('aria-selected')).toBe('false');
  });

  it('fires onChange with the clicked view key', () => {
    const onChange = vi.fn();
    render(<EpisodeViewTabs views={storyboardViews} active="storyboard" onChange={onChange} />);
    fireEvent.click(screen.getByRole('tab', { name: 'projects.episodeViews.canvas' }));
    expect(onChange).toHaveBeenCalledWith('canvas');
  });

  it('does not fire onChange when the active tab is clicked', () => {
    const onChange = vi.fn();
    render(<EpisodeViewTabs views={storyboardViews} active="storyboard" onChange={onChange} />);
    fireEvent.click(screen.getByRole('tab', { name: 'projects.episodeViews.storyboard' }));
    expect(onChange).not.toHaveBeenCalled();
  });

  it('renders nothing for an empty view set (deliverable-only node)', () => {
    const { container } = render(
      <EpisodeViewTabs views={[]} active="" onChange={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });
});
