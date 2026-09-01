// frontend/components/workspace/LinkFromLibraryDialog.tsx
//
// "Link From Library" on a project asset panel: search the workspace's asset
// library for one type, pick a row, write the project ref.
//
// Two things it deliberately does NOT do:
//
//  * It does not create anything. A name the library has never seen belongs to
//    "+ New" (which creates AND links); offering creation from a search box
//    would make the same mistake twice under two labels.
//  * It does not list assets already on the panel. `searchAssets` answers over
//    the whole scope, so the already-linked rows come back too — offering them
//    would produce a click whose only visible effect is the dialog closing
//    (`link_project` is `on_conflict_do_nothing`, a 201 for a row that already
//    existed). They are filtered against the panel's own ids instead, and when
//    that empties the result the dialog SAYS so rather than showing a blank
//    list that reads as "the library is empty".
//
// System presets are excluded for a harder reason than tidiness: a preset's
// `scope_id` is NULL and lives in no team, so `link_project` refuses it with
// `project_scope_mismatch`. Offering one would be offering a guaranteed error.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, Search } from 'lucide-react';

import { UiButton, UiModal } from '../ui/primitives';
import { linkProject, searchAssets } from '../../services/assetsService';
import type { AssetSummary, AssetType } from '../../services/assetsService';
import { typeSingularKey } from '../resources/assets/assetTypeMeta';
import { useAssetFailureReporter } from '../resources/assets/useAssetFailure';

/** How many candidates one search returns. Matches the shelf's page size in
 *  spirit: enough to pick from, short enough to scan. */
const SEARCH_LIMIT = 40;

/** Debounce before a keystroke becomes a request. */
const SEARCH_DELAY_MS = 250;

export interface LinkFromLibraryDialogProps {
  open: boolean;
  /** The asset scope to search — the PROJECT's scope, not the caller's team.
   *  The panel resolves it; see `ProjectAssetsPanel`'s note on why. */
  scopeId: string;
  projectId: string;
  assetType: AssetType;
  /** Ids already on the panel. Excluded from the results. */
  linkedIds: readonly string[];
  onClose: () => void;
  /** A ref landed — the panel refetches rather than patching its own list, so
   *  the readiness/derived fields come from the server that just wrote. */
  onLinked: () => void;
}

const ROW =
  'flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] ' +
  'text-content-2 hover:bg-island-2 disabled:cursor-not-allowed disabled:opacity-50';

export const LinkFromLibraryDialog: React.FC<LinkFromLibraryDialogProps> = ({
  open,
  scopeId,
  projectId,
  assetType,
  linkedIds,
  onClose,
  onLinked,
}) => {
  const { t } = useTranslation();
  const { report: reportFailure, reportRef } = useAssetFailureReporter('LinkFromLibraryDialog');

  const [q, setQ] = useState('');
  const [results, setResults] = useState<AssetSummary[]>([]);
  const [loading, setLoading] = useState(false);
  /** A failed search is not an empty library — the two render different lines
   *  for the same reason `AssetShelf` keeps `loadError` separate. */
  const [searchError, setSearchError] = useState(false);
  const [linkingId, setLinkingId] = useState<string | null>(null);

  /** Monotonic token: a slow search that lands after a newer one is dropped
   *  instead of painting stale candidates over fresh ones. */
  const requestRef = useRef(0);

  // Re-opening starts clean; a previous query's results greeting the next
  // open would be candidates for a question the user is no longer asking.
  useEffect(() => {
    if (!open) return;
    setQ('');
    setResults([]);
    setSearchError(false);
    setLinkingId(null);
  }, [open, assetType]);

  useEffect(() => {
    if (!open) return;
    const token = ++requestRef.current;
    const timer = setTimeout(() => {
      setLoading(true);
      searchAssets(scopeId, { type: assetType, q: q.trim() || undefined, limit: SEARCH_LIMIT })
        .then((rows) => {
          if (requestRef.current !== token) return;
          setResults(rows);
          setSearchError(false);
        })
        .catch((err) => {
          if (requestRef.current !== token) return;
          setResults([]);
          setSearchError(true);
          reportRef.current(err);
        })
        .finally(() => {
          if (requestRef.current === token) setLoading(false);
        });
    }, SEARCH_DELAY_MS);
    return () => clearTimeout(timer);
    // `reportRef` is a ref (stable identity); listed only for exhaustive-deps.
  }, [open, scopeId, assetType, q, reportRef]);

  const link = useCallback(
    async (asset: AssetSummary) => {
      if (linkingId) return;
      setLinkingId(asset.id);
      try {
        await linkProject(scopeId, asset.id, projectId);
        onLinked();
        onClose();
      } catch (err) {
        reportFailure(err);
      } finally {
        setLinkingId(null);
      }
    },
    [linkingId, scopeId, projectId, onLinked, onClose, reportFailure],
  );

  if (!open) return null;

  const linked = new Set(linkedIds);
  const candidates = results.filter((row) => !linked.has(row.id) && !row.is_system_preset);
  /** Distinct from "no candidates": the search DID find rows, they are all
   *  already on this panel. Saying "nothing to link" without that would read
   *  as a library that lost its contents. */
  const allAlreadyLinked = candidates.length === 0 && results.length > 0;

  return (
    <UiModal
      isOpen
      onClose={onClose}
      title={t('assets.project.linkTitle', {
        type: t(typeSingularKey(assetType), assetType),
        defaultValue: 'Link {{type}} From Library',
      })}
      footer={
        <UiButton variant="ghost" size="sm" onClick={onClose}>
          {t('common.cancel', 'Cancel')}
        </UiButton>
      }
    >
      <div data-testid="link-from-library" className="flex flex-col gap-2">
        <label className="relative block">
          <Search
            size={13}
            aria-hidden="true"
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-content-4"
          />
          <input
            type="search"
            value={q}
            autoFocus
            onChange={(e) => setQ(e.target.value)}
            placeholder={t('assets.project.search', 'Search The Library')}
            aria-label={t('assets.project.search', 'Search The Library')}
            className="w-full rounded-lg border border-line-strong bg-card py-1.5 pl-8 pr-2.5 text-[13px] text-content placeholder:text-content-4 focus:border-accent focus:outline-none"
          />
        </label>

        <div className="max-h-72 min-h-[9rem] overflow-y-auto">
          {loading ? (
            <p className="flex items-center gap-2 px-2.5 py-6 text-xs text-content-3">
              <Loader2 size={13} className="animate-spin" aria-hidden="true" />
              {t('common.loading', 'Loading…')}
            </p>
          ) : searchError ? (
            <p role="alert" data-testid="link-search-error" className="px-2.5 py-6 text-xs text-danger">
              {t('assets.project.searchFailed', 'Could Not Search The Library')}
            </p>
          ) : candidates.length === 0 ? (
            <p data-testid="link-no-candidates" className="px-2.5 py-6 text-xs text-content-3">
              {allAlreadyLinked
                ? t('assets.project.allLinked', 'Everything Found Is Already In This Project')
                : t('assets.project.noCandidates', 'Nothing In The Library Matches')}
            </p>
          ) : (
            candidates.map((asset) => (
              <button
                key={asset.id}
                type="button"
                data-testid="link-candidate"
                data-asset-id={asset.id}
                disabled={linkingId !== null}
                onClick={() => void link(asset)}
                className={ROW}
              >
                <span className="truncate">{asset.name}</span>
                {asset.role_tag !== '' && (
                  <span className="truncate text-[11px] text-content-4">{asset.role_tag}</span>
                )}
                <span className="flex-1" />
                {linkingId === asset.id ? (
                  <Loader2 size={12} className="animate-spin" aria-hidden="true" />
                ) : (
                  <span className="text-[11px] text-content-4">
                    {asset.readiness.state === 'ready'
                      ? t('assets.readiness.ready', 'Ready')
                      : t('assets.readiness.draft', 'Draft')}
                  </span>
                )}
              </button>
            ))
          )}
        </div>
      </div>
    </UiModal>
  );
};
