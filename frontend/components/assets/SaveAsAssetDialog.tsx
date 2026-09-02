// frontend/components/assets/SaveAsAssetDialog.tsx
//
// One picker, reused from every entry that can turn a generation into part of
// an asset (spec screen 5). Its whole job is to answer three questions —
// WHICH asset, WHICH slot, and (for a character) WHICH loadout — with as much
// of that already filled in from context as the caller can supply.
//
// Three things here are load-bearing rather than decorative:
//
//  * The footer sentence is the product's contract, not copy: attaching never
//    moves or copies the file. It is fixed text, always visible.
//  * A name collision on "create new" is RECOVERABLE, so the 409 lands as an
//    inline offer to attach to the existing asset — not as a toast that
//    leaves the user re-typing a name they cannot use.
//  * A batch resolves with per-id outcomes. "3 attached" on its own would
//    report a half-applied batch as a success, so the failure branch names
//    the codes and hands the failed ids back through `onDone`.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Loader2, Plus } from 'lucide-react';

import { UiModal } from '../ui/primitives';
import { useToast } from '../Toast';
import {
  createAsset,
  fetchAsset,
  searchAssets,
  GeneratedApiError,
} from '../../services/assetsService';
import type { AssetLoadout, AssetSummary } from '../../services/assetsService';
import { batchGenerated, saveGenerationAsAsset } from '../../services/generatedService';
import type { GeneratedItem, SaveAsAssetBody } from '../../services/generatedService';
import { generatedMediaCoverUrl } from '../../services/generatedMediaService';
import { ASSET_TYPES, UNSORTED, slotsFor, type AssetType } from './assetSlots';

/** Keystrokes settle before the search fires. */
const SEARCH_DEBOUNCE_MS = 250;

/** How many sibling thumbs the batch strip shows before it starts counting. */
const STRIP_LIMIT = 6;

const TYPE_FALLBACK: Record<AssetType, string> = {
  character: 'Character',
  location: 'Location',
  prop: 'Prop',
  costume: 'Costume',
  prompt: 'Prompt',
  audio: 'Audio',
};

/**
 * Slot → English label. The keys are the union of every type's slots plus
 * `unsorted`; a slot missing from here still renders (the raw value is the
 * fallback), so adding a slot in `assetSlots.ts` cannot produce a blank chip.
 */
const SLOT_FALLBACK: Record<string, string> = {
  sheet: 'Sheet',
  stills: 'Stills',
  expressions: 'Expressions',
  extras: 'Extras',
  worn: 'Worn',
  establishing: 'Establishing',
  keyframes: 'Keyframes',
  details: 'Details',
  layout: 'Layout',
  turnaround: 'Turnaround',
  in_scene: 'In Scene',
  flat: 'Flat',
  examples: 'Examples',
  primary: 'Primary',
  variants: 'Variants',
  [UNSORTED]: 'Unsorted',
};

/** What the caller needs to reconcile its own list with what just happened. */
export interface SaveAsAssetOutcome {
  attachedCount: number;
  failed: { id: string; code: string }[];
  assetId: string;
  assetName: string;
  slot: string;
}

export interface SaveAsAssetDialogProps {
  open: boolean;
  scopeId: string;
  /** One for a card action, many for the batch bar. Never empty when open. */
  items: GeneratedItem[];
  onClose: () => void;
  onDone: (result: SaveAsAssetOutcome) => void;
}

/**
 * The chosen target. `new` carries no id on purpose: the asset does not exist
 * until submit, and holding a placeholder id would let a code path attach to
 * something that was never created.
 */
type Target = { kind: 'existing'; id: string; name: string } | { kind: 'new' };

const SEG_BASE =
  'rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-50';
const SEG_ON = 'border-accent bg-accent-soft text-accent';
const SEG_OFF = 'border-line-strong bg-card text-content-2 hover:text-content';

interface SegProps {
  testId: string;
  label: string;
  options: { value: string; label: string }[];
  value: string;
  onChange: (value: string) => void;
}

/** A radiogroup, not a row of buttons: one of these is always the answer, and
 *  screen readers (and tests) need to be able to read which. */
const Seg: React.FC<SegProps> = ({ testId, label, options, value, onChange }) => (
  <div className="space-y-1.5">
    <div className="text-[11px] font-medium uppercase tracking-wide text-content-3">{label}</div>
    <div role="radiogroup" aria-label={label} data-testid={testId} className="flex flex-wrap gap-1.5">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          role="radio"
          aria-checked={value === opt.value}
          onClick={() => onChange(opt.value)}
          className={`${SEG_BASE} ${value === opt.value ? SEG_ON : SEG_OFF}`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  </div>
);

export const SaveAsAssetDialog: React.FC<SaveAsAssetDialogProps> = ({
  open,
  scopeId,
  items,
  onClose,
  onDone,
}) => {
  const { t } = useTranslation();
  const { addToast } = useToast();

  const first = items[0];
  /** Stable identity for the effects that must re-run when the caller opens
   *  the dialog on a DIFFERENT set of generations. */
  const itemsKey = items.map((row) => row.id).join(',');

  const [type, setType] = useState<AssetType>('character');
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [results, setResults] = useState<AssetSummary[]>([]);
  const [suggested, setSuggested] = useState<AssetSummary | null>(null);
  const [target, setTarget] = useState<Target | null>(null);
  const [newName, setNewName] = useState(first?.title ?? '');
  const [slot, setSlot] = useState<string>(UNSORTED);
  const [loadouts, setLoadouts] = useState<AssetLoadout[]>([]);
  const [loadoutId, setLoadoutId] = useState<string | null>(null);
  /**
   * The loadout fetch is in flight. Submitting during that window used to
   * send NO `loadout_id` — the file would attach to the character but not to
   * the loadout the dialog was a moment away from showing as the default,
   * with nothing anywhere saying so. A silent wrong result, so the primary
   * waits rather than guessing.
   */
  const [loadoutsPending, setLoadoutsPending] = useState(false);
  /**
   * Distinct from `results.length === 0`: an empty scope and a failed search
   * look identical in the list otherwise, and reading a network error as
   * "you have no characters yet" is what walks a user into creating a
   * duplicate of an asset they already own.
   */
  const [searchError, setSearchError] = useState(false);
  /** The recoverable 409: the id we may attach to instead. */
  const [existingConflictId, setExistingConflictId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  /** The suggestion sets the default type ONCE. After the user has touched
   *  the seg, a late-arriving fetch must not drag them back. */
  const typeTouched = useRef(false);

  const typeLabel = useCallback(
    (value: AssetType) => t(`saveAsAsset.type.${value}`, TYPE_FALLBACK[value]),
    [t],
  );
  const slotLabel = useCallback(
    (value: string) => t(`saveAsAsset.slot.${value}`, SLOT_FALLBACK[value] ?? value),
    [t],
  );

  const reportFailure = useCallback(
    (err: unknown) => {
      if (err instanceof GeneratedApiError) {
        addToast(t(`saveAsAsset.err.${err.code}`, t('saveAsAsset.err.generic')), 'error');
        return;
      }
      console.error('[SaveAsAssetDialog] request failed:', err);
      addToast(t('saveAsAsset.err.generic'), 'error');
    },
    [addToast, t],
  );

  // ─── Reset on (re)open ───────────────────────────────────────────────────
  // Declared FIRST so the fetch effects below see the reset values in the
  // same commit; the component stays mounted between openings, so without
  // this the second card opened would inherit the first one's answers.
  useEffect(() => {
    if (!open) return;
    typeTouched.current = false;
    setType('character');
    setQuery('');
    setDebouncedQuery('');
    setResults([]);
    setSearchError(false);
    setSuggested(null);
    setTarget(null);
    setNewName(items[0]?.title ?? '');
    setSlot(UNSORTED);
    setLoadouts([]);
    setLoadoutId(null);
    setLoadoutsPending(false);
    setExistingConflictId(null);
    setBusy(false);
    // `items` is a fresh array each render; `itemsKey` is its identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, itemsKey]);

  // ─── The suggestion ──────────────────────────────────────────────────────

  const suggestedId = first?.source_asset_id ?? null;

  useEffect(() => {
    if (!open || !suggestedId) return;
    let alive = true;
    fetchAsset(scopeId, suggestedId)
      .then((asset) => {
        if (!alive) return;
        setSuggested(asset);
        // The type the user most likely wants is the one the suggestion
        // already is — but only until they say otherwise.
        if (!typeTouched.current) setType(asset.asset_type);
      })
      .catch((err) => {
        // Not a toast: the dialog is fully usable without a suggestion, and
        // an error here would be noise on top of a working picker.
        console.error('[SaveAsAssetDialog] suggested asset unavailable:', err);
        if (alive) setSuggested(null);
      });
    return () => {
      alive = false;
    };
  }, [open, scopeId, suggestedId]);

  // ─── Candidates ──────────────────────────────────────────────────────────

  useEffect(() => {
    if (!open) return;
    const handle = setTimeout(() => setDebouncedQuery(query), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [open, query]);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    searchAssets(scopeId, { type, q: debouncedQuery.trim() || undefined })
      .then((list) => {
        if (alive) {
          setResults(list);
          setSearchError(false);
        }
      })
      .catch((err) => {
        // Recorded in place, NOT toasted: this effect re-runs per settled
        // keystroke, so a toast here would stack one per search against a
        // down endpoint. The inline notice says it once, next to the empty
        // list it is explaining — and the create row below stays usable.
        console.error('[SaveAsAssetDialog] asset search failed:', err);
        if (alive) {
          setResults([]);
          setSearchError(true);
        }
      });
    return () => {
      alive = false;
    };
  }, [open, scopeId, type, debouncedQuery]);

  /** Suggestion first, then the search, with the suggestion de-duplicated out
   *  of the search so it cannot appear twice. It only belongs here at all
   *  when its type matches the one being picked. */
  const candidates = useMemo<AssetSummary[]>(() => {
    const pinned = suggested && suggested.asset_type === type ? suggested : null;
    const rest = results.filter((row) => row.id !== pinned?.id);
    return pinned ? [pinned, ...rest] : rest;
  }, [suggested, results, type]);

  // ─── Loadouts (character + an existing asset only) ───────────────────────

  const existingId = target?.kind === 'existing' ? target.id : null;

  useEffect(() => {
    if (!open || type !== 'character' || !existingId) {
      setLoadouts([]);
      setLoadoutId(null);
      setLoadoutsPending(false);
      return;
    }
    let alive = true;
    setLoadoutsPending(true);
    fetchAsset(scopeId, existingId)
      .then((asset) => {
        if (!alive) return;
        setLoadouts(asset.loadouts);
        // The default is the one FLAGGED default, not merely the first row.
        setLoadoutId(asset.loadouts.find((l) => l.is_default)?.id ?? null);
      })
      .catch((err) => {
        console.error('[SaveAsAssetDialog] loadouts unavailable:', err);
        if (alive) {
          setLoadouts([]);
          setLoadoutId(null);
        }
      })
      .finally(() => {
        // Settled either way: a character whose loadouts could not be read
        // must still be attachable, just without one.
        if (alive) setLoadoutsPending(false);
      });
    return () => {
      alive = false;
    };
  }, [open, scopeId, type, existingId]);

  // ─── Actions ─────────────────────────────────────────────────────────────

  const chooseType = (next: AssetType) => {
    if (next === type) return;
    typeTouched.current = true;
    setType(next);
    // Cleared rather than left to be overwritten when the new search lands:
    // for that round-trip the list would otherwise show the OLD type's
    // assets under the new heading, and a click in that window attaches to
    // an asset of the wrong type.
    setResults([]);
    setSearchError(false);
    // A slot only means something under one type — `stills` is not a slot a
    // location has.
    setSlot(UNSORTED);
    setExistingConflictId(null);
    // Unconditionally, and the "unless it is the suggestion of the new type"
    // exception is deliberately absent: it cannot fire. An existing target is
    // only ever set from a row of the CURRENT type — the candidate list is
    // `searchAssets({type})` plus the suggestion pinned only while its type
    // matches, and the 409 path adopts the asset that collided with a
    // `createAsset({asset_type: type})`. Since this function early-returns on
    // `next === type`, every surviving target would be of the wrong type.
    setTarget(null);
  };

  /** Adopt the asset the 409 pointed at. Its name is fetched because the
   *  primary button names its target, and "Attach to …" with no name is not
   *  a confirmation of anything. */
  const adoptExistingAsset = async () => {
    const id = existingConflictId;
    if (!id || busy) return;
    setBusy(true);
    try {
      const asset = await fetchAsset(scopeId, id);
      setTarget({ kind: 'existing', id: asset.id, name: asset.name });
      setExistingConflictId(null);
    } catch (err) {
      reportFailure(err);
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    if (!target || busy || items.length === 0) return;
    setBusy(true);
    try {
      let assetId: string;
      let assetName: string;

      if (target.kind === 'new') {
        const name = newName.trim();
        if (!name) return;
        // `source` is NOT optional in practice: it defaults to `manual`
        // server-side, and the backend's own create-and-attach path
        // (`generated_inbox_service.save_as_asset`) passes `generated`. This
        // client creates then attaches in two steps, so omitting it here
        // would label every asset born in this dialog as hand-made —
        // permanently, and with nothing downstream able to tell.
        const created = await createAsset(scopeId, {
          asset_type: type,
          name,
          source: 'generated',
        });
        assetId = created.id;
        assetName = created.name;
      } else {
        assetId = target.id;
        assetName = target.name;
      }

      const body: SaveAsAssetBody = { asset_id: assetId, slot };
      // Only for a character with a loadout actually resolved — sending
      // `loadout_id: null` would be a claim about a loadout we do not have.
      if (loadoutId) body.loadout_id = loadoutId;

      let attachedCount: number;
      let failed: { id: string; code: string }[] = [];

      if (items.length === 1) {
        const result = await saveGenerationAsAsset(scopeId, items[0].id, body);
        // The server's own answer wins over what we picked: it is the row
        // that actually exists now.
        assetId = result.asset_id || assetId;
        attachedCount = 1;
      } else {
        const result = await batchGenerated(scopeId, {
          ids: items.map((row) => row.id),
          action: 'save_as_asset',
          save_as_asset: body,
        });
        attachedCount = result.ok.length;
        failed = result.failed.map((f) => ({ id: f.id, code: f.code }));
      }

      if (failed.length === 0) {
        addToast(
          t('saveAsAsset.attached', {
            name: assetName,
            slot: slotLabel(slot),
            defaultValue: 'Attached to {{name}} · {{slot}}',
          }),
          'success',
        );
      } else {
        // Name the codes. A count alone leaves the user with no idea what to
        // retry or why it refused.
        const codes = [...new Set(failed.map((f) => f.code))].join(', ');
        addToast(
          `${t('saveAsAsset.partial', {
            ok: attachedCount,
            failed: failed.length,
            defaultValue: '{{ok}} attached, {{failed}} failed',
          })} (${codes})`,
          'error',
        );
      }

      onDone({ attachedCount, failed, assetId, assetName, slot });
      onClose();
    } catch (err) {
      if (
        err instanceof GeneratedApiError &&
        err.code === 'asset_exists' &&
        typeof err.extra.existing_asset_id === 'string'
      ) {
        // Recoverable: the offer is on screen, so a toast would only be a
        // second, less useful copy of it.
        setExistingConflictId(err.extra.existing_asset_id);
        return;
      }
      reportFailure(err);
    } finally {
      setBusy(false);
    }
  };

  // ─── Render ──────────────────────────────────────────────────────────────

  if (!open || !first) return null;

  const created = new Date(first.created_at);
  const createdLabel = Number.isNaN(created.getTime())
    ? first.created_at
    : created.toLocaleDateString();
  const metaLine = [first.model, createdLabel].filter(Boolean).join(' · ');

  const siblings = items.slice(1);
  const stripThumbs = siblings.slice(0, STRIP_LIMIT);
  const stripOverflow = siblings.length - stripThumbs.length;

  const canSubmit =
    target !== null &&
    !busy &&
    !loadoutsPending &&
    (target.kind === 'existing' || newName.trim().length > 0);

  const primaryLabel =
    target?.kind === 'existing'
      ? t('saveAsAsset.attachTo', {
          name: target.name,
          defaultValue: 'Attach to {{name}}',
        })
      : t('saveAsAsset.createAndAttach', 'Create & attach');

  return (
    <UiModal
      isOpen
      title={t('saveAsAsset.title', 'Add To Asset')}
      onClose={onClose}
      widthClassName="w-[680px]"
      footer={
        <div className="flex w-full items-center gap-3">
          {/* The product's contract, stated where the commitment is made. */}
          <span className="text-[11px] text-content-3">
            {t(
              'saveAsAsset.neverMoves',
              'File stays where it is · attaching never moves or copies',
            )}
          </span>
          <div className="flex-1" />
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-line-strong px-3 py-1.5 text-xs font-medium text-content-2 hover:bg-island-2"
          >
            {t('common.cancel', 'Cancel')}
          </button>
          <button
            type="button"
            data-testid="sa-primary"
            disabled={!canSubmit}
            onClick={() => void submit()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-accent bg-accent px-3 py-1.5 text-xs font-semibold text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy && <Loader2 size={12} className="animate-spin" aria-hidden="true" />}
            {primaryLabel}
          </button>
        </div>
      }
    >
      <div data-testid="save-as-asset-dialog" className="flex gap-4">
        {/* ─── What is being attached ─── */}
        <div className="w-40 shrink-0 space-y-2">
          <img
            src={generatedMediaCoverUrl(first.id)}
            alt={first.title}
            className="aspect-square w-full rounded-lg border border-line object-cover"
          />
          <div className="space-y-0.5 text-[11px] leading-snug">
            <div className="font-medium text-content" title={first.title}>
              {first.title}
            </div>
            <div className="tabular-nums text-content-4">{metaLine}</div>
            <div className="text-content-3" title={first.source.label}>
              {first.source.label}
            </div>
          </div>

          {siblings.length > 0 && (
            <div data-testid="sa-more-strip" className="space-y-1">
              <div className="text-[11px] font-medium text-content-3">
                {t('saveAsAsset.more', {
                  n: siblings.length,
                  defaultValue: '+{{n}} more',
                })}
              </div>
              <div className="flex flex-wrap items-center gap-1">
                {stripThumbs.map((row) => (
                  <img
                    key={row.id}
                    data-testid="sa-more-thumb"
                    src={generatedMediaCoverUrl(row.id)}
                    alt={row.title}
                    className="h-7 w-7 rounded border border-line object-cover"
                  />
                ))}
                {stripOverflow > 0 && (
                  <span className="text-[11px] tabular-nums text-content-4">
                    +{stripOverflow}
                  </span>
                )}
              </div>
            </div>
          )}
        </div>

        {/* ─── Where it goes ─── */}
        <div className="min-w-0 flex-1 space-y-3.5">
          <Seg
            testId="sa-type-seg"
            label={t('saveAsAsset.assetType', 'Asset Type')}
            options={ASSET_TYPES.map((value) => ({ value, label: typeLabel(value) }))}
            value={type}
            onChange={(value) => chooseType(value as AssetType)}
          />

          <div className="space-y-1.5">
            <div className="text-[11px] font-medium uppercase tracking-wide text-content-3">
              {t('saveAsAsset.attachToLabel', 'Attach To')}
            </div>
            <div className="overflow-hidden rounded-xl border border-line-strong">
              <input
                type="text"
                data-testid="sa-search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t('saveAsAsset.search', 'Search…')}
                aria-label={t('saveAsAsset.searchLabel', 'Search Assets')}
                className="w-full border-b border-line bg-card px-2.5 py-1.5 text-xs text-content"
              />
              <div className="max-h-52 overflow-y-auto">
                {candidates.map((asset) => {
                  const chosen = target?.kind === 'existing' && target.id === asset.id;
                  const isSuggested = asset.id === suggested?.id;
                  return (
                    <button
                      key={asset.id}
                      type="button"
                      data-testid="sa-candidate"
                      data-asset-id={asset.id}
                      aria-pressed={chosen}
                      onClick={() =>
                        setTarget({ kind: 'existing', id: asset.id, name: asset.name })
                      }
                      className={`flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs ${
                        chosen ? 'bg-accent-soft text-accent' : 'text-content-2 hover:bg-island-2'
                      }`}
                    >
                      {chosen && <Check size={12} aria-hidden="true" className="shrink-0" />}
                      <span className="truncate font-medium">{asset.name}</span>
                      <span
                        className={`shrink-0 rounded-full border px-1.5 text-[10px] ${
                          asset.readiness.state === 'ready'
                            ? 'border-ok-line bg-ok-soft text-ok'
                            : 'border-warn-line bg-warn-soft text-warn'
                        }`}
                      >
                        {asset.readiness.state === 'ready'
                          ? t('saveAsAsset.ready', 'Ready')
                          : t('saveAsAsset.draft', 'Draft')}
                      </span>
                      <span className="flex-1" />
                      {isSuggested && (
                        <span className="shrink-0 text-[10px] text-content-4">
                          {t('saveAsAsset.suggested', 'suggested · from canvas')}
                        </span>
                      )}
                    </button>
                  );
                })}

                <div data-testid="sa-create-row" className="border-t border-line">
                  <button
                    type="button"
                    data-testid="sa-create-button"
                    aria-pressed={target?.kind === 'new'}
                    onClick={() => setTarget({ kind: 'new' })}
                    className={`flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left text-xs ${
                      target?.kind === 'new'
                        ? 'bg-accent-soft text-accent'
                        : 'text-content-2 hover:bg-island-2'
                    }`}
                  >
                    <Plus size={12} aria-hidden="true" className="shrink-0" />
                    <span className="truncate">
                      {t('saveAsAsset.createNew', {
                        type: typeLabel(type),
                        name: newName,
                        defaultValue: 'Create New {{type}} "{{name}}"',
                      })}
                    </span>
                  </button>
                  {target?.kind === 'new' && (
                    <input
                      type="text"
                      data-testid="sa-new-name"
                      value={newName}
                      onChange={(e) => {
                        setNewName(e.target.value);
                        // A new name is a new question — the previous
                        // collision no longer describes it.
                        setExistingConflictId(null);
                      }}
                      aria-label={t('saveAsAsset.newName', 'New Asset Name')}
                      className="w-full border-t border-line bg-card px-2.5 py-1.5 text-xs text-content"
                    />
                  )}
                </div>
              </div>
            </div>

            {searchError && (
              <p data-testid="sa-search-error" className="text-[11px] text-danger">
                {t(
                  'saveAsAsset.searchUnavailable',
                  'Could not load assets — you can still create a new one.',
                )}
              </p>
            )}

            {existingConflictId && (
              <div
                data-testid="sa-exists-notice"
                className="flex items-center gap-2 rounded-lg border border-warn-line bg-warn-soft px-2.5 py-1.5 text-[11px] text-warn"
              >
                <span className="flex-1">
                  {t(
                    'saveAsAsset.exists',
                    'An asset with this name exists — attach to it instead?',
                  )}
                </span>
                <button
                  type="button"
                  data-testid="sa-use-existing"
                  disabled={busy}
                  onClick={() => void adoptExistingAsset()}
                  className="shrink-0 rounded-md border border-warn-line px-2 py-0.5 font-semibold hover:bg-card disabled:opacity-50"
                >
                  {t('saveAsAsset.useExisting', 'Use Existing')}
                </button>
              </div>
            )}
          </div>

          <Seg
            testId="sa-slot-seg"
            label={t('saveAsAsset.slotLabel', 'Slot')}
            options={slotsFor(type).map((value) => ({ value, label: slotLabel(value) }))}
            value={slot}
            onChange={setSlot}
          />

          {/* Only a character with an EXISTING asset has loadouts to pick
              from: a to-be-created asset has none yet. */}
          {type === 'character' && target?.kind === 'existing' && loadouts.length > 0 && (
            <Seg
              testId="sa-loadout-seg"
              label={t('saveAsAsset.loadout', 'Loadout')}
              options={loadouts.map((l) => ({ value: l.id, label: l.name }))}
              value={loadoutId ?? ''}
              onChange={setLoadoutId}
            />
          )}
        </div>
      </div>
    </UiModal>
  );
};
