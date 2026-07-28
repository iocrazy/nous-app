// frontend/components/resources/GalleryViewer.tsx
// Gallery entity (PR-A) detail viewer: a lightweight 1/n pager for the ordered
// child images of a first-class gallery resource. Mirrors the parse-GALLERY
// SlidePlayer interaction (arrows / dots / 1-of-n counter / ← → keys) but stays
// decoupled from parsed_media — children are ordinary image resources served
// through the token'd /media/{id} URL the rest of the detail page already uses.
import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronLeft, ChevronRight, ChevronUp, ImageOff, Sparkles } from 'lucide-react';
import Loading from '../common/Loading';
import {
  getGalleryItems,
  getResourceMediaUrl,
  type GalleryChildItem,
} from '../../services/resourceService';
import { ResourcePromptSection } from './ResourcePromptSection';

interface GalleryViewerProps {
  galleryId: string;
  /** Signed media token — appended as a query param so the <img> loads without
   *  an Authorization header (same transport the detail page image uses). */
  mediaToken?: string;
}

export const GalleryViewer: React.FC<GalleryViewerProps> = ({ galleryId, mediaToken }) => {
  const { t } = useTranslation();
  const [items, setItems] = useState<GalleryChildItem[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [promptOpen, setPromptOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const fetchItems = async () => {
      setIsLoading(true);
      setLoadError(null);
      try {
        const data = await getGalleryItems(galleryId);
        if (cancelled) return;
        // Defensive: honor the position order even if the API returns unsorted.
        const ordered = [...data].sort((a, b) => a.position - b.position);
        setItems(ordered);
        setCurrentIndex(0);
      } catch (err) {
        console.error('Failed to load gallery items:', err);
        if (!cancelled) {
          setLoadError(
            err instanceof Error ? err.message : 'Failed to load gallery',
          );
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };
    void fetchItems();
    return () => { cancelled = true; };
  }, [galleryId]);

  const goTo = useCallback((index: number) => {
    setCurrentIndex((prev) => {
      if (items.length === 0) return prev;
      return Math.max(0, Math.min(items.length - 1, index));
    });
  }, [items.length]);

  const goNext = useCallback(() => goTo(currentIndex + 1), [goTo, currentIndex]);
  const goPrev = useCallback(() => goTo(currentIndex - 1), [goTo, currentIndex]);

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (
        target.tagName === 'INPUT'
        || target.tagName === 'TEXTAREA'
        || target.isContentEditable
      ) return;
      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        goPrev();
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        goNext();
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [goNext, goPrev]);

  // An open prompt drawer refers to the previous child's resourceId — close
  // it on navigation instead of silently re-pointing at the new one.
  useEffect(() => {
    setPromptOpen(false);
  }, [currentIndex]);

  if (isLoading) {
    return (
      <div className="w-full h-full min-h-[16rem] bg-ink-950 rounded-lg flex items-center justify-center text-ink-500">
        <Loading center />
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="w-full h-full min-h-[16rem] bg-ink-950 rounded-lg flex flex-col items-center justify-center gap-3">
        <ImageOff size={48} className="text-ink-600" />
        <p className="text-ink-400 text-sm">{loadError}</p>
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="w-full h-full min-h-[16rem] bg-ink-950 rounded-lg flex flex-col items-center justify-center gap-3">
        <ImageOff size={48} className="text-ink-600" />
        <p className="text-ink-400 text-sm">
          {t('resources.gallery.empty', 'This gallery has no images yet')}
        </p>
      </div>
    );
  }

  const current = items[currentIndex];

  return (
    <div className="w-full h-full min-h-[16rem] bg-ink-950 rounded-lg overflow-hidden flex flex-col">
      {/* Pager — image + arrows + the existing dots/counter overlay. */}
      <div className="relative flex-1 min-w-0 min-h-0 select-none">
        <div className="absolute inset-0 flex items-center justify-center">
          <img
            key={current.id}
            src={getResourceMediaUrl(String(current.id), mediaToken)}
            alt={current.filename || `Image ${currentIndex + 1}`}
            className="max-w-full max-h-full object-contain"
            draggable={false}
          />
        </div>

        {currentIndex > 0 && (
          <button
            type="button"
            onClick={goPrev}
            className="absolute left-2 top-1/2 -translate-y-1/2 p-2 bg-black/50 hover:bg-black/70 text-white/80 hover:text-white rounded-full transition-colors backdrop-blur-sm"
            aria-label={t('resources.gallery.prev', 'Previous image')}
          >
            <ChevronLeft size={24} />
          </button>
        )}

        {currentIndex < items.length - 1 && (
          <button
            type="button"
            onClick={goNext}
            className="absolute right-2 top-1/2 -translate-y-1/2 p-2 bg-black/50 hover:bg-black/70 text-white/80 hover:text-white rounded-full transition-colors backdrop-blur-sm"
            aria-label={t('resources.gallery.next', 'Next image')}
          >
            <ChevronRight size={24} />
          </button>
        )}

        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/70 to-transparent pt-8 pb-3 px-4">
          {items.length > 1 && (
            <div className="flex items-center justify-center gap-1.5 flex-wrap">
              {items.map((it, idx) => (
                <button
                  type="button"
                  key={it.id}
                  onClick={() => goTo(idx)}
                  className={`rounded-full transition-all ${
                    idx === currentIndex
                      ? 'w-2.5 h-2.5 bg-white'
                      : 'w-1.5 h-1.5 bg-white/40 hover:bg-white/60'
                  }`}
                  aria-label={t('resources.gallery.goTo', 'Go to image {{n}}', { n: idx + 1 })}
                />
              ))}
            </div>
          )}
          <p className="text-center text-xs text-white/60 mt-1.5 tabular-nums">
            {currentIndex + 1} / {items.length}
          </p>
        </div>
      </div>

      {/* Bottom info area — the current child IS a real resource, so its
          per-image prompt is just that child's own gen_prompt* columns via
          the existing ResourcePromptSection (spec 2026-07-28-prompt-dataline
          §2: "zero new storage" for upload galleries). Rendered here
          collapsed-by-default behind a compact toggle rather than always-on:
          the full section (header + Generate + Send-to-Canvas) is sized for
          a sidebar panel and reads as too heavy stacked under every gallery
          view, and lazy-mounting it also skips the resource fetch entirely
          until the user actually wants it. */}
      <div className="shrink-0 border-t border-ink-800 bg-ink-900">
        <button
          type="button"
          onClick={() => setPromptOpen((v) => !v)}
          className="w-full flex items-center gap-1.5 px-4 py-2 text-ink-500 hover:text-ink-300 transition-colors"
        >
          <Sparkles size={11} />
          <span className="text-[11px] font-semibold uppercase tracking-widest">
            {t('resources.infoPanel.promptSection', 'Prompt')}
          </span>
          <span className="ml-auto">
            {promptOpen ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          </span>
        </button>
        {promptOpen && (
          <div className="max-h-64 overflow-y-auto pb-2">
            <ResourcePromptSection resourceId={String(current.id)} />
          </div>
        )}
      </div>
    </div>
  );
};

export default GalleryViewer;
