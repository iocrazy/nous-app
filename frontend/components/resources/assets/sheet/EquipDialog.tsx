// frontend/components/resources/assets/sheet/EquipDialog.tsx
//
// "Equip" on an empty slot: pick one or more files already in the library and
// attach them to that slot. The file is not moved or copied - `asset_files` is
// a link table, and the resource keeps living wherever it lives.
//
// Four things worth knowing before editing:
//
//  * THE BATCH IS ALL-OR-NOTHING. `POST /assets/{id}/files` with `items` runs
//    the whole list inside one `unit_of_work()` (assets_router.py, I-2), so a
//    rejection means NOTHING was attached - and the envelope carries one code
//    for the batch, not one per item. This dialog therefore says "nothing was
//    attached" and KEEPS the selection, rather than implying the good ones
//    landed or clearing what the user chose.
//  * THE SCOPE CHECK IS MIRRORED, NOT GUESSED. `attach_file` refuses a
//    resource whose `resource_items.scope_id` is not this asset's scope
//    (`resource_not_found`), and `/resources/search` deliberately returns
//    "team + personal". A row from the other side is shown DISABLED with a
//    reason instead of hidden: a user who just searched for a file by name
//    must not conclude it does not exist.
//  * NO KIND FILTER. Nothing server-side restricts which mime a slot accepts,
//    so filtering the picker to images would hide attachable files while
//    claiming to show the library.
//  * THE LOADOUT IS ONLY APPLIED TO `worn`. That is the one slot whose files
//    belong to an outfit rather than to the character; stamping a loadout onto
//    a `stills` file would make it vanish the moment another outfit is
//    selected. The target line states what will happen either way.

import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Loader2, Search } from 'lucide-react';

import { UiModal } from '../../../ui/primitives';
import { useToast } from '../../../Toast';
import { useResourceSearch } from '../../../../hooks/useResourceSearch';
import { attachFiles } from '../../../../services/assetsService';
import type { AssetRowDetail, AttachFileBody } from '../../../../services/assetsService';
import { getResourceCoverUrl } from '../../../../services/resourceService';
import type { ResourceSearchResult } from '../../../../types';
import { slotLabelKey } from '../assetTypeMeta';
import { loadoutForSlot } from './assetSheetModel';

export interface EquipDialogProps {
  open: boolean;
  scopeId: string;
  /** Undefined in a personal scope - `/resources/search` reads it as "no team
   *  narrowing", which is the right query there. */
  teamId: string | undefined;
  detail: AssetRowDetail;
  slot: string;
  /** The sheet's selected loadout, or null. */
  loadoutId: string | null;
  loadoutName: string | null;
  onClose: () => void;
  /** Attached successfully - the page refetches the detail so the pins move. */
  onAttached: (count: number) => void;
  onError: (err: unknown) => void;
}

export const EquipDialog: React.FC<EquipDialogProps> = ({
  open,
  scopeId,
  teamId,
  detail,
  slot,
  loadoutId,
  loadoutName,
  onClose,
  onAttached,
  onError,
}) => {
  const { t } = useTranslation();
  const { addToast } = useToast();

  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  /**
   * The last refusal, rendered INSIDE the dialog. The shared reporter already
   * toasts, but a toast is transient and the user is still looking at a
   * selection that did not land - the dialog has to say so where they are.
   */
  const [refused, setRefused] = useState<{ code: string | null; message: string } | null>(
    null,
  );

  // A fresh open is a fresh question. Carrying the previous slot's selection
  // over would attach files the user chose for somewhere else.
  useEffect(() => {
    if (!open) return;
    setQuery('');
    setSelected([]);
    setRefused(null);
  }, [open, slot]);

  // `kinds: ''` on purpose - see the header note.
  const { data, loading, error } = useResourceSearch(open ? query : '', '', teamId);

  const targetLoadoutId = loadoutForSlot(slot, loadoutId);

  /** Already in this slot under the same loadout: attaching again would be a
   *  no-op (the repo upserts on the PK), so it is shown as done rather than
   *  offered as an action that changes nothing. */
  const attachedIds = useMemo(() => {
    const ids = new Set<string>();
    for (const file of detail.files) {
      if (file.slot === slot && (file.loadout_id ?? null) === targetLoadoutId) {
        ids.add(file.resource_id);
      }
    }
    return ids;
  }, [detail.files, slot, targetLoadoutId]);

  const slotLabel = t(slotLabelKey(slot), slot);

  if (!open) return null;

  const rows: ResourceSearchResult[] = data.results;

  const toggle = (id: string) =>
    setSelected((current) =>
      current.includes(id) ? current.filter((x) => x !== id) : [...current, id],
    );

  const submit = async () => {
    if (busy || selected.length === 0) return;
    setBusy(true);
    setRefused(null);
    const items: AttachFileBody[] = selected.map((resourceId) => ({
      resource_id: resourceId,
      slot,
      loadout_id: targetLoadoutId,
    }));
    try {
      const created = await attachFiles(scopeId, detail.id, items);
      // The count comes from the SELECTION, not from `created.length`: the
      // batch is atomic, so a resolve means every item landed, and a server
      // that answered with a shorter array would be a contract break worth
      // seeing rather than a number to quietly render.
      addToast(
        // `n`, not `count`: i18next treats a `count` option as a PLURAL
        // selector and would look for `..._one` / `..._other` keys that do
        // not exist, silently falling back to the raw key.
        t('assets.equip.attached', {
          n: items.length,
          slot: slotLabel,
          defaultValue: 'Attached {{n}} To {{slot}}',
        }),
        'success',
      );
      if (created.length !== items.length) {
        console.error(
          '[EquipDialog] batch attach answered with a different number of rows:',
          { asked: items.length, got: created.length },
        );
      }
      onAttached(items.length);
      onClose();
    } catch (err) {
      onError(err);
      const code = (err as { code?: string } | null)?.code ?? null;
      setRefused({
        code,
        message: code
          ? t(`assets.err.${code}`, t('assets.err.generic'))
          : t('assets.err.generic'),
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <UiModal
      isOpen
      onClose={onClose}
      title={t('assets.equip.title', { slot: slotLabel, defaultValue: 'Equip {{slot}}' })}
      widthClassName="max-w-lg"
    >
      <div className="flex flex-col gap-3" data-testid="equip-dialog" data-slot={slot}>
        <p className="text-[11px] text-content-4" data-testid="equip-target">
          {targetLoadoutId && loadoutName
            ? t('assets.equip.targetWithLoadout', {
                slot: slotLabel,
                loadout: loadoutName,
                defaultValue: 'Attaching to {{slot}} of the {{loadout}} loadout',
              })
            : t('assets.equip.target', {
                slot: slotLabel,
                defaultValue: 'Attaching to {{slot}}',
              })}
        </p>

        <label className="relative block">
          <Search
            size={13}
            aria-hidden="true"
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-content-4"
          />
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label={t('assets.equip.search', 'Search My Uploads')}
            placeholder={t('assets.equip.search', 'Search My Uploads')}
            data-testid="equip-search"
            className="w-full rounded-lg border border-line-strong bg-card py-1.5 pl-8 pr-2.5 text-[13px] text-content placeholder:text-content-4 focus:border-accent focus:outline-none"
          />
        </label>

        {refused && (
          <p
            role="alert"
            data-testid="equip-refused"
            // The raw code as well as the sentence: a refusal the locale has
            // no string for still has to leave something behind that names
            // which rule was hit.
            data-code={refused.code ?? ''}
            className="rounded-lg border border-danger-line bg-danger-soft px-2.5 py-1.5 text-[11px] text-danger"
          >
            {t('assets.equip.nothingAttached', 'Nothing Was Attached')} {refused.message}
          </p>
        )}

        <div className="max-h-72 overflow-y-auto">
          {loading ? (
            <p className="flex items-center gap-2 py-6 text-xs text-content-3">
              <Loader2 size={14} className="animate-spin" aria-hidden="true" />
              {t('common.loading', 'Loading...')}
            </p>
          ) : error ? (
            <p role="alert" data-testid="equip-search-failed" className="py-6 text-xs text-danger">
              {t('assets.equip.searchFailed', 'Could Not Search The Library')}
            </p>
          ) : rows.length === 0 ? (
            <p className="py-6 text-xs text-content-3">
              {t('assets.equip.noResults', 'No Files Match That Search')}
            </p>
          ) : (
            <ul className="flex flex-col">
              {rows.map((row) => {
                // Mirrors `attach_file`'s own check: the resource has to sit in
                // THIS asset's scope. `/resources/search` returns team +
                // personal, so a mismatch is normal, not exceptional.
                const outOfScope = row.scope.id !== scopeId;
                const already = attachedIds.has(row.id);
                const disabled = outOfScope || already;
                const picked = selected.includes(row.id);
                return (
                  <li key={row.id}>
                    <button
                      type="button"
                      data-testid="equip-candidate"
                      data-resource-id={row.id}
                      data-picked={picked}
                      aria-pressed={picked}
                      disabled={disabled}
                      onClick={() => toggle(row.id)}
                      className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-island-2 disabled:cursor-not-allowed disabled:opacity-50 ${
                        picked ? 'bg-[var(--accent-soft)]' : ''
                      }`}
                    >
                      <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded border border-line-strong">
                        {picked && <Check size={11} className="text-accent" aria-hidden="true" />}
                      </span>
                      <span className="h-8 w-8 shrink-0 overflow-hidden rounded border border-line bg-island-2">
                        {row.thumbnail_url && (
                          <img
                            src={getResourceCoverUrl(row.id)}
                            alt=""
                            className="h-full w-full object-cover"
                          />
                        )}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13px] text-content">{row.name}</span>
                        <span className="block truncate text-[11px] text-content-4">
                          {row.kind}
                        </span>
                      </span>
                      {already && (
                        <span data-testid="equip-already" className="text-[10px] text-content-4">
                          {t('assets.equip.alreadyAttached', 'Attached')}
                        </span>
                      )}
                      {!already && outOfScope && (
                        <span
                          data-testid="equip-out-of-scope"
                          className="text-[10px] text-content-4"
                        >
                          {t('assets.equip.otherWorkspace', 'Other Workspace')}
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="flex items-center gap-2">
          <span className="flex-1 text-[11px] tabular-nums text-content-4">
            {t('assets.equip.selected', {
              n: selected.length,
              defaultValue: '{{n}} Selected',
            })}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-line-strong px-2.5 py-1 text-xs font-medium text-content-2 hover:bg-island-2"
          >
            {t('common.cancel', 'Cancel')}
          </button>
          <button
            type="button"
            data-testid="equip-submit"
            disabled={busy || selected.length === 0}
            onClick={() => void submit()}
            className="inline-flex items-center gap-1 rounded-lg border border-accent bg-accent px-2.5 py-1 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy && <Loader2 size={12} className="animate-spin" aria-hidden="true" />}
            {t('assets.equip.attach', 'Attach')}
          </button>
        </div>
      </div>
    </UiModal>
  );
};
