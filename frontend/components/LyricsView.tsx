import React, { useEffect, useRef } from 'react';
import type { SodaTheme } from '../utils/sodaTheme';

/** A single lyric line. `line_start_ms` drives time-sync highlighting. */
export interface LyricLine {
  text: string;
  line_start_ms?: number | null;
}

interface LyricsViewProps {
  lines: LyricLine[];
  /** Current playback position (seconds). When provided (>0), the matching
   * lyric line is highlighted and scrolled into view. */
  currentTime?: number;
  /** Track's own Soda palette for active / inactive lyric coloring. */
  theme?: SodaTheme;
  /**
   * `'card'` (default) = compact scrollable list (desktop panel inner body).
   * `'bare'` = large centered lines flowing directly on the backdrop
   * (full-screen Soda-style lyrics page).
   */
  variant?: 'card' | 'bare';
}

/**
 * Dumb, reusable renderer for a synced lyric list. Highlights and auto-scrolls
 * the active line based on `currentTime`. Owns no data fetching or card chrome
 * — callers supply the lines and (optionally) the surrounding header / wrapper.
 */
export const LyricsView: React.FC<LyricsViewProps> = ({ lines, currentTime, theme, variant = 'card' }) => {
  const isBare = variant === 'bare';
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
    const el = activeLineRef.current;
    // Guard for non-browser envs (jsdom) where scrollIntoView is unimplemented.
    if (typeof el?.scrollIntoView === 'function') {
      el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }, [activeIndex]);

  // Bare = full-screen Soda lyrics: large lines flowing on the gradient, active
  // line bold + themed, others dimmed. Rendered as a fragment so the caller's
  // wrapper (e.g. the Copy button row) owns spacing and scroll.
  if (isBare) {
    return (
      <>
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
      </>
    );
  }

  return (
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
            } ${theme ? '' : isActive ? 'text-white' : synced ? 'text-ink-500' : 'text-ink-300'}`}
          >
            {line.text || ' '}
          </p>
        );
      })}
    </div>
  );
};
