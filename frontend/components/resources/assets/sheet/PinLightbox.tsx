// frontend/components/resources/assets/sheet/PinLightbox.tsx
//
// A pin, full size.
//
// DECISION — this is a minimal modal, not canvas-core's `OutputLightbox`.
// That component is built around a generation: it imports
// `canvasGenerationService` (zip download), lazily pulls a panorama viewer,
// and takes compare sources / regenerate / edit-action props that only mean
// something on a canvas node. Reusing it here would drag the canvas
// generation module into the resources tree to show one attached file. The
// brief allowed either; the import cost decided it.
//
// What it keeps from that one: arrow-key + button navigation with a counter,
// Escape to close, and a click on the backdrop (but not on the image) to
// close.
//
// EXTENDED (Generated inbox) — additively, so the pin board's calls are
// untouched. The four new props are all optional and all default to the
// original behaviour:
//
//   srcFor        how an id becomes a URL          (default: the resource file)
//   kindFor       image or video                   (default: image)
//   metadataFor   a panel beside/below the media   (default: none)
//   actionsFor    a row of buttons                 (default: none)
//
// Forking a second modal was the alternative and was rejected: two lightboxes
// drift on the things nobody re-tests — Escape, backdrop clicks, arrow keys —
// and the second one is always the one that loses them.

import React, { useCallback, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { ChevronLeft, ChevronRight, X } from 'lucide-react';

// The FULL file, not `/cover` - that route serves
// `thumbnail_path > cover_image_path > original`, and a viewer opened to
// inspect a pin at full screen would be showing a thumbnail.
import { getResourceFileUrl } from '../../../../services/resourceService';

export interface PinLightboxProps {
  /** Ids, in the order the caller shows them. Resource ids by default; any
   *  id `srcFor` understands when that prop is given. */
  resourceIds: string[];
  index: number;
  /** Caption for the whole viewer — a pin is only meaningful with its slot. */
  slotLabel: string;
  onIndexChange: (next: number) => void;
  onClose: () => void;
  /** id → media URL. Defaults to the resource file route. */
  srcFor?: (id: string) => string;
  /** Renders a `<video controls>` instead of an `<img>` for 'video'. */
  kindFor?: (id: string) => 'image' | 'video';
  /** Details panel for the current item (source, model, date, state…). */
  metadataFor?: (id: string) => React.ReactNode;
  /** Action row for the current item, under the media. */
  actionsFor?: (id: string) => React.ReactNode;
}

export const PinLightbox: React.FC<PinLightboxProps> = ({
  resourceIds,
  index,
  slotLabel,
  onIndexChange,
  onClose,
  srcFor = getResourceFileUrl,
  kindFor,
  metadataFor,
  actionsFor,
}) => {
  const { t } = useTranslation();
  const count = resourceIds.length;
  // Modulo rather than clamping: the counter wraps, so the last pin's "next"
  // is the first one instead of a dead button.
  const step = useCallback(
    (delta: number) => {
      if (count === 0) return;
      onIndexChange((index + delta + count) % count);
    },
    [count, index, onIndexChange],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      if (e.key === 'ArrowRight') step(1);
      if (e.key === 'ArrowLeft') step(-1);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose, step]);

  if (count === 0) return null;
  const current = resourceIds[Math.min(Math.max(index, 0), count - 1)];
  const isVideo = kindFor?.(current) === 'video';
  const metadata = metadataFor?.(current);
  const actions = actionsFor?.(current);

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={slotLabel}
      data-testid="pin-lightbox"
      className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/80 p-8"
      onClick={onClose}
    >
      <div className="flex w-full items-center justify-between text-white">
        <span className="text-[13px]">{slotLabel}</span>
        <span className="flex items-center gap-3">
          <span className="text-[12px] tabular-nums opacity-80">
            {index + 1} / {count}
          </span>
          <button
            type="button"
            aria-label={t('common.close', 'Close')}
            data-testid="pin-lightbox-close"
            onClick={onClose}
            className="rounded p-1 hover:bg-white/10"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </span>
      </div>

      <div
        className="flex min-h-0 w-full flex-1 items-center justify-center gap-4"
        // The image and its arrows are inside the backdrop's click target, so
        // the stop is what makes "click outside to close" mean outside.
        onClick={(e) => e.stopPropagation()}
      >
        {count > 1 && (
          <button
            type="button"
            aria-label={t('common.previous', 'Previous')}
            data-testid="pin-lightbox-prev"
            onClick={() => step(-1)}
            className="rounded-full bg-white/10 p-2 text-white hover:bg-white/20"
          >
            <ChevronLeft size={18} aria-hidden="true" />
          </button>
        )}
        {isVideo ? (
          <video
            src={srcFor(current)}
            controls
            data-testid="pin-lightbox-video"
            data-resource-id={current}
            className="max-h-full max-w-full object-contain"
          />
        ) : (
          <img
            src={srcFor(current)}
            alt={slotLabel}
            data-testid="pin-lightbox-image"
            data-resource-id={current}
            className="max-h-full max-w-full object-contain"
          />
        )}
        {count > 1 && (
          <button
            type="button"
            aria-label={t('common.next', 'Next')}
            data-testid="pin-lightbox-next"
            onClick={() => step(1)}
            className="rounded-full bg-white/10 p-2 text-white hover:bg-white/20"
          >
            <ChevronRight size={18} aria-hidden="true" />
          </button>
        )}
      </div>

      {(metadata || actions) && (
        <div
          data-testid="pin-lightbox-panel"
          className="w-full max-w-3xl shrink-0 pt-4 text-white"
          // Inside the backdrop's click target like the media is, so reading
          // the metadata or pressing an action does not close the viewer.
          onClick={(e) => e.stopPropagation()}
        >
          {metadata}
          {actions && <div className="mt-3 flex flex-wrap gap-2">{actions}</div>}
        </div>
      )}
    </div>,
    document.body,
  );
};
