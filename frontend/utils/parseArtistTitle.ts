export interface ArtistTitle {
  artist?: string;
  title: string;
}

const AUDIO_EXT = /\.(mp3|m4a|wav|flac|aac|ogg|opus|wma|aiff?)$/i;

/**
 * Parse a "Artist - Title" audio filename into display fields. Splits on the
 * FIRST " - " (space-hyphen-space) only; a stem with no " - " (or an empty
 * side) yields title-only. Display-only — never persisted.
 */
export function parseArtistTitle(filename: string): ArtistTitle {
  const stem = (filename || '').replace(AUDIO_EXT, '').trim();
  const i = stem.indexOf(' - ');
  if (i < 0) return { title: stem };
  const artist = stem.slice(0, i).trim();
  const title = stem.slice(i + 3).trim();
  if (!artist || !title) return { title: stem };
  return { artist, title };
}
