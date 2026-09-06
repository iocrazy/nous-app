// frontend/components/resources/generated/GeneratedView.tsx
//
// The Generated inbox: a flat, keyset-paged list of everything the system made
// for you, with review state as the primary navigation. There is no tree —
// source and project are FILTERS, so a new generation source is one more enum
// value rather than one more folder.
//
// Two invariants worth stating up front, because both have bitten this repo
// before:
//
//  * Every filter lives in the URL. A pasted link reproduces the exact view,
//    and a remount does not silently land somewhere else.
//  * Every failure is spoken. `GeneratedApiError.code` maps onto
//    `generated.err.<code>` with a generic fallback, and a partial batch
//    result names the codes that failed — "1 done" on its own would report a
//    half-applied batch as success.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ExternalLink, FolderInput, Loader2, PackagePlus, Trash2 } from 'lucide-react';

import { useResourcesContext } from '../../../contexts/ResourcesContext';
import { useToast } from '../../Toast';
import { fetchProjects } from '../../../services/projectsService';
import type { Project } from '../../../types';
import {
  batchGenerated,
  deleteGeneration,
  fetchGenerated,
  fetchGeneratedCounts,
  saveGeneration,
  GeneratedApiError,
} from '../../../services/generatedService';
import type {
  BatchAction,
  GeneratedCounts,
  GeneratedFilterState,
  GeneratedItem,
} from '../../../services/generatedService';
import { GeneratedCard } from './GeneratedCard';
import { CleanupDialog } from './CleanupDialog';
import {
  isNonVisualMediaKind,
  type NonVisualMediaKind,
} from '../mediaKindPlaceholder';
import { PinLightbox } from '../assets/sheet/PinLightbox';
import {
  generatedMediaFileUrl,
  generatedMediaStreamUrl,
} from '../../../services/generatedMediaService';
import { SaveAsAssetDialog } from '../../assets/SaveAsAssetDialog';
import type { SaveAsAssetOutcome } from '../../assets/SaveAsAssetDialog';
import {
  FILTER_STATES,
  INTERMEDIATE_OPTION,
  MEDIA_KINDS,
  SINCE_PRESETS,
  SOURCE_OPTIONS,
  hasActiveFilters,
  listOptionsFor,
  parseFilters,
  serializeFilters,
  toggleOriginKind,
  type GeneratedFilters,
} from './generatedFilters';

const TAB_LABELS: Record<GeneratedFilterState, { key: string; fallback: string }> = {
  unreviewed: { key: 'generated.tab.unreviewed', fallback: 'Unreviewed' },
  saved: { key: 'generated.tab.saved', fallback: 'Saved' },
  in_assets: { key: 'generated.tab.inAssets', fallback: 'In Assets' },
  all: { key: 'generated.tab.all', fallback: 'All' },
};

const SINCE_LABELS: Record<(typeof SINCE_PRESETS)[number], { key: string; fallback: string }> = {
  '24h': { key: 'generated.since.24h', fallback: 'Last 24 Hours' },
  '7d': { key: 'generated.since.7d', fallback: 'Last 7 Days' },
  '30d': { key: 'generated.since.30d', fallback: 'Last 30 Days' },
};

const MEDIA_KIND_LABELS: Record<(typeof MEDIA_KINDS)[number], { key: string; fallback: string }> = {
  image: { key: 'generated.type.image', fallback: 'Image' },
  video: { key: 'generated.type.video', fallback: 'Video' },
  audio: { key: 'generated.type.audio', fallback: 'Audio' },
  file: { key: 'generated.type.file', fallback: 'File' },
};

/** `media_kind` → what the lightbox should draw. Unknown kinds stay on the
 *  image path, matching the backend's own default for an unrecognised mime.
 *  The non-visual half comes from `isNonVisualMediaKind`, not from a second
 *  copy of that list. */
const lightboxKindFor = (
  kind: string | undefined,
): 'image' | 'video' | NonVisualMediaKind => {
  if (kind === 'video') return 'video';
  return isNonVisualMediaKind(kind) ? kind : 'image';
};

const MENU_ITEM =
  'flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-content-2 hover:bg-island-2';

// ─── Filter chip (plain button + popover) ───────────────────────────────────
//
// Deliberately NOT `components/resources/filter/FilterChip` — that one's chip
// set, clear affordance and dropdown contract are resource-specific. This is
// the same visual language, none of the coupling.

interface ChipProps {
  chipId: string;
  label: string;
  summary?: string | null;
  active: boolean;
  children: (close: () => void) => React.ReactNode;
}

const Chip: React.FC<ChipProps> = ({ chipId, label, summary, active, children }) => {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative" data-chip-id={chipId}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors ${
          active
            ? 'border-accent bg-accent-soft text-accent'
            : 'border-line-strong bg-card text-content-2 hover:text-content'
        }`}
      >
        <span>{summary ? `${label} · ${summary}` : label}</span>
        <ChevronDown size={12} aria-hidden="true" />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute left-0 top-full z-30 mt-1.5 max-h-72 min-w-[13rem] overflow-y-auto rounded-xl border border-line bg-card py-1 shadow-2xl"
        >
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
};

// ─── View ───────────────────────────────────────────────────────────────────

export interface GeneratedViewProps {
  /**
   * Hand-off to the "Save as Asset" dialog (Task 10). Called with the cards
   * the user picked — one for a card action, many for the batch bar. Until
   * that dialog lands the view still tracks the request in
   * `saveAsAssetItems`, so the wiring is observable rather than a dead click.
   */
  onSaveAsAsset?: (items: GeneratedItem[]) => void;
}

export const GeneratedView: React.FC<GeneratedViewProps> = ({ onSaveAsAsset }) => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const { scopeId, teamId, refreshGeneratedCounts } = useResourcesContext();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();

  const filters = useMemo<GeneratedFilters>(() => parseFilters(searchParams), [searchParams]);
  // A stable string for effect deps: `filters` is a fresh object every render,
  // and `searchParams` identity changes on unrelated params too.
  const filterKey = useMemo(() => serializeFilters(filters).toString(), [filters]);

  const [items, setItems] = useState<GeneratedItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [counts, setCounts] = useState<GeneratedCounts | null>(null);
  const [countsTick, setCountsTick] = useState(0);
  const [reloadTick, setReloadTick] = useState(0);
  const [projects, setProjects] = useState<Project[]>([]);
  // Distinct from `projects.length === 0`: an empty list and a failed fetch
  // look identical in the popover otherwise, and the user is left reading
  // "this workspace has no projects" off a network error.
  const [projectsError, setProjectsError] = useState(false);
  /** A failed list is NOT an empty inbox. Without this flag both render as
   *  "Nothing To Review", which tells the user their generations are gone
   *  when in fact the request never landed. The toast is transient; this line
   *  is not. Mirrors `AssetShelf`'s `loadError` — same contract, same reason. */
  const [loadError, setLoadError] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set());
  const [saveAsAssetItems, setSaveAsAssetItems] = useState<GeneratedItem[] | null>(null);
  const [cleanupOpen, setCleanupOpen] = useState(false);
  /** Index into `items` of the generation open in the lightbox, or null.
   *  An INDEX, not an id, so the viewer's arrow keys walk the same list the
   *  grid is showing rather than a snapshot of one card. */
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);
  /** Same two-click gate the card uses, kept per-viewer-session so moving to
   *  the next generation cannot inherit a pending confirm. */
  const [confirmingLightboxDelete, setConfirmingLightboxDelete] = useState(false);
  // Same two-click gate the card uses, and more load-bearing here: this
  // button destroys N rows at once with no per-row confirmation behind it.
  const [confirmingBatchDelete, setConfirmingBatchDelete] = useState(false);

  /** Monotonic token: a page that lands after the filters moved on is dropped
   *  rather than painted over the newer one. */
  const requestRef = useRef(0);

  const reportFailure = useCallback(
    (err: unknown) => {
      if (err instanceof GeneratedApiError) {
        addToast(t(`generated.err.${err.code}`, t('generated.err.generic')), 'error');
        return;
      }
      console.error('[GeneratedView] request failed:', err);
      addToast(t('generated.err.generic'), 'error');
    },
    [addToast, t],
  );

  /**
   * `reportFailure` closes over `t`, whose identity react-i18next does not
   * promise to keep stable across renders. Listing it in a data-fetching
   * effect's deps would therefore make that effect re-run on every render —
   * a refetch loop that only shows up at runtime. Effects read the latest
   * reporter through this ref instead of depending on it.
   */
  const reportFailureRef = useRef(reportFailure);
  reportFailureRef.current = reportFailure;

  /** Both counters: ours (the tabs) and the sidebar's. */
  const refreshAllCounts = useCallback(() => {
    setCountsTick((n) => n + 1);
    refreshGeneratedCounts();
  }, [refreshGeneratedCounts]);

  // ─── Data ────────────────────────────────────────────────────────────────

  useEffect(() => {
    if (!scopeId) return;
    const token = ++requestRef.current;
    setLoading(true);
    setSelectedIds(new Set());
    fetchGenerated(scopeId, listOptionsFor(parseFilters(new URLSearchParams(filterKey)), new Date()))
      .then((page) => {
        if (requestRef.current !== token) return;
        setItems(page.items);
        setCursor(page.next_cursor);
        setLoadError(false);
      })
      .catch((err) => {
        if (requestRef.current !== token) return;
        // An error page is not an empty page: leave nothing on screen that
        // could read as "there is nothing here" — see `loadError` above.
        setItems([]);
        setCursor(null);
        setLoadError(true);
        reportFailureRef.current(err);
      })
      .finally(() => {
        if (requestRef.current === token) setLoading(false);
      });
  }, [scopeId, filterKey, reloadTick]);

  useEffect(() => {
    if (!scopeId) return;
    let alive = true;
    fetchGeneratedCounts(scopeId)
      .then((next) => {
        if (alive) setCounts(next);
      })
      .catch((err) => {
        // Console-only ON PURPOSE — the only one in this file. Tabs rendering
        // without numbers is already an honest, visible "not known" (a failed
        // counter must never become a fake zero), and the counters are
        // decoration around a list that reports its own failures loudly. A
        // toast here would fire alongside the list's on every outage.
        console.error('[GeneratedView] counts unavailable:', err);
        if (alive) setCounts(null);
      });
    return () => {
      alive = false;
    };
  }, [scopeId, countsTick]);

  useEffect(() => {
    let alive = true;
    fetchProjects(teamId ? { teamId } : undefined)
      .then((list) => {
        if (alive) {
          setProjects(list);
          setProjectsError(false);
        }
      })
      .catch((err) => {
        // Recorded, not just logged: the chip stays usable (clearing an
        // active project filter must keep working when the list is down),
        // but the popover says the options are missing rather than
        // presenting "no projects" as fact.
        console.error('[GeneratedView] project list unavailable:', err);
        if (alive) {
          setProjects([]);
          setProjectsError(true);
        }
      });
    return () => {
      alive = false;
    };
  }, [teamId]);

  const loadMore = useCallback(async () => {
    if (!cursor || loadingMore || !scopeId) return;
    const token = requestRef.current;
    setLoadingMore(true);
    try {
      const page = await fetchGenerated(scopeId, {
        ...listOptionsFor(filters, new Date()),
        cursor,
      });
      // Filters may have changed while this was in flight; appending then
      // would mix two different queries into one list.
      if (requestRef.current !== token) return;
      setItems((prev) => [...prev, ...page.items]);
      setCursor(page.next_cursor);
    } catch (err) {
      reportFailure(err);
    } finally {
      setLoadingMore(false);
    }
  }, [cursor, loadingMore, scopeId, filters, reportFailure]);

  // ─── Filter mutation ─────────────────────────────────────────────────────

  const applyFilters = useCallback(
    (next: GeneratedFilters) => setSearchParams(serializeFilters(next)),
    [setSearchParams],
  );

  // ─── Mutations ───────────────────────────────────────────────────────────

  const markBusy = (id: string, busy: boolean) =>
    setBusyIds((prev) => {
      const next = new Set(prev);
      if (busy) next.add(id);
      else next.delete(id);
      return next;
    });

  const handleSave = useCallback(
    async (item: GeneratedItem) => {
      markBusy(item.id, true);
      try {
        const updated = await saveGeneration(scopeId, item.id);
        // Patched in place rather than dropped from the unreviewed tab: the
        // card flipping to "Saved" IS the confirmation, and a row vanishing
        // mid-triage reads as a delete.
        setItems((prev) => prev.map((row) => (row.id === item.id ? updated : row)));
        refreshAllCounts();
      } catch (err) {
        reportFailure(err);
      } finally {
        markBusy(item.id, false);
      }
    },
    [scopeId, refreshAllCounts, reportFailure],
  );

  const handleDelete = useCallback(
    async (item: GeneratedItem) => {
      markBusy(item.id, true);
      try {
        await deleteGeneration(scopeId, item.id);
        setItems((prev) => prev.filter((row) => row.id !== item.id));
        setSelectedIds((prev) => {
          const next = new Set(prev);
          next.delete(item.id);
          return next;
        });
        refreshAllCounts();
      } catch (err) {
        reportFailure(err);
      } finally {
        markBusy(item.id, false);
      }
    },
    [scopeId, refreshAllCounts, reportFailure],
  );

  const openSaveAsAsset = useCallback(
    (chosen: GeneratedItem[]) => {
      if (chosen.length === 0) return;
      setSaveAsAssetItems(chosen);
      onSaveAsAsset?.(chosen);
    },
    [onSaveAsAsset],
  );

  /**
   * The dialog already spoke its own result (toast + failed codes); this is
   * the list catching up. Patched in place rather than dropped, for the same
   * reason `handleSave` patches: a row vanishing mid-triage reads as a
   * delete. The ids the dialog reports as FAILED are deliberately left where
   * they were — they did not become assets, and moving them would be the UI
   * claiming a success the server refused.
   */
  const handleSaveAsAssetDone = useCallback(
    (result: SaveAsAssetOutcome) => {
      const failed = new Set(result.failed.map((f) => f.id));
      const attached = new Set(
        (saveAsAssetItems ?? []).map((row) => row.id).filter((id) => !failed.has(id)),
      );
      if (attached.size > 0) {
        setItems((prev) =>
          prev.map((row) =>
            attached.has(row.id)
              ? { ...row, review_state: 'in_assets', source_asset_id: result.assetId }
              : row,
          ),
        );
        setSelectedIds((prev) => {
          const next = new Set(prev);
          for (const id of attached) next.delete(id);
          return next;
        });
      }
      refreshAllCounts();
    },
    [saveAsAssetItems, refreshAllCounts],
  );

  const runBatch = useCallback(
    async (action: Exclude<BatchAction, 'save_as_asset'>) => {
      const ids = [...selectedIds];
      if (ids.length === 0) return;
      try {
        const result = await batchGenerated(scopeId, { ids, action });
        const ok = new Set(result.ok);
        setItems((prev) =>
          action === 'delete'
            ? prev.filter((row) => !ok.has(row.id))
            : prev.map((row) => (ok.has(row.id) ? { ...row, review_state: 'saved' } : row)),
        );
        setSelectedIds(new Set(result.failed.map((f) => f.id)));

        if (result.failed.length === 0) {
          addToast(
            t('generated.batch.done', { ok: result.ok.length, defaultValue: '{{ok}} done' }),
            'success',
          );
        } else {
          // Name the codes. A count alone leaves the user with no idea what
          // to retry or why it refused.
          const codes = [...new Set(result.failed.map((f) => f.code))].join(', ');
          addToast(
            `${t('generated.batch.partial', {
              ok: result.ok.length,
              failed: result.failed.length,
              defaultValue: '{{ok}} done, {{failed}} failed',
            })} (${codes})`,
            'error',
          );
        }
        refreshAllCounts();
      } catch (err) {
        reportFailure(err);
      }
    },
    [selectedIds, scopeId, addToast, t, refreshAllCounts, reportFailure],
  );

  // ─── Derived ─────────────────────────────────────────────────────────────

  const toggleSelect = useCallback((id: string) => {
    // Editing the selection retracts a pending confirm: "Confirm Delete (3)"
    // must never survive into a moment when it means a different 3.
    setConfirmingBatchDelete(false);
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  /** Open the viewer on a card. Resolved to an index here rather than in the
   *  card, so the lightbox's arrows walk the loaded page. */
  const openLightbox = useCallback(
    (item: GeneratedItem) => {
      const index = items.findIndex((row) => row.id === item.id);
      // -1 means the row left the list between render and click; opening the
      // viewer on a clamped neighbour would show the user a different image
      // than the one they clicked.
      if (index >= 0) {
        setConfirmingLightboxDelete(false);
        setLightboxIndex(index);
      }
    },
    [items],
  );

  // A page that reloads under an open viewer must not leave it pointing at
  // whatever now sits at that index — the lightbox would silently swap to a
  // different generation.
  useEffect(() => {
    setLightboxIndex(null);
  }, [filterKey, reloadTick]);

  const selectedItems = useMemo(
    () => items.filter((row) => selectedIds.has(row.id)),
    [items, selectedIds],
  );

  /** Batch Delete is offered only when EVERY selected row is still
   *  unreviewed — the same rule the per-card menu already applies (Delete
   *  appears in the `unreviewed` branch only).
   *
   *  This is defence in depth for the destructive direction: a saved /
   *  in_assets row's bytes are owned by a `resources` row, and a
   *  content-addressed key is shared, so "delete this card" must never read
   *  as "delete my file". The backend refuses to remove the object for a
   *  promoted row; this stops the user asking for it in the first place. */
  const deletableSelection = useMemo(
    () =>
      selectedItems.length > 0 &&
      selectedItems.every((row) => row.review_state === 'unreviewed'),
    [selectedItems],
  );

  /** Model options come from what is actually on screen — an exhaustive list
   *  of every model the workspace ever used is not something the API offers,
   *  and inventing one would advertise filters that match nothing. */
  const modelOptions = useMemo(() => {
    const seen = new Set<string>();
    for (const row of items) if (row.model) seen.add(row.model);
    if (filters.model) seen.add(filters.model);
    return [...seen].sort();
  }, [items, filters.model]);

  const tabCount = (state: GeneratedFilterState): number | null => {
    if (!counts) return null;
    if (state === 'all') return counts.unreviewed + counts.saved + counts.in_assets;
    return counts[state];
  };

  /** The Source chip's count. The intermediate toggle is counted alongside
   *  the origin kinds so the chip cannot read "Source" untouched while the
   *  page is showing masks. */
  const sourceChipSummary = (() => {
    const n = filters.originKinds.length + (filters.includeIntermediate ? 1 : 0);
    return n > 0 ? String(n) : null;
  })();

  const itemsById = useMemo(
    () => new Map(items.map((row) => [row.id, row])),
    [items],
  );

  /**
   * The lightbox's details panel.
   *
   * `origin params` here means the fields the wire actually carries — prompt,
   * model, provider, origin kind. `generated_media.params` is deliberately
   * NOT on the wire (`GeneratedItem` drops it in `_build_item`), so there is
   * no raw params blob to render and inventing one would be a second, unowned
   * copy of the schema.
   */
  const renderLightboxMeta = (item: GeneratedItem | undefined) => {
    if (!item) return null;
    const created = new Date(item.created_at);
    const createdLabel = Number.isNaN(created.getTime())
      ? item.created_at
      : created.toLocaleString();
    const rows: Array<[string, React.ReactNode]> = [];
    rows.push([
      t('generated.filter.source', 'Source'),
      item.source.deep_link ? (
        <button
          type="button"
          data-testid="lightbox-source-link"
          onClick={() => navigate(item.source.deep_link as string)}
          className="inline-flex items-center gap-1 underline-offset-2 hover:underline"
        >
          <ExternalLink size={11} aria-hidden="true" />
          {item.source.label}
        </button>
      ) : (
        item.source.label
      ),
    ]);
    if (item.model) rows.push([t('generated.filter.model', 'Model'), item.model]);
    if (item.provider) {
      rows.push([t('generated.meta.provider', 'Provider'), item.provider]);
    }
    rows.push([t('generated.meta.created', 'Created'), createdLabel]);
    rows.push([
      t('generated.meta.state', 'State'),
      t(`generated.state.${item.review_state === 'in_assets' ? 'asset' : item.review_state === 'unreviewed' ? 'new' : item.review_state}`),
    ]);
    if (item.prompt) rows.push([t('generated.meta.prompt', 'Prompt'), item.prompt]);
    return (
      <dl
        data-testid="lightbox-metadata"
        className="grid grid-cols-[auto,1fr] gap-x-4 gap-y-1 text-[12px]"
      >
        {rows.map(([label, value]) => (
          <React.Fragment key={label}>
            <dt className="whitespace-nowrap opacity-60">{label}</dt>
            <dd className="min-w-0 break-words">{value}</dd>
          </React.Fragment>
        ))}
      </dl>
    );
  };

  /** The lightbox's action row — the card's three actions, worded, because
   *  a full-screen viewer has the room the 150px card does not. */
  const renderLightboxActions = (item: GeneratedItem | undefined) => {
    if (!item) return null;
    const busy = busyIds.has(item.id);
    const btn =
      'inline-flex items-center gap-1.5 rounded-lg border border-white/30 px-2.5 py-1 ' +
      'text-xs font-medium text-white hover:bg-white/10 disabled:opacity-50';
    return (
      <>
        {item.review_state === 'unreviewed' && (
          <button
            type="button"
            disabled={busy}
            title={t('generated.action.saveHint', 'Turn this into a regular file in My Uploads')}
            onClick={() => void handleSave(item)}
            className={btn}
          >
            <FolderInput size={13} aria-hidden="true" />
            {t('generated.action.save', 'Save To Uploads')}
          </button>
        )}
        {item.review_state !== 'in_assets' && (
          <button
            type="button"
            disabled={busy}
            title={t(
              'generated.action.saveAsAssetHint',
              "Attach it to an asset card's slot (character, location, …)",
            )}
            onClick={() => openSaveAsAsset([item])}
            className={btn}
          >
            <PackagePlus size={13} aria-hidden="true" />
            {t('generated.action.saveAsAsset', 'As Asset')}
          </button>
        )}
        {item.review_state === 'unreviewed' &&
          (confirmingLightboxDelete ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setConfirmingLightboxDelete(false);
                // Close first: the row is about to leave `items`, and the
                // viewer's index would then point at a different generation.
                setLightboxIndex(null);
                void handleDelete(item);
              }}
              className={`${btn} border-danger-line text-danger`}
            >
              <Trash2 size={13} aria-hidden="true" />
              {t('generated.card.confirmDelete', 'Confirm Delete')}
            </button>
          ) : (
            <button
              type="button"
              disabled={busy}
              onClick={() => setConfirmingLightboxDelete(true)}
              className={btn}
            >
              <Trash2 size={13} aria-hidden="true" />
              {t('generated.action.delete', 'Delete')}
            </button>
          ))}
      </>
    );
  };

  const projectName = filters.projectId
    ? (projects.find((p) => String(p.id) === filters.projectId)?.name ?? filters.projectId)
    : null;

  const emptyMessage = () => {
    if (filters.projectId) {
      return t(
        'generated.empty.project',
        'Only Canvas Generations Can Be Filtered By Project',
      );
    }
    if (filters.state === 'unreviewed' && !hasActiveFilters(filters)) {
      return t('generated.empty.unreviewed', 'Nothing To Review');
    }
    return t('generated.empty.filtered', 'No Generations Match These Filters');
  };

  // ─── Render ──────────────────────────────────────────────────────────────

  return (
    <div
      className="relative flex h-full flex-col overflow-hidden"
      data-testid="generated-view"
    >
      <div className="flex items-center gap-3 px-5 pt-4">
        <h2 className="text-base font-semibold text-content">
          {t('generated.title', 'Generated')}
        </h2>
        {counts !== null && counts.unreviewed > 0 && (
          <span className="rounded-full border border-warn-line bg-warn-soft px-2 py-0.5 text-[11px] font-medium text-warn">
            {t('generated.unreviewedChip', {
              n: counts.unreviewed,
              defaultValue: '{{n}} unreviewed',
            })}
          </span>
        )}
        <div className="flex-1" />
        <button
          type="button"
          onClick={() => setCleanupOpen(true)}
          className="rounded-lg px-2.5 py-1 text-xs font-medium text-content-3 hover:bg-island-2 hover:text-content"
        >
          {t('generated.cleanup.open', 'Clean Up…')}
        </button>
      </div>

      <div role="tablist" className="mt-3 flex gap-1 border-b border-line px-5">
        {FILTER_STATES.map((state) => {
          const active = filters.state === state;
          const count = tabCount(state);
          return (
            <button
              key={state}
              role="tab"
              type="button"
              aria-selected={active}
              onClick={() => applyFilters({ ...filters, state })}
              className={`-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-[13px] transition-colors ${
                active
                  ? 'border-accent font-semibold text-accent'
                  : 'border-transparent text-content-3 hover:text-content'
              }`}
            >
              <span>{t(TAB_LABELS[state].key, TAB_LABELS[state].fallback)}</span>
              {count !== null && (
                <span className="text-[11px] tabular-nums text-content-4">{count}</span>
              )}
            </button>
          );
        })}
      </div>

      <div className="flex flex-wrap gap-1.5 px-5 py-2.5">
        <Chip
          chipId="source"
          label={t('generated.filter.source', 'Source')}
          summary={sourceChipSummary}
          active={filters.originKinds.length > 0 || filters.includeIntermediate}
        >
          {() => (
            <>
              {SOURCE_OPTIONS.map((opt) => {
                const checked = filters.originKinds.includes(opt.kind);
                return (
                  <button
                    key={opt.kind}
                    type="button"
                    role="menuitemcheckbox"
                    aria-checked={checked}
                    className={`${MENU_ITEM} ${checked ? 'text-accent' : ''}`}
                    onClick={() =>
                      applyFilters({
                        ...filters,
                        originKinds: toggleOriginKind(filters.originKinds, opt.kind),
                      })
                    }
                  >
                    {t(opt.labelKey, opt.fallback)}
                  </button>
                );
              })}
              {/* Separated, because this one is not an origin kind — it
                  WIDENS the page rather than narrowing it. Grouping it with
                  the kinds would suggest "show me only masks", which is not
                  what it does. */}
              <div className="my-1 border-t border-line" />
              <button
                type="button"
                role="menuitemcheckbox"
                aria-checked={filters.includeIntermediate}
                className={`${MENU_ITEM} ${filters.includeIntermediate ? 'text-accent' : ''}`}
                onClick={() =>
                  applyFilters({
                    ...filters,
                    includeIntermediate: !filters.includeIntermediate,
                  })
                }
              >
                {t(INTERMEDIATE_OPTION.labelKey, INTERMEDIATE_OPTION.fallback)}
              </button>
            </>
          )}
        </Chip>

        <Chip
          chipId="project"
          label={t('generated.filter.project', 'Project')}
          summary={projectName}
          active={filters.projectId !== null}
        >
          {(close) => (
            <>
              <button
                type="button"
                role="menuitemradio"
                aria-checked={filters.projectId === null}
                className={MENU_ITEM}
                onClick={() => {
                  applyFilters({ ...filters, projectId: null });
                  close();
                }}
              >
                {t('generated.filter.anyProject', 'Any Project')}
              </button>
              {projectsError && (
                <p className="px-3 py-1.5 text-xs text-danger">
                  {t('generated.projectsUnavailable', 'Projects unavailable')}
                </p>
              )}
              {projects.map((project) => (
                <button
                  key={String(project.id)}
                  type="button"
                  role="menuitemradio"
                  aria-checked={filters.projectId === String(project.id)}
                  className={MENU_ITEM}
                  onClick={() => {
                    applyFilters({ ...filters, projectId: String(project.id) });
                    close();
                  }}
                >
                  {project.name}
                </button>
              ))}
            </>
          )}
        </Chip>

        <Chip
          chipId="type"
          label={t('generated.filter.type', 'Type')}
          summary={
            filters.mediaKind
              ? t(MEDIA_KIND_LABELS[filters.mediaKind].key, MEDIA_KIND_LABELS[filters.mediaKind].fallback)
              : null
          }
          active={filters.mediaKind !== null}
        >
          {(close) => (
            <>
              <button
                type="button"
                role="menuitemradio"
                aria-checked={filters.mediaKind === null}
                className={MENU_ITEM}
                onClick={() => {
                  applyFilters({ ...filters, mediaKind: null });
                  close();
                }}
              >
                {t('generated.filter.anyType', 'Any Type')}
              </button>
              {MEDIA_KINDS.map((kind) => (
                <button
                  key={kind}
                  type="button"
                  role="menuitemradio"
                  aria-checked={filters.mediaKind === kind}
                  className={MENU_ITEM}
                  onClick={() => {
                    applyFilters({ ...filters, mediaKind: kind });
                    close();
                  }}
                >
                  {t(MEDIA_KIND_LABELS[kind].key, MEDIA_KIND_LABELS[kind].fallback)}
                </button>
              ))}
            </>
          )}
        </Chip>

        <Chip
          chipId="model"
          label={t('generated.filter.model', 'Model')}
          summary={filters.model}
          active={filters.model !== null}
        >
          {(close) => (
            <>
              <button
                type="button"
                role="menuitemradio"
                aria-checked={filters.model === null}
                className={MENU_ITEM}
                onClick={() => {
                  applyFilters({ ...filters, model: null });
                  close();
                }}
              >
                {t('generated.filter.anyModel', 'Any Model')}
              </button>
              {modelOptions.length === 0 && (
                <p className="px-3 py-1.5 text-xs text-content-4">
                  {t('generated.filter.noModels', 'No Models On This Page')}
                </p>
              )}
              {modelOptions.map((model) => (
                <button
                  key={model}
                  type="button"
                  role="menuitemradio"
                  aria-checked={filters.model === model}
                  className={MENU_ITEM}
                  onClick={() => {
                    applyFilters({ ...filters, model });
                    close();
                  }}
                >
                  {model}
                </button>
              ))}
            </>
          )}
        </Chip>

        <Chip
          chipId="date"
          label={t('generated.filter.date', 'Date')}
          summary={
            filters.since
              ? t(SINCE_LABELS[filters.since].key, SINCE_LABELS[filters.since].fallback)
              : null
          }
          active={filters.since !== null}
        >
          {(close) => (
            <>
              <button
                type="button"
                role="menuitemradio"
                aria-checked={filters.since === null}
                className={MENU_ITEM}
                onClick={() => {
                  applyFilters({ ...filters, since: null });
                  close();
                }}
              >
                {t('generated.filter.anyDate', 'Any Time')}
              </button>
              {SINCE_PRESETS.map((preset) => (
                <button
                  key={preset}
                  type="button"
                  role="menuitemradio"
                  aria-checked={filters.since === preset}
                  className={MENU_ITEM}
                  onClick={() => {
                    applyFilters({ ...filters, since: preset });
                    close();
                  }}
                >
                  {t(SINCE_LABELS[preset].key, SINCE_LABELS[preset].fallback)}
                </button>
              ))}
            </>
          )}
        </Chip>
      </div>

      <div className="flex-1 overflow-y-auto px-5 pb-24">
        {loading ? (
          <div className="flex items-center gap-2 py-10 text-xs text-content-3">
            <Loader2 size={14} className="animate-spin" aria-hidden="true" />
            {t('common.loading', 'Loading…')}
          </div>
        ) : loadError ? (
          <p
            role="alert"
            data-testid="generated-load-error"
            className="py-10 text-sm text-danger"
          >
            {t('generated.loadFailed', 'Could Not Load Generations')}
          </p>
        ) : items.length === 0 ? (
          <p className="py-10 text-sm text-content-3">{emptyMessage()}</p>
        ) : (
          <>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3">
              {items.map((item) => (
                <GeneratedCard
                  key={item.id}
                  item={item}
                  selected={selectedIds.has(item.id)}
                  teamId={teamId}
                  busy={busyIds.has(item.id)}
                  onToggleSelect={toggleSelect}
                  onOpen={openLightbox}
                  onSave={handleSave}
                  onSaveAsAsset={(one) => openSaveAsAsset([one])}
                  onDelete={handleDelete}
                />
              ))}
            </div>
            {cursor && (
              <div className="flex justify-center py-5">
                <button
                  type="button"
                  disabled={loadingMore}
                  onClick={() => void loadMore()}
                  className="rounded-lg border border-line-strong bg-card px-3 py-1.5 text-xs font-medium text-content-2 hover:bg-island-2 disabled:opacity-50"
                >
                  {loadingMore
                    ? t('common.loading', 'Loading…')
                    : t('generated.loadMore', 'Load More')}
                </button>
              </div>
            )}
          </>
        )}
      </div>

      {selectedIds.size > 0 && (
        <div
          data-testid="generated-batch-bar"
          className="pointer-events-auto absolute bottom-5 left-1/2 z-40 flex -translate-x-1/2 items-center gap-2 rounded-xl border border-line bg-card px-3 py-2 shadow-2xl"
        >
          <span className="pr-1 text-xs text-content-2">
            {t('generated.batch.selected', {
              n: selectedIds.size,
              defaultValue: '{{n}} selected',
            })}
          </span>
          {/* Worded, unlike the card. The bar is one row for the whole
              selection with room to spare, and an icon here would be the
              only place in the flow where the destructive and the safe
              action look alike. */}
          <button
            type="button"
            title={t(
              'generated.action.saveHint',
              'Turn this into a regular file in My Uploads',
            )}
            onClick={() => void runBatch('save')}
            className="rounded-lg border border-line-strong px-2.5 py-1 text-xs font-medium text-content hover:bg-island-2"
          >
            {t('generated.action.save', 'Save To Uploads')}
          </button>
          <button
            type="button"
            title={t(
              'generated.action.saveAsAssetHint',
              "Attach it to an asset card's slot (character, location, …)",
            )}
            onClick={() => openSaveAsAsset(selectedItems)}
            className="rounded-lg border border-accent bg-accent px-2.5 py-1 text-xs font-medium text-white hover:opacity-90"
          >
            {t('generated.action.saveAsAsset', 'As Asset')}
          </button>
          {confirmingBatchDelete && deletableSelection ? (
            <button
              type="button"
              onClick={() => {
                setConfirmingBatchDelete(false);
                void runBatch('delete');
              }}
              className="rounded-lg border border-danger-line bg-danger-soft px-2.5 py-1 text-xs font-semibold text-danger hover:opacity-90"
            >
              {t('generated.batch.confirmDelete', {
                n: selectedIds.size,
                defaultValue: 'Confirm Delete ({{n}})',
              })}
            </button>
          ) : (
            <button
              type="button"
              disabled={!deletableSelection}
              title={
                deletableSelection
                  ? undefined
                  : t(
                      'generated.batch.deleteOnlyUnreviewed',
                      'Only unreviewed items can be deleted — saved items live in your library',
                    )
              }
              onClick={() => setConfirmingBatchDelete(true)}
              className="rounded-lg border border-danger-line px-2.5 py-1 text-xs font-medium text-danger hover:bg-danger-soft disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
            >
              {t('generated.action.delete', 'Delete')}
            </button>
          )}
          <button
            type="button"
            onClick={() => {
              setConfirmingBatchDelete(false);
              setSelectedIds(new Set());
            }}
            className="rounded-lg px-2.5 py-1 text-xs font-medium text-content-3 hover:text-content"
          >
            {t('generated.batch.clear', 'Clear')}
          </button>
        </div>
      )}

      {lightboxIndex !== null && items.length > 0 && (
        <PinLightbox
          resourceIds={items.map((row) => row.id)}
          index={lightboxIndex}
          slotLabel={t('generated.title', 'Generated')}
          onIndexChange={(next) => {
            setConfirmingLightboxDelete(false);
            setLightboxIndex(next);
          }}
          onClose={() => setLightboxIndex(null)}
          // The FULL file, not `/cover` — a viewer opened to inspect a
          // generation at full screen must not be showing the thumbnail.
          // Timed media goes through `/stream`, which is Range-capable and
          // needs no Bearer header: `/file` is auth-gated, and neither a
          // `<video src>` nor the audio player's fetch can carry one.
          srcFor={(id) => {
            const kind = itemsById.get(id)?.media_kind;
            return kind === 'video' || kind === 'audio'
              ? generatedMediaStreamUrl(id)
              : generatedMediaFileUrl(id);
          }}
          kindFor={(id) => lightboxKindFor(itemsById.get(id)?.media_kind)}
          titleFor={(id) => itemsById.get(id)?.title}
          metadataFor={(id) => renderLightboxMeta(itemsById.get(id))}
          actionsFor={(id) => renderLightboxActions(itemsById.get(id))}
        />
      )}

      <SaveAsAssetDialog
        open={saveAsAssetItems !== null && saveAsAssetItems.length > 0}
        scopeId={scopeId}
        items={saveAsAssetItems ?? []}
        onClose={() => setSaveAsAssetItems(null)}
        onDone={handleSaveAsAssetDone}
      />

      {cleanupOpen && (
        <CleanupDialog
          scopeId={scopeId}
          onClose={() => setCleanupOpen(false)}
          onDone={() => {
            setCleanupOpen(false);
            setReloadTick((n) => n + 1);
            refreshAllCounts();
          }}
        />
      )}
    </div>
  );
};
