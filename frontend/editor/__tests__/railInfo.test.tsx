import { render, screen, cleanup, fireEvent, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

import { RailModules } from '../components/RailModules';
import { RailEntities } from '../components/RailEntities';
import { deriveRailCharacters, deriveRailLocations } from '../railDerive';
import type { SceneDoc } from '../types';

afterEach(cleanup);

const scene = (over: Partial<SceneDoc>): SceneDoc => ({
  id: '1',
  script_id: '1',
  chapter_id: null,
  heading_int_ext: 'INT',
  location_text: 'Studio',
  time_of_day: 'NIGHT',
  content_version: 1,
  sort_order: 0,
  elements: [],
  ...over,
});

// Ada appears in s1 + s2; Blythe only s1; Cy only s3. Studio in s1 + s3; Rooftop s2.
const fixture: SceneDoc[] = [
  scene({
    id: 's1',
    location_text: 'Studio',
    elements: [
      { id: 'a', type: 'character', text: 'Ada' },
      { id: 'b', type: 'dialogue', text: 'Hi' },
      { id: 'c', type: 'character', text: 'Blythe' },
    ],
  }),
  scene({
    id: 's2',
    location_text: 'Rooftop',
    elements: [{ id: 'd', type: 'character', text: 'Ada' }],
  }),
  scene({
    id: 's3',
    location_text: 'Studio',
    elements: [{ id: 'e', type: 'character', text: 'Cy' }],
  }),
];

describe('railDerive', () => {
  it('derives characters with scene-appearance counts and first scene, in first-seen order', () => {
    expect(deriveRailCharacters(fixture)).toEqual([
      { name: 'Ada', sceneCount: 2, firstSceneId: 's1' },
      { name: 'Blythe', sceneCount: 1, firstSceneId: 's1' },
      { name: 'Cy', sceneCount: 1, firstSceneId: 's3' },
    ]);
  });

  it('counts a character once per scene even if cued twice', () => {
    const s = scene({
      id: 's9',
      elements: [
        { id: '1', type: 'character', text: 'Ada' },
        { id: '2', type: 'character', text: 'ADA' },
      ],
    });
    expect(deriveRailCharacters([s])).toEqual([
      { name: 'Ada', sceneCount: 1, firstSceneId: 's9' },
    ]);
  });

  it('derives distinct locations with scene counts and first scene', () => {
    expect(deriveRailLocations(fixture)).toEqual([
      { name: 'Studio', sceneCount: 2, firstSceneId: 's1' },
      { name: 'Rooftop', sceneCount: 1, firstSceneId: 's2' },
    ]);
  });
});

describe('RailModules', () => {
  it('marks all four module slots selectable', () => {
    render(<RailModules activeView="script" onSelect={vi.fn()} />);
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(4);
    // Script — selectable and active (current view).
    expect(buttons[0]).toBeEnabled();
    expect(buttons[0]).toHaveClass('active');
    // Beats — now selectable (beat sheet, PR-BT2), not active yet.
    expect(buttons[1]).toBeEnabled();
    expect(buttons[1]).not.toHaveClass('active');
    // Storyboard — selectable (shot board, Phase B P3), not active yet.
    expect(buttons[2]).toBeEnabled();
    expect(buttons[2]).not.toHaveClass('active');
    // Scenes — selectable (node view), not the active one yet.
    expect(buttons[3]).toBeEnabled();
    expect(buttons[3]).not.toHaveClass('active');
  });

  it('fires onSelect with the beats view when the Beats slot is clicked', () => {
    const onSelect = vi.fn();
    render(<RailModules activeView="script" onSelect={onSelect} />);
    fireEvent.click(screen.getByRole('button', { name: /moduleBeats/ }));
    expect(onSelect).toHaveBeenCalledWith('beats');
  });

  it('follows aria-current to the active view and fires onSelect on click', () => {
    const onSelect = vi.fn();
    const { rerender } = render(<RailModules activeView="script" onSelect={onSelect} />);
    const scenes = screen.getByRole('button', { name: /moduleScenes/ });
    expect(scenes).not.toHaveAttribute('aria-current');

    fireEvent.click(scenes);
    expect(onSelect).toHaveBeenCalledWith('nodes');

    rerender(<RailModules activeView="nodes" onSelect={onSelect} />);
    expect(screen.getByRole('button', { name: /moduleScenes/ })).toHaveAttribute(
      'aria-current',
      'page',
    );
  });
});

describe('RailEntities', () => {
  const characters = deriveRailCharacters(fixture);
  const locations = deriveRailLocations(fixture);

  it('lists characters with their appearance counts and scrolls to the first scene on click', () => {
    const onSelect = vi.fn();
    render(<RailEntities characters={characters} locations={locations} onSelect={onSelect} />);

    const ada = screen.getByRole('button', { name: /Ada/ });
    expect(within(ada).getByText('2')).toBeInTheDocument();

    fireEvent.click(ada);
    expect(onSelect).toHaveBeenCalledWith('s1');
  });

  it('lists locations with scene counts and scrolls to the first matching scene on click', () => {
    const onSelect = vi.fn();
    render(<RailEntities characters={characters} locations={locations} onSelect={onSelect} />);

    const rooftop = screen.getByRole('button', { name: /Rooftop/ });
    expect(within(rooftop).getByText('1')).toBeInTheDocument();

    fireEvent.click(rooftop);
    expect(onSelect).toHaveBeenCalledWith('s2');
  });

  it('shows empty hints when there are no characters or locations', () => {
    render(<RailEntities characters={[]} locations={[]} onSelect={vi.fn()} />);
    expect(screen.getByText('editor.charactersEmpty')).toBeInTheDocument();
    expect(screen.getByText('editor.locationsEmpty')).toBeInTheDocument();
  });
});
