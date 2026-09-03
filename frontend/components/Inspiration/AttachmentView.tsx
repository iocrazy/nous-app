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
      // Read by any modal that embeds AttachmentView (the note editor does):
      // this layer stacks above it and both listen for Escape on window, so
      // the host yields the key while this attribute is in the DOM. See
      // InspirationPage's edit-modal key handler.
      data-lightbox="attachment"
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


/**
 * Optional remove affordance (edit mode). When `onDelete` is undefined this
 * renders its child verbatim — no wrapper element, no extra DOM — so the
 * read-only consumer (NoteCard) keeps byte-identical markup. Only when a
 * caller opts in does the child get wrapped in a positioned container
 * carrying the "x" button.
 *
 * The button lives OUTSIDE the child (a sibling, not a descendant): images
 * render as a lightbox-opening <button>, and nesting a button inside a button
 * is invalid HTML that would also fire both handlers on one click.
 */
const Removable: React.FC<{
  attachment: NoteAttachment;
  onDelete?: (a: NoteAttachment) => void;
  block?: boolean;
  children: React.ReactNode;
}> = ({ attachment, onDelete, block, children }) => {
  if (!onDelete) return <>{children}</>;
  return (
    <span className={`relative ${block ? 'block' : 'inline-block'}`}>
      {children}
      <button
        type="button"
        aria-label={`Remove ${attachment.original_name}`}
        onClick={() => onDelete(attachment)}
        className="absolute -right-1.5 -top-1.5 rounded-full border border-line bg-island p-0.5 text-content-3 shadow-sm hover:text-danger"
      >
        <X size={11} />
      </button>
    </span>
  );
};

export const AttachmentView: React.FC<{
  attachments: NoteAttachment[];
  /** Edit mode: render a per-attachment remove button that hands the whole
   *  attachment back. Omitted → read-only rendering (the NoteCard default). */
  onDelete?: (attachment: NoteAttachment) => void;
}> = ({ attachments, onDelete }) => {
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
            <Removable key={a.id} attachment={a} onDelete={onDelete}>
              <button
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
            </Removable>
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
        <Removable key={a.id} attachment={a} onDelete={onDelete} block>
          <video
            src={urls[a.id] ?? ''}
            controls
            preload="metadata"
            className="max-h-64 rounded-lg bg-island-2"
          />
        </Removable>
      ))}
      {audios.map((a) => (
        <Removable key={a.id} attachment={a} onDelete={onDelete} block>
          <audio src={urls[a.id] ?? ''} controls className="h-9 w-full max-w-xs" />
        </Removable>
      ))}
      {files.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {files.map((a) => (
            <Removable key={a.id} attachment={a} onDelete={onDelete}>
              <a
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
            </Removable>
          ))}
        </div>
      )}
    </div>
  );
};
