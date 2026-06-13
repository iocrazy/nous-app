import React, { useState, useEffect, useCallback } from 'react';
import { Music, Heart, MessageCircle, Bookmark, MoreVertical } from 'lucide-react';
import type { Tag } from '../types';
import { buildSodaTheme, type SodaTheme } from '../utils/sodaTheme';
import type { LyricLine } from '../services/lyricsService';
import { fetchResourceTags, addResourceTag, removeResourceTag } from '../services/resourceService';
import { fetchAllTags, createTag } from '../services/unifiedTagService';
import { AudioWaveformPlayer } from './AudioWaveformPlayer';
import { LyricsOverlay } from './LyricsOverlay';
import { EagleTagPicker } from './EagleTagPicker';
import { RatingStars } from './detail/DetailCardKit';

/**
 * MobileAudioShell — the presentational immersive mobile-audio layout.
 *
 * Pure view-model in, no source-specific (Video / upload) coupling: it renders
 * ONE continuous gradient surface (the track's Soda palette), scrollable, in
 * this top→bottom order:
 *   1. (top bar lives in the caller)
 *   2. large rounded album cover
 *   3. title (+ optional artist line)
 *   4. lyric couplet (current + next) — tap → full lyrics overlay
 *      (collapses when there are no synced lyrics; a "Fetch Lyrics" entry takes
 *      its place for qishui tracks that can still fetch)
 *   5. stats + actions row: social stats (filled icons + count) LEFT,
 *      speed chip "1x" + ⋮ overflow menu RIGHT
 *   6. tags (editable EagleTagPicker, keyed on `resourceId`)
 *   7. color block: the waveform player (or a caller-supplied no-audio prompt)
 *   8. rating + notes
 *
 * MobileAudioScreen (download) and a later upload adapter both map their own
 * props onto this shell — keeping the rendered output identical for downloads.
 */

/** One ⋮ overflow menu entry. `href` → renders an `<a>`; otherwise a `<button>`.
 * `dividerBefore` draws the group separator line above the entry — the adapter
 * sets it to reproduce the download menu's exact dividers. */
export interface MobileAudioMenuItem {
  key: string;
  label: string;
  Icon: React.ComponentType<{ size?: number; className?: string }>;
  color?: string;
  href?: string;
  danger?: boolean;
  onClick?: () => void;
  dividerBefore?: boolean;
}

export interface MobileAudioShellProps {
  /** Audio stream URL (already token-signed). Empty when not downloaded yet. */
  src: string;
  /** Whether the audio file is playable. When false, `noAudioPrompt` shows in
   * the player's spot (cover/title/stats/actions/tags all stay). */
  hasAudio: boolean;
  /** Album cover URL; falls back to a themed Music placeholder. */
  coverUrl?: string;
  /** Track's Soda palette (gradient + accents). */
  theme?: SodaTheme;

  title: string;
  /** Artist line under the title — rendered ONLY when present. */
  artist?: string;

  /** Social counts for the stats row. Absent/empty → the left side is blank
   * (speed chip + ⋮ still render). */
  social?: {
    likes?: number | null;
    comments?: number | null;
    shares?: number | null;
    collects?: number | null;
    onShareClick?: () => void;
  } | null;

  /** Synced lyric lines — drive the couplet preview + (upload) the overlay. */
  lyricLines: LyricLine[];
  /** Re-pull lyrics after the overlay closes (overlay can fetch on a track that
   * had none). */
  onReloadLyrics?: () => void;
  /** Media id for the download overlay's fetch-by-id path. When present the
   * overlay delegates to SodaLyricsTab (its own Fetch/Copy Lyrics actions);
   * when absent the overlay renders `lyricLines` directly (upload). */
  overlayMediaId?: string;
  /** Subtitle (artist/author) shown in the overlay header. */
  overlaySubtitle?: string;

  /** Live playback position (seconds) — drives the active lyric line. */
  currentTime?: number;
  /** Forwarded from the player so the caller tracks position. */
  onTimeUpdate?: (s: number) => void;

  /** Chorus highlight marker (seconds). */
  chorusStartSec?: number;
  /** Opt-in chorus set/clear UI in the player (upload only today). */
  chorusEditable?: boolean;
  onChorusChange?: (s: number | null) => void;
  /** Track duration hint (seconds) for the player before it decodes. */
  durationSec?: number;

  /** ⋮ overflow menu entries, already ordered + flagged by the adapter. */
  menuItems: MobileAudioMenuItem[];

  /** Resolved resource id (when known) for tags/rating/notes wiring. */
  resourceId?: string;
  rating?: number;
  notes?: string;
  onRatingChange?: (n: number) => void;
  onNotesChange?: (s: string) => void;
  onNotesBlur?: () => void;

  /** Rendered inside the player's color block when `hasAudio` is false. */
  noAudioPrompt?: React.ReactNode;
  /** Source platform — gates the qishui "Fetch Lyrics" entry + the overlay. */
  sourcePlatform?: string;
}

function formatCount(num?: number | null): string {
  if (!num || num <= 0) return '0';
  if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
  if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
  return String(num);
}

const RATES = [1, 1.25, 1.5, 2, 0.75];

interface StatDef {
  key: string;
  Icon?: typeof Heart;
  /** Custom icon node (used for share — a filled arrow lucide can't fill). */
  node?: React.ReactNode;
  value?: number | null;
  /** When set, the stat becomes a button (e.g. share → copy original link). */
  onClick?: () => void;
}

/** A filled-icon social stat with its count as a small number at the top-right. */
function SocialStat({ Icon, node, value, onClick }: {
  Icon?: typeof Heart;
  node?: React.ReactNode;
  value?: number | null;
  onClick?: () => void;
}) {
  const body = (
    <>
      {node ?? (Icon ? <Icon size={27} fill="currentColor" stroke="none" /> : null)}
      <span className="absolute left-full top-[-4px] ml-px text-[11px] font-semibold leading-none whitespace-nowrap">
        {formatCount(value)}
      </span>
    </>
  );
  return onClick ? (
    <button type="button" onClick={onClick} className="relative inline-flex text-white" title="Copy original link">{body}</button>
  ) : (
    <span className="relative inline-flex text-white">{body}</span>
  );
}

export function MobileAudioShell({
  src,
  hasAudio,
  coverUrl,
  theme,
  title,
  artist,
  social,
  lyricLines,
  onReloadLyrics,
  overlayMediaId,
  overlaySubtitle,
  currentTime,
  onTimeUpdate,
  chorusStartSec,
  chorusEditable,
  onChorusChange,
  durationSec,
  menuItems,
  resourceId,
  rating,
  notes,
  onRatingChange,
  onNotesChange,
  onNotesBlur,
  noAudioPrompt,
  sourcePlatform,
}: MobileAudioShellProps) {
  const t = theme ?? buildSodaTheme(null, src);

  // ── Lyrics (couplet preview + full overlay) ─────────────────────────────
  const [showLyrics, setShowLyrics] = useState(false);

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
  const hasLyrics = lyricLines.length > 0 && activeLine.length > 0;
  const canFetchLyrics = sourcePlatform === 'qishui' && !!overlayMediaId;

  // ── Playback speed (chip in the stats row, applied in the player) ────────
  const [playbackRate, setPlaybackRate] = useState(1);
  const cycleRate = useCallback(() => {
    setPlaybackRate((prev) => {
      const idx = RATES.indexOf(prev);
      return RATES[(idx + 1) % RATES.length];
    });
  }, []);

  // ── ⋮ overflow menu ──────────────────────────────────────────────────────
  const [showMenu, setShowMenu] = useState(false);

  // ── Tags (keyed on the resolved resource id) ─────────────────────────────
  const [resourceTags, setResourceTags] = useState<Array<{ tag: Tag }>>([]);
  const [allTags, setAllTags] = useState<Tag[]>([]);

  useEffect(() => {
    fetchAllTags().then(setAllTags).catch((err) => console.error('Failed to load tags:', err));
  }, []);

  useEffect(() => {
    if (!resourceId) {
      setResourceTags([]);
      return;
    }
    let cancelled = false;
    setResourceTags([]);
    fetchResourceTags(resourceId)
      .then((tags) => { if (!cancelled) setResourceTags(tags); })
      .catch((err) => console.error('Failed to load resource tags:', err));
    return () => { cancelled = true; };
  }, [resourceId]);

  const handleAddTag = useCallback(async (tagId: string) => {
    if (!resourceId) return;
    try {
      await addResourceTag(resourceId, tagId);
      const updated = await fetchResourceTags(resourceId);
      setResourceTags(updated);
    } catch (err) {
      console.error('Failed to add tag:', err);
    }
  }, [resourceId]);

  const handleRemoveTag = useCallback(async (tagId: string) => {
    if (!resourceId) return;
    try {
      await removeResourceTag(resourceId, tagId);
      setResourceTags((prev) => prev.filter((tg) => String(tg.tag?.id) !== tagId));
    } catch (err) {
      console.error('Failed to remove tag:', err);
    }
  }, [resourceId]);

  const handleCreateTag = useCallback(async (name: string, color: string): Promise<Tag | null> => {
    try {
      const tag = await createTag({ name, color, type: 'user' });
      setAllTags((prev) => [...prev, tag]);
      return tag;
    } catch (err) {
      console.error('Failed to create tag:', err);
      return null;
    }
  }, []);

  // Social stats — filled white icons, skip any that are 0/missing.
  const shareArrow = (
    <svg width={27} height={27} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M14 9V5l8 7-8 7v-4C7 14 4 17 3 21c0-7 4-11 11-12Z" />
    </svg>
  );
  const stats: StatDef[] = social ? [
    { key: 'like', Icon: Heart, value: social.likes },
    { key: 'comment', Icon: MessageCircle, value: social.comments },
    // share = filled forward arrow; tapping it copies the original link.
    { key: 'share', node: shareArrow, value: social.shares, onClick: social.onShareClick },
    { key: 'collect', Icon: Bookmark, value: social.collects },
  ] : [];
  const visibleStats = stats.filter((s) => (s.value ?? 0) > 0);

  return (
    <div
      className="w-full min-h-full px-5 pb-12"
      style={{ background: t.gradientCss }}
    >
      {/* Cover — pt clears the floating top bar (back chevron + @author). */}
      <div className="pt-14 flex justify-center">
        {coverUrl ? (
          <img
            src={coverUrl}
            alt={title}
            className="w-56 h-56 rounded-[22px] object-cover shadow-2xl"
            onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
          />
        ) : (
          <div
            className="w-56 h-56 rounded-[22px] flex items-center justify-center shadow-2xl"
            style={{ backgroundColor: t.accentSoft }}
          >
            <Music size={64} style={{ color: t.onAccent }} />
          </div>
        )}
      </div>

      {/* Title (+ optional artist line) */}
      <h2 className="mt-5 text-center text-[22px] font-extrabold text-white truncate px-2" title={title}>
        {title}
      </h2>
      {artist && (
        <p className="mt-1 text-center text-sm text-white/60 truncate px-2" title={artist}>
          {artist}
        </p>
      )}

      {/* Lyric couplet — tap opens the full lyrics overlay. Collapses when no
          synced lyrics (the rest moves up). */}
      {hasLyrics ? (
        <button
          type="button"
          onClick={() => setShowLyrics(true)}
          aria-label="Open lyrics"
          className="mt-5 w-full px-2 text-center focus:outline-none"
        >
          <p className="text-[18px] font-bold text-white leading-snug line-clamp-2">{activeLine}</p>
          {nextLine && (
            <p className="mt-1.5 text-sm text-white/45 truncate">{nextLine}</p>
          )}
        </button>
      ) : canFetchLyrics ? (
        // No lyrics yet — the couplet collapses, so give a reachable entry to
        // the lyrics overlay (where the Fetch Lyrics button lives).
        <button
          type="button"
          onClick={() => setShowLyrics(true)}
          className="mt-5 mx-auto flex items-center gap-1.5 px-4 py-1.5 rounded-full bg-white/10 hover:bg-white/20 text-sm font-medium text-white/80 transition-colors"
        >
          <Music size={14} /> Fetch Lyrics
        </button>
      ) : null}

      {/* Stats + actions row */}
      <div className="mt-6 flex items-center">
        {visibleStats.length > 0 && (
          <div className="flex items-start gap-[30px]">
            {visibleStats.map((s) => (
              <SocialStat key={s.key} Icon={s.Icon} node={s.node} value={s.value} onClick={s.onClick} />
            ))}
          </div>
        )}
        <div className="ml-auto flex items-center gap-3.5">
          {/* Speed chip */}
          <button
            type="button"
            onClick={cycleRate}
            className="bg-white/[0.14] hover:bg-white/20 rounded-lg px-2.5 py-1 text-xs font-medium text-white tabular-nums transition-colors"
          >
            {playbackRate === 1 ? '1x' : `${playbackRate}x`}
          </button>
          {/* ⋮ overflow menu */}
          <div className="relative">
            <button
              type="button"
              onClick={() => setShowMenu((v) => !v)}
              aria-label="More actions"
              className="text-white/90 hover:text-white p-0.5 transition-colors"
            >
              <MoreVertical size={20} />
            </button>
            {showMenu && (
              <>
                <div className="fixed inset-0 z-30" onClick={() => setShowMenu(false)} />
                <div className="absolute right-0 top-full mt-1 z-40 min-w-[160px] bg-ink-900 border border-ink-700 rounded-lg shadow-xl py-1">
                  {menuItems.map((item) => {
                    const { Icon } = item;
                    return (
                      <React.Fragment key={item.key}>
                        {item.dividerBefore && <div className="border-t border-ink-700 my-1" />}
                        {item.href ? (
                          <a
                            href={item.href}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={() => setShowMenu(false)}
                            className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-ink-300 hover:bg-ink-800 hover:text-ink-50 transition-colors"
                          >
                            <Icon size={13} className={item.color} /> {item.label}
                          </a>
                        ) : (
                          <button
                            onClick={() => { setShowMenu(false); item.onClick?.(); }}
                            className={
                              item.danger
                                ? 'flex items-center gap-2 w-full px-3 py-1.5 text-xs text-red-400 hover:bg-red-950/50 hover:text-red-300 transition-colors'
                                : 'flex items-center gap-2 w-full px-3 py-1.5 text-xs text-ink-300 hover:bg-ink-800 hover:text-ink-50 transition-colors'
                            }
                          >
                            <Icon size={13} className={item.color} /> {item.label}
                          </button>
                        )}
                      </React.Fragment>
                    );
                  })}
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Tags */}
      {resourceId && (
        <div className="mt-4">
          <EagleTagPicker
            variant="bare"
            assignedTags={resourceTags.map((item) => item.tag).filter((tg): tg is Tag => !!tg)}
            allTags={allTags}
            onAdd={handleAddTag}
            onRemove={handleRemoveTag}
            onCreate={handleCreateTag}
          />
        </div>
      )}

      {/* Color block: the player when downloaded, else a caller-supplied "not
          downloaded — Fetch" prompt in the SAME spot. */}
      <div className="mt-4 bg-black/20 rounded-2xl p-4">
        {hasAudio ? (
          <AudioWaveformPlayer
            src={src}
            filename={title}
            duration={durationSec}
            chorusStartSec={chorusStartSec}
            chorusEditable={chorusEditable}
            onChorusChange={onChorusChange}
            onTimeUpdate={onTimeUpdate}
            theme={t}
            layout="compact"
            playbackRate={playbackRate}
          />
        ) : (
          noAudioPrompt ?? null
        )}
      </div>

      {/* Rating */}
      {onRatingChange && (
        <div className="mt-5 flex items-center gap-3">
          <span className="text-[11px] text-white/55 uppercase tracking-wider">Rating</span>
          <RatingStars value={rating || 0} onChange={onRatingChange} size={18} />
        </div>
      )}

      {/* Notes */}
      {onNotesChange && (
        <div className="mt-4">
          <textarea
            value={notes || ''}
            onChange={(e) => onNotesChange(e.target.value)}
            onBlur={onNotesBlur}
            placeholder="Add notes..."
            rows={3}
            className="w-full rounded-xl border border-white/[0.18] bg-white/[0.06] px-3 py-3 text-[13px] text-white placeholder-white/40 focus:outline-none focus:border-white/30 resize-none"
          />
        </div>
      )}

      {showLyrics && (overlayMediaId || lyricLines.length > 0) && (
        <LyricsOverlay
          lines={overlayMediaId ? undefined : lyricLines}
          mediaId={overlayMediaId}
          currentTime={currentTime}
          title={title}
          subtitle={overlaySubtitle}
          coverUrl={coverUrl}
          theme={t}
          onClose={() => { setShowLyrics(false); onReloadLyrics?.(); }}
          sourcePlatform={sourcePlatform}
        />
      )}
    </div>
  );
}
