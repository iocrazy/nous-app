import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { WritingPanel, deriveStatistics } from '../components/WritingPanel';
import type { SceneDoc } from '../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

// 2 scenes / 7 elements / 2 distinct characters / 2 distinct locations.
const fixture: SceneDoc[] = [
  {
    id: '1',
    script_id: '1',
    chapter_id: null,
    heading_int_ext: 'INT',
    location_text: 'Studio',
    time_of_day: 'NIGHT',
    content_version: 1,
    sort_order: 0,
    elements: [
      { id: 'el_1', type: 'action', text: 'Black pool of light' },
      { id: 'el_2', type: 'character', text: 'CLIENT' },
      { id: 'el_3', type: 'dialogue', text: 'I said four seconds' },
    ],
  },
  {
    id: '2',
    script_id: '1',
    chapter_id: null,
    heading_int_ext: 'EXT',
    location_text: 'Rooftop',
    time_of_day: 'NIGHT',
    content_version: 1,
    sort_order: 1,
    elements: [
      { id: 'el_4', type: 'action', text: 'Wind on the roof' },
      { id: 'el_5', type: 'character', text: 'DEV' },
      { id: 'el_6', type: 'dialogue', text: 'Send it back' },
      { id: 'el_7', type: 'action', text: 'She waits' },
    ],
  },
];

afterEach(cleanup);

describe('deriveStatistics', () => {
  it('counts scenes, words, distinct characters and distinct locations', () => {
    const stats = deriveStatistics(fixture);
    expect(stats.scenes).toBe(2);
    expect(stats.characters).toBe(2);
    expect(stats.locations).toBe(2);
    expect(stats.words).toBe(19);
    expect(stats.cast).toEqual(['CLIENT', 'DEV']);
  });

  it('treats character cues case-insensitively as one entry', () => {
    const stats = deriveStatistics([
      { ...fixture[0], elements: [
        { id: 'a', type: 'character', text: 'Client' },
        { id: 'b', type: 'character', text: 'CLIENT' },
      ] },
    ]);
    expect(stats.characters).toBe(1);
  });
});

describe('WritingPanel', () => {
  it('renders the live statistics for the loaded scenes', () => {
    render(<WritingPanel scenes={fixture} format="hollywood" onFormatChange={vi.fn()} />);
    expect(screen.getByTestId('stat-scenes')).toHaveTextContent('2');
    expect(screen.getByTestId('stat-words')).toHaveTextContent('19');
    expect(screen.getByTestId('stat-characters')).toHaveTextContent('2');
    expect(screen.getByTestId('stat-locations')).toHaveTextContent('2');
    expect(screen.getByText('CLIENT')).toBeInTheDocument();
    expect(screen.getByText('DEV')).toBeInTheDocument();
  });

  it('keeps the Asian format disabled and fires onFormatChange for Hollywood', () => {
    const onFormatChange = vi.fn();
    render(<WritingPanel scenes={fixture} format="hollywood" onFormatChange={onFormatChange} />);
    expect(screen.getByRole('button', { name: 'editor.asian' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'editor.hollywood' }));
    expect(onFormatChange).toHaveBeenCalledWith('hollywood');
  });
});
