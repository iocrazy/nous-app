/**
 * EntityAssetStrip (CC5 asset backlink) — the bible-card asset strip shared
 * by CharacterLibrary and EntityLibrary. Canvas generations dispatched from
 * an entity branch carry entity_kind/entity_id in generated_media.params;
 * this strip queries them by entity and renders click-to-lightbox thumbs.
 */

import { Film, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  fetchEntityGenerations,
  generatedMediaCoverUrl,
  generatedMediaStreamUrl,
  type GenerationItem,
} from '../../services/generatedMediaService';

export interface EntityAssetStripProps {
  entityKind: 'character' | 'location' | 'prop';
  entityId: string;
}

export function EntityAssetStrip({ entityKind, entityId }: EntityAssetStripProps) {
  const { t } = useTranslation();
  const [items, setItems] = useState<GenerationItem[]>([]);
  const [open, setOpen] = useState<GenerationItem | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchEntityGenerations(entityKind, entityId)
      .then((page) => {
        // Guard the shape at the boundary too: never trust `page.items` to be
        // an array (regression #1457 — an undefined here crashed the card into
        // its error boundary via the items.length read below).
        if (!cancelled) setItems(Array.isArray(page?.items) ? page.items : []);
      })
      .catch((err) => {
        // Asset strip is decoration — a failed fetch degrades to the empty
        // hint instead of breaking the card.
        console.error('[EntityAssetStrip] fetch failed:', err);
        if (!cancelled) setItems([]);
      });
    return () => {
      cancelled = true;
    };
  }, [entityKind, entityId]);

  if (items.length === 0) {
    return (
      <span className="text-[10px] text-ink-600">
        {t('libEntities.assetsHint', 'Generated assets appear here')}
      </span>
    );
  }

  return (
    <>
      <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto">
        {items.map((item) =>
          item.media_kind === 'video' ? (
            <button
              key={item.id}
              type="button"
              aria-label={t('libEntities.videoAsset', 'Video asset')}
              onClick={() => setOpen(item)}
              className="flex h-12 w-12 shrink-0 items-center justify-center rounded-md bg-ink-800 text-ink-400 hover:text-ink-200"
            >
              <Film size={16} />
            </button>
          ) : (
            <img
              key={item.id}
              src={generatedMediaCoverUrl(item.id)}
              alt={item.prompt || t('libEntities.generatedAsset', 'Generated asset')}
              loading="lazy"
              onClick={() => setOpen(item)}
              className="h-12 w-12 shrink-0 cursor-pointer rounded-md object-cover hover:opacity-80"
            />
          ),
        )}
      </div>
      {open && (
        <div
          role="dialog"
          aria-modal="true"
          onClick={() => setOpen(null)}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-6"
        >
          <button
            type="button"
            aria-label={t('common.close', 'Close')}
            onClick={() => setOpen(null)}
            className="absolute right-4 top-4 rounded-full bg-ink-800/80 p-2 text-ink-200 hover:bg-ink-700"
          >
            <X size={16} />
          </button>
          {open.media_kind === 'video' ? (
            <video
              src={generatedMediaStreamUrl(open.id)}
              controls
              autoPlay
              onClick={(e) => e.stopPropagation()}
              className="max-h-full max-w-full rounded-lg"
            />
          ) : (
            <img
              src={generatedMediaCoverUrl(open.id)}
              alt={open.prompt || t('libEntities.generatedAsset', 'Generated asset')}
              onClick={(e) => e.stopPropagation()}
              className="max-h-full max-w-full rounded-lg object-contain"
            />
          )}
        </div>
      )}
    </>
  );
}
