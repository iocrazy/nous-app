import { describe, expect, it, vi } from 'vitest';

vi.mock('./apiClient', () => ({
  apiClient: { get: vi.fn(), post: vi.fn() },
}));

import { apiClient } from './apiClient';
import { getMediaLyrics, toLyricLines } from './lyricsService';
import type { MediaLyrics } from '../types/api';

// Real wire shape of GET /media/{id}/lyrics for a Soda track (all six keys
// per line, as `lyrics_payload_from_track` stores them)…
const sodaBody: MediaLyrics = {
  lrc: '[00:01.00]Hello world',
  lines: [
    {
      line_start_ms: 1000,
      line_duration_ms: 2000,
      line_end_ms: 3000,
      text: 'Hello world',
      tokens: [{ text: 'Hello', offset_ms: 0, duration_ms: 500, flag: 0, start_ms: 1000, end_ms: 1500 }],
      raw: '<0,500,0>Hello',
    },
  ],
};

describe('lyricsService', () => {
  it('narrows API lines to what the players render', async () => {
    vi.mocked(apiClient.get).mockResolvedValue(sodaBody);
    const out = await getMediaLyrics('7');
    expect(out.lrc).toBe('[00:01.00]Hello world');
    expect(out.lines).toEqual([{ text: 'Hello world', line_start_ms: 1000 }]);
  });

  it('keeps a hand-edited historical row readable', () => {
    // …and of a hand-edited row: keys missing, extra keys, a line with no text.
    const lines: MediaLyrics['lines'] = [
      { text: 'only text' },
      { text: 'b', line_start_ms: 5, note: 'hand-edited' },
      { line_start_ms: 9 },
    ];
    expect(toLyricLines(lines)).toEqual([
      { text: 'only text', line_start_ms: undefined },
      { text: 'b', line_start_ms: 5 },
    ]);
  });
});
