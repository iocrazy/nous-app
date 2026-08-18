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

export function createResourceMentionExtension(opts: MentionOptions) {
  void opts; // reserved for future suggestion-trigger wiring
  return Extension.create({
    name: 'resourceMention',
    addExtensions() {
      return [ResourceRefNode];
    },
    addCommands() {
      return {
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
