import { apiClient } from './apiClient';
import type { MediaLyricLine, MediaLyrics } from '../types/api';

export type { MediaLyrics, MediaLyricLine } from '../types/api';

/**
 * A lyric line as the display components read it. Not an API shape: the
 * players feed it from two sources — `GET /media/{id}/lyrics` lines (narrowed
 * by `toLyricLines` below) and a resource's parsed `lyrics_json`
 * (`utils/resourceLyrics.ts`).
 */
export interface LyricLine {
  text: string;
  line_start_ms?: number;
}

/** `MediaLyrics` with its lines narrowed to what the players render. */
export type DisplayLyrics = Omit<MediaLyrics, 'lines'> & { lines: LyricLine[] };

/**
 * The API's line keys are all optional: the read side is lenient so a
 * hand-edited historical row still loads (same ruling as `lyrics_json`). A
 * line without text has nothing to show, so it is dropped here.
 */
export const toLyricLines = (lines: MediaLyricLine[]): LyricLine[] =>
  lines.flatMap((line) =>
    typeof line.text === 'string'
      ? [{ text: line.text, line_start_ms: line.line_start_ms ?? undefined }]
      : [],
  );

const toDisplay = (data: MediaLyrics): DisplayLyrics => ({
  ...data,
  lines: toLyricLines(data.lines),
});

/**
 * Fetch lyrics for a media item.
 * GET /api/v1/media/{mediaId}/lyrics (auth required).
 * Empty lyrics come back as `{ lrc: "", lines: [] }`, never null.
 * Throws ApiError on a non-OK response (handled by apiClient).
 */
export const getMediaLyrics = async (mediaId: string): Promise<DisplayLyrics> =>
  toDisplay(await apiClient.get<MediaLyrics>(`/api/v1/media/${mediaId}/lyrics`));

/**
 * Re-fetch lyrics from the source platform and persist them.
 * POST /api/v1/media/{mediaId}/lyrics/fetch (auth required).
 * For tracks missing lyrics (legacy parse / source returned none). Only the
 * qishui (Soda) platform is wired today; others return 422.
 * Resolves with the (possibly still-empty) lyrics; throws ApiError otherwise.
 */
export const fetchMediaLyrics = async (mediaId: string): Promise<DisplayLyrics> =>
  toDisplay(await apiClient.post<MediaLyrics>(`/api/v1/media/${mediaId}/lyrics/fetch`));
