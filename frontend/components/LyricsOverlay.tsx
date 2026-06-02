import React, { useEffect } from 'react';
import { ChevronDown, Music } from 'lucide-react';
import SodaLyricsTab from './SodaLyricsTab';
import type { SodaTheme } from '../utils/sodaTheme';

interface LyricsOverlayProps {
  mediaId: string;
  currentTime?: number;
  title?: string;
  subtitle?: string;
  coverUrl?: string;
  theme: SodaTheme;
  onClose: () => void;
}

/**
 * Full-screen synced-lyrics overlay (Soda Music style). Opens when the inline
 * lyric preview on the mobile audio player is tapped. Renders the track's themed
 * gradient backdrop, a header (close arrow + cover thumbnail + title/artist),
 * and the scrolling synced lyric list (reusing SodaLyricsTab).
 */
export const LyricsOverlay: React.FC<LyricsOverlayProps> = ({
  mediaId,
  currentTime,
  title,
  subtitle,
  coverUrl,
  theme,
  onClose,
}) => {
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
        className="flex items-center gap-3 px-4 pt-[calc(env(safe-area-inset-top)+12px)] pb-3 shrink-0"
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
        <SodaLyricsTab mediaId={mediaId} currentTime={currentTime} theme={theme} variant="bare" />
      </div>
    </div>
  );
};
