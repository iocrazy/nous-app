// features/canvas-core/library/LibraryPreviewCard.tsx
//
// The hover preview: a bigger look at the cell under the pointer, and — for an
// asset — the answer to "which of its files does this model actually get?"
// BEFORE the pick is committed. Spec §3.3, and spec §1 row 7: today that
// answer only exists after a run has already spent the tokens.
//
// Three things about it are load-bearing rather than styling:
//
//  1. It waits `PREVIEW_DELAY_MS`. A card that opens on every pointer crossing
//     turns a scan across the shelf into a strobe, and it would fetch a detail
//     row for every cell the pointer merely passed over.
//  2. It is `pointer-events-none`. It is anchored next to the cell that
//     summoned it and can overlap the cell beside that one; if it took the
//     pointer, moving toward it would end the hover that produced it and the
//     card would fight itself.
//  3. It portals to `document.body` and positions in VIEWPORT coordinates.
//     The panel is a clipped, scrolling island — a card positioned inside it
//     would be cut off at the shelf's edge, which is exactly where a preview
//     of the right-hand column needs to be.

import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { ImageOff } from 'lucide-react';

import { slotLabelKey } from '../../../components/resources/assets/assetTypeMeta';
import {
  fetchAssetDetail,
  type AssetRowDetail,
  type AssetType,
} from '../../../services/assetsService';
import { useModelCapabilities } from '../smart/nodes/useModelCapabilities';
import type { LibraryItem } from './librarySearch';
import { referenceCut } from './referenceCut';

/** How long the pointer must rest on a cell before the card opens. */
export const PREVIEW_DELAY_MS = 400;

const CARD_WIDTH = 320;
/** Breathing room between the cell and the card, and off the viewport edge. */
const GAP = 8;

export interface LibraryPreviewCardProps {
  item: LibraryItem;
  /** Anchor rect of the hovered cell, in viewport coordinates. */
  anchor: DOMRect;
  /** The target node's generation model, or null when browsing. */
  model: string | null;
  scopeId: string;
}

export function LibraryPreviewCard({
  item,
  anchor,
  model,
  scopeId,
}: LibraryPreviewCardProps): React.ReactElement | null {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<AssetRowDetail | null>(null);
  const [failed, setFailed] = useState(false);

  const isAsset = item.store === 'assets';
  const key = `${item.store}:${item.id}`;

  // Restart the clock for every new cell: the state below is keyed to the item
  // that is showing, so a card left open from the previous one would draw the
  // old asset's file table beside the new cell.
  useEffect(() => {
    setOpen(false);
    setDetail(null);
    setFailed(false);
    const timer = setTimeout(() => setOpen(true), PREVIEW_DELAY_MS);
    return () => clearTimeout(timer);
  }, [key]);

  useEffect(() => {
    if (!open || !isAsset) return undefined;
    let live = true;
    fetchAssetDetail(scopeId, item.id)
      .then((row) => {
        if (live) setDetail(row);
      })
      .catch((err: unknown) => {
        // Loud, then visible. A silent catch here would leave the card drawing
        // an empty table, which reads as "this asset has no files" — a
        // statement about the asset rather than about the request.
        console.error('[LibraryPreviewCard] asset detail fetch failed:', err);
        if (live) setFailed(true);
      });
    return () => {
      live = false;
    };
  }, [open, isAsset, scopeId, item.id]);

  // Hooks run unconditionally — the early return is below them.
  const caps = useModelCapabilities(model);
  // `null` is UNKNOWN, not zero: every consumer of this hook renders FULL
  // support on it, and reading it as a ceiling of 0 would tell the user their
  // references are all being dropped on the day the endpoint hiccups.
  const maxRefs = caps?.max_refs ?? null;

  if (!open) return null;

  const rows = detail ? referenceCut(detail.files, item.kind as AssetType, null, maxRefs) : [];

  const flip = anchor.right + CARD_WIDTH > window.innerWidth;
  const left = flip ? anchor.left - CARD_WIDTH - GAP : anchor.right + GAP;

  // A note is only rendered where there is something true to say. With a model
  // but no ceiling (capabilities unknown) every row already says Sends, and a
  // note naming a ceiling nobody knows would be the one sentence on this card
  // that is not backed by an answer.
  const note = !isAsset
    ? null
    : model === null
      ? t('canvas.library.previewNoModel', 'Pick a target node to see which files are sent.')
      : maxRefs !== null
        ? t('canvas.library.modelNote', {
            model,
            max: maxRefs,
            defaultValue: 'On {{model}}, {{max}} references are sent.',
          })
        : null;

  return createPortal(
    <div
      data-testid="library-preview"
      style={{
        position: 'fixed',
        left: `${Math.max(GAP, left)}px`,
        top: `${Math.max(GAP, anchor.top)}px`,
        width: `${CARD_WIDTH}px`,
      }}
      className="canvas-island pointer-events-none z-50 overflow-hidden rounded-xl p-2 text-xs"
    >
      {item.thumbUrl ? (
        <img
          src={item.thumbUrl}
          alt=""
          className="mb-1.5 h-40 w-full rounded-md object-cover"
        />
      ) : (
        <span className="mb-1.5 flex h-40 w-full items-center justify-center rounded-md bg-canvas-card text-canvas-muted">
          <ImageOff size={18} />
        </span>
      )}

      <p className="truncate font-medium text-canvas-text">{item.title}</p>

      {failed && (
        <p data-testid="library-preview-error" className="mt-1.5 text-[11px] text-warn">
          {t('canvas.library.previewFailed', 'Could not load this asset')}
        </p>
      )}

      {rows.length > 0 && (
        <ul className="mt-1.5 space-y-0.5">
          {rows.map((r) => (
            <li
              key={r.resourceId}
              data-testid="library-preview-row"
              data-slot={r.slot}
              data-sends={r.sends ? 'true' : 'false'}
              className="flex items-center justify-between gap-2 text-[11px]"
            >
              <span className="truncate text-canvas-muted">{t(slotLabelKey(r.slot), r.slot)}</span>
              {r.sends ? (
                <span className="shrink-0 text-ok">
                  {t('canvas.library.previewSends', 'Sends')}
                </span>
              ) : (
                <span className="shrink-0 text-warn">
                  {t('canvas.library.previewCut', {
                    max: maxRefs ?? 0,
                    defaultValue: 'Cut · max {{max}}',
                  })}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      {note !== null && (
        <p data-testid="library-preview-note" className="mt-1.5 text-[10px] text-canvas-muted">
          {note}
        </p>
      )}
    </div>,
    document.body,
  );
}

export default LibraryPreviewCard;
