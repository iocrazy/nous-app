// frontend/components/resources/assets/sheet/LinkAssetDialog.tsx
//
// "Add" on a relation section: search the ONE type this relation accepts, pick
// a row, create the link.
//
// The search is deliberately narrowed to `spec.targetType` rather than showing
// everything and letting the server refuse: `link_allowed` is mirrored in
// `assetSlots.ts`, so an impossible pair is unreachable here instead of
// arriving as a 422 `link_not_allowed` after the user already chose. Presets
// are filtered out for the same reason - a link's two ends must share a scope,
// and a global preset has none.

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';

import { UiModal } from '../../../ui/primitives';
import { listAssets } from '../../../../services/assetsService';
import type { AssetLinkRelation, AssetRow, AssetType } from '../../../../services/assetsService';
import { getResourceCoverUrl } from '../../../../services/resourceService';
import { ASSET_TYPE_ICON, typeSingularKey } from '../assetTypeMeta';

/** Enough rows to pick from without paginating a picker. */
const SEARCH_LIMIT = 30;

export interface LinkAssetDialogProps {
  open: boolean;
  scopeId: string;
  /** The type the relation accepts. */
  targetType: AssetType;
  relation: AssetLinkRelation;
  /** Ids already linked - shown as "Linked" rather than hidden, so a user who
   *  searched for one is told why it cannot be picked instead of concluding
   *  the asset does not exist. */
  existingIds: string[];
  /** The sheet's own id, which can never be a target of its own link. */
  selfId: string;
  onClose: () => void;
  onPick: (asset: AssetRow) => void;
  onError: (err: unknown) => void;
}

export const LinkAssetDialog: React.FC<LinkAssetDialogProps> = ({
  open,
  scopeId,
  targetType,
  relation,
  existingIds,
  selfId,
  onClose,
  onPick,
  onError,
}) => {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');
  const [rows, setRows] = useState<AssetRow[]>([]);
  const [loading, setLoading] = useState(false);
  /** A failed search is not an empty result set - see the shelf's `loadError`
   *  for the same distinction. */
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!open) return;
    setQuery('');
  }, [open, targetType]);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setLoading(true);
    const timer = setTimeout(() => {
      listAssets(scopeId, {
        type: targetType,
        q: query.trim() === '' ? undefined : query.trim(),
        limit: SEARCH_LIMIT,
      })
        .then((page) => {
          if (!alive) return;
          setRows(page);
          setFailed(false);
        })
        .catch((err) => {
          if (!alive) return;
          setRows([]);
          setFailed(true);
          onError(err);
        })
        .finally(() => {
          if (alive) setLoading(false);
        });
    }, 200);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
    // `onError` is a stable reporter from the sheet; re-running on its identity
    // would make this a search loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, scopeId, targetType, query]);

  if (!open) return null;

  const Icon = ASSET_TYPE_ICON[targetType];
  const linked = new Set(existingIds);
  // A preset has no scope, so it cannot be either end of a link (the service
  // requires both ends in the same scope). Filtering here keeps the picker
  // from offering rows the server would refuse.
  const candidates = rows.filter((row) => !row.is_system_preset && row.id !== selfId);

  return (
    <UiModal
      isOpen
      onClose={onClose}
      title={t(`assets.rel.add.${relation}`, {
        type: t(typeSingularKey(targetType), targetType),
        defaultValue: 'Link {{type}}',
      })}
      widthClassName="max-w-md"
    >
      <div className="flex flex-col gap-3" data-testid="link-asset-dialog">
        <input
          autoFocus
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label={t('assets.rel.search', 'Search Assets')}
          placeholder={t('assets.rel.search', 'Search Assets')}
          data-testid="link-search"
          className="w-full rounded-lg border border-line-strong bg-card px-2.5 py-1.5 text-[13px] text-content placeholder:text-content-4 focus:border-accent focus:outline-none"
        />

        <div className="max-h-72 overflow-y-auto">
          {loading ? (
            <p className="flex items-center gap-2 py-6 text-xs text-content-3">
              <Loader2 size={14} className="animate-spin" aria-hidden="true" />
              {t('common.loading', 'Loading...')}
            </p>
          ) : failed ? (
            <p role="alert" className="py-6 text-xs text-danger">
              {t('assets.loadFailed', 'Could Not Load Assets')}
            </p>
          ) : candidates.length === 0 ? (
            <p className="py-6 text-xs text-content-3">
              {t('assets.rel.noCandidates', 'Nothing To Link Here Yet')}
            </p>
          ) : (
            <ul className="flex flex-col">
              {candidates.map((row) => {
                const already = linked.has(row.id);
                return (
                  <li key={row.id}>
                    <button
                      type="button"
                      data-testid="link-candidate"
                      data-asset-id={row.id}
                      disabled={already}
                      onClick={() => onPick(row)}
                      className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-island-2 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <span className="h-8 w-8 shrink-0 overflow-hidden rounded border border-line bg-island-2">
                        {row.cover_file_id ? (
                          <img
                            src={getResourceCoverUrl(row.cover_file_id)}
                            alt=""
                            className="h-full w-full object-cover"
                          />
                        ) : (
                          <span className="flex h-full w-full items-center justify-center text-content-4">
                            <Icon size={13} />
                          </span>
                        )}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13px] text-content">{row.name}</span>
                        {row.role_tag !== '' && (
                          <span className="block truncate text-[11px] text-content-4">
                            {row.role_tag}
                          </span>
                        )}
                      </span>
                      {already && (
                        <span className="text-[10px] text-content-4">
                          {t('assets.rel.alreadyLinked', 'Linked')}
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </UiModal>
  );
};
