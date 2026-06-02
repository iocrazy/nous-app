import { useEffect, useRef, useState } from 'react';
import { Loader2, Music, AlertCircle } from 'lucide-react';
import { getMediaLyrics, type LyricLine } from '../services/lyricsService';
import type { SodaTheme } from '../utils/sodaTheme';

interface SodaLyricsTabProps {
  mediaId: string;
  /** Current playback position (seconds). When provided (>0), the matching
   * lyric line is highlighted and scrolled into view. */
  currentTime?: number;
  /** Track's own Soda palette for active / inactive lyric coloring. */
  theme?: SodaTheme;
  /**
   * `'card'` (default) = the zinc card with a "Lyrics" header (desktop panel).
   * `'bare'` = no card / no header, large centered lines flowing directly on
   * the backdrop (full-screen Soda-style lyrics page).
   */
  variant?: 'card' | 'bare';
}

/**
 * Displays the lyrics for an audio media item as a scrollable list of lines.
 * When `currentTime` is provided, the active line is highlighted and the list
 * auto-scrolls to keep it centered. Renders statically when it's undefined.
 */
const SodaLyricsTab = ({ mediaId, currentTime, theme, variant = 'card' }: SodaLyricsTabProps) => {
  const isBare = variant === 'bare';
  const [lines, setLines] = useState<LyricLine[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const activeLineRef = useRef<HTMLParagraphElement>(null);

  // Active line = last line whose start time has passed. -1 when no sync.
  const synced = typeof currentTime === 'number' && currentTime > 0;
  let activeIndex = -1;
  if (synced) {
    for (let i = 0; i < lines.length; i++) {
      const startMs = lines[i].line_start_ms;
      if (typeof startMs === 'number' && startMs / 1000 <= currentTime) {
        activeIndex = i;
      } else if (typeof startMs === 'number') {
        break;
      }
    }
  }

  // Autoscroll the active line into view whenever it changes.
  useEffect(() => {
    if (activeIndex < 0) return;
    activeLineRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }, [activeIndex]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    getMediaLyrics(mediaId)
      .then((data) => {
        if (cancelled) return;
        setLines(data.lines);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('Failed to load lyrics:', err);
        setError(err instanceof Error ? err.message : 'Failed to load lyrics');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [mediaId]);

  if (loading) {
    if (isBare) {
      return (
        <div className="flex items-center justify-center gap-2 py-16 text-white/60">
          <Loader2 size={16} className="animate-spin" />
          <span className="text-sm">Loading lyrics...</span>
        </div>
      );
    }
    return (
      <div className="p-4 bg-zinc-900 rounded-xl border border-zinc-800">
        <div className="flex items-center justify-center gap-2 text-zinc-500">
          <Loader2 size={16} className="animate-spin" />
          <span className="text-sm">Loading lyrics...</span>
        </div>
      </div>
    );
  }

  if (error) {
    if (isBare) {
      return (
        <div className="flex items-center justify-center gap-2 py-16 text-red-300">
          <AlertCircle size={16} />
          <span className="text-sm">{error}</span>
        </div>
      );
    }
    return (
      <div className="p-4 bg-zinc-900 rounded-xl border border-zinc-800">
        <div className="flex items-center justify-center gap-2 text-red-400">
          <AlertCircle size={16} />
          <span className="text-sm">{error}</span>
        </div>
      </div>
    );
  }

  if (lines.length === 0) {
    if (isBare) {
      return (
        <div className="flex flex-col items-center justify-center gap-2 py-20 text-white/55">
          <Music size={20} />
          <span className="text-sm">No lyrics available</span>
        </div>
      );
    }
    return (
      <div className="p-6 bg-zinc-900 rounded-xl border border-zinc-800">
        <div className="flex flex-col items-center justify-center gap-2 text-zinc-500">
          <Music size={20} />
          <span className="text-sm">No lyrics available</span>
        </div>
      </div>
    );
  }

  // Bare = full-screen Soda lyrics: no card chrome, large lines flowing on the
  // gradient, active line bold + themed, others dimmed. The overlay owns scroll.
  if (isBare) {
    return (
      <div className="py-[35vh] space-y-6">
        {lines.map((line, index) => {
          const isActive = index === activeIndex;
          const themedStyle = theme
            ? { color: isActive ? theme.lyricActive : theme.lyricNormal }
            : undefined;
          return (
            <p
              key={index}
              ref={isActive ? activeLineRef : undefined}
              style={themedStyle}
              className={`leading-relaxed transition-all duration-300 ${
                isActive ? 'text-[22px] font-bold' : 'text-[17px] font-medium opacity-60'
              } ${theme ? '' : isActive ? 'text-white' : 'text-white/55'}`}
            >
              {line.text || ' '}
            </p>
          );
        })}
      </div>
    );
  }

  return (
    <div className="bg-zinc-900 rounded-xl border border-zinc-800 overflow-hidden">
      <div className="flex items-center gap-2 p-3 border-b border-zinc-800">
        <Music
          size={16}
          className={theme ? '' : 'text-zinc-300'}
          style={theme ? { color: theme.accent } : undefined}
        />
        <span className="text-sm font-medium text-white">Lyrics</span>
      </div>
      <div className="max-h-96 overflow-y-auto p-3 space-y-1">
        {lines.map((line, index) => {
          const isActive = index === activeIndex;
          // When a theme is present, lyric colors come from the track palette;
          // otherwise keep the neutral zinc/white classes.
          const themedStyle = theme
            ? { color: isActive ? theme.lyricActive : synced ? theme.lyricNormal : undefined }
            : undefined;
          return (
            <p
              key={index}
              ref={isActive ? activeLineRef : undefined}
              style={themedStyle}
              className={`leading-relaxed transition-all duration-200 ${
                isActive ? 'text-base font-semibold' : 'text-sm font-medium'
              } ${theme ? '' : isActive ? 'text-white' : synced ? 'text-zinc-500' : 'text-zinc-300'}`}
            >
              {line.text || ' '}
            </p>
          );
        })}
      </div>
    </div>
  );
};

export default SodaLyricsTab;
