// frontend/components/resources/assets/AssetShelf.tsx
//
// The asset shelf (spec screen 3): a type tab bar, a filter row, a grid of
// `AssetCard`s, and the one mutation this page owns — creating an asset.
//
// Three invariants, each of which has a way of quietly breaking:
//
//  * THE URL IS THE STATE. The open type is a path segment (the sidebar
//    navigates to it and `AssetsView` validates it); the filters are query
//    params. There is no local "current tab" — a second copy of that would
//    drift from the rail the moment either side navigated on its own.
//  * PRESETS ARE SEPARATED, NOT MIXED. The list endpoint unions global system
//    presets into every scope's shelf, but `GET /assets/counts` deliberately
//    excludes them. Interleaving presets into the main grid would leave the
//    grid permanently longer than the badge with nothing on screen explaining
//    why. They get their own labelled section instead, so the badge and the
//    team's own grid agree.
//  * COUNTS ARE REFRESHED AFTER A WRITE. `refreshAssetCounts()` is the only
//    thing that moves the six sidebar badges; skipping it after a create
//    leaves them stale until the scope changes.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ChevronDown, Loader2, Plus } from 'lucide-react';

import { useResourcesContext } from '../../../contexts/ResourcesContext';
import { fetchProjects } from '../../../services/projectsService';
import type { Project } from '../../../types';
import { ASSET_TYPES, type AssetType } from '../../assets/assetSlots';
import { listAssets } from '../../../services/assetsService';
import type { AssetRow, AssetSummary } from '../../../services/assetsService';
import { AssetCard } from './AssetCard';
import { useAssetFailureReporter } from './useAssetFailure';
import { NewAssetDialog } from './NewAssetDialog';
import { ASSET_TYPE_ICON, typeLabelKey, typeSingularKey } from './assetTypeMeta';
import {
  ASSET_PAGE_SIZE,
  LIBRARY_VALUES,
  SORT_VALUES,
  READINESS_VALUES,
  assetListOptionsFor,
  hasActiveAssetFilters,
  parseAssetFilters,
  serializeAssetFilters,
  tagOptionsFrom,
  type AssetFilters,
  type AssetSort,
  type ReadinessFilter,
} from './assetFilters';
import type { AssetLibraryFilter } from '../../../services/assetsService';

const MENU_ITEM =
  'flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-content-2 hover:bg-island-2';

const SORT_FALLBACK: Record<AssetSort, string> = {
  recent: 'Recently Updated',
  name: 'Name',
  readiness: 'Readiness',
};

const READINESS_FALLBACK: Record<ReadinessFilter, string> = {
  ready: 'Ready',
  draft: 'Draft',
};

// Library membership (mig 449). The shelf shows `in` by default; `out` is how
// a user finds the project-originated assets nobody has adopted yet, and `all`
// is both. Worded as what the user would SAY, not as the wire value.
const LIBRARY_FALLBACK: Record<AssetLibraryFilter, string> = {
  in: 'In Library',
  out: 'Not In Library',
  all: 'All',
};

// ─── Filter chip ────────────────────────────────────────────────────────────
//
// Same visual language as the Generated inbox's chip and, like that one,
// deliberately not `components/resources/filter/FilterChip` — that component's
// chip set and clear affordance are resource-tree specific. Copied rather than
// hoisted because hoisting would mean editing the Generated view mid-task; if
// a third shelf wants one, that is the moment to extract it.

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

// ─── Shelf ──────────────────────────────────────────────────────────────────

export interface AssetShelfProps {
  /** The open type, or null on the Assets landing page ("All"). Comes from the
   *  route via `AssetsView`, which has already rejected unknown slugs. */
  assetType: AssetType | null;
}

export const AssetShelf: React.FC<AssetShelfProps> = ({ assetType }) => {
  const { t } = useTranslation();
  const { scopeId, teamId, resPath, assetCounts, promptEntryCount, refreshAssetCounts } = useResourcesContext();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const filters = useMemo<AssetFilters>(() => parseAssetFilters(searchParams), [searchParams]);
  /** A stable string for effect deps: `filters` is a fresh object every render
   *  and `searchParams` identity changes on unrelated params too. */
  const filterKey = useMemo(() => serializeAssetFilters(filters).toString(), [filters]);

  const [rows, setRows] = useState<AssetRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  /** Whether the last page came back FULL. There is no cursor on this
   *  endpoint, so a full page is the only evidence more may exist. */
  const [hasMore, setHasMore] = useState(false);
  /** A failed list is NOT an empty shelf. Without this flag both render as
   *  "No Assets Yet", which tells the user their library is gone when in fact
   *  the request never landed. The toast is transient; this line is not. */
  const [loadError, setLoadError] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  /** Distinct from `projects.length === 0`: an empty workspace and a failed
   *  fetch look identical in the popover otherwise, and the user reads a
   *  network error as fact. */
  const [projectsError, setProjectsError] = useState(false);
  const [newMenuOpen, setNewMenuOpen] = useState(false);
  const [newDialogType, setNewDialogType] = useState<AssetType | null>(null);

  const newMenuRef = useRef<HTMLDivElement>(null);
  /** Monotonic token: a page that lands after the filters moved on is dropped
   *  rather than painted over the newer one. */
  const requestRef = useRef(0);

  // Shared with the entity sheet — see `useAssetFailure.ts` for why the ref
  // exists (a fetching effect must not depend on `t`'s identity).
  const { report: reportFailure, reportRef: reportFailureRef } =
    useAssetFailureReporter('AssetShelf');

  // ─── Data ────────────────────────────────────────────────────────────────

  useEffect(() => {
    if (!scopeId) return;
    const token = ++requestRef.current;
    setLoading(true);
    listAssets(
      scopeId,
      assetListOptionsFor(parseAssetFilters(new URLSearchParams(filterKey)), assetType, 0),
    )
      .then((page) => {
        if (requestRef.current !== token) return;
        setRows(page);
        setHasMore(page.length >= ASSET_PAGE_SIZE);
        setLoadError(false);
      })
      .catch((err) => {
        if (requestRef.current !== token) return;
        // An error page is not an empty page — see `loadError` above.
        setRows([]);
        setHasMore(false);
        setLoadError(true);
        reportFailureRef.current(err);
      })
      .finally(() => {
        if (requestRef.current === token) setLoading(false);
      });
    // `reportFailureRef` is a ref (stable identity); listed only to satisfy
    // the exhaustive-deps rule, which cannot see that through a custom hook.
  }, [scopeId, assetType, filterKey, reportFailureRef]);

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
        // Recorded, not merely logged: the chip stays usable (clearing an
        // active project filter must keep working when the list is down) but
        // the popover says the options are missing rather than presenting
        // "no projects" as a fact.
        console.error('[AssetShelf] project list unavailable:', err);
        if (alive) {
          setProjects([]);
          setProjectsError(true);
        }
      });
    return () => {
      alive = false;
    };
  }, [teamId]);

  /**
   * `sort=readiness` orders drafts-first IN PYTHON, over the page that came
   * back (`assets_service.py`), so appending a second page produces
   * `drafts, ready, drafts, ready` — the ordering stops holding exactly where
   * the user stops looking, and nothing on screen says so.
   *
   * Refusing the second page is the honest answer for P2: the user sees one
   * correctly-ordered page and is told why there is no more of it. Ordering
   * across the whole result set needs the primary-slot count in the SQL, which
   * is P3 (recorded in the PR's 已知).
   *
   * Deliberately NOT hiding the sort chip once `hasMore` is true: a control
   * that disappears when the library grows past 60 rows is a worse surprise
   * than a button that says why it is off.
   */
  const readinessSortIsPaged = filters.sort === 'readiness' && hasMore;

  const loadMore = useCallback(async () => {
    // `readinessSortIsPaged` here as well as on the button: a disabled control
    // is a UI affordance, not a guarantee, and appending a second page under
    // this sort is the mis-ordering the flag exists to prevent.
    if (!hasMore || loadingMore || !scopeId || readinessSortIsPaged) return;
    const token = requestRef.current;
    setLoadingMore(true);
    try {
      const page = await listAssets(
        scopeId,
        assetListOptionsFor(filters, assetType, rows.length),
      );
      // Filters may have changed while this was in flight; appending then
      // would mix two different queries into one list.
      if (requestRef.current !== token) return;
      setRows((prev) => [...prev, ...page]);
      setHasMore(page.length >= ASSET_PAGE_SIZE);
    } catch (err) {
      reportFailure(err);
    } finally {
      setLoadingMore(false);
    }
  }, [
    hasMore,
    loadingMore,
    scopeId,
    filters,
    assetType,
    rows.length,
    reportFailure,
    readinessSortIsPaged,
  ]);

  // ─── Navigation ──────────────────────────────────────────────────────────

  const shelfPath = useCallback(
    (type: AssetType | null) =>
      resPath(type ? `/resources/assets/${type}` : '/resources/assets'),
    [resPath],
  );

  /** Switching tabs keeps the filters. Someone narrowed to a project and then
   *  looked at Locations — dropping the project there would be answering a
   *  question they did not ask. */
  const goToType = useCallback(
    (type: AssetType | null) => {
      navigate({ pathname: shelfPath(type), search: filterKey });
    },
    [navigate, shelfPath, filterKey],
  );

  const applyFilters = useCallback(
    (next: AssetFilters) => {
      navigate({
        pathname: shelfPath(assetType),
        search: serializeAssetFilters(next).toString(),
      });
    },
    [navigate, shelfPath, assetType],
  );

  const openSheet = useCallback(
    (assetId: string) => navigate(resPath(`/resources/assets/item/${assetId}`)),
    [navigate, resPath],
  );

  const handleCreated = useCallback(
    (created: AssetSummary) => {
      setNewDialogType(null);
      // CONTRACT (Task 5): the sidebar badges only move when this is called.
      refreshAssetCounts();
      openSheet(created.id);
    },
    [refreshAssetCounts, openSheet],
  );

  // Close the six-way "+ New" menu on an outside click, like the chips.
  useEffect(() => {
    if (!newMenuOpen) return;
    const onClick = (e: MouseEvent) => {
      if (newMenuRef.current && !newMenuRef.current.contains(e.target as Node)) {
        setNewMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [newMenuOpen]);

  // ─── Derived ─────────────────────────────────────────────────────────────

  const mine = useMemo(() => rows.filter((row) => !row.is_system_preset), [rows]);
  const presets = useMemo(() => rows.filter((row) => row.is_system_preset), [rows]);

  const projectNames = useMemo(() => {
    const map: Record<string, string> = {};
    for (const project of projects) map[String(project.id)] = project.name;
    return map;
  }, [projects]);

  const tagOptions = useMemo(() => tagOptionsFrom(rows, filters.tag), [rows, filters.tag]);

  /**
   * The number on a tab, in the currency of the surface that tab opens.
   *
   * Prompts is the odd one out and has to be: its tab does not open this
   * shelf at all, it opens `PromptsShelf` over the unified catalog, so it
   * counts prompt ENTRIES (pictures, albums, templates, presets included).
   * Every other tab — and therefore the "All" sum, which stays over
   * `assetCounts` alone — counts asset rows, which is what the grid below
   * can actually show. Summing the entry count into "All" was how a scope
   * holding no assets came to advertise fifteen of them above the words
   * "No Assets Yet".
   */
  const tabCount = (type: AssetType | null): number | null => {
    if (type === 'prompt') return promptEntryCount;
    if (!assetCounts) return null;
    if (type === null) {
      return ASSET_TYPES.reduce((sum, one) => sum + (assetCounts[one] ?? 0), 0);
    }
    return assetCounts[type] ?? 0;
  };

  const projectName = filters.projectId
    ? (projectNames[filters.projectId] ?? filters.projectId)
    : null;

  /** Whether anything OTHER than the library chip is narrowing the shelf — the
   *  "nothing left to adopt" line is only honest when the library chip is the
   *  sole reason the grid is empty. */
  const hasOtherFilters =
    filters.projectId !== null || filters.readiness !== null || filters.tag !== null;

  // The `out` shelf gets its own line rather than the generic filtered one:
  // "No Assets Match These Filters" would leave a user who just clicked "Not
  // In Library" hunting for a filter they did not set, when the answer is the
  // good news that there is nothing left to adopt.
  const emptyMessage =
    filters.library === 'out' && !hasOtherFilters
      ? t('assets.empty.nothingOutOfLibrary', 'Every Asset Is Already In Your Library')
      : hasActiveAssetFilters(filters)
        ? t('assets.empty.filtered', 'No Assets Match These Filters')
        : assetType
          ? t('assets.empty.ofType', {
              type: t(typeLabelKey(assetType), assetType),
              defaultValue: 'No {{type}} Yet',
            })
          : t('assets.empty.none', 'No Assets Yet');

  // "Import from script" writes into ONE project, so it is only meaningful
  // once a project is picked. P3 owns the actual flow; this is the doorway.
  const importProjectId = filters.projectId;

  // ─── Render ──────────────────────────────────────────────────────────────

  return (
    <div className="relative flex h-full flex-col overflow-hidden" data-testid="asset-shelf">
      <div className="flex items-center gap-3 px-5 pt-4">
        <h2 className="text-base font-semibold text-content">
          {t('resources.assets', 'Assets')}
        </h2>
        <div className="flex-1" />

        {importProjectId ? (
          <button
            type="button"
            data-testid="import-from-script"
            title={t('assets.importFromScriptGo', 'Opens the project workspace')}
            onClick={() => navigate(resPath(`/projects/${importProjectId}`))}
            className="rounded-lg border border-line-strong px-2.5 py-1 text-xs font-medium text-content-2 hover:bg-island-2"
          >
            {t('assets.importFromScript', 'Import From Script')}
          </button>
        ) : (
          <button
            type="button"
            data-testid="import-from-script"
            disabled
            title={t(
              'assets.importFromScriptHint',
              'Pick a project first — a script imports into one project',
            )}
            className="cursor-not-allowed rounded-lg border border-line-strong px-2.5 py-1 text-xs font-medium text-content-4 opacity-50"
          >
            {t('assets.importFromScript', 'Import From Script')}
          </button>
        )}

        {/* On a type shelf the "+ New" button already knows the type; on All
            it has to ask, so it becomes a six-way menu. */}
        {assetType ? (
          <button
            type="button"
            data-testid="new-asset"
            onClick={() => setNewDialogType(assetType)}
            className="inline-flex items-center gap-1 rounded-lg border border-accent bg-accent px-2.5 py-1 text-xs font-medium text-white hover:opacity-90"
          >
            <Plus size={12} aria-hidden="true" />
            {t('assets.newOfType', {
              type: t(typeSingularKey(assetType), assetType),
              defaultValue: 'New {{type}}',
            })}
          </button>
        ) : (
          <div ref={newMenuRef} className="relative">
            <button
              type="button"
              data-testid="new-asset"
              aria-haspopup="menu"
              aria-expanded={newMenuOpen}
              onClick={() => setNewMenuOpen((v) => !v)}
              className="inline-flex items-center gap-1 rounded-lg border border-accent bg-accent px-2.5 py-1 text-xs font-medium text-white hover:opacity-90"
            >
              <Plus size={12} aria-hidden="true" />
              {t('assets.new', 'New')}
              <ChevronDown size={12} aria-hidden="true" />
            </button>
            {newMenuOpen && (
              <div
                role="menu"
                className="absolute right-0 top-full z-30 mt-1.5 min-w-[11rem] rounded-xl border border-line bg-card py-1 shadow-2xl"
              >
                {ASSET_TYPES.map((type) => {
                  const Icon = ASSET_TYPE_ICON[type];
                  return (
                    <button
                      key={type}
                      type="button"
                      role="menuitem"
                      className={MENU_ITEM}
                      onClick={() => {
                        setNewMenuOpen(false);
                        setNewDialogType(type);
                      }}
                    >
                      <Icon size={13} className="opacity-70" />
                      {t(typeSingularKey(type), type)}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Type tabs. `aria-selected` is read straight off the route — there is
          no local tab state that could disagree with the URL or the rail. */}
      <div role="tablist" className="mt-3 flex gap-1 overflow-x-auto border-b border-line px-5">
        {[null, ...ASSET_TYPES].map((type) => {
          const active = assetType === type;
          const count = tabCount(type);
          return (
            <button
              key={type ?? 'all'}
              role="tab"
              type="button"
              data-tab-type={type ?? 'all'}
              aria-selected={active}
              onClick={() => goToType(type)}
              className={`-mb-px flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-2 text-[13px] transition-colors ${
                active
                  ? 'border-accent font-semibold text-accent'
                  : 'border-transparent text-content-3 hover:text-content'
              }`}
            >
              <span>{type === null ? t('assets.tab.all', 'All') : t(typeLabelKey(type), type)}</span>
              {count !== null && (
                <span className="text-[11px] tabular-nums text-content-4">{count}</span>
              )}
            </button>
          );
        })}
      </div>

      <div className="flex flex-wrap gap-1.5 px-5 py-2.5">
        <Chip
          chipId="project"
          label={t('assets.filter.project', 'Project')}
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
                {t('assets.filter.anyProject', 'Any Project')}
              </button>
              {projectsError && (
                <p className="px-3 py-1.5 text-xs text-danger">
                  {t('assets.projectsUnavailable', 'Projects unavailable')}
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
          chipId="readiness"
          label={t('assets.filter.readiness', 'Readiness')}
          summary={
            filters.readiness
              ? t(`assets.readiness.${filters.readiness}`, READINESS_FALLBACK[filters.readiness])
              : null
          }
          active={filters.readiness !== null}
        >
          {(close) => (
            <>
              <button
                type="button"
                role="menuitemradio"
                aria-checked={filters.readiness === null}
                className={MENU_ITEM}
                onClick={() => {
                  applyFilters({ ...filters, readiness: null });
                  close();
                }}
              >
                {t('assets.filter.anyReadiness', 'Any Readiness')}
              </button>
              {READINESS_VALUES.map((value) => (
                <button
                  key={value}
                  type="button"
                  role="menuitemradio"
                  aria-checked={filters.readiness === value}
                  className={MENU_ITEM}
                  onClick={() => {
                    applyFilters({ ...filters, readiness: value });
                    close();
                  }}
                >
                  {t(`assets.readiness.${value}`, READINESS_FALLBACK[value])}
                </button>
              ))}
            </>
          )}
        </Chip>

        <Chip
          chipId="library"
          label={t('assets.filter.library', 'Library')}
          summary={t(
            `assets.library.${filters.library}`,
            LIBRARY_FALLBACK[filters.library],
          )}
          // Active when the shelf is NARROWED away from its default. `all`
          // widens rather than narrows, so it is not "active" in the sense the
          // other chips use — but it IS a departure from the default, so the
          // summary always shows which of the three is in force.
          active={filters.library === 'out'}
        >
          {(close) => (
            <>
              {LIBRARY_VALUES.map((value) => (
                <button
                  key={value}
                  type="button"
                  role="menuitemradio"
                  aria-checked={filters.library === value}
                  className={MENU_ITEM}
                  onClick={() => {
                    applyFilters({ ...filters, library: value });
                    close();
                  }}
                >
                  {t(`assets.library.${value}`, LIBRARY_FALLBACK[value])}
                </button>
              ))}
            </>
          )}
        </Chip>

        <Chip
          chipId="tag"
          label={t('assets.filter.tag', 'Tag')}
          summary={filters.tag}
          active={filters.tag !== null}
        >
          {(close) => (
            <>
              <button
                type="button"
                role="menuitemradio"
                aria-checked={filters.tag === null}
                className={MENU_ITEM}
                onClick={() => {
                  applyFilters({ ...filters, tag: null });
                  close();
                }}
              >
                {t('assets.filter.anyTag', 'Any Tag')}
              </button>
              {tagOptions.length === 0 && (
                <p className="px-3 py-1.5 text-xs text-content-4">
                  {t('assets.filter.noTags', 'No Tags On This Page')}
                </p>
              )}
              {tagOptions.map((tag) => (
                <button
                  key={tag}
                  type="button"
                  role="menuitemradio"
                  aria-checked={filters.tag === tag}
                  className={MENU_ITEM}
                  onClick={() => {
                    applyFilters({ ...filters, tag });
                    close();
                  }}
                >
                  {tag}
                </button>
              ))}
            </>
          )}
        </Chip>

        <Chip
          chipId="sort"
          label={t('assets.filter.sort', 'Sort')}
          summary={t(`assets.sort.${filters.sort}`, SORT_FALLBACK[filters.sort])}
          // Sorting never removes a row, so the chip is not "active" in the
          // narrowing sense the other three are.
          active={false}
        >
          {(close) => (
            <>
              {SORT_VALUES.map((value) => (
                <button
                  key={value}
                  type="button"
                  role="menuitemradio"
                  aria-checked={filters.sort === value}
                  className={MENU_ITEM}
                  onClick={() => {
                    applyFilters({ ...filters, sort: value });
                    close();
                  }}
                >
                  {t(`assets.sort.${value}`, SORT_FALLBACK[value])}
                </button>
              ))}
            </>
          )}
        </Chip>
      </div>

      <div className="flex-1 overflow-y-auto px-5 pb-10">
        {loading ? (
          <div className="flex items-center gap-2 py-10 text-xs text-content-3">
            <Loader2 size={14} className="animate-spin" aria-hidden="true" />
            {t('common.loading', 'Loading…')}
          </div>
        ) : (
          <>
            {loadError ? (
              <p role="alert" data-testid="asset-load-error" className="py-10 text-sm text-danger">
                {t('assets.loadFailed', 'Could Not Load Assets')}
              </p>
            ) : mine.length === 0 ? (
              <p className="py-10 text-sm text-content-3">{emptyMessage}</p>
            ) : (
              <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3">
                {mine.map((asset) => (
                  <AssetCard
                    key={asset.id}
                    asset={asset}
                    projectNames={projectNames}
                    showTypeTag={assetType === null}
                    onOpen={(row) => openSheet(row.id)}
                  />
                ))}
              </div>
            )}

            {/* Presets are global and read-only, and the sidebar badge does
                not count them. Their own section is what keeps the badge and
                the grid above from silently disagreeing. */}
            {presets.length > 0 && (
              <section data-testid="preset-section" className="mt-6">
                <div className="flex items-baseline gap-2 border-t border-line pt-4">
                  <h3 className="text-[13px] font-medium text-content-2">
                    {t('assets.presets.title', 'System Presets')}
                  </h3>
                  <p className="text-[11px] text-content-4">
                    {t('assets.presets.hint', 'Read-only — duplicate one to edit it')}
                  </p>
                </div>
                <div className="mt-3 grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3">
                  {presets.map((asset) => (
                    <AssetCard
                      key={asset.id}
                      asset={asset}
                      projectNames={projectNames}
                      showTypeTag={assetType === null}
                      onOpen={(row) => openSheet(row.id)}
                    />
                  ))}
                </div>
              </section>
            )}

            {hasMore && (
              <div className="flex flex-col items-center gap-1.5 py-5">
                <button
                  type="button"
                  data-testid="asset-load-more"
                  disabled={loadingMore || readinessSortIsPaged}
                  onClick={() => void loadMore()}
                  className="rounded-lg border border-line-strong bg-card px-3 py-1.5 text-xs font-medium text-content-2 hover:bg-island-2 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {loadingMore
                    ? t('common.loading', 'Loading…')
                    : t('assets.loadMore', 'Load More')}
                </button>
                {readinessSortIsPaged && (
                  <p
                    data-testid="readiness-sort-paged"
                    className="max-w-sm text-center text-[11px] text-content-3"
                  >
                    {t(
                      'assets.readinessSortPaged',
                      'Readiness Sorting Only Orders One Page — Switch Sort To Load More',
                    )}
                  </p>
                )}
              </div>
            )}
          </>
        )}
      </div>

      {newDialogType !== null && (
        <NewAssetDialog
          open
          scopeId={scopeId}
          assetType={newDialogType}
          onClose={() => setNewDialogType(null)}
          onCreated={handleCreated}
          onOpenExisting={(id) => {
            setNewDialogType(null);
            openSheet(id);
          }}
        />
      )}
    </div>
  );
};
