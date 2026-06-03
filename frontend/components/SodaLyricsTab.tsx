import { useEffect, useRef, useState } from 'react';
import { Loader2, Music, AlertCircle, CloudDownload, Copy, Check } from 'lucide-react';
import { getMediaLyrics, fetchMediaLyrics, type LyricLine } from '../services/lyricsService';
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
  /** Source platform — gates the "Fetch Lyrics" action. Only platforms with a
   * lyrics-fetch path (today: `qishui`) show the button when lyrics are absent. */
  sourcePlatform?: string;
}

/**
 * Displays the lyrics for an audio media item as a scrollable list of lines.
 * When `currentTime` is provided, the active line is highlighted and the list
 * auto-scrolls to keep it centered. Renders statically when it's undefined.
 */
const SodaLyricsTab = ({ mediaId, currentTime, theme, variant = 'card', sourcePlatform }: SodaLyricsTabProps) => {
  const isBare = variant === 'bare';
  const [lines, setLines] = useState<LyricLine[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [fetching, setFetching] = useState(false);
  const [fetchTried, setFetchTried] = useState(false);
  const [copied, setCopied] = useState(false);
  const activeLineRef = useRef<HTMLParagraphElement>(null);

  const canFetch = sourcePlatform === 'qishui';

  // Re-fetch lyrics from the source (track parsed before lyric support, or the
  // source returned none at parse time). Persists server-side + fills the view.
  const handleFetchLyrics = async () => {
    setFetching(true);
    setError(null);
    try {
      const data = await fetchMediaLyrics(mediaId);
      setLines(data.lines);
      setFetchTried(true);
    } catch (err) {
      console.error('Failed to fetch lyrics:', err);
      setError(err instanceof Error ? err.message : 'Failed to fetch lyrics');
    } finally {
      setFetching(false);
    }
  };

  // Copy the readable lyric text (one line per row) to the clipboard.
  const handleCopyLyrics = async () => {
    const text = lines.map((l) => l.text).filter(Boolean).join('\n');
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (err) {
      console.error('Failed to copy lyrics:', err);
    }
  };

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
    setFetchTried(false);

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
    // After a fetch that came back empty, the track genuinely has no lyrics
    // (e.g. instrumental) — say so and don't offer the button again.
    const label = fetchTried ? 'No lyrics found for this track' : 'No lyrics available';
    const fetchBtn = canFetch && !fetchTried && (
      <button
        type="button"
        onClick={handleFetchLyrics}
        disabled={fetching}
        className={
          isBare
            ? 'mt-1 flex items-center gap-1.5 px-4 py-2 rounded-full bg-white/15 hover:bg-white/25 text-sm font-medium text-white transition-colors disabled:opacity-60'
            : 'mt-1 flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-xs font-medium text-zinc-200 transition-colors disabled:opacity-60'
        }
      >
        {fetching ? <Loader2 size={14} className="animate-spin" /> : <CloudDownload size={14} />}
        {fetching ? 'Fetching...' : 'Fetch Lyrics'}
      </button>
    );
    if (isBare) {
      return (
        <div className="flex flex-col items-center justify-center gap-2 py-20 text-white/55">
          <Music size={20} />
          <span className="text-sm">{label}</span>
          {fetchBtn}
        </div>
      );
    }
    return (
      <div className="p-6 bg-zinc-900 rounded-xl border border-zinc-800">
        <div className="flex flex-col items-center justify-center gap-2 text-zinc-500">
          <Music size={20} />
          <span className="text-sm">{label}</span>
          {fetchBtn}
        </div>
      </div>
    );
  }

  // Bare = full-screen Soda lyrics: no card chrome, large lines flowing on the
  // gradient, active line bold + themed, others dimmed. The overlay owns scroll.
  if (isBare) {
    return (
      // Small top gap (lyrics start right under the header — no big blank), with
      // generous bottom space so the last lines can still scroll up to center.
      <div className="pt-4 pb-[55vh] space-y-6">
        <button
          type="button"
          onClick={handleCopyLyrics}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-white/10 hover:bg-white/20 text-xs font-medium text-white/80 transition-colors"
          title="Copy lyrics"
        >
          {copied ? <Check size={13} className="text-emerald-300" /> : <Copy size={13} />}
          {copied ? 'Copied' : 'Copy Lyrics'}
        </button>
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
        <button
          type="button"
          onClick={handleCopyLyrics}
          className="ml-auto flex items-center gap-1 px-2 py-1 rounded text-xs text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 transition-colors"
          title="Copy lyrics"
        >
          {copied ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
          {copied ? 'Copied' : 'Copy Lyrics'}
        </button>
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
