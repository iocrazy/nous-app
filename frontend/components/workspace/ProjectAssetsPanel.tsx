// frontend/components/workspace/ProjectAssetsPanel.tsx
//
// The project workspace's Characters / Locations / Props / Costumes pages, on
// the ASSET LIBRARY (P3 Task 5). One component serves all four — they differ
// only by `assetType` and by whether Import From Script applies.
//
// What changed underneath: these pages used to read `project_characters` /
// `project_lib_entities`, project-local tables whose rows could not leave the
// project. They now read `assets`, which is scope-level, and a project's page
// is a VIEW over `asset_project_refs`. That reframes every action here:
//
//  * Link From Library adds a reference to an asset that already exists.
//  * "+ New" creates in the scope AND references it — a new asset that only
//    the creating project could see would recreate the old model under a new
//    table name.
//  * Unlink removes the REFERENCE ONLY. The copy says so, because "remove"
//    on a page that looks like a library reads as "delete" otherwise, and the
//    undo for a wrong guess is one dialog away either direction.
//
// SCOPE RESOLUTION, and why it is a prop rather than the caller's own team:
// `GET /projects/{id}/assets` derives the scope server-side from the project's
// OWNER, so reads are always aimed correctly. The write endpoints take an
// explicit `scope_id` instead, so this component has to resolve the same
// answer client-side: the project's `team_id`, or — for a personal project —
// the viewer's personal team. Those agree for every project a viewer owns and
// for every team project; they disagree for exactly one case, a collaborator
// on someone ELSE'S PERSONAL PROJECT, where every write here is aimed at the
// collaborator's own personal team instead of the owner's.
//
// The blast radius is NOT uniform across the three write paths, and the
// difference is what a future fixer needs:
//
//  * Unlink and Link From Library's `linkProject` are both refused CLEAN —
//    but by DIFFERENT gates, and naming the wrong one sends the next fixer to
//    the wrong file:
//      · `linkProject` sends the panel's GUESSED scope, which the collaborator
//        really is a member of, so `_gate` admits it and the service's own
//        comparison against the project's resolved scope answers a typed
//        `project_scope_mismatch` (422). The panel renders that sentence.
//      · Unlink sends `asset.scope_id` — the OWNER's scope, straight from the
//        row the server just returned — so it never reaches a scope
//        comparison at all: `_gate` answers `not_a_member` (403) first, and
//        `unlink_project` has no `project_scope_mismatch` path to reach.
//    The row-scope request shape is still the more correct of the two: it is
//    what lets Unlink work on a personal project whose viewer has no resolved
//    personal team, where the guess is NULL and the other two writes disable
//    themselves (pinned by the Unlink case in `e2e/project-assets.spec.ts`).
//  * `+ New` LEAVES A STRAY. `NewAssetDialog` calls `POST /assets` with the
//    guessed scope FIRST, and that endpoint gates only on membership of the
//    scope it was given — which the collaborator legitimately has, for their
//    own personal team. So the asset is created in the wrong library and only
//    the follow-up `linkProject` is refused. The user is told the ref failed,
//    but an orphan row is left behind in a library they were not aiming at.
//  * `Link From Library` also SEARCHES the wrong scope, so the rows it offers
//    are the collaborator's own assets — every pick is then a guaranteed 422.
//    That is the same "offering a click that cannot succeed" this dialog
//    avoids for system presets, reached by a different route.
//
// `Import From Script` is unaffected: that endpoint resolves its own scope.
//
// Recorded rather than papered over: the honest fix is the project payload
// carrying its asset scope, not a better guess here. Until then the reads are
// always right and no write silently succeeds against the wrong library — but
// `+ New` can silently CREATE in one.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { BookmarkMinus, BookmarkPlus, Link2, Loader2, Plus, Unlink, Wand2, X } from 'lucide-react';

import { useToast } from '../Toast';
import { AssetCard } from '../resources/assets/AssetCard';
import { NewAssetDialog } from '../resources/assets/NewAssetDialog';
import { typeLabelKey, typeSingularKey } from '../resources/assets/assetTypeMeta';
import { ASSET_TYPES } from '../assets/assetSlots';
import { useAssetFailureReporter } from '../resources/assets/useAssetFailure';
import { fetchProjects } from '../../services/projectsService';
import type { Project } from '../../types';
import {
  importFromScript,
  linkProject,
  listProjectAssets,
  setAssetLibraryMembership,
  unlinkProject,
} from '../../services/assetsService';
import type {
  AssetRow,
  AssetSummary,
  AssetType,
  ImportFromScriptResponse,
  ImportedAssetItem,
} from '../../services/assetsService';
import { LinkFromLibraryDialog } from './LinkFromLibraryDialog';

/**
 * The types Import From Script can produce. The endpoint reads the whole
 * script and lands BOTH in one call, so the button means the same thing on
 * either panel; on Props and Costumes it is hidden rather than disabled —
 * nothing in a script derives them, so a disabled button would be promising a
 * feature that is not coming.
 */
const IMPORTABLE_TYPES: readonly AssetType[] = ['character', 'location'];

/** A per-item `code` that is NOT a failure: the name was already an asset and
 *  already referenced, which is the idempotent re-run answering honestly. */
const BENIGN_IMPORT_CODE = 'already_linked';

export interface ProjectAssetsPanelProps {
  assetType: AssetType;
  projectId: string;
  /** The project's asset scope — see the scope note at the top of this file.
   *  Null while the workspace has not resolved a team yet: the panel still
   *  READS (that route needs no scope) and says why the writes are off. */
  scopeId: string | null;
  /** The URL's team segment, for building cross-module links. Undefined in the
   *  personal workspace, whose paths carry no `/team/{id}` prefix. */
  teamId?: string;
}

const ACTION =
  'inline-flex items-center gap-1 rounded-lg border border-line-strong px-2.5 py-1 ' +
  'text-xs font-medium text-content-2 hover:bg-island-2 disabled:cursor-not-allowed ' +
  'disabled:opacity-50';

/** One skipped/failed name from an import run. `code` is the machine-readable
 *  reason and gets a translated sentence; the server's own `detail` is the
 *  fallback so an unmapped code still says something true. */
const ImportLine: React.FC<{ item: ImportedAssetItem }> = ({ item }) => {
  const { t } = useTranslation();
  const reason = item.code
    ? t(`assets.err.${item.code}`, item.detail ?? t('assets.err.generic'))
    : (item.detail ?? t('assets.err.generic'));
  return (
    <li
      data-testid="import-failure"
      data-name={item.name}
      data-asset-type={item.asset_type}
      className="text-[11px] text-warn"
    >
      {t('assets.project.importSkipped', {
        name: item.name,
        // The run spans two types; a bare name leaves the reader guessing
        // which panel a refused row belonged to.
        type: t(typeSingularKey(item.asset_type), item.asset_type),
        reason,
        defaultValue: '{{name}} ({{type}}) — {{reason}}',
      })}
    </li>
  );
};

export const ProjectAssetsPanel: React.FC<ProjectAssetsPanelProps> = ({
  assetType,
  projectId,
  scopeId,
  teamId,
}) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { addToast } = useToast();
  const { report: reportFailure, reportRef } = useAssetFailureReporter('ProjectAssetsPanel');

  const [rows, setRows] = useState<AssetRow[]>([]);
  const [loading, setLoading] = useState(true);
  /** A failed list is NOT an empty project. Both would otherwise render the
   *  "nothing linked yet" invitation, telling the user their work is gone. */
  const [loadError, setLoadError] = useState(false);
  const [linkOpen, setLinkOpen] = useState(false);
  const [newOpen, setNewOpen] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<ImportFromScriptResponse | null>(null);
  const [unlinkingId, setUnlinkingId] = useState<string | null>(null);
  /** In flight for the library toggle. Separate from `unlinkingId` — the two
   *  actions sit on the same card and do opposite things, so one spinner for
   *  both would show the wrong control as busy. */
  const [libraryBusyId, setLibraryBusyId] = useState<string | null>(null);
  /** Project id → name, for `AssetCard`'s chips. An asset here may be used by
   *  OTHER projects too, and those chips rendered raw Snowflake ids before
   *  this list was fetched. A failure is non-fatal: the card falls back to a
   *  short id form. */
  const [projects, setProjects] = useState<Project[]>([]);

  /** Monotonic token — a refetch that lands after a newer one is dropped. */
  const requestRef = useRef(0);
  /** Bumped by every mutation to re-run the load effect. */
  const [reloadTick, setReloadTick] = useState(0);
  const refetch = useCallback(() => setReloadTick((n) => n + 1), []);

  useEffect(() => {
    const token = ++requestRef.current;
    setLoading(true);
    listProjectAssets(projectId, assetType)
      .then((page) => {
        if (requestRef.current !== token) return;
        setRows(page);
        setLoadError(false);
      })
      .catch((err) => {
        if (requestRef.current !== token) return;
        setRows([]);
        setLoadError(true);
        reportRef.current(err);
      })
      .finally(() => {
        if (requestRef.current === token) setLoading(false);
      });
    // `reportRef` is a ref (stable identity); listed only for exhaustive-deps.
  }, [projectId, assetType, reloadTick, reportRef]);

  useEffect(() => {
    let alive = true;
    fetchProjects(teamId ? { teamId } : undefined)
      .then((list) => {
        if (alive) setProjects(list);
      })
      .catch((err) => {
        // Recorded, not swallowed — but NOT surfaced as a toast: the panel's
        // own job (this project's assets) is unaffected, and the only visible
        // consequence is chips falling back to a short id.
        console.error('[ProjectAssetsPanel] project list unavailable:', err);
        if (alive) setProjects([]);
      });
    return () => {
      alive = false;
    };
  }, [teamId]);

  const projectNames = useMemo(() => {
    const map: Record<string, string> = {};
    for (const project of projects) map[String(project.id)] = project.name;
    return map;
  }, [projects]);

  // Switching panels drops the previous type's import report: its counts
  // describe names that are not on screen any more.
  useEffect(() => {
    setImportResult(null);
  }, [assetType, projectId]);

  const openSheet = useCallback(
    (assetId: string) => {
      // Mirrors `ResourcesContext.resPath`: the team segment is the URL's
      // workspace scope, absent in the personal workspace.
      const base = teamId ? `/team/${teamId}` : '';
      navigate(`${base}/resources/assets/item/${assetId}`);
    },
    [navigate, teamId],
  );

  const linkedIds = useMemo(() => rows.map((row) => row.id), [rows]);

  const unlink = useCallback(
    async (asset: AssetRow) => {
      if (unlinkingId) return;
      // The asset's OWN scope, not the panel's guess: the row came from the
      // server that knows which library it lives in. Presets carry a null
      // `scope_id` and cannot be project-linked, so the fallback is only ever
      // reached by a row that should not be here.
      const rowScope = asset.scope_id ?? scopeId;
      if (!rowScope) {
        addToast(t('assets.project.scopeUnknown', 'Workspace Not Resolved Yet'), 'error');
        return;
      }
      setUnlinkingId(asset.id);
      try {
        await unlinkProject(rowScope, asset.id, projectId);
        addToast(
          t('assets.project.unlinked', 'Removed From This Project — Still In Your Library'),
          'success',
        );
        refetch();
      } catch (err) {
        reportFailure(err);
      } finally {
        setUnlinkingId(null);
      }
    },
    [unlinkingId, scopeId, projectId, addToast, t, refetch, reportFailure],
  );

  /**
   * Add this asset to the scope's library, or take it out (mig 449).
   *
   * This panel is where the distinction is VISIBLE: 一键导入 and the legacy
   * migration land rows here outside the library, and this is the one click
   * that promotes one. Removing is the inverse and is NOT an unlink — the copy
   * on both the button and the toast says so, because on a page full of
   * project entities "remove" reads as "delete" otherwise.
   *
   * Same scope rule as `unlink`: the asset's OWN `scope_id`, never the panel's
   * guess. A row whose scope the caller is not a member of is refused 403 by
   * the router, which is the honest answer — the alternative is aiming a write
   * at the wrong library.
   */
  const toggleLibrary = useCallback(
    async (asset: AssetRow) => {
      if (libraryBusyId) return;
      const rowScope = asset.scope_id ?? scopeId;
      if (!rowScope) {
        addToast(t('assets.project.scopeUnknown', 'Workspace Not Resolved Yet'), 'error');
        return;
      }
      const next = !asset.in_library;
      setLibraryBusyId(asset.id);
      try {
        await setAssetLibraryMembership(rowScope, asset.id, next);
        addToast(
          next
            ? t('assets.library.added', 'Added To Your Library')
            : t('assets.library.removed', 'Removed From Your Library — Still In This Project'),
          'success',
        );
        refetch();
      } catch (err) {
        // Typed echo, not a silent no-op: the button did nothing and the user
        // is told why, rather than being left to notice the badge never moved.
        reportFailure(err);
      } finally {
        setLibraryBusyId(null);
      }
    },
    [libraryBusyId, scopeId, addToast, t, refetch, reportFailure],
  );

  /** Created in the scope, then referenced from this project. The dialog does
   *  NOT navigate away here (unlike the shelf's use of it): the user is
   *  building this project's cast and expects to keep adding. */
  const handleCreated = useCallback(
    async (created: AssetSummary) => {
      setNewOpen(false);
      if (!scopeId) {
        addToast(t('assets.project.scopeUnknown', 'Workspace Not Resolved Yet'), 'error');
        return;
      }
      try {
        await linkProject(scopeId, created.id, projectId);
        addToast(t('assets.project.linked', 'Added To This Project'), 'success');
      } catch (err) {
        // The asset EXISTS — only the ref failed. Reported as itself rather
        // than as a failed create, which would send the user to make it twice.
        reportFailure(err);
      } finally {
        // Refetch either way: on the failure path the panel must not pretend
        // the row is there, and on the success path the derived fields come
        // from the server rather than from a locally patched object.
        refetch();
      }
    },
    [scopeId, projectId, addToast, t, refetch, reportFailure],
  );

  /** The 409 branch: the name is taken in this scope. Here that is not a dead
   *  end — referencing the existing asset is exactly what the user asked for. */
  const handleOpenExisting = useCallback(
    (assetId: string) => {
      setNewOpen(false);
      if (!scopeId) {
        addToast(t('assets.project.scopeUnknown', 'Workspace Not Resolved Yet'), 'error');
        return;
      }
      linkProject(scopeId, assetId, projectId)
        .then(() => addToast(t('assets.project.linked', 'Added To This Project'), 'success'))
        .catch(reportFailure)
        .finally(refetch);
    },
    [scopeId, projectId, addToast, t, refetch, reportFailure],
  );

  const runImport = useCallback(async () => {
    if (importing) return;
    setImporting(true);
    try {
      const result = await importFromScript(projectId);
      setImportResult(result);
      refetch();
    } catch (err) {
      reportFailure(err);
    } finally {
      setImporting(false);
    }
  }, [importing, projectId, refetch, reportFailure]);

  const typeLabel = t(typeSingularKey(assetType), assetType);
  const canImport = IMPORTABLE_TYPES.includes(assetType);
  const writesDisabled = !scopeId;
  const scopeHint = writesDisabled
    ? t('assets.project.scopeUnknown', 'Workspace Not Resolved Yet')
    : undefined;

  const importItems = importResult?.items ?? [];
  const importFailures = importItems.filter(
    (item) => item.code != null && item.code !== BENIGN_IMPORT_CODE,
  );

  /**
   * How many of each type THIS SCRIPT'S NAMES now sit on the project as, in
   * `ASSET_TYPES` order. Read it as "what the names this run looked at map
   * to", NOT as "what this run wrote".
   *
   * That distinction is load-bearing on a RE-RUN: a name that was already an
   * asset and already referenced comes back `action: 'skipped'` with
   * `code: 'already_linked'` and `linked: true`, so it keeps counting here
   * while the summary above says "0 Created, 0 Linked, N Skipped". Both are
   * true and they are answering different questions — the summary reports the
   * writes, the chips report where the reader will find the cards. Pinned by
   * `renders the same chips on an idempotent re-run` in the test file.
   *
   * Keyed on `linked`, not on `action`, for the same reason: the endpoint
   * lands Characters AND Locations in one call, so a Characters panel that
   * gains three cards after a "5 Created" summary reads as an import that
   * under-delivered. `linked` is the field that decides whether a card
   * appears — an item can be `action: 'created'` with `linked: false` (the
   * asset landed, the ref did not), and counting that one here would promise
   * a card that is not coming. It already has its own failure line.
   */
  const importedByType = ASSET_TYPES.map((type) => ({
    type,
    n: importItems.filter((item) => item.linked === true && item.asset_type === type).length,
  })).filter((entry) => entry.n > 0);

  return (
    <div className="flex h-full flex-col" data-testid="project-assets-panel" data-asset-type={assetType}>
      <div className="flex flex-wrap items-center gap-2 pb-3">
        <h2 className="text-base font-semibold text-content">
          {t(typeLabelKey(assetType), assetType)}
        </h2>
        {!loading && !loadError && (
          <span data-testid="project-assets-count" className="text-[11px] tabular-nums text-content-4">
            {t('assets.project.count', { n: rows.length, defaultValue: '{{n}} Linked' })}
          </span>
        )}
        <div className="flex-1" />

        {canImport && (
          <button
            type="button"
            data-testid="import-from-script"
            disabled={importing}
            onClick={() => void runImport()}
            className={ACTION}
          >
            {importing ? (
              <Loader2 size={12} className="animate-spin" aria-hidden="true" />
            ) : (
              <Wand2 size={12} aria-hidden="true" />
            )}
            {t('assets.importFromScript', 'Import From Script')}
          </button>
        )}

        <button
          type="button"
          data-testid="link-from-library"
          disabled={writesDisabled}
          title={scopeHint}
          onClick={() => setLinkOpen(true)}
          className={ACTION}
        >
          <Link2 size={12} aria-hidden="true" />
          {t('assets.project.linkFromLibrary', 'Link From Library')}
        </button>

        <button
          type="button"
          data-testid="new-asset"
          disabled={writesDisabled}
          title={scopeHint}
          onClick={() => setNewOpen(true)}
          className="inline-flex items-center gap-1 rounded-lg border border-accent bg-accent px-2.5 py-1 text-xs font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Plus size={12} aria-hidden="true" />
          {t('assets.newOfType', { type: typeLabel, defaultValue: 'New {{type}}' })}
        </button>
      </div>

      {/* The import report. Counts alone would let a refused name read as an
          import, so every non-benign per-item code gets its own line. */}
      {importResult && (
        <div
          data-testid="import-report"
          className="mb-3 rounded-xl border border-line bg-island-2 px-3 py-2"
        >
          <div className="flex items-start gap-2">
            <p className="flex-1 text-xs text-content-2">
              {t('assets.project.importDone', {
                created: importResult.created,
                linked: importResult.linked,
                skipped: importResult.skipped,
                defaultValue:
                  'Imported — {{created}} Created, {{linked}} Linked, {{skipped}} Skipped',
              })}
            </p>
            <button
              type="button"
              onClick={() => setImportResult(null)}
              aria-label={t('common.close', 'Close')}
              className="text-content-4 hover:text-content"
            >
              <X size={13} aria-hidden="true" />
            </button>
          </div>
          {importedByType.length > 0 && (
            <p data-testid="import-by-type" className="flex flex-wrap gap-1.5 pt-1">
              {importedByType.map(({ type, n }) => (
                <span
                  key={type}
                  data-asset-type={type}
                  className="rounded-full border border-line-strong px-1.5 text-[10px] text-content-3"
                >
                  {t('assets.project.importTypeCount', {
                    type: t(typeLabelKey(type), type),
                    n,
                    // `{{type}} {{n}}` rather than "{{n}} {{type}}": the type
                    // labels are plurals, so a leading count would read "1
                    // Characters" whenever a run lands exactly one.
                    defaultValue: '{{type}} {{n}}',
                  })}
                </span>
              ))}
            </p>
          )}
          {importResult.items.length === 0 && (
            <p data-testid="import-nothing" className="pt-1 text-[11px] text-content-3">
              {t(
                'assets.project.importNothing',
                'Your Scripts Name No Characters Or Locations Yet',
              )}
            </p>
          )}
          {importFailures.length > 0 && (
            <ul className="flex flex-col gap-0.5 pt-1">
              {importFailures.map((item) => (
                <ImportLine key={`${item.asset_type}:${item.name}`} item={item} />
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center gap-2 py-10 text-xs text-content-3">
            <Loader2 size={14} className="animate-spin" aria-hidden="true" />
            {t('common.loading', 'Loading…')}
          </div>
        ) : loadError ? (
          <p role="alert" data-testid="project-assets-error" className="py-10 text-sm text-danger">
            {t('assets.project.loadFailed', 'Could Not Load This Project’s Assets')}
          </p>
        ) : rows.length === 0 ? (
          <p data-testid="project-assets-empty" className="py-10 text-sm text-content-3">
            {t('assets.project.empty', {
              type: typeLabel,
              defaultValue: 'No {{type}} In This Project Yet — Link One Or Create One',
            })}
          </p>
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3">
            {rows.map((asset) => (
              // The card is itself a button, so the unlink control is a
              // SIBLING overlay rather than a child — a button inside a button
              // is invalid HTML and swallows one of the two clicks.
              <div key={asset.id} className="relative">
                <AssetCard
                  asset={asset}
                  projectNames={projectNames}
                  onOpen={(row) => openSheet(row.id)}
                />
                <button
                  type="button"
                  data-testid="unlink-asset"
                  data-asset-id={asset.id}
                  disabled={unlinkingId !== null}
                  onClick={() => void unlink(asset)}
                  title={t(
                    'assets.project.unlinkHint',
                    'Removes it from this project only — the asset stays in your library',
                  )}
                  aria-label={t('assets.project.unlink', {
                    name: asset.name,
                    defaultValue: 'Remove {{name}} From This Project',
                  })}
                  className="absolute left-1.5 top-1.5 rounded-full border border-line-strong bg-card/90 p-1 text-content-3 hover:text-danger disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {unlinkingId === asset.id ? (
                    <Loader2 size={12} className="animate-spin" aria-hidden="true" />
                  ) : (
                    <Unlink size={12} aria-hidden="true" />
                  )}
                </button>

                {/* Library membership. Its own control, opposite corner from
                    Unlink: the two look alike and mean very different things
                    (one changes which project shows the asset, the other
                    changes whether the LIBRARY does), so they are kept apart
                    and each carries its own sentence. */}
                <button
                  type="button"
                  data-testid="toggle-library"
                  data-asset-id={asset.id}
                  data-in-library={asset.in_library}
                  disabled={libraryBusyId !== null}
                  onClick={() => void toggleLibrary(asset)}
                  title={
                    asset.in_library
                      ? t(
                          'assets.library.removeHint',
                          'Takes it off your library shelf — it stays in this project',
                        )
                      : t(
                          'assets.library.addHint',
                          'Puts it on your library shelf so other projects can use it',
                        )
                  }
                  aria-label={
                    asset.in_library
                      ? t('assets.library.removeNamed', {
                          name: asset.name,
                          defaultValue: 'Remove {{name}} From Your Library',
                        })
                      : t('assets.library.addNamed', {
                          name: asset.name,
                          defaultValue: 'Add {{name}} To Your Library',
                        })
                  }
                  className="absolute right-1.5 bottom-1.5 rounded-full border border-line-strong bg-card/90 p-1 text-content-3 hover:text-accent disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {libraryBusyId === asset.id ? (
                    <Loader2 size={12} className="animate-spin" aria-hidden="true" />
                  ) : asset.in_library ? (
                    <BookmarkMinus size={12} aria-hidden="true" />
                  ) : (
                    <BookmarkPlus size={12} aria-hidden="true" />
                  )}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {linkOpen && scopeId && (
        <LinkFromLibraryDialog
          open
          scopeId={scopeId}
          projectId={projectId}
          assetType={assetType}
          linkedIds={linkedIds}
          onClose={() => setLinkOpen(false)}
          onLinked={refetch}
        />
      )}

      {newOpen && scopeId && (
        <NewAssetDialog
          open
          scopeId={scopeId}
          assetType={assetType}
          onClose={() => setNewOpen(false)}
          onCreated={(created) => void handleCreated(created)}
          onOpenExisting={handleOpenExisting}
        />
      )}
    </div>
  );
};
