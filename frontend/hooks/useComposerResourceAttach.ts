/**
 * useComposerResourceAttach — the two ways a library asset lands in the
 * chat composer, and the one rule they share.
 *
 * Entry A: the @ picker (`attachResource`) — the user is already typing,
 * the editor is live.
 * Entry B: the resource context menu's "Send to Agent", which arrives
 * through globalChatStore's one-shot `pendingResource` channel after the
 * panel has been asked to open.
 *
 * The shared rule is F1: an asset the agent cannot read is worth little, so
 * attaching one tops up whatever AI processing it is missing — and says out
 * loud what that cost, because it is charged in points. Entry B triggers at
 * the menu (it has the resource row in hand); this hook only inserts.
 *
 * Insertion is retried over frames: the composer editor mounts a frame or
 * two after the panel opens (RECON#20), the same race the quote channel
 * already walks.
 */

import { useCallback, useEffect, useRef } from 'react';
import type { Editor } from '@tiptap/core';

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
  editorRef: React.RefObject<Editor | null>;
  selectedAgentSlug: string | null;
  setSelectedAgentSlug: (slug: string) => void;
  /** Non-null when the host panel is locked to one agent (no selector). */
  lockedAgent?: string | null;
  notify: (message: string, type: 'info' | 'error') => void;
  t: Translate;
}

export interface UseComposerResourceAttachResult {
  /** Insert an asset picked in the @ menu and top up its processing. */
  attachResource: (item: ResourceRefInsertItem) => void;
}

function insertChip(editor: Editor, item: ResourceRefInsertItem): void {
  (editor.commands as unknown as {
    insertResourceRef: (i: ResourceRefInsertItem) => boolean;
  }).insertResourceRef(item);
}

export function useComposerResourceAttach({
  editorRef,
  selectedAgentSlug,
  setSelectedAgentSlug,
  lockedAgent = null,
  notify,
  t,
}: UseComposerResourceAttachOptions): UseComposerResourceAttachResult {
  const pendingResource = useGlobalChatStore((s) => s.pendingResource);

  // Frames still waiting for the composer to mount. They must NOT be
  // cancelled by the consuming effect's own cleanup: consuming the channel
  // changes the store, which changes this effect's dep, which runs the
  // previous cleanup — i.e. the retry would cancel itself one frame after
  // arming and the asset would be dropped instead of awaited. Only an
  // unmount may cancel them.
  const pendingFrames = useRef<Set<number>>(new Set());
  useEffect(() => () => {
    pendingFrames.current.forEach((handle) => cancelAnimationFrame(handle));
    pendingFrames.current.clear();
  }, []);

  useEffect(() => {
    if (!pendingResource) return undefined;

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

    // Consume immediately (as the quote channel does) so a re-render cannot
    // insert the same chip twice; the retry below closes over `item`.
    useGlobalChatStore.getState().consumePendingResource();

    let handle = 0;
    let tries = 0;
    const insertWhenReady = () => {
      if (handle) pendingFrames.current.delete(handle);
      const editor = editorRef.current;
      if (!editor) {
        if (tries++ > 30) return; // give up quietly rather than loop forever
        handle = requestAnimationFrame(insertWhenReady);
        pendingFrames.current.add(handle);
        return;
      }
      insertChip(editor, item);
      editor.chain().focus('end').run();
    };
    insertWhenReady();

    // No cleanup: each send owns its own retry loop (two sends in a row
    // must both land, so a new one may not cancel the previous one).
    return undefined;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingResource]);

  const attachResource = useCallback(
    (item: ResourceRefInsertItem) => {
      const editor = editorRef.current;
      if (editor) insertChip(editor, item);

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
    [editorRef, notify, t],
  );

  return { attachResource };
}
