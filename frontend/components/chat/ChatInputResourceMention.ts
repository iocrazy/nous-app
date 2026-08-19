import { Extension } from '@tiptap/core';
import { ResourceRefNode } from './ResourceChipNode';
import type { ResourceSearchResult } from '../../types';

interface MentionOptions {
  /** Called when user opens the picker — parent renders the popover and
   *  picks the active item. Returns a Promise that resolves with the
   *  chosen result or null if cancelled. */
  onPick: (query: string) => Promise<ResourceSearchResult | null>;
}

/**
 * What `insertResourceRef` needs. Deliberately looser than
 * `ResourceSearchResult`: the context-menu "Send to Agent" path has a
 * library resource, not a search row, so the enriched fields are optional
 * and every one of them has a defined empty fallback.
 */
export interface ResourceRefInsertItem {
  id: string;
  name: string;
  kind: string;
  mime?: string | null;
  scope?: { type: 'personal' | 'team'; id: string };
  /** Relative cover path (`/api/v1/resources/{id}/cover`) or null. */
  thumbnail_url?: string | null;
  transcript_status?: string | null;
  summary_status?: string | null;
}

/** How far back we look for the "@" that opened the picker. Matches the
 *  window ChatInput scans when it tracks the live query. */
const MENTION_SCAN_CHARS = 80;

export function createResourceMentionExtension(opts: MentionOptions) {
  void opts; // reserved for future suggestion-trigger wiring
  return Extension.create({
    name: 'resourceMention',
    addExtensions() {
      return [ResourceRefNode];
    },
    addCommands() {
      return {
        /**
         * Delete the "@query" the user typed to open the picker.
         *
         * Mandatory now that picking stages the asset above the composer
         * instead of dropping a chip where the caret is: without this the
         * literal text "@clip" stays behind and gets sent as message body.
         *
         * The scan is confined to the caret's own text block and uses
         * `parentOffset`, so offsets map 1:1 onto document positions (inline
         * atoms count as one either way). Scanning across blocks with
         * `doc.textBetween` would not — a paragraph boundary is one
         * character of text but two positions.
         */
        removeMentionTrigger:
          () =>
          ({ state, commands }: any) => {
            const { $from, from, empty } = state.selection;
            if (!empty) return false;
            const start = Math.max(0, $from.parentOffset - MENTION_SCAN_CHARS);
            const textBefore = $from.parent.textBetween(
              start,
              $from.parentOffset,
              undefined,
              '￼',
            );
            const atIdx = textBefore.lastIndexOf('@');
            if (atIdx === -1) return false;
            // Whitespace after the "@" means the picker session is over and
            // this is ordinary prose — leave it alone.
            if (/\s/.test(textBefore.slice(atIdx + 1))) return false;
            const deleteFrom = from - (textBefore.length - atIdx);
            return commands.deleteRange({ from: deleteFrom, to: from });
          },
        insertResourceRef:
          (item: ResourceRefInsertItem) =>
          ({ commands }: any) => {
            return commands.insertContent({
              type: 'resourceRef',
              attrs: {
                resourceId: item.id,
                name: item.name,
                kind: item.kind,
                mime: item.mime ?? '',
                scope: item.scope ?? { type: 'personal', id: '' },
                // Snapshot: what the chip should paint until (and unless) the
                // Task Center has something fresher to say.
                thumbnailUrl: item.thumbnail_url ?? '',
                transcriptStatus: item.transcript_status ?? '',
                summaryStatus: item.summary_status ?? '',
              },
            });
          },
      } as any;
    },
  });
}
