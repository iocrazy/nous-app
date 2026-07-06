/**
 * RailEntities tests (Task 4.5 — rail section hierarchy).
 *
 * The Characters and Locations rail sections carry a count badge on their
 * section header (laper "Assets ②" pattern), so the rail reads as a structured
 * information architecture rather than a flat list. Empty sections show no
 * badge (nothing to count).
 */
import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { RailCharacter, RailLocation } from '../railDerive';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

// WritingPanel supplies the CAST palette RailEntities imports; stub it so the
// test does not pull the whole panel in.
vi.mock('../components/WritingPanel', () => ({
  CAST_COLORS: ['#111', '#222', '#333'],
}));

import { RailEntities } from '../components/RailEntities';

const char = (name: string, sceneCount: number): RailCharacter => ({
  name,
  sceneCount,
  firstSceneId: '900',
});
const loc = (name: string, sceneCount: number): RailLocation => ({
  name,
  sceneCount,
  firstSceneId: '900',
});

afterEach(cleanup);

describe('RailEntities section count badges', () => {
  it('renders a count badge matching the number of characters and locations', () => {
    render(
      <RailEntities
        characters={[char('ANNA', 2), char('BEN', 1)]}
        locations={[loc('Rooftop', 3)]}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByTestId('rail-characters-count')).toHaveTextContent('2');
    expect(screen.getByTestId('rail-locations-count')).toHaveTextContent('1');
  });

  it('omits the badge for an empty section', () => {
    render(<RailEntities characters={[]} locations={[loc('Rooftop', 1)]} onSelect={vi.fn()} />);
    expect(screen.queryByTestId('rail-characters-count')).toBeNull();
    expect(screen.getByTestId('rail-locations-count')).toHaveTextContent('1');
  });
});
