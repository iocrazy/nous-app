/**
 * useComposerAssetAttach — the `pendingAsset` channel's receiving end.
 *
 * Sibling of `useComposerResourceAttach`, and separate on purpose: ONE EFFECT
 * PER CHANNEL. A single effect watching both `pendingResource` and
 * `pendingAsset` would re-run when either changed, and the "did the other one
 * already get consumed?" guard that follows from that is exactly the shape of
 * bug the one-shot channels exist to avoid.
 *
 * What arrives here comes from `utils/sendAssetToAgent` (asset sheet sidebar,
 * asset card menu). Unlike the resource channel there is no AI processing
 * top-up on either side of the boundary — an asset is a library entity, not a
 * media row (P5 ruling I) — so this hook only stages and focuses.
 *
 * An asset that arrives before the panel mounts is safe: it waits in the store
 * until the first effect pass consumes it.
 */

import { useEffect } from 'react';

import { useGlobalChatStore } from '../stores/globalChatStore';
import type { AssetRefInsertItem } from '../components/chat/stagedResources';

/** Agent used when the panel has none selected yet. Never overrides a choice
 *  the user (or an agent-locked host) already made. Same value and same rule
 *  as the resource channel's. */
const DEFAULT_AGENT_SLUG = 'analyze';

export interface UseComposerAssetAttachOptions {
  /** Put the asset in the composer's attachment row. */
  stageAsset: (item: AssetRefInsertItem) => void;
  /**
   * Called after staging. The user came from the asset library with no caret
   * in the composer, so without this they have to click into it before they
   * can say anything about the asset they just sent.
   */
  focusComposer?: () => void;
  selectedAgentSlug: string | null;
  setSelectedAgentSlug: (slug: string) => void;
  /** Non-null when the host panel is locked to one agent (no selector). */
  lockedAgent?: string | null;
}

export function useComposerAssetAttach({
  stageAsset,
  focusComposer,
  selectedAgentSlug,
  setSelectedAgentSlug,
  lockedAgent = null,
}: UseComposerAssetAttachOptions): void {
  const pendingAsset = useGlobalChatStore((s) => s.pendingAsset);

  useEffect(() => {
    if (!pendingAsset) return;

    // Send To Agent does not pick the agent for the user — it only fills the
    // gap when there is nothing selected at all.
    if (!lockedAgent && !selectedAgentSlug) setSelectedAgentSlug(DEFAULT_AGENT_SLUG);

    const item: AssetRefInsertItem = {
      // PendingAsset is camelCase; the insert item uses the wire field names
      // the staged row and the search rows share — `id`, not `assetId`.
      id: pendingAsset.assetId,
      name: pendingAsset.name,
      asset_type: pendingAsset.assetType,
      loadout_id: pendingAsset.loadoutId,
      cover_file_id: pendingAsset.coverFileId,
      scope_id: pendingAsset.scopeId,
    };

    stageAsset(item);
    // Consume after staging so a re-render cannot stage the same asset twice.
    useGlobalChatStore.getState().consumePendingAsset();
    focusComposer?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingAsset]);
}
