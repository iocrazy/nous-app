import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ChevronLeft,
  Copy,
  FileText,
  Image as ImageIcon,
  Trash2,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { Resource } from '../types';
import type { LyricLine } from '../services/lyricsService';
import { buildSodaTheme } from '../utils/sodaTheme';
import { parseArtistTitle } from '../utils/parseArtistTitle';
import {
  getResourceCoverUrl,
  getResourceLyrics,
  setResourceChorus,
  updateResource,
  uploadResourceCover,
  uploadResourceLyrics,
} from '../services/resourceService';
import { useToast } from './Toast';
import { MobileAudioShell, type MobileAudioMenuItem } from './MobileAudioShell';

/**
 * MobileAudioUpload — the uploaded-audio adapter for the immersive mobile-audio
 * layout. It owns the upload-specific data (a `Resource`, lyrics + chorus stored
 * on the row, the cover/lrc replace actions) and maps it onto the presentational
 * `MobileAudioShell` — the same shell the download adapter (`MobileAudioScreen`)
 * uses, keeping the rendered layout identical.
 *
 * Uploads have no social stats and no qishui fetch, so the overlay renders the
 * row's own `lyrics_json` lines directly (no `overlayMediaId`).
 *
 * Mobile-audio ONLY. Desktop keeps rendering the inspector layout.
 */

interface MobileAudioUploadProps {
  resource: Resource;
  /** Audio stream URL (already token-signed). */
  fileUrl: string;
  /** Called with the fresh row after any mutation (cover / lrc / chorus / rating / notes). */
  onResourceUpdated?: (updated: Resource) => void;
  /** Floating back arrow handler (top-left). */
  onBack: () => void;
  /** Trash/delete the resource — reuses ResourceDetailPage's handler. */
  onDelete?: () => void;
}

export function MobileAudioUpload({
  resource,
  fileUrl,
  onResourceUpdated,
  onBack,
  onDelete,
}: MobileAudioUploadProps) {
  const { t } = useTranslation();
  const { addToast } = useToast();

  const coverInputRef = useRef<HTMLInputElement>(null);
  const lrcInputRef = useRef<HTMLInputElement>(null);

  // ── View-model derivation ────────────────────────────────────────────────
  const { artist, title } = parseArtistTitle(resource.filename);
  const theme = buildSodaTheme(null, fileUrl);
  const coverUrl =
    resource.cover_image_path && resource.id
      ? getResourceCoverUrl(String(resource.id), undefined, resource.updated_at)
      : undefined;

  // lyrics_json lines carry `line_start_ms: number | null`; the shell wants
  // `LyricLine` (`number | undefined`) — normalize null → undefined.
  const lyricLines: LyricLine[] = (resource.lyrics_json?.lines ?? []).map((l) => ({
    text: l.text,
    line_start_ms: l.line_start_ms ?? undefined,
  }));

  const chorusStartSec =
    resource.chorus_start_ms != null ? resource.chorus_start_ms / 1000 : undefined;

  // ── Playback position (drives the active lyric line) ─────────────────────
  const [currentTime, setCurrentTime] = useState(0);

  // ── Notes (local typing → commit on blur) ────────────────────────────────
  const [notesValue, setNotesValue] = useState(resource.notes || '');
  useEffect(() => {
    setNotesValue(resource.notes || '');
  }, [resource.id, resource.notes]);

  // ── Mutations ─────────────────────────────────────────────────────────────
  const reloadLyrics = useCallback(async () => {
    try {
      const data = await getResourceLyrics(resource.id);
      if (data) onResourceUpdated?.({ ...resource, lyrics_json: data });
    } catch (err) {
      console.error('Failed to reload lyrics:', err);
    }
  }, [resource, onResourceUpdated]);

  const handleChorusChange = useCallback(
    async (sec: number | null) => {
      try {
        const ms = sec == null ? null : Math.round(sec * 1000);
        const updated = await setResourceChorus(resource.id, ms);
        onResourceUpdated?.(updated);
        addToast(
          t(
            ms == null ? 'resources.detail.chorusCleared' : 'resources.detail.chorusSet',
            ms == null ? 'Chorus removed' : 'Chorus marked',
          ),
          'success',
        );
      } catch (err) {
        console.error('Failed to set chorus:', err);
        addToast('Failed to set chorus', 'error');
      }
    },
    [resource.id, onResourceUpdated, addToast, t],
  );

  const handleRatingChange = useCallback(
    async (n: number) => {
      try {
        const updated = await updateResource(resource.id, { rating: n });
        onResourceUpdated?.(updated);
      } catch (err) {
        console.error('Failed to update rating:', err);
      }
    },
    [resource.id, onResourceUpdated],
  );

  const commitNotes = useCallback(async () => {
    const val = notesValue.trim();
    if (val === (resource.notes || '').trim()) return;
    try {
      const updated = await updateResource(resource.id, { notes: val });
      onResourceUpdated?.(updated);
    } catch (err) {
      console.error('Failed to update notes:', err);
    }
  }, [notesValue, resource.notes, resource.id, onResourceUpdated]);

  const handleCoverFile = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const f = e.target.files?.[0];
      e.target.value = ''; // allow re-selecting the same file
      if (!f) return;
      try {
        const updated = await uploadResourceCover(resource.id, f);
        onResourceUpdated?.(updated);
        addToast(t('resources.detail.coverUpdated', 'Cover updated'), 'success');
      } catch (err) {
        console.error('Failed to upload cover:', err);
        addToast('Failed to upload cover', 'error');
      }
    },
    [resource.id, onResourceUpdated, addToast, t],
  );

  const handleLrcFile = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const f = e.target.files?.[0];
      e.target.value = '';
      if (!f) return;
      try {
        const lyricsJson = await uploadResourceLyrics(resource.id, f);
        onResourceUpdated?.({ ...resource, lyrics_json: lyricsJson });
        addToast(t('resources.detail.lyricsUploaded', 'Lyrics uploaded'), 'success');
      } catch (err) {
        console.error('Failed to upload lyrics:', err);
        addToast('Failed to upload lyrics', 'error');
      }
    },
    [resource, onResourceUpdated, addToast, t],
  );

  // ── ⋮ overflow menu ──────────────────────────────────────────────────────
  const menuItems: MobileAudioMenuItem[] = [
    {
      key: 'cover',
      label: t('resources.detail.replaceCover', 'Replace cover'),
      Icon: ImageIcon,
      color: 'text-emerald-400',
      onClick: () => coverInputRef.current?.click(),
    },
    {
      key: 'lrc',
      label: t('resources.detail.uploadLyrics', 'Upload .lrc'),
      Icon: FileText,
      color: 'text-amber-400',
      onClick: () => lrcInputRef.current?.click(),
    },
  ];
  if (resource.url) {
    menuItems.push({
      key: 'copy',
      label: t('resources.detail.copyLink', 'Copy link'),
      Icon: Copy,
      dividerBefore: true,
      onClick: () => {
        navigator.clipboard.writeText(resource.url!);
        addToast(t('chat.copied', 'Copied'), 'success');
      },
    });
  }
  if (onDelete) {
    menuItems.push({
      key: 'delete',
      label: t('common.delete', 'Delete'),
      Icon: Trash2,
      danger: true,
      dividerBefore: true,
      onClick: onDelete,
    });
  }

  return (
    <div className="relative w-full h-full overflow-y-auto">
      {/* Floating back arrow — the shell's pt-14 clears this. */}
      <button
        type="button"
        onClick={onBack}
        aria-label="Back"
        className="absolute top-3 left-3 z-20 p-2 rounded-full bg-black/30 text-white hover:bg-black/50 transition-colors"
      >
        <ChevronLeft size={22} />
      </button>

      <MobileAudioShell
        src={fileUrl}
        hasAudio
        coverUrl={coverUrl}
        theme={theme}
        title={title}
        artist={artist}
        social={undefined}
        lyricLines={lyricLines}
        onReloadLyrics={reloadLyrics}
        overlayMediaId={undefined}
        overlaySubtitle={artist}
        currentTime={currentTime}
        onTimeUpdate={setCurrentTime}
        chorusStartSec={chorusStartSec}
        chorusEditable
        onChorusChange={handleChorusChange}
        durationSec={resource.duration_seconds ?? undefined}
        menuItems={menuItems}
        resourceId={String(resource.id)}
        rating={resource.rating ?? 0}
        notes={notesValue}
        onRatingChange={handleRatingChange}
        onNotesChange={setNotesValue}
        onNotesBlur={commitNotes}
        sourcePlatform={resource.source_type}
      />

      {/* Hidden upload inputs driven by the ⋮ menu actions. */}
      <input
        ref={coverInputRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={handleCoverFile}
      />
      <input
        ref={lrcInputRef}
        type="file"
        accept=".lrc"
        className="hidden"
        onChange={handleLrcFile}
      />
    </div>
  );
}
