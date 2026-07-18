import { render, screen, cleanup, fireEvent, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SceneToc } from '../components/SceneToc';
import type { SceneDoc } from '../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const scene = (over: Partial<SceneDoc>): SceneDoc => ({
  id: '1',
  script_id: '1',
  chapter_id: null,
  heading_int_ext: 'INT',
  location_text: 'Blank Studio',
  time_of_day: 'NIGHT',
  content_version: 1,
  sort_order: 0,
  elements: [],
  ...over,
});

const threeScenes = [
  scene({ id: '111', location_text: 'Blank Studio' }),
  scene({ id: '222', location_text: 'Rooftop Access' }),
  scene({ id: '333', location_text: 'Server Room' }),
];

const SCRIPT_ID = 'script-toc-test';

afterEach(cleanup);
beforeEach(() => localStorage.clear());

describe('SceneToc', () => {
  it('renders one tick per scene plus the reused SceneRail list', () => {
    render(
      <SceneToc scenes={threeScenes} activeSceneId={null} onSelect={vi.fn()} scriptId={SCRIPT_ID} />,
    );
    expect(screen.getAllByTestId('scene-toc-tick')).toHaveLength(3);
    // Panel content reuses SceneRail — its rows are present in the DOM.
    expect(screen.getByTestId('scene-rail')).toBeInTheDocument();
    expect(screen.getByText('Rooftop Access')).toBeInTheDocument();
  });

  it('marks the tick of the active scene', () => {
    render(
      <SceneToc scenes={threeScenes} activeSceneId="222" onSelect={vi.fn()} scriptId={SCRIPT_ID} />,
    );
    const ticks = screen.getAllByTestId('scene-toc-tick');
    expect(ticks[0]).not.toHaveClass('active');
    expect(ticks[1]).toHaveClass('active');
    expect(ticks[1]).toHaveAttribute('aria-current', 'true');
  });

  it('calls onSelect with the scene id when a tick is clicked', () => {
    const onSelect = vi.fn();
    render(
      <SceneToc scenes={threeScenes} activeSceneId={null} onSelect={onSelect} scriptId={SCRIPT_ID} />,
    );
    fireEvent.click(screen.getAllByTestId('scene-toc-tick')[2]);
    expect(onSelect).toHaveBeenCalledWith('333');
  });

  it('is closed by default and opens on hover, closing again after the leave delay', () => {
    vi.useFakeTimers();
    try {
      render(
        <SceneToc scenes={threeScenes} activeSceneId={null} onSelect={vi.fn()} scriptId={SCRIPT_ID} />,
      );
      const toc = screen.getByTestId('scene-toc');
      expect(toc).toHaveAttribute('data-open', 'false');

      fireEvent.mouseEnter(toc);
      expect(toc).toHaveAttribute('data-open', 'true');

      fireEvent.mouseLeave(toc);
      // Still open during the grace window…
      act(() => vi.advanceTimersByTime(100));
      expect(toc).toHaveAttribute('data-open', 'true');
      // …then closes.
      act(() => vi.advanceTimersByTime(300));
      expect(toc).toHaveAttribute('data-open', 'false');
    } finally {
      vi.useRealTimers();
    }
  });

  it('pins the panel open and persists the choice per script', () => {
    const { unmount } = render(
      <SceneToc scenes={threeScenes} activeSceneId={null} onSelect={vi.fn()} scriptId={SCRIPT_ID} />,
    );
    const toc = screen.getByTestId('scene-toc');
    expect(toc).toHaveAttribute('data-open', 'false');

    fireEvent.click(screen.getByTestId('scene-toc-pin'));
    expect(toc).toHaveAttribute('data-open', 'true');
    expect(localStorage.getItem(`editor.sceneToc.${SCRIPT_ID}`)).toBe('1');

    // A fresh mount for the same script restores the pinned (open) state.
    unmount();
    render(
      <SceneToc scenes={threeScenes} activeSceneId={null} onSelect={vi.fn()} scriptId={SCRIPT_ID} />,
    );
    expect(screen.getByTestId('scene-toc')).toHaveAttribute('data-open', 'true');
  });

  it('renders nothing when there are no scenes', () => {
    const { container } = render(
      <SceneToc scenes={[]} activeSceneId={null} onSelect={vi.fn()} scriptId={SCRIPT_ID} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
