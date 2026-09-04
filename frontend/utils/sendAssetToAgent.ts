/**
 * "Send To Agent" for a library ASSET — stage it as a chip in the floating
 * chat composer.
 *
 * Two entry points reach this and both must keep reaching it: the asset
 * sheet's sidebar (one asset, opened) and the asset card's action menu on the
 * shelf (any asset, in passing). It lives here for the same reason
 * `sendResourceToAgent` does — the first version of that feature was wired to
 * one of its two menus and the other one silently did nothing for a whole
 * release.
 *
 * WHAT THIS DOES NOT DO (P5 ruling I): it does not call
 * `ensureResourceProcessed`. That helper tops up transcription / summarisation
 * on a MEDIA row and charges points for it. An asset is a library entity — a
 * character, a location, a prompt — with nothing to transcribe; running the
 * top-up here would either no-op confusingly or bill the user for work on the
 * wrong row.
 *
 * The send is local and synchronous (a zustand write), so there is no failure
 * branch to report: either the store takes it — and the toast says so — or the
 * call never happened. The confirmation matters because the composer may be
 * off-screen when the click lands: without it, "Send To Agent" from the shelf
 * looks exactly like a no-op.
 */

import { useGlobalChatStore } from '../stores/globalChatStore';

/** The subset of an asset row this path reads. Deliberately loose about the
 *  source: the sheet passes an `AssetRowDetail`, the shelf card an `AssetRow`,
 *  and a future @-picker row will carry the same six fields. */
export interface AgentSendableAsset {
  id: string;
  name: string;
  asset_type: string;
  cover_file_id?: string | null;
  scope_id?: string | null;
}

/** i18next's `t(key, defaultValue, options)` shape, narrowed to what we use. */
type Translate = (
  key: string,
  defaultValue: string,
  options?: Record<string, unknown>,
) => string;

export interface SendAssetToAgentOptions {
  /** Null in hosts with no ToastProvider (bare unit mounts, isolated embeds).
   *  The send still happens; only its confirmation is dropped — which is the
   *  right trade for a nice-to-have, and why both call sites read the toast
   *  context through `useOptionalToast`. */
  addToast: ((message: string, type: 'success' | 'error' | 'info') => void) | null;
  t: Translate;
}

/**
 * Open the floating chat and stage `asset` in its composer.
 *
 * `loadout_id` is null in v1: neither entry point offers a loadout picker, and
 * the backend reads null as "use the asset's default loadout" (ruling D).
 */
export function sendAssetToAgent(
  asset: AgentSendableAsset,
  { addToast, t }: SendAssetToAgentOptions,
): void {
  const id = String(asset.id ?? '');
  if (!id) return;

  useGlobalChatStore.getState().sendAssetToChat({
    assetId: id,
    loadoutId: null,
    name: asset.name ?? '',
    assetType: asset.asset_type ?? '',
    coverFileId: asset.cover_file_id ?? null,
    scopeId: asset.scope_id ?? null,
  });

  addToast?.(
    t('assets.agent.staged', 'Added {{name}} To The Chat Composer', {
      name: asset.name ?? '',
    }),
    'info',
  );
}
