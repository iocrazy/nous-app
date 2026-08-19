/**
 * useComposerResourceAttach — the two ways a library asset lands in the
 * chat composer, and the one rule they share.
 *
 * Entry A: the @ picker (`attachResource`) — the user is already typing.
 * Entry B: the resource context menu's "Send to Agent", which arrives
 * through globalChatStore's one-shot `pendingResource` channel after the
 * panel has been asked to open.
 *
 * The shared rule is F1: an asset the agent cannot read is worth little, so
 * attaching one tops up whatever AI processing it is missing — and says out
 * loud what that cost, because it is charged in points. Entry B triggers at
 * the menu (it has the resource row in hand); this hook only stages.
 *
 * Both entries now stage the asset in the attachment row above the composer
 * rather than inserting a chip into the sentence. That also retires the
 * frame-by-frame retry this hook used to carry for INSERTION: it existed
 * because the tiptap editor mounts a frame or two after the panel
 * (RECON#20), and staging writes React state owned by the panel this hook
 * already lives in. A resource that arrives before the panel mounts is
 * still safe — it waits in the store until the first effect pass consumes it.
 *
 * Focus is the one thing that still needs the editor, and only on entry B:
 * the user was in the library, not the composer, and the point of "Send to
 * Agent" is to leave them able to type. Entry A never lost focus in the
 * first place. The panel owns that retry (it already runs the same one for
 * the quote channel).
 */

import { useCallback, useEffect } from 'react';

import { useGlobalChatStore } from '../stores/globalChatStore';
import { ensureResourceProcessed } from '../utils/ensureResourceProcessed';
import { resourceProcessingNotice } from '../utils/resourceProcessingToast';
import type { ResourceRefInsertItem } from '../components/chat/ChatInputResourceMention';

/** Agent used when the panel has none selected yet. Never overrides a
 *  choice the user (or an agent-locked host) already made. */
const DEFAULT_AGENT_SLUG = 'analyze';

type Translate = (
  key: string,
  defaultValue: string,
  options?: Record<string, unknown>,
) => string;

export interface UseComposerResourceAttachOptions {
  /** Put the asset in the composer's attachment row. */
  stageResource: (item: ResourceRefInsertItem) => void;
  /**
   * Called only for the context-menu channel, after staging. The user came
   * from the library with no caret in the composer, so without this they
   * have to click into it before they can say anything about the asset
   * they just sent.
   */
  focusComposer?: () => void;
  selectedAgentSlug: string | null;
  setSelectedAgentSlug: (slug: string) => void;
  /** Non-null when the host panel is locked to one agent (no selector). */
  lockedAgent?: string | null;
  notify: (message: string, type: 'info' | 'error') => void;
  t: Translate;
}

export interface UseComposerResourceAttachResult {
  /** Stage an asset picked in the @ menu and top up its processing. */
  attachResource: (item: ResourceRefInsertItem) => void;
}

export function useComposerResourceAttach({
  stageResource,
  focusComposer,
  selectedAgentSlug,
  setSelectedAgentSlug,
  lockedAgent = null,
  notify,
  t,
}: UseComposerResourceAttachOptions): UseComposerResourceAttachResult {
  const pendingResource = useGlobalChatStore((s) => s.pendingResource);

  useEffect(() => {
    if (!pendingResource) return;

    // Send to Agent does not pick the agent for the user — it only fills
    // the gap when there is nothing selected at all. An agent-locked panel
    // is never touched.
    if (!lockedAgent && !selectedAgentSlug) setSelectedAgentSlug(DEFAULT_AGENT_SLUG);

    const item: ResourceRefInsertItem = {
      // PendingResource is camelCase; the insert item uses the search-row
      // field names — `id`, not `resourceId`.
      id: pendingResource.resourceId,
      name: pendingResource.name,
      kind: pendingResource.kind,
      mime: pendingResource.mime,
      scope: pendingResource.scope,
      thumbnail_url: pendingResource.thumbnailUrl ?? null,
      // Carried end to end so the chip can state the asset's status. Absent
      // status means the chip claims nothing, which is the right failure —
      // the wrong one is calling a finished video "not processed yet".
      transcript_status: pendingResource.transcriptStatus ?? null,
      summary_status: pendingResource.summaryStatus ?? null,
    };

    stageResource(item);
    // Consume after staging (as the quote channel does) so a re-render
    // cannot stage the same asset twice.
    useGlobalChatStore.getState().consumePendingResource();
    focusComposer?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingResource]);

  const attachResource = useCallback(
    (item: ResourceRefInsertItem) => {
      stageResource(item);

      void ensureResourceProcessed({
        id: item.id,
        kind: item.kind,
        mime: item.mime,
        transcript_status: item.transcript_status,
        summary_status: item.summary_status,
      }).then((result) => {
        const notice = resourceProcessingNotice(result, t);
        if (notice) notify(notice.message, notice.type);
      });
    },
    [stageResource, notify, t],
  );

  return { attachResource };
}
