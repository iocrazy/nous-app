import React from 'react';
import { ListMusic, Music } from 'lucide-react';

interface PlaylistIslandProps {
  title?: string;
  author?: string;
  coverUrl?: string | null;
}

// Stub placeholder rows shown below the current track.
// Dimmed and non-interactive — real queue data is COMING SOON.
const PLACEHOLDER_ROWS: { duration: string }[] = [
  { duration: '3:21' },
  { duration: '4:07' },
  { duration: '2:58' },
];

/**
 * PlaylistIsland — v2 P4 audio detail right info-island content.
 *
 * Renders a "Playlist" header + the current track row (with an equalizer
 * animation markup) + a few dimmed placeholder queue rows.
 *
 * COMING SOON: no real queue data or behavior yet. The parent portal is
 * responsible for sizing; this component fills 100% height.
 *
 * Constraint: no zinc-* classes; use ink-* / line tokens.
 */
export const PlaylistIsland: React.FC<PlaylistIslandProps> = ({ title, author, coverUrl }) => {
  return (
    <div className="h-full flex flex-col min-h-0">
      {/* ── Header ─────────────────────────────────────────────── */}
      <div className="flex items-center justify-between gap-2 px-4 pt-4 pb-3 shrink-0">
        <div className="flex items-center gap-2">
          <ListMusic size={16} className="text-content-2 shrink-0" />
          <span className="text-sm font-semibold text-content tracking-wide">Playlist</span>
        </div>
        {/* COMING SOON badge */}
        <span className="text-[10px] font-semibold uppercase tracking-widest text-content-3 border border-line rounded-full px-2 py-0.5 leading-none">
          COMING SOON
        </span>
      </div>

      {/* ── Divider ─────────────────────────────────────────────── */}
      <div className="h-px bg-line mx-4 shrink-0" />

      {/* ── Current track ───────────────────────────────────────── */}
      <div
        className={
          'flex items-center gap-3 mx-3 mt-3 px-3 py-3 rounded-xl shrink-0 ' +
          'bg-[rgba(var(--tint,99,102,241),0.08)] border border-[rgba(var(--tint,99,102,241),0.22)]'
        }
      >
        {/* Cover thumbnail */}
        <div className="w-10 h-10 rounded-lg overflow-hidden shrink-0 flex items-center justify-center bg-[rgba(var(--tint,99,102,241),0.15)]">
          {coverUrl ? (
            <img
              src={coverUrl}
              alt={title ?? 'Cover'}
              className="w-full h-full object-cover"
              onError={(e) => {
                (e.currentTarget as HTMLImageElement).style.display = 'none';
              }}
            />
          ) : (
            <Music size={18} className="text-[rgba(var(--tint,99,102,241),0.7)]" />
          )}
        </div>

        {/* Title + author */}
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-content truncate">{title}</p>
          {author && (
            <p className="text-xs text-content-2 truncate mt-0.5">{author}</p>
          )}
        </div>

        {/* Equalizer animation markup — inert until Task 3/5 adds .eq CSS */}
        <span className="eq shrink-0" aria-hidden>
          <i /><i /><i /><i />
        </span>
      </div>

      {/* ── Queue placeholder rows (disabled) ───────────────────── */}
      <div className="flex-1 min-h-0 overflow-hidden mt-2 px-3 pb-4 flex flex-col gap-1">
        {PLACEHOLDER_ROWS.map((row, idx) => (
          <div
            key={idx}
            className="flex items-center gap-3 px-3 py-2.5 rounded-lg opacity-40 pointer-events-none"
          >
            {/* Thumb skeleton */}
            <div className="w-9 h-9 rounded-md shrink-0 bg-line" />
            {/* Text skeletons */}
            <div className="flex-1 min-w-0 flex flex-col gap-1.5">
              <div className="h-2.5 rounded bg-line w-3/4" />
              <div className="h-2 rounded bg-line w-1/2" />
            </div>
            {/* Duration */}
            <span className="text-xs text-content-3 shrink-0 tabular-nums">{row.duration}</span>
          </div>
        ))}
      </div>
    </div>
  );
};
