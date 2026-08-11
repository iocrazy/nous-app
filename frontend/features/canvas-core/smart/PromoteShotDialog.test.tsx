/**
 * PromoteShotDialog's `scenesLoading` prop (shot-nodes-on-canvas Task 5, T4
 * forward note): an empty `scenes` array during the mount-time reconcile
 * window must render a loading state, not the "This episode has no scenes
 * yet." empty state — the episode may well have scenes, the fetch just
 * hasn't resolved yet. `scenesLoading` defaults to `false` so every existing
 * caller (none yet — Task 4 didn't have this prop) keeps the old behavior.
 */
import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

import { PromoteShotDialog } from './PromoteShotDialog';

afterEach(() => cleanup());

const noop = () => {};

describe('PromoteShotDialog — scenesLoading', () => {
  it('renders a loading state (not the empty state) when scenes is empty AND scenesLoading is true', () => {
    render(
      <PromoteShotDialog
        open
        scenes={[]}
        scenesLoading
        submitting={false}
        error={null}
        onCancel={noop}
        onPickScene={noop}
      />,
    );
    expect(screen.getByTestId('promote-shot-scenes-loading')).toBeInTheDocument();
    expect(screen.queryByTestId('promote-shot-no-scenes')).toBeNull();
  });

  it('renders the genuine empty state when scenes is empty and scenesLoading is false (default)', () => {
    render(
      <PromoteShotDialog open scenes={[]} submitting={false} error={null} onCancel={noop} onPickScene={noop} />,
    );
    expect(screen.getByTestId('promote-shot-no-scenes')).toBeInTheDocument();
    expect(screen.queryByTestId('promote-shot-scenes-loading')).toBeNull();
  });

  it('renders scene rows (not the loading state) once scenes resolve, even if scenesLoading is stale-true', () => {
    render(
      <PromoteShotDialog
        open
        scenes={[
          { id: 's1', script_id: 'sc1', chapter_id: null, scene_number: '1', heading_int_ext: 'INT',
            location_text: 'Kitchen', time_of_day: 'DAY', content_version: 1, sort_order: 0, elements: [] },
        ]}
        scenesLoading={false}
        submitting={false}
        error={null}
        onCancel={noop}
        onPickScene={noop}
      />,
    );
    expect(screen.getAllByTestId('promote-shot-scene-option')).toHaveLength(1);
    expect(screen.queryByTestId('promote-shot-scenes-loading')).toBeNull();
    expect(screen.queryByTestId('promote-shot-no-scenes')).toBeNull();
  });
});
