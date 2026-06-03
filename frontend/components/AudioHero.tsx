import React, { useEffect, useState } from 'react';
import { Music } from 'lucide-react';
import { AudioWaveformPlayer } from './AudioWaveformPlayer';
import { LyricsOverlay } from './LyricsOverlay';
import { getMediaLyrics, type LyricLine } from '../services/lyricsService';
import { buildSodaTheme, type SodaTheme } from '../utils/sodaTheme';

interface AudioHeroProps {
  src: string;
  /** Filename — not displayed; forwarded to the player for accessibility. */
  title?: string;
  /** Album/cover URL; falls back to a themed Music placeholder when absent. */
  coverUrl?: string;
  duration?: number;
  chorusStartSec?: number;
  onTimeUpdate?: (seconds: number) => void;
  /**
   * Track's Soda palette. When omitted, a stable per-track color is derived
   * from the src so every audio detail view (downloads / uploads / share)
   * renders with the SAME layout + a consistent color treatment.
   */
  theme?: SodaTheme;
  /**
   * Optional artist / author shown under the title on the mobile full-screen
   * player. Only rendered when present.
   */
  subtitle?: string;
  /**
   * Media id used to fetch synced lyrics for the mobile inline preview + the
   * tap-to-expand full-screen lyrics overlay. When absent, no lyrics UI renders.
   */
  mediaId?: string;
  /**
   * Current playback position (seconds). Drives the active lyric line in the
   * inline preview and the overlay. When absent, the first line is previewed.
   */
  currentTime?: number;
  /** Source platform — gates the "Fetch Lyrics" action (only qishui today). */
  sourcePlatform?: string;
}

/**
 * Shared audio detail "hero": a gradient backdrop + large rounded cover (or a
 * themed placeholder) above a centered waveform player. Used by both the
 * download detail page and the resource (upload) detail page so audio looks
 * identical everywhere.
 */
export const AudioHero: React.FC<AudioHeroProps> = ({
  src,
  title,
  coverUrl,
  duration,
  chorusStartSec,
  onTimeUpdate,
  theme,
  subtitle,
  mediaId,
  currentTime,
  sourcePlatform,
}) => {
  const t = theme ?? buildSodaTheme(null, src);
  const canFetchLyrics = sourcePlatform === 'qishui';

  // Lyrics for the mobile inline preview. Fetched once per media id; the
  // overlay reuses SodaLyricsTab (which fetches its own copy when opened).
  const [lyricLines, setLyricLines] = useState<LyricLine[]>([]);
  const [showLyrics, setShowLyrics] = useState(false);

  useEffect(() => {
    if (!mediaId) {
      setLyricLines([]);
      return;
    }
    let cancelled = false;
    getMediaLyrics(mediaId)
      .then((data) => {
        if (!cancelled) setLyricLines(data.lines);
      })
      .catch((err) => {
        if (!cancelled) {
          console.error('Failed to load lyrics for inline preview:', err);
          setLyricLines([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [mediaId]);

  // Active line = last line whose start time has passed; fallback 0 when no
  // playback time yet. Mirrors SodaLyricsTab's activeIndex logic.
  let activeIndex = 0;
  if (typeof currentTime === 'number' && currentTime > 0) {
    for (let i = 0; i < lyricLines.length; i++) {
      const startMs = lyricLines[i].line_start_ms;
      if (typeof startMs === 'number' && startMs / 1000 <= currentTime) {
        activeIndex = i;
      } else if (typeof startMs === 'number') {
        break;
      }
    }
  }
  const activeLine = lyricLines[activeIndex]?.text?.trim() || '';
  const nextLine = lyricLines[activeIndex + 1]?.text?.trim() || '';
  const hasLyrics = mediaId && lyricLines.length > 0 && activeLine.length > 0;

  return (
    <div
      className="w-full h-full flex flex-col items-center justify-center gap-8 sm:gap-6 px-6 py-10 sm:py-8"
      style={{ background: t.gradientCss }}
    >
      {coverUrl ? (
        <img
          src={coverUrl}
          alt={title || 'Cover'}
          className="w-64 h-64 sm:w-56 sm:h-56 rounded-2xl object-cover shadow-2xl shrink-0"
          onError={(e) => {
            (e.currentTarget as HTMLImageElement).style.display = 'none';
          }}
        />
      ) : (
        <div
          className="w-64 h-64 sm:w-56 sm:h-56 rounded-2xl flex items-center justify-center shadow-2xl shrink-0"
          style={{ backgroundColor: t.accentSoft }}
        >
          <Music size={64} style={{ color: t.onAccent }} />
        </div>
      )}
      {/* Title + artist — mobile only (full-screen player look); hidden on
          tablet/desktop where the metadata panel carries this info. */}
      {(title || subtitle) && (
        <div className="sm:hidden w-full max-w-md text-center px-2 shrink-0">
          {title && (
            <h2 className="text-xl font-bold text-white truncate" title={title}>
              {title}
            </h2>
          )}
          {subtitle && (
            <p className="mt-1 text-sm text-white/70 truncate" title={subtitle}>
              {subtitle}
            </p>
          )}
        </div>
      )}
      {/* Inline lyric preview — mobile only, the visual centerpiece between the
          title/artist and the waveform. Tapping opens the full-screen synced-
          lyrics overlay. Hidden entirely when no lyrics are available. */}
      {hasLyrics ? (
        <button
          type="button"
          onClick={() => setShowLyrics(true)}
          aria-label="Open lyrics"
          className="sm:hidden w-full max-w-md px-4 text-center shrink-0 focus:outline-none"
        >
          <p className="text-lg font-semibold text-white leading-snug line-clamp-2">{activeLine}</p>
          {nextLine && (
            <p className="mt-1 text-sm text-white/45 truncate">{nextLine}</p>
          )}
        </button>
      ) : canFetchLyrics ? (
        // No lyrics yet — reachable entry to the overlay (Fetch Lyrics lives there).
        <button
          type="button"
          onClick={() => setShowLyrics(true)}
          className="sm:hidden flex items-center gap-1.5 px-4 py-1.5 rounded-full bg-white/10 hover:bg-white/20 text-sm font-medium text-white/80 transition-colors shrink-0"
        >
          <Music size={14} /> Fetch Lyrics
        </button>
      ) : null}
      <div className="w-full max-w-2xl flex-1 min-h-[96px] sm:min-h-[160px]">
        <AudioWaveformPlayer
          src={src}
          filename={title || 'Audio'}
          duration={duration}
          chorusStartSec={chorusStartSec}
          onTimeUpdate={onTimeUpdate}
          theme={t}
        />
      </div>
      {showLyrics && mediaId && (
        <LyricsOverlay
          mediaId={mediaId}
          currentTime={currentTime}
          title={title}
          subtitle={subtitle}
          coverUrl={coverUrl}
          theme={t}
          onClose={() => setShowLyrics(false)}
          sourcePlatform={sourcePlatform}
        />
      )}
    </div>
  );
};
