import React, { useCallback, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Shirt } from 'lucide-react';

import { fetchAssetDetail } from '../../services/assetsService';
import type { AssetLoadoutRow } from '../../services/assetsService';
import type { StagedAssetRef } from './stagedResources';

/**
 * Which outfit a mentioned character is wearing for this turn (v2).
 *
 * v1 sent `loadout_id: null` from both entry points and the backend read that
 * as "the asset's default". That was fine while nobody could say otherwise;
 * the moment a chip carries a picked loadout, the backend stops falling back —
 * a foreign id now comes back as a typed `loadout_not_owned` refusal rather
 * than silently dressing the character in something else.
 *
 * Three deliberate silences, all of them "there is no choice to offer":
 *
 * - anything that is not a `character` (only characters have loadouts);
 * - a SYSTEM PRESET (`scope_id` empty — `toStagedAsset` normalises the wire's
 *   null to ''): presets are read-only to every scope, and `fetchAssetDetail`
 *   needs a scope id it does not have;
 * - fewer than two loadouts once the answer arrives — one option is not a pick.
 *
 * The fourth case is NOT silent. A fetch that failed cannot support the claim
 * "this character has no outfits", so the button stays and says what happened
 * in its `title` instead of quietly disappearing.
 *
 * The detail is fetched LAZILY, on first open. A composer row can hold up to
 * `MAX_ASSET_REF_ATTACHMENTS` chips and each fetch is a real round trip, so
 * staging an asset must not cost one before anybody asks a question.
 */

export interface StagedAssetLoadoutMenuProps {
  asset: StagedAssetRef;
  /** Both halves: the id goes on the wire, the name goes on the chip. Passing
   *  only the id would make every consumer re-fetch to render a label it just
   *  had in hand. `(null, null)` is the default-loadout entry. */
  onChange: (loadoutId: string | null, loadoutName: string | null) => void;
}

type LoadState = 'idle' | 'loading' | 'ready' | 'failed';

export function StagedAssetLoadoutMenu({
  asset,
  onChange,
}: StagedAssetLoadoutMenuProps): React.ReactElement | null {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<LoadState>('idle');
  const [loadouts, setLoadouts] = useState<AssetLoadoutRow[]>([]);
  // Survives re-renders so a failed chip does not re-request on every click.
  // One failure is information; a click that silently costs another round trip
  // each time is a retry loop the user never asked for.
  const asked = useRef(false);

  const eligible = asset.asset_type === 'character' && Boolean(asset.scope_id);
  // Withdrawn only on a real ANSWER. `state === 'failed'` deliberately keeps
  // the button: see the header note.
  const hidden = state === 'ready' && loadouts.length < 2;

  const openMenu = useCallback(async () => {
    if (open) {
      setOpen(false);
      return;
    }
    if (asked.current) {
      if (state === 'ready') setOpen(true);
      return;
    }
    asked.current = true;
    setState('loading');
    try {
      const detail = await fetchAssetDetail(asset.scope_id, asset.asset_id);
      setLoadouts(detail.loadouts);
      setState('ready');
      setOpen(detail.loadouts.length >= 2);
    } catch (err) {
      console.error('[StagedAssetLoadoutMenu] loadouts unavailable:', err);
      setState('failed');
      setOpen(false);
    }
  }, [open, state, asset.scope_id, asset.asset_id]);

  const pick = useCallback(
    (loadoutId: string | null, loadoutName: string | null) => {
      onChange(loadoutId, loadoutName);
      setOpen(false);
    },
    [onChange],
  );

  if (!eligible || hidden) return null;

  const title =
    state === 'failed'
      ? t('chat.stagedAsset.loadoutsUnavailable', 'Could not load loadouts')
      : t('chat.stagedAsset.chooseLoadout', 'Choose Loadout');

  return (
    <span className="relative inline-flex">
      <button
        type="button"
        data-testid="staged-asset-loadout-button"
        data-state={state}
        onClick={() => void openMenu()}
        title={title}
        aria-label={title}
        className="opacity-50 hover:opacity-100 inline-flex"
      >
        <Shirt size={10} />
      </button>
      {open && state === 'ready' && (
        <span
          role="menu"
          data-testid="staged-asset-loadout-menu"
          className="absolute bottom-full left-0 mb-1 z-30 min-w-[140px] flex flex-col rounded border border-ink-700 bg-ink-900 py-0.5 shadow-lg"
        >
          {/* The way BACK. Without it a user who picked an outfit could only
              undo it by removing the chip and staging the asset again. */}
          <button
            type="button"
            role="menuitem"
            onClick={() => pick(null, null)}
            data-active={asset.loadout_id === null}
            className="px-2 py-1 text-left text-[11px] text-ink-300 hover:bg-ink-800"
          >
            {t('chat.stagedAsset.defaultLoadout', 'Default Loadout')}
          </button>
          {loadouts.map((lo) => (
            <button
              key={lo.id}
              type="button"
              role="menuitem"
              onClick={() => pick(lo.id, lo.name)}
              data-active={asset.loadout_id === lo.id}
              data-default={lo.is_default}
              className="px-2 py-1 text-left text-[11px] text-ink-300 hover:bg-ink-800"
            >
              {lo.name}
            </button>
          ))}
        </span>
      )}
    </span>
  );
}

export default StagedAssetLoadoutMenu;
