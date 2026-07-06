import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SceneRail } from '../components/SceneRail';
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

afterEach(cleanup);

describe('SceneRail', () => {
  it('renders a row per scene with location and INT/EXT badge', () => {
    render(
      <SceneRail
        scenes={[
          scene({ id: '111', heading_int_ext: 'INT', location_text: 'Blank Studio' }),
          scene({ id: '222', heading_int_ext: 'EXT', location_text: 'Rooftop Access' }),
        ]}
        activeSceneId={null}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText('Blank Studio')).toBeInTheDocument();
    expect(screen.getByText('Rooftop Access')).toBeInTheDocument();
    expect(screen.getByText('EXT')).toBeInTheDocument();
  });

  it('calls onSelect with the scene id when a row is clicked', () => {
    const onSelect = vi.fn();
    render(
      <SceneRail
        scenes={[
          scene({ id: '111', location_text: 'Blank Studio' }),
          scene({ id: '222', location_text: 'Rooftop Access' }),
        ]}
        activeSceneId={null}
        onSelect={onSelect}
      />,
    );
    fireEvent.click(screen.getByText('Rooftop Access'));
    expect(onSelect).toHaveBeenCalledWith('222');
  });

  it('truncates the first-action summary to 40 characters', () => {
    const long = 'This is a very long action line that should be truncated hard';
    render(
      <SceneRail
        scenes={[
          scene({ id: '111', elements: [{ id: 'el_a', type: 'action', text: long }] }),
        ]}
        activeSceneId={null}
        onSelect={vi.fn()}
      />,
    );
    const summary = screen.getByText(/This is a very long action/);
    expect(summary.textContent!.endsWith('…')).toBe(true);
    expect(summary.textContent!.length).toBeLessThanOrEqual(41); // 40 chars + ellipsis
  });

  it('marks the active scene', () => {
    render(
      <SceneRail
        scenes={[scene({ id: '111', location_text: 'Blank Studio' })]}
        activeSceneId="111"
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByRole('button')).toHaveClass('active');
    expect(screen.getByRole('button')).toHaveAttribute('aria-current', 'true');
  });
});
