import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  File,
  Film,
  Image,
  Music,
  Search,
  X,
} from 'lucide-react';
import { fetchResources, getResourceCoverUrl } from '../../services/resourceService';
import { useToast } from '../Toast';
import type { ResourceItem } from '../../types';

// ─────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────

interface Props {
  open: boolean;
  teamId: string;
  onClose: () => void;
  onSelect: (resource: ResourceItem) => void;
}

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────

function formatFileSize(bytes: number | null | undefined): string {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function getTypeLabel(mimeType: string | null | undefined): string {
  if (!mimeType) return 'File';
  if (mimeType.startsWith('video/')) return 'Video';
  if (mimeType.startsWith('image/')) return 'Image';
  if (mimeType.startsWith('audio/')) return 'Audio';
  if (mimeType.includes('pdf')) return 'PDF';
  if (mimeType.startsWith('text/')) return 'Text';
  return 'File';
}

function getTypeIcon(mimeType: string | null | undefined): React.ReactElement {
  if (mimeType?.startsWith('video/')) return <Film size={14} className="text-purple-400" />;
  if (mimeType?.startsWith('image/')) return <Image size={14} className="text-green-400" />;
  if (mimeType?.startsWith('audio/')) return <Music size={14} className="text-cyan-400" />;
  return <File size={14} className="text-[#74747e]" />;
}

// ResourceCard-exact thumbnail logic: attempt cover whenever any signal is
// present OR the file is an image/* (backend falls through thumbnail_path >
// cover_image_path > parsed_media.cover_download_path > original for images).
// No token needed — the /cover endpoint is auth-gated via session cookie.
function buildThumbnailSrc(item: ResourceItem): string | null {
  const resource = item.resource;
  if (!resource?.id) return null;
  const isImage = resource.mime_type?.startsWith('image/') ?? false;
  if (
    resource.thumbnail_path ||
    resource.cover_image_path ||
    resource.media_id ||
    isImage
  ) {
    return getResourceCoverUrl(String(resource.id));
  }
  return null;
}

// ─────────────────────────────────────────────
// ResourcePicker
// ─────────────────────────────────────────────

const PAGE_SIZE = 50;

export default function ResourcePicker({ open, teamId, onClose, onSelect }: Props) {
  const { t } = useTranslation();
  const { addToast } = useToast();

  const [items, setItems] = useState<ResourceItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');

  const searchInputRef = useRef<HTMLInputElement>(null);
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Debounce search input
  function handleSearchChange(value: string): void {
    setSearch(value);
    if (debounceTimer.current) clearTimeout(debounceTimer.current);
    debounceTimer.current = setTimeout(() => {
      setDebouncedSearch(value);
    }, 280);
  }

  // Reset + fetch when modal opens or debounced search changes
  useEffect(() => {
    if (!open) return;

    let cancelled = false;

    setLoading(true);
    setItems([]);

    fetchResources({
      isPersonal: false,
      scopeId: teamId,
      flatten: true,
      search: debouncedSearch.trim() || undefined,
    })
      .then((results) => {
        if (cancelled) return;
        // Cap to PAGE_SIZE so the picker stays snappy even on large libraries.
        setItems(results.slice(0, PAGE_SIZE));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        console.error('[ResourcePicker] fetchResources failed:', err);
        addToast(t('chat.mediaCard.loadError'), 'error');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [open, teamId, debouncedSearch]); // eslint-disable-line react-hooks/exhaustive-deps

  // Focus search when opened
  useEffect(() => {
    if (!open) return;
    setSearch('');
    setDebouncedSearch('');
    const timer = setTimeout(() => searchInputRef.current?.focus(), 60);
    return () => clearTimeout(timer);
  }, [open]);

  // Dismiss on Escape
  useEffect(() => {
    if (!open) return;
    function handleKeyDown(e: KeyboardEvent): void {
      if (e.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  // ── Handlers ────────────────────────────────

  function handleSelect(item: ResourceItem): void {
    onSelect(item);
    onClose();
  }

  function handleBackdropClick(e: React.MouseEvent<HTMLDivElement>): void {
    if (e.target === e.currentTarget) onClose();
  }

  // ── Render ──────────────────────────────────

  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 backdrop-blur-[2px] grid place-items-center"
      onClick={handleBackdropClick}
    >
      <div className="w-[520px] max-h-[80vh] bg-[#15151a] border border-white/[.12] rounded-[18px] shadow-[0_24px_70px_rgba(0,0,0,.6)] flex flex-col overflow-hidden">

        {/* ── Header ── */}
        <div className="flex items-center justify-between px-5 py-[18px] border-b border-white/[.065] shrink-0">
          <h3 className="text-[16px] font-semibold text-[#e7e7ea] leading-none">
            {t('chat.mediaCard.title')}
          </h3>
          <button
            onClick={onClose}
            className="w-[26px] h-[26px] rounded-[7px] grid place-items-center text-[#74747e] hover:text-[#a3a3ad] hover:bg-white/[.06] transition-colors"
            aria-label="Close"
          >
            <X size={14} strokeWidth={2} />
          </button>
        </div>

        {/* ── Search ── */}
        <div className="px-5 pt-[14px] pb-[10px] shrink-0">
          <div className="relative">
            <Search
              size={14}
              strokeWidth={2}
              className="absolute left-[11px] top-1/2 -translate-y-1/2 text-[#74747e] pointer-events-none"
            />
            <input
              ref={searchInputRef}
              type="text"
              value={search}
              onChange={(e) => handleSearchChange(e.target.value)}
              placeholder={t('chat.mediaCard.search')}
              className="w-full bg-[#09090b] border border-white/[.12] rounded-[9px] pl-[32px] pr-[11px] py-[9px] text-[13.5px] text-[#e7e7ea] font-[inherit] outline-none focus:border-indigo-500/50 placeholder:text-[#74747e]"
            />
          </div>
        </div>

        {/* ── Body / Grid ── */}
        <div className="flex-1 overflow-y-auto px-5 pb-5 min-h-0">
          {loading ? (
            <div className="py-10 text-center text-[13px] text-[#74747e]">
              {t('chat.mediaCard.loading')}
            </div>
          ) : items.length === 0 ? (
            <div className="py-10 text-center text-[13px] text-[#74747e]">
              {t('chat.mediaCard.empty')}
            </div>
          ) : (
            <div className="grid grid-cols-3 gap-[10px] mt-[4px]">
              {items.map((item) => {
                const resource = item.resource;
                const filename = resource?.filename ?? 'Untitled';
                const mimeType = resource?.mime_type ?? null;
                const fileSize = resource?.file_size_bytes ?? null;
                const thumbnailSrc = buildThumbnailSrc(item);

                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => handleSelect(item)}
                    className="flex flex-col rounded-[10px] bg-[#17171b] border border-white/[.065] overflow-hidden text-left hover:border-indigo-500/50 hover:bg-[#1c1c22] transition-colors group"
                  >
                    {/* Thumbnail */}
                    <div className="relative w-full aspect-video bg-[#0e0e12] overflow-hidden shrink-0">
                      {thumbnailSrc ? (
                        <img
                          src={thumbnailSrc}
                          alt={filename}
                          className="w-full h-full object-cover"
                          onError={(e) => {
                            (e.currentTarget as HTMLImageElement).style.display = 'none';
                          }}
                        />
                      ) : null}
                      {/* Icon overlay for non-image types or when thumbnail absent */}
                      {!thumbnailSrc && (
                        <div className="absolute inset-0 grid place-items-center">
                          {getTypeIcon(mimeType)}
                        </div>
                      )}
                    </div>

                    {/* Info */}
                    <div className="px-[9px] py-[8px] flex flex-col gap-[3px] min-w-0">
                      <p
                        className="text-[12px] font-medium text-[#e7e7ea] leading-[1.35] truncate"
                        title={filename}
                      >
                        {filename}
                      </p>
                      <div className="flex items-center gap-[5px] text-[11px] text-[#74747e]">
                        {getTypeIcon(mimeType)}
                        <span>{getTypeLabel(mimeType)}</span>
                        {fileSize ? (
                          <>
                            <span className="opacity-40">·</span>
                            <span>{formatFileSize(fileSize)}</span>
                          </>
                        ) : null}
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
