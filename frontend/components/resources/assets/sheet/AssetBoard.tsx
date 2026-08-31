// frontend/components/resources/assets/sheet/AssetBoard.tsx
//
// The pinboard half of the entity sheet (spec screen 4): one large frame for
// the primary slot, a grid of pins for the rest, and an `Arrange` mode that
// reorders the grid and saves the order onto the asset.
//
// Three things worth knowing before editing:
//
//  * EMPTY SLOTS ARE PINS. A slot with no files is drawn dashed with its own
//    `Equip / Generate` pair, so the board shows the SHAPE of a complete asset
//    of this type rather than only what happens to exist. Hiding empties would
//    make "what is this asset still missing" invisible on the page that exists
//    to answer it.
//  * REORDER HAS TWO INPUTS, ONE FUNCTION. Pointer drag is the mouse gesture;
//    ArrowLeft / ArrowRight on a focused handle is the keyboard one. Both call
//    `moveSlot` and both persist through the same `commitOrder`, so the
//    behaviour has one implementation and the tests can drive the keyboard
//    path (JSDOM has no real pointer stream - pinning a synthetic one would
//    pin the harness, not the reorder).
//  * THE PATCH SENDS THE WHOLE `attrs`. `AssetUpdate.attrs` replaces the
//    column; sending `{board_layout}` alone would silently drop every other
//    key the asset carries.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { GripVertical, ImageOff, Loader2, Sparkles, Wand2 } from 'lucide-react';

import { PRIMARY_SLOT } from '../../../assets/assetSlots';
import { updateAsset } from '../../../../services/assetsService';
import type { AssetRow, AssetRowDetail } from '../../../../services/assetsService';
import { getResourceCoverUrl } from '../../../../services/resourceService';
import { slotLabelKey } from '../assetTypeMeta';
import { boardPins, boardSlotOrder, moveSlot, primaryFile } from './assetSheetModel';
import { PinLightbox } from './PinLightbox';

export interface AssetBoardProps {
  scopeId: string;
  detail: AssetRowDetail;
  /** Selected loadout, or null. Filters the loadout-scoped slots. */
  loadoutId: string | null;
  /** System presets cannot be written to - no Arrange, no Equip, no Generate. */
  readOnly: boolean;
  /**
   * TASK 8 HOOK POINTS. The Board renders the buttons and knows which slot
   * each one belongs to; `EquipDialog` / `GenerateMissingDialog` are Task 8's
   * to build. Omitting a handler renders its button DISABLED with the
   * "arrives shortly" title rather than a live button that does nothing -
   * a control that silently no-ops is the failure mode this repo keeps
   * re-learning.
   */
  onEquip?: (slot: string) => void;
  onGenerate?: (slot: string) => void;
  /** The asset row PATCH answered with, after an order save. */
  onAssetUpdated: (row: AssetRow) => void;
  onError: (err: unknown) => void;
}

const PIN_FRAME =
  'relative w-full overflow-hidden rounded-lg border bg-island-2 text-left transition-colors';

export const AssetBoard: React.FC<AssetBoardProps> = ({
  scopeId,
  detail,
  loadoutId,
  readOnly,
  onEquip,
  onGenerate,
  onAssetUpdated,
  onError,
}) => {
  const { t } = useTranslation();

  const [arranging, setArranging] = useState(false);
  const [saving, setSaving] = useState(false);
  /**
   * The order being dragged. Null means "use the asset's own order" - the
   * distinction matters after a save: keeping a local copy forever would let
   * it drift from what the server answered with.
   */
  const [draftOrder, setDraftOrder] = useState<string[] | null>(null);
  const [lightbox, setLightbox] = useState<{ slot: string; index: number } | null>(null);

  const savedOrder = useMemo(
    () => boardSlotOrder(detail.asset_type, detail.attrs),
    [detail.asset_type, detail.attrs],
  );
  const order = draftOrder ?? savedOrder;

  // A different asset discards a stale draft. Without this, opening a second
  // sheet would inherit the first one's in-progress arrangement.
  useEffect(() => {
    setDraftOrder(null);
    setArranging(false);
  }, [detail.id]);

  const pins = useMemo(() => boardPins(detail, loadoutId), [detail, loadoutId]);
  const pinBySlot = useMemo(() => new Map(pins.map((pin) => [pin.slot, pin])), [pins]);

  const primarySlot = PRIMARY_SLOT[detail.asset_type];
  const main = primaryFile(detail, loadoutId);

  const commitOrder = useCallback(
    async (next: string[]) => {
      setDraftOrder(next);
      if (readOnly) return;
      setSaving(true);
      try {
        // The WHOLE attrs object: `AssetUpdate.attrs` replaces the column.
        const row = await updateAsset(scopeId, detail.id, {
          attrs: { ...(detail.attrs ?? {}), board_layout: { slot_order: next } },
        });
        onAssetUpdated(row);
        setDraftOrder(null);
      } catch (err) {
        // The board snaps back to the saved order: leaving the moved pin where
        // the user dropped it would show an arrangement the server does not
        // have.
        setDraftOrder(null);
        onError(err);
      } finally {
        setSaving(false);
      }
    },
    [readOnly, scopeId, detail.id, detail.attrs, onAssetUpdated, onError],
  );

  const moveBy = useCallback(
    (slot: string, delta: number) => {
      const from = order.indexOf(slot);
      if (from < 0) return;
      const next = moveSlot(order, from, from + delta);
      if (next.join(' ') === order.join(' ')) return;
      void commitOrder(next);
    },
    [order, commitOrder],
  );

  // --- Pointer drag ---------------------------------------------------------
  //
  // Pointer events rather than HTML5 drag-and-drop: the same gesture then
  // works with a mouse, a trackpad and a touch screen, and there is no
  // drag-image to fight. `setPointerCapture` keeps the moves coming even when
  // the cursor leaves the tile.

  const dragState = useRef<{ slot: string; pointerId: number } | null>(null);

  const onHandlePointerDown = useCallback(
    (event: React.PointerEvent<HTMLSpanElement>, slot: string) => {
      if (!arranging || readOnly) return;
      event.preventDefault();
      dragState.current = { slot, pointerId: event.pointerId };
      event.currentTarget.setPointerCapture?.(event.pointerId);
    },
    [arranging, readOnly],
  );

  const onHandlePointerMove = useCallback(
    (event: React.PointerEvent<HTMLSpanElement>) => {
      const state = dragState.current;
      if (!state || state.pointerId !== event.pointerId) return;
      const over = document
        .elementFromPoint(event.clientX, event.clientY)
        ?.closest<HTMLElement>('[data-board-slot]');
      const overSlot = over?.dataset.boardSlot;
      if (!overSlot || overSlot === state.slot) return;
      const from = order.indexOf(state.slot);
      const to = order.indexOf(overSlot);
      if (from < 0 || to < 0) return;
      // Reorder live, persist on release: a PATCH per pixel of travel would be
      // a request storm and would race itself.
      setDraftOrder(moveSlot(order, from, to));
    },
    [order],
  );

  const onHandlePointerUp = useCallback(
    (event: React.PointerEvent<HTMLSpanElement>) => {
      const state = dragState.current;
      if (!state || state.pointerId !== event.pointerId) return;
      dragState.current = null;
      event.currentTarget.releasePointerCapture?.(event.pointerId);
      const next = draftOrder;
      if (next && next.join(' ') !== savedOrder.join(' ')) void commitOrder(next);
      else setDraftOrder(null);
    },
    [draftOrder, savedOrder, commitOrder],
  );

  // --- Render ---------------------------------------------------------------

  const label = (slot: string) => t(slotLabelKey(slot), slot);

  const openLightbox = (slot: string, index: number) => setLightbox({ slot, index });

  const lightboxIds = lightbox
    ? lightbox.slot === primarySlot
      ? main
        ? [main.resource_id]
        : []
      : (pinBySlot.get(lightbox.slot)?.files ?? []).map((f) => f.resource_id)
    : [];

  return (
    <section data-testid="asset-board" className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <h3 className="text-[13px] font-medium text-content-2">
          {t('assets.sheet.board', 'Board')}
        </h3>
        {saving && (
          <Loader2 size={12} className="animate-spin text-content-4" aria-hidden="true" />
        )}
        <div className="flex-1" />
        {!readOnly && (
          <button
            type="button"
            data-testid="board-arrange"
            aria-pressed={arranging}
            onClick={() => setArranging((v) => !v)}
            className={`rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors ${
              arranging
                ? 'border-accent bg-[var(--accent-soft)] text-accent'
                : 'border-line-strong bg-card text-content-2 hover:bg-island-2'
            }`}
          >
            {t('assets.sheet.arrange', 'Arrange')}
          </button>
        )}
      </div>

      {arranging && (
        <p className="text-[11px] text-content-4">
          {t(
            'assets.sheet.arrangeHint',
            'Drag a pin by its handle, or focus one and use the arrow keys',
          )}
        </p>
      )}

      {/* The primary slot: one 16:9 frame. It is not part of the arrangeable
          grid - its position is what the slot table means by "primary". */}
      {primarySlot !== null && (
        <div data-testid="board-main" data-slot={primarySlot} className="flex flex-col gap-1">
          {main ? (
            <button
              type="button"
              onClick={() => openLightbox(primarySlot, 0)}
              aria-label={label(primarySlot)}
              className={`${PIN_FRAME} aspect-video border-line hover:border-line-strong`}
            >
              <img
                src={getResourceCoverUrl(main.resource_id)}
                alt={label(primarySlot)}
                data-resource-id={main.resource_id}
                className="h-full w-full object-cover"
              />
            </button>
          ) : (
            <EmptyPin
              slot={primarySlot}
              label={label(primarySlot)}
              aspect="aspect-video"
              readOnly={readOnly}
              onEquip={onEquip}
              onGenerate={onGenerate}
            />
          )}
          <span className="text-[11px] font-medium text-content-3">{label(primarySlot)}</span>
        </div>
      )}

      <div
        data-testid="board-pins"
        className="grid grid-cols-[repeat(auto-fill,minmax(120px,1fr))] gap-2"
      >
        {order.map((slot, position) => {
          const pin = pinBySlot.get(slot);
          const files = pin?.files ?? [];
          const first = files[0];
          return (
            <div
              key={slot}
              data-board-slot={slot}
              data-slot-position={position}
              className="flex flex-col gap-1"
            >
              {first ? (
                <button
                  type="button"
                  data-testid="board-pin"
                  data-slot={slot}
                  onClick={() => openLightbox(slot, 0)}
                  aria-label={label(slot)}
                  className={`${PIN_FRAME} aspect-square border-line hover:border-line-strong`}
                >
                  <img
                    src={getResourceCoverUrl(first.resource_id)}
                    alt={label(slot)}
                    data-resource-id={first.resource_id}
                    className="h-full w-full object-cover"
                  />
                  {files.length > 1 && (
                    <span
                      data-testid="pin-count"
                      className="absolute right-1 top-1 rounded-full border border-line-strong bg-card/90 px-1.5 text-[10px] font-medium tabular-nums text-content-2"
                    >
                      {files.length}
                    </span>
                  )}
                </button>
              ) : (
                <EmptyPin
                  slot={slot}
                  label={label(slot)}
                  aspect="aspect-square"
                  readOnly={readOnly}
                  onEquip={onEquip}
                  onGenerate={onGenerate}
                />
              )}

              <span className="flex items-center gap-1 text-[11px] text-content-3">
                {arranging && !readOnly && (
                  <span
                    role="button"
                    tabIndex={0}
                    data-testid="pin-drag-handle"
                    data-slot={slot}
                    aria-label={t('assets.sheet.reorder', {
                      slot: label(slot),
                      defaultValue: 'Reorder {{slot}}',
                    })}
                    onPointerDown={(e) => onHandlePointerDown(e, slot)}
                    onPointerMove={onHandlePointerMove}
                    onPointerUp={onHandlePointerUp}
                    onPointerCancel={onHandlePointerUp}
                    onKeyDown={(e) => {
                      if (e.key === 'ArrowRight') {
                        e.preventDefault();
                        moveBy(slot, 1);
                      }
                      if (e.key === 'ArrowLeft') {
                        e.preventDefault();
                        moveBy(slot, -1);
                      }
                    }}
                    className="cursor-grab touch-none text-content-4 hover:text-content-2"
                  >
                    <GripVertical size={12} aria-hidden="true" />
                  </span>
                )}
                <span className="truncate">{label(slot)}</span>
                {files.length > 0 && (
                  <span className="tabular-nums text-content-4">{files.length}</span>
                )}
              </span>
            </div>
          );
        })}
      </div>

      {lightbox && lightboxIds.length > 0 && (
        <PinLightbox
          resourceIds={lightboxIds}
          index={lightbox.index}
          slotLabel={label(lightbox.slot)}
          onIndexChange={(index) => setLightbox((l) => (l ? { ...l, index } : l))}
          onClose={() => setLightbox(null)}
        />
      )}
    </section>
  );
};

// --- Empty pin --------------------------------------------------------------

interface EmptyPinProps {
  slot: string;
  label: string;
  aspect: string;
  readOnly: boolean;
  onEquip?: (slot: string) => void;
  onGenerate?: (slot: string) => void;
}

/**
 * A slot with nothing in it: dashed, with the two ways to fill it.
 *
 * On a read-only preset the buttons are absent entirely (there is nothing the
 * user could do). When the handler is missing - Task 8 has not landed - they
 * render DISABLED with a title saying so, rather than as live buttons that
 * swallow the click.
 */
const EmptyPin: React.FC<EmptyPinProps> = ({
  slot,
  label,
  aspect,
  readOnly,
  onEquip,
  onGenerate,
}) => {
  const { t } = useTranslation();
  const soon = t('assets.sheet.comingSoon', 'Arrives shortly');
  return (
    <div
      data-testid="board-empty-pin"
      data-slot={slot}
      className={`${PIN_FRAME} ${aspect} flex flex-col items-center justify-center gap-1.5 border-dashed border-line-strong bg-transparent`}
    >
      <ImageOff size={16} className="text-content-4" aria-hidden="true" />
      {!readOnly && (
        <div className="flex items-center gap-1">
          <button
            type="button"
            data-testid="pin-equip"
            data-slot={slot}
            disabled={!onEquip}
            title={onEquip ? undefined : soon}
            onClick={() => onEquip?.(slot)}
            className="inline-flex items-center gap-1 rounded border border-line-strong px-1.5 py-0.5 text-[10px] font-medium text-content-2 hover:bg-island-2 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Wand2 size={10} aria-hidden="true" />
            {t('assets.sheet.equip', 'Equip')}
          </button>
          <span aria-hidden="true" className="text-[10px] text-content-4">
            &middot;
          </span>
          <button
            type="button"
            data-testid="pin-generate"
            data-slot={slot}
            disabled={!onGenerate}
            title={onGenerate ? undefined : soon}
            onClick={() => onGenerate?.(slot)}
            className="inline-flex items-center gap-1 rounded border border-line-strong px-1.5 py-0.5 text-[10px] font-medium text-content-2 hover:bg-island-2 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Sparkles size={10} aria-hidden="true" />
            {t('assets.sheet.generate', 'Generate')}
          </button>
        </div>
      )}
      <span className="sr-only">{label}</span>
    </div>
  );
};
