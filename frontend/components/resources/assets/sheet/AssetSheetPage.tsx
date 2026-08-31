// frontend/components/resources/assets/sheet/AssetSheetPage.tsx
//
// The entity sheet (spec screens 4 / 4b / 4c). ONE skeleton for all six types:
//
//   breadcrumb -> header -> body -> relations -> prompt -> right column
//
// and exactly three substitutions in the body, which is what "six sheets that
// differ only in ..." means in code:
//
//   prompt : no board at all - the PromptEditor IS the body (its placeholders,
//            platform params and Examples pins come with it).
//   audio  : waveform + variants instead of a board.
//   others : the pinboard.
//
// Everything else - loadouts (characters only), relation sections, the prompt
// block, Details / Used In - is decided by the model functions in
// `assetSheetModel.ts`, not by a branch per type in here.
//
// Two page-level contracts:
//
//  * AFTER A DELETE OR DUPLICATE, `refreshAssetCounts()`. Nothing else moves
//    the six sidebar badges; skipping it leaves them stating a number that is
//    no longer true (Task 6 wired only create).
//  * A SYSTEM PRESET IS READ-ONLY, ALL THE WAY DOWN. `is_system_preset` sets
//    `readOnly`, which every child honours by not rendering the control at
//    all. The server would answer 403 `system_preset_readonly` anyway; the
//    point is not to offer an action whose only outcome is a refusal.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ChevronRight, Loader2 } from 'lucide-react';

import { useResourcesContext } from '../../../../contexts/ResourcesContext';
import { useToast } from '../../../Toast';
import { fetchProjects } from '../../../../services/projectsService';
import type { Project } from '../../../../types';
import {
  createLink,
  deleteAsset,
  deleteLink,
  duplicateAsset,
  fetchAssetDetail,
  updateAsset,
} from '../../../../services/assetsService';
import type {
  AssetLinkRelation,
  AssetRow,
  AssetRowDetail,
  AssetUpdateBody,
} from '../../../../services/assetsService';
import { typeLabelKey } from '../assetTypeMeta';
import { useAssetFailureReporter } from '../useAssetFailure';
import {
  composeLoadoutPrompt,
  defaultLoadoutId,
  hasLoadouts,
  relatedAssetIds,
  relationSectionsFor,
} from './assetSheetModel';
import { AssetBoard } from './AssetBoard';
import { AssetSheetHeader } from './AssetSheetHeader';
import { AudioSheetBody } from './AudioSheetBody';
import { LoadoutChips } from './LoadoutChips';
import { PromptEditor } from './PromptEditor';
import { RelationsSection } from './RelationsSection';
import { SheetSidebar } from './SheetSidebar';

export interface AssetSheetPageProps {
  assetId: string;
}

export const AssetSheetPage: React.FC<AssetSheetPageProps> = ({ assetId }) => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const navigate = useNavigate();
  const { scopeId, teamId, resPath, refreshAssetCounts } = useResourcesContext();
  const { report, reportRef } = useAssetFailureReporter('AssetSheetPage');

  const [detail, setDetail] = useState<AssetRowDetail | null>(null);
  const [loading, setLoading] = useState(true);
  /** A failed load is NOT a missing asset. Rendering "not found" for a network
   *  error tells the user their asset is gone when it is not. */
  const [loadFailed, setLoadFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [selectedLoadoutId, setSelectedLoadoutId] = useState<string | null>(null);
  const [related, setRelated] = useState<Record<string, AssetRow | undefined>>({});
  const [projects, setProjects] = useState<Project[]>([]);
  /** Bumped to re-run the detail fetch after a write. */
  const [reloadTick, setReloadTick] = useState(0);

  const reload = useCallback(() => setReloadTick((n) => n + 1), []);

  // --- Detail ---------------------------------------------------------------

  useEffect(() => {
    if (!scopeId) return;
    let alive = true;
    setLoading(true);
    fetchAssetDetail(scopeId, assetId)
      .then((row) => {
        if (!alive) return;
        setDetail(row);
        setLoadFailed(false);
      })
      .catch((err) => {
        if (!alive) return;
        setDetail(null);
        setLoadFailed(true);
        reportRef.current(err);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [scopeId, assetId, reloadTick, reportRef]);

  // The selected loadout follows the asset. Keeping a selection across assets
  // would filter one character's board by another's outfit id and silently
  // show an empty `worn` pin.
  useEffect(() => {
    setSelectedLoadoutId(detail ? defaultLoadoutId(detail.loadouts) : null);
  }, [detail?.id, detail?.loadouts.length]); // eslint-disable-line react-hooks/exhaustive-deps

  // --- Related assets -------------------------------------------------------
  //
  // Link rows carry ids only, and the loadout chips name costumes that have no
  // link row of their own, so both have to be resolved before anything can
  // render a NAME. There is no batch endpoint (`GET /assets` filters, it does
  // not take an id list), so this is one request per related asset - bounded by
  // how many costumes and props one character has, and recorded as a
  // limitation rather than papered over.
  //
  // Each fetch settles on its own: one costume the caller cannot read must not
  // blank the other five. An unresolved id renders as itself downstream.

  const relatedIds = useMemo(
    () => (detail ? relatedAssetIds(detail) : []),
    [detail],
  );
  const relatedKey = relatedIds.join(',');

  useEffect(() => {
    if (!scopeId || relatedKey === '') {
      setRelated({});
      return;
    }
    let alive = true;
    const ids = relatedKey.split(',');
    Promise.allSettled(ids.map((id) => fetchAssetDetail(scopeId, id))).then((results) => {
      if (!alive) return;
      const next: Record<string, AssetRow | undefined> = {};
      results.forEach((result, index) => {
        if (result.status === 'fulfilled') next[ids[index]] = result.value;
        else console.error('[AssetSheetPage] related asset unreadable:', ids[index], result.reason);
      });
      setRelated(next);
    });
    return () => {
      alive = false;
    };
  }, [scopeId, relatedKey]);

  useEffect(() => {
    let alive = true;
    fetchProjects(teamId ? { teamId } : undefined)
      .then((list) => {
        if (alive) setProjects(list);
      })
      .catch((err) => {
        // Only the Used In chips depend on this; they fall back to raw ids.
        console.error('[AssetSheetPage] project list unavailable:', err);
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

  // --- Mutations ------------------------------------------------------------

  const patch = useCallback(
    async (body: AssetUpdateBody) => {
      if (!scopeId || !detail) return;
      try {
        await updateAsset(scopeId, detail.id, body);
        reload();
      } catch (err) {
        report(err);
      }
    },
    [scopeId, detail, reload, report],
  );

  const addLink = useCallback(
    async (toAssetId: string, relation: AssetLinkRelation) => {
      if (!scopeId || !detail) return;
      try {
        await createLink(scopeId, detail.id, toAssetId, relation);
        reload();
      } catch (err) {
        report(err);
      }
    },
    [scopeId, detail, reload, report],
  );

  const removeLink = useCallback(
    async (toAssetId: string, relation: AssetLinkRelation) => {
      if (!scopeId || !detail) return;
      try {
        await deleteLink(scopeId, detail.id, toAssetId, relation);
        reload();
      } catch (err) {
        report(err);
      }
    },
    [scopeId, detail, reload, report],
  );

  const openAsset = useCallback(
    (id: string) => navigate(resPath(`/resources/assets/item/${id}`)),
    [navigate, resPath],
  );

  const onDuplicate = useCallback(async () => {
    if (!scopeId || !detail || busy) return;
    setBusy(true);
    try {
      const copy = await duplicateAsset(scopeId, detail.id);
      // CONTRACT: the six sidebar badges only move when this is called.
      refreshAssetCounts();
      openAsset(copy.id);
    } catch (err) {
      report(err);
    } finally {
      setBusy(false);
    }
  }, [scopeId, detail, busy, refreshAssetCounts, openAsset, report]);

  const onDelete = useCallback(async () => {
    if (!scopeId || !detail || busy) return;
    // A soft delete is still a delete from where the user stands; the shelf has
    // no undo for it yet, so it asks.
    const confirmed = window.confirm(
      t('assets.sheet.confirmDelete', {
        name: detail.name,
        defaultValue: 'Delete {{name}}? Attached files stay in the library.',
      }),
    );
    if (!confirmed) return;
    setBusy(true);
    try {
      await deleteAsset(scopeId, detail.id);
      refreshAssetCounts();
      navigate(resPath(`/resources/assets/${detail.asset_type}`));
    } catch (err) {
      report(err);
    } finally {
      setBusy(false);
    }
  }, [scopeId, detail, busy, t, refreshAssetCounts, navigate, resPath, report]);

  const activeLoadout = useMemo(
    () => detail?.loadouts.find((l) => l.id === selectedLoadoutId) ?? null,
    [detail, selectedLoadoutId],
  );

  const onCopyLoadoutPrompt = useCallback(async () => {
    if (!detail) return;
    const text = composeLoadoutPrompt(detail, activeLoadout, related);
    if (text === '') {
      // Nothing to copy is a real answer, not a silent success: a clipboard
      // write of "" looks identical to a copy that worked.
      addToast(t('assets.sheet.nothingToCopy', 'There Is No Prompt To Copy Yet'), 'info');
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      addToast(t('assets.sheet.promptCopied', 'Prompt Copied'), 'success');
    } catch (err) {
      console.error('[AssetSheetPage] clipboard write failed:', err);
      addToast(t('assets.sheet.copyFailed', 'Could Not Copy To The Clipboard'), 'error');
    }
  }, [detail, activeLoadout, related, addToast, t]);

  // --- Render ---------------------------------------------------------------

  if (loading) {
    return (
      <div className="flex flex-1 items-center gap-2 p-6 text-xs text-content-3">
        <Loader2 size={14} className="animate-spin" aria-hidden="true" />
        {t('common.loading', 'Loading...')}
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="flex-1 p-6">
        <p role="alert" data-testid="sheet-load-error" className="text-sm text-danger">
          {loadFailed
            ? t('assets.loadFailed', 'Could Not Load Assets')
            : t('assets.err.not_found', 'That asset no longer exists.')}
        </p>
        <button
          type="button"
          onClick={() => navigate(resPath('/resources/assets'))}
          className="mt-3 rounded-lg border border-line-strong px-2.5 py-1 text-xs font-medium text-content-2 hover:bg-island-2"
        >
          {t('assets.sheet.backToAssets', 'Back To Assets')}
        </button>
      </div>
    );
  }

  const readOnly = detail.is_system_preset;
  const sections = relationSectionsFor(detail.asset_type, detail.subtype);

  return (
    <div
      className="flex-1 overflow-y-auto p-5"
      data-testid="asset-sheet"
      data-asset-id={detail.id}
      data-asset-type={detail.asset_type}
      data-read-only={readOnly}
    >
      <nav aria-label="Breadcrumb" className="mb-4 flex items-center gap-1 text-[11px] text-content-4">
        <button
          type="button"
          data-testid="crumb-assets"
          onClick={() => navigate(resPath('/resources/assets'))}
          className="hover:text-content-2"
        >
          {t('resources.assets', 'Assets')}
        </button>
        <ChevronRight size={11} aria-hidden="true" />
        <button
          type="button"
          data-testid="crumb-type"
          onClick={() => navigate(resPath(`/resources/assets/${detail.asset_type}`))}
          className="hover:text-content-2"
        >
          {t(typeLabelKey(detail.asset_type), detail.asset_type)}
        </button>
        <ChevronRight size={11} aria-hidden="true" />
        <span className="truncate text-content-3">{detail.name}</span>
      </nav>

      <div className="flex gap-6">
        <div className="flex min-w-0 flex-1 flex-col gap-6">
          <AssetSheetHeader detail={detail} readOnly={readOnly} onPatch={(b) => void patch(b)}>
            {hasLoadouts(detail.asset_type) && scopeId && (
              <LoadoutChips
                scopeId={scopeId}
                assetId={detail.id}
                loadouts={detail.loadouts}
                selectedId={selectedLoadoutId}
                readOnly={readOnly}
                onSelect={setSelectedLoadoutId}
                onChanged={reload}
                onError={report}
              />
            )}
          </AssetSheetHeader>

          {/* The body, and the only place the six types diverge.
              TASK 8 PLUGS IN HERE: `AssetBoard` and `AudioSheetBody` both take
              optional `onEquip(slot)` / `onGenerate(slot)`. Until they are
              passed, both buttons render DISABLED with an "arrives shortly"
              title - visible, inert and honest, rather than live and silent. */}
          {detail.asset_type === 'audio' ? (
            <AudioSheetBody detail={detail} readOnly={readOnly} />
          ) : detail.asset_type === 'prompt' ? null : (
            scopeId && (
              <AssetBoard
                scopeId={scopeId}
                detail={detail}
                loadoutId={selectedLoadoutId}
                readOnly={readOnly}
                onAssetUpdated={reload}
                onError={report}
              />
            )
          )}

          {sections.map((spec) => (
            <RelationsSection
              key={spec.key}
              scopeId={scopeId}
              detail={detail}
              spec={spec}
              related={related}
              readOnly={readOnly}
              onOpenAsset={openAsset}
              onAdd={(toId) => spec.addRelation && void addLink(toId, spec.addRelation)}
              onRemove={(toId, relation) => void removeLink(toId, relation)}
              onError={report}
            />
          ))}

          {scopeId && (
            <PromptEditor
              scopeId={scopeId}
              detail={detail}
              readOnly={readOnly}
              showPromptExtras={detail.asset_type === 'prompt'}
              onDetailUpdated={setDetail}
              onChanged={reload}
              onError={report}
            />
          )}
        </div>

        <SheetSidebar
          detail={detail}
          loadout={activeLoadout}
          readOnly={readOnly}
          projectNames={projectNames}
          busy={busy}
          onCopyLoadoutPrompt={() => void onCopyLoadoutPrompt()}
          onDuplicate={() => void onDuplicate()}
          onDelete={() => void onDelete()}
          onOpenCanvas={(canvasId) => navigate(resPath(`/canvas/${canvasId}`))}
          onError={report}
        />
      </div>
    </div>
  );
};
