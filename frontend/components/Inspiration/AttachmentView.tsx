// Renders a note's attachments by mime family (spec §2.2 #5):
// image grid / inline audio / video card / typed download chip.
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { FileText, X } from 'lucide-react';
import {
  attachmentUrlWithToken,
  type NoteAttachment,
} from '../../services/inspirationService';
import { useAuth } from '../../contexts/AuthContext';

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

function extOf(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot > 0 ? name.slice(dot + 1).toUpperCase().slice(0, 5) : 'FILE';
}

/**
 * Resolve every attachment's token-authed URL. `attachmentUrlWithToken` is a
 * pure sync builder (same shape as resourceService's getResourceMediaUrl) —
 * no fetching happens here, so a plain memo is enough (no effect/state).
 */
function useAttachmentUrls(
  attachments: NoteAttachment[],
  mediaToken: string | undefined,
): Record<string, string> {
  return useMemo(
    () =>
      Object.fromEntries(
        attachments.map((a) => [a.id, attachmentUrlWithToken(a.id, mediaToken)]),
      ),
    [attachments, mediaToken],
  );
}

/**
 * In-place image lightbox: click a thumbnail to enlarge over the page
 * (no navigation away), click the image to toggle fit ↔ full size,
 * click the backdrop / press Escape to close.
 */
const ImageLightbox: React.FC<{
  src: string;
  alt: string;
  onClose: () => void;
}> = ({ src, alt, onClose }) => {
  const [zoomed, setZoomed] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 backdrop-blur-sm"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={alt}
    >
      <button
        type="button"
        aria-label="Close"
        className="absolute right-4 top-4 rounded-full bg-white/10 p-2 text-white hover:bg-white/20"
        onClick={onClose}
      >
        <X size={18} />
      </button>
      <div
        className={zoomed ? 'max-h-full max-w-full overflow-auto' : 'contents'}
        onClick={(e) => e.stopPropagation()}
      >
        <img
          src={src}
          alt={alt}
          onClick={() => setZoomed((z) => !z)}
          className={
            zoomed
              ? 'max-w-none cursor-zoom-out'
              : 'max-h-[90vh] max-w-[92vw] cursor-zoom-in object-contain'
          }
        />
      </div>
    </div>
  );
};

export const AttachmentView: React.FC<{ attachments: NoteAttachment[] }> = ({
  attachments,
}) => {
  const { mediaToken } = useAuth();
  const urls = useAttachmentUrls(attachments, mediaToken ?? undefined);
  const [lightbox, setLightbox] = useState<{ src: string; alt: string } | null>(
    null,
  );
  const closeLightbox = useCallback(() => setLightbox(null), []);
  if (!attachments.length) return null;
  const images = attachments.filter((a) => a.mime.startsWith('image/'));
  const audios = attachments.filter((a) => a.mime.startsWith('audio/'));
  const videos = attachments.filter((a) => a.mime.startsWith('video/'));
  const files = attachments.filter(
    (a) =>
      !a.mime.startsWith('image/') &&
      !a.mime.startsWith('audio/') &&
      !a.mime.startsWith('video/'),
  );

  return (
    <div className="mt-2 space-y-2">
      {images.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {images.map((a) => (
            <button
              key={a.id}
              type="button"
              className="block cursor-zoom-in"
              aria-label={`View ${a.original_name}`}
              onClick={() =>
                setLightbox({ src: urls[a.id] ?? '', alt: a.original_name })
              }
            >
              <img
                src={urls[a.id] ?? ''}
                alt={a.original_name}
                loading="lazy"
                className="h-24 w-32 rounded-lg object-cover bg-island-2"
              />
            </button>
          ))}
        </div>
      )}
      {lightbox && (
        <ImageLightbox
          src={lightbox.src}
          alt={lightbox.alt}
          onClose={closeLightbox}
        />
      )}
      {videos.map((a) => (
        <video
          key={a.id}
          src={urls[a.id] ?? ''}
          controls
          preload="metadata"
          className="max-h-64 rounded-lg bg-island-2"
        />
      ))}
      {audios.map((a) => (
        <audio key={a.id} src={urls[a.id] ?? ''} controls className="h-9 w-full max-w-xs" />
      ))}
      {files.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {files.map((a) => (
            <a
              key={a.id}
              href={urls[a.id] ?? ''}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-2 rounded-lg bg-island-2 px-3 py-1.5 text-xs text-content-2 hover:bg-line"
            >
              <FileText size={14} className="text-content-3" />
              <span className="max-w-[180px] truncate">{a.original_name}</span>
              <span className="rounded bg-[var(--accent-soft)] px-1.5 py-0.5 text-[10px] font-bold text-[var(--accent-text)]">
                {extOf(a.original_name)}
              </span>
              <span className="text-content-3">{formatSize(a.size_bytes)}</span>
            </a>
          ))}
        </div>
      )}
    </div>
  );
};
