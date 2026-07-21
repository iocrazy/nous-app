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

  it('counts CJK text per character, mixed with Latin words', () => {
    const stats = deriveStatistics([
      { ...fixture[0], location_text: '', elements: [
        // 8 hanzi + 1 Latin word = 9; punctuation is neither.
        { id: 'a', type: 'dialogue', text: '我没有什么可说的, button' },
        // 4 hanzi + 1 Latin word = 5.
        { id: 'b', type: 'dialogue', text: '水电费 i 呢?' },
      ] },
    ]);
    expect(stats.words).toBe(14);
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

  it('offers both formats and fires onFormatChange for each', () => {
    const onFormatChange = vi.fn();
    render(<WritingPanel scenes={fixture} format="hollywood" onFormatChange={onFormatChange} />);

    const asian = screen.getByRole('button', { name: 'editor.asian' });
    expect(asian).toBeEnabled();
    fireEvent.click(asian);
    expect(onFormatChange).toHaveBeenCalledWith('asian');

    fireEvent.click(screen.getByRole('button', { name: 'editor.hollywood' }));
    expect(onFormatChange).toHaveBeenCalledWith('hollywood');
  });

  it('marks the active format with aria-pressed', () => {
    render(<WritingPanel scenes={fixture} format="asian" onFormatChange={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'editor.asian' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByRole('button', { name: 'editor.hollywood' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });
});
