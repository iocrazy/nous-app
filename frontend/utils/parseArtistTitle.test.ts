import { describe, it, expect } from 'vitest';
import { parseArtistTitle } from './parseArtistTitle';

describe('parseArtistTitle', () => {
  it('splits "Artist - Title.mp3"', () => {
    expect(
      parseArtistTitle('Biboulakis - Is That Too Much to Ask (feat. Nina Zeitlin).mp3'),
    ).toEqual({ artist: 'Biboulakis', title: 'Is That Too Much to Ask (feat. Nina Zeitlin)' });
  });

  it('no " - " → title only', () => {
    expect(parseArtistTitle('just-a-song.mp3')).toEqual({ title: 'just-a-song' });
  });

  it('splits on the FIRST " - " only', () => {
    expect(parseArtistTitle('A - B - C.wav')).toEqual({ artist: 'A', title: 'B - C' });
  });

  it('does not split a date-like stem with no spaces', () => {
    expect(parseArtistTitle('2024-01-01.m4a')).toEqual({ title: '2024-01-01' });
  });

  it('handles no extension', () => {
    expect(parseArtistTitle('X - Y')).toEqual({ artist: 'X', title: 'Y' });
  });

  it('empty side falls back to title-only', () => {
    expect(parseArtistTitle(' - Title.mp3')).toEqual({ title: '- Title' });
  });
});
