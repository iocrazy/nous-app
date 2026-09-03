// features/canvas-core/library/LibraryReferencePopover.tsx
//
// The Add-reference affordance on a prompt node, rebuilt on LibraryGrid.
//
// What it replaces could not search (its `query` was hard-coded to '' — there
// was no input box to type into), could not multi-select, and never said what
// the model would do with the pick. All three are the same defect: the picker
// answered "which file in my library?" without ever answering "and then what?".
//
// It portals to the body because it opens from INSIDE a React Flow node, and
// it renders three stores as three segments so a generation from yesterday is
// reachable without a detour through Save To Uploads.

import React, { useCallback, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';

import { useCanvasScope } from '../smart/canvasScope';
import { useModelCapabilities } from '../smart/nodes/useModelCapabilities';
import { MAX_REFERENCE_IMAGES } from '../smart/refOrder';
import type { GeneratedImageRef, PromptNodeData } from '../smart/types';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { addReferences } from './addReferences';
import { LibraryGrid } from './LibraryGrid';
import { useLibrarySearch, type LibraryItem, type LibraryStore } from './librarySearch';
import type { LibraryItemKey } from './librarySelection';

const SEGMENTS: readonly LibraryStore[] = ['uploads', 'generated', 'assets'];

// Key and English default per segment, as a lookup rather than a key built by
// template literal. `canvas.library.*` keys are guarded by a scan for
// single-quoted literals (`libraryI18n.test.ts`), and a key assembled at
// runtime is invisible to it — the guard would report three fewer keys than
// the UI actually asks for and still pass.
const STORE_LABEL: Record<LibraryStore, [string, string]> = {
  uploads: ['canvas.library.storeUploads', 'Uploads'],
  generated: ['canvas.library.storeGenerated', 'Generated'],
  assets: ['canvas.library.storeAssets', 'Assets'],
};

export interface LibraryReferencePopoverProps {
  nodeId: string;
  /** The prompt's own generation model, for the ceiling and the header line. */
  model: string | null;
  onClose: () => void;
}

export function LibraryReferencePopover({
  nodeId,
  model,
  onClose,
}: LibraryReferencePopoverProps): React.ReactElement {
  const { t } = useTranslation();
  const { scopeId } = useCanvasScope();
  const canvasId = useCanvasCoreStore((s) => s.canvasId);
  const nodes = useCanvasCoreStore((s) => s.nodes);
  const caps = useModelCapabilities(model);

  const [store, setStore] = useState<LibraryStore>('uploads');
  const [query, setQuery] = useState('');
  const [selection, setSelection] = useState<LibraryItemKey[]>([]);
  const [busy, setBusy] = useState(false);
  // Every outcome that must not close the popover writes here: a typed
  // failure, an all-duplicate pick, and a pick trimmed by the ceiling.
  const [notice, setNotice] = useState<string | null>(null);

  const used = useMemo(() => {
    const data = (nodes.find((n) => (n as { id?: unknown }).id === nodeId) as
      | { data?: PromptNodeData }
      | undefined)?.data;
    return ((data?.manual_refs ?? []) as GeneratedImageRef[]).length;
  }, [nodes, nodeId]);

  // `null` capabilities means UNKNOWN — still loading, fetch failed, or a model
  // absent from the map — and every consumer of that hook renders FULL support.
  // Falling back to 0 would disable the button on the day the endpoint hiccups.
  const max = caps?.max_refs ?? MAX_REFERENCE_IMAGES;
  const atLimit = used >= max;

  const result = useLibrarySearch(query, {
    scopeId,
    stores: SEGMENTS,
    canvasId,
    generatedScope: 'this-canvas',
    uploadKinds: 'image',
  });
  const current = result[store];

  // EVERY path into an add goes through here — the primary button, a double
  // click and the grid's Enter fallback all call it — so the ceiling has to be
  // enforced here rather than on the button's `disabled` prop. `LibraryGrid`
  // routes Enter to `onItemActivate` precisely WHEN the primary is disabled,
  // so a gate that lives only on the button is a gate the keyboard walks past.
  const commit = useCallback(
    (items: LibraryItem[]) => {
      if (atLimit) return;
      if (busy) return;
      setBusy(true);
      setNotice(null);
      // The ceiling is handed DOWN rather than applied here. Slicing the pick
      // list would bound picks, not refs, and one asset resolves to one ref
      // per primary-slot file — so a single asset could carry the node past
      // `max_refs` with nothing refused and nothing said. Past the ceiling the
      // backend drops the tail and reports it as `dropped_refs` after the run,
      // which is far too late for anyone to act on.
      void addReferences(nodeId, items, scopeId, { maxRefs: max })
        .then((r) => {
          if (r.failed.length > 0) {
            // A pick that adds nothing must SAY so. A silent no-op is not
            // acceptable — it is the defect class this repo keeps re-learning.
            setNotice(
              t('canvas.library.someFailed', {
                count: r.failed.length,
                defaultValue: '{{count}} could not be added as references',
              }),
            );
            return;
          }
          if (r.added === 0 && r.skipped > 0) {
            // Nothing changed, and the grid draws no "already referenced"
            // marker — so closing here would look exactly like success.
            setNotice(
              t('canvas.library.alreadyReferenced', {
                count: r.skipped,
                defaultValue: '{{count}} already on this node',
              }),
            );
            return;
          }
          if (r.clamped > 0) {
            setNotice(
              t('canvas.library.quotaClamped', {
                count: r.clamped,
                defaultValue: '{{count}} references not added · quota reached',
              }),
            );
            return;
          }
          setSelection([]);
          onClose();
        })
        .finally(() => setBusy(false));
    },
    [atLimit, busy, max, nodeId, scopeId, t, onClose],
  );

  return createPortal(
    <div
      data-testid="reference-picker"
      className="mh-pop-in fixed left-1/2 top-24 z-[60] flex h-[26rem] w-[30rem] max-w-[92vw] -translate-x-1/2 flex-col rounded-xl border border-canvas-line bg-canvas-card shadow-xl"
    >
      <div className="flex gap-1 border-b border-canvas-line p-1.5">
        {SEGMENTS.map((s) => (
          <button
            key={s}
            type="button"
            data-testid={`library-segment-${s}`}
            aria-pressed={store === s}
            onClick={() => {
              setStore(s);
              // Keys are `store:id`, so a key kept across this switch resolves
              // against nothing in the new segment: it would be counted by the
              // button's label and then silently dropped at send time.
              setSelection([]);
            }}
            className={`nodrag rounded-full border px-2 py-0.5 text-[11px] ${
              store === s
                ? 'border-[var(--accent-border)] text-[var(--accent-text)]'
                : 'border-canvas-line text-canvas-muted hover:text-canvas-text'
            }`}
          >
            {t(STORE_LABEL[s][0], STORE_LABEL[s][1])}
          </button>
        ))}
      </div>
      <LibraryGrid
        className="min-h-0 flex-1"
        items={current.items}
        loading={current.loading}
        error={current.error}
        onRetry={current.reload}
        query={query}
        onQueryChange={setQuery}
        searchPlaceholder={t('canvas.library.searchPlaceholder', 'Search Library…')}
        selection={selection}
        onSelectionChange={setSelection}
        consequence={t('canvas.library.referenceConsequence', {
          model: model || t('canvas.library.noModel', 'No Model Yet'),
          used,
          max,
          defaultValue: 'Reference images · sent to {{model}} · {{used}} / {{max}} used',
        })}
        note={
          notice ??
          (atLimit
            ? t('canvas.library.quotaFull', {
                used,
                max,
                defaultValue: '{{used}} / {{max}} references used · remove one on the node',
              })
            : null)
        }
        primaryAction={{
          label: t('canvas.library.addReferences', {
            count: selection.length,
            defaultValue: 'Add {{count}} References',
          }),
          disabled: atLimit || busy,
          onClick: commit,
        }}
        onItemActivate={(item) => commit([item])}
        emptyLabel={t('canvas.library.empty', 'Nothing Here Yet')}
        targetRowHeight={96}
      />
    </div>,
    document.body,
  );
}

export default LibraryReferencePopover;
