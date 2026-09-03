// features/canvas-core/library/LibraryMediaPage.tsx
//
// The panel's Media page: three segments over one grid, plus the two things a
// pick can become — a card on the board, or a reference on a node.
//
// Split out of `LibraryPanel` because that file was carrying two jobs: the
// island's chrome (geometry, key trapping, which page is showing, which node
// it aims at) and the media surface's own arithmetic (quota, scopes, kind
// chips, the two commit paths). The panel keeps the first and hands this the
// target it resolved, so there is still exactly one place that decides whether
// a target is live.
//
// TARGET MODE carries the deleted add-reference popover's contract intact:
// the ceiling comes from the target node's own model, it is handed DOWN to
// `addReferences` (one asset expands to several refs, so slicing the pick list
// would bound picks rather than refs), and every outcome — failed,
// all-duplicate, clamped — is spoken. A pick that changes nothing and says
// nothing is the silent no-op this repo keeps re-learning.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useOptionalToast } from '../../../components/Toast';
import type { AssetType } from '../../../services/assetsService';
import { useCanvasScope } from '../smart/canvasScope';
import { useCanvasReadOnly } from '../smart/nodes/useCanvasReadOnly';
import { useModelCapabilities } from '../smart/nodes/useModelCapabilities';
import { MAX_REFERENCE_IMAGES } from '../smart/refOrder';
import type { GeneratedImageRef, PromptNodeData } from '../smart/types';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { screenToWorld } from '../utils/viewport';
import { addReferences } from './addReferences';
import { writeLibraryDrag } from './dropLibraryItems';
import { LibraryGrid, type LibraryKindChip } from './LibraryGrid';
import {
  useLibrarySearch,
  type AssetScope,
  type GeneratedScope,
  type LibraryItem,
  type LibraryStore,
} from './librarySearch';
import { LibraryPreviewCard } from './LibraryPreviewCard';
import { libraryKey, selectedItems } from './librarySelection';
import { useLibraryStore, type LibraryTarget } from './libraryStore';
import { chipClass, type Label } from './libraryChrome';
import { placeLibraryItems } from './placeLibraryItems';

const SEGMENTS: readonly LibraryStore[] = ['assets', 'uploads', 'generated'];

// Key + English default per label, as lookups rather than keys built by
// template literal. `canvas.library.*` keys are guarded by a scan for
// single-quoted literals (`libraryI18n.test.ts`), and a key assembled at
// runtime is invisible to it — the guard would report fewer keys than the UI
// actually asks for and still pass.
const STORE_LABEL: Record<LibraryStore, Label> = {
  assets: ['canvas.library.storeAssets', 'Assets'],
  uploads: ['canvas.library.storeUploads', 'Uploads'],
  generated: ['canvas.library.storeGenerated', 'Generated'],
};

const ASSET_SCOPE_LABEL: Record<AssetScope, Label> = {
  'this-project': ['canvas.library.scopeThisProject', 'This Project'],
  all: ['canvas.library.scopeAllLibrary', 'All Library'],
};

const GENERATED_SCOPE_LABEL: Record<GeneratedScope, Label> = {
  'this-canvas': ['canvas.library.scopeThisCanvas', 'This Canvas'],
  today: ['canvas.library.scopeToday', 'Today'],
  all: ['canvas.library.scopeAll', 'All'],
};

/** Kind chips per store. Assets deliberately omit `prompt`: a saved prompt is
 *  not media, and it belongs to the Prompts page P3 fills in. */
const KIND_LABEL: Record<'assets' | 'uploads', ReadonlyArray<readonly [string, Label]>> = {
  assets: [
    ['character', ['canvas.library.kindCharacter', 'Character']],
    ['location', ['canvas.library.kindLocation', 'Location']],
    ['prop', ['canvas.library.kindProp', 'Prop']],
    ['costume', ['canvas.library.kindCostume', 'Costume']],
    ['audio', ['canvas.library.kindAudio', 'Audio']],
  ],
  uploads: [
    ['image', ['canvas.library.kindImage', 'Image']],
    ['video', ['canvas.library.kindVideo', 'Video']],
    ['audio', ['canvas.library.kindAudio', 'Audio']],
    ['doc', ['canvas.library.kindDoc', 'Doc']],
  ],
};

export interface LibraryMediaPageProps {
  /** The panel's aim, or null. `target.title` is only what the node was CALLED
   *  when the panel opened; the id is the authority. */
  target: LibraryTarget | null;
  /** The target node's live data, or null when it no longer resolves. The
   *  panel owns that lookup so exactly one place decides "is the aim live". */
  targetData: PromptNodeData | null;
}

export function LibraryMediaPage({
  target,
  targetData,
}: LibraryMediaPageProps): React.ReactElement {
  const { t } = useTranslation();
  const toast = useOptionalToast();
  const { scopeId } = useCanvasScope();
  const canvasId = useCanvasCoreStore((s) => s.canvasId);
  const projectId = useCanvasCoreStore((s) => s.projectId);
  // A viewer gets the shelf and nothing that writes. `L` is view-only and
  // stays live in a read-only session, so the panel MOUNTS there — and a
  // panel that offered Place on Canvas would hand a viewer a button whose
  // write `markDirty` silently swallows.
  const readOnly = useCanvasReadOnly();

  const open = useLibraryStore((s) => s.open);
  const mediaStore = useLibraryStore((s) => s.mediaStore);
  const query = useLibraryStore((s) => s.query);
  const kind = useLibraryStore((s) => s.kind);
  const assetScope = useLibraryStore((s) => s.assetScope);
  const generatedScope = useLibraryStore((s) => s.generatedScope);
  const selection = useLibraryStore((s) => s.selection);

  const [busy, setBusy] = useState(false);
  // The cell the pointer is resting on, for the hover preview. Cleared on
  // leave, on a drag starting (a card floating over the drop path is in the
  // way of the gesture) and on every control that swaps the shelf underneath —
  // the anchor rect belongs to a cell that is about to stop existing, and a
  // card left pinned to it would describe the wrong item at the wrong place.
  const [hover, setHover] = useState<{ item: LibraryItem; rect: DOMRect } | null>(null);
  useEffect(() => setHover(null), [mediaStore, kind, assetScope, generatedScope, query]);

  const model = targetData?.gen?.model ?? null;
  const caps = useModelCapabilities(model);
  // `null` capabilities means UNKNOWN — still loading, fetch failed, or a model
  // absent from the map — and every consumer of that hook renders FULL support.
  // Falling back to 0 would disable the button on the day the endpoint hiccups.
  const max = caps?.max_refs ?? MAX_REFERENCE_IMAGES;
  const used = ((targetData?.manual_refs ?? []) as GeneratedImageRef[]).length;
  const inTargetMode =
    !readOnly && target !== null && target.kind === 'prompt' && targetData !== null;
  const atLimit = inTargetMode && used >= max;

  // "This Project" needs a project. `fetchLibraryAssets` falls through to the
  // scope-wide search when there is none, so a project-less canvas showed the
  // chip PRESSED over every asset in the workspace — the P1 label over a
  // scope-wide list that Plan-time ruling 1 exists to prevent. The chip is
  // disabled below with the reason as its title; this is the other half, so
  // a scope left over from the last canvas cannot mislabel this one's shelf.
  const projectScopeOk = projectId !== null && projectId !== undefined && projectId !== '';
  const effectiveAssetScope: AssetScope = projectScopeOk ? assetScope : 'all';

  const result = useLibrarySearch(query, {
    scopeId,
    // Nothing fetches while the panel is closed — it stays mounted for the
    // whole canvas session, not just while it is visible.
    stores: open ? [mediaStore] : [],
    assetType: mediaStore === 'assets' ? ((kind as AssetType | null) ?? null) : null,
    assetScope: effectiveAssetScope,
    projectId,
    uploadKinds: mediaStore === 'uploads' ? (kind ?? '') : '',
    generatedScope,
    canvasId,
  });
  const current = result[mediaStore];
  const items = current.items;
  const chosen = useMemo(() => selectedItems(selection, items), [selection, items]);

  const setSelection = useCallback((keys: string[]) => {
    useLibraryStore.getState().setSelection(keys);
  }, []);

  // ── Place on canvas ─────────────────────────────────────────────────────
  const doPlace = useCallback(
    (picked: LibraryItem[]) => {
      if (readOnly || busy || picked.length === 0) return;
      setBusy(true);
      // The world point under the SCREEN centre — what the user is looking
      // at. `null` would mean "lay them out in project lanes", which puts a
      // card to the right of everything already on the board; the surface
      // culls off-viewport nodes, so that answer is a toast and a blank
      // screen. The guard is defensive: the store types `viewport` as always
      // present, and a canvas row with no `viewport_json` still resolves to
      // the identity one.
      const vp = useCanvasCoreStore.getState().viewport;
      const centre = vp
        ? screenToWorld({ x: window.innerWidth / 2, y: window.innerHeight / 2 }, vp)
        : null;
      void placeLibraryItems(picked, scopeId, centre)
        .then((r) => {
          if (r.failed.length > 0) {
            toast?.addToast(
              t('canvas.library.placeFailed', {
                count: r.failed.length,
                defaultValue: '{{count}} could not be placed',
              }),
              'error',
            );
            return;
          }
          if (r.inserted === 0 && r.skipped > 0) {
            // Nothing changed, and the board looks identical — clearing the
            // selection here would be indistinguishable from success.
            toast?.addToast(
              t('canvas.library.alreadyOnCanvas', {
                count: r.skipped,
                defaultValue: '{{count}} already on this canvas',
              }),
              'info',
            );
            return;
          }
          toast?.addToast(
            t('canvas.library.placedSome', {
              count: r.inserted,
              defaultValue: '{{count}} placed on the canvas',
            }),
            'success',
          );
          setSelection([]);
        })
        // Every known rejection is typed inside `placeLibraryItems`, so this
        // is the guard against a future one — an unhandled rejection here
        // would look exactly like a placement that worked.
        .catch((err: unknown) => {
          console.error('[LibraryMediaPage] place failed:', err);
          toast?.addToast(
            t('canvas.library.placeFailed', {
              count: picked.length,
              defaultValue: '{{count}} could not be placed',
            }),
            'error',
          );
        })
        .finally(() => setBusy(false));
    },
    [busy, readOnly, scopeId, setSelection, t, toast],
  );

  // ── Add as references ───────────────────────────────────────────────────
  // EVERY path into an add goes through here — the primary button, a double
  // click and the grid's Enter fallback all call it — so the ceiling has to be
  // enforced here rather than on the button's `disabled` prop alone.
  const doAddRefs = useCallback(
    (picked: LibraryItem[]) => {
      if (readOnly || busy || atLimit || !target || picked.length === 0) return;
      setBusy(true);
      // The ceiling is handed DOWN rather than applied here: one asset
      // resolves to one ref per primary-slot file, so a sliced pick list would
      // let a single asset carry the node past `max_refs` with nothing said.
      void addReferences(target.nodeId, picked, scopeId, { maxRefs: max })
        .then((r) => {
          if (r.failed.length > 0) {
            toast?.addToast(
              t('canvas.library.someFailed', {
                count: r.failed.length,
                defaultValue: '{{count}} could not be added as references',
              }),
              'error',
            );
            return;
          }
          if (r.added === 0 && r.skipped > 0) {
            // Nothing changed, and the grid draws no "already referenced"
            // marker — so staying quiet would look exactly like success.
            toast?.addToast(
              t('canvas.library.alreadyReferenced', {
                count: r.skipped,
                defaultValue: '{{count}} already on this node',
              }),
              'info',
            );
            return;
          }
          if (r.clamped > 0) {
            toast?.addToast(
              t('canvas.library.quotaClamped', {
                count: r.clamped,
                defaultValue: '{{count}} references not added · quota reached',
              }),
              'info',
            );
            return;
          }
          setSelection([]);
        })
        // Same guard as `doPlace` above, for the same reason.
        .catch((err: unknown) => {
          console.error('[LibraryMediaPage] add references failed:', err);
          toast?.addToast(
            t('canvas.library.someFailed', {
              count: picked.length,
              defaultValue: '{{count}} could not be added as references',
            }),
            'error',
          );
        })
        .finally(() => setBusy(false));
    },
    [atLimit, busy, max, readOnly, scopeId, setSelection, t, target, toast],
  );

  const kinds: LibraryKindChip[] | undefined =
    mediaStore === 'generated'
      ? undefined
      : [
          { value: null, label: t('canvas.library.kindAll', 'All') },
          ...KIND_LABEL[mediaStore].map(([value, [key, english]]) => ({
            value,
            label: t(key, english),
          })),
        ];

  const scopeChips: Array<[string, Label]> =
    mediaStore === 'assets'
      ? (Object.entries(ASSET_SCOPE_LABEL) as Array<[string, Label]>)
      : mediaStore === 'generated'
        ? (Object.entries(GENERATED_SCOPE_LABEL) as Array<[string, Label]>)
        : [];
  const activeScope = mediaStore === 'assets' ? effectiveAssetScope : generatedScope;

  const consequence = readOnly
    ? t('canvas.library.readOnlyConsequence', 'Browse only · this canvas is read-only')
    : inTargetMode
    ? t('canvas.library.targetConsequence', {
        title: target.title,
        model: model || t('canvas.library.noModel', 'No Model Yet'),
        used,
        max,
        defaultValue:
          'Adding references to {{title}} · sent to {{model}} · {{used}} / {{max}} used',
      })
    : t('canvas.library.browseConsequence', 'Place on canvas, or drag onto a node');

  // What will REALLY be sent, after the node's remaining room — in FILES,
  // which is what the footer's word says (spec §3.3: "目标模型 max_refs 裁剪后
  // 真正会送的数量"). One asset expands to one ref per primary-slot file, so
  // for a pick list containing an asset the number is NOT KNOWABLE here: only
  // the detail rows say how many files it carries, and this is a footer, not
  // a fetch. `null` hides the count rather than printing "1 file" over a send
  // of four — the hover preview is where a per-asset answer already lives.
  const fileCount =
    inTargetMode && !chosen.some((i) => i.store === 'assets')
      ? Math.max(0, Math.min(chosen.length, max - used))
      : null;

  const placeAction = {
    label: t('canvas.library.place', 'Place on Canvas'),
    disabled: busy,
    onClick: doPlace,
  };
  const referenceAction = {
    label: t('canvas.library.addReferences', {
      count: chosen.length,
      defaultValue: 'Add {{count}} References',
    }),
    disabled: busy || atLimit,
    onClick: doAddRefs,
  };

  return (
    <>
      <div className="flex flex-wrap items-center gap-1 border-b border-canvas-line px-2 py-1.5">
        {SEGMENTS.map((s) => (
          <button
            key={s}
            type="button"
            data-testid={`library-segment-${s}`}
            aria-pressed={mediaStore === s}
            onClick={() => useLibraryStore.getState().setMediaStore(s)}
            className={chipClass(mediaStore === s)}
          >
            {t(STORE_LABEL[s][0], STORE_LABEL[s][1])}
          </button>
        ))}
        <span className="flex-1" />
        <button
          type="button"
          data-testid="library-select-all"
          disabled={items.length === 0}
          onClick={() => setSelection(items.map(libraryKey))}
          className="nodrag shrink-0 text-[10px] text-canvas-muted hover:text-canvas-text disabled:opacity-40"
        >
          {t('canvas.library.selectAll', 'Select All')}
        </button>
      </div>

      {scopeChips.length > 0 && (
        <div className="flex flex-wrap gap-1 border-b border-canvas-line px-2 py-1.5">
          {scopeChips.map(([value, [key, english]]) => {
            const noProject = mediaStore === 'assets' && value === 'this-project' && !projectScopeOk;
            return (
              <button
                key={value}
                type="button"
                data-testid={`library-scope-${value}`}
                aria-pressed={activeScope === value}
                disabled={noProject}
                title={
                  noProject
                    ? t('canvas.library.noProject', 'This canvas has no project')
                    : undefined
                }
                onClick={() =>
                  mediaStore === 'assets'
                    ? useLibraryStore.getState().setAssetScope(value as AssetScope)
                    : useLibraryStore.getState().setGeneratedScope(value as GeneratedScope)
                }
                className={`${chipClass(activeScope === value)} disabled:cursor-not-allowed disabled:opacity-40`}
              >
                {t(key, english)}
              </button>
            );
          })}
        </div>
      )}

      <LibraryGrid
        className="min-h-0 flex-1"
        items={items}
        loading={current.loading}
        error={current.error}
        onRetry={current.reload}
        query={query}
        onQueryChange={(q) => useLibraryStore.getState().setQuery(q)}
        searchPlaceholder={t('canvas.library.searchPlaceholder', 'Search Library…')}
        kinds={kinds}
        activeKind={kind}
        onKindChange={(k) => useLibraryStore.getState().setKind(k)}
        selection={selection}
        onSelectionChange={setSelection}
        consequence={consequence}
        fileCount={fileCount}
        note={
          atLimit
            ? t('canvas.library.quotaFull', {
                used,
                max,
                defaultValue: '{{used}} / {{max}} references used · remove one on the node',
              })
            : null
        }
        primaryAction={readOnly ? undefined : inTargetMode ? referenceAction : placeAction}
        secondaryAction={!readOnly && inTargetMode ? placeAction : undefined}
        onItemActivate={(item) => (inTargetMode ? doAddRefs([item]) : doPlace([item]))}
        onItemDragStart={
          // A viewer gets no drag at all: every landing a drop resolves into
          // writes, and `draggable` is set from this prop being present, so
          // withdrawing it is what keeps a read-only session from starting a
          // gesture it could only refuse at the end.
          readOnly
            ? undefined
            : (item, e) => {
                setHover(null);
                // Drag the SELECTION when the grabbed cell is part of it, else
                // just that cell. Dragging one item out of a five-item
                // selection and getting five is the behaviour every file
                // manager has.
                const chosen = selectedItems(selection, current.items);
                const dragging = chosen.some((i) => libraryKey(i) === libraryKey(item))
                  ? chosen
                  : [item];
                writeLibraryDrag(e.dataTransfer, dragging);
                e.dataTransfer.effectAllowed = 'copy';
              }
        }
        onItemHover={(item, rect) => setHover(item && rect ? { item, rect } : null)}
        emptyLabel={t('canvas.library.empty', 'Nothing Here Yet')}
        targetRowHeight={96}
      />

      {hover && (
        <LibraryPreviewCard
          item={hover.item}
          anchor={hover.rect}
          model={inTargetMode ? model : null}
          scopeId={scopeId}
        />
      )}
    </>
  );
}

export default LibraryMediaPage;
