import { useEffect, useRef, useState } from 'react';
import { Loader2, Music, AlertCircle } from 'lucide-react';
import { getMediaLyrics, type LyricLine } from '../services/lyricsService';

interface SodaLyricsTabProps {
  mediaId: string;
  /** Current playback position (seconds). When provided (>0), the matching
   * lyric line is highlighted and scrolled into view. */
  currentTime?: number;
}

/**
 * Displays the lyrics for an audio media item as a scrollable list of lines.
 * When `currentTime` is provided, the active line is highlighted and the list
 * auto-scrolls to keep it centered. Renders statically when it's undefined.
 */
const SodaLyricsTab = ({ mediaId, currentTime }: SodaLyricsTabProps) => {
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
    return (
      <div className="p-6 bg-zinc-900 rounded-xl border border-zinc-800">
        <div className="flex flex-col items-center justify-center gap-2 text-zinc-500">
          <Music size={20} />
          <span className="text-sm">No lyrics available</span>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-zinc-900 rounded-xl border border-zinc-800 overflow-hidden">
      <div className="flex items-center gap-2 p-3 border-b border-zinc-800">
        <Music size={16} className="text-indigo-400" />
        <span className="text-sm font-medium text-white">Lyrics</span>
      </div>
      <div className="max-h-96 overflow-y-auto p-3 space-y-1">
        {lines.map((line, index) => {
          const isActive = index === activeIndex;
          return (
            <p
              key={index}
              ref={isActive ? activeLineRef : undefined}
              className={`text-sm leading-relaxed transition-colors ${
                isActive
                  ? 'text-white font-medium'
                  : synced
                    ? 'text-zinc-500'
                    : 'text-zinc-300'
              }`}
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
