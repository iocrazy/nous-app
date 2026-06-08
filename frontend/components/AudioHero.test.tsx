import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
import { AudioHero } from './AudioHero';

// The waveform player touches audio/canvas APIs that jsdom lacks; stub it.
vi.mock('./AudioWaveformPlayer', () => ({
  AudioWaveformPlayer: () => null,
}));
// No media id is passed in these tests, but guard the lyrics fetch anyway.
vi.mock('../services/lyricsService', () => ({
  getMediaLyrics: vi.fn().mockResolvedValue({ lines: [] }),
}));

describe('AudioHero cover affordance', () => {
  it('makes the cover clickable when onCoverClick is provided', () => {
    const onCoverClick = vi.fn();
    const { getByLabelText } = render(
      <AudioHero src="blob:audio" title="Song" onCoverClick={onCoverClick} />,
    );
    const btn = getByLabelText('Change cover');
    fireEvent.click(btn);
    expect(onCoverClick).toHaveBeenCalledTimes(1);
  });

  it('renders a non-interactive cover when onCoverClick is absent', () => {
    const { queryByLabelText } = render(
      <AudioHero src="blob:audio" title="Song" />,
    );
    expect(queryByLabelText('Change cover')).toBeNull();
  });
});
