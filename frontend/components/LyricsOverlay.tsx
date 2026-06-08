import React, { useEffect } from 'react';
import { ChevronDown, Music } from 'lucide-react';
import SodaLyricsTab from './SodaLyricsTab';
import { LyricsView } from './LyricsView';
import type { LyricLine } from '../services/lyricsService';
import type { SodaTheme } from '../utils/sodaTheme';

interface LyricsOverlayProps {
  /** Download path: media item whose lyrics SodaLyricsTab fetches by id. */
  mediaId?: string;
  /** Upload path: lyric lines already in hand (no mediaId / no fetch). When
   * provided, the overlay renders these directly via LyricsView instead of
   * delegating to SodaLyricsTab. */
  lines?: LyricLine[];
  currentTime?: number;
  title?: string;
  subtitle?: string;
  coverUrl?: string;
  theme: SodaTheme;
  onClose: () => void;
  /** Source platform — forwarded to SodaLyricsTab to gate the Fetch Lyrics action. */
  sourcePlatform?: string;
}

/**
 * Full-screen synced-lyrics overlay (Soda Music style). Opens when the inline
 * lyric preview on the mobile audio player is tapped. Renders the track's themed
 * gradient backdrop, a header (close arrow + cover thumbnail + title/artist),
 * and the scrolling synced lyric list (reusing SodaLyricsTab).
 */
export const LyricsOverlay: React.FC<LyricsOverlayProps> = ({
  mediaId,
  lines,
  currentTime,
  title,
  subtitle,
  coverUrl,
  theme,
  onClose,
  sourcePlatform,
}) => {
  // Upload path: lyrics passed in directly (no mediaId, no fetch). Falls back to
  // the SodaLyricsTab download path (fetch by mediaId + qishui Fetch button) when
  // no lines are supplied.
  const usePassedLines = lines !== undefined;
  // Nice-to-have: lock body scroll while the overlay is open.
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col"
      style={{ background: theme.gradientCss }}
      role="dialog"
      aria-modal="true"
      aria-label="Lyrics"
    >
      {/* Header: close + cover thumbnail + title/artist */}
      <div
        // `fixed inset-0` covers the status bar, but on a freshly-rendered fixed
        // overlay iOS resolves env(safe-area-inset-top) to 0 until the first
        // scroll — which jams the back arrow / cover under the status bar. The
        // 44px floor guarantees clearance on mount; env() corrects upward once
        // resolved (notch / dynamic island).
        className="flex items-center gap-3 px-4 pt-[calc(max(env(safe-area-inset-top),44px)+8px)] pb-3 shrink-0"
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="Close lyrics"
          className="p-1.5 -ml-1.5 rounded-full text-white/90 hover:bg-white/10 active:bg-white/20 transition-colors shrink-0"
        >
          <ChevronDown size={26} />
        </button>
        {coverUrl ? (
          <img
            src={coverUrl}
            alt={title || 'Cover'}
            className="w-10 h-10 rounded-lg object-cover shadow-lg shrink-0"
            onError={(e) => {
              (e.currentTarget as HTMLImageElement).style.display = 'none';
            }}
          />
        ) : (
          <div
            className="w-10 h-10 rounded-lg flex items-center justify-center shadow-lg shrink-0"
            style={{ backgroundColor: theme.accentSoft }}
          >
            <Music size={18} style={{ color: theme.onAccent }} />
          </div>
        )}
        <div className="min-w-0 flex-1">
          {title && (
            <p className="text-sm font-semibold text-white truncate" title={title}>
              {title}
            </p>
          )}
          {subtitle && (
            <p className="text-xs text-white/70 truncate" title={subtitle}>
              {subtitle}
            </p>
          )}
        </div>
      </div>

      {/* Body: scrolling synced lyrics */}
      <div className="flex-1 min-h-0 overflow-y-auto px-4 pb-[calc(env(safe-area-inset-bottom)+24px)]">
        {usePassedLines ? (
          // Upload path: render the in-hand lines directly. Match the bare
          // top/bottom spacing SodaLyricsTab uses for its bare variant so the
          // first line sits under the header and the last can scroll to center.
          <div className="pt-4 pb-[55vh] space-y-6">
            <LyricsView lines={lines} currentTime={currentTime} theme={theme} variant="bare" />
          </div>
        ) : (
          <SodaLyricsTab mediaId={mediaId as string} currentTime={currentTime} theme={theme} variant="bare" sourcePlatform={sourcePlatform} />
        )}
      </div>
    </div>
  );
};
