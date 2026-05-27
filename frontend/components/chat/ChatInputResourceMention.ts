import { Extension } from '@tiptap/core';
import { ResourceRefNode } from './ResourceChipNode';
import type { ResourceSearchResult } from '../../types';

interface MentionOptions {
  /** Called when user opens the picker — parent renders the popover and
   *  picks the active item. Returns a Promise that resolves with the
   *  chosen result or null if cancelled. */
  onPick: (query: string) => Promise<ResourceSearchResult | null>;
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
          (item: ResourceSearchResult) =>
          ({ commands }: any) => {
            return commands.insertContent({
              type: 'resourceRef',
              attrs: {
                resourceId: item.id,
                name: item.name,
                kind: item.kind,
                mime: item.mime ?? '',
                scope: item.scope,
              },
            });
          },
      } as any;
    },
  });
}
