import React, { useState, useEffect, useCallback } from 'react';
import {
  Music, Heart, MessageCircle, Share2, Bookmark,
  MoreVertical, Download, Copy, Trash2, ExternalLink,
} from 'lucide-react';
import type { Video, Tag } from '../types';
import { buildSodaTheme, type SodaTheme } from '../utils/sodaTheme';
import { getMediaLyrics, type LyricLine } from '../services/lyricsService';
import { getSupabaseClient } from '../supabaseClient';
import { fetchResourceTags, addResourceTag, removeResourceTag } from '../services/resourceService';
import { fetchAllTags, createTag } from '../services/unifiedTagService';
import { AudioWaveformPlayer } from './AudioWaveformPlayer';
import { LyricsOverlay } from './LyricsOverlay';
import { EagleTagPicker } from './EagleTagPicker';
import { RatingStars } from './detail/DetailCardKit';

/**
 * MobileAudioScreen — the LOCKED mobile-audio detail layout.
 *
 * ONE continuous gradient surface (the track's Soda palette, no black-card
 * breaks), scrollable, in this top→bottom order:
 *   1. (top bar lives in DownloadDetailPage)
 *   2. large rounded album cover
 *   3. title (NO artist line — author is in the top bar)
 *   4. lyric couplet (current + next) — tap → full lyrics sub-page
 *      (collapses to nothing when the track has no synced lyrics)
 *   5. stats + actions row: social stats (filled icons + count) LEFT,
 *      speed chip "1x" + ⋮ overflow menu RIGHT
 *   6. tags (editable EagleTagPicker)
 *   7. color block: round play/pause + time + waveform-as-seek-bar
 *   8. rating + notes
 *
 * Mobile-audio ONLY. Desktop + mobile-video keep rendering the old path.
 */

interface MobileAudioScreenProps {
  video: Video;
  /** Audio stream URL (already token-signed). */
  src: string;
  /** Album cover URL; falls back to a themed Music placeholder. */
  coverUrl?: string;
  /** Track's Soda palette (gradient + accents). */
  theme?: SodaTheme;
  /** Media id — drives lyrics fetch + the full-lyrics sub-page. */
  mediaId: string;
  /** Live playback position (seconds) — drives the active lyric line. */
  currentTime?: number;
  /** Forwarded from the player so the page tracks position. */
  onTimeUpdate?: (seconds: number) => void;
  /** Chorus highlight marker (seconds). */
  chorusStartSec?: number;

  /** Resolved resource id (when known) for tags/rating/notes wiring. */
  resourceId?: string;
  resourceRating?: number;
  resourceNotes?: string;
  onRatingChange?: (n: number) => void;
  onNotesChange?: (s: string) => void;
  onNotesBlur?: () => void;

  /** ⋮ overflow actions — wired to DownloadDetailPage's existing handlers. */
  onDownloadAudio?: () => void;
  onShare?: () => void;
  onDelete?: () => void;
  onCopyLink?: () => void;
  canShare?: boolean;
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

export function MobileAudioScreen({
  video,
  src,
  coverUrl,
  theme,
  mediaId,
  currentTime,
  onTimeUpdate,
  chorusStartSec,
  resourceId,
  resourceRating,
  resourceNotes,
  onRatingChange,
  onNotesChange,
  onNotesBlur,
  onDownloadAudio,
  onShare,
  onDelete,
  onCopyLink,
  canShare,
}: MobileAudioScreenProps) {
  const t = theme ?? buildSodaTheme(null, src);
  const title = video.music_name || video.title || 'Audio';

  // ── Lyrics (couplet preview + full sub-page) ────────────────────────────
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
          console.error('Failed to load lyrics for couplet preview:', err);
          setLyricLines([]);
        }
      });
    return () => { cancelled = true; };
  }, [mediaId]);

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
  const hasLyrics = !!mediaId && lyricLines.length > 0 && activeLine.length > 0;

  // ── Playback speed (owned here; chip in the stats row, applied in player) ─
  const [playbackRate, setPlaybackRate] = useState(1);
  const cycleRate = useCallback(() => {
    setPlaybackRate((prev) => {
      const idx = RATES.indexOf(prev);
      return RATES[(idx + 1) % RATES.length];
    });
  }, []);

  // ── ⋮ overflow menu ──────────────────────────────────────────────────────
  const [showMenu, setShowMenu] = useState(false);

  // ── Tags (same wiring as MobileAudioMeta / MediaCard) ─────────────────────
  const [resolvedResourceId, setResolvedResourceId] = useState<string | null>(resourceId ?? null);
  const [resourceTags, setResourceTags] = useState<Array<{ tag: Tag }>>([]);
  const [allTags, setAllTags] = useState<Tag[]>([]);

  useEffect(() => {
    fetchAllTags().then(setAllTags).catch((err) => console.error('Failed to load tags:', err));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setResourceTags([]);
    (async () => {
      let resId = resourceId ?? null;
      if (!resId) {
        if (!video.id) return;
        const supabase = getSupabaseClient();
        if (!supabase) return;
        const { data: resource } = await supabase
          .from('resources')
          .select('id')
          .eq('media_id', video.id)
          .limit(1)
          .maybeSingle();
        if (cancelled || !resource) return;
        resId = String(resource.id);
      }
      if (cancelled) return;
      setResolvedResourceId(resId);
      try {
        const tags = await fetchResourceTags(resId);
        if (!cancelled) setResourceTags(tags);
      } catch (err) {
        console.error('Failed to load resource tags:', err);
      }
    })();
    return () => { cancelled = true; };
  }, [resourceId, video.id]);

  const handleAddTag = useCallback(async (tagId: string) => {
    if (!resolvedResourceId) return;
    try {
      await addResourceTag(resolvedResourceId, tagId);
      const updated = await fetchResourceTags(resolvedResourceId);
      setResourceTags(updated);
    } catch (err) {
      console.error('Failed to add tag:', err);
    }
  }, [resolvedResourceId]);

  const handleRemoveTag = useCallback(async (tagId: string) => {
    if (!resolvedResourceId) return;
    try {
      await removeResourceTag(resolvedResourceId, tagId);
      setResourceTags((prev) => prev.filter((tg) => String(tg.tag?.id) !== tagId));
    } catch (err) {
      console.error('Failed to remove tag:', err);
    }
  }, [resolvedResourceId]);

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
  const stats: StatDef[] = [
    { key: 'like', Icon: Heart, value: video.like_count },
    { key: 'comment', Icon: MessageCircle, value: video.comment_count },
    // share = filled forward arrow; tapping it copies the original link (restores
    // the old MediaCard behavior). lucide Share2 fills to 3 dots, so use a custom path.
    { key: 'share', node: shareArrow, value: video.share_count, onClick: video.original_url ? onCopyLink : undefined },
    { key: 'collect', Icon: Bookmark, value: video.favorite_count },
  ];
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

      {/* Title (no artist line — author is in the top bar) */}
      <h2 className="mt-5 text-center text-[22px] font-extrabold text-white truncate px-2" title={title}>
        {title}
      </h2>

      {/* Lyric couplet — tap opens the full lyrics sub-page. Collapses when no
          synced lyrics (the rest moves up). */}
      {hasLyrics && (
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
      )}

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
                <div className="absolute right-0 top-full mt-1 z-40 min-w-[160px] bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl py-1">
                  {onDownloadAudio && (
                    <button
                      onClick={() => { setShowMenu(false); onDownloadAudio(); }}
                      className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white transition-colors"
                    >
                      <Download size={13} className="text-amber-400" /> Download
                    </button>
                  )}
                  {canShare && onShare && (
                    <button
                      onClick={() => { setShowMenu(false); onShare(); }}
                      className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white transition-colors"
                    >
                      <Share2 size={13} className="text-purple-400" /> Share
                    </button>
                  )}
                  {video.original_url && onCopyLink && (
                    <button
                      onClick={() => { setShowMenu(false); onCopyLink(); }}
                      className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white transition-colors"
                    >
                      <Copy size={13} /> Copy Link
                    </button>
                  )}
                  {video.original_url && (
                    <a
                      href={video.original_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      onClick={() => setShowMenu(false)}
                      className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white transition-colors"
                    >
                      <ExternalLink size={13} /> Open Original
                    </a>
                  )}
                  {onDelete && (
                    <>
                      <div className="border-t border-zinc-700 my-1" />
                      <button
                        onClick={() => { setShowMenu(false); onDelete(); }}
                        className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-red-400 hover:bg-red-950/50 hover:text-red-300 transition-colors"
                      >
                        <Trash2 size={13} /> Delete
                      </button>
                    </>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Tags */}
      {resolvedResourceId && (
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

      {/* Color block: play + time + waveform-as-seek-bar */}
      <div className="mt-4 bg-black/20 rounded-2xl p-4">
        <AudioWaveformPlayer
          src={src}
          filename={title}
          duration={Number(video.duration) || undefined}
          chorusStartSec={chorusStartSec}
          onTimeUpdate={onTimeUpdate}
          theme={t}
          layout="compact"
          playbackRate={playbackRate}
        />
      </div>

      {/* Rating */}
      {onRatingChange && (
        <div className="mt-5 flex items-center gap-3">
          <span className="text-[11px] text-white/55 uppercase tracking-wider">Rating</span>
          <RatingStars value={resourceRating || 0} onChange={onRatingChange} size={18} />
        </div>
      )}

      {/* Notes */}
      {onNotesChange && (
        <div className="mt-4">
          <textarea
            value={resourceNotes || ''}
            onChange={(e) => onNotesChange(e.target.value)}
            onBlur={onNotesBlur}
            placeholder="Add notes..."
            rows={3}
            className="w-full rounded-xl border border-white/[0.18] bg-white/[0.06] px-3 py-3 text-[13px] text-white placeholder-white/40 focus:outline-none focus:border-white/30 resize-none"
          />
        </div>
      )}

      {showLyrics && mediaId && (
        <LyricsOverlay
          mediaId={mediaId}
          currentTime={currentTime}
          title={title}
          subtitle={video.author || undefined}
          coverUrl={coverUrl}
          theme={t}
          onClose={() => setShowLyrics(false)}
        />
      )}
    </div>
  );
}
