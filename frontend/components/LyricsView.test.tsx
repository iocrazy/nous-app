import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { LyricsView } from './LyricsView';

describe('LyricsView', () => {
  const lines = [
    { text: 'first', line_start_ms: 0 },
    { text: 'second', line_start_ms: 10000 },
  ];
  it('renders all lines', () => {
    const { getByText } = render(<LyricsView lines={lines} />);
    expect(getByText('first')).toBeTruthy();
    expect(getByText('second')).toBeTruthy();
  });
  it('marks the active line by currentTime', () => {
    const { getByText } = render(<LyricsView lines={lines} currentTime={11} />);
    expect(getByText('second').className).toContain('font-semibold');
  });
});
