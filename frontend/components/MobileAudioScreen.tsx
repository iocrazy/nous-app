import { useState, useEffect, useCallback } from 'react';
import {
  Music, Copy, Trash2, ExternalLink, CloudDownload,
  Share2, Image as ImageIcon,
} from 'lucide-react';
import type { Video } from '../types';
import { audioMenuActions } from './mobileAudioMenu';
import type { SodaTheme } from '../utils/sodaTheme';
import { getMediaLyrics, type LyricLine } from '../services/lyricsService';
import { getSupabaseClient } from '../supabaseClient';
import { MobileAudioShell, type MobileAudioMenuItem } from './MobileAudioShell';

/**
 * MobileAudioScreen — the download adapter for the immersive mobile-audio
 * layout. It owns the download-specific data (a `Video`, lyrics fetched by
 * media id, the per-asset ⋮ menu, the resource-id resolution from `video.id`)
 * and maps it onto the presentational `MobileAudioShell`.
 *
 * The shell is the locked layout; this adapter keeps the download output
 * byte-identical (no artist line, social = video counts, overlay fetches lyrics
 * by id). A sibling upload adapter maps its own props onto the same shell.
 *
 * Mobile-audio ONLY. Desktop + mobile-video keep rendering the old path.
 */

interface MobileAudioScreenProps {
  video: Video;
  /** Audio stream URL (already token-signed). Empty when not downloaded yet. */
  src: string;
  /** Whether the audio file is downloaded. When false, the player area shows a
   * "not downloaded — Fetch" prompt instead of the waveform, but the cover,
   * title, stats, actions, rating/notes/tags all stay. */
  hasAudio: boolean;
  /** Re-download the track (e.g. qishui soda fetch). Shown in the no-audio prompt. */
  onFetchAudio?: () => void;
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

  /** ⋮ overflow actions — wired to DownloadDetailPage's existing handlers.
   * The menu is per-asset (audio + cover), mirroring the PC DownloadMenuDropdown:
   * each asset shows Download (present) or Fetch/Retry (missing). */
  onDownloadAudio?: () => void;
  /** Download the cover file (when already on disk). */
  onDownloadCover?: () => void;
  /** Re-fetch a missing/failed cover (qishui → soda re-download tops it up). */
  onFetchCover?: () => void;
  onShare?: () => void;
  onDelete?: () => void;
  onCopyLink?: () => void;
  canShare?: boolean;
}

export function MobileAudioScreen({
  video,
  src,
  hasAudio,
  onFetchAudio,
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
  onDownloadCover,
  onFetchCover,
  onShare,
  onDelete,
  onCopyLink,
  canShare,
}: MobileAudioScreenProps) {
  const title = video.music_name || video.title || 'Audio';

  // ── Per-asset ⋮ menu (audio + cover), mirroring PC DownloadMenuDropdown ────
  const isQishui = video.source_platform === 'qishui';
  const assetActions = audioMenuActions(video, { hasAudio, isQishui });
  // Resolve each asset action to a concrete menu entry (label/icon/handler).
  // Drop any whose handler wasn't supplied so we never render a dead button.
  const assetMenuItems = assetActions
    .map((a) => {
      if (a.asset === 'audio') {
        if (a.kind === 'download') {
          return onDownloadAudio && { key: 'audio-dl', label: 'Download Audio', Icon: Music, color: 'text-amber-400', onClick: onDownloadAudio };
        }
        return onFetchAudio && {
          key: 'audio-fetch',
          label: a.kind === 'retry' ? 'Retry Audio' : 'Fetch Audio',
          Icon: CloudDownload,
          color: a.kind === 'retry' ? 'text-red-400' : 'text-amber-400',
          onClick: onFetchAudio,
        };
      }
      // cover
      if (a.kind === 'download') {
        return onDownloadCover && { key: 'cover-dl', label: 'Download Cover', Icon: ImageIcon, color: 'text-emerald-400', onClick: onDownloadCover };
      }
      return onFetchCover && {
        key: 'cover-fetch',
        label: a.kind === 'retry' ? 'Retry Cover' : 'Fetch Cover',
        Icon: CloudDownload,
        color: a.kind === 'retry' ? 'text-red-400' : 'text-emerald-400',
        onClick: onFetchCover,
      };
    })
    .filter(Boolean) as MobileAudioMenuItem[];

  // Tail entries (Share / Copy Link / Open Original), in order. The divider
  // before the tail group only shows when there were asset items above it —
  // reproduces the download menu's exact separator placement.
  const tailItems: MobileAudioMenuItem[] = [];
  if (canShare && onShare) {
    tailItems.push({ key: 'share', label: 'Share', Icon: Share2, color: 'text-purple-400', onClick: onShare });
  }
  if (video.original_url && onCopyLink) {
    tailItems.push({ key: 'copy', label: 'Copy Link', Icon: Copy, onClick: onCopyLink });
  }
  if (video.original_url) {
    tailItems.push({ key: 'open', label: 'Open Original', Icon: ExternalLink, href: video.original_url });
  }
  const tailWithDivider = tailItems.length > 0 && assetMenuItems.length > 0
    ? [{ ...tailItems[0], dividerBefore: true }, ...tailItems.slice(1)]
    : tailItems;

  // Delete always sits below its own divider (matches today's unconditional
  // separator before Delete).
  const deleteItem: MobileAudioMenuItem[] = onDelete
    ? [{ key: 'delete', label: 'Delete', Icon: Trash2, danger: true, onClick: onDelete, dividerBefore: true }]
    : [];

  const menuItems: MobileAudioMenuItem[] = [...assetMenuItems, ...tailWithDivider, ...deleteItem];

  // ── Lyrics (fetched by media id) ──────────────────────────────────────────
  const [lyricLines, setLyricLines] = useState<LyricLine[]>([]);

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

  // Re-pull lyrics after the overlay closes — the overlay can Fetch lyrics for a
  // track that had none, and this keeps the couplet/entry in sync.
  const reloadLyrics = useCallback(() => {
    if (!mediaId) return;
    getMediaLyrics(mediaId)
      .then((data) => setLyricLines(data.lines))
      .catch((err) => console.error('Failed to reload lyrics:', err));
  }, [mediaId]);

  // ── Resolve the resource id (passed, or looked up from the media id) ──────
  const [resolvedResourceId, setResolvedResourceId] = useState<string | null>(resourceId ?? null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (resourceId) {
        setResolvedResourceId(resourceId);
        return;
      }
      if (!video.id) {
        setResolvedResourceId(null);
        return;
      }
      const supabase = getSupabaseClient();
      if (!supabase) return;
      const { data: resource } = await supabase
        .from('resources')
        .select('id')
        .eq('media_id', video.id)
        .limit(1)
        .maybeSingle();
      if (cancelled || !resource) return;
      setResolvedResourceId(String(resource.id));
    })();
    return () => { cancelled = true; };
  }, [resourceId, video.id]);

  // No-audio prompt rendered in the player's spot when the track isn't on disk.
  const noAudioPrompt = (
    <div className="flex flex-col items-center justify-center gap-3 py-6 text-center">
      <Music size={28} className="text-white/40" />
      <p className="text-[13px] text-white/70">
        This track isn&apos;t downloaded yet.
      </p>
      {onFetchAudio && (
        <button
          type="button"
          onClick={onFetchAudio}
          className="flex items-center gap-1.5 px-4 py-2 rounded-full bg-white/15 hover:bg-white/25 text-sm font-medium text-white transition-colors"
        >
          <CloudDownload size={15} /> Fetch Audio
        </button>
      )}
    </div>
  );

  return (
    <MobileAudioShell
      src={src}
      hasAudio={hasAudio}
      coverUrl={coverUrl}
      theme={theme}
      title={title}
      // Download keeps NO artist line — author lives in the top bar.
      artist={undefined}
      social={{
        likes: video.like_count,
        comments: video.comment_count,
        shares: video.share_count,
        collects: video.favorite_count,
        onShareClick: video.original_url ? onCopyLink : undefined,
      }}
      lyricLines={lyricLines}
      onReloadLyrics={reloadLyrics}
      overlayMediaId={mediaId}
      overlaySubtitle={video.author || undefined}
      currentTime={currentTime}
      onTimeUpdate={onTimeUpdate}
      chorusStartSec={chorusStartSec}
      durationSec={Number(video.duration) || undefined}
      menuItems={menuItems}
      resourceId={resolvedResourceId ?? undefined}
      rating={resourceRating}
      notes={resourceNotes}
      onRatingChange={onRatingChange}
      onNotesChange={onNotesChange}
      onNotesBlur={onNotesBlur}
      noAudioPrompt={noAudioPrompt}
      sourcePlatform={video.source_platform}
    />
  );
}
