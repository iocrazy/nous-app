import { apiClient } from './apiClient';

/**
 * A single lyric line. Time-sync fields are optional and currently unused by
 * the display tab (sync to playback is deferred).
 */
export interface LyricLine {
  text: string;
  line_start_ms?: number;
}

export interface MediaLyrics {
  lrc: string;
  lines: LyricLine[];
}

/**
 * Fetch lyrics for a media item.
 * GET /api/v1/media/{mediaId}/lyrics (auth required).
 * Throws ApiError on a non-OK response (handled by apiClient).
 */
export const getMediaLyrics = async (mediaId: string): Promise<MediaLyrics> => {
  const data = await apiClient.get<MediaLyrics>(
    `/api/v1/media/${mediaId}/lyrics`,
  );
  return {
    lrc: data.lrc ?? '',
    lines: data.lines ?? [],
  };
};
